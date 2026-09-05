from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from netconf_console.namespaces import (
    NamespaceRegistry,
    YANG_LIBRARY_NS,
    parse_yang_header,
    yang_library_filter,
)
from netconf_console.session import ConnectionSettings, ConsoleContext, SessionMetadata


class NamespaceRegistryTests(unittest.TestCase):
    def test_yang_header_provides_module_namespace_and_prefix(self):
        text = """
            // prefix fake;
            module vendor-radio {
              namespace "https://example.test/yang/radio";
              prefix vr;
              /* namespace "urn:wrong"; */
            }
        """
        self.assertEqual(
            parse_yang_header(text),
            ("vendor-radio", "https://example.test/yang/radio", "vr"),
        )

    def test_capability_and_yang_library_modules_are_learned(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = NamespaceRegistry(Path(directory) / "namespaces.json")
            registry.select_server("ssh://oru:830")
            registry.learn_capabilities([
                "urn:vendor:alarm?module=vendor-alarm&revision=2026-01-01",
                "urn:ietf:params:netconf:capability:yang-library:1.1?content-id=abc",
            ])
            root = etree.fromstring((
                "<data><yang-library xmlns='%s'><content-id>abc</content-id>"
                "<module-set><name>complete</name><module>"
                "<name>vendor-radio</name><namespace>urn:vendor:radio</namespace>"
                "</module></module-set></yang-library></data>" % YANG_LIBRARY_NS
            ).encode())
            self.assertTrue(registry.should_query_yang_library([
                "urn:ietf:params:netconf:capability:yang-library:1.1?content-id=abc"
            ]))
            self.assertEqual(registry.learn_yang_library(root), 1)
            self.assertEqual(registry.mapping()["vendor-alarm"], "urn:vendor:alarm")
            self.assertEqual(registry.mapping()["vendor-radio"], "urn:vendor:radio")

    def test_legacy_module_set_id_invalidates_cached_yang_library(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = NamespaceRegistry(Path(directory) / "namespaces.json")
            registry.select_server("ssh://oru:830")
            old_capability = (
                "urn:ietf:params:netconf:capability:yang-library:1.0"
                "?revision=2016-06-21&module-set-id=old"
            )
            registry.learn_capabilities([old_capability])
            root = etree.fromstring((
                "<data><modules-state xmlns='%s'><module-set-id>old</module-set-id>"
                "</modules-state></data>" % YANG_LIBRARY_NS
            ).encode())
            registry.learn_yang_library(root)
            self.assertFalse(registry.should_query_yang_library([old_capability]))

            new_capability = old_capability.replace("module-set-id=old", "module-set-id=new")
            registry.learn_capabilities([new_capability])
            self.assertTrue(registry.should_query_yang_library([new_capability]))

    def test_get_schema_prefix_and_cache_survive_new_process(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "namespaces.json"
            first = NamespaceRegistry(cache)
            first.select_server("ssh://oru:830")
            learned = first.learn_schema(
                "module vendor-radio { namespace 'urn:vendor:radio'; prefix vr; }"
            )
            self.assertEqual(learned, ("vr", "urn:vendor:radio"))

            second = NamespaceRegistry(cache)
            second.select_server("ssh://oru:830")
            self.assertEqual(second.mapping()["vr"], "urn:vendor:radio")
            self.assertEqual(second.mapping()["vendor-radio"], "urn:vendor:radio")

    def test_yang_library_filter_supports_new_and_legacy_roots(self):
        root = yang_library_filter()
        self.assertEqual(root.get("type"), "subtree")
        self.assertIsNotNone(root.find("{%s}yang-library" % YANG_LIBRARY_NS))
        self.assertIsNotNone(root.find("{%s}modules-state" % YANG_LIBRARY_NS))

    def test_console_discovers_yang_library_once_and_reuses_cache(self):
        data = etree.fromstring((
            "<data><yang-library xmlns='%s'><content-id>abc</content-id>"
            "<module-set><module><name>vendor-radio</name>"
            "<namespace>urn:vendor:radio</namespace></module></module-set>"
            "</yang-library></data>" % YANG_LIBRARY_NS
        ).encode())

        class Manager:
            connected = True
            timeout = 30
            server_capabilities = [
                "urn:ietf:params:netconf:capability:yang-library:1.1?content-id=abc"
            ]

            def __init__(self):
                self.get_count = 0

            def get(self, _filter):
                self.get_count += 1
                return type("Reply", (), {"data": data})()

        with tempfile.TemporaryDirectory() as directory:
            context = ConsoleContext(ConnectionSettings(host="oru", port=830))
            context.namespace_registry = NamespaceRegistry(
                Path(directory) / "namespaces.json"
            )
            context.manager = Manager()
            context.metadata = SessionMetadata(
                mode="Direct", transport="SSH", remote_host="oru", remote_port=830,
                local_address=None, peer_address=None, listen_address=None,
                username="tester", connected_since=datetime.now(timezone.utc),
            )
            self.assertEqual(context.refresh_namespaces(), 1)
            self.assertEqual(context.manager.timeout, 30)
            self.assertEqual(
                context.namespace_registry.mapping()["vendor-radio"],
                "urn:vendor:radio",
            )
            self.assertEqual(context.refresh_namespaces(), 0)
            self.assertEqual(context.manager.get_count, 1)


if __name__ == "__main__":
    unittest.main()
