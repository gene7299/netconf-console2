"""Read-only export and conservative visibility comparisons, without a DUT."""
import unittest
from unittest.mock import Mock

from lxml import etree

from netconf_console.gui.model import EditError
from netconf_console.gui.schema import SchemaIndex
from netconf_console.gui.sysrepo import export_tree
from netconf_console.gui.tree_visibility import visibility

YANG = '''module test-tree {yang-version 1.1; namespace "urn:tree"; prefix t;
 container root {list item {key "name"; leaf name {type string;}
 leaf value {type string;}} leaf-list tags {type string;} leaf hidden {type string;}}
}'''


def data(body):
    return etree.fromstring(('<data><root xmlns="urn:tree">' + body + '</root></data>').encode())


class VisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = SchemaIndex.compile({"test-tree": YANG})

    def test_missing_leaf_and_value_change_are_different(self):
        repo = data('<item><name>a</name><value>new</value></item><hidden>x</hidden>')
        nc = data('<item><name>a</name><value>old</value></item>')
        states = visibility(repo, nc, self.schema)
        self.assertEqual(states[repo[0][0][1]], "visible")
        self.assertEqual(states[repo[0][1]], "missing")

    def test_list_keys_and_leaf_list_values_not_positions(self):
        repo = data('<item><name>b</name><value>1</value></item>'
                    '<item><name>a</name></item><tags>x</tags><tags>y</tags>')
        nc = data('<item><name>a</name></item><tags>y</tags>')
        states = visibility(repo, nc, self.schema)
        self.assertEqual(states[repo[0][0]], "missing")
        self.assertEqual(states[repo[0][0][1]], "missing")
        self.assertEqual(states[repo[0][1]], "visible")
        self.assertEqual(states[repo[0][2]], "missing")
        self.assertEqual(states[repo[0][3]], "visible")

    def test_unknown_schema_and_missing_keys_are_not_false_matches(self):
        repo = data('<item><name>a</name></item>')
        self.assertEqual(set(visibility(repo, repo, SchemaIndex()).values()), {"unknown"})
        nc = data('<item><value>x</value></item>')
        self.assertEqual(visibility(repo, nc, self.schema)[repo[0][0]], "unknown")

    def test_failed_read_is_not_successful_empty_read(self):
        repo = data('<hidden>x</hidden>')
        self.assertEqual(set(visibility(repo, None, self.schema).values()), {"unknown"})
        empty = etree.fromstring(b'<data/>')
        self.assertEqual(set(visibility(repo, empty, self.schema).values()), {"missing"})

    def test_namespaces_and_duplicate_identities(self):
        repo = data('<hidden>x</hidden>')
        nc = etree.fromstring(b'<data><root xmlns="urn:other"><hidden>x</hidden></root></data>')
        self.assertEqual(set(visibility(repo, nc, self.schema).values()), {"missing"})
        repo = data('<item><name>a</name></item><item><name>a</name></item>')
        nc = data('<item><name>a</name></item>')
        self.assertEqual(visibility(repo, nc, self.schema)[repo[0][0]], "unknown")


class ExportTests(unittest.TestCase):
    def test_read_all_modules_without_any_write(self):
        shell = Mock()
        shell.run.return_value = (b'<?xml version="1.0"?><a xmlns="urn:a"/><b xmlns="urn:b"/>', b'')
        for source in ("running", "candidate", "startup", "operational"):
            with self.subTest(source=source):
                root, warning = export_tree(shell, source)
                self.assertEqual(len(root), 2)
                self.assertFalse(warning)
                shell.run.assert_called_with(
                    'sysrepocfg --export --datastore %s --format xml --timeout 10 --defaults explicit' % source,
                    timeout=20)

    def test_reject_injection_and_invalid_timeout_before_ssh(self):
        shell = Mock()
        for program in ("sudo sysrepocfg", "sysrepocfg; id", "../sysrepocfg", "$(id)"):
            with self.assertRaises(EditError):
                export_tree(shell, program=program)
        for timeout in (0, 121, True, "10"):
            with self.assertRaises(EditError):
                export_tree(shell, timeout=timeout)
        with self.assertRaises(EditError):
            export_tree(shell, datastore="running; id")
        shell.run.assert_not_called()

    def test_reject_non_xml_and_unsafe_xml(self):
        for raw in (b'not XML', b'<x/> trailing garbage',
                    b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'):
            shell = Mock()
            shell.run.return_value = (raw, b'')
            with self.subTest(raw=raw), self.assertRaises(Exception):
                export_tree(shell)

    def test_empty_export_and_failure_no_retry(self):
        shell = Mock()
        shell.run.return_value = (b'', b'')
        self.assertEqual(len(export_tree(shell)[0]), 0)
        shell.reset_mock()
        shell.run.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            export_tree(shell)
        self.assertEqual(shell.run.call_count, 1)
