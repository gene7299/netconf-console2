"""Software download URI compatibility checks."""

import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from netconf_console.gui.software import validate_uri
from netconf_console.gui.software_transfer import local_sftp_username, make_uri


class SoftwareTransferTests(unittest.TestCase):
    @patch("netconf_console.gui.software_transfer.secrets.token_hex", return_value="844a64f9")
    def test_automatic_sftp_username_is_alphanumeric(self, _token_hex):
        username = local_sftp_username()
        self.assertEqual(username, "ncc844a64f9")
        uri = make_uri("192.168.142.128", 53073, username, "/cobra_sdk-1.2.1.zip")
        self.assertEqual(urlsplit(uri).username, username)
        self.assertNotIn("-", username)

    def test_rfc3986_hyphen_remains_valid_for_manual_uri(self):
        parts = validate_uri("sftp://ncc-844a64f9@192.168.142.128:53073/cobra_sdk-1.2.1.zip")
        self.assertEqual(parts.username, "ncc-844a64f9")


if __name__ == "__main__":
    unittest.main()
