import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lxml import etree
import pytest

from netconf_console.gui import events, raw_rpc, subscription_templates as templates
from netconf_console.gui.model import EditError


def test_fault_alternatives_are_separate_branches_and_keep_other_predicates():
    alarm = templates.bundled_catalog()["notifications"]["alarm-notif"]
    xml = templates.notification_filter(alarm, {("fault-severity",): ["CRITICAL", "MAJOR", "MINOR"],
                                                ("is-cleared",): ["false"]})
    plan = raw_rpc.subscription_rpc(events.subscription_options("fault-management", {}, xml))
    roots = plan.rpc.findall(".//{%s}alarm-notif" % events.FM_NS)
    assert len(roots) == 3
    assert [n.findtext("{%s}fault-severity" % events.FM_NS) for n in roots] == ["CRITICAL", "MAJOR", "MINOR"]
    assert all(n.findtext("{%s}is-cleared" % events.FM_NS) == "false" for n in roots)
    assert plan.stream == "fault-management"


@pytest.mark.parametrize("xml", ["<root/>", "<!DOCTYPE a [<!ENTITY x 'x'>]><a xmlns='urn:a'>&x;</a>",
                                "<a xmlns='urn:a'/>stray", "<filter xmlns='urn:a'><a/></filter>"])
def test_fragment_support_still_rejects_invalid_payloads(xml):
    with pytest.raises((ValueError, etree.XMLSyntaxError)):
        events.subscription_options("NETCONF", {}, xml)


def test_catalog_types_and_measurements_come_from_v17_yang():
    catalog = templates.bundled_catalog()["notifications"]
    alarm = {tuple(f["path"]): f for f in catalog["alarm-notif"]["fields"]}
    assert alarm[("fault-severity",)]["values"] == ["CRITICAL", "MAJOR", "MINOR", "WARNING"]
    assert len(alarm[("alarm-type",)]["values"]) == 10
    assert len(events.MEASUREMENT_GROUPS) == 11
    assert sum(map(len, events.MEASUREMENT_GROUPS.values())) == 59
    assert "TX_POPWER" not in events.MEASUREMENT_GROUPS["transceiver-stats"]
    with pytest.raises(EditError):
        templates.notification_filter(catalog["alarm-notif"], {("fault-id",): ["65536"]})
    with pytest.raises(EditError):
        templates.notification_filter(catalog["alarm-notif"], {("fault-severity",): ["critical"]})


def test_device_extra_measurement_values_are_not_silently_dropped():
    with pytest.raises(EditError):
        events.measurement_filter({"transceiver-stats": ["VENDOR_VALUE"]})
    xml = events.measurement_filter({"transceiver-stats": ["VENDOR_VALUE"]},
                                    {"transceiver-stats": {"VENDOR_VALUE"}})
    assert "VENDOR_VALUE" in xml


def test_template_panels_preserve_selection_and_match_mp_11_3():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from netconf_console.gui.qt_app import QtMainWindow, build_application
    app = build_application()
    window = QtMainWindow(persist=False)
    try:
        panel = window.netconf_template_panel
        panel.preset.setCurrentIndex(1)
        panel.load_preset()
        plan = raw_rpc.subscription_rpc(events.subscription_options("NETCONF", {}, panel.filter_xml))
        roots = plan.rpc.find(".//{%s}filter" % events.NOTIFICATION_NS)
        assert len(roots) == 5  # C/M/M alarms, entire transceiver group, RX_ON_TIME.
        assert roots[-2][0].tag == "{%s}transceiver-stats" % events.PM_NS
        assert roots[-1][0][0].text == "RX_ON_TIME"
        panel.event_tree.setCurrentItem(panel.event_items["measurement-result-stats"])
        panel.event_tree.setCurrentItem(panel.event_items["alarm-notif"])
        assert panel.fields.selections()[("fault-severity",)] == ["CRITICAL", "MAJOR", "MINOR"]
        panel.apply_button.click()
        assert not window.subscription_conditions_group.isChecked()
        assert not window.subscription_preview_group.isChecked()
        assert window.stream_box.currentText() == "NETCONF"
        assert "RX_ON_TIME" in window.subscription_rpc_preview.toPlainText()
        fault = window.fault_template_panel
        fault.preset.setCurrentIndex(2)
        fault.load_preset()
        fault.fields.value_items[("fault-severity",), "MAJOR"].setCheckState(0, Qt.CheckState.Checked)
        assert "CRITICAL" in fault.filter_xml and "MAJOR" in fault.filter_xml
        assert fault.apply_button.isEnabled()
        fault.fields.inputs[("fault-id",)].setCurrentText("99999")
        assert not fault.apply_button.isEnabled()
        for index in range(panel.preset.count()):
            panel.preset.setCurrentIndex(index)
            panel.load_preset()
            assert panel.apply_button.isEnabled(), panel.preview_status.text()
    finally:
        window.close()
        app.processEvents()


def test_dut_schema_change_keeps_invalid_conditions_visible_until_removed():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from netconf_console.gui.qt_app import CodeEditor, build_application
    from netconf_console.gui.schema import SchemaIndex
    from netconf_console.gui.subscription_widgets import NotificationTemplatePanel
    app = build_application()
    panel = NotificationTemplatePanel(CodeEditor, fault_only=True)
    panel.preset.setCurrentIndex(1)
    panel.load_preset()
    schema = SchemaIndex.compile({"o-ran-fm": '''module o-ran-fm {
        yang-version 1.1; namespace "urn:o-ran:fm:1.0"; prefix fm;
        notification alarm-notif { leaf fault-severity { type enumeration { enum CRITICAL; } } }
    }'''})
    panel.update_device_schema(schema.modules)
    assert not panel.apply_button.isEnabled()
    for value in ("MAJOR", "MINOR"):
        panel.fields.value_items[("fault-severity",), value].setCheckState(0, Qt.CheckState.Unchecked)
    assert panel.apply_button.isEnabled()
    assert "CRITICAL" in panel.filter_xml
    panel.close()
    app.processEvents()


def test_epe_multi_report_histogram_and_optional_reporting_interval():
    pytest.importorskip("PySide6")
    from unittest.mock import Mock
    from netconf_console.gui.qt_app import QtMainWindow, build_application
    app = build_application()
    window = QtMainWindow(persist=False)
    window.show_error = Mock()
    try:
        window.epe_reports["MAXIMUM"].setChecked(True)
        window.epe_reports["FREQUENCY_TABLE"].setChecked(True)
        window.epe_notification_enabled.setChecked(True)
        window.epe_notification_interval.setValue(120)
        window._load_epe_config_rpc()
        assert window.show_error.called  # Blank histogram limits cannot be sent.
        window.show_error.reset_mock()
        window.epe_lower_bound.setText("0")
        window.epe_upper_bound.setText("100.1234")
        window._load_epe_config_rpc()
        window.show_error.assert_not_called()
        plan = raw_rpc.prepare(window.rpc_editor.toPlainText())
        assert plan.rpc.xpath("//*[local-name()='report-info']/text()") == ["AVERAGE", "MAXIMUM", "FREQUENCY_TABLE"]
        assert plan.rpc.xpath("string(//*[local-name()='notification-interval'])") == "120"
        assert plan.rpc.xpath("string(//*[local-name()='bin-count'])") == "10"
        window.epe_capabilities = {"POWER": {"report_info": ["AVERAGE"]}}
        window._load_epe_config_rpc()
        assert window.show_error.called
    finally:
        window.close()
        app.processEvents()


def test_search_and_selected_views_keep_hidden_notification_conditions():
    pytest.importorskip("PySide6")
    from netconf_console.gui.qt_app import CodeEditor, build_application
    from netconf_console.gui.subscription_widgets import NotificationTemplatePanel
    app = build_application()
    panel = NotificationTemplatePanel(CodeEditor)
    try:
        panel.preset.setCurrentIndex(1)
        panel.load_preset()
        original = panel.filter_xml
        panel.event_search.setText("download")
        assert panel.event_items["alarm-notif"].isHidden()
        assert not panel.event_items["download-event"].isHidden()
        panel.events_selected_only.setChecked(True)
        assert panel.event_items["download-event"].isHidden()
        assert panel.filter_xml == original
        panel.event_search.clear()
        assert not panel.event_items["alarm-notif"].isHidden()
        panel.event_tree.setCurrentItem(panel.event_items["alarm-notif"])
        panel.field_search.setText("CRITICAL")
        assert not panel.fields.value_items[("fault-severity",), "CRITICAL"].isHidden()
        assert panel.fields.value_items[("fault-severity",), "MAJOR"].isHidden()
        assert panel.filter_xml == original
        panel.field_search.clear()
        panel.fields.inputs[("fault-source",)].setCurrentText("radio-1")
        selected_xml = panel.filter_xml
        panel.fields_selected_only.setChecked(True)
        assert not panel.fields.field_items[("fault-source",)].isHidden()
        assert panel.fields.field_items[("fault-text",)].isHidden()
        panel.field_search.setText("no-such-field")
        assert panel.fields.field_items[("fault-source",)].isHidden()
        assert panel.filter_xml == selected_xml
    finally:
        panel.close()
        app.processEvents()


def test_preset_and_clear_undo_restore_full_scope_and_conditions():
    pytest.importorskip("PySide6")
    from netconf_console.gui.qt_app import CodeEditor, build_application
    from netconf_console.gui.subscription_widgets import NotificationTemplatePanel
    app = build_application()
    panel = NotificationTemplatePanel(CodeEditor)
    try:
        assert not panel.undo_button.isEnabled()
        panel.preset.setCurrentIndex(1)
        panel.load_preset()
        original = panel.filter_xml
        panel.preset.setCurrentIndex(2)
        panel.load_preset()
        assert panel.filter_xml != original
        panel.undo_button.click()
        assert panel.filter_xml == original
        assert not panel.undo_button.isEnabled()
        panel.clear_conditions()
        assert panel.filter_xml != original
        panel.undo_button.click()
        assert panel.filter_xml == original
        assert "完整群組" in panel.summary.toPlainText()
        panel.set_send_available(True, "連線可用")
        assert panel.send_button.isEnabled()
        panel.fields.inputs[("fault-id",)].setCurrentText("99999")
        panel.set_send_available(True, "連線可用")
        assert not panel.send_button.isEnabled()
        assert not panel.copy_button.isEnabled()
    finally:
        panel.close()
        app.processEvents()


def test_measurement_requires_explicit_all_and_preserves_filtered_choices_with_undo():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from netconf_console.gui.qt_app import QtMainWindow, build_application
    app = build_application()
    window = QtMainWindow(persist=False)
    try:
        original = window._measurement_filter_xml()
        assert "POWER" in original
        window.measurement_search.setText("rx-window")
        assert window.measurement_tree_items["epe-statistics", None].isHidden()
        window.measurement_selected_only.setChecked(True)
        assert window._measurement_filter_xml() == original
        assert not window.measurement_send_button.isEnabled()
        window._set_all_measurements(Qt.CheckState.Unchecked)
        assert not window.measurement_apply_button.isEnabled()
        assert not window.measurement_copy_button.isEnabled()
        window.measurement_undo_button.click()
        assert window._measurement_filter_xml() == original
        window._set_all_measurements(Qt.CheckState.Unchecked)
        window.measurement_all_notifications.setChecked(True)
        assert window.measurement_apply_button.isEnabled()
        assert len(etree.fromstring(window._measurement_filter_xml().encode())) == 0
        window.measurement_preset.setCurrentIndex(1)
        window._load_measurement_preset()
        assert window.measurement_whole_groups == {"transceiver-stats"}
        window._set_all_measurements(Qt.CheckState.Unchecked)
        window.measurement_undo_button.click()
        assert window.measurement_whole_groups == {"transceiver-stats"}
        assert "RX_ON_TIME" in window._measurement_filter_xml()
        window.measurement_search.clear()
        assert not window.measurement_tree_items["transceiver-stats", None].isHidden()
    finally:
        window.close()
        app.processEvents()
