"""Tests for GUI language selection and translation boundaries."""

import unittest

from netconf_console.gui.i18n import detect_system_language, set_language, translate


class I18nTests(unittest.TestCase):
    def tearDown(self):
        set_language("zh-TW")

    def test_system_locale_maps_to_supported_languages(self):
        self.assertEqual(detect_system_language("zh-TW"), "zh-TW")
        self.assertEqual(detect_system_language("zh-HK"), "zh-TW")
        self.assertEqual(detect_system_language("zh-CN"), "zh-CN")
        self.assertEqual(detect_system_language("zh-SG"), "zh-CN")
        self.assertEqual(detect_system_language("en-US"), "en")
        self.assertEqual(detect_system_language("ja-JP"), "en")

    def test_english_translates_dynamic_status_and_menu_labels(self):
        set_language("en")
        self.assertEqual(
            translate("已讀取 running · 2 個根節點 + config false"),
            "Read running · 2 root nodes + config false",
        )
        self.assertEqual(translate("比較 running / startup…"), "Compare running / startup…")
        self.assertEqual(translate("刪除整個選取節點…"), "Delete the entire selected node…")

    def test_simplified_chinese_translates_common_controls(self):
        set_language("zh-CN")
        self.assertEqual(translate("NETCONF連線"), "NETCONF连接")
        self.assertEqual(translate("驗證 SSH host key"), "验证 SSH host key")
        self.assertEqual(translate("包含 config false（唯讀）"), "包含 config false（只读）")


if __name__ == "__main__":
    unittest.main()
