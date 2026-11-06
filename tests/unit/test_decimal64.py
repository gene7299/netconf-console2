"""Exact per-leaf decimal contracts and every structured outgoing XML path."""
from copy import deepcopy
from decimal import localcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from lxml import etree
from ncclient.xml_ import to_xml

from netconf_console.yang_decimal import Decimal64Error, Decimal64Rules, decimal_rules, format_scaled
from netconf_console.gui import lifecycle, raw_rpc, subscription_templates, sysrepo
from netconf_console.gui.client import GuiClient, ReadOptions, config_semantic
from netconf_console.gui.creation import Template, validate_scalar
from netconf_console.gui.decimal_validation import decimal_diagnostics, reference_rules, validate_decimal_statement
from netconf_console.gui.drafts import DraftShelf, draft_key, schema_fingerprint
from netconf_console.gui.model import EditError, NC, Selection, build_plan
from netconf_console.gui.schema import SchemaIndex
from netconf_console.gui.workspace import import_selection

SOURCE = '''module d {yang-version 1.1;namespace "urn:d";prefix d;
  typedef signed {type decimal64 {fraction-digits 3;range "-10..-2 | 0..10";}}
  typedef narrow {type signed {range "min..-2 | 2..max";}}
  container root {
    leaf value {type narrow {range "min..-3 | 3..max";}}
    leaf reference {type leafref {path "../value";}}
    leaf choice {type union {type narrow;type enumeration {enum unlimited;}}}
    leaf free-choice {type union {type narrow;type string;}}
    leaf-list samples {type decimal64 {fraction-digits 1;range "0..9.9";}}
    list item {key "id";
      leaf id {type decimal64 {fraction-digits 1;range "0..2";}}
      leaf gain {type narrow;}}
  }
  notification measurement {leaf value {type narrow;}}
  rpc tune {input {leaf value {type narrow;}}}
}'''
ROOT = "{urn:d}root"
VALUE = (ROOT, "{urn:d}value")


class ExactDecimalTests(unittest.TestCase):
    def test_all_v15_precision_classes_and_signed_limits(self):
        for digits in (1, 3, 4, 5, 8, 18):
            with self.subTest(digits=digits):
                rules = Decimal64Rules(digits)
                for scaled in (-(2**63), -1, 0, 1, 2**63 - 1):
                    self.assertEqual(rules.validate(format_scaled(scaled, digits)), scaled)
                for scaled in (-(2**63) - 1, 2**63):
                    with self.assertRaises(Decimal64Error):
                        rules.validate(format_scaled(scaled, digits))
                with self.assertRaises(Decimal64Error):
                    rules.validate("0." + "0" * (digits + 1))

    def test_invalid_xml_lexical_forms_and_trailing_zeros(self):
        rules = Decimal64Rules(3)
        for value in ("NaN", "Infinity", "-Infinity", "1e0", "1E+2", ".5", "1.", " 1", "1 ",
                      "1\n", "١.٢", "1.0000", "1.00000000000000000000000000000001", "--1", ""):
            with self.subTest(value=value), self.assertRaises(Decimal64Error):
                rules.validate(value)
        self.assertEqual(rules.validate("+0001.230"), 1230)

    def test_decimal_context_cannot_round_an_invalid_value_into_the_range(self):
        with localcontext() as context:
            context.prec = 2
            rules = Decimal64Rules(8)
            self.assertEqual(rules.validate("24.81735333"), 2481735333)
            with self.assertRaises(Decimal64Error):
                rules.validate("24.817353333333333")
            rules = Decimal64Rules(18)
            self.assertEqual(rules.validate("9.223372036854775807"), 2**63 - 1)

    def test_missing_precision_is_rejected_instead_of_guessing_eighteen(self):
        for constraints in ({}, {"fraction-digits": "0"}, {"fraction-digits": "19"}):
            with self.assertRaises(Decimal64Error):
                Decimal64Rules.from_constraints(constraints)

    def test_v15_catalogue_retains_every_leaf_own_contract(self):
        rules = reference_rules()
        self.assertEqual(len(rules), 72)
        latitude = next(rule for path, rule in rules.items() if path[-1].endswith("}latitude"))
        longitude = next(rule for path, rule in rules.items() if path[-1].endswith("}longitude"))
        self.assertEqual(latitude.fraction_digits, 8)
        for value in ("-90", "90", "24.81735333"):
            latitude.validate(value)
        for value in ("-90.00000001", "90.00000001", "24.817353333333333"):
            with self.assertRaises(Decimal64Error):
                latitude.validate(value)
        self.assertEqual(longitude.validate("180"), 18000000000)
        with self.assertRaises(Decimal64Error):
            longitude.validate("180.00000001")
        self.assertEqual({rule.fraction_digits for rule in rules.values()}, {1, 3, 4, 5, 8})


class DecimalEntryPoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = SchemaIndex.compile({"d": SOURCE})
        assert cls.schema.complete, cls.schema.warnings

    def setUp(self):
        self.root = etree.fromstring(b'<root xmlns="urn:d"><value>3.000</value></root>')
        self.selection = Selection(self.root)

    def test_typedef_chains_min_max_and_disjoint_ranges(self):
        typ = self.schema.statements[VALUE].search_one("type")
        rules = decimal_rules(typ.i_type_spec)
        self.assertEqual(rules.ranges, ((-10000, -3000), (3000, 10000)))
        self.assertEqual(Decimal64Rules.from_constraints(rules.constraints()), rules)
        for value in ("-10", "-3", "3", "10"):
            validate_decimal_statement(self.schema.statements[VALUE], value, "value")
        for value in ("-10.001", "-2.999", "0", "2.999", "10.001", "3.0000"):
            with self.subTest(value=value), self.assertRaises(EditError):
                validate_decimal_statement(self.schema.statements[VALUE], value, "value")

    def test_manual_xml_leaf_and_parent_edits_both_validate_before_preview(self):
        for selected in (self.selection, Selection(self.root[0], (self.root,))):
            for value in ("3.0001", "2.999", "1e1", "NaN"):
                with self.subTest(value=value, path=selected.path), self.assertRaises(EditError):
                    build_plan(selected, selected.text().replace("3.000", value), self.schema)
            plan = build_plan(selected, selected.text().replace("3.000", "4.001"), self.schema)
            self.assertIn("4.001", plan.wire_xml)

    def test_creation_import_and_leaf_list_use_the_same_restrictions(self):
        template = Template(self.schema, self.schema.lookup(VALUE))
        with self.assertRaises(EditError):
            template.set_value(template.root, "2.999")
        template.set_value(template.root, "3.001")
        with self.assertRaises(EditError):
            import_selection(etree.tostring(self.root).replace(b"3.000", b"0.0"), self.selection, self.schema)
        with self.assertRaises(EditError):
            build_plan(self.selection, self.selection.text().replace("</root>", "<samples>1.00</samples></root>"), self.schema)

    def test_leafref_and_decimal_union_members_keep_target_precision(self):
        for name in ("reference", "choice"):
            info = self.schema.lookup((ROOT, "{urn:d}" + name))
            node = etree.Element(info.path[-1])
            for value in ("1.2345", "3.0000", "3\n", "0"):
                node.text = value
                with self.subTest(name=name, value=value), self.assertRaises(EditError):
                    validate_scalar(self.schema, info, node)
        # A union's other legal branch must not inherit decimal restrictions.
        for name, value in (("choice", "unlimited"), ("free-choice", "1e999")):
            info = self.schema.lookup((ROOT, "{urn:d}" + name))
            node = etree.Element(info.path[-1])
            node.text = value
            validate_scalar(self.schema, info, node)

    def test_notification_filters_check_inherited_effective_ranges(self):
        definition = subscription_templates.notification_catalog(self.schema.modules)["measurement"]
        field = definition["fields"][0]
        self.assertEqual(field["constraints"]["fraction-digits"], "3")
        for value in ("-1", "1", "2.0000", "1e0"):
            with self.subTest(value=value), self.assertRaises(EditError):
                subscription_templates.notification_filter(definition, {("value",): [value]})
        self.assertIn("3.001", subscription_templates.notification_filter(definition, {("value",): ["3.001"]}))

    def rpc_xml(self, value):
        return ('<edit-config xmlns="%s"><target><running/></target><default-operation>none</default-operation>'
                '<config><root xmlns="urn:d"><value>%s</value></root></config></edit-config>') % (NC, value)

    def test_raw_rpc_preview_and_execution_revalidate_config_filters_and_rpc_inputs(self):
        for text in (self.rpc_xml("0.0"), '<tune xmlns="urn:d"><value>0</value></tune>',
                     '<get xmlns="%s"><filter><root xmlns="urn:d"><value>0</value></root></filter></get>' % NC):
            with self.subTest(text=text), self.assertRaises(EditError):
                raw_rpc.prepare(text, self.schema)
            prepared = raw_rpc.prepare(text)  # Unbound passthrough remains possible.
            manager = Mock()
            client = SimpleNamespace(connected=True, manager=manager, schema=self.schema, pending_commit=None)
            with self.assertRaises(EditError):
                raw_rpc.execute(client, manager, prepared)
            manager.xrpc.assert_not_called()
        raw_rpc.prepare('<get xmlns="%s"><filter><root xmlns="urn:d"><value/></root></filter></get>' % NC, self.schema)
        raw_rpc.prepare('<tune xmlns="urn:d"><value>3.001</value></tune>', self.schema)
        raw_rpc.prepare(self.rpc_xml("4.001"), self.schema)

    def test_sysrepo_and_netconf_reject_invalid_coherent_plans_before_writes(self):
        plan = build_plan(self.selection, self.selection.text().replace("3.000", "4.001"), self.schema)
        plan.rpc.find('.//{urn:d}value').text = "1.000"
        plan.wire_xml = to_xml(plan.rpc)
        with self.assertRaises(EditError):
            sysrepo.prepare(plan, self.schema)
        manager = Mock(server_capabilities=["urn:ietf:params:netconf:capability:writable-running:1.0"])
        client = GuiClient()
        client.schema = self.schema
        client.context = Mock()
        client.context.require_manager.return_value = manager
        with self.assertRaises(EditError):
            client.apply(self.selection, plan, ReadOptions())
        manager.lock.assert_not_called()
        manager.xrpc.assert_not_called()

    def test_received_invalid_values_are_reported_without_rounding_or_dropping(self):
        received = deepcopy(self.root)
        received[0].text = "3.00001"
        original = etree.tostring(received)
        wrapper = etree.Element("data")
        wrapper.append(received)
        errors = decimal_diagnostics(wrapper, self.schema)
        self.assertIn(VALUE, errors)
        self.assertIn("3.00001", errors[VALUE])
        self.assertEqual(etree.tostring(received), original)

    def test_equal_decimal_encodings_do_not_cause_false_changes_or_readback_conflicts(self):
        equivalent = deepcopy(self.root)
        equivalent[0].text = "+003.0"
        self.assertEqual(config_semantic(self.root, (ROOT,), self.schema),
                         config_semantic(equivalent, (ROOT,), self.schema))
        self.assertIsNone(build_plan(self.selection, etree.tostring(equivalent).decode(), self.schema).rpc)
        with self.assertRaises(EditError):
            build_plan(self.selection, self.selection.text().replace(
                "</root>", "<samples>1.0</samples><samples>01</samples></root>"), self.schema)

    def test_decimal_list_keys_use_numeric_identity_and_validate_target_keys(self):
        root = etree.fromstring(b'<root xmlns="urn:d"><item><id>1.0</id><gain>3.000</gain></item></root>')
        selected = Selection(root[0], (root,))
        edited = selected.text().replace("1.0<", "01<").replace("3.000", "4.001")
        plan = build_plan(selected, edited, self.schema)
        self.assertIn("4.001", plan.wire_xml)
        root[0][0].text = "1.00"  # Invalid received key must not be used in a write.
        with self.assertRaises(EditError):
            build_plan(selected, selected.text().replace("3.000", "4.001"), self.schema)

    def test_lifecycle_blocks_copying_invalid_decimal_source_but_allows_server_validate(self):
        invalid = etree.Element("data")
        invalid.append(deepcopy(self.root))
        invalid[0][0].text = "2.999"
        manager = Mock()
        client = SimpleNamespace(manager=manager, schema=self.schema,
            capabilities=["urn:ietf:params:netconf:capability:startup:1.0",
                          "urn:ietf:params:netconf:capability:validate:1.0"],
            read=lambda options: SimpleNamespace(data=invalid))
        with self.assertRaises(EditError):
            lifecycle.prepare(client, "save")
        manager.xrpc.assert_not_called()
        manager.lock.assert_not_called()
        self.assertEqual(lifecycle.prepare(client, "validate").operation, "validate")

    def test_legacy_decimal_key_drafts_remain_loadable_and_migrate_on_capture(self):
        root = etree.fromstring(b'<root xmlns="urn:d"><item><id>1</id><gain>3.000</gain></item></root>')
        selected = Selection(root[0], (root,))
        shelf = DraftShelf()
        entry = shelf.capture("RU", "running", selected, selected.text(), self.schema, None, schema_fingerprint(self.schema))
        del shelf.entries[entry.key]
        entry.key = draft_key("RU", "running", selected, self.schema, canonical=False)
        shelf.entries[entry.key] = entry
        current = deepcopy(root)
        current[0][0].text = "1.0"
        current[0][1].text = "3.0"
        latest = Selection(current[0], (current,))
        self.assertIs(shelf.matching("RU", "running", latest, self.schema), entry)
        data = etree.Element("data")
        data.append(current)
        shelf.verify(entry, "RU", "running", self.schema, data, object())
        self.assertIs(shelf.capture("RU", "running", latest, latest.text(), self.schema, None,
                                    schema_fingerprint(self.schema)), entry)
        self.assertEqual(len(shelf.entries), 1)
        self.assertEqual(entry.key, draft_key("RU", "running", latest, self.schema))


if __name__ == "__main__":
    unittest.main()
