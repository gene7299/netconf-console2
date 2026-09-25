import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lxml import etree
from ncclient.xml_ import to_xml

from netconf_console.gui import events, raw_rpc
from netconf_console.gui.model import EditError, NC

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QThread, Qt
    from netconf_console.gui.demo import DemoClient
    from netconf_console.gui.qt_app import QtMainWindow, build_application
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


class RawRpcTests(unittest.TestCase):
    def test_operation_gets_envelope_and_unique_ids(self):
        first = raw_rpc.prepare(raw_rpc.TEMPLATES["get"])
        second = raw_rpc.prepare(raw_rpc.TEMPLATES["get"])
        self.assertEqual(first.rpc.tag, "{%s}rpc" % NC)
        self.assertNotEqual(first.rpc.get("message-id"), second.rpc.get("message-id"))
        self.assertFalse(first.may_change_data)

    def test_full_envelope_preserves_id_attributes_and_edit_operations(self):
        source = ('<rpc xmlns="%s" xmlns:nc="%s" message-id="user-7" vendor="x">'
                  '<edit-config><target><running/></target><config><root xmlns="urn:test">'
                  '<item nc:operation="delete"/></root></config></edit-config></rpc>') % (NC, NC)
        prepared = raw_rpc.prepare(source)
        self.assertEqual(prepared.rpc.get("message-id"), "user-7")
        self.assertEqual(prepared.rpc.get("vendor"), "x")
        self.assertEqual(prepared.rpc.xpath('string(//@nc:operation)', namespaces={"nc": NC}), "delete")
        self.assertTrue(prepared.may_change_data)
        manager = Mock()
        manager.xrpc.return_value.xml = "<reply/>"
        client = SimpleNamespace(connected=True, manager=manager, pending_commit=None)
        self.assertEqual(raw_rpc.execute(client, manager, prepared), "<reply/>")
        wire = manager.xrpc.call_args.args[0]
        self.assertEqual(to_xml(wire), prepared.wire_xml)
        self.assertIsNot(wire, prepared.rpc)

    def test_rejects_protocol_messages_ambiguous_envelopes_and_entities(self):
        cases = ["<get/>", '<rpc xmlns="urn:wrong"><get/></rpc>',
                 '<rpc xmlns="%s"><get/><get/></rpc>' % NC,
                 '<rpc xmlns="%s"/>' % NC,
                 '<rpc xmlns="%s" message-id=" "><get/></rpc>' % NC,
                 '<rpc xmlns="%s">text<get/></rpc>' % NC,
                 '<rpc xmlns="%s"><get/>text</rpc>' % NC,
                 '<notification xmlns="%s"/>' % events.NOTIFICATION_NS,
                 '<rpc xmlns="%s"><rpc-reply/></rpc>' % NC,
                 '<!DOCTYPE get [<!ENTITY x "secret">]><get xmlns="%s">&x;</get>' % NC]
        for source in cases:
            with self.subTest(source=source), self.assertRaises(EditError):
                raw_rpc.prepare(source)

    def test_bound_and_tampered_or_changed_session_prevents_send(self):
        with self.assertRaises(EditError):
            raw_rpc.prepare(" " * (raw_rpc.MAX_XML_BYTES + 1))
        prepared = raw_rpc.prepare(raw_rpc.TEMPLATES["get"])
        manager = Mock()
        client = SimpleNamespace(connected=True, manager=object(), pending_commit=None)
        with self.assertRaises(EditError):
            raw_rpc.execute(client, manager, prepared)
        client.manager = manager
        prepared.rpc.set("message-id", "tampered")
        with self.assertRaises(EditError):
            raw_rpc.execute(client, manager, prepared)
        manager.xrpc.assert_not_called()

    def test_subscription_builder_preserves_filter_and_replay(self):
        options = events.subscription_options("NETCONF",
            {"NETCONF": events.StreamInfo("NETCONF", "", True, "")},
            '<alarm-notif xmlns="urn:o-ran:fm:1.0"><fault-severity>MAJOR</fault-severity></alarm-notif>',
            "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")
        prepared = raw_rpc.subscription_rpc(options)
        self.assertTrue(prepared.subscription)
        self.assertEqual(prepared.stream, "NETCONF")
        ns = "{%s}" % events.NOTIFICATION_NS
        op = prepared.rpc[0]
        self.assertEqual(op.findtext(ns + "startTime"), options["start_time"])
        self.assertEqual(op.findtext(ns + "stopTime"), options["stop_time"])
        self.assertEqual(op.find(ns + "filter")[0].tag, "{urn:o-ran:fm:1.0}alarm-notif")

    def test_stream_resolution_uses_dut_supported_alternative(self):
        actual, note = events.resolve_stream("measurement-result-stats", {
            "NETCONF": events.StreamInfo("NETCONF", "", True, ""),
            "o-ran-performance-management": events.StreamInfo("o-ran-performance-management", "", False, ""),
        })
        self.assertEqual(actual, "NETCONF")
        self.assertIn("measurement-result-stats", note)
        self.assertEqual(events.resolve_stream("custom", {"NETCONF": object()}), ("custom", ""))

    def test_all_rpc_templates_are_well_formed(self):
        for name, xml in raw_rpc.TEMPLATES.items():
            with self.subTest(template=name):
                raw_rpc.prepare(xml)


class FakeSubscriptionClient:
    def __init__(self, manager, capabilities):
        self.manager = manager
        self.capabilities = capabilities
        self.connected = False
        self.context = None
        self.pending_commit = None

    def connect(self, settings, _progress=lambda _message: None):
        self.connected = True
        self.context = SimpleNamespace(settings=settings)

    def disconnect(self):
        self.connected = False


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class RpcWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = build_application()

    def setUp(self):
        self.manager = Mock()
        self.manager.session_id = "100"
        self.manager.xrpc.return_value.xml = '<rpc-reply xmlns="%s"><ok/></rpc-reply>' % NC
        self.manager.take_notification.return_value = None
        client = DemoClient()
        client.manager = self.manager
        client.pending_commit = None
        client.capabilities = ["urn:ietf:params:netconf:capability:notification:1.0"]
        self.window = QtMainWindow(client=client, persist=False, language="zh-TW")
        self.window.client.context = SimpleNamespace(settings=SimpleNamespace(host="test-device", port=830))
        self.window._run = self.synchronous
        self.window.show_error = Mock()
        self.subscribers = []
        self.available_streams = {"NETCONF": events.StreamInfo("NETCONF", "", True, "")}

    def synchronous(self, _label, work, done=None, failed=None):
        try:
            value = work(lambda _text: None)
        except Exception as exc:
            if failed:
                failed(exc)
            else:
                raise
        else:
            if done:
                done(value)

    def make_subscriber(self, capabilities=None):
        manager = Mock()
        manager.session_id = str(100 + len(self.subscribers) + 1)
        manager.xrpc.return_value.xml = '<rpc-reply xmlns="%s"><ok/></rpc-reply>' % NC
        manager.take_notification.return_value = None
        caps = ["urn:ietf:params:netconf:capability:notification:1.0"]
        if capabilities:
            caps.extend(capabilities)
        subscriber = FakeSubscriptionClient(manager, caps)
        self.subscribers.append(subscriber)
        return subscriber

    def subscribe(self, stream="NETCONF", capabilities=None):
        self.window.stream_box.setCurrentText(stream)
        subscriber = self.make_subscriber(capabilities)
        with patch("netconf_console.gui.qt_app.GuiClient", return_value=subscriber), \
             patch("netconf_console.gui.qt_app.events.discover_streams", return_value=self.available_streams):
            self.window.subscribe()
        return subscriber

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_subscription_uses_a_new_session_and_does_not_block_main_rpc(self):
        subscriber = self.subscribe()
        self.assertTrue(subscriber.connected)
        self.assertIs(self.window.notification_manager, subscriber.manager)
        self.assertEqual(self.window.session_records[-1].status, "已訂閱")
        self.assertEqual(self.window.session_records[-1].server_id, "101")
        self.assertTrue(self.window.notification_timer.isActive())
        self.assertFalse(hasattr(self.window, "stop_subscription_button"))
        self.assertIn("<ok/>", self.window.subscription_reply.toPlainText())

        self.window.rpc_editor.setPlainText(raw_rpc.TEMPLATES["get"])
        self.window.send_rpc()
        self.assertEqual(self.manager.xrpc.call_count, 1)
        self.assertIs(self.window.rpc_output_tabs.currentWidget(), self.window.rpc_reply)
        self.window.show_error.assert_not_called()

    def test_template_direct_send_opens_dedicated_live_session_without_stale_replay(self):
        for stream, panel in (("fault-management", self.window.fault_template_panel),
                              ("NETCONF", self.window.netconf_template_panel),
                              ("measurement-result-stats", None)):
            self.window.event_start_edit.setText("2026-09-20T00:00:00Z")
            self.window.event_stop_edit.setText("2026-09-20T01:00:00Z")
            self.window._sync_rpc_controls()
            subscriber = self.make_subscriber()
            with patch("netconf_console.gui.qt_app.GuiClient", return_value=subscriber), \
                 patch("netconf_console.gui.qt_app.events.discover_streams", return_value=self.available_streams):
                button = panel.send_button if panel else self.window.measurement_send_button
                self.assertTrue(button.isEnabled(), stream)
                button.click()
            subscriber.manager.xrpc.assert_called_once()
            rpc = subscriber.manager.xrpc.call_args.args[0]
            self.assertFalse(rpc.xpath("//*[local-name()='startTime' or local-name()='stopTime']"))
            self.assertNotEqual(rpc.get("message-id"), "preview")
            self.assertIs(self.window.workspace_tabs.currentWidget(), self.window.subscription_page)
            self.assertFalse(self.window.subscription_conditions_group.isChecked())
            self.assertEqual(self.window.event_start_edit.text(), "")
            self.window.show_error.assert_not_called()
        self.assertEqual(len([r for r in self.window.session_records if r.status == "已訂閱"]), 3)
        self.manager.xrpc.assert_not_called()

    def test_session_and_measurement_tools_live_on_separate_tabs(self):
        self.assertTrue(self.window.session_page.isAncestorOf(self.window.session_table))
        self.assertFalse(self.window.subscription_page.isAncestorOf(self.window.session_table))
        self.assertTrue(self.window.measurement_page.isAncestorOf(self.window.measurement_tree))
        self.assertTrue(self.window.measurement_page.isAncestorOf(self.window.epe_template))
        self.assertFalse(self.window.subscription_page.isAncestorOf(self.window.measurement_tree))

    def test_subscription_rpc_preview_updates_and_action_order_matches_ui(self):
        self.window.stream_box.setCurrentText("measurement-result-stats")
        initial = self.window.subscription_rpc_preview.toPlainText()
        self.assertIn("measurement-result-stats", initial)
        self.window.event_filter_editor.setPlainText(
            '<measurement-result-stats xmlns="urn:o-ran:performance-management:1.0">'
            '<epe-statistics><measurement-object>POWER</measurement-object></epe-statistics>'
            '</measurement-result-stats>')
        updated = self.window.subscription_rpc_preview.toPlainText()
        self.assertNotEqual(initial, updated)
        self.assertIn("POWER", updated)
        self.assertEqual(self.window.subscribe_button.text(), "送出Create Subscription")
        self.assertLess(self.window.subscription_action_bar.indexOf(self.window.subscribe_button),
                        self.window.subscription_action_bar.indexOf(self.window.auto_supervision_reset))
        action_index = next(i for i in range(self.window.subscription_layout.count())
                            if self.window.subscription_layout.itemAt(i).layout()
                            is self.window.subscription_action_bar)
        reply_index = self.window.subscription_layout.indexOf(self.window.subscription_reply_group)
        self.assertLess(action_index, reply_index)

    def test_each_subscription_gets_an_independent_session_and_can_close_separately(self):
        first = self.make_subscriber()
        second = self.make_subscriber()
        with patch("netconf_console.gui.qt_app.GuiClient", side_effect=[first, second]), \
             patch("netconf_console.gui.qt_app.events.discover_streams", return_value=self.available_streams):
            self.window.subscribe()
            self.window.subscribe()
        self.assertEqual(len([r for r in self.window.session_records if r.status == "已訂閱"]), 2)
        target = self.window.session_records[0]
        self.window.disconnect_managed_session(target.session_id)
        self.assertFalse(first.connected)
        self.assertTrue(second.connected)
        self.assertEqual(target.status, "已中斷")
        self.assertTrue(self.window.client.connected)

    def test_unavailable_measurement_stream_falls_back_to_netconf_with_filter(self):
        self.available_streams["o-ran-performance-management"] = events.StreamInfo(
            "o-ran-performance-management", "", False, "")
        subscriber = self.subscribe("measurement-result-stats")
        sent = subscriber.manager.xrpc.call_args.args[0]
        self.assertEqual(sent[0].findtext("{%s}stream" % events.NOTIFICATION_NS), "NETCONF")
        self.assertIn("measurement-result-stats", self.window.rpc_preview.toPlainText())
        self.assertIn("<filter type=\"subtree\">", self.window.rpc_preview.toPlainText())
        self.assertIn("已改用 NETCONF", self.window.notification_status.text())

    def test_supervision_auto_reset_uses_its_subscription_session(self):
        self.window.auto_supervision_reset.setChecked(True)
        subscriber = self.subscribe("supervision-notification", [
            "urn:ietf:params:netconf:capability:interleave:1.0"])
        payload = ('<notification xmlns="%s"><supervision-notification '
                   'xmlns="urn:o-ran:supervision:1.0"/></notification>') % events.NOTIFICATION_NS
        subscriber.manager.take_notification.side_effect = [
            SimpleNamespace(notification_xml=payload), None]
        self.window._poll_notifications()
        self.assertEqual(subscriber.manager.xrpc.call_count, 2)
        request = subscriber.manager.xrpc.call_args.args[0]
        self.assertEqual(request[0].tag, "{urn:o-ran:supervision:1.0}supervision-watchdog-reset")
        self.assertIn("watchdog reset", self.window.notification_status.text())
        self.assertFalse(self.window.session_records[-1].watchdog_pending)

    def test_preview_updates_during_edit_and_reply_tab_is_selected_after_rpc(self):
        self.window.rpc_editor.setPlainText(raw_rpc.TEMPLATES["get"])
        first_preview = self.window.rpc_preview.toPlainText()
        self.window.rpc_editor.setPlainText(raw_rpc.TEMPLATES["get-config (running)"])
        self.assertNotEqual(self.window.rpc_preview.toPlainText(), first_preview)
        self.assertIn("get-config", self.window.rpc_preview.toPlainText())
        self.assertIsNotNone(self.window.raw_rpc_plan)
        self.window.send_rpc()
        self.assertIs(self.window.rpc_output_tabs.currentWidget(), self.window.rpc_reply)
        self.assertIn("<ok/>", self.window.rpc_reply.toPlainText())

    def test_epe_template_generates_running_edit_config_rpc(self):
        self.window._load_epe_config_rpc()
        prepared = raw_rpc.prepare(self.window.rpc_editor.toPlainText())
        self.assertEqual(prepared.operation, "{%s}edit-config" % NC)
        self.assertEqual(prepared.rpc.xpath("string(//*[local-name()='epe-measurement-interval'])"), "60")
        self.assertEqual(prepared.rpc.xpath("string(//*[local-name()='measurement-object'])"), "POWER")
        self.assertEqual(prepared.rpc.xpath("string(//*[local-name()='active'])"), "true")
        self.assertEqual(prepared.rpc.xpath("string(//*[local-name()='object-unit'])"), "or-hw:O-RAN-RADIO")
        self.assertIs(self.window.workspace_tabs.currentWidget(), self.window.rpc_page)

    def test_measurement_filter_uses_o_ran_namespace_and_selected_object(self):
        self.window.measurement_tree_items[("epe-statistics", "POWER")].setCheckState(
            0, Qt.CheckState.Checked)
        self.window.apply_measurement_template()
        root = raw_rpc.parse_xml(self.window.event_filter_editor.toPlainText())
        self.assertEqual(root.tag, "{urn:o-ran:performance-management:1.0}measurement-result-stats")
        self.assertEqual(root[0].tag, "{urn:o-ran:performance-management:1.0}epe-statistics")

    def test_notification_complete_closes_only_that_subscription_session(self):
        first = self.make_subscriber()
        second = self.make_subscriber()
        with patch("netconf_console.gui.qt_app.GuiClient", side_effect=[first, second]), \
             patch("netconf_console.gui.qt_app.events.discover_streams", return_value=self.available_streams):
            self.window.subscribe()
            self.window.subscribe()
        completed = '<notification xmlns="%s"><notificationComplete/></notification>' % events.NOTIFICATION_NS
        first.manager.take_notification.side_effect = [SimpleNamespace(notification_xml=completed)]
        self.window._poll_notifications()
        self.assertFalse(first.connected)
        self.assertTrue(second.connected)
        self.assertEqual(self.window.session_records[0].status, "已結束")

    def test_worker_sends_once_and_returns_to_gui_thread(self):
        self.window._run = QtMainWindow._run.__get__(self.window)
        self.window.rpc_editor.setPlainText(raw_rpc.TEMPLATES["get"])
        preview = self.window.preview_rpc().wire_xml
        reply_threads = []
        original = self.window.rpc_reply.setPlainText
        def record(text):
            reply_threads.append(QThread.currentThread())
            original(text)
        with patch.object(self.window.rpc_reply, "setPlainText", side_effect=record):
            self.window.send_rpc()
            deadline = time.monotonic() + 5
            while self.window._task_thread is not None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.005)
        self.assertIsNone(self.window._task_thread)
        self.manager.xrpc.assert_called_once()
        self.assertEqual(to_xml(self.manager.xrpc.call_args.args[0]), preview)
        self.assertEqual(reply_threads, [self.app.thread()])
        self.assertIn("<ok/>", self.window.rpc_reply.toPlainText())


if __name__ == "__main__":
    unittest.main()
