"""A broken RU provider must not hide healthy modules or invent state values."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock

from lxml import etree
from ncclient.operations.rpc import RPCError

from netconf_console.gui.client import GuiClient, ReadOptions, Snapshot
from netconf_console.gui.model import EditError, NC
from netconf_console.gui.operational import read_operational
from netconf_console.gui.schema import SchemaIndex

SYNC = "urn:o-ran:sync:1.0"
GOOD = "urn:healthy"
SYNC_TAG = "{%s}sync" % SYNC
GOOD_TAG = "{%s}device" % GOOD
VALUE = "24.817353333333333"
ERROR = ("failed to create leaf 'latitude' as a child of '/o-ran-sync:sync/gnss-status/gnss-data/location': "
         'Value "%s" of decimal64 type exceeds defined number (8) of fraction digits.. '
         "Schema path: /o-ran-sync:sync/gnss-status/gnss-data/location/latitude\nUser callback failed." % VALUE)
SOURCES = {
    "o-ran-sync": '''module o-ran-sync {yang-version 1.1;namespace "urn:o-ran:sync:1.0";prefix s;
      container sync {container gnss-config {leaf enable {type boolean;}}
        container gnss-status {config false;container gnss-data {container location {
          leaf latitude {type decimal64 {fraction-digits 8;}}
        }}}}
      container sync-extra {config false;leaf status {type string;}}
    }''',
    "healthy": '''module healthy {yang-version 1.1;namespace "urn:healthy";prefix h;
      container device {leaf name {type string;}leaf status {config false;type string;}}
    }''',
}


def data(body=""):
    return etree.fromstring(('<data xmlns="%s">%s</data>' % (NC, body)).encode())


def rpc_error(message=ERROR, tag="operation-failed"):
    root = etree.Element("{%s}rpc-error" % NC)
    for name, value in (("error-type", "application"), ("error-tag", tag),
                        ("error-severity", "error"), ("error-message", message)):
        etree.SubElement(root, "{%s}%s" % (NC, name)).text = value
    return RPCError(root)


BASELINE = data('<sync xmlns="%s"><gnss-config><enable>true</enable></gnss-config></sync>'
                '<device xmlns="%s"><name>RU</name></device>' % (SYNC, GOOD))
HEALTHY = data('<device xmlns="%s"><name>RU</name><status>up</status></device>' % GOOD)


class OperationalReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = SchemaIndex.compile(SOURCES)
        assert cls.schema.complete, cls.schema.warnings

    def client(self):
        client = GuiClient()
        manager = MagicMock()
        manager.server_capabilities = [
            "urn:ietf:params:netconf:capability:with-defaults:1.0?basic-mode=report-all-tagged"]
        client.context = Mock()
        client.context.require_manager.return_value = manager
        client.schema = self.schema
        manager.get_config_ncc.return_value = SimpleNamespace(data=BASELINE)

        def get(filter, defaults):
            if filter is None or (isinstance(filter, list) and any(n.tag == SYNC_TAG for n in filter)):
                raise rpc_error()
            return SimpleNamespace(data=HEALTHY)
        manager.get_ncc.side_effect = get
        return client, manager

    def test_decimal64_failure_keeps_fresh_config_and_healthy_state(self):
        client, manager = self.client()
        original = etree.tostring(BASELINE)
        result = client.read(ReadOptions(defaults=True, state=True))
        self.assertEqual(result.data.findtext('.//{%s}enable' % SYNC), "true")
        self.assertEqual(result.data.findtext('.//{%s}status' % GOOD), "up")
        self.assertIsNone(result.data.find('.//{%s}latitude' % SYNC))
        self.assertIn(SYNC_TAG, result.state_failures)
        self.assertIn("{%s}sync-extra" % SYNC, result.state_failures)
        self.assertIn(VALUE, result.state_failures[SYNC_TAG])
        self.assertTrue(result.warnings)
        self.assertEqual(manager.get_ncc.call_count, 3)  # full + two modules
        self.assertEqual(len(manager.get_ncc.call_args_list[1].kwargs["filter"]), 2)
        manager.get_config_ncc.assert_called_once_with(source="running", filter=None, defaults="report-all-tagged")
        self.assertTrue(all(call.kwargs["defaults"] == "report-all-tagged"
                            for call in manager.get_ncc.call_args_list))
        self.assertEqual(etree.tostring(BASELINE), original)
        manager.xrpc.assert_not_called()
        manager.lock.assert_not_called()
        manager.copy_config.assert_not_called()

    def test_alarm_provider_attribute_error_is_also_isolated(self):
        broken = rpc_error("'NoneType' object has no attribute 'alarm_list'\nUser callback failed.")
        state = Mock(side_effect=[broken, broken, HEALTHY])
        result = read_operational(state, lambda: BASELINE, self.schema)
        self.assertIn(SYNC_TAG, result.state_failures)
        self.assertIn("alarm_list", result.state_failures[SYNC_TAG])
        self.assertEqual(result.data.findtext('.//{%s}status' % GOOD), "up")

    def test_authorization_transport_and_filter_errors_do_not_fall_back(self):
        for error in (rpc_error("Access denied", "access-denied"),
                      rpc_error(ERROR, "access-denied"),
                      rpc_error("Unknown filter", "unknown-element"), TimeoutError("read timed out")):
            with self.subTest(error=error):
                state, config = Mock(side_effect=error), Mock()
                with self.assertRaises(type(error)):
                    read_operational(state, config, self.schema)
                state.assert_called_once()
                config.assert_not_called()

    def test_config_read_failure_propagates_without_module_requests(self):
        state = Mock(side_effect=rpc_error())
        with self.assertRaises(TimeoutError):
            read_operational(state, Mock(side_effect=TimeoutError()), self.schema)
        state.assert_called_once()

    def test_incomplete_schema_displays_only_running_and_marks_all_state_unknown(self):
        state = Mock(side_effect=rpc_error())
        result = read_operational(state, lambda: BASELINE, SchemaIndex(complete=False))
        self.assertEqual(etree.tostring(result.data), etree.tostring(BASELINE))
        self.assertIn("*", result.state_failures)
        state.assert_called_once()

    def test_unmodelled_running_root_prevents_claiming_state_is_complete(self):
        baseline = deepcopy(BASELINE)
        baseline.append(etree.Element("{urn:unknown}unknown"))
        state = Mock(side_effect=rpc_error())
        result = read_operational(state, lambda: baseline, self.schema)
        self.assertIn("*", result.state_failures)
        state.assert_called_once()

    def test_cancel_during_module_reads_stops_without_publishing_a_partial_snapshot(self):
        cancel = Mock(side_effect=[False, False, True])
        state = Mock(side_effect=rpc_error())
        with self.assertRaises(InterruptedError):
            read_operational(state, lambda: BASELINE, self.schema, cancelled=cancel)
        state.assert_called_once()

    def test_ignored_module_filter_is_not_accepted_as_success(self):
        state = Mock(side_effect=[rpc_error(), HEALTHY])  # first module is sync
        with self.assertRaisesRegex(EditError, "ignored"):
            read_operational(state, lambda: BASELINE, self.schema)

    def test_selected_root_refresh_keeps_only_its_running_baseline(self):
        baseline = data('<sync xmlns="%s"><gnss-config><enable>true</enable></gnss-config></sync>' % SYNC)
        state = Mock(side_effect=rpc_error())
        result = read_operational(state, lambda: baseline, self.schema, SYNC_TAG)
        self.assertEqual(set(result.state_failures), {SYNC_TAG})
        self.assertEqual([node.tag for node in result.data], [SYNC_TAG])
        self.assertEqual(state.call_count, 2)

    def test_replacing_a_root_preserves_other_failure_markers_and_clears_recovered_one(self):
        snapshot = Snapshot(deepcopy(BASELINE), ReadOptions(state=True),
                            state_failures={SYNC_TAG: ERROR, GOOD_TAG: "other failure"})
        refreshed = snapshot.replace_root(Snapshot(deepcopy(HEALTHY), ReadOptions(state=True)), GOOD_TAG)
        self.assertEqual(set(refreshed.state_failures), {SYNC_TAG})
        self.assertEqual(refreshed.data.findtext('.//{%s}status' % GOOD), "up")
        self.assertNotIn("up", etree.tostring(snapshot.data).decode())


if __name__ == "__main__":
    unittest.main()
