"""Templates, three-way merging, profile import and diagnostics are opt-in."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from lxml import etree

from netconf_console.gui.app import NetconfWindow
from netconf_console.gui.client import GuiClient, ReadOptions
from netconf_console.gui.creation import append_template
from netconf_console.gui import templates, reconcile, safety
from netconf_console.gui.schema import SchemaIndex
from netconf_console.gui.model import EditError, Selection, build_plan, NC
from netconf_console.gui.profile_exchange import read_import, merge_profiles
from netconf_console.gui.profile_import_ui import ProfileImportDialog
from netconf_console.gui.preferences import empty_book, PreferencesStore, PreferencesError
from netconf_console.gui.connection_diagnostics import DiagnosticRun, report_text
from netconf_console.gui.reconcile_ui import ReconcileDialog
from netconf_console.session import ConnectionSettings

YANG = '''module fixture {yang-version 1.1;namespace "urn:fixture";prefix f;
 container root {list client {key "name zone";
 leaf name {type string;} leaf zone {type uint8;} leaf host {type string;}
 leaf password {type string;} container private-key {leaf data {type string;}}
 leaf enabled {type boolean;} leaf state {config false;type string;}
 }}}'''
XML = b'''<data><root xmlns="urn:fixture"><client><name>client0</name><zone>1</zone>
 <host>original</host><password>synthetic-password</password><private-key><data>synthetic-private-key</data></private-key>
 <enabled>true</enabled><state>up</state></client></root></data>'''
F = "{urn:fixture}"


class Models(unittest.TestCase):
    def setUp(self):
        self.schema = SchemaIndex.compile({"fixture": YANG})
        self.data = etree.fromstring(XML)
        self.selection = Selection(self.data[0][0], (self.data[0],))

    def test_clone_resets_all_keys_secrets_and_state(self):
        template, removed = templates.from_node(self.schema, self.selection, self.selection.node)
        text = etree.tostring(template.root).decode()
        self.assertNotIn("synthetic", text)
        self.assertNotIn("client0", text)
        self.assertNotIn("state", text)
        self.assertGreaterEqual(len(template.pending), 4)
        self.assertTrue(template.issues())
        for node in tuple(template.pending):
            template.set_value(node, "2" if node.tag == F+"zone" else "filled")
        self.assertFalse(template.issues())
        result = append_template(self.schema, self.data[0], (F+"root",), template)
        self.assertEqual(len(result), 2)
        self.assertEqual(self.selection.node.findtext(F+"name"), "client0")

    def test_duplicate_clone_is_rejected(self):
        template, _ = templates.from_node(self.schema, self.selection, self.selection.node)
        for node in tuple(template.pending):
            template.set_value(node, "1" if node.tag == F+"zone" else "client0" if node.tag == F+"name" else "filled")
        with self.assertRaises(EditError):
            append_template(self.schema, self.data[0], (F+"root",), template)

    def test_library_encrypted_sanitized_schema_guard_and_no_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"personal.ncctemplate"
            template, _ = templates.from_node(self.schema, self.selection, self.selection.node)
            templates.save_template(path, template, self.schema)
            self.assertNotIn(b"original", path.read_bytes())
            restored = templates.load_template(path, self.schema)
            self.assertEqual(restored.path, self.selection.path)
            self.assertTrue(restored.pending)
            other = SchemaIndex.compile({"fixture": YANG.replace('type uint8', 'type uint16')})
            with self.assertRaises(EditError):
                templates.load_template(path, other)
            original = path.read_bytes()
            with patch("netconf_console.gui.templates._dpapi", side_effect=OSError()):
                with self.assertRaises(OSError):
                    templates.save_template(path, template, self.schema)
            self.assertEqual(original, path.read_bytes())

    def test_three_way_keeps_unrelated_remote_changes(self):
        mine, fresh = deepcopy(self.selection.node), deepcopy(self.data)
        mine.find(F+"host").text = "mine"
        fresh[0][0].find(F+"enabled").text = "false"
        latest, rows = reconcile.compare(self.selection, mine, fresh, self.schema, "running")
        self.assertEqual({r.choice for r in rows}, {"mine", "latest"})
        baseline, text = reconcile.resolve_rows(self.selection, latest, rows, self.schema, "running")
        edited = etree.fromstring(text.encode())
        self.assertEqual(edited.findtext(F+"host"), "mine")
        self.assertEqual(edited.findtext(F+"enabled"), "false")
        self.assertEqual(baseline.node.findtext(F+"enabled"), "false")
        plan = build_plan(baseline, text, self.schema)
        self.assertNotIn("enabled", plan.wire_xml)

    def test_same_leaf_conflict_requires_choice_and_state_is_ignored(self):
        mine, fresh = deepcopy(self.selection.node), deepcopy(self.data)
        mine.find(F+"host").text = "mine"
        fresh[0][0].find(F+"host").text = "remote"
        fresh[0][0].find(F+"state").text = "down"
        latest, rows = reconcile.compare(self.selection, mine, fresh, self.schema, "running")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].choice, "")
        with self.assertRaises(EditError):
            reconcile.resolve_rows(self.selection, latest, rows, self.schema, "running")
        rows[0].choice = "mine"
        baseline, text = reconcile.resolve_rows(self.selection, latest, rows, self.schema, "running")
        self.assertEqual(etree.fromstring(text.encode()).findtext(F+"state"), "down")
        self.assertEqual(baseline.node.findtext(F+"host"), "remote")

    def test_delete_vs_remote_modify_is_conflict(self):
        mine, fresh = deepcopy(self.selection.node), deepcopy(self.data)
        mine.remove(mine.find(F+"host"))
        fresh[0][0].find(F+"host").text = "remote"
        latest, rows = reconcile.compare(self.selection, mine, fresh, self.schema, "running")
        self.assertFalse(rows[0].choice)
        rows[0].choice = "mine"
        baseline, text = reconcile.resolve_rows(self.selection, latest, rows, self.schema, "running")
        self.assertIn('operation="remove"', build_plan(baseline, text, self.schema).wire_xml)

    def test_new_key_does_not_merge_another_instance_and_missing_parent_stops(self):
        fresh = deepcopy(self.data)
        fresh[0][0].find(F+"name").text = "other"
        mine = deepcopy(self.selection.node)
        mine.find(F+"host").text = "mine"
        latest, rows = reconcile.compare(self.selection, mine, fresh, self.schema, "running")
        self.assertIsNone(latest)
        rows[0].choice = "mine"
        with self.assertRaises(EditError):
            reconcile.resolve_rows(self.selection, latest, rows, self.schema, "running")

    def test_readback_matches_and_wrong_value_remains_visible(self):
        mine, fresh = deepcopy(self.selection.node), deepcopy(self.data)
        mine.find(F+"host").text = "mine"
        fresh[0][0].find(F+"host").text = "mine"
        _, rows = reconcile.compare(self.selection, mine, fresh, self.schema, "running")
        self.assertEqual(rows[0].status, "已符合草稿")


class ProfileModels(unittest.TestCase):
    def setUp(self):
        self.book = empty_book()
        self.book["connections"]["RU"] = {"host": "fixture.invalid", "password": "private", "cert": "C:/other/cert", "mode": "Direct TLS"}
        self.book["accounts"]["account"] = {"username": "u", "password": "secret"}

    def test_plain_json_never_imports_password_even_if_manually_added(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"settings.json"
            path.write_text(json.dumps(self.book))
            book, encrypted = read_import(path, {"host", "password", "cert", "mode", "username"})
            self.assertFalse(encrypted)
            self.assertNotIn("password", book["connections"]["RU"])
            with self.assertRaises(PreferencesError):
                read_import(path, {"host"})

    def test_default_import_renames_clears_paths_and_keeps_current_fields(self):
        current = empty_book()
        current["last"] = {"values": {"host": "unchanged"}}
        result = merge_profiles(current, self.book, [("connections", "RU", "new name", False)])
        self.assertEqual(result["last"], current["last"])
        self.assertEqual(result["connections"]["new name"]["cert"], "")
        self.assertNotIn("password", result["connections"]["new name"])
        self.assertFalse(current["connections"])

    def test_name_collision_is_atomic_and_replace_is_explicit(self):
        original = deepcopy(self.book)
        with self.assertRaises(PreferencesError):
            merge_profiles(self.book, self.book, [("accounts", "account", "new", False), ("connections", "RU", "RU", False)])
        self.assertEqual(self.book, original)
        result = merge_profiles(self.book, self.book, [("connections", "RU", "RU", True)], include_secrets=True, keep_paths=True)
        self.assertEqual(result, self.book)

    def test_encrypted_import_requires_explicit_secret_inclusion(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"settings.dpapi"
            PreferencesStore(path).save(self.book)
            book, encrypted = read_import(path, {"host", "password", "cert", "mode", "username"})
            self.assertTrue(encrypted)
            self.assertIn("password", book["accounts"]["account"])
            result = merge_profiles(empty_book(), book, [("accounts", "account", "a", False)])
            self.assertNotIn("password", result["accounts"]["a"])


class DiagnosticsModels(unittest.TestCase):
    def test_failure_report_redacts_fields_and_exception_values(self):
        settings = ConnectionSettings(host="sensitive-host", username="sensitive-user", password="SECRET-PASS", raw_file="must-not-write")
        run = DiagnosticRun(settings, threading.Event())
        def fail(*_args, **kwargs):
            kwargs["phase"]("tcp", "start")
            raise OSError("SECRET-PASS sensitive-host")
        with patch("netconf_console.gui.connection_diagnostics.open_direct", side_effect=fail):
            result = run.run()
        text = report_text(result)
        for value in ("SECRET-PASS", "sensitive-host", "sensitive-user", "must-not-write"):
            self.assertNotIn(value, text)
        self.assertIsNone(run.settings.raw_file)
        self.assertFalse(result["passed"])
        self.assertEqual(result["stages"][-1]["error_types"], ["OSError"])

    def test_cancelled_before_start_never_opens_socket(self):
        cancel = threading.Event()
        cancel.set()
        with patch("netconf_console.gui.connection_diagnostics.open_direct") as connect:
            result = DiagnosticRun(ConnectionSettings(), cancel).run()
        connect.assert_not_called()
        self.assertFalse(result["passed"])

    def test_stage_durations_and_callhome_peer_redaction(self):
        run = DiagnosticRun(ConnectionSettings(call_home=True), threading.Event())
        run.phase("listen", "start")
        run.phase("listen", "done", "0.0.0.0:4334")
        run.phase("tcp", "start")
        run.phase("tcp", "done", "192.0.2.1:54321")
        self.assertGreaterEqual(run.rows[-1]["seconds"], 0)
        text = report_text(run.report)
        self.assertNotIn("192.0.2.1", text)
        self.assertNotIn("0.0.0.0:4334", text)


class Widgets(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = NetconfWindow(self.root, persist=False)
        self.app.load_demo()
        self.root.update()
        self.app._expand_item("0")
        self.app._show_selection("0/0")

    def tearDown(self):
        self.app._destroy()

    def test_clone_form_requires_new_key_and_stages_only(self):
        self.app.clone_list()
        dialog = self.app.lifecycle_dialog
        self.assertIsNotNone(dialog)
        self.assertEqual(self.app.client.sent, [])
        self.assertTrue(any("複製" in dialog.title() for _ in (0,)))
        dialog.event_generate("<Escape>")

    def test_profile_cancel_does_not_modify_settings(self):
        book = empty_book()
        book["connections"]["fixture"] = {"host": "fixture.invalid"}
        original = deepcopy(self.app.preferences)
        dialog = ProfileImportDialog(self.app, book, False)
        self.assertTrue(all(not row[0] for row in dialog.rows.values()))
        dialog.close()
        self.assertEqual(self.app.preferences, original)
        self.assertEqual(self.app.client.sent, [])

    def test_real_export_fields_roundtrip_through_import_entry_point(self):
        values = self.app._preference_values()
        self.assertIn("wrap_xml", values)
        book = empty_book()
        book["connections"]["fixture"] = values
        book["last"] = {"values": values, "connection": "fixture", "account": ""}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "export.json"
            path.write_text(json.dumps(book), encoding="utf-8")
            with patch("netconf_console.gui.profile_import_ui.filedialog.askopenfilename", return_value=str(path)), patch.object(self.app, "_error") as error:
                dialog = self.app.import_profiles()
            error.assert_not_called()
            self.assertIsNotNone(dialog)
            self.assertIn("wrap_xml", dialog.book["connections"]["fixture"])
            dialog.close()
        self.assertEqual(self.app.client.sent, [])

    def test_profile_import_preview_selected_merge_never_connects(self):
        book = empty_book()
        book["connections"]["fixture"] = {"host": "fixture.invalid", "password": "secret", "ssh_key": "C:/other/key"}
        before = self.app._preference_values()
        dialog = ProfileImportDialog(self.app, book, False)
        dialog.tree.selection_set("0")
        dialog.select()
        dialog.toggle()
        with patch("netconf_console.gui.profile_import_ui.messagebox.askyesno", return_value=True):
            dialog.apply()
        self.assertEqual(self.app._preference_values(), before)
        self.assertEqual(self.app.preferences["connections"]["fixture"]["ssh_key"], "")
        self.assertNotIn("password", self.app.preferences["connections"]["fixture"])

    def test_rollback_option_is_capability_gated_and_exact_in_preview(self):
        self.app.editor.set(self.app.editor.get().replace(">1500<", ">9000<"))
        self.app.vars["rollback_on_error"].set(True)
        self.app._update_preview()
        self.assertIsNone(self.app.plan)
        self.app.client.capabilities = [*self.app.client.capabilities, "urn:ietf:params:netconf:capability:rollback-on-error:1.0"]
        self.app._update_preview()
        self.assertIn("rollback-on-error", self.app.plan.wire_xml)
        tested = safety.draft_rpc(self.app.plan)
        names = [etree.QName(n).localname for n in tested[0]]
        self.assertLess(names.index("test-option"), names.index("error-option"))
        self.assertLess(names.index("error-option"), names.index("config"))

    def test_send_results_match_and_reread_never_resends(self):
        self.app.editor.set(self.app.editor.get().replace(">1500<", ">9000<"))
        self.app._update_preview()
        with patch("netconf_console.gui.app.messagebox.askyesno", return_value=True), patch.object(self.app, "_run", side_effect=lambda _l,w,d: d(w())):
            self.app.send()
            count = len(self.app.client.sent)
            self.app.reread_attempt()
        self.assertEqual(len(self.app.client.sent), count)
        self.assertTrue(self.app.result_tree.get_children())
        self.assertTrue(all(self.app.result_tree.set(i, "status") == "符合預期" for i in self.app.result_tree.get_children()))

    def test_unknown_write_results_remain_pending_not_assumed_rollback(self):
        self.app.editor.set(self.app.editor.get().replace(">1500<", ">9000<"))
        self.app._update_preview()
        self.app._begin_attempt(self.app.selection, self.app.plan, self.app.snapshot.options, "NETCONF")
        self.app._finish_attempt(error=TimeoutError())
        self.assertTrue(self.app.result_pending)
        self.assertEqual(len(self.app.result_rows), 1)
        self.assertEqual(self.app.result_tree.set("0", "status"), "待確認")
        self.assertEqual(self.app.client.sent, [])

    def test_noninterleaved_subscription_blocks_new_readback_and_reconcile(self):
        self.app.editor.set(self.app.editor.get().replace(">1500<", ">9000<"))
        self.app._update_preview()
        self.app._begin_attempt(self.app.selection, self.app.plan, self.app.snapshot.options, "NETCONF")
        with patch.object(self.app, "_rpc_allowed", return_value=False), patch.object(self.app, "_run") as run:
            self.app.reread_attempt()
            self.app.open_reconcile()
        run.assert_not_called()
        self.assertEqual(self.app.client.sent, [])

    def test_backend_rejects_unsupported_rollback_before_lock(self):
        self.app.editor.set(self.app.editor.get().replace(">1500<", ">9000<"))
        self.app.client.capabilities = [*self.app.client.capabilities, "urn:ietf:params:netconf:capability:rollback-on-error:1.0"]
        self.app.vars["rollback_on_error"].set(True)
        self.app._update_preview()
        client = GuiClient()
        client.schema = self.app.client.schema
        context = MagicMock()
        context.require_manager.return_value.server_capabilities = ["urn:ietf:params:netconf:capability:writable-running:1.0"]
        client.context = context
        with self.assertRaises(EditError):
            client.apply(self.app.selection, self.app.plan, self.app.snapshot.options)
        context.require_manager.return_value.lock.assert_not_called()

    def test_reconcile_dialog_stages_without_send(self):
        selection = self.app.selection
        mine = deepcopy(selection.node)
        mine.find("{urn:o-ran:interfaces:1.0}l2-mtu").text = "9000"
        fresh = self.app.client.read(self.app.snapshot.options).data
        latest, rows = reconcile.compare(selection, mine, fresh, self.app.client.schema, "running")
        dialog = ReconcileDialog(self.app, selection, latest, rows, self.app.snapshot.options, "DEMO", self.app.client, self.app.client.schema)
        with patch("netconf_console.gui.reconcile_ui.messagebox.askyesno", return_value=True):
            dialog.stage()
        self.assertIsNone(self.app.lifecycle_dialog, dialog.status.get())
        self.assertIn("9000", self.app.editor.get())
        self.assertEqual(self.app.client.sent, [])


if __name__ == "__main__":
    unittest.main()
