"""Real four-mode loopbacks for source or frozen GUI; never contacts a device."""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

import paramiko
from lxml import etree

from netconf_console.gui.client import ReadOptions
from netconf_console.gui.demo import DemoClient, SOURCES
from netconf_console.gui.model import NC, WD, WD_YANG, EditPlan
from netconf_console.session import ConnectionSettings

# Share the well-tested crypto/socket fixture code, not the CLI request loop.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_transport_smoke as transport_fixture

END = transport_fixture.END
IF = "urn:ietf:params:xml:ns:yang:ietf-interfaces"
MONITORING = "urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring"
LIBRARY = "urn:ietf:params:xml:ns:yang:ietf-yang-library"


class Peer:
    def __init__(self):
        self.demo = DemoClient()
        self.received_edit = ""
        self.operations = []

    def serve(self, stream):
        transport_fixture.recv_until(stream)
        hello = etree.Element("{%s}hello" % NC, nsmap={None: NC})
        caps = etree.SubElement(hello, "{%s}capabilities" % NC)
        for cap in (
            "urn:ietf:params:netconf:base:1.0", "urn:netconf-console2:gui-test-peer:1.0",
            "urn:ietf:params:netconf:capability:writable-running:1.0",
            "urn:ietf:params:netconf:capability:with-defaults:1.0?basic-mode=explicit&also-supported=report-all,report-all-tagged",
            "urn:ietf:params:netconf:capability:yang-library:1.1?revision=2019-01-04&content-id=gui-fixture",
        ):
            etree.SubElement(caps, "{%s}capability" % NC).text = cap
        etree.SubElement(hello, "{%s}session-id" % NC).text = "601"
        stream.sendall(etree.tostring(hello) + END)
        while True:
            request = transport_fixture.recv_until(stream).split(END, 1)[0]
            rpc = etree.fromstring(request)
            operation = rpc[0]
            name = etree.QName(operation).localname
            self.operations.append(name)
            reply = etree.Element("{%s}rpc-reply" % NC, nsmap={None: NC}, attrib={"message-id": rpc.get("message-id")})
            if name == "get-schema":
                if operation.find("{%s}format" % MONITORING) is not None:
                    raise AssertionError("Use the default YANG format, not an unbound identityref")
                identifier = operation.findtext("{%s}identifier" % MONITORING)
                etree.SubElement(reply, "{%s}data" % MONITORING).text = SOURCES[identifier]
            elif name in {"get", "get-config"}:
                if any(etree.QName(node).namespace == LIBRARY for node in operation.iter()):
                    data = etree.SubElement(reply, "{%s}data" % NC)
                    state = etree.SubElement(data, "{%s}modules-state" % LIBRARY)
                    etree.SubElement(state, "{%s}module-set-id" % LIBRARY).text = "gui-fixture"
                    for module in SOURCES:
                        item = etree.SubElement(state, "{%s}module" % LIBRARY)
                        etree.SubElement(item, "{%s}name" % LIBRARY).text = module
                        etree.SubElement(item, "{%s}conformance-type" % LIBRARY).text = "implement"
                else:
                    defaults = operation.find("{urn:ietf:params:xml:ns:yang:ietf-netconf-with-defaults}with-defaults") is not None
                    filt = operation.find("{%s}filter" % NC)
                    tag = filt[0].tag if filt is not None and len(filt) else None
                    data = self.demo.read(ReadOptions(defaults=defaults, state=name == "get"), tag).data
                    # Exercise the libyang/sysrepo spelling, as well as the
                    # RFC spelling covered by the standalone widget self-test.
                    for node in data.iter():
                        default = node.attrib.pop("{%s}default" % WD, None)
                        if default is not None:
                            node.set("{%s}default" % WD_YANG, default)
                    reply.append(data)
            elif name == "edit-config":
                self.received_edit = request.decode("utf-8")
                self.demo.apply(None, EditPlan(etree.Element("unused"), rpc, self.received_edit), ReadOptions(defaults=True))
                etree.SubElement(reply, "{%s}ok" % NC)
            elif name in {"lock", "unlock", "close-session"}:
                etree.SubElement(reply, "{%s}ok" % NC)
            else:
                raise AssertionError("Unexpected RPC " + name)
            stream.sendall(etree.tostring(reply) + END)
            if name == "close-session":
                return

    def ssh(self, sock, key):
        with paramiko.Transport(sock) as transport:
            transport.add_server_key(key)
            transport.start_server(server=transport_fixture.NetconfSSHServer())
            channel = transport.accept(15)
            if channel is None:
                raise AssertionError("Missing SSH NETCONF channel")
            with channel:
                self.serve(channel)

    def tls(self, sock, context):
        with context.wrap_socket(sock, server_side=True) as secured:
            if not secured.getpeercert():
                raise AssertionError("Missing TLS client certificate")
            self.serve(secured)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="netconf-gui-loopback-") as temporary:
        directory = Path(temporary)
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
            settings = ConnectionSettings(
                transport=protocol, call_home=call_home, host="127.0.0.1", port=port,
                listen_host="127.0.0.1", listen_port=port, username="bundle-test", password="bundle-test",
                allow_agent=False, look_for_keys=False, hostkey_verify=False, timeout=10, rpc_timeout=10,
                cert=str(certs["client_cert"]), key=str(certs["client_key"]) if protocol == "tls" else None,
                trusted_ca=str(certs["ca"]), tls_server_name="mismatch.fixture.invalid" if protocol == "tls" else None,
                verify_hostname=False,
            )
            config = directory / "settings.json"
            report_path = directory / (protocol + ("-callhome" if call_home else "-direct") + ".json")
            config.write_text(json.dumps(asdict(settings)), encoding="utf-8")
            command = [str(args.exe.resolve())] if args.exe else [sys.executable, "-B", "-m", "netconf_console.gui.app"]
            completed = subprocess.run(command + ["--loopback-test", str(config), "--self-test", str(report_path)],
                                       capture_output=True, timeout=55)
            worker.join(2)
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
            if not report.get("passed") or completed.returncode:
                raise AssertionError((protocol, call_home, report, completed.stderr.decode("utf-8", errors="replace")))
            if worker.is_alive() or not failures.empty():
                raise AssertionError("Loopback peer failed")
            if peer.received_edit != report["wire_xml"]:
                raise AssertionError("Sent XML differs from the GUI preview")
            if peer.operations.count("edit-config") != 1 or not {"lock", "unlock", "get-schema"}.issubset(peer.operations):
                raise AssertionError(peer.operations)
            print("[OK] %s %s: schema, retrieval, exact edit preview and round-trip" % (protocol.upper(), "Call Home" if call_home else "Direct"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
