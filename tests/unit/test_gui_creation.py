"""Creation safety, effective schema metadata, native forms and RPC regressions."""
from copy import deepcopy
import tkinter as tk
from tkinter import ttk
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lxml import etree

from netconf_console.gui.app import NetconfWindow
from netconf_console.gui.client import GuiClient, Snapshot, ReadOptions, check_selection_current
from netconf_console.gui.creation import (Template, candidates, append_template, choice_candidates,
                                           missing_choice_candidates, new_root_selection, scalar_options,
                                           suggested_values)
from netconf_console.gui.creation_ui import CreationDialog, field_info_text
from netconf_console.gui.model import EditError, NC, Selection, build_plan
from netconf_console.gui.schema import SchemaIndex, ModuleSpec
from netconf_console.gui import safety, sysrepo
from netconf_console.gui.workspace import value_changes

YANG = '''module create-test {
 yang-version 1.1; namespace "urn:create-test"; prefix c;
 feature optional; identity transport; identity ssh {base transport;}
 container server {
   leaf enabled {type boolean; default true;}
   leaf readonly {config false; type string;}
   leaf feature-only {if-feature optional; type string;}
   container call-home {
     list client {key "name zone"; max-elements 3;
       leaf name {type string {length "1..12";}}
       leaf zone {type uint8;}
     container endpoint {
       leaf address {type string {length "1..50";} mandatory true;}
       leaf port {type uint16 {range "1..65535";} default 4334;}
       choice transport {mandatory true;
         case ssh {container ssh {leaf host-key {type string;}}}
         case tls {container tls {leaf certificate {type string;}}}
       }
     }
       choice auth {mandatory true;
         case password {leaf password {type string {length "3..30";} mandatory true;}}
         case key {leaf key {type string; mandatory true;}}
       }
       leaf kind {type identityref {base transport;} default ssh;}
       leaf-list label {type string; max-elements 2;}
       leaf ref {type leafref {path "../name";}}
       leaf flag {type empty;}
       leaf pattern {type string {pattern '[a-z]+';}}
       leaf enum {type enumeration {enum one; enum two;}}
       leaf conditional {when "../zone = 1"; mandatory true; type string;}
       leaf choice-holder {type string;}
     }
   }
   container optional-presence {presence "enabled"; leaf required {type string; mandatory true;}}
   container must-have {leaf required {type int16; mandatory true;}}
 }
 container absent {presence "enabled";}
 list top-list {key id; leaf id {type string;}}
 leaf-list top-values {type string;}
}'''


def path(*names):
    return tuple("{urn:create-test}" + n for n in names)


class CreationTests(unittest.TestCase):
    def setUp(self):
        self.schema = SchemaIndex.compile({"create-test": YANG}, [ModuleSpec("create-test", features=())])
        self.assertTrue(self.schema.complete, self.schema.warnings)

    def info(self, *names):
        return self.schema.lookup(path(*names))

    def client_template(self):
        template = Template(self.schema, self.info("server", "call-home", "client"))
        for names, value in ((('name',), 'client0'), (('zone',), '1'), (('endpoint', 'address'), '2000::c5')):
            node = template.root
            for name in names:
                node = node.find(path(name)[0])
            template.set_value(node, value)
        endpoint = template.root.find(path("endpoint")[0])
        template.add(endpoint, self.info("server", "call-home", "client", "endpoint", "ssh"))
        password = template.add(template.root, self.info("server", "call-home", "client", "password"))
        template.set_value(password, "test-only")
        return template

    def test_metadata_features_choices_and_minimal_required_seed(self):
        template = Template(self.schema, self.info("server"))
        self.assertIsNotNone(template.root.find(path("must-have")[0]))
        self.assertIsNone(template.root.find(path("optional-presence")[0]))
        self.assertIsNone(template.root.find(path("enabled")[0]))
        self.assertIsNone(self.info("server", "feature-only"))
        client = Template(self.schema, self.info("server", "call-home", "client"))
        self.assertEqual({n.tag for n in client.pending}, set(path("name", "zone", "address")))
        self.assertTrue(any(c.mandatory and c.parent == client.path for c in self.schema.choices.values()))
        self.assertTrue(self.info("server", "optional-presence").presence)

    def test_field_info_uses_semantic_lines_without_empty_type_separator(self):
        info = SimpleNamespace(module="ietf-netconf-server", kind="container", type_name="",
                               presence="Indicates that server-initiated call home connections\n"
                                       "have been configured.", constraints="", description="Configures NETCONF.")
        rendered = field_info_text(info)
        self.assertEqual(rendered.splitlines()[0], "ietf-netconf-server · container")
        self.assertIn("presence：Indicates that server-initiated call home connections have been configured.", rendered)
        self.assertIn("description：Configures NETCONF.", rendered)
        self.assertNotIn(" · · ", rendered)

    def test_candidate_reasons_include_readonly_existing_and_choice(self):
        parent = etree.fromstring(b'<server xmlns="urn:create-test"><enabled>true</enabled></server>')
        catalog = {c.info.path[-1]: c for c in candidates(self.schema, parent, path("server"))}
        self.assertIn("config false", catalog[path("readonly")[0]].reason)
        self.assertIn("已存在", catalog[path("enabled")[0]].reason)
        self.assertTrue(catalog[path("call-home")[0]].allowed)
        template = self.client_template()
        self.assertEqual(template.issues(), [])
        key = next(c for c in candidates(self.schema, template.root, template.path) if c.info.path[-1] == path("key")[0])
        self.assertFalse(key.allowed)

    def test_type_validation_default_identity_and_empty(self):
        template = self.client_template()
        zone = template.root.find(path("zone")[0])
        for invalid in ("256", "-1", "0x10", "abc"):
            with self.subTest(value=invalid), self.assertRaises(EditError):
                template.set_value(zone, invalid)
        for field, valid, invalid in (("pattern", "abc", "123"), ("enum", "one", "three"), ("flag", "", "true")):
            node = template.add(template.root, self.info("server", "call-home", "client", field))
            template.set_value(node, valid)
            with self.assertRaises(EditError):
                template.set_value(node, invalid)
        kind = template.add(template.root, self.info("server", "call-home", "client", "kind"))
        self.assertEqual(kind.text, "create-test:ssh")
        self.assertIn(kind.text, scalar_options(self.schema, self.info("server", "call-home", "client", "kind")))
        self.assertEqual(template.issues(), [])

    def test_suggested_values_are_helpful_but_not_a_closed_value_set(self):
        enabled = self.info("server", "enabled")
        self.assertEqual(suggested_values(self.schema, enabled), ("true", "false"))

        port = self.info("server", "call-home", "client", "endpoint", "port")
        self.assertEqual(suggested_values(self.schema, port), ("4334",))

        ref = self.info("server", "call-home", "client", "ref")
        self.assertEqual(suggested_values(self.schema, ref, ("client0", "client1")),
                         ("client0", "client1"))

    def test_list_duplicates_and_leaf_list_maximum(self):
        template = self.client_template()
        parent = etree.Element(path("call-home")[0])
        added = append_template(self.schema, parent, path("server", "call-home"), template)
        self.assertEqual(len(parent), 0)
        with self.assertRaises(EditError):
            append_template(self.schema, added, path("server", "call-home"), template)
        label_info = self.info("server", "call-home", "client", "label")
        for value in ("one", "two"):
            template.set_value(template.add(template.root, label_info), value)
        with self.assertRaises(EditError):
            template.add(template.root, label_info)

    def test_required_choice_blocks_until_branch_has_value(self):
        template = self.client_template()
        template.root.remove(template.root.find(path("password")[0]))
        self.assertTrue(any("choice" in issue for issue in template.issues()))
        key = template.add(template.root, self.info("server", "call-home", "client", "key"))
        self.assertTrue(template.issues())
        template.set_value(key, "synthetic-key")
        self.assertFalse(template.issues())

    def test_missing_choice_exposes_concrete_branch_and_can_add_it(self):
        template = Template(self.schema, self.info("server", "call-home", "client"))
        for names, value in ((('name',), 'client0'), (('zone',), '1'), (('endpoint', 'address'), '2000::c5')):
            node = template.root
            for name in names:
                node = node.find(path(name)[0])
            template.set_value(node, value)
        password = template.add(template.root, self.info("server", "call-home", "client", "password"))
        template.set_value(password, "test-only")
        endpoint = template.root.find(path("endpoint")[0])
        options = choice_candidates(self.schema, endpoint,
                                    path("server", "call-home", "client", "endpoint"))
        self.assertEqual({(option.choice.name, option.branch) for option in options},
                         {("transport", "ssh"), ("transport", "tls")})
        self.assertEqual({option.info.path[-1] for option in options},
                         {path("server", "call-home", "client", "endpoint", "ssh")[-1],
                          path("server", "call-home", "client", "endpoint", "tls")[-1]})
        ssh = next(option for option in options if option.branch == "ssh")
        added = template.add(ssh.parent, ssh.info)
        self.assertEqual(added.tag, path("ssh")[-1])
        remaining = missing_choice_candidates(self.schema, template.root, template.path)
        self.assertNotIn("transport", {option.choice.name for option in remaining})
        self.assertNotIn("choice", " ".join(template.issues()))

    def test_presence_min_elements_and_deviation(self):
        source = '''module extra {yang-version 1.1; namespace "urn:extra"; prefix e;
          container root {presence "on";
            leaf-list required {type uint8; min-elements 2; max-elements 3;}
            leaf removed {type string;}
          }
          deviation "/e:root/e:removed" {deviate not-supported;}
        }'''
        schema = SchemaIndex.compile({"extra": source})
        self.assertTrue(schema.complete, schema.warnings)
        template = Template(schema, schema.lookup(("{urn:extra}root",)))
        self.assertEqual(len(template.root), 2)
        self.assertIsNone(schema.lookup(("{urn:extra}root", "{urn:extra}removed")))
        template.set_value(template.root[0], "1")
        template.set_value(template.root[1], "2")
        self.assertFalse(template.issues())
        template.root.remove(template.root[0])
        self.assertTrue(any("min-elements" in issue for issue in template.issues()))

    def test_imported_identity_default_uses_defining_namespace(self):
        schema = SchemaIndex.compile({
            "a": 'module a {namespace "urn:a";prefix a;identity base; identity val {base base;} typedef kind {type identityref {base base;} default val;}}',
            "b": 'module b {namespace "urn:b";prefix b;import a {prefix other;} leaf value {type other:kind;}}',
        })
        self.assertTrue(schema.complete, schema.warnings)
        template = Template(schema, schema.lookup(("{urn:b}value",)))
        self.assertEqual(template.root.text, "a:val")
        self.assertFalse(template.issues())

    def test_existing_draft_preserved_and_minimal_create_rpc(self):
        before = etree.fromstring(b'<call-home xmlns="urn:create-test"/>')
        template = self.client_template()
        edited = append_template(self.schema, before, path("server", "call-home"), template)
        selection = Selection(before, (etree.Element(path("server")[0]),))
        plan = build_plan(selection, etree.tostring(edited).decode(), self.schema)
        self.assertEqual(len(plan.changes), 1)
        self.assertIn('operation="create"', plan.wire_xml)
        self.assertNotIn("readonly", plan.wire_xml)
        self.assertNotIn("<nc:commit", plan.wire_xml)

    def test_absent_root_empty_presence_is_real_creation_and_conflict_guard(self):
        template = Template(self.schema, self.info("absent"))
        selection = new_root_selection(template.root, self.schema)
        plan = build_plan(selection, etree.tostring(template.root).decode(), self.schema)
        self.assertFalse(selection.exists)
        self.assertIsNotNone(plan.rpc)
        self.assertEqual(value_changes(selection, plan.edited, self.schema)[0].before, "（不存在）")
        data = etree.Element("data")
        check_selection_current(data, selection, self.schema)
        data.append(deepcopy(template.root))
        with self.assertRaises(EditError):
            check_selection_current(data, selection, self.schema)

    def test_root_list_identity_conflicts_not_unrelated_instances(self):
        for name in ("top-list", "top-values"):
            template = Template(self.schema, self.info(name))
            node = template.root[0] if name == "top-list" else template.root
            template.set_value(node, "new")
            selection = new_root_selection(template.root, self.schema)
            build_plan(selection, etree.tostring(template.root).decode(), self.schema)
            data = etree.Element("data")
            other = deepcopy(template.root)
            (other[0] if name == "top-list" else other).text = "old"
            data.append(other)
            check_selection_current(data, selection, self.schema)
            data.append(template.root)
            with self.assertRaises(EditError):
                check_selection_current(data, selection, self.schema)

    def test_netconf_and_test_only_support_absent_roots(self):
        template = Template(self.schema, self.info("absent"))
        selection = new_root_selection(template.root, self.schema)
        plan = build_plan(selection, etree.tostring(template.root).decode(), self.schema)
        client = GuiClient()
        client.schema = self.schema
        client.context = MagicMock()
        manager = client.context.require_manager.return_value
        manager.server_capabilities = ["urn:ietf:params:netconf:capability:" + c for c in ("writable-running:1.0", "validate:1.1")]
        manager.xrpc.return_value.xml = "<ok/>"
        client.read = MagicMock(return_value=Snapshot(etree.Element("data"), ReadOptions()))
        client.apply(selection, plan, ReadOptions())
        self.assertEqual(manager.xrpc.call_count, 1)
        safety.test_draft(client, selection, plan, ReadOptions(), safety.draft_rpc(plan))
        self.assertIn("test-only", etree.tostring(manager.xrpc.call_args.args[0]).decode())
        client.read.return_value.data.append(template.root)
        with self.assertRaises(EditError):
            client.apply(selection, plan, ReadOptions())
        self.assertEqual(manager.xrpc.call_count, 2)
        self.assertEqual(manager.unlock.call_count, 3)

    def test_sysrepo_create_stdin_and_readback(self):
        template = Template(self.schema, self.info("absent"))
        selection = new_root_selection(template.root, self.schema)
        plan = build_plan(selection, etree.tostring(template.root).decode(), self.schema)
        prepared = sysrepo.prepare(plan, self.schema)
        self.assertIn(b'operation="create"', prepared.payload)
        self.assertNotIn(b"<nc:rpc", prepared.payload)
        shell = MagicMock()
        shell.run.side_effect = [(b"", b""), (b"", b""), (etree.tostring(template.root), b"")]
        _, warnings = sysrepo.execute(shell, prepared, selection, plan, self.schema)
        self.assertEqual(warnings, [])
        self.assertEqual(shell.run.call_count, 3)


class CreationWidgetTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = NetconfWindow(self.root, persist=False)
        self.app.load_demo()
        self.root.update()
        self.app._expand_item("0")
        self.app._show_selection("0/1")

    def tearDown(self):
        self.app._destroy()

    def test_form_stages_optional_leaf_without_send_preserving_edit(self):
        self.app.editor.set(self.app.editor.get().replace("Fronthaul", "Existing draft"))
        self.app._update_preview()
        path = self.app.selection.path
        info = self.app.client.schema.lookup(path + ("{urn:o-ran:interfaces:1.0}l2-mtu",))
        dialog = CreationDialog(self.app, etree.fromstring(self.app.editor.get().encode()), path, info)
        self.root.update()
        self.assertIsNotNone(dialog.template)
        dialog.value.set("9000")
        dialog.set_value()
        self.root.update()
        dialog.stage()
        self.assertIsNone(self.app.lifecycle_dialog)
        self.assertIn("Existing draft", self.app.editor.get())
        self.assertIn("9000", self.app.plan.wire_xml)
        self.assertIn('operation="create"', self.app.plan.wire_xml)
        self.assertEqual(self.app.client.sent, [])

    def test_ghost_toggle_keeps_draft_and_real_snapshot(self):
        original = etree.tostring(self.app.snapshot.data)
        self.app.editor.set(self.app.editor.get().replace("Fronthaul", "draft"))
        edited = self.app.editor.get()
        self.app.vars["show_candidates"].set(True)
        self.app._refresh_creation_candidates()
        self.app._expand_item("0/1")
        self.assertTrue(self.app.creation_candidates)
        self.app.vars["show_candidates"].set(False)
        self.app._refresh_creation_candidates()
        self.assertFalse(self.app.creation_candidates)
        self.assertEqual(self.app.editor.get(), edited)
        self.assertEqual(etree.tostring(self.app.snapshot.data), original)

    def test_root_creation_candidates_align_with_root_rows(self):
        self.app.vars["show_candidates"].set(True)
        self.app._refresh_creation_candidates()
        self.root.update_idletasks()
        root_candidates = [iid for iid in self.app.creation_candidates if self.app.tree.parent(iid) == ""]
        self.assertTrue(root_candidates)
        for iid in root_candidates:
            self.assertTrue(self.app.tree.get_children(iid))
            self.assertTrue(self.app.tree.get_children(iid)[0].endswith("/indicator"))
            self.assertFalse(self.app.tree.item(iid, "text").startswith("＋ "))
        for iid in self.app.creation_candidates:
            self.assertTrue(self.app.tree.get_children(iid))
            self.assertFalse(self.app.tree.item(iid, "text").startswith("＋ "))

    def test_empty_snapshot_root_creation_and_revert(self):
        schema = SchemaIndex.compile({"create-test": YANG})
        self.app.client.schema = schema
        snapshot = Snapshot(etree.Element("data"), ReadOptions())
        self.app._accept_snapshot(snapshot)
        info = schema.lookup(path("absent"))
        dialog = CreationDialog(self.app, snapshot.data, (), info)
        self.root.update()
        dialog.stage()
        self.assertFalse(self.app.selection.exists)
        self.assertTrue(self.app.dirty)
        self.assertIsNotNone(self.app.plan.rpc)
        self.assertEqual(len(snapshot.data), 0)
        with patch("netconf_console.gui.app.messagebox.askyesno", return_value=True):
            self.app.revert()
        self.assertIsNone(self.app.selection)
        self.assertFalse(self.app.dirty)

    def test_cancel_form_does_not_touch_editor_or_snapshot(self):
        before = self.app.editor.get()
        dialog = CreationDialog(self.app, self.app.selection.node, self.app.selection.path)
        self.root.update()
        dialog.close()
        self.assertEqual(self.app.editor.get(), before)
        self.assertEqual(self.app.client.sent, [])

    def test_delete_presence_container_stages_one_remove_from_parent(self):
        schema = SchemaIndex.compile({"create-test": YANG}, [ModuleSpec("create-test", features=())])
        self.app.client.schema = schema
        data = etree.Element("data")
        server = etree.SubElement(data, path("server")[0])
        call_home = etree.SubElement(server, path("call-home")[0])
        client = etree.SubElement(call_home, path("client")[0])
        etree.SubElement(client, path("name")[0]).text = "client0"
        etree.SubElement(client, path("zone")[0]).text = "1"
        self.app._accept_snapshot(Snapshot(data, ReadOptions(source="running")))
        self.app._expand_item("0")
        call_home_iid = next(
            iid for iid, item in self.app.items.items()
            if item.node.tag.endswith("}call-home"))
        self.app._show_selection(call_home_iid)
        self.app._sync()
        self.assertFalse(self.app.delete_button.instate(["disabled"]))
        with patch("netconf_console.gui.app.messagebox.askyesno", return_value=True):
            self.app.delete_selected()
        self.assertTrue(self.app.selection.node.tag.endswith("}server"))
        self.assertNotIn("call-home", self.app.editor.get())
        self.assertIn('operation="remove"', self.app.preview.get())
        self.assertEqual(self.app.plan.removals, 1)
        self.assertIn("REMOVE", self.app.plan.changes[0])

    def test_delete_parent_also_clears_descendant_draft(self):
        schema = SchemaIndex.compile({"create-test": YANG}, [ModuleSpec("create-test", features=())])
        self.app.client.schema = schema
        data = etree.Element("data")
        server = etree.SubElement(data, path("server")[0])
        call_home = etree.SubElement(server, path("call-home")[0])
        client = etree.SubElement(call_home, path("client")[0])
        etree.SubElement(client, path("name")[0]).text = "client0"
        etree.SubElement(client, path("zone")[0]).text = "1"
        self.app._accept_snapshot(Snapshot(data, ReadOptions(source="running")))
        self.app._expand_item("0")
        call_home_iid = next(iid for iid, item in self.app.items.items()
                             if item.node.tag.endswith("}call-home"))
        self.app._expand_item(call_home_iid)
        client_iid = next(iid for iid, item in self.app.items.items()
                          if iid.startswith(call_home_iid + "/") and item.node.tag.endswith("}client"))
        self.app._show_selection(client_iid)
        self.app.editor.set(self.app.editor.get().replace(">1<", ">2<"))
        self.app._update_preview()
        self.assertEqual(len(self.app.drafts.entries), 1)
        self.app._show_selection(call_home_iid)
        self.app.uncertain = True
        self.app._sync()
        self.assertFalse(self.app.delete_button.instate(["disabled"]))
        with patch("netconf_console.gui.app.messagebox.askyesno", return_value=True):
            self.app.delete_selected()
        self.assertEqual(len(self.app.drafts.entries), 1)
        self.assertTrue(self.app.selection.node.tag.endswith("}server"))
        self.assertIn('operation="remove"', self.app.preview.get())
        self.assertNotIn("client0", " ".join(entry.label for entry in self.app.drafts.entries.values()))

    def test_choice_branch_is_visible_even_when_leaf_is_selected(self):
        schema = SchemaIndex.compile({
            "create-test": YANG,
            "unused": 'module unused { namespace "urn:unused"; prefix u; leaf orphan { type string; } }',
        })
        self.app.client.schema = schema
        server = etree.Element(path("server")[0])
        call_home = etree.SubElement(server, path("call-home")[0])
        self.app.snapshot = Snapshot(etree.Element("data"), ReadOptions(source="running"))
        self.app.selection = Selection(call_home, (server,))
        self.app.selection_iid = None
        self.app.editor.set(etree.tostring(call_home, encoding="unicode"))
        dialog = CreationDialog(self.app, call_home, path("server", "call-home"),
                                schema.lookup(path("server", "call-home", "client")))
        self.root.update()
        self.assertIsNotNone(dialog.template)
        self.assertNotIn("urn:unused", dialog.preview.get())
        values = tuple(dialog.choice_box.cget("values"))
        self.assertTrue(any("transport → ssh" in value for value in values), values)
        self.assertTrue(any("transport → tls" in value for value in values), values)
        name_iid = next(iid for iid, node in dialog.nodes.items() if node.tag == path("name")[-1])
        dialog.tree.selection_set(name_iid)
        dialog.tree.event_generate("<<TreeviewSelect>>")
        self.root.update()
        self.assertTrue(any("transport → ssh" in value for value in dialog.choice_box.cget("values")))
        dialog.choice_box.current(next(i for i, value in enumerate(dialog.choice_box.cget("values"))
                                        if "transport → ssh" in value))
        dialog.add_choice()
        endpoint = dialog.template.root.find(path("endpoint")[0])
        self.assertIsNotNone(endpoint.find(path("ssh")[0]))
        self.assertNotIn("transport", " ".join(dialog.template.issues()))
        dialog.close()

    def test_creation_field_is_free_text_and_can_apply_a_suggestion(self):
        schema = SchemaIndex.compile({"create-test": YANG})
        self.app.client.schema = schema
        self.app.snapshot = Snapshot(etree.Element("data"), ReadOptions(source="running"))
        parent = etree.Element(path("server")[0])
        self.app.selection = Selection(parent, ())
        self.app.selection_iid = None
        self.app.editor.set(etree.tostring(parent, encoding="unicode"))
        info = schema.lookup(path("server", "enabled"))
        dialog = CreationDialog(self.app, parent, path("server"), info)
        self.root.update()
        self.assertIsInstance(dialog.value_entry, ttk.Entry)
        enabled = next(iid for iid, node in dialog.nodes.items() if node.tag == path("enabled")[-1])
        dialog.tree.selection_set(enabled)
        dialog.tree.event_generate("<<TreeviewSelect>>")
        self.root.update()
        self.assertEqual(tuple(dialog.suggestion_box.cget("values")), ("true", "false"))
        dialog.value.set("custom-value")
        self.assertEqual(dialog.value.get(), "custom-value")
        dialog.suggestion_box.current(1)
        dialog.use_suggestion()
        self.assertEqual(dialog.value.get(), "false")
        dialog.set_value()
        self.assertEqual(dialog.selected_node().text, "false")
        dialog.close()
