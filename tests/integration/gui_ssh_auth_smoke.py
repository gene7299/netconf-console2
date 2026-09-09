"""Reproduce publickey-only rejection with synthetic loopback SSH peers."""
import tempfile
from pathlib import Path

import paramiko
from ncclient.transport.errors import AuthenticationError

from gui_transport_smoke import Peer, transport_fixture
from netconf_console.gui.client import GuiClient, ReadOptions
from netconf_console.session import ConnectionSettings


def main():
    with tempfile.TemporaryDirectory(prefix="netconf-gui-auth-") as folder:
        client_key = paramiko.RSAKey.generate(2048)
        private = Path(folder) / "synthetic-client-key"
        client_key.write_private_key_file(str(private))
        encrypted = Path(folder) / "synthetic-encrypted-client-key"
        client_key.write_private_key_file(str(encrypted), password="synthetic-passphrase")
        host_key = paramiko.RSAKey.generate(2048)
        class KeyOnly(transport_fixture.NetconfSSHServer):
            def get_allowed_auths(self, username):
                return "publickey"

            def check_auth_password(self, username, password):
                return paramiko.AUTH_FAILED

            def check_auth_publickey(self, username, key):
                return paramiko.AUTH_SUCCESSFUL if username == "fixture" and key == client_key else paramiko.AUTH_FAILED

        for call_home in (False, True):
            for key_path, password, mode, passphrase, expected_success, label in (
                (None, "synthetic-password", "legacy", None, False, "legacy password rejected with BadAuthenticationType/publickey"),
                (str(private), None, "legacy", None, True, "legacy unencrypted private key accepted with empty password"),
                (str(private), "synthetic-password", "legacy", None, False, "legacy unused password masks unencrypted-key load failure"),
                (str(encrypted), "synthetic-passphrase", "legacy", None, True, "legacy encrypted key accepted with matching passphrase"),
                (str(private), "synthetic-password", "auto", None, True, "GUI auto ignores login password when loading unencrypted key"),
                (str(private), "synthetic-password", "private-key", None, True, "GUI explicit private-key authentication"),
                (str(encrypted), "different-login-password", "auto", "synthetic-passphrase", True, "GUI encrypted key uses independent passphrase"),
                (str(encrypted), "synthetic-passphrase", "private-key", "wrong", False, "GUI wrong key passphrase never falls back to login password"),
                (str(private), "synthetic-password", "password", None, False, "GUI password-only does not silently use selected key"),
            ):
                listener, port = transport_fixture.listen_loopback()
                if call_home:
                    listener.close()
                def serve():
                    try:
                        sock = transport_fixture.connect_with_retry(port) if call_home else listener.accept()[0]
                        with paramiko.Transport(sock) as transport:
                            transport.add_server_key(host_key)
                            transport.start_server(server=KeyOnly())
                            channel = transport.accept(8)
                            if channel is not None:
                                try:
                                    Peer().serve(channel)
                                finally:
                                    try:
                                        channel.close()
                                    except (EOFError, OSError):
                                        pass  # Peer may close after close-session reply.
                            elif expected_success:
                                raise AssertionError("Key authentication unexpectedly failed")
                    finally:
                        listener.close()
                worker, failures = transport_fixture.start_worker(serve)
                client = GuiClient()
                try:
                    settings = ConnectionSettings(host="127.0.0.1", port=port, call_home=call_home,
                        listen_host="127.0.0.1", listen_port=port, username="fixture",
                        password=password, key=key_path, allow_agent=False, look_for_keys=False,
                        ssh_auth=mode, key_passphrase=passphrase,
                        hostkey_verify=False, timeout=8, rpc_timeout=8)
                    try:
                        client.connect(settings)
                    except AuthenticationError as exc:
                        assert not expected_success
                        if mode == "legacy":
                            assert "BadAuthenticationType" in str(exc) and "publickey" in str(exc)
                        else:
                            assert "私鑰密碼" in str(exc) and "synthetic-passphrase" not in str(exc)
                    else:
                        assert expected_success, "Password must not pass a key-only server"
                        client.read(ReadOptions())
                finally:
                    client.disconnect()
                worker.join(10)
                assert not worker.is_alive(), "Peer did not stop"
                if not failures.empty():
                    raise failures.get()
                print("[OK] SSH %s: %s" % ("Call Home" if call_home else "Direct", label), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
