"""Native widget event tests using only a locally created offline demo window."""
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from netconf_console.gui.app import NetconfWindow


class WidgetTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = NetconfWindow(self.root, persist=False)
        self.app.load_demo()
        self.root.update()
        self.app._expand_item("0")
        self.app._show_selection("0/0")
        self.root.update()

    def tearDown(self):
        if not self.app.closed:
            self.app._destroy()

    def pump(self, until, timeout=3):
        deadline = time.monotonic() + timeout
        while not until() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(until())

    def change(self):
        value = self.app.editor.get().replace(">1500<", ">9000<")
        self.app.editor.text.delete("1.0", "end")
        self.app.editor.text.insert("1.0", value)
        self.pump(lambda: self.app.plan is not None and self.app.plan.rpc is not None)

    def test_native_defaults_colours_and_live_change_event(self):
        self.assertEqual(self.app.vars["source"].get(), "running")
        self.assertTrue(self.app.editor.text.tag_ranges("default"))
        self.assertTrue(self.app.editor.text.tag_ranges("state"))
        self.change()
        self.assertIn(">9000<", self.app.preview.get())
        self.assertTrue(self.app.editor.text.tag_ranges("changed"))
        self.assertFalse(self.app.send_button.instate(["disabled"]))

    def test_send_cancel_does_not_apply_then_confirm_exact_preview(self):
        self.change()
        with patch("netconf_console.gui.app.messagebox.askyesno", return_value=False):
            self.app.send()
        self.assertEqual(self.app.client.sent, [])
        with patch("netconf_console.gui.app.messagebox.askyesno", return_value=True):
            self.app.send()
            self.pump(lambda: not self.app.busy)
        self.assertEqual(self.app.client.sent, [self.app.last_sent_xml])
        self.assertIn("<ok", self.app.reply.get())
        self.assertFalse(self.app.dirty)

    def test_invalid_xml_disables_send(self):
        self.app.editor.set("<bad>")
        self.app._update_preview()
        self.assertTrue(self.app.send_button.instate(["disabled"]))
        self.assertIn("無法送出", self.app.preview_status.get())

    def test_cancel_discard_keeps_edits(self):
        self.change()
        value = self.app.editor.get()
        with patch("netconf_console.gui.app.messagebox.askyesno", return_value=False):
            self.app.revert()
        self.assertEqual(self.app.editor.get(), value)

    def test_close_completes_without_tcl_callback_error(self):
        errors = []
        self.root.report_callback_exception = lambda *args: errors.append(args)
        self.app.close()
        self.pump(lambda: self.app.closed)
        self.assertFalse(errors)

    def test_compact_window_keeps_status_and_both_xml_panes_visible(self):
        self.root.geometry("1120x780")
        self.root.deiconify()
        self.root.update()
        self.assertTrue(self.app.progress.winfo_ismapped())
        footer = self.app.progress.master
        self.assertLessEqual(footer.winfo_y() + footer.winfo_height(), self.root.winfo_height())
        self.assertGreater(self.app.editor.text.winfo_height(), 80)
        self.assertGreater(self.app.preview.text.winfo_height(), 80)

    def test_tree_summary_items_share_one_horizontal_row_across_workspace(self):
        self.root.geometry("1120x780")
        self.root.deiconify()
        self.root.update()
        footer = self.app.tree_footer
        self.assertIs(footer.master, self.root)
        self.assertEqual(
            set(footer.grid_slaves(row=0)),
            {self.app.schema_status_label, self.app.details_button, self.app.tree_status_label, self.app.progress},
        )
        self.assertTrue(all(widget.grid_info()["row"] == 0 for widget in footer.grid_slaves(row=0)))
        self.assertIs(self.app.schema_status_label.master, footer)
        self.assertIs(self.app.details_button.master, footer)
        self.assertIs(self.app.tree_status_label.master, footer)
        self.assertIs(self.app.progress.master, footer)
        self.assertGreater(footer.winfo_width(), self.app.tree.winfo_width())
        self.assertLessEqual(footer.winfo_height(), 40)

    def test_data_tree_actions_share_one_compact_row(self):
        self.root.geometry("1120x780")
        self.root.update()
        action_bar = self.app.add_child_button.master
        self.assertIs(self.app.add_root_button.master, action_bar)
        self.assertIs(self.app.delete_button.master, action_bar)
        self.assertIs(self.app.draft_button.master, action_bar)
        self.assertIs(self.app.leaf_button.master, action_bar)
        self.assertLessEqual(self.app.creation_bar.winfo_reqheight(), 55)
        self.assertGreater(self.app.tree.winfo_height(), 200)

    def test_delete_selected_child_stages_remove_in_parent_draft(self):
        self.app._expand_item("0/0")
        description_iid = next(
            iid for iid, item in self.app.items.items()
            if iid.startswith("0/0/") and item.node.tag.endswith("}description"))
        self.app._show_selection(description_iid)
        with patch("netconf_console.gui.app.messagebox.askyesno", return_value=True):
            self.app.delete_selected()
        self.assertEqual(self.app.selection.node.tag.endswith("}interface"), True)
        self.assertNotIn("<description>", self.app.editor.get())
        self.assertIn('operation="remove"', self.app.preview.get())
        self.assertIn("REMOVE", self.app.plan.changes[0])
        self.assertEqual(self.app.plan.removals, 1)
        self.assertEqual(self.app.client.sent, [])

    def test_delete_rejects_list_key_and_config_false_nodes(self):
        self.app._expand_item("0/0")
        name_iid = next(
            iid for iid, item in self.app.items.items()
            if iid.startswith("0/0/") and item.node.tag.endswith("}name"))
        self.app._show_selection(name_iid)
        self.app.delete_selected()
        self.assertIn("list key", self.app.status.get())
        state_iid = next(
            iid for iid, item in self.app.items.items()
            if iid.startswith("0/0/") and item.node.tag.endswith("}oper-status"))
        self.app._show_selection(state_iid)
        self.app.delete_selected()
        self.assertIn("config false", self.app.status.get())

    def test_primary_connect_button_is_large_blue_and_disabled_safely(self):
        from tkinter import font
        self.app.client.connected = False
        self.app._sync()
        button = self.app.connect_button
        self.assertEqual(button.cget("background"), "#0969da")
        self.assertEqual(button.cget("foreground"), "#ffffff")
        actual = font.Font(root=self.root, font=button.cget("font")).actual()
        self.assertEqual(actual["weight"], "bold")
        self.assertGreaterEqual(actual["size"], 12)
        self.assertGreater(button.winfo_reqheight(), self.app.disconnect_button.winfo_reqheight())
        self.app.busy = True
        self.app._sync()
        self.assertEqual(button.cget("state"), "disabled")
        self.assertEqual(button.cget("background"), "#e2e8f0")
        with patch.object(self.app, "_run") as run:
            button.invoke()
            run.assert_not_called()
        self.app.busy = False


if __name__ == "__main__":
    unittest.main()
