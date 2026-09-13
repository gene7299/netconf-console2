"""UTF-8 XML rendering and offline formatting without shell redirection."""

from __future__ import annotations

import codecs
import io
import re
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from lxml import etree


XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
QNAME_VALUE = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z_][A-Za-z0-9_.-]*):([A-Za-z_][A-Za-z0-9_.-]*)")


@dataclass
class XmlResult:
    """Carry an interactive command's output options back to the renderer."""

    element: etree._Element | etree._ElementTree
    mode: str = "pretty"
    filename: str | None = None


def read_xml(filename: str, huge_tree: bool = False) -> etree._ElementTree:
    """Read XML, including UTF-16 redirected by Windows PowerShell 5.1."""

    if filename == "-":
        data = getattr(sys.stdin, "buffer", sys.stdin).read()
        if isinstance(data, str):
            data = data.encode("utf-8")
    else:
        data = Path(filename).expanduser().read_bytes()

    # A BOM or UTF-16/32 byte signature is authoritative when PowerShell has
    # re-encoded stdout but left the XML declaration saying UTF-8. Do not use
    # recover=True: malformed or truncated XML must still fail validation.
    encoding = None
    for signature, candidate in (
        (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"),
        (b"<\x00\x00\x00", "utf-32-le"), (b"\x00\x00\x00<", "utf-32-be"),
        (b"<\x00", "utf-16-le"), (b"\x00<", "utf-16-be"),
    ):
        if data.startswith(signature):
            encoding = candidate
            break
    if encoding:
        text = data.decode(encoding)
        text = re.sub(
            r"^(<\?xml\s[^?]*?\bencoding\s*=\s*['\"])[^'\"]+(['\"])",
            r"\g<1>UTF-8\2", text, count=1,
        )
        data = text.encode("utf-8")

    parser = etree.XMLParser(
        resolve_entities=False, load_dtd=False, no_network=True,
        remove_blank_text=False, strip_cdata=False, huge_tree=huge_tree,
    )
    return etree.parse(io.BytesIO(data), parser)


def _compact_namespace_copy(element: etree._Element | etree._ElementTree) -> etree._Element:
    """Copy XML with only namespaces used by names, attributes or QName text."""
    source = element.getroot() if isinstance(element, etree._ElementTree) else element
    used = []
    seen_uris = set()
    preferred = {}

    def remember(uri):
        if uri and uri != XML_NAMESPACE and uri not in seen_uris:
            seen_uris.add(uri)
            used.append(uri)

    def inspect(node):
        if isinstance(node.tag, str) and node.tag.startswith("{"):
            remember(node.tag[1:].split("}", 1)[0])
        for name, value in node.attrib.items():
            if isinstance(name, str) and name.startswith("{"):
                remember(name[1:].split("}", 1)[0])
            for prefix, _local_name in QNAME_VALUE.findall(value or ""):
                remember(node.nsmap.get(prefix))
        for value in (node.text, node.tail):
            for prefix, _local_name in QNAME_VALUE.findall(value or ""):
                remember(node.nsmap.get(prefix))
        for prefix, uri in node.nsmap.items():
            if prefix != "xml" and uri:
                preferred.setdefault(uri, []).append(prefix)
        for child in node:
            inspect(child)

    inspect(source)
    nsmap = {}
    used_prefixes = set()
    root_uri = source.tag[1:].split("}", 1)[0] if isinstance(source.tag, str) and source.tag.startswith("{") else None
    for prefix, uri in source.nsmap.items():
        if uri not in seen_uris or prefix == "xml":
            continue
        if prefix is None and uri != root_uri:
            continue
        if prefix not in used_prefixes:
            nsmap[prefix] = uri
            used_prefixes.add(prefix)
    for uri in used:
        if uri in nsmap.values():
            continue
        for prefix in preferred.get(uri, ()):
            if prefix is None and uri != root_uri:
                continue
            if prefix not in used_prefixes:
                nsmap[prefix] = uri
                used_prefixes.add(prefix)
                break
        else:
            index = 0
            while "ns%d" % index in used_prefixes:
                index += 1
            prefix = "ns%d" % index
            nsmap[prefix] = uri
            used_prefixes.add(prefix)

    def clone(node, root=False):
        if isinstance(node.tag, str):
            copied = etree.Element(node.tag, attrib=dict(node.attrib), nsmap=nsmap if root else None)
            copied.text = node.text
            for child in node:
                child_copy = clone(child)
                copied.append(child_copy)
                child_copy.tail = child.tail
            copied.tail = node.tail
            return copied
        return deepcopy(node)

    return clone(source, root=True)


def serialize_xml(element: etree._Element | etree._ElementTree, mode: str = "pretty",
                  *, compact_namespaces: bool = False) -> bytes:
    """Indent XML, optionally dropping unused namespace declarations."""

    if compact_namespaces:
        element = _compact_namespace_copy(element)
    elif mode == "pretty":
        element = deepcopy(element)
    if mode == "pretty":
        root = element.getroot() if isinstance(element, etree._ElementTree) else element
        pending = [(root, 0)]
        while pending:
            node, depth = pending.pop()
            if not isinstance(node.tag, str) or not len(node):
                continue
            # Keep whole subtrees intact for xml:space='preserve' or mixed
            # content. In particular, never strip whitespace in a YANG leaf.
            if (node.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"
                    or (node.text or "").strip()
                    or any(isinstance(child, etree._Entity) for child in node)
                    or any((child.tail or "").strip() for child in node)):
                continue
            node.text = "\n" + "  " * (depth + 1)
            for child in node:
                child.tail = "\n" + "  " * (depth + 1)
                pending.append((child, depth + 1))
            node[-1].tail = "\n" + "  " * depth

    data = etree.tostring(element, encoding="UTF-8", xml_declaration=True)
    if mode == "pretty" and not data.endswith(b"\n"):
        data += b"\n"
    return data


def emit_xml(element: etree._Element | etree._ElementTree, mode: str = "pretty",
             filename: str | None = None) -> None:
    data = serialize_xml(element, mode)
    if filename and filename != "-":
        target = Path(filename).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print("Saved XML to %s (UTF-8, %s)" % (filename, mode), file=sys.stderr)
    else:
        # Native stdout receives UTF-8 bytes; --out also avoids PowerShell's
        # own decoding/re-encoding when its older versions implement `>`.
        stream = getattr(sys.stdout, "buffer", None)
        if stream is not None:
            sys.stdout.flush()
            stream.write(data)
            stream.flush()
        else:
            sys.stdout.write(data.decode("utf-8"))
