"""Pure workspace helpers: instance-aware diffs, safe import and schema search."""
from copy import deepcopy
from dataclasses import dataclass
import difflib

from lxml import etree

from ..xmloutput import serialize_xml
from .model import EditError, NC, Selection, children, identity, local, parse_editor, semantic


def instance_path(selection, schema):
    parts = []
    chain = (*selection.ancestors, selection.node)
    for index, node in enumerate(chain):
        info = schema.lookup(tuple(n.tag for n in chain[:index + 1]))
        module = schema.namespaces.get(etree.QName(node).namespace, etree.QName(node).namespace or "")
        label = module + ":" + local(node.tag)
        if info and info.keys:
            label += "[" + ", ".join(local(k) + "=" + repr(node.findtext(k)) for k in info.keys) + "]"
        if info and info.kind == "leaf-list":
            label += "[.=" + repr(node.text or "") + "]"
        parts.append(label)
    return "/" + "/".join(parts)


def selections(data):
    def walk(node, ancestors):
        yield Selection(node, ancestors)
        for child in children(node):
            yield from walk(child, (*ancestors, node))
    for node in children(data):
        yield from walk(node, ())


def search_snapshot(data, schema, query, limit=500):
    query = query.strip().casefold()
    if not query:
        return []
    results = []
    for selection in selections(data):
        path = instance_path(selection, schema)
        info = schema.lookup(selection.path)
        haystack = path + " " + (selection.node.text or "") + " " + (info.description if info else "")
        if query in haystack.casefold():
            results.append((path, selection))
            if len(results) >= limit:
                break
    return results


def xml_diff(before, after, before_name="原始值", after_name="修改後"):
    return "".join(difflib.unified_diff(
        serialize_xml(before).decode("utf-8").splitlines(keepends=True),
        serialize_xml(after).decode("utf-8").splitlines(keepends=True),
        fromfile=before_name, tofile=after_name)) or "沒有文字差異。"


@dataclass(frozen=True)
class ValueChange:
    path: str
    before: str
    after: str


def value_changes(selection, edited, schema):
    """Keys are part of each path; never merge values from different list instances."""
    def flatten(root):
        result = {}
        def walk(node, ancestors):
            if not children(node):
                result[instance_path(Selection(node, ancestors), schema)] = node.text or ""
            for child in children(node):
                walk(child, (*ancestors, node))
        walk(root, selection.ancestors)
        return result
    old, new = flatten(selection.node) if selection.exists else {}, flatten(edited)
    return [ValueChange(path, old.get(path, "（不存在）"), new.get(path, "（移除）"))
            for path in dict.fromkeys((*old, *new))
            if path not in old or path not in new or old[path] != new[path]]


def import_selection(raw, selection, schema):
    """Import selected subtree or its unique instance in data/config/rpc-reply.

    Omitted writable values mean deletion within the selected subtree. Existing
    state/unknown nodes are preserved and never copied from another device.
    """
    if len(raw) > 32 * 1024 * 1024:
        raise EditError("XML 匯入上限為 32 MiB。請先拆成較小的 subtree。")
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    parsed = etree.fromstring(raw, parser)
    if parsed.getroottree().docinfo.doctype:
        raise EditError("DOCTYPE/entities are not allowed.")
    parsed = parse_editor(etree.tostring(parsed, encoding="unicode"))
    if not schema.complete:
        raise EditError("請先載入完整 YANG schema。")
    if parsed.tag == selection.node.tag:
        imported = parsed
    else:
        if parsed.tag == "{%s}rpc-reply" % NC:
            parsed = parsed.find("{%s}data" % NC)
        if parsed is None or parsed.tag not in {"{%s}data" % NC, "{%s}config" % NC, "data", "config"}:
            raise EditError("請匯入選取節點、data、config 或含 data 的 rpc-reply；不接受 RPC。")
        parent = parsed
        for depth, original in enumerate((*selection.ancestors, selection.node), 1):
            path = selection.path[:depth]
            wanted = identity(original, schema, path)
            matches = [n for n in children(parent) if n.tag == original.tag and identity(n, schema, path) == wanted]
            if len(matches) != 1:
                raise EditError("匯入檔找不到唯一的選取 instance；請確認 list key 與 namespace。")
            parent = matches[0]
        imported = parent
    info = schema.lookup(selection.path)
    if info is None or info.config is not True:
        raise EditError("只能匯入 config true 節點。")

    def clean(new, old, path):
        result = deepcopy(new)
        for node in list(result):
            result.remove(node)
        for node in children(new):
            child_path = path + (node.tag,)
            child_info = schema.lookup(child_path)
            if child_info is None:
                raise EditError("匯入包含未知 YANG 節點：" + local(node.tag))
            if child_info.config is not True:
                continue
            matches = [] if old is None else [n for n in children(old)
                if n.tag == node.tag and identity(n, schema, child_path) == identity(node, schema, child_path)]
            result.append(clean(node, matches[0] if len(matches) == 1 else None, child_path))
        if old is not None:
            for index, node in enumerate(children(old)):
                child_info = schema.lookup(path + (node.tag,))
                if child_info is None or child_info.config is not True:
                    result.insert(min(index, len(result)), deepcopy(node))
        return result
    return clean(imported, selection.node, selection.path)
