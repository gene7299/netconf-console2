"""System SSH uses explicit administration, minimal XML and no automatic retries."""
from copy import deepcopy
from dataclasses import replace
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from lxml import etree

from netconf_console.gui import sysrepo
from netconf_console.gui.app import NetconfWindow
from netconf_console.gui.model import NC, EditError, Selection, build_plan
from netconf_console.gui.schema import SchemaIndex
from netconf_console.gui.preferences import empty_book, public_book

NS = "urn:ietf:params:xml:ns:yang:ietf-netconf-server"
YANG = '''module ietf-netconf-server {namespace "urn:ietf:params:xml:ns:yang:ietf-netconf-server";prefix ncs;
 container netconf-server {container call-home {list netconf-client {key name;leaf name {type string;}
 container endpoints {list endpoint {key name;leaf name {type string;}container ssh {
 container tcp-client-parameters {leaf remote-address {type string;}leaf remote-port {type uint16;}}
 container client-authentication {leaf password {type string;}}}}}}}}}'''
BEFORE = ('<netconf-server xmlns="%s"><call-home><netconf-client><name>client0</name><endpoints><endpoint>'
    '<name>ssh-ep0</name><ssh><tcp-client-parameters><remote-address>2000::c4</remote-address><remote-port>4334</remote-port>'
    '</tcp-client-parameters><client-authentication><password>synthetic-secret-unchanged</password></client-authentication>'
    '</ssh></endpoint></endpoints></netconf-client></call-home></netconf-server>' % NS).encode()
AFTER = BEFORE.replace(b"2000::c4", b"2000::c5")


def fixture():
    schema = SchemaIndex.compile({"ietf-netconf-server": YANG})
    root = etree.fromstring(BEFORE)
    selection = Selection(root[0], (root,))
    plan = build_plan(selection, selection.text().replace("2000::c4", "2000::c5"), schema)
    return schema, selection, plan


class SysrepoTests(unittest.TestCase):
    def setUp(self):
        self.schema, self.selection, self.plan = fixture()
        self.prepared = sysrepo.prepare(self.plan, self.schema)

    def test_exact_minimal_payload_without_rpc_envelope_or_other_credentials(self):
        prepared = self.prepared
        root = etree.fromstring(prepared.payload)
        self.assertEqual(root.tag, "{%s}netconf-server" % NS)
        self.assertEqual(root.get("{%s}operation" % sysrepo.SR), "none")
        self.assertIsNone(root.get("{%s}operation" % NC))
        self.assertEqual(root.find(".//{%s}remote-address" % NS).get("{%s}operation" % NC), "merge")
        self.assertIn(b"client0", prepared.payload)
        self.assertIn(b"ssh-ep0", prepared.payload)
        self.assertNotIn(b"password", prepared.payload)
        self.assertNotIn(b"remote-port", prepared.payload)
        self.assertNotIn(b"edit-config", prepared.payload)
        self.assertEqual(prepared.command, "sysrepocfg --edit --datastore running --module ietf-netconf-server --format xml --timeout 10 --lock")

    def test_commands_are_not_shell_input_and_target_is_running_only(self):
        for path in ("sysrepocfg; id", "sudo sysrepocfg", "$(id)", "sysrepocfg --import", "../sysrepocfg"):
            with self.subTest(path=path), self.assertRaises(EditError):
                sysrepo.prepare(self.plan, self.schema, path)
        valid = sysrepo.prepare(self.plan, self.schema, "/usr/local/bin/sysrepocfg")
        self.assertTrue(valid.command.startswith("/usr/local/bin/sysrepocfg --edit"))
        plan = build_plan(self.selection, self.selection.text().replace("2000::c4", "2000::c5"), self.schema, "candidate")
        with self.assertRaises(EditError):
            sysrepo.prepare(plan, self.schema)

    def test_preflight_write_readback_and_no_other_rpc(self):
        shell = MagicMock()
        shell.run.side_effect = [(BEFORE, b""), (b"", b""), (AFTER, b"")]
        reply, warnings = sysrepo.execute(shell, self.prepared, self.selection, self.plan, self.schema)
        self.assertEqual(warnings, [])
        self.assertIn("exit=0", reply)
        self.assertEqual(shell.run.call_count, 3)
        self.assertEqual(shell.run.call_args_list[1].args, (self.prepared.command, self.prepared.payload))

    def test_conflict_and_bad_export_never_write(self):
        for data in (AFTER, b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>', b"not XML"):
            shell = MagicMock()
            shell.run.return_value = (data, b"")
            with self.subTest(data=data[:20]), self.assertRaises(Exception):
                sysrepo.execute(shell, self.prepared, self.selection, self.plan, self.schema)
            self.assertEqual(shell.run.call_count, 1)

    def test_lost_write_reply_is_not_retried(self):
        shell = MagicMock()
        shell.run.side_effect = [(BEFORE, b""), TimeoutError("unknown")]
        with self.assertRaises(TimeoutError):
            sysrepo.execute(shell, self.prepared, self.selection, self.plan, self.schema)
        self.assertEqual(shell.run.call_count, 2)

    def test_readback_failure_reports_warning_not_retry(self):
        shell = MagicMock()
        shell.run.side_effect = [(BEFORE, b""), (b"", b""), TimeoutError()]
        reply, warnings = sysrepo.execute(shell, self.prepared, self.selection, self.plan, self.schema)
        self.assertIn("exit=0", reply)
        self.assertTrue(warnings)
        self.assertEqual(shell.run.call_count, 3)

    def test_tampered_preview_rejected_before_ssh(self):
        shell = MagicMock()
        for prepared in (replace(self.prepared, command=self.prepared.command.replace("--edit", "--import")),
                         replace(self.prepared, payload=AFTER)):
            with self.assertRaises(EditError):
                sysrepo.execute(shell, prepared, self.selection, self.plan, self.schema)
        shell.run.assert_not_called()

    def test_multiple_export_roots_and_xml_declaration(self):
        data = sysrepo.parse_export(b'<?xml version="1.0" encoding="UTF-8"?>' + BEFORE + b'<extra xmlns="urn:extra"/>')
        self.assertEqual(len(data), 2)

    def test_public_export_removes_both_admin_secrets(self):
        book = empty_book()
        values = {"admin_password": "admin-secret", "admin_passphrase": "key-secret", "admin_host": "localhost"}
        book["last"] = {"values": values}
        book["connections"]["test"] = values
        self.assertNotIn("secret", str(public_book(book)))


class AdminWidgetTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = NetconfWindow(self.root, persist=False)

    def tearDown(self):
        self.app._destroy()

    def test_separate_tab_defaults_and_profile_migration(self):
        tabs = [self.app.auth_tabs.tab(tab, "text") for tab in self.app.auth_tabs.tabs()]
        self.assertIn("系統 SSH／sysrepo", tabs)
        self.assertEqual(self.app.vars["admin_port"].get(), "22")
        self.assertFalse(self.app.vars["admin_verify"].get())
        self.assertGreaterEqual(self.app.admin_password_entry.cget("width"), 22)
        self.assertGreaterEqual(self.app.netconf_password_entry.cget("width"), 26)
        self.assertGreaterEqual(self.app.jump_password_entry.cget("width"), 22)
        self.assertEqual(self.app.admin_connect_button.cget("background"), "#0969da")
        self.assertEqual(self.app.admin_connect_button.cget("state"), "normal")
        self.app.vars["admin_password"].set("old-secret")
        self.app._set_preference_values({"mode": "Direct SSH", "username": "oranuser"})
        self.assertEqual(self.app.vars["admin_password"].get(), "")
        self.app.vars["admin_password"].set("keep-system-secret")
        self.app._set_preference_values({"username": "different-netconf-account"})
        self.assertEqual(self.app.vars["admin_password"].get(), "keep-system-secret")

    def test_no_automatic_root_fallback_on_nacm_error(self):
        with patch("netconf_console.gui.app.messagebox.showerror"), patch("netconf_console.gui.sysrepo.ShellConnection") as shell:
            self.app._error(EditError("access-denied NACM"))
            shell.assert_not_called()

    def test_system_ssh_connect_button_turns_green_when_connected(self):
        shell = MagicMock()
        shell.connected = True
        self.app.admin_connection = shell
        self.app._sync_admin()
        self.assertEqual(self.app.admin_connect_button.cget("background"), "#16a34a")
        self.assertEqual(self.app.admin_connect_button.cget("state"), "disabled")
        self.assertEqual(self.app.admin_connect_button.cget("text"), "系統 SSH 已連線")

    def test_connection_labels_and_running_defaults(self):
        tabs = [self.app.auth_tabs.tab(tab, "text") for tab in self.app.auth_tabs.tabs()]
        self.assertIn("SSH跳板", tabs)
        self.assertNotIn("SSH 跳板（VMware）", tabs)
        self.assertEqual(self.app.vars["source"].get(), "running")
        labels = [widget.cget("text") for widget in self.app.source_box.master.winfo_children()
                  if widget.winfo_class() in {"Label", "TLabel"}]
        self.assertTrue(any("預設 running" in text for text in labels))

    def test_edit_actions_are_coloured_and_ordered_in_xml_toolbar(self):
        from tkinter import ttk
        normal, admin = self.app.send_button, self.app.admin_edit_button
        self.assertEqual(normal.cget("text"), "NETCONF方式修改")
        self.assertEqual(admin.cget("text"), "使用系統sysrepocfg修改")
        self.assertIs(normal.master, admin.master)
        style = ttk.Style(self.root)
        for button, colour in ((normal, "#15803d"), (admin, "#b91c1c")):
            self.assertEqual(style.lookup(button.cget("style"), "background", ()), colour)
            self.assertEqual(style.lookup(button.cget("style"), "background", ("disabled",)), "#e2e8f0")
            self.assertTrue(button.instate(["disabled"]))
        self.root.deiconify()
        self.root.update()
        self.assertLess(normal.winfo_rootx(), admin.winfo_rootx())
        self.assertEqual(normal.winfo_rooty(), admin.winfo_rooty())

    def test_connection_tabs_have_distinct_selected_and_idle_styles(self):
        from tkinter import ttk
        style = ttk.Style(self.root)
        self.assertEqual(self.app.auth_tabs.cget("style"), "Connection.TNotebook")
        name = "Connection.TNotebook.Tab"
        self.assertEqual(style.lookup(name, "background", ("selected",)), "#0969da")
        self.assertEqual(style.lookup(name, "foreground", ("selected",)), "white")
        self.assertNotEqual(style.lookup(name, "background", ()), "#0969da")
        for tab in self.app.auth_tabs.tabs():
            self.app.auth_tabs.select(tab)
            self.root.update_idletasks()
            self.assertEqual(self.app.auth_tabs.select(), tab)

    def test_preview_cancel_and_authority_gate_then_single_explicit_write(self):
        self.app.load_demo()
        self.app.demo = False
        self.app._expand_item("0")
        self.app._show_selection("0/0")
        self.app.editor.set(self.app.editor.get().replace(">1500<", ">9000<"))
        shell = MagicMock()
        shell.connected = True
        shell.settings = self.app._admin_settings()
        self.app.admin_connection = shell
        self.app.uncertain = True  # A previous access-denied does not force a root retry.
        self.app.open_sysrepo()
        window = self.app.lifecycle_dialog
        self.assertIsNotNone(window)
        shell.run.assert_not_called()
        bar = window.winfo_children()[-1]
        buttons = [w for w in bar.winfo_children() if w.winfo_class() == "TButton"]
        next(b for b in buttons if b.cget("text") == "取消").invoke()
        shell.run.assert_not_called()
        self.app.open_sysrepo()
        bar = self.app.lifecycle_dialog.winfo_children()[-1]
        send = next(w for w in bar.winfo_children() if w.winfo_class() == "TButton" and "執行" in w.cget("text"))
        with patch("netconf_console.gui.admin.messagebox.showinfo"):
            send.invoke()
        shell.run.assert_not_called()
        next(w for w in bar.winfo_children() if w.winfo_class() == "TCheckbutton").invoke()
        with patch("netconf_console.gui.admin.messagebox.askyesno", return_value=True), patch(
                "netconf_console.gui.sysrepo.execute", return_value=("exit=0", [])) as execute, patch.object(
                self.app, "_run", side_effect=lambda label, work, done: done(work())):
            send.invoke()
            execute.assert_called_once()
        self.assertTrue(self.app.admin_requires_refresh)
        self.assertTrue(self.app.uncertain)
        self.assertEqual(self.app.client.sent, [])
        self.app.open_sysrepo()
        self.assertIsNone(self.app.lifecycle_dialog)


if __name__ == "__main__":
    unittest.main()
