"""Safety boundaries for the second GUI feature batch; synthetic data only."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import sys
import time
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from lxml import etree
from ncclient.xml_ import to_xml

from netconf_console.gui import backups, events, lifecycle, safety, rpcerrors
from netconf_console.gui.app import NetconfWindow
from netconf_console.gui.client import GuiClient, ReadOptions
from netconf_console.gui.demo import DemoClient
from netconf_console.gui.model import EditError, NC, Selection, build_plan, semantic
from netconf_console.gui.preferences import empty_book, public_book
from netconf_console.session import ConnectionSettings, open_direct, open_call_home
from netconf_console.jump import open_jump

IF = "urn:ietf:params:xml:ns:yang:ietf-interfaces"
ORAN = "urn:o-ran:interfaces:1.0"
CAP = "urn:ietf:params:netconf:capability:"


class AdvancedServiceTests(unittest.TestCase):
    def setUp(self):
        self.demo = DemoClient()
        self.snapshot = self.demo.read(ReadOptions(defaults=True, state=True))
        self.node = self.snapshot.data[0][0]
        self.selection = Selection(self.node, (self.snapshot.data[0],))
        self.schema = self.demo.schema
        self.plan = build_plan(self.selection, self.selection.text().replace(">1500<", ">9000<"), self.schema)
        self.client = GuiClient()
        self.client.schema = self.schema
        self.client.context = MagicMock()
        self.client.context.settings.rpc_timeout = 2
        self.manager = self.client.manager
        self.manager.server_capabilities = [CAP + value for value in ("validate:1.1", "candidate:1.0", "confirmed-commit:1.1")]
        self.manager.xrpc.return_value = SimpleNamespace(xml="<ok/>")
        self.client.read = MagicMock(side_effect=lambda options, *args: self.demo.read(options))

    def test_draft_test_only_never_applies_and_unlocks(self):
        before = semantic(self.demo.data)
        rpc = safety.draft_rpc(self.plan)
        safety.test_draft(self.client, self.selection, self.plan, self.snapshot.options, rpc)
        actual = self.manager.xrpc.call_args.args[0]
        self.assertEqual(actual.findtext("{%s}edit-config/{%s}test-option" % (NC, NC)), "test-only")
        self.assertEqual(to_xml(actual), to_xml(rpc))
        self.assertEqual(semantic(self.demo.data), before)
        self.manager.unlock.assert_called_once_with(target="running")

    def test_draft_capability_and_tamper_fail_without_network(self):
        rpc = safety.draft_rpc(self.plan)
        self.manager.server_capabilities = [CAP + "validate:1.0"]
        with self.assertRaises(EditError):
            safety.test_draft(self.client, self.selection, self.plan, self.snapshot.options, rpc)
        self.manager.server_capabilities = [CAP + "validate:1.1"]
        rpc[0].find("{%s}test-option" % NC).text = "test-then-set"
        with self.assertRaises(EditError):
            safety.test_draft(self.client, self.selection, self.plan, self.snapshot.options, rpc)
        self.manager.lock.assert_not_called()
        self.manager.xrpc.assert_not_called()

    def test_draft_conflict_and_timeout_never_retry(self):
        rpc = safety.draft_rpc(self.plan)
        self.demo.data.find(".//{%s}description" % IF).text = "other operator"
        with self.assertRaises(EditError):
            safety.test_draft(self.client, self.selection, self.plan, self.snapshot.options, rpc)
        self.manager.xrpc.assert_not_called()
        self.client.read.side_effect = lambda *args: self.snapshot
        self.manager.xrpc.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            safety.test_draft(self.client, self.selection, self.plan, self.snapshot.options, rpc)
        self.assertEqual(self.manager.xrpc.call_count, 1)
        self.assertEqual(self.manager.unlock.call_count, 2)

    def start_confirmed(self):
        prepared = lifecycle.prepare(self.client, "confirmed", confirm_timeout=30)
        lifecycle.execute(self.client, prepared)
        return self.client.pending_commit

    def test_confirmed_commit_holds_locks_and_uses_unique_token(self):
        pending = self.start_confirmed()
        self.assertEqual(pending.state, "pending")
        self.assertEqual(set(pending.locks), {"running", "candidate"})
        self.manager.unlock.assert_not_called()
        rpc = safety.pending_rpc(pending, True)
        self.assertEqual(rpc[0].findtext("{%s}persist-id" % NC), pending.token)
        safety.finish_pending(self.client, pending, True, rpc)
        self.assertIsNone(self.client.pending_commit)
        self.assertEqual(self.manager.unlock.call_count, 2)

    def test_confirmed_timeout_blocks_confirm_but_allows_token_cancel(self):
        pending = self.start_confirmed()
        pending.deadline = time.monotonic() - 1
        self.manager.xrpc.reset_mock()
        with self.assertRaises(EditError):
            safety.finish_pending(self.client, pending, True, safety.pending_rpc(pending, True))
        self.manager.xrpc.assert_not_called()
        rpc = safety.pending_rpc(pending, False)
        self.assertEqual(rpc[0].tag, "{%s}cancel-commit" % NC)
        safety.finish_pending(self.client, pending, False, rpc)
        self.assertIsNone(self.client.pending_commit)

    def test_unknown_confirmed_result_retains_locks_and_blocks_other_writes(self):
        prepared = lifecycle.prepare(self.client, "confirmed", confirm_timeout=30)
        self.manager.xrpc.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            lifecycle.execute(self.client, prepared)
        self.assertEqual(self.client.pending_commit.state, "unknown")
        self.manager.unlock.assert_not_called()
        with self.assertRaises(EditError):
            lifecycle.prepare(self.client, "commit")
        with self.assertRaises(EditError):
            self.client.apply(self.selection, self.plan, self.snapshot.options)
        self.assertEqual(self.manager.xrpc.call_count, 1)

    def test_wrong_session_and_tampered_confirmation_rejected(self):
        pending = self.start_confirmed()
        rpc = safety.pending_rpc(pending, True)
        rpc[0][0].text = "other-token"
        self.manager.xrpc.reset_mock()
        with self.assertRaises(EditError):
            safety.finish_pending(self.client, pending, True, rpc)
        self.client.context.require_manager.return_value = MagicMock()
        with self.assertRaises(EditError):
            safety.finish_pending(self.client, pending, False, safety.pending_rpc(pending, False))
        self.manager.xrpc.assert_not_called()

    def test_confirmed_capability_and_duration_gate(self):
        self.manager.server_capabilities = [CAP + "candidate:1.0", CAP + "confirmed-commit:1.0"]
        with self.assertRaises(EditError):
            lifecycle.prepare(self.client, "confirmed")
        self.manager.server_capabilities.append(CAP + "confirmed-commit:1.1")
        for seconds in (0, 29, 601, True, "120"):
            with self.subTest(seconds=seconds), self.assertRaises(EditError):
                lifecycle.prepare(self.client, "confirmed", confirm_timeout=seconds)
        self.manager.xrpc.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
    def test_backup_encrypted_roundtrip_corruption_and_no_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "version.nccbackup"
            with self.assertRaises(EditError):
                backups.save_backup(path, self.snapshot, "fixture", self.schema)
            snapshot = self.demo.read(ReadOptions(defaults=True))
            backups.save_backup(path, snapshot, "fixture-private-name", self.schema)
            self.assertNotIn(b"fixture-private-name", path.read_bytes())
            self.assertNotIn(b"<interface", path.read_bytes())
            payload = backups.load_backup(path)
            self.assertEqual(payload["source"], "running")
            self.assertNotIn("oper-status", payload["xml"])
            raw = bytearray(path.read_bytes())
            raw[-1] ^= 0x80
            path.write_bytes(raw)
            with self.assertRaises(EditError):
                backups.load_backup(path)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_selective_restore_only_selected_leaf_and_preserves_state(self):
        modified = deepcopy(self.node)
        modified.find("{%s}l2-mtu" % ORAN).text = "9000"
        modified.find("{%s}description" % IF).text = "backup-description"
        modified.find("{%s}oper-status" % IF).text = "down"
        choices = backups.restore_choices(self.selection, etree.tostring(modified).decode(), self.schema, "running")
        self.assertEqual(len(choices), 2)
        index = next(i for i, item in enumerate(choices) if "l2-mtu" in item.path)
        restored = backups.apply_choices(self.selection, choices, [index], self.schema, "running")
        result = etree.fromstring(restored.encode())
        self.assertEqual(result.find("{%s}l2-mtu" % ORAN).text, "9000")
        self.assertNotIn("backup-description", restored)
        self.assertEqual(result.find("{%s}oper-status" % IF).text, "up")
        self.manager.xrpc.assert_not_called()

    def test_restore_explicit_removal_and_invalid_selection(self):
        modified = deepcopy(self.node)
        modified.remove(modified.find("{%s}description" % IF))
        choices = backups.restore_choices(self.selection, etree.tostring(modified).decode(), self.schema, "running")
        text = backups.apply_choices(self.selection, choices, [], self.schema, "running")
        self.assertIn("description", text)
        text = backups.apply_choices(self.selection, choices, [0], self.schema, "running")
        self.assertNotIn("description", text)
        with self.assertRaises(EditError):
            backups.apply_choices(self.selection, choices, [123], self.schema, "running")

    def test_rpc_error_namespaces_locate_augmented_leaf_and_deep_selection(self):
        error = etree.fromstring(('''<rpc-error xmlns="%s"><error-tag>invalid-value</error-tag>
            <error-app-tag>range-error</error-app-tag><error-path xmlns:if="%s" xmlns:o="%s">
            /if:interfaces/if:interface[if:name='eth0']/o:l2-mtu</error-path><error-message>bad mtu</error-message></rpc-error>'''
            % (NC, IF, ORAN)).encode())
        details, raw = rpcerrors.parse_errors(SimpleNamespace(xml=error))
        details[0].path = details[0].path.strip()
        self.assertIn("range-error", rpcerrors.describe_errors(details))
        self.assertIn("rpc-error", raw)
        spans = rpcerrors.locate_errors(self.selection.text(), self.selection, details)
        self.assertEqual(len(spans), 1)
        self.assertIn("l2-mtu", self.selection.text()[spans[0][0]:spans[0][1]])
        leaf = self.node.find("{%s}l2-mtu" % ORAN)
        selection = Selection(leaf, (*self.selection.ancestors, self.node))
        self.assertEqual(len(rpcerrors.locate_errors(selection.text(), selection, details)), 1)
        details[0].path = "/if:interfaces//*"
        self.assertEqual(rpcerrors.locate_errors(self.selection.text(), self.selection, details), [])


class EventTests(unittest.TestCase):
    def test_stream_discovery_and_replay_filter(self):
        manager = MagicMock()
        manager.get.return_value.data = etree.fromstring(('''<data><netconf xmlns="%s"><streams><stream>
            <name>NETCONF</name><description>All</description><replaySupport>true</replaySupport>
            <replayLogCreationTime>2026-01-01T00:00:00Z</replayLogCreationTime></stream></streams></netconf></data>'''
            % events.STREAM_NS).encode())
        streams = events.discover_streams(manager)
        options = events.subscription_options("NETCONF", streams, '<alarm xmlns="urn:fixture"/>',
            "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z", datetime(2026, 9, 9, tzinfo=timezone.utc))
        self.assertEqual(options["filter"][0], "subtree")
        self.assertEqual(options["stream_name"], "NETCONF")
        self.assertIn("start_time", options)

    def test_invalid_replay_dates_and_entities_fail_before_rpc(self):
        streams = {"NETCONF": events.StreamInfo("NETCONF", "", True, "")}
        for start, stop in (("", "2026-01-02T00:00:00Z"), ("bad", ""),
                            ("2099-01-01T00:00:00Z", ""), ("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z")):
            with self.subTest(start=start), self.assertRaises(EditError):
                events.subscription_options("NETCONF", streams, start=start, stop=stop)
        with self.assertRaises(EditError):
            events.subscription_options("unknown", streams, start="2026-01-01T00:00:00Z")
        with self.assertRaises(EditError):
            events.subscription_options("NETCONF", streams, '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]><x>&e;</x>')

    def test_event_alarm_and_completion_namespace(self):
        record = events.event_record('<notification xmlns="%s"><eventTime>2026-09-09T00:00:00Z</eventTime>'
            '<alarm xmlns="urn:fixture"><fault-severity>MAJOR</fault-severity><fault-source>port0</fault-source></alarm></notification>'
            % events.NOTIFICATION_NS)
        self.assertEqual((record.severity, record.source, record.event), ("major", "port0", "alarm"))
        self.assertFalse(record.completed)
        self.assertTrue(events.event_record('<notification xmlns="%s"><notificationComplete/></notification>' % events.NOTIFICATION_NS).completed)
        self.assertFalse(events.event_record('<notification xmlns="%s"><notificationComplete xmlns="urn:wrong"/></notification>' % events.NOTIFICATION_NS).completed)


class JumpTests(unittest.TestCase):
    def test_invalid_modes_rejected_before_socket(self):
        with patch("netconf_console.jump.socket.create_connection") as connect:
            for settings in (ConnectionSettings(transport="tls", jump_enabled=True), ConnectionSettings(call_home=True, jump_enabled=True)):
                with self.assertRaises(ValueError):
                    open_jump(settings)
            connect.assert_not_called()

    def test_jump_known_host_and_auth_cleanup(self):
        settings = ConnectionSettings(jump_enabled=True, jump_host="jump", jump_username="bastion", jump_password="secret")
        with patch("netconf_console.jump.socket.create_connection") as sock, patch("netconf_console.jump.paramiko.Transport") as transport, patch(
                "netconf_console.jump.paramiko.HostKeys") as keys, patch("netconf_console.jump.authenticate") as auth:
            keys.return_value.check.return_value = False
            with self.assertRaises(Exception):
                open_jump(settings)
            auth.assert_not_called()
            transport.return_value.close.assert_called_once()
            sock.return_value.close.assert_called_once()
            keys.return_value.check.return_value = True
            owner, channel = open_jump(settings)
            self.assertIs(owner, transport.return_value)
            self.assertEqual(auth.call_args.args[1:3], ("bastion", "secret"))
            self.assertEqual(owner.open_channel.call_args.args[:2], ("direct-tcpip", (settings.host, settings.port)))

    def test_public_profiles_redact_jump_secrets(self):
        book = empty_book()
        book["last"] = {"values": {"jump_password": "loginsecret", "jump_passphrase": "keysecret", "jump_host": "vmware"}}
        self.assertNotIn("secret", str(public_book(book)))


class AdvancedWidgetTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = NetconfWindow(self.root, persist=False)

    def tearDown(self):
        self.app._destroy()

    def test_disabled_reason_and_jump_profiles_defaults(self):
        self.assertIn("尚未連線", self.app.action_reason("draft"))
        self.assertTrue(self.app.disabled_reasons)
        self.app.vars["jump_password"].set("old-secret")
        self.app.vars["jump_enabled"].set(True)
        self.app._set_preference_values({"mode": "Direct SSH", "username": "old"})
        self.assertFalse(self.app.vars["jump_enabled"].get())
        self.assertEqual(self.app.vars["jump_password"].get(), "")
        self.app.vars["jump_password"].set("keep-jump")
        self.app._set_preference_values({"username": "another-target-account"})
        self.assertEqual(self.app.vars["jump_password"].get(), "keep-jump")

    def test_disconnected_subscription_sync_does_not_read_missing_manager(self):
        self.app.notification_manager = MagicMock()
        self.app._sync()
        self.assertFalse(self.app._rpc_allowed())
        self.app._poll_notifications()
        self.assertIsNone(self.app.notification_manager)

    def test_jump_settings_separate_target_and_reject_callhome(self):
        for name, value in {"username": "ru", "password": "ru-secret", "jump_enabled": True,
                "jump_host": "192.0.2.1", "jump_username": "vm", "jump_password": "vm-secret"}.items():
            self.app.vars[name].set(value)
        settings = self.app.settings()
        self.assertEqual((settings.username, settings.jump_username), ("ru", "vm"))
        self.assertEqual((settings.password, settings.jump_password), ("ru-secret", "vm-secret"))
        self.app.vars["mode"].set("SSH Call Home")
        with self.assertRaises(ValueError):
            self.app.settings()

    def test_restore_dialog_defaults_unchecked_and_only_stages_after_confirmation(self):
        self.app.load_demo()
        self.app._expand_item("0")
        self.app._show_selection("0/0")
        original = self.app.editor.get()
        payload = {"xml": original.replace(">1500<", ">9000<"), "device": "fixture", "created": "today", "source": "running"}
        self.app._restore_dialog(payload)
        window = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
        entries = next(w for w in window.winfo_children() if w.winfo_class() == "Treeview")
        self.assertTrue(entries.item("0", "text").startswith("☐"))
        controls = next(w for w in window.winfo_children() if w.winfo_class() == "TFrame"
                        and w.winfo_children() and all(b.winfo_class() == "TButton" for b in w.winfo_children()))
        stage = next(b for b in controls.winfo_children() if "載入選定" in b.cget("text"))
        stage.invoke()
        self.assertEqual(self.app.editor.get(), original)
        next(b for b in controls.winfo_children() if b.cget("text") == "全選").invoke()
        self.assertTrue(entries.item("0", "text").startswith("☑"))
        with patch("netconf_console.gui.advanced.messagebox.askyesno", return_value=True):
            stage.invoke()
        self.assertIn(">9000<", self.app.editor.get())
        self.assertEqual(self.app.client.sent, [])

    def test_error_highlight_preserves_raw_reply_and_clears_on_edit(self):
        self.app.load_demo()
        self.app._expand_item("0")
        self.app._show_selection("0/0")
        error = etree.fromstring(('<rpc-error xmlns="%s"><error-tag>invalid-value</error-tag>'
            '<error-path xmlns:if="%s" xmlns:o="%s">/if:interfaces/if:interface[if:name="eth0"]/o:l2-mtu</error-path>'
            '<error-message>fixture error</error-message></rpc-error>' % (NC, IF, ORAN)).encode())
        with patch("netconf_console.gui.app.messagebox.showerror") as popup:
            self.app._error(SimpleNamespace(xml=error))
            self.assertIn("error-tag", popup.call_args.args[1])
        self.assertTrue(self.app.editor.text.tag_ranges("rpc_error"))
        self.assertIn("fixture error", self.app.reply.get())
        self.app.editor.syntax()
        self.assertFalse(self.app.editor.text.tag_ranges("rpc_error"))

    def test_pending_disables_write_and_auto_reconnect_and_expires_safely(self):
        self.app.load_demo()
        self.app.demo = False
        self.app.client.capabilities = [CAP + "candidate:1.0", CAP + "confirmed-commit:1.1"]
        self.app.client.manager = MagicMock()
        pending = safety.PendingCommit(self.app.client.manager, "token", time.monotonic() + 30, time.monotonic() + 34, ())
        self.app.client.pending_commit = pending
        self.app.reconnect_enabled = True
        self.app._poll_confirmed()
        self.assertFalse(self.app.reconnect_enabled)
        self.assertTrue(self.app.send_button.instate(["disabled"]))
        pending.release_after = time.monotonic() - 1
        with patch.object(self.app, "_run") as run:
            self.app._poll_confirmed()
            self.assertEqual(run.call_args.args[0], "結束限時提交等待…")
        self.app.client.manager.xrpc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
