"""Real SSH exec/stdin/exit-status tests against an in-memory synthetic sysrepo."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time

import paramiko
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_transport_smoke as fixture
from netconf_console.gui.demo import DemoClient
from netconf_console.gui.client import ReadOptions
from netconf_console.session import ConnectionSettings


class ExecServer(paramiko.ServerInterface):
    def __init__(self):
        self.commands = {}
        self.changed = threading.Event()

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ("system-admin", "synthetic-system-secret") else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        self.commands[channel.get_id()] = command.decode()
        self.changed.set()
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    with __import__("tempfile").TemporaryDirectory(prefix="ncc-system-ssh-test-") as temporary:
        directory = Path(temporary)
        key = paramiko.RSAKey.generate(2048)
        for mode in ("success", "conflict", "denied", "lost-reply"):
            listener, port = fixture.listen_loopback()
            demo = DemoClient()
            data = demo.read(ReadOptions(defaults=True)).data[0]
            if mode == "conflict":
                data.find(".//{urn:o-ran:interfaces:1.0}l2-mtu").text = "9001"
            edits, commands = [], []
            def serve():
                with listener:
                    sock, _ = listener.accept()
                with paramiko.Transport(sock) as transport:
                    transport.add_server_key(key)
                    server = ExecServer()
                    transport.start_server(server=server)
                    while transport.is_active():
                        channel = transport.accept(1)
                        if channel is None:
                            continue
                        with channel:
                            deadline = time.monotonic() + 5
                            while channel.get_id() not in server.commands:
                                if time.monotonic() >= deadline:
                                    raise TimeoutError("No exec request")
                                server.changed.wait(0.05)
                            command = server.commands[channel.get_id()]
                            commands.append(command)
                            payload = bytearray()
                            channel.settimeout(15)
                            while True:
                                chunk = channel.recv(32768)
                                if not chunk:
                                    break
                                payload.extend(chunk)
                            if command == "sysrepocfg --version":
                                channel.sendall(b"ncc-synthetic-sysrepo-peer\n")
                            elif "--export" in shlex.split(command):
                                assert not payload
                                channel.sendall(etree.tostring(data))
                            elif "--edit" in shlex.split(command):
                                assert "--lock" in shlex.split(command) and "--import" not in command
                                edits.append(bytes(payload))
                                if mode == "denied":
                                    channel.send_stderr(b"synthetic permission denied")
                                    channel.send_exit_status(1)
                                    continue
                                root = etree.fromstring(payload)
                                assert root.tag.endswith("interfaces")
                                assert root.get("{http://www.sysrepo.org/yang/sysrepo}operation") == "none"
                                assert all(node.get("{urn:ietf:params:xml:ns:netconf:base:1.0}operation") in {None, "merge", "remove", "replace", "create", "delete"} for node in root.iter())
                                assert "password" not in payload.decode() and "oper-status" not in payload.decode()
                                mtu = root.find(".//{urn:o-ran:interfaces:1.0}l2-mtu")
                                data.find(".//{urn:o-ran:interfaces:1.0}l2-mtu").text = mtu.text
                                if mode == "lost-reply":
                                    while transport.is_active() and not channel.closed:
                                        time.sleep(0.02)
                                    continue
                            else:
                                raise AssertionError(command)
                            channel.send_exit_status(0)
            worker, failures = fixture.start_worker(serve)
            known = directory / "known_hosts"
            hosts = paramiko.HostKeys()
            hosts.add("[127.0.0.1]:%d" % port, key.get_name(), key)
            hosts.save(str(known))
            settings = ConnectionSettings(host="127.0.0.1", port=port, username="system-admin", password="synthetic-system-secret",
                ssh_auth="password", hostkey_verify=True, known_hosts=str(known), timeout=5)
            config, report_file = directory / "settings.json", directory / (mode + ".json")
            config.write_text(json.dumps(asdict(settings)), encoding="utf-8")
            command = [str(args.exe.resolve())] if args.exe else [sys.executable, "-B", "-m", "netconf_console.gui.app"]
            result = subprocess.run(command + ["--sysrepo-loopback-test", str(config), "--self-test", str(report_file)],
                                    capture_output=True, timeout=40)
            worker.join(3)
            report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else {}
            assert not worker.is_alive() and failures.empty(), "Fixture cleanup failed"
            assert bool(report.get("passed")) == (mode == "success"), (mode, report)
            assert result.returncode == (0 if mode == "success" else 1), (mode, result.stderr)
            assert len(edits) == (0 if mode == "conflict" else 1), "Unexpected retry/write"
            if edits:
                assert edits[0].decode() == report["payload"]
            print("[OK] system SSH %s: verified host key, exec/stdin, bounded result, no retry" % mode, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
