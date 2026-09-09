"""Schema-driven creation of local drafts. Never performs network operations."""
from copy import deepcopy
from dataclasses import dataclass
import re

from lxml import etree

from .model import EditError, Selection, children, identity, local


@dataclass(frozen=True)
class Candidate:
    info: object
    reason: str = ""

    @property
    def allowed(self):
        return not self.reason


def candidates(schema, parent, path):
    """Compare effective schema with a data parent (or the NETCONF data root)."""
    present = children(parent)
    active = {}
    for node in present:
        info = schema.lookup(path + (node.tag,))
        if info:
            for choice, case in info.choices:
                active.setdefault(choice, set()).add(case)
    result = []
    for info in schema.child_nodes(path):
        count = sum(n.tag == info.path[-1] for n in present)
        reason = ""
        if not schema.complete:
            reason = "Schema 不完整，禁止建立"
        elif info.config is not True:
            reason = "config false／唯讀"
        elif info.kind in {"anyxml", "anydata"}:
            reason = "結構化範本不支援 anyxml/anydata"
        elif count and info.kind not in {"list", "leaf-list"}:
            reason = "已存在；請選取後編輯"
        elif info.max_elements is not None and count >= info.max_elements:
            reason = "已達 max-elements"
        elif any(choice in active and active[choice] != {case} for choice, case in info.choices):
            reason = "另一個 choice 分支已存在；不會自動移除"
        result.append(Candidate(info, reason))
    return result


def namespace_map(schema):
    # Use module names as XML prefixes: deterministic across imported typedefs.
    return {module: uri for uri, module in schema.namespaces.items()}


def scalar_options(schema, info):
    stmt = schema.statements.get(info.path)
    typ = stmt.search_one("type") if stmt is not None else None
    spec = getattr(typ, "i_type_spec", None)
    name = getattr(spec, "name", info.type_name)
    if name == "boolean":
        return ("true", "false")
    if name == "enumeration":
        return tuple(name for name, _ in spec.enums)
    if name == "identityref":
        from pyang import types
        bases = [getattr(base, "i_identity", None) for base in spec.idbases]
        return tuple(sorted(module.arg + ":" + item.arg
            for module in schema.modules.values() for item in module.search("identity")
            if not getattr(item, "i_not_implemented", False)
            and all(base is not None and (item is base or types.is_derived_from(item, base)) for base in bases)))
    return info.defaults


def validate_scalar(schema, info, node):
    """Use compiled types, not textual constraint heuristics. XPath needs server."""
    stmt = schema.statements.get(info.path)
    typ = stmt.search_one("type") if stmt is not None else None
    spec = getattr(typ, "i_type_spec", None)
    if spec is None:
        return  # Hand-built diagnostic schemas may not carry pyang statements.
    text = node.text or ""
    name = getattr(spec, "name", "")
    if name == "empty":
        if text:
            raise EditError("empty 型別必須為空元素")
        return
    if name == "identityref":
        prefix, sep, value = text.partition(":")
        uri = node.nsmap.get(prefix) if sep else node.nsmap.get(None)
        module = schema.namespaces.get(uri)
        normalized = str(module) + ":" + (value if sep else prefix)
        if normalized not in scalar_options(schema, info):
            raise EditError("identityref 必須使用有效 identity 與 XML namespace prefix")
        return
    if name == "boolean" and text not in {"true", "false"}:
        raise EditError("boolean 請填 true 或 false")
    if re.fullmatch(r"u?int(8|16|32|64)", name):
        if not re.fullmatch(r"[+-]?[0-9]+", text):
            raise EditError("XML 整數必須為十進位")
        value = int(text, 10)
    else:
        errors = []
        value = spec.str_to_val(errors, stmt.pos, text, stmt.i_module)
        if errors:
            raise EditError("值不符合 YANG 型別 " + info.type_name)
    errors = []
    if not spec.validate(errors, stmt.pos, value, stmt.i_module) or errors:
        raise EditError("值不符合 YANG 型別／range／length／pattern：" + info.type_name)


class Template:
    """Unfilled required values are tracked separately, never as XML placeholders."""
    def __init__(self, schema, info):
        if not schema.complete or info.config is not True:
            raise EditError("請先載入完整且可寫的 YANG schema")
        self.schema = schema
        self.path = info.path
        self.pending = set()
        self.created_count = 0
        self.root = self._seed(info, 0)

    def _required(self, info, depth=0):
        if depth > 32:
            raise EditError("範本超過 32 層，請分段建立")
        if info.config is not True or info.choices or info.conditions:
            return False
        if info.mandatory or info.min_elements:
            return True
        return info.kind == "container" and not info.presence and (
            any(self._required(child, depth + 1) for child in self.schema.child_nodes(info.path))
            or any(c.parent == info.path and c.mandatory and not c.outer for c in self.schema.choices.values()))

    def _seed(self, info, depth):
        self.created_count += 1
        if depth > 32 or self.created_count > 512:
            raise EditError("範本太大，請分段建立")
        if info.kind not in {"container", "list", "leaf", "leaf-list"} or info.config is not True:
            raise EditError("此節點不支援結構化建立")
        node = etree.Element(info.path[-1], nsmap=namespace_map(self.schema))
        if info.kind in {"leaf", "leaf-list"}:
            stmt = self.schema.statements.get(info.path)
            typ = stmt.search_one("type") if stmt is not None else None
            name = getattr(getattr(typ, "i_type_spec", None), "name", info.type_name)
            if info.defaults:
                node.text = info.defaults[0]
                # Schema defaults may use YANG import prefixes, not XML prefixes.
                if name == "identityref":
                    raw = node.text
                    prefix, sep, ident = raw.partition(":")
                    owner, owner_type = stmt, typ
                    while owner.search_one("default") is None:
                        typedef = getattr(owner_type, "i_typedef", None)
                        if typedef is None:
                            break
                        owner, owner_type = typedef, typedef.search_one("type")
                    module = owner.i_module
                    module_name = module.i_prefixes.get(prefix, (None,))[0] if sep else module.arg
                    node.text = str(module_name) + ":" + (ident if sep else prefix)
            elif name != "empty":
                self.pending.add(node)
        else:
            for child in self.schema.child_nodes(info.path):
                if child.path[-1] in info.keys or self._required(child):
                    count = max(1, child.min_elements)
                    if count > 64:
                        raise EditError("min-elements 過大（>64），請使用人工 XML 草稿")
                    for _ in range(count):
                        node.append(self._seed(child, depth + 1))
        return node

    def node_path(self, node):
        tags = []
        current = node
        while current is not self.root:
            tags.append(current.tag)
            current = current.getparent()
            if current is None:
                raise EditError("節點不屬於此範本")
        return self.path + tuple(reversed(tags))

    def add(self, parent, info):
        path = self.node_path(parent)
        item = next((c for c in candidates(self.schema, parent, path) if c.info == info), None)
        if item is None or not item.allowed:
            raise EditError(item.reason if item else "不是此層可新增節點")
        node = self._seed(info, 0)
        parent.append(node)
        return node

    def set_value(self, node, value):
        info = self.schema.lookup(self.node_path(node))
        if info.kind not in {"leaf", "leaf-list"}:
            raise EditError("請選擇 leaf 或 leaf-list")
        candidate = deepcopy(node)
        candidate.text = value
        validate_scalar(self.schema, info, candidate)
        node.text = value or None
        self.pending.discard(node)

    def issues(self):
        issues = []
        live = set(self.root.iter())
        for node in self.pending & live:
            issues.append("待填：" + "/".join(map(local, self.node_path(node))))
        try:
            validate_subtree(self.schema, self.root, self.path)
        except EditError as exc:
            issues.append(str(exc))
        return issues


def validate_subtree(schema, node, path):
    """Check new data locally; deliberately do not claim XPath/NACM validation."""
    info = schema.lookup(path)
    if info is None or info.config is not True:
        raise EditError("未知或唯讀節點：" + local(node.tag))
    if info.kind in {"leaf", "leaf-list"}:
        validate_scalar(schema, info, node)
        return
    if info.kind not in {"container", "list"}:
        raise EditError("不支援 opaque 節點範本")
    active = {}
    seen = set()
    for child in children(node):
        child_path = path + (child.tag,)
        child_info = schema.lookup(child_path)
        if child_info is None:
            raise EditError("未知節點：" + local(child.tag))
        key = identity(child, schema, child_path)
        if key in seen:
            raise EditError("重複節點／list key／leaf-list 值：" + local(child.tag))
        seen.add(key)
        for choice, case in child_info.choices:
            if choice in active and active[choice] != case:
                raise EditError("choice 不可同時存在不同分支")
            active[choice] = case
        validate_subtree(schema, child, child_path)
    for choice_key, choice in schema.choices.items():
        if choice.parent == path and choice.mandatory and not choice.conditions and all(active.get(c) == case for c, case in choice.outer):
            if choice_key not in active:
                raise EditError("請選擇必填 choice 分支：" + choice.name)
    for child in schema.child_nodes(path):
        if child.config is not True:
            continue
        count = len(node.findall(child.path[-1]))
        branch = all(active.get(c) == case for c, case in child.choices)
        if not branch or child.conditions:
            continue  # Conditional presence is checked by the server.
        required = child.mandatory or child.path[-1] in info.keys
        if count < max(int(required), child.min_elements):
            raise EditError("缺少必填／min-elements：" + local(child.path[-1]))
        if child.max_elements is not None and count > child.max_elements:
            raise EditError("超過 max-elements：" + local(child.path[-1]))
        if not count and child.kind == "container" and not child.presence:
            validate_subtree(schema, etree.Element(child.path[-1]), child.path)


def append_template(schema, parent, path, template):
    issues = template.issues()
    if issues:
        raise EditError("\n".join(issues[:8]))
    candidate = next((c for c in candidates(schema, parent, path) if c.info.path == template.path), None)
    if candidate is None or not candidate.allowed:
        raise EditError(candidate.reason if candidate else "範本不是此層子節點")
    wanted = identity(template.root, schema, template.path)
    if any(identity(node, schema, template.path) == wanted for node in children(parent) if node.tag == template.root.tag):
        raise EditError("此節點／list key／leaf-list 值已存在")
    result = deepcopy(parent)
    result.append(deepcopy(template.root))
    return result


def new_root_selection(node, schema):
    """A virtual baseline is never inserted into the server snapshot."""
    info = schema.lookup((node.tag,))
    baseline = etree.Element(node.tag, nsmap=node.nsmap)
    if info.kind == "list":
        for key in info.keys:
            baseline.append(deepcopy(node.find(key)))
    elif info.kind == "leaf-list":
        baseline.text = node.text
    return Selection(baseline, exists=False)
