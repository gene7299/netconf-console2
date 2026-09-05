from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lxml import etree

from netconf_console import operations


class Reply:
    def __init__(self, root, data=None):
        self._root = [root]
        self.data = data if data is not None else root


class FakeManager:
    def __init__(self):
        self.calls = []
        self.server_capabilities = [
            "urn:ietf:params:netconf:base:1.0",
            "urn:ietf:params:netconf:base:1.1",
        ]
        self._session = SimpleNamespace(_base=2, _device_handler=operations.ConsoleDeviceHandler())
        self.connected = True
        self.session_id = "7"

    def _ok(self):
        return Reply(etree.Element("{urn:ietf:params:xml:ns:netconf:base:1.0}ok"))

    def get_ncc(self, *args):
        self.calls.append(("get", args))
        data = etree.Element("{urn:ietf:params:xml:ns:netconf:base:1.0}data")
        return Reply(data, data=data)

    def get_config_ncc(self, *args):
        self.calls.append(("get-config", args))
        data = etree.Element("{urn:ietf:params:xml:ns:netconf:base:1.0}data")
        return Reply(data, data=data)

    def edit_config(self, *args, **kwargs):
        self.calls.append(("edit-config", args, kwargs))
        return self._ok()

    def copy_config(self, *args):
        self.calls.append(("copy-config", args))
        return self._ok()

    def delete_config(self, *args):
        self.calls.append(("delete-config", args))
        return self._ok()

    def cancel_commit(self, *args):
        self.calls.append(("cancel-commit", args))
        return self._ok()

    def close_session(self):
        self.calls.append(("close-session", ()))
        return self._ok()

    def lock(self, *args):
        self.calls.append(("lock", args))
        return self._ok()

    def unlock(self, *args):
        self.calls.append(("unlock", args))
        return self._ok()

    def get_schema(self, *args):
        self.calls.append(("get-schema", args))
        schema = etree.Element("schema")
        schema.text = "module demo { namespace 'urn:demo'; prefix d; }"
        return Reply(schema, data=schema.text)

    def commit(self, **kwargs):
        self.calls.append(("commit", kwargs))
        return self._ok()

    def rpc(self, element):
        self.calls.append(("rpc", element))
        return self._ok()

    def xrpc(self, element):
        self.calls.append(("xrpc", element))
        return self._ok()


def ns(**values):
    defaults = dict(
        style=[], xpath=None, filter=None, filter_file=None, ns=None,
        wdefaults=None, winactive=False, db="running", test=None,
        default_operation=None, error_option=None, operation="merge",
        deloperation="remove", timeout=None, persist=None, persist_id=None,
        stream=None, start=None, stop=None, content=None, rpc_content=None,
    )
    defaults.update(values)
    return SimpleNamespace(**defaults)


class OperationTests(unittest.TestCase):
    def test_get_xpath_with_defaults(self):
        manager = FakeManager()
        result = operations.Get().invoke(manager, ns(xpath="/interfaces", wdefaults="trim"))
        self.assertEqual(result.tag, "{urn:ietf:params:xml:ns:netconf:base:1.0}data")
        self.assertEqual(manager.calls[0][0], "get")
        self.assertEqual(manager.calls[0][1][0], ("xpath", ({}, "/interfaces")))

    def test_common_yang_namespaces_are_resolved_automatically(self):
        manager = FakeManager()
        xpath = "/if:interfaces/if:interface[if:name='eth0']/oran:mac-address"
        operations.Get().invoke(manager, ns(xpath=xpath))
        mapping = manager.calls[-1][1][0][1][0]
        self.assertEqual(
            mapping["if"], "urn:ietf:params:xml:ns:yang:ietf-interfaces"
        )
        self.assertEqual(mapping["oran"], "urn:o-ran:interfaces:1.0")

    def test_unknown_yang_prefix_fails_locally_and_explicit_ns_overrides(self):
        manager = FakeManager()
        with self.assertRaisesRegex(ValueError, "--ns vendor=URI"):
            operations.Get().invoke(manager, ns(xpath="/vendor:system/vendor:name"))
        mapping = operations.nsmap(
            ns(ns=["if=urn:vendor:interfaces"]), "/if:interfaces/if:name"
        )
        self.assertEqual(mapping["if"], "urn:vendor:interfaces")

    def test_device_discovery_precedes_builtin_and_explicit_ns_wins(self):
        discovered = {
            "if": "urn:device:interfaces",
            "vr": "urn:vendor:radio",
            "unused": "urn:vendor:unused",
        }
        mapping = operations.nsmap(
            ns(discovered_namespaces=discovered), "/if:interfaces/vr:radio"
        )
        self.assertEqual(mapping["if"], "urn:device:interfaces")
        self.assertEqual(mapping["vr"], "urn:vendor:radio")
        self.assertNotIn("unused", mapping)
        overridden = operations.nsmap(
            ns(discovered_namespaces=discovered, ns=["if=urn:explicit:interfaces"]),
            "/if:interfaces",
        )
        self.assertEqual(overridden["if"], "urn:explicit:interfaces")

    def test_namespaces_labels_an_explicit_override_correctly(self):
        context = SimpleNamespace(namespace_registry=None)
        result = operations.Namespaces().invoke(
            context,
            ns(
                discovered_namespaces={"if": "urn:device:interfaces"},
                ns=["if=urn:explicit:interfaces"],
            ),
        )
        self.assertIn(
            "if                   explicit     urn:explicit:interfaces",
            result.text,
        )

    def test_edit_config_passes_all_rfc6241_parameters(self):
        manager = FakeManager()
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "config.xml"
            filename.write_text("<interfaces xmlns='urn:example'><interface/></interfaces>", encoding="utf-8")
            operations.EditConfig().invoke(manager, ns(
                db="candidate", test="test-only", default_operation="none",
                error_option="rollback-on-error",
            ), str(filename))
        call = manager.calls[-1]
        self.assertEqual(call[0], "edit-config")
        self.assertEqual(call[2]["target"], "candidate")
        self.assertEqual(call[2]["default_operation"], "none")
        self.assertEqual(call[2]["test_option"], "test-only")
        self.assertEqual(call[2]["error_option"], "rollback-on-error")

    def test_confirmed_commit_persist_and_persist_id(self):
        manager = FakeManager()
        operations.Commit().invoke(manager, ns(timeout=30, persist="token"), "confirmed")
        self.assertEqual(manager.calls[-1][1], {
            "confirmed": True, "timeout": "30", "persist": "token"
        })
        operations.Commit().invoke(manager, ns(persist_id="token"), False)
        self.assertEqual(manager.calls[-1][1], {"confirmed": False, "persist_id": "token"})

    def test_rpc_error_fields_are_preserved(self):
        root = etree.fromstring(
            b"<rpc-reply xmlns='urn:ietf:params:xml:ns:netconf:base:1.0'>"
            b"<rpc-error><error-type>protocol</error-type><error-tag>invalid-value</error-tag>"
            b"<error-severity>error</error-severity><error-path>/x</error-path>"
            b"<error-message>bad</error-message><error-info><x>1</x></error-info>"
            b"</rpc-error></rpc-reply>"
        )
        details = operations.rpc_error_details(root)
        self.assertEqual(details[0]["error-tag"], "invalid-value")
        self.assertIn("error-info", details[0])

    def test_set_builds_namespaced_edit(self):
        manager = FakeManager()
        operations.Set().invoke(manager, ns(ns=["x=urn:example"], operation="replace"),
                                 "/x:root/x:leaf=value")
        config = manager.calls[-1][1][0]
        leaf = config[0][0]
        self.assertEqual(leaf.tag, "{urn:example}leaf")
        self.assertEqual(leaf.text, "value")
        self.assertEqual(leaf.get("{urn:ietf:params:xml:ns:netconf:base:1.0}operation"), "replace")

    def test_full_rpc_preserves_message_id_and_user_rpc_alias(self):
        manager = FakeManager()
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "request.xml"
            filename.write_text(
                "<rpc xmlns='urn:ietf:params:xml:ns:netconf:base:1.0' message-id='fixed'>"
                "<get/></rpc>", encoding="utf-8"
            )
            result = operations.Rpc().invoke(manager, ns(content=str(filename), full=True))
        self.assertEqual(result.tag, "{urn:ietf:params:xml:ns:netconf:base:1.0}ok")
        self.assertEqual(manager.calls[-1][0], "xrpc")
        self.assertEqual(manager.calls[-1][1].get("message-id"), "fixed")

    def test_notification_listener_writes_received_notification(self):
        stream = io.StringIO()
        listener = operations.NotificationListener(stream)
        tag = "{%s}notification" % operations.NOTIFICATION_NS
        listener.callback((tag, {}), "<notification/>" )
        self.assertEqual(stream.getvalue(), "<notification/>\n")

    def test_core_datastore_lifecycle_operations_use_rfc_arguments(self):
        manager = FakeManager()
        operations.CopyConfig().invoke(manager, ns(db="candidate", source="running"))
        operations.DeleteConfig().invoke(manager, ns(db="startup"))
        operations.CancelCommit().invoke(manager, ns(persist_id="token"))
        operations.Lock().invoke(manager, ns(db="candidate"))
        operations.Unlock().invoke(manager, ns(db="candidate"))
        operations.CloseSession().invoke(manager, ns())
        self.assertEqual(manager.calls[0], ("copy-config", ("running", "candidate")))
        self.assertEqual(manager.calls[1], ("delete-config", ("startup",)))
        self.assertEqual(manager.calls[2], ("cancel-commit", ("token",)))
        self.assertEqual(manager.calls[3], ("lock", ("candidate",)))
        self.assertEqual(manager.calls[4], ("unlock", ("candidate",)))
        self.assertEqual(manager.calls[5][0], "close-session")

    def test_subscribe_and_nmda_rpc_xml(self):
        manager = FakeManager()
        operations.Subscribe().invoke(manager, ns(
            stream="NETCONF", xpath="/x:event", ns=["x=urn:test:event"],
            start="2026-01-01T00:00:00Z",
        ))
        subscription = manager.calls[-1][1]
        self.assertEqual(subscription.tag, "{%s}create-subscription" % operations.NOTIFICATION_NS)
        self.assertEqual(subscription.find("{%s}stream" % operations.NOTIFICATION_NS).text, "NETCONF")
        self.assertIsNotNone(subscription.find("{%s}startTime" % operations.NOTIFICATION_NS))

        operations.GetData().invoke(manager, ns(datastore="operational", depth=3, origin=["or:system"]))
        get_data = manager.calls[-1][1]
        self.assertEqual(get_data.tag, "{%s}get-data" % operations.NMDA_NS)
        self.assertEqual(get_data.find("{%s}datastore" % operations.NMDA_NS).text, "ds:operational")
        self.assertEqual(get_data.find("{%s}max-depth" % operations.NMDA_NS).text, "3")
        self.assertEqual(get_data.find("{%s}origin-filter" % operations.NMDA_NS).text, "or:system")

        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "data.xml"
            filename.write_text("<interfaces xmlns='urn:example'><interface/></interfaces>", encoding="utf-8")
            operations.EditData().invoke(manager, ns(datastore="operational"), str(filename))
        edit_data = manager.calls[-1][1]
        self.assertEqual(edit_data.tag, "{%s}edit-data" % operations.NMDA_NS)
        self.assertEqual(edit_data.find("{%s}datastore" % operations.NMDA_NS).text, "ds:operational")
        self.assertIsNotNone(edit_data.find("{%s}config" % operations.NMDA_NS))

    def test_get_schema_can_write_schema_text(self):
        manager = FakeManager()
        learned = []

        class Context:
            def require_manager(self):
                return manager

            def learn_yang_schema(self, text, module_hint):
                learned.append((text, module_hint))

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "demo.yang"
            result = operations.GetSchema().invoke(Context(), ns(out=str(output)), "demo")
            self.assertIn("Saved schema demo", result.text)
            self.assertIn("module demo", output.read_text(encoding="utf-8"))
        self.assertEqual(learned[0][1], "demo")
        self.assertIn("prefix d", learned[0][0])

        displayed = operations.GetSchema().invoke(Context(), ns(out=None), "demo")
        self.assertIsInstance(displayed, operations.TextResult)
        self.assertIn("namespace 'urn:demo'", displayed.text)


if __name__ == "__main__":
    unittest.main()
