import unittest
from unittest.mock import MagicMock, patch

import paramiko
from ncclient.transport.errors import AuthenticationError

from netconf_console.sshauth import authenticate


class SSHAuthenticationMessageTests(unittest.TestCase):
    def test_password_failure_has_actionable_message_and_collapsed_details(self):
        transport = MagicMock()
        transport.auth_publickey.side_effect = paramiko.BadAuthenticationType("not allowed", ["password"])
        transport.auth_password.side_effect = paramiko.AuthenticationException()
        with patch("netconf_console.sshauth.paramiko.PKey.from_path", return_value=object()):
            with self.assertRaises(AuthenticationError) as raised:
                authenticate(transport, "u", "wrong", ["id_ed25519"], False, False, mode="auto")
        message = str(raised.exception)
        self.assertIn("DUT 只接受登入密碼", message)
        self.assertIn("確認 SSH 使用者名稱、登入密碼", message)
        self.assertNotIn("AuthenticationException", message)
        self.assertIn("認證嘗試紀錄", raised.exception.auth_details)


if __name__ == "__main__":
    unittest.main()
