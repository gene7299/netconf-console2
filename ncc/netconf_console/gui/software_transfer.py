"""Temporary read-only SFTP serving and isolated jump-host upload staging."""

from dataclasses import dataclass, field
import base64
import hmac
import ipaddress
import json
import os
from pathlib import Path
import posixpath
import secrets
import shlex
import socket
import stat
from threading import Event, Lock, Thread
import time
from urllib.parse import quote
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
import paramiko

from ..jump import open_jump_transport
from .model import EditError


def check_cancel(cancel):
    if cancel.is_set():
        raise InterruptedError("已取消檔案準備。")


def usable_ip(address):
    try:
        ip = ipaddress.ip_address(address)
        return not (ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local)
    except ValueError:
        return False


def local_addresses(target=""):
    from PySide6.QtNetwork import QNetworkInterface
    entries = []
    preferred = ""
    if target:
        try:
            for family, _kind, _protocol, _name, address in socket.getaddrinfo(target, 9, type=socket.SOCK_DGRAM):
                with socket.socket(family, socket.SOCK_DGRAM) as probe:
                    # UDP connect only selects a local route; no data is sent.
                    probe.connect(address)
                    preferred = probe.getsockname()[0]
                    break
        except OSError:
            pass
    for interface in QNetworkInterface.allInterfaces():
        flags = interface.flags()
        if (not flags & QNetworkInterface.InterfaceFlag.IsUp
                or not flags & QNetworkInterface.InterfaceFlag.IsRunning
                or flags & QNetworkInterface.InterfaceFlag.IsLoopBack):
            continue
        for entry in interface.addressEntries():
            address = entry.ip().toString()
            if usable_ip(address):
                entries.append({"ip": address, "interface": interface.humanReadableName(),
                                "preferred": address == preferred})
    return sorted(entries, key=lambda item: (not item["preferred"], ":" in item["ip"], item["interface"], item["ip"]))


def remote_output(transport, command, cancel):
    check_cancel(cancel)
    channel = transport.open_session(timeout=10)
    try:
        channel.settimeout(10)
        channel.exec_command(command)
        deadline = time.monotonic() + 10
        output = bytearray()
        while True:
            check_cancel(cancel)
            if channel.recv_ready():
                output.extend(channel.recv(32768))
                if len(output) > 262144:
                    raise EditError("跳板 IP 清單回應過大。")
            if channel.recv_stderr_ready():
                channel.recv_stderr(32768)
            if channel.exit_status_ready() and not channel.recv_ready():
                if channel.recv_exit_status() != 0:
                    raise EditError("跳板無法執行網卡查詢指令。")
                return output.decode("utf-8", errors="replace")
            if time.monotonic() >= deadline:
                raise EditError("跳板 IP 查詢逾時。")
            cancel.wait(0.03)
    finally:
        channel.close()


def jump_addresses(settings, cancel):
    transport = open_jump_transport(settings.copy(timeout=min(settings.timeout or 15, 15)))
    try:
        entries = []
        try:
            data = json.loads(remote_output(transport, "ip -j address show up", cancel))
            for interface in data:
                for entry in interface.get("addr_info", []):
                    address = entry.get("local", "")
                    if usable_ip(address) and not entry.get("tentative") and not entry.get("dadfailed"):
                        entries.append({"ip": address, "interface": interface.get("ifname", ""), "preferred": False})
        except (ValueError, EditError):
            for address in remote_output(transport, "hostname -I", cancel).split():
                if usable_ip(address):
                    entries.append({"ip": address, "interface": "跳板網卡", "preferred": False})
        try:
            target = str(ipaddress.ip_address(settings.host))
            routes = json.loads(remote_output(transport, "ip -j route get " + shlex.quote(target), cancel))
            source = routes[0].get("prefsrc", routes[0].get("src", "")) if routes else ""
            for entry in entries:
                entry["preferred"] = entry["ip"] == source
        except (ValueError, EditError, KeyError):
            pass
        return sorted({entry["ip"]: entry for entry in entries}.values(),
                      key=lambda item: (not item["preferred"], ":" in item["ip"], item["interface"], item["ip"]))
    finally:
        transport.close()


def file_signature(path):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise EditError("只能選擇一般檔案：" + path.name)
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def selected_files(paths):
    files = {}
    for value in paths:
        path = Path(value).resolve(strict=True)
        name = path.name
        if name in files or name in {".", ".."} or "\\" in name:
            raise EditError("選取的檔案不可有重複名稱：" + name)
        files[name] = (path, file_signature(path))
    if not files:
        raise EditError("請先選擇軟體檔案。")
    return files


def make_uri(address, port, username, path):
    ip = ipaddress.ip_address(address)
    host = "[%s]" % ip if ip.version == 6 else str(ip)
    return "sftp://%s@%s:%d%s" % (quote(username, safe=""), host, port, quote(path, safe="/"))


def yang_host_key(key):
    """Convert SSH wire keys to the encoding required by the file-mgmt YANG."""
    public = serialization.load_ssh_public_key((key.get_name() + " " + key.get_base64()).encode("ascii"))
    if isinstance(public, rsa.RSAPublicKey) and public.key_size in {1024, 2048, 3072, 4096, 7680, 15360}:
        algorithm = "rsa%d" % public.key_size
        data = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.PKCS1)
    elif isinstance(public, ec.EllipticCurvePublicKey) and public.curve.name in {"secp192r1", "secp224r1", "secp256r1", "secp384r1", "secp521r1"}:
        algorithm = public.curve.name
        data = public.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    else:
        return ""
    return algorithm + " " + base64.b64encode(data).decode("ascii")


class _ReadHandle(paramiko.SFTPHandle):
    def __init__(self, source, stream, name):
        super().__init__(os.O_RDONLY)
        self.readfile = stream
        self.source = source
        self.name = name

    def stat(self):
        result = paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
        result.st_mode = stat.S_IFREG | 0o444
        return result

    def read(self, offset, length):
        data = super().read(offset, min(length, 1024 * 1024))
        if isinstance(data, bytes):
            with self.source.lock:
                self.source.last_read = (self.name, offset + len(data), os.fstat(self.readfile.fileno()).st_size)
        return data


class _FilesOnlySftp(paramiko.SFTPServerInterface):
    def __init__(self, server, *args, source=None, **kwargs):
        super().__init__(server, *args, **kwargs)
        self.source = source

    def canonicalize(self, path):
        return posixpath.normpath("/" + path.lstrip("/"))

    def _file(self, path):
        # There is no backing filesystem root: only these exact virtual names
        # resolve. Path traversal and unselected files never reach OS open().
        parts = path.split("/")
        if ".." in parts or "\\" in path:
            return None
        entry = self.source.files.get(self.canonicalize(path).lstrip("/"))
        if entry is None:
            return None
        selected, signature = entry
        if selected.resolve(strict=True) != selected or file_signature(selected) != signature:
            raise OSError("Selected file changed")
        return selected

    def stat(self, path):
        if path in {"/", ".", ""}:
            result = paramiko.SFTPAttributes()
            result.st_mode = stat.S_IFDIR | 0o555
            result.st_size = 0
            return result
        try:
            selected = self._file(path)
            if selected is None:
                return paramiko.SFTP_NO_SUCH_FILE
            result = paramiko.SFTPAttributes.from_stat(selected.stat())
            result.st_mode = stat.S_IFREG | 0o444
            return result
        except OSError:
            return paramiko.SFTP_FAILURE

    lstat = stat

    def list_folder(self, path):
        if path not in {"/", ".", ""}:
            return paramiko.SFTP_NO_SUCH_FILE
        result = []
        for name in self.source.files:
            info = self.stat("/" + name)
            if isinstance(info, paramiko.SFTPAttributes):
                info.filename = name
                result.append(info)
        return result

    def open(self, path, flags, attr):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_EXCL):
            return paramiko.SFTP_PERMISSION_DENIED
        stream = None
        try:
            selected = self._file(path)
            if selected is None:
                return paramiko.SFTP_NO_SUCH_FILE
            stream = selected.open("rb")
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != self.source.files[selected.name][1]:
                stream.close()
                return paramiko.SFTP_FAILURE
            return _ReadHandle(self.source, stream, selected.name)
        except OSError:
            if stream is not None:
                stream.close()
            return paramiko.SFTP_FAILURE


class _SftpLogin(paramiko.ServerInterface):
    def __init__(self, source):
        self.source = source
        self.channels = []

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        if (hmac.compare_digest(username.encode(), self.source.username.encode())
                and hmac.compare_digest(password.encode(), self.source.password.encode())):
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_subsystem_request(self, channel, name):
        self.channels = [item for item in self.channels if not item.closed]
        if name != "sftp" or len(self.channels) >= 8:
            return False
        if super().check_channel_subsystem_request(channel, name):
            self.channels.append(channel)
            return True
        return False


class LocalSftpSource:
    def __init__(self, address, port, paths, cancel):
        self.files = selected_files(paths)
        self.username = "ncc-" + secrets.token_hex(4)
        self.password = secrets.token_urlsafe(24)
        self.key = paramiko.RSAKey.generate(2048)
        self.keys = yang_host_key(self.key)
        self.address = str(ipaddress.ip_address(address))
        self.lock = Lock()
        self.transports = set()
        self.last_read = None
        self.stop = Event()
        self.listener = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM)
        try:
            check_cancel(cancel)
            if os.name == "nt":
                self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            if ":" in address:
                self.listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            self.listener.bind((self.address, port))
            self.listener.listen(8)
            self.listener.settimeout(0.5)
            self.port = self.listener.getsockname()[1]
            self.uris = [make_uri(self.address, self.port, self.username, "/" + name) for name in self.files]
            self.thread = Thread(target=self._accept, daemon=True, name="software-sftp-listener")
            self.thread.start()
        except Exception:
            self.listener.close()
            raise

    def _accept(self):
        while not self.stop.is_set():
            try:
                sock, _peer = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            transport = paramiko.Transport(sock)
            with self.lock:
                if self.stop.is_set() or len(self.transports) >= 8:
                    sock.close()
                    continue
                self.transports.add(transport)
            Thread(target=self._serve, args=(transport,), daemon=True, name="software-sftp-client").start()

    def _serve(self, transport):
        try:
            transport.banner_timeout = 15
            transport.auth_timeout = 15
            transport.add_server_key(self.key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _FilesOnlySftp, source=self)
            transport.start_server(server=_SftpLogin(self))
            deadline = time.monotonic() + 20
            channels = []  # Retain accepted channels while their SFTP handlers run.
            while transport.is_active() and not self.stop.is_set():
                channel = transport.accept(0.25)
                if channel is not None:
                    channels.append(channel)
                channels = [channel for channel in channels if not channel.closed]
                if not transport.is_authenticated() and time.monotonic() > deadline:
                    break
        except (EOFError, OSError, paramiko.SSHException):
            pass
        finally:
            transport.close()
            with self.lock:
                self.transports.discard(transport)

    def close(self):
        self.stop.set()
        try:
            self.listener.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.listener.close()
        with self.lock:
            transports = tuple(self.transports)
        for transport in transports:
            transport.close()
        self.thread.join(0.6)

    def validate(self):
        if self.stop.is_set() or not self.thread.is_alive():
            raise EditError("本機 SFTP 已停止，請重新準備。")
        for path, signature in self.files.values():
            if file_signature(path) != signature:
                raise EditError("檔案已變更，請重新選檔並準備 SFTP：" + path.name)


@dataclass
class JumpSftpSource:
    settings: object = field(repr=False)
    address: str
    directory: str
    names: tuple
    password: str = field(repr=False)
    keys: str = ""

    @property
    def uris(self):
        return [make_uri(self.address, self.settings.jump_port, self.settings.jump_username,
                         self.directory + "/" + name) for name in self.names]

    def validate(self):
        # The remote files have been size-checked after upload. A reboot or
        # external /tmp cleanup can still remove them; DUT reports that error.
        if not self.names:
            raise EditError("尚未完成跳板檔案上傳。")

    def cleanup(self):
        transport = open_jump_transport(self.settings)
        try:
            with paramiko.SFTPClient.from_transport(transport) as sftp:
                sftp.get_channel().settimeout(10)
                cleanup_owned_files(sftp, self.directory, self.names)
                self.names = ()
        finally:
            transport.close()


def cleanup_owned_files(sftp, directory, names):
    if not directory.startswith("/tmp/netconf-update-") or posixpath.dirname(directory) != "/tmp":
        raise EditError("無效的暫存目錄。")
    try:
        info = sftp.lstat(directory)
    except OSError as exc:
        if exc.errno == 2:
            return
        raise
    if not stat.S_ISDIR(info.st_mode):
        raise EditError("暫存路徑已被替換；不執行清理。")
    if sftp.normalize(directory) != directory:
        raise EditError("暫存目錄解析到其他位置；不執行清理。")
    for name in names:
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise EditError("無效的暫存檔名。")
        try:
            sftp.remove(directory + "/" + name)
        except OSError as exc:
            if exc.errno != 2:
                raise
    sftp.rmdir(directory)  # Never recurse into unknown contents.


def upload_to_jump(settings, address, paths, password, progress, cancel):
    files = selected_files(paths)
    if not password:
        raise EditError("跳板連線未提供登入密碼；請填入供 DUT 使用的跳板 SFTP 密碼。")
    # Authenticate with the very password passed to the DUT, not a local agent
    # or private-key passphrase that the DUT cannot use.
    settings = settings.copy(jump_auth="password", jump_password=password, timeout=min(settings.timeout or 15, 15))
    transport = open_jump_transport(settings)
    directory = "/tmp/netconf-update-" + uuid.uuid4().hex
    owned = []
    created = False
    try:
        keys = yang_host_key(transport.get_remote_server_key())
        with paramiko.SFTPClient.from_transport(transport) as sftp:
            sftp.get_channel().settimeout(10)
            try:
                check_cancel(cancel)
                sftp.mkdir(directory, mode=0o700)
                created = True
                total = sum(signature[2] for _path, signature in files.values())
                complete = 0
                last_progress = 0
                for name, (path, signature) in files.items():
                    check_cancel(cancel)
                    if file_signature(path) != signature:
                        raise EditError("上傳前檔案已變更：" + name)
                    temporary = ".upload-" + uuid.uuid4().hex
                    with path.open("rb") as source, sftp.open(directory + "/" + temporary, "wx") as target:
                        owned.append(temporary)
                        target.set_pipelined(True)
                        while True:
                            check_cancel(cancel)
                            data = source.read(1024 * 1024)
                            if not data:
                                break
                            target.write(data)
                            complete += len(data)
                            if time.monotonic() - last_progress > 0.25:
                                progress("上傳至跳板：%s · %d%% (%d / %d MiB)" %
                                         (name, complete * 100 // max(1, total), complete // 1048576, total // 1048576))
                                last_progress = time.monotonic()
                    if file_signature(path) != signature or sftp.stat(directory + "/" + temporary).st_size != signature[2]:
                        raise EditError("上傳檔案大小不符或來源已變更：" + name)
                    sftp.chmod(directory + "/" + temporary, 0o600)
                    sftp.rename(directory + "/" + temporary, directory + "/" + name)
                    owned.remove(temporary)
                    owned.append(name)
                check_cancel(cancel)
                progress("跳板上傳完成：" + directory)
                return JumpSftpSource(settings, address, directory, tuple(files), password, keys)
            except Exception as error:
                if created:
                    try:
                        cleanup_owned_files(sftp, directory, owned)
                    except Exception:
                        raise EditError("檔案準備未完成（%s）；無法清理的暫存檔案可能保留於 %s。" %
                                        (type(error).__name__, directory)) from error
                raise
    finally:
        transport.close()
