"""Persistence uses synthetic credentials and an isolated directory only."""
import os
import ssl
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from netconf_console.gui.app import NetconfWindow
from netconf_console.session import ConnectionSettings, build_tls_context
from netconf_console.gui.preferences import (
    MAGIC, PreferencesError, PreferencesStore, empty_book, remember_account, remember_connection,
)


@unittest.skipUnless(os.name == "nt", "Windows DPAPI")
class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "gui-settings.dpapi"
        self.store = PreferencesStore(self.path)

    def test_dpapi_password_roundtrip_and_no_plaintext(self):
        book = empty_book()
        values = {"username": "fixture-user", "password": "Fixture-secret-123-測試", "host": "localhost"}
        book["last"] = {"values": values, "connection": "A", "account": "user"}
        remember_account(book, values, "user")
        remember_connection(book, values, "A")
        self.store.save(book)
        raw = self.path.read_bytes()
        self.assertTrue(raw.startswith(MAGIC))
        for value in values.values():
            self.assertNotIn(value.encode(), raw)
        self.assertEqual(PreferencesStore(self.path).load(), book)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_failed_encryption_preserves_previous_file(self):
        self.store.save(empty_book())
        original = self.path.read_bytes()
        with patch("netconf_console.gui.preferences._dpapi", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(RuntimeError):
                self.store.save(empty_book())
        self.assertEqual(self.path.read_bytes(), original)

    def test_corrupt_file_is_not_overwritten_by_window(self):
        self.path.write_bytes(b"not a DPAPI file")
        with self.assertRaises(PreferencesError):
            self.store.load()
        root = tk.Tk()
        root.withdraw()
        app = NetconfWindow(root, store=self.store)
        try:
            self.assertFalse(app._save_preferences())
            self.assertEqual(self.path.read_bytes(), b"not a DPAPI file")
        finally:
            app._destroy()

    def test_multiple_same_username_credentials_and_named_overwrite(self):
        book = empty_book()
        a = {"username": "fixture", "password": "one"}
        b = {"username": "fixture", "password": "two"}
        first = remember_account(book, a)
        self.assertEqual(remember_account(book, a), first)
        second = remember_account(book, b)
        self.assertNotEqual(first, second)
        remember_account(book, b, first)
        self.assertEqual(book["accounts"][first], b)

    def test_restart_restores_every_field_and_independent_profiles(self):
        root = tk.Tk()
        root.withdraw()
        app = NetconfWindow(root, store=self.store)
        try:
            self.assertFalse(app.vars["hostkey_verify"].get())
            self.assertFalse(app.vars["verify_hostname"].get())
            self.assertEqual(app.format_button.cget("text"), "Pretty")
            for index, (key, var) in enumerate(app.vars.items()):
                if type(var.get()) is bool:
                    var.set(True)
                else:
                    var.set("fixture-%s-%d" % (key, index))
            app.vars["mode"].set("TLS Call Home")
            app.vars["port"].set("830")  # Preserve custom TLS ports exactly.
            app.vars["source"].set("running")
            app.wrap_xml.set(False)
            expected = app._preference_values()
            app.connection_name.set("TLS lab")
            app.save_connection()
            app.vars["username"].set("another-user")
            app.vars["password"].set("another-fixture-password")
            app.account_name.set("Other SSH")
            app.save_account()
            app.vars["mode"].set("Direct SSH")
            app.connection_name.set("SSH lab")
            app.save_connection()
            self.assertEqual(len(app.preferences["connections"]), 2)
            app.connection_name.set("TLS lab")
            app._choose_connection()
            self.assertEqual(app._preference_values(), expected)
            self.assertTrue(app._save_preferences())
        finally:
            app._destroy()
        root = tk.Tk()
        root.withdraw()
        restored = NetconfWindow(root, store=PreferencesStore(self.path))
        try:
            self.assertEqual(restored._preference_values(), expected)
            self.assertEqual(restored.connection_name.get(), "TLS lab")
            self.assertEqual(len(restored.connection_box.cget("values")), 2)
            restored.account_name.set("Other SSH")
            restored._choose_account()
            self.assertEqual(restored.vars["password"].get(), "another-fixture-password")
            self.assertEqual(restored.vars["cert"].get(), expected["cert"])
        finally:
            restored._destroy()

    def test_connect_has_no_host_key_warning_and_remembers_history(self):
        root = tk.Tk()
        root.withdraw()
        app = NetconfWindow(root, store=self.store)
        try:
            app.vars["username"].set("fixture")
            app.vars["password"].set("fixture-password")
            app.connection_name.set("Test peer")
            with patch("netconf_console.gui.app.messagebox.askyesno") as warning, patch.object(app, "_run") as run:
                app.connect()
            warning.assert_not_called()
            run.assert_called_once()
            book = self.store.load()
            self.assertIn("Test peer", book["connections"])
            self.assertTrue(book["accounts"])
        finally:
            app._destroy()

    def test_autosave_partial_fields_without_connecting(self):
        root = tk.Tk()
        root.withdraw()
        app = NetconfWindow(root, store=self.store)
        try:
            app.vars["password"].set("unsent-fixture")
            app.vars["port"].set("")
            root.after(900, root.quit)
            root.mainloop()
            last = self.store.load()["last"]["values"]
            self.assertEqual(last["password"], "unsent-fixture")
            self.assertEqual(last["port"], "")
        finally:
            app._destroy()

    def test_tls_name_off_keeps_ca_chain_verification(self):
        with patch.object(ssl.SSLContext, "load_cert_chain"), patch.object(ssl.SSLContext, "load_default_certs"):
            context = build_tls_context(ConnectionSettings(cert="fixture.pem", verify_hostname=False))
        self.assertFalse(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


if __name__ == "__main__":
    unittest.main()
