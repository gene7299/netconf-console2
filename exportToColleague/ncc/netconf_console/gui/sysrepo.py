"""Explicit system-SSH administration; never a NETCONF authorization fallback."""
from copy import deepcopy
from dataclasses import dataclass
import re
import shlex
import socket
import time
import threading
from pathlib import Path

import paramiko
from lxml import etree
from ncclient.xml_ import to_xml

from ..jump import open_jump
from ..sshauth import authenticate
from ..trace import redact_secrets
from ..xmloutput import serialize_xml
from .client import config_semantic, locate, check_selection_current
from .model import NC, EditError, parse_editor

MAX_OUTPUT = 32 * 1024 * 1024
SR = "http://www.sysrepo.org/yang/sysrepo"


@dataclass(frozen=True)
class PreparedEdit:
    payload: bytes
    module: str
    command: str
    read_command: str


def prepare(plan, schema, program="sysrepocfg", timeout=10, defaults=False):
    if plan is None or plan.rpc is None or to_xml(plan.rpc) != plan.wire_xml:
        raise EditError("請先建立有效的 XML 修改草稿。")
    if not re.fullmatch(r"(?:/[A-Za-z0-9_.-]+)*/?sysrepocfg", program):
        raise EditError("僅接受 sysrepocfg 或以 / 開頭的 sysrepocfg 完整路徑；不接受 shell 指令。")
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise EditError("sysrepocfg timeout 需為 1–120 秒。")
    op = plan.rpc.find("{%s}edit-config" % NC)
    if len(plan.rpc) != 1 or op is None or op.find("{%s}target/{%s}running" % (NC, NC)) is None:
        raise EditError("系統 SSH 修改目前只支援 running。")
    config = op.find("{%s}config" % NC)
    if config is None or len(config) != 1 or op.findtext("{%s}default-operation" % NC) != "none":
        raise EditError("需要單一 module 根節點的最小變更預覽。")
    root = deepcopy(config[0])
    module = schema.namespaces.get(etree.QName(root).namespace, "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", module):
        raise EditError("無法從目前 YANG schema 確認 module。")
    # sysrepocfg uses default merge; preserve the NETCONF preview's 'none'
    # semantics on ancestors. Explicit leaf merge/remove still takes precedence.
    if "{%s}operation" % NC not in root.attrib:
        # 'none' is sysrepo metadata, NOT a legal ietf-netconf operation enum.
        prefix = "sr"
        while prefix in root.nsmap and root.nsmap[prefix] != SR:
            prefix += "_edit"
        wrapper = etree.Element(root.tag, nsmap={**root.nsmap, prefix: SR}, attrib=dict(root.attrib))
        wrapper.text = root.text
        wrapper.extend(list(root))
        root = wrapper
        root.set("{%s}operation" % SR, "none")
    payload = serialize_xml(root)
    if len(payload) > 1024 * 1024:
        raise EditError("系統 SSH 修改的最小 XML 上限為 1 MiB。")
    base = [program, "--datastore", "running", "--module", module, "--format", "xml", "--timeout", str(timeout)]
    command = shlex.join([base[0], "--edit", *base[1:], "--lock"])
    read = shlex.join([base[0], "--export", *base[1:], "--defaults", "report-all" if defaults else "explicit"])
    return PreparedEdit(payload, module, command, read)


def parse_export(raw):
    # A sysrepo module may have multiple top-level data nodes, not an RPC envelope.
    text = raw.decode("utf-8-sig")
    text = re.sub(r"^\s*<\?xml[^?]*\?>", "", text, count=1)
    root = parse_editor('<data xmlns="%s">%s</data>' % (NC, text))
    return root


class ShellConnection:
    def __init__(self, settings):
        self.settings = deepcopy(settings)
        self.transport = None
        self.jump = None

    @property
    def connected(self):
        return bool(self.transport and self.transport.is_active() and self.transport.is_authenticated())

    def connect(self):
        self.close()
        settings = self.settings
        sock = None
        try:
            if settings.jump_enabled:
                self.jump, sock = open_jump(settings)
            else:
                sock = socket.create_connection((settings.host, settings.port), timeout=settings.timeout)
            self.transport = paramiko.Transport(sock)
            self.transport.auth_timeout = self.transport.banner_timeout = settings.timeout
            self.transport.start_client(timeout=settings.timeout)
            if settings.hostkey_verify:
                hosts = paramiko.HostKeys()
                hosts.load(str(Path(settings.known_hosts).expanduser() if settings.known_hosts else Path.home() / ".ssh" / "known_hosts"))
                name = settings.host if settings.port == 22 else "[%s]:%s" % (settings.host, settings.port)
                if not hosts.check(name, self.transport.get_remote_server_key()):
                    raise EditError("系統 SSH host key 不符或未知，請核對 known_hosts。")
            authenticate(self.transport, settings.username, settings.password,
                         [settings.key] if settings.key else [], settings.ssh_auth in {"agent", "auto"},
                         False, settings.ssh_auth, settings.key_passphrase)
        except Exception:
            self.close()
            if sock is not None:
                sock.close()
            raise

    def close(self):
        if self.transport:
            self.transport.close()
            self.transport = None
        if self.jump:
            self.jump.close()
            self.jump = None

    def run(self, command, payload=b"", timeout=20):
        if not self.connected:
            raise EditError("系統 SSH 尚未連線或已中斷；不會自動重新連線／重送。")
        channel = self.transport.open_session(timeout=self.settings.timeout)
        deadline = time.monotonic() + timeout
        stdout, stderr = bytearray(), bytearray()
        offset = 0
        watchdog = threading.Timer(timeout, channel.close)
        watchdog.daemon = True
        watchdog.start()
        try:
            channel.settimeout(min(timeout, self.settings.timeout))
            channel.exec_command(command)
            channel.settimeout(0.5)
            eof = False
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("系統 SSH 命令逾時；远端可能已執行，不會重送，請讀回確認。")
                if offset < len(payload) and channel.send_ready():
                    try:
                        count = channel.send(payload[offset:offset + 32768])
                    except socket.timeout:
                        count = None
                    if count == 0:
                        raise ConnectionError("SSH stdin 提早關閉；結果待確認。")
                    if count:
                        offset += count
                if offset == len(payload) and not eof:
                    channel.shutdown_write()
                    eof = True
                for ready, receive, buffer in ((channel.recv_ready, channel.recv, stdout),
                                                (channel.recv_stderr_ready, channel.recv_stderr, stderr)):
                    if ready():
                        buffer.extend(receive(32768))
                if len(stdout) + len(stderr) > MAX_OUTPUT:
                    raise EditError("SSH 輸出超過 32 MiB；停止接收，結果待確認。")
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    status = channel.recv_exit_status()
                    if status != 0 or offset != len(payload):
                        detail = redact_secrets(stderr.decode("utf-8", errors="replace"))[:4000]
                        raise EditError("sysrepocfg exit=%s；%s\n不會自動重送。" % (status, detail))
                    return bytes(stdout), bytes(stderr)
                if channel.closed:
                    raise ConnectionError("系統 SSH 中斷，未取得完整執行結果。")
                time.sleep(0.01)
        finally:
            watchdog.cancel()
            channel.close()


def execute(shell, prepared, selection, plan, schema, timeout=10, defaults=False):
    # Reconstruct command/payload to reject altered previews and program injection.
    program = shlex.split(prepared.command)[0]
    if prepare(plan, schema, program, timeout, defaults) != prepared:
        raise EditError("sysrepocfg 預覽已改變，請重新開啟。")
    before, _ = shell.run(prepared.read_command, timeout=timeout + 10)
    try:
        check_selection_current(parse_export(before), selection, schema)
    except EditError as exc:
        raise EditError("SSH 設定與 NETCONF 快照不符或新增項目已存在；未送出修改。請確認設備並重新讀取。") from exc
    stdout, stderr = shell.run(prepared.command, prepared.payload, timeout=timeout + 10)
    warnings = []
    try:
        after, _ = shell.run(prepared.read_command, timeout=timeout + 10)
        actual = locate(parse_export(after), selection, schema)
        if config_semantic(actual, selection.path, schema) != config_semantic(plan.edited, selection.path, schema):
            warnings.append("exit=0，但 SSH 讀回與草稿不同；請核對設備，不要重送。")
    except Exception:
        warnings.append("exit=0，但 SSH 讀回失敗；修改連線參數可能導致斷線，請另行確認。")
    return "sysrepocfg exit=0\n" + redact_secrets((stdout + stderr).decode("utf-8", errors="replace"))[:8000], warnings
