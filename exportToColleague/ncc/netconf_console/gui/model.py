"""Pure XML browsing/edit planning. No network calls and no implicit commits."""

from __future__ import annotations

import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field

from lxml import etree
from ncclient.xml_ import to_xml

from ..namespaces import NETCONF_BASE_NS as NC
from ..xmloutput import serialize_xml
from .schema import SchemaIndex, NodeInfo

WD = "urn:ietf:params:xml:ns:netconf:default:1.0"
WD_YANG = "urn:ietf:params:xml:ns:yang:ietf-netconf-with-defaults"
ORIGIN = "urn:ietf:params:xml:ns:yang:ietf-origin"
# RFC 6243 uses WD; libyang/sysrepo also emits a YANG metadata annotation
# in WD_YANG. Both are read metadata, never editable configuration.
DEFAULT_ATTRIBUTES = {"{%s}default" % ns for ns in (WD, WD_YANG)}
READ_ATTRIBUTES = DEFAULT_ATTRIBUTES | {"{%s}origin" % ORIGIN}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


class EditError(ValueError):
    pass


def children(node):
    return [child for child in node if isinstance(child.tag, str)]


def local(tag):
    return etree.QName(tag).localname


def parse_editor(text: str):
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                             remove_blank_text=False, huge_tree=True)
    root = etree.fromstring(text.encode("utf-8"), parser)
    if root.getroottree().docinfo.doctype:
        raise EditError("DOCTYPE/entities are not allowed in configuration XML.")
    for node in root.iter():
        if isinstance(node, etree._Entity):
            raise EditError("Entity references are not allowed.")
        if isinstance(node.tag, str) and "{%s}operation" % NC in node.attrib:
            raise EditError("Do not enter nc:operation in the editor; the preview generates it from your changes.")
    return root


def value_identity(node):
    text = node.text or ""
    bindings = tuple(sorted((prefix, uri) for prefix, uri in node.nsmap.items()
                            if prefix and re.search(r"(?<![\w.-])" + re.escape(prefix) + ":", text)))
    return text, bindings


def semantic(node):
    items = children(node)
    text = (node.text or "") if not items else (node.text or "").strip()
    return (node.tag, text, value_identity(node)[1],
            tuple(sorted((key, value) for key, value in node.attrib.items() if key not in READ_ATTRIBUTES)),
            tuple(semantic(child) for child in items))


def identity(node, schema: SchemaIndex, path: tuple[str, ...]):
    info = schema.lookup(path)
    if info and info.kind == "list":
        keys = []
        for tag in info.keys:
            found = node.findall(tag)
            if len(found) != 1:
                raise EditError("List %s requires exactly one key %s." % (local(node.tag), local(tag)))
            keys.append(value_identity(found[0]))
        return node.tag, tuple(keys)
    if info and info.kind == "leaf-list":
        return node.tag, value_identity(node)
    return (node.tag,)


@dataclass
class Selection:
    """Snapshot node plus all ancestors needed to address a deep list leaf."""
    node: etree._Element
    ancestors: tuple[etree._Element, ...] = ()
    exists: bool = True

    @property
    def path(self):
        return tuple(node.tag for node in self.ancestors) + (self.node.tag,)

    def text(self):
        return serialize_xml(self.node).decode("utf-8")


@dataclass
class EditPlan:
    edited: etree._Element
    rpc: etree._Element | None = None
    wire_xml: str = ""
    changed: set = field(default_factory=set)
    changes: list[str] = field(default_factory=list)
    removals: int = 0


def _copy_clean(node, deep=True):
    result = deepcopy(node)
    result.tail = None
    if not deep:
        for child in list(result):
            result.remove(child)
        result.text = None
    for item in result.iter():
        if isinstance(item.tag, str):
            for name in list(item.attrib):
                if name != XML_SPACE:
                    del item.attrib[name]
    return result


def _key_shell(node, info):
    result = _copy_clean(node, False)
    if info and info.kind == "list":
        if not info.keys:
            raise EditError("Cannot address a writable list without YANG keys.")
        for key in info.keys:
            matches = node.findall(key)
            if len(matches) != 1:
                raise EditError("Missing/duplicate list key: " + local(key))
            result.append(_copy_clean(matches[0]))
    return result


def build_plan(selection: Selection, text: str, schema: SchemaIndex, target="running",
               message_id: str | None = None) -> EditPlan:
    edited = parse_editor(text)
    plan = EditPlan(edited)
    before = selection.node
    if edited.tag != before.tag:
        raise EditError("Keep the selected root name and namespace unchanged.")
    if target not in {"running", "candidate", "startup"}:
        raise EditError("Unsupported edit target.")
    if selection.exists and semantic(before) == semantic(edited):
        return plan
    if not selection.exists and selection.ancestors:
        raise EditError("Only a root creation can use an absent baseline.")
    if not schema.complete:
        raise EditError("YANG schemas are incomplete. Load/refresh the device schemas before editing.")

    def require_writable(path):
        info = schema.lookup(path)
        if info is None or info.config is None:
            raise EditError("Unknown YANG node is read-only: " + "/".join(map(local, path)))
        if not info.config:
            raise EditError("config false cannot be modified: " + "/".join(map(local, path)))
        if info.kind in {"anyxml", "anydata"}:
            raise EditError("Opaque anyxml/anydata is read-only in the structured editor.")
        return info

    def check_structure(node, info):
        if any(key not in READ_ATTRIBUTES | {XML_SPACE} for key in node.attrib):
            raise EditError("Only YANG data values may be edited; custom XML attributes are not supported.")
        if info.kind in {"leaf", "leaf-list"} and children(node):
            raise EditError("A YANG leaf cannot contain child elements.")
        if info.kind in {"container", "list"} and (node.text or "").strip():
            raise EditError("Containers/lists cannot contain mixed XML text.")
        if any((child.tail or "").strip() for child in node):
            raise EditError("Mixed XML content is not supported for YANG configuration.")

    def added(node, path):
        info = require_writable(path)
        check_structure(node, info)
        if info.kind == "list":
            identity(node, schema, path)
            if not info.keys:
                raise EditError("Writable list requires YANG keys.")
        indexed(children(node), path)
        for child in children(node):
            added(child, path + (child.tag,))
        plan.changed.add(node)

    def indexed(nodes, path):
        result = {}
        for child in nodes:
            key = identity(child, schema, path + (child.tag,))
            if key in result:
                raise EditError("Duplicate or unidentifiable node: " + local(child.tag))
            result[key] = child
        return result

    def difference(old, new, path):
        if old is not None and new is not None and semantic(old) == semantic(new):
            return None
        node = new if new is not None else old
        info = require_writable(path)
        label = "/".join(map(local, path))
        if new is None:
            output = _key_shell(old, info)
            if info.kind == "leaf-list":
                output.text = old.text
            output.set("{%s}operation" % NC, "remove")
            plan.changes.append("REMOVE " + label)
            plan.removals += 1
            return output
        check_structure(new, info)
        if old is None:
            from .creation import validate_subtree
            validate_subtree(schema, new, path)
            added(new, path)
            output = _copy_clean(new)
            output.set("{%s}operation" % NC, "create")
            plan.changes.append("ADD " + label)
            return output
        if info.kind in {"leaf", "leaf-list"}:
            plan.changed.add(new)
            output = _copy_clean(new)
            output.set("{%s}operation" % NC, "merge")
            plan.changes.append("CHANGE " + label)
            return output
        if identity(old, schema, path) != identity(new, schema, path):
            raise EditError("List keys identify the selected instance; do not rename them in place.")
        old_items = indexed(children(old), path)
        new_items = indexed(children(new), path)
        # Reordering ordered-by user data requires yang:insert/yang:key handling.
        # Never silently pretend an order-only edit was sent.
        for tag in {child.tag for child in children(new)}:
            child_info = schema.lookup(path + (tag,))
            if child_info and child_info.ordered_by_user:
                expected_order = ([key for key in old_items if key[0] == tag and key in new_items]
                                  + [key for key in new_items if key[0] == tag and key not in old_items])
                new_order = [key for key in new_items if key[0] == tag]
                if expected_order != new_order:
                    raise EditError("Reordering ordered-by user lists is not supported by this editor.")
        output = _key_shell(new, info)
        key_tags = set(info.keys)
        count = 0
        for key in list(old_items) + [k for k in new_items if k not in old_items]:
            child = new_items.get(key)
            previous = old_items.get(key)
            tag = key[0]
            if tag in key_tags:
                continue
            patch = difference(previous, child, path + (tag,))
            if patch is not None:
                output.append(patch)
                count += 1
        if not count:
            raise EditError("Only value/add/remove edits are supported; namespace, attribute or ordering-only edits cannot be sent.")
        return output

    # Keys of the selected list, or a selected key leaf, must remain stable.
    selected_info = require_writable(selection.path)
    if selected_info.kind == "leaf-list" and value_identity(before) != value_identity(edited):
        raise EditError("To change a leaf-list value, select its parent so the preview can remove the old value and add the new one.")
    if selected_info.kind == "list" and identity(before, schema, selection.path) != identity(edited, schema, selection.path):
        raise EditError("The selected list key cannot be changed in place.")
    if selection.ancestors:
        parent = schema.lookup(selection.path[:-1])
        if parent and before.tag in parent.keys:
            raise EditError("List key leaves are identifiers and cannot be edited in place.")
    fragment = difference(before if selection.exists else None, edited, selection.path)
    if fragment is None:
        return plan
    for index in range(len(selection.ancestors) - 1, -1, -1):
        ancestor = selection.ancestors[index]
        info = require_writable(tuple(n.tag for n in selection.ancestors[:index + 1]))
        wrapper = _key_shell(ancestor, info)
        wrapper.append(fragment)
        fragment = wrapper
    rpc = etree.Element("{%s}rpc" % NC, nsmap={"nc": NC},
                        attrib={"message-id": message_id or "gui-" + uuid.uuid4().hex})
    operation = etree.SubElement(rpc, "{%s}edit-config" % NC)
    etree.SubElement(etree.SubElement(operation, "{%s}target" % NC), "{%s}%s" % (NC, target))
    etree.SubElement(operation, "{%s}default-operation" % NC).text = "none"
    etree.SubElement(operation, "{%s}config" % NC).append(fragment)
    # The same tree is passed to ncclient.xrpc; preserve both whitespace and id.
    # NETCONF framing (1.0 delimiter / 1.1 chunks) is outside the XML envelope.
    plan.rpc = etree.fromstring(serialize_xml(rpc))
    plan.wire_xml = to_xml(plan.rpc)
    return plan


def node_style(node, path, schema, changed=()):
    if node in changed:
        return "changed"
    info = schema.lookup(path)
    if info and info.config is False:
        return "state"
    if any(node.get(name) in {"true", "1"} for name in DEFAULT_ATTRIBUTES):
        return "default"
    if info and info.defaults and (node.text or "") in info.defaults:
        return "schema_default"
    if info is None:
        return "unknown"
    return ""


XML_TOKEN = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|</?[A-Za-z_][\w.:-]*(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", re.S)


def xml_spans(text, root):
    """Map actual source ranges to parsed nodes, including multiline leaf values."""
    nodes = iter(node for node in root.iter() if isinstance(node.tag, str))
    stack = []
    for match in XML_TOKEN.finditer(text):
        token = match.group()
        if token.startswith(("<!", "<?")):
            continue
        if token.startswith("</"):
            if stack:
                node, start, content_start = stack.pop()
                yield node, start, match.end(), content_start, match.start()
        else:
            node = next(nodes, None)
            if node is None:
                return
            if token.endswith("/>"):
                yield node, match.start(), match.end(), match.start(), match.end()
            else:
                stack.append((node, match.start(), match.end()))
