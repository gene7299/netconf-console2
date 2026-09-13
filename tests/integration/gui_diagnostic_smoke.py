"""Four real loopback diagnostic sessions; assert read-only RPC allowlist."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import paramiko

from gui_transport_smoke import Peer, transport_fixture
from netconf_console.session import ConnectionSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ncc-diagnostic-") as folder:
        directory = Path(folder)
        certs = transport_fixture.make_certificates(directory)
        tls_context = transport_fixture.make_tls_server_context(certs)
        key = paramiko.RSAKey.generate(2048)
        for protocol, call_home in (("ssh", False), ("tls", False), ("ssh", True), ("tls", True)):
            peer = Peer()
            listener, port = transport_fixture.listen_loopback()
            if call_home:
                listener.close()
            def serve():
                if call_home:
                    sock = transport_fixture.connect_with_retry(port)
                else:
                    with listener:
                        sock, _ = listener.accept()
                if protocol == "ssh":
                    peer.ssh(sock, key)
                else:
                    peer.tls(sock, tls_context)
            worker, failures = transport_fixture.start_worker(serve)
            settings = ConnectionSettings(transport=protocol, call_home=call_home, host="127.0.0.1", port=port,
                listen_host="127.0.0.1", listen_port=port, username="bundle-test", password="bundle-test",
                allow_agent=False, look_for_keys=False, hostkey_verify=False, ssh_auth="password", timeout=10, rpc_timeout=10,
                cert=str(certs["client_cert"]), key=str(certs["client_key"]) if protocol == "tls" else None,
                trusted_ca=str(certs["ca"]), tls_server_name="fixture.invalid", verify_hostname=False)
            config, output = directory/"settings.json", directory/"report.json"
            config.write_text(json.dumps(asdict(settings)), encoding="utf-8")
            command = [str(args.exe.resolve())] if args.exe else [sys.executable, "-B", "-m", "netconf_console.gui.app"]
            completed = subprocess.run(command + ["--connection-diagnostic-test", str(config), "--self-test", str(output)],
                                       capture_output=True, timeout=55)
            worker.join(3)
            report = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
            assert completed.returncode == 0 and report.get("passed"), (report, completed.stderr)
            assert not worker.is_alive() and failures.empty(), "peer cleanup failed"
            stages = [row["stage"] for row in report["stages"]]
            assert {"TCP 連線／接入", "SSH 認證／TLS 憑證握手", "NETCONF subsystem／hello", "YANG schema 載入"}.issubset(stages), stages
            if call_home:
                assert "Call Home 監聽" in stages
            assert set(peer.operations) <= {"get", "get-schema", "close-session"}, peer.operations
            raw = output.read_text(encoding="utf-8")
            assert "bundle-test" not in raw and "127.0.0.1" not in raw and str(certs["client_key"]) not in raw
            print("[OK] %s %s: timed TCP/auth/hello/schema, redacted report, read-only RPCs, cleanup" % (protocol.upper(), "Call Home" if call_home else "Direct"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
