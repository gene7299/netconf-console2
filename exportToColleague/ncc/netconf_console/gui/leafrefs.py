"""Context-aware leafref suggestions from visible data, never existence guesses."""
from copy import deepcopy
from dataclasses import dataclass, field
import re

from lxml import etree

from .client import locate
from .model import EditError, Selection, children


@dataclass
class ReferenceResult:
    paths: list[str] = field(default_factory=list)
    targets: list[Selection] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def specs(schema, info):
    stmt = schema.statements.get(info.path)
    typ = stmt.search_one("type") if stmt is not None else None
    def walk(current):
        spec = getattr(current, "i_type_spec", None)
        if getattr(spec, "name", None) == "leafref" and hasattr(spec, "path_"):
            yield spec
        elif getattr(spec, "name", None) == "union":
            for member in spec.types:
                yield from walk(member)
    return list(walk(typ))


def qualify_path(spec, schema):
    path_stmt = spec.path_
    module = path_stmt.i_module
    namespaces = {}
    for prefix, (module_name, _revision) in module.i_prefixes.items():
        target = schema.modules.get(module_name)
        ns = target.search_one("namespace") if target is not None else None
        if ns is not None:
            namespaces[prefix] = ns.arg
    namespace = module.search_one("namespace")
    if namespace is None:
        raise EditError("leafref 定義 module namespace 不明")
    default = "_ncc_ref"
    while default in namespaces:
        default += "_"
    namespaces[default] = namespace.arg
    expression = path_stmt.arg
    # YANG leafref path grammar allows node identifiers, ../ and predicates
    # with current(). Never substitute inside namespaced tokens or literals.
    token = re.compile(r"'[^']*'|\"[^\"]*\"|[A-Za-z_][A-Za-z0-9_.-]*(?::[A-Za-z_][A-Za-z0-9_.-]*)?")
    def replace(match):
        value = match.group()
        if value.startswith(("'", '"')) or ":" in value or value == "current":
            return value
        return default + ":" + value
    expression = token.sub(replace, expression)
    if expression.startswith("/"):
        expression = "/*" + expression  # NETCONF <data> is an artificial wrapper.
    return expression, namespaces


def resolve(schema, data, selection, edited, indices=(), limit=200):
    """Overlay this local subtree, then evaluate in the selected leaf's context."""
    result = ReferenceResult()
    root = deepcopy(data)
    replacement = deepcopy(edited)
    if selection.exists:
        try:
            current = locate(root, selection, schema)
        except EditError:
            result.messages.append("目前快照無法定位草稿上下文，待伺服器驗證")
            return result
        current.getparent().replace(current, replacement)
    else:
        root.append(replacement)
    node, path = replacement, selection.path
    for index in indices:
        node = children(node)[index]
        path += (node.tag,)
    info = schema.lookup(path)
    if info is None:
        return result
    references = specs(schema, info)
    for spec in references:
        result.paths.append(spec.path_.arg)
        result.messages.append("require-instance=" + str(getattr(spec, "require_instance", True)).lower())
        try:
            expression, nsmap = qualify_path(spec, schema)
            found = node.xpath(expression, namespaces=nsmap, extensions={(None, "current"): lambda _context: [node]})
            if not isinstance(found, list) or any(not isinstance(n, etree._Element) for n in found):
                raise EditError("leafref 結果不是節點集合")
            for target in found:
                if len(result.targets) >= limit:
                    result.messages.append("候選超過 %d 筆，已截斷" % limit)
                    break
                ancestors = []
                parent = target.getparent()
                while parent is not root and parent is not None:
                    ancestors.append(parent)
                    parent = parent.getparent()
                if parent is None:
                    continue
                target_selection = Selection(target, tuple(reversed(ancestors)))
                target_info = schema.lookup(target_selection.path)
                if target_info is None or target_info.kind not in {"leaf", "leaf-list"}:
                    continue
                result.targets.append(target_selection)
                value = target.text or ""
                # Preserve QName bindings in values referenced from identityrefs.
                if ":" in value:
                    prefix, local = value.split(":", 1)
                    uri = target.nsmap.get(prefix)
                    if uri and replacement.nsmap.get(prefix) != uri:
                        alias = next((p for p, u in replacement.nsmap.items() if p and u == uri), None)
                        if alias is None:
                            result.messages.append("部分候選需要先宣告 XML namespace prefix")
                            continue
                        value = alias + ":" + local
                if value not in result.values:
                    result.values.append(value)
        except (etree.XPathError, EditError, AttributeError, KeyError):
            result.messages.append("無法可靠解析此 leafref，待伺服器驗證")
    if references:
        result.messages.append("候選僅代表可見快照／此份草稿；未列出不等於不存在（NACM、default 或尚未讀取）。完整條件待伺服器驗證。")
    for ancestor_depth in range(1, len(path) + 1):
        parent_info = schema.lookup(path[:ancestor_depth])
        if parent_info and parent_info.conditions:
            result.messages.extend("when: " + condition for condition in parent_info.conditions)
    return result
