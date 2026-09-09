"""Drop real local transports and drive GUI auto-reconnect without any writes."""
import socket
import tempfile
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

import paramiko

from gui_transport_smoke import Peer, transport_fixture
from netconf_console.gui.app import NetconfWindow


def pump(root, predicate, seconds=25):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        root.update()
        time.sleep(.02)
    if not predicate():
        raise AssertionError("GUI operation timed out")


def main():
    with tempfile.TemporaryDirectory(prefix="netconf-gui-reconnect-") as folder:
        certs = transport_fixture.make_certificates(Path(folder))
        context = transport_fixture.make_tls_server_context(certs)
        key = paramiko.RSAKey.generate(2048)
        for protocol, call_home in (("ssh", False), ("tls", False), ("ssh", True), ("tls", True)):
            listener, port = transport_fixture.listen_loopback()
            if call_home:
                listener.close()
            peers = [Peer(), Peer()]
            streams = []
            root = tk.Tk()
            root.withdraw()
            window = NetconfWindow(root, persist=False)
            def serve():
                try:
                    for index, peer in enumerate(peers):
                        sock = transport_fixture.connect_with_retry(port) if call_home else listener.accept()[0]
                        # TLS wrap_socket detaches the original socket. Retain
                        # the actual NETCONF stream so the test can break it.
                        original = peer.serve
                        def capture(stream, original=original):
                            streams.append(stream)
                            return original(stream)
                        peer.serve = capture
                        try:
                            if protocol == "ssh":
                                peer.ssh(sock, key)
                            else:
                                peer.tls(sock, context)
                        except (OSError, EOFError, RuntimeError):
                            if index != 0 or not streams:
                                raise
                finally:
                    listener.close()
            worker, failures = transport_fixture.start_worker(serve)
            try:
                for name, value in {
                    "mode": protocol.upper() + " Call Home" if call_home else "Direct " + protocol.upper(),
                    "host": "127.0.0.1", "port": str(port), "listen_host": "127.0.0.1", "listen_port": str(port),
                    "username": "bundle-test", "password": "bundle-test", "timeout": "10", "rpc_timeout": "10",
                    "allow_agent": False, "look_for_keys": False, "auto_reconnect": True,
                    "cert": str(certs["client_cert"]), "tls_key": str(certs["client_key"]), "trusted_ca": str(certs["ca"]),
                }.items():
                    window.vars[name].set(value)
                with patch("netconf_console.gui.app.messagebox.showerror") as errors:
                    window.connect()
                    pump(root, lambda: window.client.connected and window.client.schema.complete and not window.busy)
                    window._expand_item("0")
                    window._show_selection("0/0")
                    draft = window.editor.get().replace(">1500<", ">9000<")
                    window.editor.set(draft)
                    window._update_preview()
                    if protocol == "ssh":
                        streams[0].get_transport().close()
                    else:
                        streams[0].shutdown(socket.SHUT_RDWR)
                        streams[0].close()
                    pump(root, lambda: len(streams) == 2 and not window.busy and window.client.connected
                         and window.status.get().startswith("已重新連線"))
                    assert window.editor.get() == draft, "Draft lost"
                    assert window.uncertain and window.send_button.instate(["disabled"]), "Unsafe send enabled"
                    assert all("edit-config" not in peer.operations for peer in peers), "Unexpected write"
                    errors.assert_not_called()
                    window.reconnect_enabled = False
                    window.client.disconnect()
                worker.join(3)
                assert not worker.is_alive(), "Peer did not exit"
                if not failures.empty():
                    raise failures.get()
                print("[OK] %s %s: transport drop, automatic recovery, draft retained, no write" %
                      (protocol.upper(), "Call Home" if call_home else "Direct"), flush=True)
            finally:
                window.reconnect_enabled = False
                if not window.busy:
                    window.client.disconnect()
                window._destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
