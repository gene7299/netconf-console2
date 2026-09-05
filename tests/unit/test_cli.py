from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from netconf_console.ncc import (
    SafeParser,
    argparser,
    expression_parser,
    parse_expr_args,
    resolve_namespace,
    settings_from_namespace,
)
from netconf_console.config import get_profile


class CliParserTests(unittest.TestCase):
    def test_new_transport_and_tls_options(self):
        namespace = resolve_namespace(argparser().parse_args([
            "--host", "192.0.2.10", "--port", "6513", "--transport", "tls",
            "--cert", "client.crt", "--key", "client.key", "--trusted-ca", "ca.pem",
            "--tls-version", "1.3", "--no-hostname-verify", "--trace",
            "--trace-file", "session.log", "--rpc", "request.xml",
        ]))
        self.assertEqual(namespace.transport, "tls")
        self.assertEqual(namespace.port, 6513)
        self.assertEqual(namespace.cert, "client.crt")
        self.assertFalse(namespace.hostkey_verify)
        self.assertEqual(namespace.trace_file, "session.log")
        self.assertEqual(len(namespace.operations), 1)

    def test_legacy_aliases_remain_parseable(self):
        namespace = resolve_namespace(argparser().parse_args([
            "--user", "admin", "--password", "admin", "--privKeyFile", "id_rsa",
            "--edit-config", "config.xml", "--db", "candidate", "--test-option", "set",
        ]))
        self.assertEqual(namespace.username, "admin")
        self.assertEqual(namespace.key, "id_rsa")
        self.assertEqual(namespace.operations[0][0].name, "edit_config")
        self.assertEqual(namespace.operations[0][1], ["config.xml"])
        self.assertEqual(namespace.db, "candidate")

    def test_rpc_content_and_interactive_help(self):
        namespace = argparser().parse_args(["--rpc", "--content", "request.xml"])
        self.assertEqual(namespace.content, "request.xml")
        self.assertEqual(namespace.operations[0][0].name, "rpc")
        parser = expression_parser()
        parsed = parser.parse_args(parse_expr_args("get-schema --model ietf-interfaces --version 1.1"))
        self.assertEqual(parsed.op.name, "get_schema")
        self.assertEqual(parsed.schema_model, "ietf-interfaces")
        self.assertEqual(parsed.schema_version, "1.1")

    def test_windows_paths_are_not_split_on_backslashes(self):
        self.assertEqual(parse_expr_args(r"rpc --content C:\temp\request.xml"), [
            "rpc", "--content", r"C:\temp\request.xml"
        ])

    def test_resolved_defaults(self):
        namespace = resolve_namespace(argparser().parse_args(["--transport", "tls"]))
        settings = settings_from_namespace(namespace)
        self.assertEqual(settings.port, 6513)
        self.assertEqual(settings.listen_port, 4335)
        self.assertEqual(settings.transport, "tls")

        ssh_namespace = resolve_namespace(argparser().parse_args(["--transport", "ssh"]))
        self.assertIsNone(ssh_namespace.username)
        self.assertIsNone(ssh_namespace.password)

    def test_new_aliases_and_confirm_timeout(self):
        namespace = resolve_namespace(argparser().parse_args([
            "--transport", "ssh", "--user", "tester", "--password", "pw",
            "--commit", "confirmed", "--confirm-timeout", "45",
            "--user-rpc", "request.xml", "--notifications", "--close-session",
        ]))
        self.assertEqual([item[0].name for item in namespace.operations], [
            "commit", "rpc", "watch_notifications", "close-session"
        ])
        self.assertEqual(namespace.timeout, 45.0)

    def test_interactive_connection_tls_policy_is_resolved(self):
        parser = expression_parser()
        parsed = parser.parse_args(parse_expr_args(
            "connect --tls --host 2001:db8::10 --port 6513 "
            "--cert client.crt --trusted-ca ca.pem --no-hostname-verify "
            "--netconf-version 1.1 --reply-timeout 9"
        ))
        base = settings_from_namespace(resolve_namespace(argparser().parse_args([])))
        settings = settings_from_namespace(parsed, base, command=True)
        self.assertEqual(settings.transport, "tls")
        self.assertEqual(settings.host, "2001:db8::10")
        self.assertFalse(settings.verify_hostname)
        self.assertEqual(settings.netconf_version, "1.1")
        self.assertEqual(settings.rpc_timeout, 9.0)

    def test_profiles_are_loaded_without_passwords(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "config.toml"
            filename.write_text(
                "[profiles.oru]\n"
                "host = '192.0.2.20'\n"
                "transport = 'tls'\n"
                "timeout = 12\n"
                "allow_agent = false\n"
                "password = 'must-not-load'\n",
                encoding="utf-8",
            )
            profile = get_profile("oru", filename)
            namespace = resolve_namespace(argparser().parse_args(["--profile", "oru", "--profile-file", str(filename)]))
        self.assertEqual(profile["host"], "192.0.2.20")
        self.assertNotIn("password", profile)
        self.assertEqual(namespace.timeout, 12)
        self.assertFalse(namespace.allow_agent)

    def test_contextual_help_does_not_exit_interactive_parser(self):
        parser = expression_parser(SafeParser, custom_help=True)
        for command in ("get", "edit-config", "listen", "connect", "subscribe", "namespaces"):
            parsed = parser.parse_args([command, "--help"])
            self.assertIsNotNone(parsed.cmd_parser)


if __name__ == "__main__":
    unittest.main()
