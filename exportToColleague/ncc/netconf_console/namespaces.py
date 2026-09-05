"""Dynamic YANG module and XML namespace discovery with per-server caching."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit

from lxml import etree

from .config import config_dir


NETCONF_BASE_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
YANG_LIBRARY_NS = "urn:ietf:params:xml:ns:yang:ietf-yang-library"
DATASTORES_NS = "urn:ietf:params:xml:ns:yang:ietf-datastores"
ORIGIN_NS = "urn:ietf:params:xml:ns:yang:ietf-origin"

# These aliases are an offline fast path. Device-discovered entries override
# them, and an explicit --ns assignment has the final word.
COMMON_NAMESPACES = {
    "nc": NETCONF_BASE_NS,
    "if": "urn:ietf:params:xml:ns:yang:ietf-interfaces",
    "ietf-if": "urn:ietf:params:xml:ns:yang:ietf-interfaces",
    "ianaift": "urn:ietf:params:xml:ns:yang:iana-if-type",
    "hw": "urn:ietf:params:xml:ns:yang:ietf-hardware",
    "oran": "urn:o-ran:interfaces:1.0",
    "o-ran-int": "urn:o-ran:interfaces:1.0",
    "o-ran-hw": "urn:o-ran:hardware:1.0",
    "ds": DATASTORES_NS,
    "or": ORIGIN_NS,
}


def _local_name(element: etree._Element) -> str:
    return etree.QName(element).localname


def _direct_child_text(element: etree._Element, name: str) -> str | None:
    for child in element:
        if _local_name(child) == name and child.text:
            return child.text.strip()
    return None


def _strip_yang_comments(text: str) -> str:
    """Remove YANG comments without damaging quoted namespace URIs."""

    output: list[str] = []
    index = 0
    quote: str | None = None
    line_comment = False
    block_comment = False
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
                output.append(char)
            index += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                output.append(" ")
                index += 2
            else:
                if char in "\r\n":
                    output.append(char)
                index += 1
            continue
        if quote:
            output.append(char)
            if char == "\\" and index + 1 < len(text):
                output.append(following)
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            output.append(char)
            index += 1
            continue
        if char == "/" and following == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and following == "*":
            block_comment = True
            index += 2
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _yang_statement(text: str, keyword: str, terminator: str) -> str | None:
    import re

    argument = r'(?:"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\'|([^\s;{}]+))'
    match = re.search(r"\b%s\s+%s\s*%s" % (re.escape(keyword), argument, terminator), text)
    if not match:
        return None
    return next((value for value in match.groups() if value is not None), None)


def parse_yang_header(text: str) -> tuple[str | None, str | None, str | None]:
    """Return ``(module, namespace, prefix)`` from a YANG module."""

    cleaned = _strip_yang_comments(text)
    module = _yang_statement(cleaned, "module", r"\{")
    namespace = _yang_statement(cleaned, "namespace", ";")
    prefix = _yang_statement(cleaned, "prefix", ";")
    return module, namespace, prefix


def yang_library_filter() -> etree._Element:
    """Build one subtree filter covering RFC 8525 and legacy RFC 7895 roots."""

    root = etree.Element("{%s}filter" % NETCONF_BASE_NS, type="subtree")
    etree.SubElement(root, "{%s}yang-library" % YANG_LIBRARY_NS)
    etree.SubElement(root, "{%s}modules-state" % YANG_LIBRARY_NS)
    return root


class NamespaceRegistry:
    """Namespace aliases learned from hello, YANG Library and YANG schemas."""

    CACHE_VERSION = 1

    def __init__(self, cache_file: str | Path | None = None) -> None:
        self.cache_file = Path(cache_file) if cache_file else config_dir() / "namespaces.json"
        self.server_key: str | None = None
        self.content_id: str | None = None
        self.advertised_content_id: str | None = None
        self.library_loaded = False
        self._aliases: dict[str, str] = {}
        self._sources: dict[str, str] = {}

    def select_server(self, key: str) -> None:
        if key == self.server_key:
            return
        self.server_key = key
        self.content_id = None
        self.advertised_content_id = None
        self.library_loaded = False
        self._aliases = {}
        self._sources = {}
        document = self._load_cache()
        saved = document.get("servers", {}).get(key, {})
        aliases = saved.get("aliases", {})
        if isinstance(aliases, dict):
            self._aliases = {
                str(alias): str(uri) for alias, uri in aliases.items()
                if alias and uri
            }
            self._sources.update({alias: "cache" for alias in self._aliases})
        content_id = saved.get("content_id")
        self.content_id = str(content_id) if content_id else None
        self.library_loaded = bool(saved.get("library_loaded", False))

    def mapping(self) -> dict[str, str]:
        return dict(self._aliases)

    def rows(self) -> list[tuple[str, str, str]]:
        return [
            (alias, uri, self._sources.get(alias, "learned"))
            for alias, uri in sorted(self._aliases.items())
        ]

    def add(self, alias: str | None, uri: str | None, source: str) -> bool:
        if not alias or not uri:
            return False
        alias, uri = alias.strip(), uri.strip()
        if not alias or not uri:
            return False
        changed = self._aliases.get(alias) != uri
        self._aliases[alias] = uri
        self._sources[alias] = source
        return changed

    def learn_capabilities(self, capabilities: Iterable[str]) -> bool:
        changed = False
        self.advertised_content_id = None
        for capability in capabilities:
            value = str(capability)
            parsed = urlsplit(value)
            params = parse_qs(parsed.query)
            module = (params.get("module") or [None])[0]
            if module:
                namespace = value.split("?", 1)[0]
                changed |= self.add(module, namespace, "hello")
            if "yang-library" in value:
                content_id = (
                    params.get("content-id")
                    or params.get("content_id")
                    or params.get("module-set-id")
                    or [None]
                )[0]
                if content_id:
                    self.advertised_content_id = str(content_id)
        if changed:
            self.save()
        return changed

    def should_query_yang_library(self, capabilities: Iterable[str], force: bool = False) -> bool:
        values = [str(value) for value in capabilities]
        if not any("yang-library" in value for value in values):
            return False
        if force:
            return True
        if not self.library_loaded:
            return True
        if self.advertised_content_id:
            return self.advertised_content_id != self.content_id
        return False

    def learn_yang_library(self, root: etree._Element | None) -> int:
        if root is None:
            return 0
        learned = 0
        for element in root.iter():
            local = _local_name(element)
            if local in {"content-id", "module-set-id"} and element.text:
                self.content_id = element.text.strip()
            if local not in {"module", "import-only-module"}:
                continue
            name = _direct_child_text(element, "name")
            namespace = _direct_child_text(element, "namespace")
            if self.add(name, namespace, "yang-library"):
                learned += 1
        if self.advertised_content_id and not self.content_id:
            self.content_id = self.advertised_content_id
        self.library_loaded = True
        self.save()
        return learned

    def learn_schema(self, text: str, module_hint: str | None = None) -> tuple[str, str] | None:
        module, namespace, prefix = parse_yang_header(text)
        module = module or module_hint
        if not namespace:
            return None
        changed = self.add(module, namespace, "get-schema")
        changed |= self.add(prefix, namespace, "get-schema")
        if changed:
            self.save()
        return prefix or module or "", namespace

    def save(self) -> None:
        if not self.server_key:
            return
        document = self._load_cache()
        servers = document.setdefault("servers", {})
        servers[self.server_key] = {
            "content_id": self.content_id,
            "library_loaded": self.library_loaded,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "aliases": dict(sorted(self._aliases.items())),
        }
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_file.with_suffix(self.cache_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self.cache_file)

    def _load_cache(self) -> dict[str, Any]:
        try:
            document = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"version": self.CACHE_VERSION, "servers": {}}
        if not isinstance(document, dict) or document.get("version") != self.CACHE_VERSION:
            return {"version": self.CACHE_VERSION, "servers": {}}
        if not isinstance(document.get("servers"), dict):
            document["servers"] = {}
        return document
