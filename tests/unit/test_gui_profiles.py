"""Profile management tests with isolated encrypted synthetic records only."""
import os
import tempfile
import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from netconf_console.gui.app import NetconfWindow
from netconf_console.gui.preferences import PreferencesError, PreferencesStore, change_profiles, empty_book, remember_connection
from netconf_console.gui.profiles import ProfileManager


class CatalogTests(unittest.TestCase):
    def book(self):
        book = empty_book()
        book["connections"] = {"A": {"host": "a.invalid", "password": "fixture-secret"}, "B": {"host": "b.invalid"}}
        book["accounts"] = {"User": {"username": "fixture", "password": "fixture-secret"}}
        book["last"] = {"values": {}, "connection": "A", "account": "User"}
        return book

    def test_rename_preserves_order_values_and_selection(self):
        book = self.book()
        result = change_profiles(book, "connections", ["A"], "New")
        self.assertEqual(list(result["connections"]), ["New", "B"])
        self.assertEqual(result["connections"]["New"], book["connections"]["A"])
        self.assertEqual(result["last"]["connection"], "New")
        self.assertEqual(book["last"]["connection"], "A")

    def test_delete_multiple_without_cascade_and_clear_selection(self):
        book = self.book()
        result = change_profiles(book, "connections", ["A", "B"])
        self.assertFalse(result["connections"])
        self.assertEqual(result["last"]["connection"], "")
        self.assertEqual(result["accounts"], book["accounts"])
        result = change_profiles(book, "accounts", ["User"])
        self.assertEqual(result["connections"], book["connections"])
        self.assertFalse(result["accounts"])

    def test_invalid_batch_and_rename_collision_leave_original_intact(self):
        book = self.book()
        original = deepcopy(book)
        for names, new in ((["A"], "B"), (["A"], " "), (["A", "missing"], None), ([], None)):
            with self.assertRaises(PreferencesError):
                change_profiles(book, "connections", names, new)
            self.assertEqual(book, original)

    def test_view_changes_do_not_duplicate_anonymous_history(self):
        book = empty_book()
        values = {"mode": "Direct SSH", "host": "a.invalid", "username": "user", "port": "830", "password": "test"}
        name = remember_connection(book, values)
        values.update(defaults=True, state=True, connection_hidden=True, source="candidate")
        self.assertEqual(remember_connection(book, values), name)
        self.assertEqual(len(book["connections"]), 1)
        self.assertTrue(book["connections"][name]["defaults"])


@unittest.skipUnless(os.name == "nt", "Windows GUI credential store")
class ProfileWidgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = PreferencesStore(Path(self.temp.name) / "settings.dpapi")
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = NetconfWindow(self.root, store=self.store)
        self.app.vars["username"].set("synthetic-user")
        self.app.vars["password"].set("synthetic-secret")
        self.app.connection_name.set("Lab A")
        self.app.account_name.set("User A")
        self.app.save_connection()

    def tearDown(self):
        if not self.app.closed:
            self.app._destroy()
        self.temp.cleanup()

    def test_selected_named_history_updates_without_creating_suffixes(self):
        self.app.vars["state"].set(True)
        self.app.vars["password"].set("synthetic-new-secret")
        self.app._save_preferences(remember=True)
        self.assertEqual(list(self.app.preferences["connections"]), ["Lab A"])
        self.assertEqual(list(self.app.preferences["accounts"]), ["User A"])
        self.assertEqual(self.store.load()["accounts"]["User A"]["password"], "synthetic-new-secret")

    def test_deletion_persists_and_close_does_not_recreate(self):
        self.assertTrue(self.app.change_profile_catalog("connections", ["Lab A"]))
        self.assertTrue(self.app.change_profile_catalog("accounts", ["User A"]))
        # Field restoration stays independent of the deleted catalogs.
        self.assertEqual(self.app.vars["password"].get(), "synthetic-secret")
        with patch.object(self.app, "_run"):
            self.app.close()
        result = self.store.load()
        self.assertFalse(result["connections"])
        self.assertFalse(result["accounts"])
        self.app._destroy()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = NetconfWindow(self.root, store=self.store)
        self.assertFalse(self.app.connection_box.cget("values"))
        self.assertFalse(self.app.account_box.cget("values"))
        self.assertEqual(self.app.vars["password"].get(), "synthetic-secret")

    def test_failed_persist_leaves_catalog_and_selection_intact(self):
        before = deepcopy(self.app.preferences)
        raw = self.store.path.read_bytes()
        with patch.object(self.store, "save", side_effect=OSError("synthetic failure")), patch.object(self.app, "_error"):
            self.assertFalse(self.app.change_profile_catalog("connections", ["Lab A"]))
        self.assertEqual(self.app.preferences, before)
        self.assertEqual(self.app.connection_name.get(), "Lab A")
        self.assertEqual(self.store.path.read_bytes(), raw)

    def test_manage_search_rename_cancel_delete_and_load(self):
        self.app.connection_name.set("Lab B")
        self.app.save_connection()
        dialog = ProfileManager(self.app, "connections")
        dialog.search.set("lab a")
        self.assertEqual(dialog.list.size(), 1)
        dialog.list.selection_set(0)
        dialog.describe()
        self.assertNotIn("synthetic-secret", dialog.description.get())
        with patch("netconf_console.gui.profiles.messagebox.askyesno", return_value=False):
            dialog.delete()
        self.assertIn("Lab A", self.app.preferences["connections"])
        with patch("netconf_console.gui.profiles.simpledialog.askstring", return_value="Lab Alpha"):
            dialog.rename()
        self.assertIn("Lab Alpha", self.app.preferences["connections"])
        dialog.list.selection_set(0)
        dialog.load()
        self.assertEqual(self.app.connection_name.get(), "Lab Alpha")
        self.assertFalse(self.app.client.connected)
        dialog = ProfileManager(self.app, "connections")
        dialog.list.selection_set(0, "end")
        with patch("netconf_console.gui.profiles.messagebox.askyesno", return_value=True):
            dialog.delete()
        self.assertFalse(self.store.load()["connections"])
        dialog.window.destroy()

    def test_account_manager_description_hides_password_and_renames_selection(self):
        dialog = ProfileManager(self.app, "accounts")
        dialog.list.selection_set(0)
        dialog.describe()
        self.assertNotIn("synthetic-secret", dialog.description.get())
        with patch("netconf_console.gui.profiles.simpledialog.askstring", return_value="Renamed user"):
            dialog.rename()
        self.assertEqual(self.app.account_name.get(), "Renamed user")
        self.assertEqual(self.store.load()["last"]["account"], "Renamed user")
        dialog.window.destroy()

    def test_catalog_changes_blocked_while_busy_or_reconnecting(self):
        self.app.busy = True
        self.assertFalse(self.app.change_profile_catalog("connections", ["Lab A"]))
        self.app.busy = False
        self.app.reconnect_due = 1
        self.assertFalse(self.app.change_profile_catalog("accounts", ["User A"]))
        self.app.reconnect_due = None
        self.assertIn("Lab A", self.store.load()["connections"])


if __name__ == "__main__":
    unittest.main()
