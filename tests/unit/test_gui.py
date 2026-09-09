from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lxml import etree
from ncclient.xml_ import to_xml

from netconf_console.gui.client import GuiClient, ReadOptions, Snapshot, defaults_mode, locate
from netconf_console.gui.demo import DEMO_XML, SOURCES, DemoClient
from netconf_console.gui.model import DEFAULT_ATTRIBUTES, EditError, NC, WD, WD_YANG, Selection, build_plan, children, node_style, parse_editor, xml_spans
from netconf_console.gui.schema import ModuleSpec, SchemaIndex, discover_modules, load_device_schemas

IF = "urn:ietf:params:xml:ns:yang:ietf-interfaces"
ORAN = "urn:o-ran:interfaces:1.0"
CAPS = ["urn:ietf:params:netconf:capability:writable-running:1.0",
        "urn:ietf:params:netconf:capability:with-defaults:1.0?basic-mode=explicit&also-supported=report-all,report-all-tagged"]


class SchemaTests(unittest.TestCase):
    def test_augment_inherited_config_typedef_defaults_and_list_keys(self):
        schema = SchemaIndex.compile(SOURCES)
        self.assertTrue(schema.complete, schema.warnings)
        interface = ("{%s}interfaces" % IF, "{%s}interface" % IF)
        self.assertEqual(schema.lookup(interface).keys, ("{%s}name" % IF,))
        self.assertEqual(schema.lookup(interface + ("{%s}enabled" % IF,)).defaults, ("true",))
        self.assertEqual(schema.lookup(interface + ("{%s}l2-mtu" % ORAN,)).defaults, ("1500",))
        self.assertFalse(schema.lookup(interface + ("{%s}statistics" % IF, "{%s}in-octets" % IF)).config)

    def test_choice_uses_features_and_submodule(self):
        sources = {
            "test": '''module test {yang-version 1.1;namespace "urn:test";prefix t;
              include sub; feature advanced;
              grouping data {leaf value {type string;default "x";}}
              container root {choice pick {case one {uses data;}} leaf optional {if-feature advanced; type string;}}
            }''',
            "sub": '''submodule sub {yang-version 1.1;belongs-to test {prefix t;}
              container extra {config false; leaf state {type string;}}}''',
        }
        schema = SchemaIndex.compile(sources, [ModuleSpec("test", features=())])
        self.assertTrue(schema.complete, schema.warnings)
        self.assertEqual(schema.lookup(("{urn:test}root", "{urn:test}value")).defaults, ("x",))
        self.assertIsNone(schema.lookup(("{urn:test}root", "{urn:test}optional")))
        self.assertFalse(schema.lookup(("{urn:test}extra", "{urn:test}state")).config)

    def test_missing_import_fails_closed(self):
        source = 'module x {namespace "urn:x";prefix x;import absent {prefix a;} leaf value {type a:unknown;}}'
        schema = SchemaIndex.compile({"x": source})
        self.assertFalse(schema.complete)
        self.assertTrue(schema.warnings)

    def test_disabled_uses_leafref_does_not_block_unrelated_edits(self):
        source = '''module a {yang-version 1.1;namespace "urn:a";prefix a;
          feature advanced;
          leaf target {if-feature advanced;type string;}
          grouping g {container hidden {if-feature advanced;
            leaf ref {type leafref {path "/a:target";}}}}
          container root {uses g; leaf value {type string;}}
        }'''
        schema = SchemaIndex.compile({"a": source}, [ModuleSpec("a", features=())])
        self.assertTrue(schema.complete, schema.warnings)
        self.assertTrue(schema.lookup(("{urn:a}root", "{urn:a}value")).config)
        self.assertIsNone(schema.lookup(("{urn:a}root", "{urn:a}hidden")))
        self.assertTrue(any("Inactive-feature leafref" in warning for warning in schema.warnings))
        active = source.replace('container hidden {if-feature advanced;', 'container hidden {')
        self.assertFalse(SchemaIndex.compile({"a": active}, [ModuleSpec("a", features=())]).complete)

    def test_deviation_changes_config(self):
        sources = {"a": 'module a {namespace "urn:a";prefix a;leaf x {type string;}}',
                   "b": 'module b {namespace "urn:b";prefix b;import a {prefix a;} deviation "/a:x" {deviate replace {config false;}}}'}
        schema = SchemaIndex.compile(sources)
        self.assertTrue(schema.complete, schema.warnings)
        self.assertFalse(schema.lookup(("{urn:a}x",)).config)

    def test_running_yang_library_module_set_selection(self):
        xml = '''<data><yang-library xmlns="urn:ietf:params:xml:ns:yang:ietf-yang-library">
          <content-id>1</content-id>
          <module-set><name>active</name><module><name>a</name><revision>2026-01-01</revision><feature>yes</feature></module></module-set>
          <module-set><name>other</name><module><name>b</name></module></module-set>
          <schema><name>run</name><module-set>active</module-set></schema>
          <datastore><name xmlns:ds="urn:ietf:params:xml:ns:yang:ietf-datastores">ds:running</name><schema>run</schema></datastore>
        </yang-library></data>'''
        manager = MagicMock()
        manager.get.return_value.data = etree.fromstring(xml.encode())
        specs, content_id, warnings = discover_modules(manager)
        self.assertEqual([spec.name for spec in specs], ["a"])
        self.assertEqual(specs[0].features, ("yes",))
        self.assertEqual(content_id, "1")

    def test_device_schema_load_dependency_cache_and_cancel(self):
        manager = MagicMock()
        manager.server_capabilities = CAPS
        # The default format is YANG. Explicit unprefixed format="yang"
        # is rejected by strict NETCONF servers (e.g. libnetconf2).
        manager.get_schema.side_effect = lambda name, version=None: SimpleNamespace(data=SOURCES[name])
        with tempfile.TemporaryDirectory() as directory, patch("netconf_console.gui.schema.config_dir", return_value=Path(directory)), patch(
            "netconf_console.gui.schema.discover_modules", return_value=([ModuleSpec(name) for name in SOURCES], "v1", [])
        ):
            schema = load_device_schemas(manager, "server1")
            self.assertTrue(schema.complete, schema.warnings)
            count = manager.get_schema.call_count
            cached = load_device_schemas(manager, "server1")
            self.assertEqual(count, manager.get_schema.call_count)
            self.assertEqual(schema.nodes, cached.nodes)
            with self.assertRaises(InterruptedError):
                load_device_schemas(manager, "server1", cancelled=lambda: True)


class EditModelTests(unittest.TestCase):
    def setUp(self):
        self.schema = SchemaIndex.compile(SOURCES)
        self.data = etree.fromstring(DEMO_XML.encode())
        self.interfaces = self.data[0]
        self.interface = self.interfaces[0]
        self.selection = Selection(self.interface, (self.interfaces,))

    def plan(self, old, new):
        return build_plan(self.selection, self.selection.text().replace(old, new), self.schema)

    def test_noop_ignores_pretty_whitespace(self):
        self.assertIsNone(build_plan(self.selection, self.selection.text(), self.schema).rpc)

    def test_only_changed_leaf_and_ancestor_keys_are_sent(self):
        plan = self.plan(">1500<", ">9000<")
        self.assertIn(">eth0<", plan.wire_xml)
        self.assertIn(">9000<", plan.wire_xml)
        self.assertNotIn("oper-status", plan.wire_xml)
        self.assertNotIn("vlan-tagging", plan.wire_xml)
        self.assertNotIn("wd:default", plan.wire_xml)
        self.assertIn("none</nc:default-operation>", plan.wire_xml)
        self.assertEqual(plan.wire_xml, to_xml(plan.rpc))
        self.assertEqual(plan.removals, 0)
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(node_style(next(iter(plan.changed)), (), self.schema, plan.changed), "changed")

    def test_selected_deep_leaf_keeps_namespace_and_list_key(self):
        leaf = self.interface.find("{%s}l2-mtu" % ORAN)
        selection = Selection(leaf, (self.interfaces, self.interface))
        plan = build_plan(selection, selection.text().replace("1500", "9000"), self.schema)
        config = plan.rpc.find("{%s}edit-config/{%s}config" % (NC, NC))
        self.assertEqual(config[0][0].findtext("{%s}name" % IF), "eth0")
        self.assertEqual(config[0][0].findtext("{%s}l2-mtu" % ORAN), "9000")

    def test_libyang_default_annotation_is_coloured_and_never_written(self):
        leaf = self.interface.find("{%s}l2-mtu" % ORAN)
        leaf.attrib.pop("{%s}default" % WD, None)
        leaf.set("{%s}default" % WD_YANG, "true")
        selected = Selection(leaf, (self.interfaces, self.interface))
        self.assertEqual(node_style(leaf, selected.path, self.schema), "default")
        plan = build_plan(selected, selected.text().replace("1500", "9000"), self.schema)
        self.assertIsNotNone(plan.rpc)
        self.assertFalse(any(name in DEFAULT_ATTRIBUTES for node in plan.rpc.iter() for name in node.attrib))

    def test_readonly_changes_removals_and_unknown_nodes_blocked(self):
        for old, new in ((">up<", ">down<"), ("<oper-status>up</oper-status>", ""),
                         ("</interface>", "<unknown>1</unknown></interface>")):
            with self.subTest(old=old), self.assertRaises(EditError):
                self.plan(old, new)

    def test_list_keys_and_root_namespace_cannot_be_changed(self):
        with self.assertRaises(EditError):
            self.plan(">eth0<", ">eth9<")
        with self.assertRaises(EditError):
            self.plan(IF, "urn:evil")
        key = self.interface.find("{%s}name" % IF)
        selection = Selection(key, (self.interfaces, self.interface))
        with self.assertRaises(EditError):
            build_plan(selection, selection.text().replace("eth0", "eth9"), self.schema)

    def test_delete_is_explicit_remove_and_does_not_include_state(self):
        edited = deepcopy(self.interface)
        edited.remove(edited.find("{%s}description" % IF))
        plan = build_plan(self.selection, etree.tostring(edited).decode(), self.schema)
        removed = plan.rpc.find(".//{%s}description" % IF)
        self.assertEqual(removed.get("{%s}operation" % NC), "remove")
        self.assertEqual(plan.removals, 1)

    def test_duplicate_leaf_missing_keys_and_nc_operation_rejected(self):
        for old, new in (("</interface>", "<enabled>false</enabled></interface>"),
                         ("<name>eth0</name>", ""),
                         ("<name>", '<name xmlns:nc="%s" nc:operation="delete">' % NC)):
            with self.subTest(old=old), self.assertRaises(EditError):
                self.plan(old, new)

    def test_invalid_xml_entities_and_incomplete_schema_blocked(self):
        for text in ("<broken>", '<!DOCTYPE x [<!ENTITY external SYSTEM "file:///private">]><x>&external;</x>'):
            with self.assertRaises((EditError, etree.XMLSyntaxError)):
                parse_editor(text)
        self.schema.complete = False
        with self.assertRaises(EditError):
            self.plan(">1500<", ">9000<")

    def test_xml_source_spans_include_cdata_and_multiline_values(self):
        text = '<root><!-- <ignored/> --><leaf>line1\nline2</leaf><data><![CDATA[<not-element/>]]></data></root>'
        root = parse_editor(text)
        spans = list(xml_spans(text, root))
        self.assertEqual(len(spans), 3)
        self.assertEqual(text[spans[0][3]:spans[0][4]], "line1\nline2")

    def test_three_distinct_colours(self):
        path = self.selection.path
        enabled = self.interface.find("{%s}enabled" % IF)
        state = self.interface.find("{%s}oper-status" % IF)
        explicit = self.interface.find("{%s}vlan-tagging" % ORAN)
        self.assertEqual(node_style(enabled, path+(enabled.tag,), self.schema), "default")
        self.assertEqual(node_style(state, path+(state.tag,), self.schema), "state")
        self.assertEqual(node_style(explicit, path+(explicit.tag,), self.schema), "schema_default")

    def test_leaf_list_parent_replace_and_order_guards(self):
        source = 'module x {yang-version 1.1;namespace "urn:x";prefix x;container root {leaf-list names {type string;ordered-by user;}}}'
        schema = SchemaIndex.compile({"x": source})
        root = etree.fromstring(b'<root xmlns="urn:x"><names>a</names><names>b</names></root>')
        selection = Selection(root)
        with self.assertRaises(EditError):
            build_plan(Selection(root[0], (root,)), '<names xmlns="urn:x">z</names>', schema)
        plan = build_plan(selection, '<root xmlns="urn:x"><names>b</names><names>z</names></root>', schema)
        self.assertEqual(plan.removals, 1)
        self.assertEqual(len(plan.changes), 2)
        with self.assertRaises(EditError):
            build_plan(selection, '<root xmlns="urn:x"><names>a</names><names>z</names><names>b</names></root>', schema)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.manager = MagicMock()
        self.manager.server_capabilities = CAPS
        self.data = etree.fromstring(DEMO_XML.encode())
        self.manager.get_config_ncc.return_value = SimpleNamespace(data=self.data)
        self.manager.get_ncc.return_value = SimpleNamespace(data=self.data)
        self.manager.xrpc.return_value = SimpleNamespace(xml='<rpc-reply><ok/></rpc-reply>')
        self.client = GuiClient()
        self.client.context = MagicMock()
        self.client.context.require_manager.return_value = self.manager
        self.client.schema = SchemaIndex.compile(SOURCES)
        self.selection = Selection(self.data[0][0], (self.data[0],))
        self.plan = build_plan(self.selection, self.selection.text().replace(">1500<", ">9000<"), self.client.schema)

    def test_defaults_negotiation_does_not_invent_missing_values(self):
        self.assertEqual(defaults_mode(CAPS, True)[0], "report-all-tagged")
        self.assertEqual(defaults_mode(CAPS, False), (None, []))
        self.assertTrue(defaults_mode([], True)[1])
        self.assertIsNone(defaults_mode([], True)[0])

    def test_source_running_and_state_get_are_distinct(self):
        self.client.read(ReadOptions())
        self.manager.get_config_ncc.assert_called_once_with(source="running", filter=None, defaults=None)
        self.client.read(ReadOptions(defaults=True, state=True))
        self.manager.get_ncc.assert_called_once_with(filter=None, defaults="report-all-tagged")
        with self.assertRaises(EditError):
            self.client.read(ReadOptions(source="candidate", state=True))

    def test_apply_lock_conflict_check_exact_preview_unlock_no_commit(self):
        self.client.apply(self.selection, self.plan, ReadOptions(defaults=True, state=True))
        self.manager.lock.assert_called_once_with(target="running")
        actual = self.manager.xrpc.call_args.args[0]
        self.assertEqual(to_xml(actual), self.plan.wire_xml)
        self.manager.unlock.assert_called_once_with(target="running")
        self.manager.commit.assert_not_called()
        self.manager.copy_config.assert_not_called()

    def test_external_config_change_blocks_write_and_unlocks(self):
        latest = deepcopy(self.data)
        latest.find(".//{%s}description" % IF).text = "another operator"
        self.manager.get_config_ncc.return_value.data = latest
        with self.assertRaises(EditError):
            self.client.apply(self.selection, self.plan, ReadOptions(defaults=True))
        self.manager.xrpc.assert_not_called()
        self.manager.unlock.assert_called_once()

    def test_changing_operational_state_does_not_cause_false_conflict(self):
        latest = deepcopy(self.data)
        latest.find(".//{%s}in-octets" % IF).text = "999999"
        self.manager.get_config_ncc.return_value.data = latest
        self.client.apply(self.selection, self.plan, ReadOptions(defaults=True, state=True))
        self.manager.xrpc.assert_called_once()

    def test_rpc_error_unlocks_and_does_not_retry(self):
        self.manager.xrpc.side_effect = RuntimeError("RPC timed out")
        with self.assertRaises(RuntimeError):
            self.client.apply(self.selection, self.plan, ReadOptions(defaults=True))
        self.manager.xrpc.assert_called_once()
        self.manager.unlock.assert_called_once()

    def test_preview_tampering_or_source_change_rejected_before_lock(self):
        self.plan.rpc.set("message-id", "tampered")
        with self.assertRaises(EditError):
            self.client.apply(self.selection, self.plan, ReadOptions(defaults=True))
        self.manager.lock.assert_not_called()

    def test_locate_uses_keys_not_list_position(self):
        data = deepcopy(self.data)
        data[0].insert(0, data[0][-1])
        self.assertEqual(locate(data, self.selection, self.client.schema).findtext("{%s}name" % IF), "eth0")


if __name__ == "__main__":
    unittest.main()
