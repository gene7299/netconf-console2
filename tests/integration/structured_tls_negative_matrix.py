"""Executable-level IPv4/IPv6 × Direct/Call Home × N2/N3/N4/N5 tests."""

from __future__ import annotations

import argparse
import ipaddress
import json
import queue
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


END = b"]]>]]>"
SERVER_HELLO = (
    b'<hello xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><capabilities>'
    b'<capability>urn:ietf:params:netconf:base:1.0</capability>'
    b'</capabilities><session-id>311</session-id></hello>' + END
)


def write_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))


def make_ca(name: str, now: datetime):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def issue(ca_key, ca_cert, name: str, usage, now: datetime, *, server=False, expired=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    before = now - timedelta(days=3) if expired else now - timedelta(minutes=5)
    after = now - timedelta(days=2) if expired else now + timedelta(days=7)
    builder = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(ca_cert.subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(before).not_valid_after(after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
    )
    if server:
        builder = builder.add_extension(x509.SubjectAlternativeName([
            x509.DNSName("oru.test.invalid"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            x509.IPAddress(ipaddress.ip_address("::1")),
        ]), critical=False)
    return key, builder.sign(ca_key, hashes.SHA256())


def materials(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ca_key, ca_cert = make_ca("matrix good CA", now)
    bad_ca_key, bad_ca_cert = make_ca("matrix unrelated CA", now)
    server_key, server_cert = issue(ca_key, ca_cert, "oru.test.invalid", ExtendedKeyUsageOID.SERVER_AUTH, now, server=True)
    client_key, client_cert = issue(ca_key, ca_cert, "controller", ExtendedKeyUsageOID.CLIENT_AUTH, now)
    bad_client_key, bad_client_cert = issue(bad_ca_key, bad_ca_cert, "bad controller", ExtendedKeyUsageOID.CLIENT_AUTH, now)
    expired_key, expired_cert = issue(ca_key, ca_cert, "expired controller", ExtendedKeyUsageOID.CLIENT_AUTH, now, expired=True)
    output = {}
    objects = {
        "ca": ca_cert, "bad_ca": bad_ca_cert, "server_cert": server_cert,
        "client_cert": client_cert, "bad_client_cert": bad_client_cert,
        "expired_client_cert": expired_cert,
    }
    keys = {
        "server_key": server_key, "client_key": client_key,
        "bad_client_key": bad_client_key, "expired_client_key": expired_key,
    }
    for name, cert in objects.items():
        path = directory / (name + ".pem")
        path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        output[name] = path
    for name, key in keys.items():
        path = directory / (name + ".pem")
        write_key(path, key)
        output[name] = path
    return output


def listener(family: int) -> tuple[socket.socket, str, int]:
    host = "127.0.0.1" if family == socket.AF_INET else "::1"
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.bind((host, 0))
    sock.listen(1)
    sock.settimeout(8)
    return sock, host, int(sock.getsockname()[1])


def server_context(paths: dict[str, Path]) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(paths["server_cert"]), str(paths["server_key"]))
    context.load_verify_locations(cafile=str(paths["ca"]))
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def serve_rejection(sock: socket.socket, context: ssl.SSLContext, failures: queue.Queue) -> None:
    try:
        with context.wrap_socket(sock, server_side=True) as wrapped:
            # N3/N5 are rejected by the client after the server certificate;
            # N2/N4 are rejected by this server during client authentication.
            wrapped.recv(1)
    except BaseException as exc:
        failures.put(exc)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def connect_retry(host: str, port: int, family: int, process: subprocess.Popen) -> socket.socket:
    deadline = time.monotonic() + 8
    last = None
    while time.monotonic() < deadline:
        try:
            return socket.create_connection((host, port), timeout=0.5)
        except OSError as exc:
            last = exc
            if process.poll() is not None:
                break
            time.sleep(0.05)
    raise RuntimeError("Call Home listener did not start") from last


def run_case(exe: Path, root: Path, paths: dict[str, Path], family: int, mode: str, flow: str) -> dict:
    listen, host, port = listener(family)
    context = server_context(paths)
    output = root / (f"ipv{4 if family == socket.AF_INET else 6}-{mode}-{flow}")
    output.mkdir()
    if flow == "N2":
        cert, key, trusted, peername = paths["bad_client_cert"], paths["bad_client_key"], paths["ca"], host
        expected = "TLS_CLIENT_CERTIFICATE_UNTRUSTED_REJECTED"
    elif flow == "N3":
        cert, key, trusted, peername = paths["client_cert"], paths["client_key"], paths["bad_ca"], host
        expected = "TLS_SERVER_CERTIFICATE_REJECTED"
    elif flow == "N4":
        cert, key, trusted, peername = paths["expired_client_cert"], paths["expired_client_key"], paths["ca"], host
        expected = "TLS_CLIENT_CERTIFICATE_EXPIRED_REJECTED"
    else:
        cert, key, trusted, peername = paths["client_cert"], paths["client_key"], paths["ca"], "wrong-oru.test.invalid"
        expected = "TLS_SERVER_IDENTITY_REJECTED"
    command = [
        str(exe), "--test-api", "--transport", "tls", "--cert", str(cert),
        "--key", str(key), "--trusted-ca", str(trusted),
        "--tls-server-name", peername, "--connect-timeout", "5",
        "--result-file", str(output / "result.json"),
        "--events-file", str(output / "events.jsonl"),
    ]
    if flow in {"N2", "N4"}:
        # TLS 1.2 delivers the server's certificate-authentication alert in
        # the handshake itself. TLS 1.3 may complete the client side first
        # and surface only EOF on the first NETCONF read, which cannot prove
        # a rejection reason without server-side evidence.
        command += ["--tls-version", "1.2"]
    failures = queue.Queue()
    if mode == "direct":
        command += ["--host", host, "--port", str(port), "--bind", host]
        thread = threading.Thread(
            target=lambda: _accept_and_reject(listen, context, failures), daemon=True
        )
        thread.start()
        completed = subprocess.run(command, capture_output=True, text=True, timeout=12)
    else:
        listen.close()
        command += ["--call-home", "--listen-host", host, "--listen-port", str(port)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        connected = connect_retry(host, port, family, process)
        thread = threading.Thread(target=serve_rejection, args=(connected, context, failures), daemon=True)
        thread.start()
        stdout, stderr = process.communicate(timeout=12)
        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    thread.join(8)
    if thread.is_alive():
        raise RuntimeError("peer did not finish: %s %s %s" % (family, mode, flow))
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    actual = result["classification"]["code"]
    passed = actual == expected and completed.returncode == 10
    return {
        "family": 4 if family == socket.AF_INET else 6,
        "mode": mode,
        "flow": flow,
        "expected": expected,
        "actual": actual,
        "client_returncode": completed.returncode,
        "peer_error": type(failures.get()).__name__ if not failures.empty() else None,
        "status": "PASS" if passed else "FAIL",
        "result_file": str(output / "result.json"),
    }


def _accept_and_reject(listener_socket, context, failures):
    try:
        connected, _peer = listener_socket.accept()
    finally:
        listener_socket.close()
    serve_rejection(connected, context, failures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    exe = args.exe.resolve()
    if not exe.is_file():
        parser.error("executable not found: %s" % exe)
    temporary = None
    if args.output:
        root = args.output.resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="netconf-console2-negative-")
        root = Path(temporary.name)
    paths = materials(root / "pki")
    rows = []
    try:
        for family in (socket.AF_INET, socket.AF_INET6):
            if family == socket.AF_INET6:
                try:
                    probe, _host, _port = listener(family)
                    probe.close()
                except OSError:
                    print("IPv6 loopback unavailable; matrix cannot be complete")
                    return 2
            for mode in ("direct", "call-home"):
                for flow in ("N2", "N3", "N4", "N5"):
                    row = run_case(exe, root, paths, family, mode, flow)
                    rows.append(row)
                    print("[{status}] IPv{family} {mode} {flow}: {actual}".format(**row))
        report = {
            "version": 1,
            "executable": str(exe),
            "passed": sum(row["status"] == "PASS" for row in rows),
            "total": len(rows),
            "cases": rows,
        }
        (root / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0 if report["passed"] == report["total"] == 16 else 1
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
