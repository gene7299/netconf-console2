"""Regression coverage for explicit lifecycle, imports, search and secret separation."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from lxml import etree

from netconf_console.gui import lifecycle
from netconf_console.gui.app import NetconfWindow
from netconf_console.gui.audit import AuditLog
from netconf_console.gui.client import GuiClient, ReadOptions, Snapshot
from netconf_console.gui.demo import DemoClient
from netconf_console.gui.model import EditError, NC, Selection, build_plan, semantic
from netconf_console.gui.preferences import empty_book, public_book, remember_account
from netconf_console.gui.schema import SchemaIndex
from netconf_console.gui.workspace import import_selection, instance_path, search_snapshot, value_changes
from netconf_console.sshauth import authenticate

IF = "urn:ietf:params:xml:ns:yang:ietf-interfaces"
ORAN = "urn:o-ran:interfaces:1.0"


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.manager = MagicMock()
        self.manager.server_capabilities = ["urn:ietf:params:netconf:capability:%s:1.0" % c
                                            for c in ("startup", "candidate", "validate", "writable-running")]
        self.client = GuiClient()
        self.client.context = MagicMock()
        self.client.context.require_manager.return_value = self.manager
        self.data = {name: etree.fromstring(('<data><x xmlns="urn:x">%s</x></data>' % value).encode())
                     for name, value in (("running", "run"), ("candidate", "candidate"), ("startup", "old"))}
        self.manager.get_config_ncc.side_effect = lambda source, **kw: SimpleNamespace(data=deepcopy(self.data[source]))
        def rpc(request):
            op = etree.QName(request[0]).localname
            if op == "copy-config":
                self.data["startup"] = deepcopy(self.data["running"])
            elif op == "commit":
                self.data["running"] = deepcopy(self.data["candidate"])
            elif op == "discard-changes":
                self.data["candidate"] = deepcopy(self.data["running"])
            return SimpleNamespace(xml='<rpc-reply xmlns="%s"><ok/></rpc-reply>' % NC)
        self.manager.xrpc.side_effect = rpc

    def test_save_commit_discard_validate_exact_rpc_and_locks(self):
        for operation, source, target, rpc_name in (("save", "running", "startup", "copy-config"),
                ("commit", "candidate", "running", "commit"), ("discard", "running", "candidate", "discard-changes"),
                ("validate", "running", "running", "validate")):
            with self.subTest(operation=operation):
                self.manager.reset_mock()
                prepared = lifecycle.prepare(self.client, operation)
                self.manager.xrpc.assert_not_called()
                self.manager.lock.assert_not_called()
                reply, warnings = lifecycle.execute(self.client, prepared)
                self.assertIn("ok", reply)
                self.assertEqual(warnings, [])
                self.assertEqual(etree.QName(self.manager.xrpc.call_args.args[0][0]).localname, rpc_name)
                locks = [call.kwargs["target"] for call in self.manager.lock.call_args_list]
                self.assertEqual([c.kwargs["target"] for c in self.manager.unlock.call_args_list], list(reversed(locks)))
                self.assertEqual(set(locks), {source, target})
                self.assertEqual(self.manager.xrpc.call_count, 1)

    def test_compare_never_writes(self):
        prepared = lifecycle.prepare(self.client, "compare")
        self.assertIn("startup", prepared.diff)
        with self.assertRaises(EditError):
            lifecycle.execute(self.client, prepared)
        self.manager.xrpc.assert_not_called()
        self.manager.lock.assert_not_called()

    def test_capability_and_replaced_session_rejected(self):
        prepared = lifecycle.prepare(self.client, "save")
        self.client.context.require_manager.return_value = MagicMock()
        with self.assertRaises(EditError):
            lifecycle.execute(self.client, prepared)
        self.manager.xrpc.assert_not_called()
        self.client.context.require_manager.return_value = self.manager
        self.manager.server_capabilities = []
        with self.assertRaises(EditError):
            lifecycle.prepare(self.client, "commit")
        self.manager.lock.assert_not_called()

    def test_conflict_blocks_write_and_unlocks(self):
        prepared = lifecycle.prepare(self.client, "save")
        self.data["startup"][0].text = "changed by colleague"
        with self.assertRaises(EditError):
            lifecycle.execute(self.client, prepared)
        self.manager.xrpc.assert_not_called()
        self.assertEqual(self.manager.unlock.call_count, 2)

    def test_rpc_and_partial_lock_failures_cleanup_without_retry(self):
        prepared = lifecycle.prepare(self.client, "commit")
        self.manager.lock.side_effect = [None, RuntimeError("lock denied")]
        with self.assertRaises(RuntimeError):
            lifecycle.execute(self.client, prepared)
        self.manager.unlock.assert_called_once_with(target="running")
        self.manager.xrpc.assert_not_called()
        self.manager.reset_mock()
        self.manager.lock.side_effect = None
        self.manager.xrpc.side_effect = TimeoutError("unknown outcome")
        with self.assertRaises(TimeoutError):
            lifecycle.execute(self.client, prepared)
        self.assertEqual(self.manager.unlock.call_count, 2)
        self.assertEqual(self.manager.xrpc.call_count, 1)

    def test_tampered_rpc_rejected_before_lock(self):
        prepared = lifecycle.prepare(self.client, "save")
        prepared.rpc[0].tag = "{%s}delete-config" % NC
        with self.assertRaises(EditError):
            lifecycle.execute(self.client, prepared)
        self.manager.lock.assert_not_called()

    def test_unlock_failure_closes_own_session(self):
        prepared = lifecycle.prepare(self.client, "save")
        self.manager.unlock.side_effect = RuntimeError("failure")
        with patch.object(self.client, "disconnect") as close:
            reply, warnings = lifecycle.execute(self.client, prepared)
            close.assert_called_once()
            self.assertTrue(warnings)
            self.assertIn("ok", reply)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.client = DemoClient()
        self.snapshot = self.client.read(ReadOptions(defaults=True, state=True))
        self.node = self.snapshot.data[0][0]
        self.selection = Selection(self.node, (self.snapshot.data[0],))
        self.schema = self.client.schema

    def test_diff_search_paths_contain_keys_and_collapsed_descendants(self):
        new = deepcopy(self.node)
        new.find("{%s}l2-mtu" % ORAN).text = "9000"
        changes = value_changes(self.selection, new, self.schema)
        self.assertEqual(len(changes), 1)
        self.assertIn("name='eth0'", changes[0].path)
        self.assertEqual((changes[0].before, changes[0].after), ("1500", "9000"))
        found = search_snapshot(self.snapshot.data, self.schema, "port-number")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1].node.text, "0")
        self.assertFalse(search_snapshot(self.snapshot.data, self.schema, ""))
        self.assertEqual(len(search_snapshot(self.snapshot.data, self.schema, "interface", limit=2)), 2)

    def test_data_export_import_selected_instance_state_ignored_and_readonly_preserved(self):
        data = deepcopy(self.snapshot.data)
        data.find(".//{%s}l2-mtu" % ORAN).text = "9000"
        data.find(".//{%s}oper-status" % IF).text = "down"
        imported = import_selection(etree.tostring(data), self.selection, self.schema)
        self.assertEqual(imported.find("{%s}oper-status" % IF).text, "up")
        plan = build_plan(self.selection, etree.tostring(imported).decode(), self.schema)
        self.assertEqual(len(plan.changes), 1)
        self.assertNotIn("oper-status", plan.wire_xml)
        same = import_selection(etree.tostring(self.snapshot.data), self.selection, self.schema)
        self.assertIsNone(build_plan(self.selection, etree.tostring(same).decode(), self.schema).rpc)

    def test_import_omission_means_explicit_delete_and_unknown_rejected(self):
        new = deepcopy(self.node)
        new.remove(new.find("{%s}description" % IF))
        imported = import_selection(etree.tostring(new), self.selection, self.schema)
        plan = build_plan(self.selection, etree.tostring(imported).decode(), self.schema)
        self.assertEqual(plan.removals, 1)
        etree.SubElement(new, "{urn:unknown}x")
        with self.assertRaises(EditError):
            import_selection(etree.tostring(new), self.selection, self.schema)

    def test_import_entities_wrong_instance_and_wrong_roots_rejected(self):
        for raw in (b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]><x>&e;</x>',
                    b'<rpc xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><delete-config/></rpc>',
                    etree.tostring(self.snapshot.data).replace(b">eth0<", b">eth99<")):
            with self.subTest(raw=raw[:40]), self.assertRaises(EditError):
                import_selection(raw, self.selection, self.schema)

    def test_schema_metadata_includes_typedef_constraints_units_enum(self):
        schema = SchemaIndex.compile({"a": '''module a {namespace "urn:a";prefix a;
            typedef number {type uint16 {range "1..100";} units "ms";}
            leaf interval {type number;description "timer";}
            leaf mode {type enumeration {enum one;enum two;} default one;}}'''})
        info = schema.lookup(("{urn:a}interval",))
        self.assertEqual(info.type_name, "number")
        self.assertEqual(info.units, "ms")
        self.assertIn("range: 1..100", info.constraints)
        self.assertIn("enum: two", schema.lookup(("{urn:a}mode",)).constraints)


class AuthenticationTests(unittest.TestCase):
    def test_login_password_never_used_as_private_key_secret(self):
        transport = MagicMock()
        transport.is_authenticated.return_value = True
        with patch("netconf_console.sshauth.paramiko.PKey.from_path") as key:
            authenticate(transport, "u", "login-secret", ["private"], False, False,
                         mode="private-key", passphrase="key-secret")
            self.assertEqual(key.call_args.args[1], b"key-secret")
            transport.auth_password.assert_not_called()

    def test_password_mode_never_loads_keys_or_agents(self):
        transport = MagicMock()
        transport.is_authenticated.return_value = True
        with patch("netconf_console.sshauth.paramiko.PKey.from_path") as key, patch("netconf_console.sshauth.paramiko.Agent") as agent:
            authenticate(transport, "u", "login", ["unused"], True, True, mode="password", passphrase="unused")
            key.assert_not_called()
            agent.assert_not_called()
            transport.auth_password.assert_called_once_with("u", "login")

    def test_passphrase_saved_with_account_but_excluded_from_public_export(self):
        book = empty_book()
        remember_account(book, {"username": "u", "password": "secret", "ssh_auth": "private-key", "key_passphrase": "keysecret"})
        self.assertIn("keysecret", str(book))
        self.assertNotIn("keysecret", str(public_book(book)))
        self.assertNotIn("'password'", str(public_book(book)))

    def test_audit_bounded_persist_reload_and_corruption_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operations.json"
            log = AuditLog(path=path)
            for index in range(505):
                log.add("fixture:830", "get-config", "ok")
            self.assertEqual(len(log.entries), 500)
            log.persist = True
            log.add("fixture:830", "commit", "ok")
            self.assertEqual(len(AuditLog(persist=True, path=path).entries), 500)
            path.write_text("corrupt", encoding="utf-8")
            broken = AuditLog(persist=True, path=path)
            broken.add("fixture", "read", "ok")
            self.assertEqual(path.read_text(), "corrupt")


class FeatureWidgetTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = NetconfWindow(self.root, persist=False)
        self.app.load_demo()

    def tearDown(self):
        self.app._destroy()

    def test_diff_tab_and_auth_profile_migration(self):
        self.app._expand_item("0")
        self.app._show_selection("0/0")
        self.app.editor.set(self.app.editor.get().replace(">1500<", ">9000<"))
        self.app._update_preview()
        self.assertIn("原值: 1500", self.app.diff_pane.get())
        self.assertIn("新值: 9000", self.app.diff_pane.get())
        self.app.vars["key_passphrase"].set("prior-account-secret")
        self.app._set_preference_values({"username": "old-profile"})
        self.assertEqual(self.app.vars["key_passphrase"].get(), "")
        self.assertEqual(self.app.vars["ssh_auth"].get(), "auto")

    def test_no_interleave_blocks_rpc_and_reconnect_does_not_resubscribe(self):
        manager = MagicMock()
        client = MagicMock()
        client.connected = True
        client.capabilities = ["urn:ietf:params:netconf:capability:notification:1.0"]
        client.manager = manager
        self.app.client = client
        self.app.demo = False
        self.app.notification_manager = manager
        self.app._sync()
        self.assertTrue(self.app.read_all_button.instate(["disabled"]))
        work = MagicMock()
        self.app._run("讀取…", work, lambda _: None)
        work.assert_not_called()
        manager.take_notification.return_value = SimpleNamespace(notification_xml="<notification><password>secret</password></notification>")
        self.app._poll_notifications()
        self.assertEqual(len(self.app.notifications), 50)
        self.assertNotIn(">secret<", self.app.notification_pane.get())
        client.manager = MagicMock()
        self.app._poll_notifications()
        self.assertIsNone(self.app.notification_manager)
        client.manager.create_subscription.assert_not_called()

    def test_import_only_stages_no_rpc(self):
        self.app._expand_item("0")
        self.app._show_selection("0/0")
        raw = self.app.editor.get().replace(">1500<", ">9000<")
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "import.xml"
            file.write_text(raw, encoding="utf-8")
            with patch("netconf_console.gui.features.filedialog.askopenfilename", return_value=str(file)), patch(
                    "netconf_console.gui.features.messagebox.askyesno", return_value=True):
                self.app.import_xml()
        self.assertEqual(self.app.client.sent, [])
        self.assertTrue(self.app.dirty)
        self.assertIn(">9000<", self.app.preview.get())

    def test_lifecycle_preview_cancel_never_writes_and_confirm_is_explicit(self):
        fixture = LifecycleTests()
        fixture.setUp()
        self.app.client = fixture.client
        self.app.demo = False
        self.app.selection = self.app.plan = None
        self.app.baseline_text = ""
        self.app.editor.set("")
        self.app.snapshot = Snapshot(deepcopy(fixture.data["running"]), ReadOptions())
        def run(_label, work, done):
            done(work())
        with patch.object(self.app, "_run", side_effect=run):
            self.app.datastore_action("save")
            window = self.app.lifecycle_dialog
            self.assertIsNotNone(window)
            fixture.manager.xrpc.assert_not_called()
            buttons = window.winfo_children()[-1].winfo_children()
            next(b for b in buttons if b.cget("text") == "取消").invoke()
            self.assertIsNone(self.app.lifecycle_dialog)
            fixture.manager.xrpc.assert_not_called()
            self.app.datastore_action("save")
            window = self.app.lifecycle_dialog
            buttons = window.winfo_children()[-1].winfo_children()
            # Prevent unrelated tree rendering with this synthetic schema-less fixture.
            with patch.object(self.app, "_accept_snapshot"):
                next(b for b in buttons if "確認送出" in b.cget("text")).invoke()
            self.assertEqual(fixture.manager.xrpc.call_count, 1)

    def test_password_mode_ignores_unused_key_and_captures_separate_secrets(self):
        self.app.vars["username"].set("fixture")
        self.app.vars["ssh_auth"].set("password")
        self.app.vars["ssh_key"].set("Z:/nonexistent-unused-key")
        self.app.vars["password"].set("login")
        self.app.vars["key_passphrase"].set("key-secret")
        settings = self.app.settings()
        self.assertIsNone(settings.key)
        self.assertEqual(settings.password, "login")
        self.assertEqual(settings.key_passphrase, "key-secret")

    def test_subscription_timeout_keeps_noninterleave_rpcs_blocked(self):
        client = MagicMock()
        client.connected = True
        client.capabilities = ["urn:ietf:params:netconf:capability:notification:1.0"]
        client.manager.take_notification.return_value = None
        self.app.client = client
        self.app.demo = False
        self.app.busy = True
        self.app.job_name = self.app.job_audit_operation = "訂閱事件…"
        self.app.job_device = "fixture:830"
        self.app.events.put(("error", TimeoutError()))
        with patch.object(self.app, "_error"):
            self.app._poll()
        self.assertIs(self.app.notification_manager, client.manager)
        self.assertFalse(self.app._rpc_allowed())
        self.assertTrue(self.app.subscribe_button.instate(["disabled"]))
        self.assertIn("結果待確認", self.app.notification_status.get())


if __name__ == "__main__":
    unittest.main()
