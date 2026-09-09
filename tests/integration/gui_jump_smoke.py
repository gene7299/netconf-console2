"""Real, strictly loopback SSH jump forwarding for source and frozen GUI."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import time

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_transport_smoke import Peer
import frozen_transport_smoke as fixture
from netconf_console.jump import open_jump
from netconf_console.session import ConnectionSettings


class JumpServer(paramiko.ServerInterface):
    def __init__(self, destination, deny=False):
        self.destination, self.deny = destination, deny
        self.authenticated = False
        self.forwarded = False

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        self.authenticated = (username, password) == ("jump-user", "jump-only-secret")
        return paramiko.AUTH_SUCCESSFUL if self.authenticated else paramiko.AUTH_FAILED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        assert destination == self.destination, (destination, self.destination)
        self.forwarded = not self.deny
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED if self.deny else paramiko.OPEN_SUCCEEDED


def serve_jump(listener, key, server):
    with listener:
        listener.settimeout(20)
        sock, _ = listener.accept()
    with paramiko.Transport(sock) as transport:
        transport.add_server_key(key)
        try:
            transport.start_server(server=server)
            channel = transport.accept(15)
            if channel is None:
                return
            with channel, socket.create_connection(server.destination, timeout=10) as upstream:
                while transport.is_active():
                    readable, _, _ = select.select([channel, upstream], [], [], 1)
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is channel else channel).sendall(data)
        except (EOFError, ConnectionResetError):
            return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ncc-jump-loopback-") as temporary:
        directory = Path(temporary)
        jump_key, target_key = paramiko.RSAKey.generate(2048), paramiko.RSAKey.generate(2048)
        target_listener, target_port = fixture.listen_loopback()
        jump_listener, jump_port = fixture.listen_loopback()
        peer = Peer()
        def target():
            with target_listener:
                target_listener.settimeout(30)
                sock, _ = target_listener.accept()
            peer.ssh(sock, target_key)
        target_worker, target_errors = fixture.start_worker(target)
        server = JumpServer(("127.0.0.1", target_port))
        jump_worker, jump_errors = fixture.start_worker(lambda: serve_jump(jump_listener, jump_key, server))
        known_hosts = directory / "known_hosts"
        hosts = paramiko.HostKeys()
        hosts.add("[127.0.0.1]:%d" % jump_port, jump_key.get_name(), jump_key)
        hosts.add("[127.0.0.1]:%d" % target_port, target_key.get_name(), target_key)
        hosts.save(str(known_hosts))
        settings = ConnectionSettings(host="127.0.0.1", port=target_port, username="bundle-test", password="bundle-test",
            ssh_auth="password", allow_agent=False, look_for_keys=False, hostkey_verify=True, known_hosts=str(known_hosts),
            timeout=10, rpc_timeout=10, jump_enabled=True, jump_host="127.0.0.1", jump_port=jump_port,
            jump_username="jump-user", jump_password="jump-only-secret", jump_verify=True, jump_known_hosts=str(known_hosts))
        config, report_path = directory / "settings.json", directory / "report.json"
        config.write_text(json.dumps(asdict(settings)), encoding="utf-8")
        command = [str(args.exe.resolve())] if args.exe else [sys.executable, "-B", "-m", "netconf_console.gui.app"]
        result = subprocess.run(command + ["--loopback-test", str(config), "--self-test", str(report_path)], capture_output=True, timeout=70)
        target_worker.join(3)
        jump_worker.join(3)
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        assert result.returncode == 0 and report.get("passed"), (report, result.stderr.decode(errors="replace"))
        assert not target_worker.is_alive() and not jump_worker.is_alive(), "Tunnel not closed"
        assert target_errors.empty() and jump_errors.empty(), "Fixture thread failed"
        assert server.authenticated and server.forwarded
        assert peer.received_edit == report["wire_xml"] and peer.test_xml == report["test_xml"]
        assert peer.creation_xml == report["creation_xml"] and peer.creation_test_xml == report["creation_test_xml"]
        assert not peer.locks and not peer.confirmed_token
        print("[OK] SSH jump: separate credentials + both verified host keys + full NETCONF operations + cleanup", flush=True)
        # Negative checks never reach a NETCONF endpoint or send a config RPC.
        for failure in ("host-key", "password", "forward-denied"):
            listener, port = fixture.listen_loopback()
            rejecting = JumpServer(("127.0.0.1", target_port), deny=failure == "forward-denied")
            worker, errors = fixture.start_worker(lambda: serve_jump(listener, jump_key, rejecting))
            hosts.add("[127.0.0.1]:%d" % port, jump_key.get_name(), target_key if failure == "host-key" else jump_key)
            hosts.save(str(known_hosts))
            bad = settings.copy(jump_port=port, jump_password="wrong" if failure == "password" else settings.jump_password)
            try:
                owner, channel = open_jump(bad)
            except Exception:
                pass
            else:
                owner.close()
                raise AssertionError("Negative jump test unexpectedly succeeded: " + failure)
            worker.join(3)
            assert not worker.is_alive() and errors.empty(), "Failed jump did not clean up"
            assert not rejecting.forwarded
            print("[OK] SSH jump rejects %s and closes transport" % failure, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
