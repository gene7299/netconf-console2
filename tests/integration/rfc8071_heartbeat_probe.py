"""Capture and verify RFC 8071 C4 in the exact headless executable.

The fake O-RU accepts TCP but intentionally does not complete TLS.  This lets
the probe parse the unencrypted ClientHello directly while tcpdump records the
same bytes in a PCAP for independent Wireshark review.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from frozen_transport_smoke import make_certificates, unused_loopback_port


def receive_record(sock: socket.socket) -> tuple[bytes, bytes]:
    sock.settimeout(5.0)
    header = b""
    while len(header) < 5:
        part = sock.recv(5 - len(header))
        if not part:
            raise RuntimeError("client closed before the TLS record header")
        header += part
    length = int.from_bytes(header[3:5], "big")
    payload = b""
    while len(payload) < length:
        part = sock.recv(length - len(payload))
        if not part:
            raise RuntimeError("client closed before the complete TLS record")
        payload += part
    return header, payload


def client_hello_extensions(header: bytes, payload: bytes) -> list[dict[str, object]]:
    if header[0] != 22 or not payload or payload[0] != 1:
        raise RuntimeError("first TLS record is not a ClientHello")
    handshake_length = int.from_bytes(payload[1:4], "big")
    body = payload[4:4 + handshake_length]
    offset = 2 + 32
    session_length = body[offset]
    offset += 1 + session_length
    cipher_length = int.from_bytes(body[offset:offset + 2], "big")
    offset += 2 + cipher_length
    compression_length = body[offset]
    offset += 1 + compression_length
    extensions_length = int.from_bytes(body[offset:offset + 2], "big")
    offset += 2
    end = offset + extensions_length
    result = []
    while offset + 4 <= end:
        extension_type = int.from_bytes(body[offset:offset + 2], "big")
        length = int.from_bytes(body[offset + 2:offset + 4], "big")
        value = body[offset + 4:offset + 4 + length]
        offset += 4 + length
        result.append({"type": extension_type, "value_hex": value.hex()})
    if offset != end:
        raise RuntimeError("malformed ClientHello extension list")
    return result


def pcap_client_hello_extensions(filename: Path, listener_port: int) -> list[list[dict[str, object]]]:
    """Parse complete TLS ClientHello records from classic Ethernet PCAP.

    This deliberately does not trust the bytes read from the live socket: the
    formal C4 report must prove that the same ClientHello is present in the
    requested capture.  The loopback probe emits one small, unfragmented TLS
    record, so TCP stream reassembly is neither necessary nor silently faked.
    """
    data = filename.read_bytes()
    if len(data) < 24:
        raise RuntimeError("PCAP is missing or shorter than its global header")
    magic = data[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian = ">"
    else:
        raise RuntimeError("unsupported PCAP magic")
    linktype = struct.unpack(endian + "I", data[20:24])[0]
    if linktype != 1:
        raise RuntimeError(f"expected Ethernet PCAP from Linux loopback, got linktype {linktype}")
    offset = 24
    found = []
    while offset + 16 <= len(data):
        _sec, _fraction, captured, _original = struct.unpack(
            endian + "IIII", data[offset:offset + 16]
        )
        offset += 16
        packet = data[offset:offset + captured]
        offset += captured
        if len(packet) < 14:
            continue
        network_offset = 14
        ethertype = int.from_bytes(packet[12:14], "big")
        if ethertype == 0x8100 and len(packet) >= 18:
            ethertype = int.from_bytes(packet[16:18], "big")
            network_offset = 18
        if ethertype == 0x0800 and len(packet) >= network_offset + 20:
            ihl = (packet[network_offset] & 0x0F) * 4
            if packet[network_offset + 9] != 6:
                continue
            tcp_offset = network_offset + ihl
        elif ethertype == 0x86DD and len(packet) >= network_offset + 40:
            if packet[network_offset + 6] != 6:
                continue
            tcp_offset = network_offset + 40
        else:
            continue
        if len(packet) < tcp_offset + 20:
            continue
        _source_port = int.from_bytes(packet[tcp_offset:tcp_offset + 2], "big")
        destination_port = int.from_bytes(packet[tcp_offset + 2:tcp_offset + 4], "big")
        header_length = (packet[tcp_offset + 12] >> 4) * 4
        payload_offset = tcp_offset + header_length
        payload = packet[payload_offset:]
        # Call Home reverses the TCP initiator: the client's ClientHello is
        # sent from an ephemeral source port to the listener port.
        if destination_port != listener_port or len(payload) < 9 or payload[0] != 22:
            continue
        record_length = int.from_bytes(payload[3:5], "big")
        if len(payload) < 5 + record_length:
            continue
        record = payload[5:5 + record_length]
        if record and record[0] == 1:
            found.append(client_hello_extensions(payload[:5], record))
    return found


def start_capture(filename: Path, port: int) -> subprocess.Popen[str] | None:
    # Capture exactly the first TLS handshake record, not the listener-startup
    # SYN/RST probes.  ``-c 1`` makes tcpdump close and flush the evidence by
    # itself once the ClientHello is observed.
    tls_record = "tcp[((tcp[12] & 0xf0) >> 2):1] = 0x16"
    command = ["tcpdump", "--immediate-mode", "-i", "lo", "-U", "-c", "1", "-w", str(filename),
               "tcp", "port", str(port), "and", tls_record]
    if os.geteuid() != 0:
        check = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True)
        if check.returncode:
            raise RuntimeError(
                "PCAP was requested but non-interactive sudo is unavailable; run sudo -v in this terminal"
            )
        command.insert(0, "sudo")
        command.insert(1, "-n")
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("tcpdump exited before capture started: " + (process.stderr.read() if process.stderr else ""))
        # tcpdump opens the output promptly; a short stable delay avoids a SYN
        # racing filter activation without parsing localized stderr text.
        if filename.exists():
            time.sleep(0.2)
            return process
        time.sleep(0.05)
    os.killpg(process.pid, signal.SIGTERM)
    raise RuntimeError("tcpdump did not create the PCAP within 5 seconds")


def stop_capture(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGINT)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--pcap", required=True, type=Path,
                        help="Required PCAP evidence; PASS requires extension 15/value 01 in this file")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    executable = args.exe.resolve()
    if not executable.is_file():
        parser.error("executable not found: %s" % executable)

    port = unused_loopback_port()
    capture = None
    pcap_path = args.pcap.resolve()
    args.pcap.parent.mkdir(parents=True, exist_ok=True)
    capture = start_capture(pcap_path, port)

    try:
        with tempfile.TemporaryDirectory(prefix="netconf-console2-heartbeat-") as temporary:
            directory = Path(temporary)
            keys = make_certificates(directory)
            result_file = directory / "client-result.json"
            events_file = directory / "client-events.jsonl"
            command = [
                str(executable), "--test-api", "--call-home", "--transport", "tls",
                "--listen-host", "127.0.0.1", "--listen-port", str(port),
                "--cert", str(keys["client_cert"]), "--key", str(keys["client_key"]),
                "--trusted-ca", str(keys["ca"]), "--no-hostname-verify",
                "--connect-timeout", "3", "--result-file", str(result_file),
                "--events-file", str(events_file),
            ]
            client = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
            )
            peer = None
            for _attempt in range(100):
                try:
                    peer = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                    break
                except OSError:
                    if client.poll() is not None:
                        break
                    time.sleep(0.05)
            if peer is None:
                stdout, stderr = client.communicate(timeout=5)
                raise RuntimeError("Call Home listener did not start: %s %s" % (stdout, stderr))
            with peer:
                header, payload = receive_record(peer)
            stdout, stderr = client.communicate(timeout=10)
            if capture is not None:
                # Give libpcap packet-buffered output time to commit the final
                # loopback TLS segment before requesting a clean shutdown.
                time.sleep(0.5)
                stop_capture(capture)
                capture = None
            extensions = client_hello_extensions(header, payload)
            heartbeat = [item for item in extensions if item["type"] == 15]
            extension_passed = heartbeat == [{"type": 15, "value_hex": "01"}]
            captured_client_hellos = pcap_client_hello_extensions(pcap_path, port)
            pcap_heartbeat = [
                item for hello in captured_client_hellos for item in hello
                if item["type"] == 15
            ]
            pcap_captured = bool(captured_client_hellos)
            pcap_extension_passed = pcap_heartbeat == [{"type": 15, "value_hex": "01"}]
            passed = extension_passed and pcap_captured and pcap_extension_passed
            report = {
                "version": 1,
                "requirement": "RFC 8071 section 3.1 C4",
                "executable": str(executable),
                "extension_types": [item["type"] for item in extensions],
                "heartbeat_extensions": heartbeat,
                "peer_allowed_to_send": extension_passed,
                "pcap_client_hello_count": len(captured_client_hellos),
                "pcap_heartbeat_extensions": pcap_heartbeat,
                "pcap_peer_allowed_to_send": pcap_extension_passed,
                "client_returncode": client.returncode,
                "client_result": json.loads(result_file.read_text(encoding="utf-8")),
                "pcap": str(pcap_path),
                "pcap_captured": pcap_captured,
                "status": "PASS" if passed else "FAIL",
            }
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if passed else 1
    finally:
        if capture is not None:
            stop_capture(capture)


if __name__ == "__main__":
    raise SystemExit(main())
