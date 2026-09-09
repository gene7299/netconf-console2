"""Workspace regression tests; synthetic local data and credentials only."""
import json
import os
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from netconf_console.gui.app import NetconfWindow
from netconf_console.gui.preferences import PreferencesStore
from netconf_console.gui.windows import icon_path, set_app_id, APP_ID, has_window_icons
from netconf_console.session import ConnectionSettings


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = NetconfWindow(self.root, persist=False)
        self.app.load_demo()
        self.root.update()
        self.app._expand_item("0")
        self.app._expand_item("0/0")
        self.app._show_selection("0/0/0")
        self.app.tree.selection_set("0/0/0")
        self.app.tree.focus("0/0/0")
        self.root.update()

    def tearDown(self):
        if not self.app.closed:
            self.app._destroy()

    def pump(self):
        deadline = time.monotonic() + 3
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy)

    def test_indicator_click_does_not_select_or_read_and_toggles_once(self):
        self.root.deiconify()
        self.root.update()
        box = self.app.tree.bbox("0")
        y = box[1] + box[3] // 2
        x = next(x for x in range(45) if "indicator" in self.app.tree.identify_element(x, y).lower())
        with patch.object(self.app, "refresh_selected") as read:
            for expected in (False, True):
                self.app.tree.event_generate("<ButtonPress-1>", x=x, y=y)
                self.app.tree.event_generate("<ButtonRelease-1>", x=x, y=y)
                self.root.update()
                self.assertEqual(bool(self.app.tree.item("0", "open")), expected)
            read.assert_not_called()
        self.assertEqual(self.app.tree.selection(), ("0/0/0",))

    def test_async_snapshot_keeps_closed_ancestor_and_child_expansion(self):
        bookmark = self.app._bookmark()
        self.app.tree.item("0", open=False)
        self.app._accept_snapshot(self.app.snapshot, bookmark)
        self.root.update()
        self.assertFalse(self.app.tree.item("0", "open"))
        self.assertTrue(self.app.tree.item("0/0", "open"))
        self.assertEqual(self.app.selection_iid, "0/0/0")
        self.app._expand_item("0")
        self.assertTrue(self.app.tree.item("0/0", "open"))

    def test_collapse_panel_increases_workspace_and_restores(self):
        self.root.deiconify()
        self.root.update()
        before = self.app.editor.winfo_height() + self.app.preview.winfo_height()
        self.app.toggle_connection()
        self.root.update()
        self.assertFalse(self.app.connection_panel.winfo_ismapped())
        self.assertTrue(self.app.toggle_button.winfo_ismapped())
        self.assertGreater(self.app.editor.winfo_height() + self.app.preview.winfo_height(), before)
        self.assertTrue(self.app._preference_values()["connection_hidden"])
        self.app.toggle_connection()
        self.root.update()
        self.assertTrue(self.app.connection_panel.winfo_ismapped())

    def test_export_current_unsaved_fields_and_all_profiles_without_passwords(self):
        self.app.vars["password"].set("synthetic-unsaved-password")
        self.app.preferences["accounts"]["lab"] = {"username": "test", "password": "old-secret"}
        self.app.preferences["connections"]["lab"] = {"host": "fixture.invalid", "password": "old-secret"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            with patch("netconf_console.gui.app.filedialog.asksaveasfilename", return_value=str(path)):
                self.app.export_settings()
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("password", text)
            self.assertNotIn("old-secret", text)
            self.assertIn("fixture.invalid", text)
            self.assertEqual(json.loads(text)["last"]["values"]["port"], "830")
        self.assertEqual(self.app.preferences["accounts"]["lab"]["password"], "old-secret")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_encrypted_export_contains_passwords_and_can_restore(self):
        self.app.vars["password"].set("synthetic-export-secret")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.dpapi"
            with patch("netconf_console.gui.app.filedialog.asksaveasfilename", return_value=str(path)):
                self.app.export_settings(encrypted=True)
            self.assertNotIn(b"synthetic-export-secret", path.read_bytes())
            self.assertEqual(PreferencesStore(path).load()["last"]["values"]["password"], "synthetic-export-secret")

    def dropped(self):
        self.app.demo = False
        self.app.client.connected = False
        self.app.reconnect_enabled = True
        self.app.vars["auto_reconnect"].set(True)
        self.app.reconnect_settings = ConnectionSettings(host="fixture.invalid", username="test")
        self.app.client.connect = Mock(side_effect=lambda *_args: setattr(self.app.client, "connected", True))

    def test_retry_preserves_draft_uses_original_settings_and_never_sends(self):
        self.app._show_selection("0/0")
        self.app.editor.set(self.app.editor.get().replace(">1500<", ">9000<"))
        self.app._update_preview()
        draft = self.app.editor.get()
        self.dropped()
        self.app.vars["host"].set("changed.invalid")
        self.app._monitor_connection()
        self.assertTrue(self.app.uncertain)
        self.assertTrue(self.app.send_button.instate(["disabled"]))
        self.app.reconnect_due = 0
        self.app._monitor_connection()
        self.pump()
        self.assertEqual(self.app.client.connect.call_args.args[0].host, "fixture.invalid")
        self.assertEqual(self.app.editor.get(), draft)
        self.assertTrue(self.app.uncertain)
        self.assertTrue(self.app.send_button.instate(["disabled"]))
        self.assertEqual(self.app.client.sent, [])

    def test_failed_retry_backs_off_without_modal_warning(self):
        self.dropped()
        self.app.client.connect.side_effect = OSError("synthetic outage")
        self.app._monitor_connection()
        self.assertEqual(self.app.reconnect_delay, 4)
        self.app.reconnect_due = 0
        with patch("netconf_console.gui.app.messagebox.showerror") as alert:
            self.app._monitor_connection()
            self.pump()
            alert.assert_not_called()
        self.assertEqual(self.app.reconnect_delay, 8)
        self.assertIsNotNone(self.app.reconnect_due)
        self.app.stop_reconnect()
        self.app._monitor_connection()
        self.assertIsNone(self.app.reconnect_due)

    def test_manual_disconnect_and_disabled_option_never_retry(self):
        self.dropped()
        self.app.vars["auto_reconnect"].set(False)
        self.app._monitor_connection()
        self.assertIsNone(self.app.reconnect_due)
        self.app.vars["auto_reconnect"].set(True)
        self.app.disconnect()
        self.pump()
        self.app._monitor_connection()
        self.app.client.connect.assert_not_called()
        self.assertFalse(self.app.reconnect_enabled)

    def test_schema_only_refresh_does_not_clear_stale_data_guard(self):
        self.app.uncertain = True
        self.app._accept_snapshot(self.app.snapshot, fresh=False)
        self.assertTrue(self.app.uncertain)
        self.app._accept_snapshot(self.app.snapshot)
        self.assertFalse(self.app.uncertain)

    @unittest.skipUnless(os.name == "nt", "Windows shell")
    def test_windows_app_identity_and_icon_resource(self):
        import ctypes
        set_app_id()
        value = ctypes.c_wchar_p()
        shell = ctypes.WinDLL("shell32")
        self.assertEqual(shell.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(value)), 0)
        try:
            self.assertEqual(value.value, APP_ID)
        finally:
            free = ctypes.WinDLL("ole32").CoTaskMemFree
            free.argtypes = [ctypes.c_void_p]
            free(value)
        self.assertTrue(icon_path().is_file())
        self.root.deiconify()
        self.root.update()
        self.assertTrue(has_window_icons(self.root))


if __name__ == "__main__":
    unittest.main()
