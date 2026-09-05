from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from netconf_console.history import SecureFileHistory, sanitize_history_entry


class HistoryTests(unittest.TestCase):
    def test_password_values_are_removed_but_prompt_option_is_preserved(self):
        self.assertEqual(
            sanitize_history_entry("connect --host oru --password secret"),
            "connect --host oru --password",
        )
        self.assertEqual(
            sanitize_history_entry("connect --password='two words' --host oru"),
            "connect --password --host oru",
        )
        self.assertEqual(
            sanitize_history_entry("connect -p secret --host oru"),
            "connect -p --host oru",
        )
        self.assertEqual(
            sanitize_history_entry("connect --password --host oru"),
            "connect --password --host oru",
        )

    def test_history_is_loaded_by_a_new_console_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "history"
            first_run = SecureFileHistory(str(filename))
            first_run.append_string("status")
            first_run.append_string("connect --password secret")

            second_run = SecureFileHistory(str(filename))
            loaded = list(second_run.load_history_strings())

        self.assertEqual(loaded, ["connect --password", "status"])

    def test_secret_xml_is_redacted_in_history(self):
        result = sanitize_history_entry("rpc <password>secret</password>")
        self.assertNotIn("secret", result)
        self.assertIn("REDACTED", result)


if __name__ == "__main__":
    unittest.main()
