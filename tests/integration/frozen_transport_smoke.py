"""Loopback transport smoke tests for the frozen Windows executable.

The test starts small in-process NETCONF peers and executes the packaged
client as a child process.  It exercises real TCP, SSH/TLS negotiation and
NETCONF hello exchange for all four supported connection modes without using
an external device or production credentials.
"""

from __future__ import annotations

import argparse
import ipaddress
import queue
import re
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import paramiko
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


END = b"]]>]]>"
SERVER_HELLO = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<hello xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">'
    b"<capabilities>"
    b"<capability>urn:ietf:params:netconf:base:1.0</capability>"
    b"</capabilities><session-id>101</session-id></hello>" + END
)


class NetconfSSHServer(paramiko.ServerInterface):
    def check_auth_password(self, username: str, password: str) -> int:
        if username == "bundle-test" and password == "bundle-test":
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, _username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, _chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_subsystem_request(self, _channel: object, name: str) -> bool:
        return name == "netconf"


def recv_until(stream: object, marker: bytes = END, timeout: float = 10.0) -> bytes:
    stream.settimeout(timeout)
    data = bytearray()
    while marker not in data:
        chunk = stream.recv(65536)
        if not chunk:
            raise RuntimeError("NETCONF peer closed before a complete message")
        data.extend(chunk)
    return bytes(data)


def serve_netconf(stream: object) -> None:
    client_hello = recv_until(stream)
    if b"hello" not in client_hello:
        raise RuntimeError("client did not send a NETCONF hello")
    stream.sendall(SERVER_HELLO)

    request = recv_until(stream)
    if b"close-session" not in request:
        raise RuntimeError("client did not close the NETCONF session cleanly")
    match = re.search(rb"message-id=['\"]([^'\"]+)['\"]", request)
    if match is None:
        raise RuntimeError("close-session did not contain a message-id")
    reply = (
        b'<rpc-reply xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" '
        b'message-id="' + match.group(1) + b'"><ok/></rpc-reply>' + END
    )
    stream.sendall(reply)


def listen_loopback() -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(15.0)
    return listener, int(listener.getsockname()[1])


def unused_loopback_port() -> int:
    probe, port = listen_loopback()
    probe.close()
    return port


def connect_with_retry(port: int) -> socket.socket:
    deadline = time.monotonic() + 15.0
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=2.0)
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError("frozen Call Home listener did not start") from last_error


def serve_ssh(sock: socket.socket, host_key: paramiko.PKey) -> None:
    transport = paramiko.Transport(sock)
    try:
        transport.add_server_key(host_key)
        transport.start_server(server=NetconfSSHServer())
        channel = transport.accept(10.0)
        if channel is None:
            raise RuntimeError("SSH client did not open a session channel")
        try:
            serve_netconf(channel)
        finally:
            channel.close()
    finally:
        transport.close()


def start_worker(target: Callable[[], None]) -> tuple[threading.Thread, queue.Queue[BaseException]]:
    failures: queue.Queue[BaseException] = queue.Queue()

    def run() -> None:
        try:
            target()
        except BaseException as exc:  # surfaced on the controlling test thread
            failures.put(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, failures


def run_case(
    name: str,
    executable: Path,
    arguments: list[str],
    peer: Callable[[], None],
) -> None:
    thread, failures = start_worker(peer)
    try:
        completed = subprocess.run(
            [str(executable), *arguments, "--hello"],
            cwd=str(executable.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25.0,
            check=False,
        )
    finally:
        thread.join(20.0)
    if thread.is_alive():
        raise RuntimeError(f"{name}: loopback peer did not finish")
    if not failures.empty():
        raise RuntimeError(f"{name}: loopback peer failed") from failures.get()
    if completed.returncode != 0:
        raise RuntimeError(
            f"{name}: executable returned {completed.returncode}\n{completed.stdout}"
        )
    if "Negotiated NETCONF version: 1.0" not in completed.stdout:
        raise RuntimeError(f"{name}: NETCONF hello result was not printed\n{completed.stdout}")
    print(f"[OK] {name}")


def write_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))


def make_certificates(directory: Path) -> dict[str, Path]:
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "netconf-console2 test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    def issue(common_name: str, usage: ExtendedKeyUsageOID, server: bool = False):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        )
        if server:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]),
                critical=False,
            )
        return key, builder.sign(ca_key, hashes.SHA256())

    server_key, server_cert = issue("localhost", ExtendedKeyUsageOID.SERVER_AUTH, True)
    client_key, client_cert = issue("bundle-test", ExtendedKeyUsageOID.CLIENT_AUTH)
    paths = {
        "ca": directory / "ca.pem",
        "server_cert": directory / "server.crt",
        "server_key": directory / "server.key",
        "client_cert": directory / "client.crt",
        "client_key": directory / "client.key",
    }
    paths["ca"].write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    paths["server_cert"].write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    paths["client_cert"].write_bytes(client_cert.public_bytes(serialization.Encoding.PEM))
    write_key(paths["server_key"], server_key)
    write_key(paths["client_key"], client_key)
    return paths


def make_tls_server_context(paths: dict[str, Path]) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(paths["server_cert"]), str(paths["server_key"]))
    context.load_verify_locations(cafile=str(paths["ca"]))
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def tls_peer(sock: socket.socket, context: ssl.SSLContext) -> None:
    with context.wrap_socket(sock, server_side=True) as wrapped:
        if not wrapped.getpeercert():
            raise RuntimeError("TLS client did not present its certificate")
        serve_netconf(wrapped)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    args = parser.parse_args()
    executable = args.exe.resolve()
    if not executable.is_file():
        parser.error(f"executable not found: {executable}")

    host_key = paramiko.RSAKey.generate(2048)
    with tempfile.TemporaryDirectory(prefix="netconf-console2-") as temporary:
        paths = make_certificates(Path(temporary))
        tls_context = make_tls_server_context(paths)
        common = ["--connect-timeout", "10", "--reply-timeout", "10"]
        ssh_auth = [
            "--username", "bundle-test", "--password", "bundle-test",
            "--no-agent", "--no-look-for-keys",
        ]
        tls_auth = [
            "--cert", str(paths["client_cert"]),
            "--key", str(paths["client_key"]),
            "--trusted-ca", str(paths["ca"]),
        ]

        listener, port = listen_loopback()
        run_case(
            "Direct SSH frozen loopback handshake",
            executable,
            ["--transport", "ssh", "--host", "127.0.0.1", "--port", str(port),
             *ssh_auth, *common],
            lambda: _accept_ssh(listener, host_key),
        )

        listener, port = listen_loopback()
        run_case(
            "Direct TLS frozen mTLS loopback handshake",
            executable,
            ["--transport", "tls", "--host", "127.0.0.1", "--port", str(port),
             *tls_auth, *common],
            lambda: _accept_tls(listener, tls_context),
        )

        port = unused_loopback_port()
        run_case(
            "SSH Call Home frozen loopback handshake",
            executable,
            ["--call-home", "--transport", "ssh", "--listen-host", "127.0.0.1",
             "--listen-port", str(port), *ssh_auth, *common],
            lambda: serve_ssh(connect_with_retry(port), host_key),
        )

        port = unused_loopback_port()
        run_case(
            "TLS Call Home frozen mTLS loopback handshake",
            executable,
            ["--call-home", "--transport", "tls", "--listen-host", "127.0.0.1",
             "--listen-port", str(port), "--tls-server-name", "localhost",
             *tls_auth, *common],
            lambda: tls_peer(connect_with_retry(port), tls_context),
        )

    print("All frozen loopback transport handshakes passed.")
    return 0


def _accept_ssh(listener: socket.socket, host_key: paramiko.PKey) -> None:
    with listener:
        sock, _peer = listener.accept()
    serve_ssh(sock, host_key)


def _accept_tls(listener: socket.socket, context: ssl.SSLContext) -> None:
    with listener:
        sock, _peer = listener.accept()
    tls_peer(sock, context)


if __name__ == "__main__":
    raise SystemExit(main())
