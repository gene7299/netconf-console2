"""Offline smoke tests for the PySide6 GUI layer."""

import os
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from lxml import etree
    from PySide6.QtCore import Qt, QThread
    from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QMessageBox, QTreeWidgetItem
    from netconf_console.gui.creation import Candidate
    from netconf_console.gui.demo import DemoClient
    from netconf_console.gui.qt_app import CreationDialog, QtMainWindow, build_application
    from netconf_console.gui.schema import SchemaIndex
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class QtWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = build_application()

    def setUp(self):
        self.window = QtMainWindow(client=DemoClient(), persist=False, language="zh-TW")
        self.window.load_demo()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_relayout_defaults_and_demo_tree(self):
        self.assertEqual(self.window.source_box.currentText(), "running")
        self.assertFalse(self.window.checks["hostkey_verify"].isChecked())
        self.assertFalse(self.window.checks["verify_hostname"].isChecked())
        self.assertEqual(self.window.mode_box.count(), 4)
        self.assertEqual(self.window.ssh_auth.currentText(), "auto")
        self.assertIn("key_passphrase", self.window.fields)
        self.assertGreaterEqual(self.window.tree.topLevelItemCount(), 2)
        self.assertIsNotNone(self.window.centralWidget().findChild(type(self.window.tree)))
        # The offscreen Qt test display is narrower than a normal desktop, so
        # the window is clamped between its usable minimum and 1280 x 800.
        self.assertGreaterEqual(self.window.width(), self.window.minimumWidth())
        self.assertLessEqual(self.window.width(), 1280)
        self.assertGreaterEqual(self.window.height(), self.window.minimumHeight())
        self.assertLessEqual(self.window.height(), 800)

    def test_sysrepo_bottom_tabs_and_readonly_preview_preserve_draft(self):
        window = self.window
        self.assertEqual(window.data_tree_tabs.tabPosition(), window.data_tree_tabs.TabPosition.South)
        self.assertEqual([window.data_tree_tabs.tabText(i) for i in range(2)], ["NETCONF", "sysrepocfg"])
        original = window.editor.toPlainText().replace("Fronthaul", "kept-draft")
        window.editor.setPlainText(original)
        old_snapshot, old_selection = window.snapshot, window.selection
        panel = window.sysrepo_tree
        xml_panel = window.xml_panel
        repo = etree.fromstring(b'<data><private xmlns="urn:private"><value>42</value></private></data>')
        panel.load(repo, window.snapshot.data, window.client.schema)
        window.data_tree_tabs.setCurrentIndex(1)
        self.assertIs(window.xml_panel, xml_panel)
        self.assertIs(window.data_tree_tabs.currentWidget(), panel)
        item = panel.tree.topLevelItem(0)
        self.assertEqual(item.foreground(0).color().name(), "#b42332")
        panel.expand(item)
        self.assertEqual(item.child(0).foreground(0).color().name(), "#b42332")
        window.tree_search.setText("42")
        self.assertEqual(panel.tree.topLevelItemCount(), 1)
        self.assertEqual(panel.tree.topLevelItem(0).childCount(), 1)
        self.assertIs(window.xml_panel, xml_panel)
        self.assertEqual(window.editor.toPlainText(), "")
        self.assertTrue(window.editor.isReadOnly())
        selected = panel.tree.topLevelItem(0).child(0).data(0, Qt.ItemDataRole.UserRole)
        panel.tree.setCurrentItem(panel.tree.topLevelItem(0).child(0))
        window._show_sysrepo_selection(selected)
        self.assertIn("private", window.editor.toPlainText())
        window.data_tree_tabs.setCurrentIndex(0)
        self.assertEqual(window.editor.toPlainText(), original)
        self.assertIs(window.snapshot, old_snapshot)
        self.assertIs(window.selection, old_selection)

    def _sysrepo_connection_fixture(self):
        from netconf_console.session import ConnectionSettings
        window = self.window
        settings = ConnectionSettings(host="192.0.2.10", username="admin")
        window.admin_settings = settings
        window.admin_connection = Mock(connected=True)
        window.client.context = SimpleNamespace(settings=settings,
                                                metadata=SimpleNamespace(remote_host=settings.host))
        def synchronous(_label, work, done, failed=None):
            try:
                result = work(lambda _message: None)
            except Exception as exc:
                if failed:
                    failed(exc)
                else:
                    raise
            else:
                done(result)
        window._run = synchronous
        return window

    def test_sysrepo_refresh_uses_matching_netconf_read_without_overwriting_editor(self):
        from netconf_console.gui.client import ReadOptions
        window = self._sysrepo_connection_fixture()
        original = window.editor.toPlainText()
        old_snapshot = window.snapshot
        window.checks["defaults"].blockSignals(True)
        window.checks["defaults"].setChecked(False)
        window.checks["defaults"].blockSignals(False)
        with patch("netconf_console.gui.sysrepo.export_tree", return_value=(old_snapshot.data, "")), \
                patch.object(window.client, "read", wraps=window.client.read) as read:
            window.sysrepo_source.setCurrentText("operational")
            window.read_sysrepo_tree()
            read.assert_called_once_with(ReadOptions("running", False, True))
        self.assertIs(window.snapshot, old_snapshot)
        self.assertEqual(window.editor.toPlainText(), original)
        self.assertIn("operational", window.status_label.text())

    def test_sysrepo_failed_netconf_read_remains_unknown(self):
        window = self._sysrepo_connection_fixture()
        with patch("netconf_console.gui.sysrepo.export_tree", return_value=(window.snapshot.data, "")), \
                patch.object(window.client, "read", side_effect=RuntimeError("denied")):
            window.read_sysrepo_tree()
        self.assertEqual(set(window.sysrepo_tree.states.values()), {"unknown"})
        self.assertIn("未比較", window.status_label.text())

    def test_sysrepo_different_device_skips_comparison_and_disconnect_clears(self):
        window = self._sysrepo_connection_fixture()
        window.client.context.metadata.remote_host = "192.0.2.11"
        with patch("netconf_console.gui.sysrepo.export_tree", return_value=(window.snapshot.data, "")), \
                patch.object(window.client, "read") as read:
            window.read_sysrepo_tree()
            read.assert_not_called()
        self.assertEqual(set(window.sysrepo_tree.states.values()), {"unknown"})
        window._clear_admin()
        self.assertIsNone(window.sysrepo_tree.data)

    def test_sysrepo_ssh_failure_keeps_previous_snapshot_and_reports_error(self):
        window = self._sysrepo_connection_fixture()
        previous = window.snapshot.data
        window.sysrepo_tree.load(previous, None, window.client.schema)
        with patch("netconf_console.gui.sysrepo.export_tree", side_effect=TimeoutError()), \
                patch.object(window.client, "read") as read, \
                patch.object(window, "show_error") as error:
            window.read_sysrepo_tree()
            read.assert_not_called()
            error.assert_called_once()
        self.assertIs(window.sysrepo_tree.data, previous)
        self.assertIn("保留現有 DATA TREE", window.status_label.text())

    def test_sysrepo_jump_mismatch_and_unconnected_channels(self):
        window = self._sysrepo_connection_fixture()
        window.admin_settings = window.admin_settings.copy(jump_enabled=True, jump_host="192.0.2.20")
        self.assertIn("跳板路徑不同", window._sysrepo_comparison_reason())
        window.admin_connection = None
        with patch("netconf_console.gui.sysrepo.export_tree") as export:
            window.read_sysrepo_tree()
            export.assert_not_called()

    def test_sysrepo_worker_returns_to_gui_thread(self):
        window = self._sysrepo_connection_fixture()
        window._run = QtMainWindow._run.__get__(window)
        with patch("netconf_console.gui.sysrepo.export_tree", return_value=(window.snapshot.data, "")):
            window.read_sysrepo_tree()
            self.assertTrue(window.busy)
            deadline = time.monotonic() + 5
            while (window.busy or window._task_thread is not None) and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertFalse(window.busy)
            self.assertIsNone(window._task_thread)
            self.assertIsNotNone(window.sysrepo_tree.data)
            self.assertTrue(window.tree_export_button.isEnabled())

    def test_inline_tree_search_finds_collapsed_nodes_and_restores_tree(self):
        original_roots = self.window.tree.topLevelItemCount()
        self.window.tree_search.setText("port-number")
        self.app.processEvents()
        labels = []

        def collect(item):
            labels.append(item.text(0))
            for index in range(item.childCount()):
                collect(item.child(index))

        for index in range(self.window.tree.topLevelItemCount()):
            collect(self.window.tree.topLevelItem(index))
        self.assertTrue(any("port-number" in label for label in labels), labels)
        self.assertEqual(self.window.tree_search_count.text(), "1 筆")

        self.window.tree_search.setText("definitely-not-a-yang-node")
        self.app.processEvents()
        self.assertIn("找不到符合", self.window.tree.topLevelItem(0).text(0))
        self.assertEqual(self.window.tree_search_count.text(), "0 筆")

        self.window.tree_search.clear()
        self.app.processEvents()
        self.assertEqual(self.window.tree.topLevelItemCount(), original_roots)
        self.assertEqual(self.window.tree_search_count.text(), "")

    def test_gui_language_switch_translates_controls_and_menu(self):
        self.window.set_gui_language("en")
        self.app.processEvents()
        self.assertEqual(self.window.language, "en")
        self.assertEqual(self.window.tabs.tabText(0), "NETCONF Connection")
        self.assertEqual(self.window.connect_button.text(), "Connected")
        self.assertEqual(self.window.tree_search.placeholderText(), "Node name, value, or path")
        self.assertEqual(self.window.menuBar().actions()[0].text(), "Configuration")

        self.window.set_gui_language("zh-CN")
        self.app.processEvents()
        self.assertEqual(self.window.tabs.tabText(0), "NETCONF连接")
        self.assertEqual(self.window.connect_button.text(), "已连接")
        self.assertEqual(self.window.tree_search.placeholderText(), "节点名称、值或路径")

    def test_edit_updates_exact_preview(self):
        self.window.editor.setPlainText(
            self.window.editor.toPlainText().replace("O-RU management / 管理介面", "changed")
        )
        self.window.update_preview()
        self.assertIsNotNone(self.window.plan)
        self.assertIsNotNone(self.window.plan.rpc)
        self.assertIn("CHANGE interfaces/interface/description", self.window.plan.changes)
        self.assertIn("edit-config", self.window.preview.toPlainText())
        self.assertTrue(self.window.netconf_button.isEnabled())

    def test_ssh_auth_fields_roundtrip_through_values(self):
        self.window.ssh_auth.setCurrentText("private-key")
        self.window.fields["key_passphrase"].setText("secret")
        values = self.window._values()
        self.assertEqual(values["ssh_auth"], "private-key")
        self.assertEqual(values["key_passphrase"], "secret")
        self.window.ssh_auth.setCurrentText("auto")
        self.window.fields["key_passphrase"].clear()
        self.window._apply_values(values)
        self.assertEqual(self.window.ssh_auth.currentText(), "private-key")
        self.assertEqual(self.window.fields["key_passphrase"].text(), "secret")

    def test_choice_picker_lists_concrete_transport_branches(self):
        source = {
            "choice-module": '''module choice-module {
                yang-version 1.1; namespace "urn:choice"; prefix c;
                container root {
                  choice transport {
                    case ssh { container ssh { leaf host { type string; } } }
                    case tls { container tls { leaf host { type string; } } }
                  }
                }
            }''',
            "unused-module": '''module unused-module {
                yang-version 1.1; namespace "urn:unused"; prefix u;
                leaf not-in-template { type string; }
            }'''
        }
        schema = SchemaIndex.compile(source)
        data = etree.fromstring(b'<root xmlns="urn:choice"/>')
        dialog = CreationDialog(schema, data, ("{urn:choice}root",), self.window)
        try:
            labels = [dialog.list.item(i).text() for i in range(dialog.list.count())]
            self.assertTrue(any("choice transport" in label and "ssh" in label for label in labels), labels)
            self.assertTrue(any("choice transport" in label and "tls" in label for label in labels), labels)
            self.assertNotIn("urn:unused", dialog.editor.toPlainText())
        finally:
            dialog.close()

    def test_creation_dialog_can_add_a_missing_mandatory_choice_branch(self):
        schema = SchemaIndex.compile({
            "choice-module": '''module choice-module {
                yang-version 1.1; namespace "urn:choice-required"; prefix c;
                container root {
                  list endpoint {
                    key name;
                    leaf name { type string; }
                    choice transport { mandatory true;
                      case ssh { container ssh { leaf host { type string; mandatory true; } } }
                      case tls { container tls { leaf host { type string; mandatory true; } } }
                    }
                  }
                }
            }'''
        })
        parent = etree.fromstring(b'<root xmlns="urn:choice-required"/>')
        dialog = CreationDialog(schema, parent, ("{urn:choice-required}root",), self.window)
        try:
            self.assertTrue(dialog.choice_button.isEnabled())
            self.assertTrue(any("transport" in dialog.choice_box.itemText(i)
                                for i in range(dialog.choice_box.count())))
            self.window.set_gui_language("en")
            self.app.processEvents()
            self.assertEqual(
                dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).text(),
                "Add to XML draft",
            )
            dialog._add_choice()
            xml = dialog.editor.toPlainText()
            self.assertIn("<choice-module:ssh", xml)
            self.assertNotIn("<transport", xml)
        finally:
            dialog.close()
            self.window.set_gui_language("zh-TW")

    def test_creation_picker_hides_unavailable_nodes_until_requested(self):
        schema = SchemaIndex.compile({
            "candidate-module": '''module candidate-module {
                yang-version 1.1; namespace "urn:candidate"; prefix c;
                container root {
                  leaf present { type string; }
                  leaf readonly { config false; type string; }
                  leaf available { type string; }
                }
            }'''
        })
        parent = etree.fromstring(
            b'<root xmlns="urn:candidate"><present>already here</present></root>'
        )
        dialog = CreationDialog(schema, parent, ("{urn:candidate}root",), self.window)
        try:
            self.assertFalse(dialog.show_unavailable.isChecked())
            visible = [dialog.list.item(i).text() for i in range(dialog.list.count())]
            self.assertTrue(any("available" in label for label in visible), visible)
            self.assertFalse(any("present" in label or "readonly" in label for label in visible), visible)

            dialog.show_unavailable.setChecked(True)
            self.app.processEvents()
            all_items = [dialog.list.item(i).text() for i in range(dialog.list.count())]
            self.assertTrue(any("已存在" in label for label in all_items), all_items)
            self.assertTrue(any("config false" in label for label in all_items), all_items)
        finally:
            dialog.close()

    def test_save_as_immediately_persists_complete_snapshot_in_memory(self):
        self.window.client.connected = False
        self.window.fields["host"].setText("192.0.2.10")
        self.window.fields["jump_host"].setText("192.0.2.20")
        self.window.fields["admin_host"].setText("192.0.2.30")
        with patch("netconf_console.gui.qt_app.QInputDialog.getText", return_value=("lab", True)):
            self.window.new_connection()
        saved = self.window.preferences["connections"]["lab"]
        self.assertEqual(saved["host"], "192.0.2.10")
        self.assertEqual(saved["jump_host"], "192.0.2.20")
        self.assertEqual(saved["admin_host"], "192.0.2.30")
        self.assertEqual(self.window.profile_box.currentText(), "lab")

    def test_worker_completion_callback_runs_on_qt_main_thread(self):
        observed = []
        self.window._run("thread-test", lambda _progress: "ok",
                         lambda value: observed.append((value, QThread.currentThread() is self.app.thread())))
        deadline = time.monotonic() + 3
        while self.window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertEqual(observed, [("ok", True)])

    def test_worker_callback_can_start_a_followup_without_quitting_its_thread(self):
        observed = []

        def first_done(value):
            observed.append(value)
            self.window._run("second", lambda _progress: "second",
                             lambda result: observed.append(result))

        self.window._run("first", lambda _progress: "first", first_done)
        deadline = time.monotonic() + 3
        while (self.window.busy or len(observed) < 2) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertEqual(observed, ["first", "second"])

    def test_root_delete_stages_one_atomic_remove(self):
        root_item = self.window.tree.topLevelItem(0)
        self.window.tree.setCurrentItem(root_item)
        self.app.processEvents()
        with patch(
                "netconf_console.gui.qt_app.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes):
            self.window.delete_selected()
        self.assertTrue(self.window.selection.delete)
        self.assertIsNotNone(self.window.plan.rpc)
        self.assertEqual(self.window.plan.removals, 1)
        self.assertIn('nc:operation="remove"', self.window.preview.toPlainText())

    def test_qt_layout_has_explicit_tabs_footer_and_native_tree_indentation(self):
        self.assertEqual(
            [self.window.tabs.tabText(i) for i in range(self.window.tabs.count())],
            ["NETCONF連線", "SSH 認證", "TLS 憑證", "進階",
             "SSH 認證方式 / 私鑰密碼", "SSH 跳板", "系統SSH", "備份 / 還原"],
        )
        self.assertEqual(self.window.output_tabs.count(), 5)
        self.assertEqual([self.window.workspace_tabs.tabText(i)
                          for i in range(self.window.workspace_tabs.count())],
                         ["資料/XML", "RPC", "Subscription", "Notification",
                          "Measurement範本", "Fault Management範本", "NETCONF Stream訂閱範本", "Session(s)管理"])
        self.assertEqual(self.window.source_box.currentText(), "running")
        self.assertFalse(self.window.checks["show_candidates"].isChecked())
        self.assertEqual(self.window.tree.indentation(), 18)
        self.assertEqual(self.window.tree_search.placeholderText(), "節點名稱、值或路徑")
        self.assertGreaterEqual(self.window.tabs.minimumHeight(), 130)
        self.assertLessEqual(self.window.tabs.maximumHeight(), 175)
        self.assertIn(self.window.schema_status, self.window.status_bar.findChildren(type(self.window.schema_status)))

    def test_compact_connection_admin_and_tree_layouts_do_not_overlap(self):
        connection = self.window.connection_grid
        self.assertEqual(connection.getItemPosition(connection.indexOf(self.window.connect_button)),
                         (0, 9, 1, 1))
        self.assertEqual(connection.getItemPosition(connection.indexOf(self.window.disconnect_button)),
                         (1, 9, 1, 1))
        self.assertGreaterEqual(
            self.window.connection_profile_layout.indexOf(self.window.checks["auto_reconnect"]), 0)
        self.assertGreaterEqual(
            self.window.connection_profile_layout.indexOf(self.window.stop_reconnect_button), 0)

        admin = self.window.admin_grid
        self.assertEqual(admin.getItemPosition(admin.indexOf(self.window.admin_connect_button)),
                         (0, 12, 1, 1))
        self.assertEqual(admin.getItemPosition(admin.indexOf(self.window.sysrepo_read_button)),
                         (1, 12, 1, 1))
        self.assertEqual(admin.getItemPosition(admin.indexOf(self.window.admin_disconnect_button)),
                         (2, 12, 1, 1))
        self.assertEqual(admin.getItemPosition(admin.indexOf(self.window.sysrepo_controls)),
                         (0, 10, 1, 2))
        self.assertIs(self.window.sysrepo_source.parentWidget(), self.window.sysrepo_controls)
        self.assertEqual(self.window.sysrepo_read_button.text(), "Sysrepocfg讀取")
        self.assertEqual(self.window.sysrepo_read_button.objectName(), "sysrepoReadButton")
        self.assertIn("#sysrepoReadButton", self.app.styleSheet())

        def last_occupied_row(grid):
            return max(grid.getItemPosition(index)[0] + grid.getItemPosition(index)[2] - 1
                       for index in range(grid.count()))

        expected_spacing = self.window.connection_grid.verticalSpacing()
        self.assertEqual(expected_spacing, 3)
        for tab_index in range(1, self.window.tabs.count()):
            tab_grid = self.window.tabs.widget(tab_index).layout()
            self.assertEqual(tab_grid.verticalSpacing(), expected_spacing,
                             self.window.tabs.tabText(tab_index))
            self.assertLessEqual(last_occupied_row(tab_grid), 2,
                                 self.window.tabs.tabText(tab_index))
        self.assertEqual(
            self.window.advanced_grid.getItemPosition(
                self.window.advanced_grid.indexOf(self.window.refresh_schema_button)),
            (0, 0, 1, 1),
        )
        self.assertEqual(
            self.window.advanced_grid.getItemPosition(
                self.window.advanced_grid.indexOf(self.window.export_public_button))[0], 2)
        self.assertEqual(
            self.window.admin_grid.getItemPosition(
                self.window.admin_grid.indexOf(self.window.fields["admin_username"]))[0], 0)
        self.assertEqual(
            self.window.admin_grid.getItemPosition(
                self.window.admin_grid.indexOf(self.window.fields["admin_password"]))[0], 0)

        options = self.window.tree_options_layout
        for name in ("defaults", "state", "show_candidates"):
            self.assertGreaterEqual(options.indexOf(self.window.checks[name]), 0, name)
        self.assertEqual(self.window.checks["show_candidates"].text(), "顯示可新增節點")
        self.assertEqual(self.window.tree.contextMenuPolicy(),
                         Qt.ContextMenuPolicy.CustomContextMenu)
        self.assertEqual(self.window.toggle_connection_button.objectName(),
                         "toggleConnectionButton")
        self.assertEqual(self.window.connection_divider_layout.indexOf(
            self.window.toggle_connection_button), 1)
        self.assertIn("QMenu::item:selected", self.app.styleSheet())
        self.assertIn("background: #0969da", self.app.styleSheet())

        menu = self.window._build_tree_context_menu(self.window.tree.topLevelItem(0))
        action_labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertIn("新增子節點／list 項目…", action_labels)
        self.assertIn("新增根 YANG 節點…", action_labels)
        self.assertIn("刪除整個節點…", action_labels)
        menu.deleteLater()

    def test_double_click_on_candidate_never_opens_creation_dialog(self):
        candidate_item = QTreeWidgetItem(self.window.tree, ["candidate"])
        candidate_item.setData(0, Qt.ItemDataRole.UserRole, Candidate(object()))
        with patch.object(self.window, "_open_creation") as open_creation:
            self.window._item_double_clicked(candidate_item, 0)
        open_creation.assert_not_called()
        menu = self.window._build_tree_context_menu(candidate_item)
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertIn("建立此候選節點…", labels)
        menu.deleteLater()

    def test_password_inputs_are_visible_and_jump_grid_has_no_overlap(self):
        for name in ("password", "key_passphrase", "jump_password", "jump_passphrase",
                     "admin_password", "admin_passphrase"):
            self.assertEqual(self.window.fields[name].echoMode(), QLineEdit.EchoMode.Normal, name)
        jump_port = self.window.fields["jump_port"]
        self.assertEqual(jump_port.text(), "22")
        layout = jump_port.parentWidget().layout()
        positions = {}
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item.widget() in {jump_port, self.window.jump_auth}:
                positions[item.widget()] = layout.getItemPosition(index)
        self.assertEqual(positions[jump_port], (0, 5, 1, 1))
        self.assertEqual(positions[self.window.jump_auth], (0, 7, 1, 1))


if __name__ == "__main__":
    unittest.main()
