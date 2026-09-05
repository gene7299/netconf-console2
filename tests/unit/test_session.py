from __future__ import annotations

import io
import socket
import threading
import unittest
from unittest.mock import MagicMock, patch

from netconf_console.operations import ConsoleDeviceHandler
from netconf_console.session import (
    CallHomeListener,
    CallHomeCancelled,
    ConnectionSettings,
    TracedTLSSession,
    TracedSSHSession,
    _tls_version,
    build_tls_context,
    open_call_home,
    open_direct,
)
from netconf_console.trace import TraceSink, redact_secrets


class SessionAdapterTests(unittest.TestCase):
    def test_call_home_listener_accepts_and_closes(self):
        with CallHomeListener("127.0.0.1", 0, timeout=2) as listener:
            port = listener.socket.getsockname()[1]
            result = {}

            def connect():
                sock = socket.create_connection(("127.0.0.1", port))
                result["peer"] = sock.getsockname()
                sock.close()

            thread = threading.Thread(target=connect)
            thread.start()
            accepted, peer = listener.accept()
            accepted.close()
            thread.join(2)
            self.assertEqual(peer[0], "127.0.0.1")
            self.assertIn("127.0.0.1", listener.listen_address)

    def test_call_home_listener_timeout(self):
        with self.assertRaises(TimeoutError):
            with CallHomeListener("127.0.0.1", 0, timeout=0.1) as listener:
                listener.accept()

    def test_call_home_listener_cancel(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CallHomeCancelled):
            with CallHomeListener("127.0.0.1", 0, timeout=2) as listener:
                listener.accept(cancel)

    @patch("netconf_console.session._manager_for_session", return_value="manager")
    @patch("netconf_console.session.TracedSSHSession")
    @patch("netconf_console.session.CallHomeListener")
    def test_ssh_call_home_passes_accepted_socket_to_ncclient(
        self, listener_class, session_class, manager_for_session
    ):
        accepted, peer_side = socket.socketpair()
        try:
            listener = listener_class.return_value.__enter__.return_value
            listener.listen_address = "0.0.0.0:4334"
            listener.accept.return_value = (accepted, ("192.0.2.10", 51234))
            settings = ConnectionSettings(
                transport="ssh", host="127.0.0.1", listen_host="0.0.0.0", listen_port=4334,
                username="oranuser", password="masked",
            )
            manager, metadata = open_call_home(settings)
            self.assertEqual(manager, "manager")
            self.assertEqual(metadata.mode, "Call Home")
            self.assertEqual(metadata.peer_address, "192.0.2.10:51234")
            self.assertEqual(metadata.remote_port, 51234)
            self.assertEqual(metadata.listen_address, "0.0.0.0:4334")
            kwargs = session_class.return_value.connect.call_args.kwargs
            self.assertIs(kwargs["sock"], accepted)
            self.assertEqual(kwargs["username"], "oranuser")
            manager_for_session.assert_called_once()
        finally:
            accepted.close()
            peer_side.close()

    @patch("netconf_console.session._manager_for_session", return_value="manager")
    @patch("netconf_console.session.TracedTLSSession")
    @patch("netconf_console.session.CallHomeListener")
    def test_tls_call_home_starts_from_accepted_socket(
        self, listener_class, session_class, manager_for_session
    ):
        accepted, peer_side = socket.socketpair()
        try:
            listener = listener_class.return_value.__enter__.return_value
            listener.listen_address = "0.0.0.0:4335"
            listener.accept.return_value = (accepted, ("192.0.2.11", 51235))
            settings = ConnectionSettings(
                transport="tls", host="127.0.0.1", listen_host="0.0.0.0", listen_port=4335,
                cert="client.crt", key="client.key", trusted_ca="ca.pem",
            )
            manager, metadata = open_call_home(settings)
            self.assertEqual(manager, "manager")
            self.assertEqual(metadata.transport, "TLS")
            session_class.return_value.connect_accepted.assert_called_once_with(
                accepted, settings, "192.0.2.11"
            )
            manager_for_session.assert_called_once()
        finally:
            accepted.close()
            peer_side.close()

    @patch("netconf_console.session.socket.create_connection")
    @patch.object(TracedTLSSession, "_connect_socket")
    def test_direct_tls_connect_uses_dual_stack_socket_factory(self, connect_socket, create_connection):
        raw_socket = object()
        create_connection.return_value = raw_socket
        settings = ConnectionSettings(
            transport="tls", host="2001:db8::10", port=6513,
            cert="client.crt", key="client.key", trusted_ca="ca.pem", timeout=7,
        )
        session = TracedTLSSession(ConsoleDeviceHandler())
        session.connect(settings)
        create_connection.assert_called_once_with(("2001:db8::10", 6513), timeout=7)
        connect_socket.assert_called_once_with(raw_socket, settings, "2001:db8::10")

    @patch("netconf_console.session.TracedTLSSession._post_connect")
    @patch("netconf_console.session.build_tls_context")
    def test_tls_accepted_socket_performs_client_handshake(self, build_context, post_connect):
        context = MagicMock()
        wrapped = MagicMock()
        context.wrap_socket.return_value = wrapped
        build_context.return_value = context
        raw_socket = MagicMock()
        settings = ConnectionSettings(
            transport="tls", cert="client.crt", key="client.key", trusted_ca="ca.pem", timeout=3,
            tls_server_name="oru.example",
        )
        session = TracedTLSSession(ConsoleDeviceHandler())
        session._connect_socket(raw_socket, settings, "192.0.2.12")
        context.wrap_socket.assert_called_once_with(
            raw_socket, server_hostname="oru.example", do_handshake_on_connect=False
        )
        wrapped.do_handshake.assert_called_once_with()
        post_connect.assert_called_once_with(timeout=3)
        self.assertIs(session._socket, wrapped)
        self.assertTrue(session.connected)

    @patch("netconf_console.session._manager_for_session", return_value="manager")
    @patch("netconf_console.session.TracedSSHSession")
    def test_direct_ssh_passes_authentication_and_verification_settings(
        self, session_class, manager_for_session
    ):
        settings = ConnectionSettings(
            transport="ssh", host="oru.example", port=830, username="oranuser",
            password="masked", key="id_ed25519", hostkey_verify=True,
            known_hosts="known_hosts", allow_agent=False, look_for_keys=False,
            keepalive=20, timeout=11, ssh_config="ssh_config",
        )
        manager, metadata = open_direct(settings)
        self.assertEqual(manager, "manager")
        self.assertEqual(metadata.remote_host, "oru.example")
        kwargs = session_class.return_value.connect.call_args.kwargs
        self.assertEqual(kwargs["username"], "oranuser")
        self.assertEqual(kwargs["key_filename"], "id_ed25519")
        self.assertTrue(kwargs["hostkey_verify"])
        self.assertFalse(kwargs["allow_agent"])
        self.assertFalse(kwargs["look_for_keys"])
        self.assertEqual(kwargs["keepalive"], 20)
        self.assertEqual(kwargs["ssh_config"], "ssh_config")
        manager_for_session.assert_called_once()

    def test_tls_version_and_context_controls(self):
        self.assertEqual(_tls_version("1.2").name, "TLSv1_2")
        with self.assertRaises(ValueError):
            _tls_version("1.1")
        # Context construction should reject incomplete mTLS settings before
        # attempting any network activity.
        with self.assertRaises(Exception):
            build_tls_context(ConnectionSettings(transport="tls", cert=None))

    def test_trace_redacts_credentials(self):
        self.assertNotIn("secret", redact_secrets("<password>secret</password>"))
        stream = io.StringIO()
        trace = TraceSink(enabled=True, stream=stream)
        trace.send("<rpc><password>secret</password></rpc>")
        trace.close()
        self.assertIn("SEND:", stream.getvalue())
        self.assertNotIn("secret", stream.getvalue())
        self.assertIn("REDACTED", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
