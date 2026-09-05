from __future__ import annotations

import unittest

from netconf_console.capabilities import capability_label, capability_rows


class CapabilityTests(unittest.TestCase):
    def test_standard_labels(self):
        self.assertEqual(capability_label("urn:ietf:params:netconf:base:1.1"), "NETCONF Base 1.1")
        self.assertEqual(
            capability_label("urn:ietf:params:netconf:capability:with-defaults:1.0"),
            "with-defaults",
        )
        self.assertIn("YANG module", capability_label("urn:example?module=oran-ru"))

    def test_rows_preserve_order_and_uri(self):
        capabilities = ["urn:example:a", "urn:example:b"]
        self.assertEqual(capability_rows(capabilities)[0][1], capabilities[0])
        self.assertEqual(len(capability_rows(capabilities)), 2)


if __name__ == "__main__":
    unittest.main()
