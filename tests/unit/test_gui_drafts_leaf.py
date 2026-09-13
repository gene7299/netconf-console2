"""Draft isolation/persistence and schema-aware form/reference regressions."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from lxml import etree

from netconf_console.gui.app import NetconfWindow
from netconf_console.gui.client import ReadOptions, Snapshot
from netconf_console.gui.creation import Template, scalar_input, validate_scalar
from netconf_console.gui.demo import DemoClient
from netconf_console.gui.drafts import DraftShelf, schema_fingerprint
from netconf_console.gui.leaf_editor import LeafDialog
from netconf_console.gui.leafrefs import resolve
from netconf_console.gui.model import EditError, Selection, build_plan
from netconf_console.gui.schema import SchemaIndex
from netconf_console.gui.preferences import PreferencesStore

YANG = '''module refs {yang-version 1.1;namespace "urn:refs";prefix r;
 container root {
   list interface {key "name zone";
     leaf name {type string;} leaf zone {type uint8;} leaf alias {type string;}
   }
   list route {key id; leaf id {type string;} leaf if-name {type string;} leaf zone {type uint8;}
     leaf target {type leafref {path "/r:root/r:interface[r:name=current()/../if-name][r:zone=current()/../zone]/r:alias";}}
     leaf any-name {type leafref {path "../../interface/name";require-instance false;}}
     leaf empty-ref {type leafref {path "../../interface/name";}}
     leaf readonly {config false;type string;}
   }
 }
}'''
XML = b'''<data><root xmlns="urn:refs">
 <interface><name>eth0</name><zone>1</zone><alias>one</alias></interface>
 <interface><name>eth0</name><zone>2</zone><alias>two</alias></interface>
 <route><id>r1</id><if-name>eth0</if-name><zone>2</zone><target>two</target><any-name>eth0</any-name><empty-ref>eth0</empty-ref><readonly>state</readonly></route>
</root></data>'''


class DraftModelTests(unittest.TestCase):
    def setUp(self):
        self.client = DemoClient()
        self.schema = self.client.schema
        self.data = self.client.read(ReadOptions(defaults=True, state=True)).data
        self.selection = Selection(self.data[0][0], (self.data[0],))
        self.text = self.selection.text().replace(">1500<", ">9000<")
        self.token = object()
        self.shelf = DraftShelf()

    def capture(self, scope="device-a", source="running", selection=None, text=None):
        return self.shelf.capture(scope, source, selection or self.selection,
                                  text or self.text, self.schema, self.token, schema_fingerprint(self.schema))

    def test_device_source_instance_isolation_and_overlap_guard(self):
        self.capture()
        self.capture(scope="device-b")
        self.capture(source="candidate")
        second = Selection(self.data[0][1], (self.data[0],))
        self.capture(selection=second, text=second.text().replace("Fronthaul", "draft"))
        self.assertEqual(len(self.shelf.entries), 4)
        ancestor = Selection(self.data[0])
        self.assertIsNotNone(self.shelf.overlapping("device-a", "running", ancestor, self.schema))
        with self.assertRaises(EditError):
            self.capture(selection=ancestor)

    def test_dpapi_roundtrip_no_plaintext_partial_xml_and_no_trusted_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "drafts.nccdrafts"
            self.shelf.path = path
            entry = self.capture(text="<incomplete>synthetic-secret-草稿")
            self.shelf.save()
            self.assertNotIn(b"synthetic-secret", path.read_bytes())
            loaded = DraftShelf(path)
            loaded.load()
            self.assertFalse(loaded.load_error)
            restored = loaded.entries[entry.key]
            self.assertEqual(restored.text, entry.text)
            self.assertIsNone(restored.session)
            # Restore allows unfinished text to be recovered; build_plan still blocks it.
            loaded.verify(restored, "device-a", "running", self.schema, self.data, self.token)
            self.assertIs(restored.session, self.token)
            with self.assertRaises(etree.XMLSyntaxError):
                build_plan(restored.selection, restored.text, self.schema)

    def test_corrupt_or_encryption_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "drafts.nccdrafts"
            self.shelf.path = path
            self.capture()
            self.shelf.save()
            original = path.read_bytes()
            with patch("netconf_console.gui.drafts._dpapi", side_effect=OSError("fixture failure")):
                with self.assertRaises(OSError):
                    self.shelf.save()
            self.assertEqual(path.read_bytes(), original)
            with patch("netconf_console.gui.drafts._dpapi", return_value=b"invalid-json"):
                corrupted = DraftShelf(path)
                corrupted.load()
            self.assertTrue(corrupted.load_error)
            with self.assertRaises(EditError):
                corrupted.save()
            self.assertEqual(path.read_bytes(), original)

    def test_wrong_device_source_schema_and_changed_original_fail_closed(self):
        entry = self.capture()
        entry.session = None
        for scope, source in (("device-b", "running"), ("device-a", "candidate")):
            with self.assertRaises(EditError):
                self.shelf.verify(entry, scope, source, self.schema, self.data, self.token)
        other_schema = deepcopy(self.schema)
        other_schema.content_hash = "different-revision"
        with self.assertRaises(EditError):
            self.shelf.verify(entry, "device-a", "running", other_schema, self.data, self.token)
        changed = deepcopy(self.data)
        changed[0][0].find("{urn:o-ran:interfaces:1.0}l2-mtu").text = "8000"
        with self.assertRaises(EditError):
            self.shelf.verify(entry, "device-a", "running", self.schema, changed, self.token)
        self.assertIsNone(entry.session)
        self.assertIn("衝突", entry.status)

    def test_ancestors_store_keys_not_unrelated_sensitive_subtrees(self):
        node = self.data[0][0].find("{urn:o-ran:interfaces:1.0}l2-mtu")
        selection = Selection(node, (self.data[0], self.data[0][0]))
        entry = self.capture(selection=selection, text=selection.text().replace("1500", "9000"))
        self.assertEqual(len(entry.selection.ancestors[0]), 0)
        self.assertEqual([etree.QName(n).localname for n in entry.selection.ancestors[1]], ["name"])


class LeafrefTests(unittest.TestCase):
    def setUp(self):
        self.schema = SchemaIndex.compile({"refs": YANG})
        self.assertTrue(self.schema.complete, self.schema.warnings)
        self.data = etree.fromstring(XML)
        self.selection = Selection(self.data[0][2], (self.data[0],))

    def test_predicates_current_and_multiple_keys_filter_values(self):
        result = resolve(self.schema, self.data, self.selection, self.selection.node, (3,))
        self.assertEqual(result.values, ["two"])
        self.assertEqual(len(result.targets), 1)
        changed = deepcopy(self.selection.node)
        changed.find("{urn:refs}zone").text = "1"
        result = resolve(self.schema, self.data, self.selection, changed, (3,))
        self.assertEqual(result.values, ["one"])
        self.assertEqual(self.selection.node.findtext("{urn:refs}zone"), "2")

    def test_relative_require_instance_false_and_invisible_target_is_not_invalid(self):
        result = resolve(self.schema, self.data, self.selection, self.selection.node, (4,))
        self.assertEqual(result.values, ["eth0"])
        self.assertIn("require-instance=false", result.messages)
        hidden = deepcopy(self.data)
        for node in list(hidden[0])[:2]:
            hidden[0].remove(node)
        result = resolve(self.schema, hidden, self.selection, self.selection.node, (5,))
        self.assertFalse(result.values)
        self.assertTrue(any("不等於不存在" in m for m in result.messages))

    def test_imported_path_prefix_and_union_leafref(self):
        schema = SchemaIndex.compile({
            "a": 'module a {yang-version 1.1;namespace "urn:a";prefix aa;leaf-list names {type string;}typedef ref {type leafref {path "/aa:names";}}}',
            "b": 'module b {yang-version 1.1;namespace "urn:b";prefix bb;import a {prefix x;}leaf val {type union {type x:ref;type uint8;}}}',
        })
        self.assertTrue(schema.complete, schema.warnings)
        data = etree.fromstring(b'<data><names xmlns="urn:a">foo</names><val xmlns="urn:b">foo</val></data>')
        result = resolve(schema, data, Selection(data[1]), data[1])
        self.assertEqual(result.values, ["foo"])

    def test_identity_form_adds_binding_without_rebinding_existing_aliases(self):
        schema = SchemaIndex.compile({"a": 'module a {namespace "urn:a";prefix a;identity base;identity val {base base;}leaf ref {type identityref {base base;}}}'})
        info = schema.lookup(("{urn:a}ref",))
        node = etree.fromstring(b'<ref xmlns="urn:a" xmlns:a="urn:unrelated">base</ref>')
        result = scalar_input(schema, info, node, "a:val")
        self.assertEqual(result.nsmap["a"], "urn:unrelated")
        self.assertEqual(result.nsmap["a_yang"], "urn:a")
        self.assertEqual(result.text, "a_yang:val")
        self.assertEqual(node.text, "base")
        validate_scalar(schema, info, result)

    def test_leafref_integer_uses_xml_decimal_validation(self):
        schema = SchemaIndex.compile({"r": 'module r {namespace "urn:r";prefix r;leaf num {type uint8;}leaf ref {type leafref {path "/r:num";}}}'})
        info = schema.lookup(("{urn:r}ref",))
        node = etree.Element("{urn:r}ref")
        node.text = "0x10"
        with self.assertRaises(EditError):
            validate_scalar(schema, info, node)


class DraftWidgetTests(unittest.TestCase):
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

    def pump(self):
        deadline = time.monotonic() + 4
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.root.update()
        self.assertFalse(self.app.busy)

    def change(self):
        self.app.editor.set(self.app.editor.get().replace(">1500<", ">9000<"))
        self.app._update_preview()

    def test_switch_preserves_drafts_without_confirmation_or_send(self):
        self.change()
        with patch("netconf_console.gui.app.messagebox.askyesno", side_effect=AssertionError("must not discard")):
            self.app.tree.selection_set("0/1")
            self.app._selected()
            self.pump()
            self.app.editor.set(self.app.editor.get().replace("Fronthaul", "second draft"))
            self.app._update_preview()
            self.app.tree.selection_set("0/0")
            self.app._selected()
            self.pump()
        self.assertEqual(len(self.app.drafts.entries), 2)
        self.assertIn("9000", self.app.editor.get())
        self.assertEqual(self.app.client.sent, [])

    def test_full_shelf_blocks_unsaved_plan(self):
        with patch("netconf_console.gui.drafts.MAX_ENTRIES", 0):
            self.change()
        self.assertIsNone(self.app.plan)
        self.assertIn("草稿清單上限", self.app.preview_status.get())
        self.assertIn("disabled", self.app.send_button.state())

    def test_new_root_template_cannot_overwrite_existing_root_draft(self):
        from netconf_console.gui.creation import new_root_selection
        from netconf_console.gui.creation_ui import CreationDialog
        info = self.app.client.schema.lookup(("{urn:ietf:params:xml:ns:yang:ietf-hardware}demo-note",))
        template = Template(self.app.client.schema, info)
        template.set_value(template.root[0], "first draft")
        selection = new_root_selection(template.root, self.app.client.schema)
        text = etree.tostring(template.root).decode()
        self.app.drafts.capture("DEMO", "running", selection, text, self.app.client.schema,
                               self.app.client, schema_fingerprint(self.app.client.schema))
        before = self.app.editor.get()
        dialog = CreationDialog(self.app, self.app.snapshot.data, (), info)
        self.root.update()
        dialog.template.set_value(dialog.template.root[0], "second draft")
        dialog.rebuild(dialog.template.root)
        self.root.update()
        dialog.stage()
        self.assertIn("已有草稿", dialog.status.get())
        self.assertEqual(self.app.editor.get(), before)
        self.assertEqual(next(iter(self.app.drafts.entries.values())).text, text)
        dialog.close()

    def test_corrupt_shelf_does_not_trap_clean_window_or_overwrite(self):
        self.app.drafts.load_error = "fixture unreadable shelf"
        with patch.object(self.app.drafts, "save", side_effect=AssertionError("must not overwrite")):
            self.assertTrue(self.app._preserve_current_draft(flush=True))
        self.assertEqual(self.app.draft_status.get(), "fixture unreadable shelf")

    def test_restored_draft_send_and_test_only_guard_until_verified(self):
        self.change()
        entry = self.app._active_draft()
        entry.session = None
        self.app._sync()
        self.assertTrue(self.app.send_button.instate(["disabled"]))
        with patch("netconf_console.gui.app.messagebox.askyesno", return_value=True):
            self.app.send()
        self.assertFalse(self.app.client.sent)
        self.app.drafts.verify(entry, "DEMO", "running", self.app.client.schema, self.app.snapshot.data, self.app.client)
        self.app._sync()
        self.assertFalse(self.app.send_button.instate(["disabled"]))

    def test_only_sent_draft_removed_after_matching_readback(self):
        self.change()
        first = self.app._active_draft()
        self.app._show_selection("0/1")
        self.app.editor.set(self.app.editor.get().replace("Fronthaul", "second draft"))
        self.app._update_preview()
        with patch("netconf_console.gui.app.messagebox.askyesno", return_value=True):
            self.app.send()
            self.pump()
        self.assertEqual(list(self.app.drafts.entries), [first.key])

    def test_leaf_form_bool_range_readonly_and_local_only(self):
        dialog = LeafDialog(self.app, etree.fromstring(self.app.editor.get().encode()))
        self.root.update()
        mtu = next(i for i, (_, info, _, _) in dialog.fields.items() if info.path[-1].endswith("}l2-mtu"))
        dialog.tree.selection_set(mtu)
        dialog.select()
        dialog.value.set("70000")
        self.assertFalse(dialog.apply_value())
        dialog.value.set("9000")
        self.assertTrue(dialog.apply_value())
        key = next(i for i, (_, info, _, _) in dialog.fields.items() if info.path[-1].endswith("}name"))
        self.assertTrue(dialog.fields[key][3])
        state = next(i for i, (_, info, _, _) in dialog.fields.items() if info.path[-1].endswith("}oper-status"))
        self.assertTrue(dialog.fields[state][3])
        enabled = next(i for i, (_, info, _, _) in dialog.fields.items() if info.path[-1].endswith("}enabled"))
        dialog.tree.selection_set(enabled)
        dialog.select()
        self.assertIn("true", tuple(dialog.suggestion_box.cget("values")))
        dialog.suggestion_box.current(1)
        dialog.use_suggestion()
        self.assertEqual(dialog.value.get(), "false")
        self.assertTrue(dialog.apply_value())
        self.assertTrue(dialog.stage())
        self.assertIn("9000", self.app.preview.get())
        self.assertIn("false</enabled>", self.app.preview.get())
        self.assertFalse(self.app.client.sent)

    def test_new_schema_invalidates_same_session_draft(self):
        self.change()
        schema = deepcopy(self.app.client.schema)
        schema.content_hash = "new-schema"
        self.app.client.schema = schema
        self.app._sync()
        self.assertIn("schema", self.app._draft_guard())
        self.assertTrue(self.app.send_button.instate(["disabled"]))

    def test_readback_mismatch_keeps_draft_and_disables_resend(self):
        self.change()
        from netconf_console.gui.client import ApplyResult
        original = deepcopy(self.app.snapshot)
        with patch.object(self.app.client, "apply", return_value=ApplyResult("<ok/>", original)), patch(
                "netconf_console.gui.app.messagebox.askyesno", return_value=True):
            self.app.send()
            self.pump()
        self.assertTrue(self.app.uncertain)
        self.assertEqual(len(self.app.drafts.entries), 1)
        self.assertTrue(self.app.send_button.instate(["disabled"]))

    def test_partial_xml_can_switch_and_return_without_loss(self):
        self.app.editor.set("<incomplete>synthetic draft")
        self.app._update_preview()
        self.app.tree.selection_set("0/1")
        self.app._selected()
        self.pump()
        self.app.tree.selection_set("0/0")
        self.app._selected()
        self.pump()
        self.assertEqual(self.app.editor.get(), "<incomplete>synthetic draft")
        self.assertTrue(self.app.send_button.instate(["disabled"]))
