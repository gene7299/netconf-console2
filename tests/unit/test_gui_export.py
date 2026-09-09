"""XML export operates on synthetic snapshots without touching any server."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from lxml import etree

from netconf_console.gui.app import NetconfWindow
from netconf_console.gui.client import ReadOptions
from netconf_console.gui.model import NC


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = NetconfWindow(self.root, persist=False)
        self.temp = tempfile.TemporaryDirectory()
        self.filename = Path(self.temp.name) / "tree.xml"

    def tearDown(self):
        self.app._destroy()
        self.temp.cleanup()

    def test_no_snapshot_or_busy_disables_tree_export(self):
        self.assertTrue(self.app.save_tree_button.instate(["disabled"]))
        with patch("netconf_console.gui.app.filedialog.asksaveasfilename") as dialog:
            self.app.save_tree_xml()
            self.app.load_demo()
            self.app.busy = True
            self.app._sync()
            self.app.save_tree_xml()
            dialog.assert_not_called()
        self.assertTrue(self.app.save_tree_button.instate(["disabled"]))

    def test_tree_export_is_right_of_schema_and_fits_compact_window(self):
        self.root.geometry("1120x780")
        self.root.deiconify()
        self.root.update()
        button = self.app.save_tree_button
        self.assertIs(button.master, self.app.schema_button.master)
        self.assertEqual(button.master.pack_slaves(), [self.app.read_all_button, self.app.schema_button, button])
        self.assertTrue(button.winfo_ismapped())
        self.assertGreaterEqual(button.winfo_x(), self.app.schema_button.winfo_x() + self.app.schema_button.winfo_width())
        self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())
        self.assertLessEqual(button.winfo_x() + button.winfo_width(), button.master.winfo_width())
    def test_whole_tree_exports_collapsed_roots_not_editor_draft(self):
        self.app.load_demo()
        self.assertEqual(self.app.save_button.cget("text"), "匯出XML")
        self.assertEqual(self.app.save_tree_button.cget("text"), "匯出XML")
        self.app.editor.set("<unsent-draft/>")
        original = etree.tostring(self.app.snapshot.data)
        with patch("netconf_console.gui.app.filedialog.asksaveasfilename", return_value=str(self.filename)), patch.object(self.app.client, "read") as read:
            self.app.save_tree_xml()
            read.assert_not_called()
        data = self.filename.read_bytes()
        self.assertTrue(data.startswith(b"<?xml"))
        self.assertIn(b"UTF-8", data.split(b"?>")[0])
        self.assertIn("管理介面", data.decode("utf-8"))
        root = etree.fromstring(data)
        self.assertEqual(root.tag, "{%s}data" % NC)
        self.assertEqual(len(root), 2)
        self.assertIsNotNone(root.find(".//{urn:o-ran:interfaces:1.0}port-number"))
        self.assertIsNone(root.find(".//unsent-draft"))
        self.assertEqual(etree.tostring(self.app.snapshot.data), original)

    def test_cancel_and_file_error_preserve_editor(self):
        self.app.load_demo()
        text = self.app.editor.get()
        with patch("netconf_console.gui.app.filedialog.asksaveasfilename", return_value=""):
            self.app.save_tree_xml()
        self.assertFalse(self.filename.exists())
        with patch("netconf_console.gui.app.filedialog.asksaveasfilename", return_value=str(self.filename)), patch.object(Path, "write_bytes", side_effect=OSError("synthetic failure")), patch.object(self.app, "_error") as error:
            self.app.save_tree_xml()
            error.assert_called_once()
        self.assertEqual(self.app.editor.get(), text)

    def test_export_follows_loaded_options_not_unsent_checkbox_change(self):
        self.app.load_demo()
        self.app._accept_snapshot(self.app.client.read(ReadOptions(defaults=False, state=False)))
        self.app.vars["state"].set(True)
        self.app.vars["defaults"].set(True)
        with patch("netconf_console.gui.app.filedialog.asksaveasfilename", return_value=str(self.filename)):
            self.app.save_tree_xml()
        root = etree.fromstring(self.filename.read_bytes())
        self.assertIsNone(root.find(".//{urn:ietf:params:xml:ns:yang:ietf-interfaces}oper-status"))
        self.assertIsNone(root.find(".//{urn:o-ran:interfaces:1.0}l2-mtu"))

    def test_stale_disconnected_snapshot_can_be_exported_as_snapshot(self):
        self.app.load_demo()
        self.app.client.connected = False
        self.app.uncertain = True
        self.app._sync()
        self.assertFalse(self.app.save_tree_button.instate(["disabled"]))
        with patch("netconf_console.gui.app.filedialog.asksaveasfilename", return_value=str(self.filename)):
            self.app.save_tree_xml()
        self.assertIn("快照", self.app.status.get())
        self.assertTrue(self.filename.exists())


if __name__ == "__main__":
    unittest.main()
