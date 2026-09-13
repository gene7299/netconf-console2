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


@dataclass(frozen=True)
class ChoiceCandidate:
    """A concrete data node that represents one branch of a YANG choice.

    ``choice``/``branch`` are schema metadata only: YANG choices and cases do
    not appear as XML elements.  Keeping the owning parent here lets the UI
    add a branch even when the user currently has a leaf selected.
    """

    parent: object
    parent_path: tuple[str, ...]
    choice_key: str
    choice: object
    branch: str
    candidate: Candidate

    @property
    def info(self):
        return self.candidate.info

    @property
    def allowed(self):
        return self.candidate.allowed

    @property
    def label(self):
        return "%s → %s | %s:%s (%s)" % (
            self.choice.name, self.branch, self.info.module, local(self.info.path[-1]), self.info.kind)


def choice_label(schema, info):
    """Return human-readable choice/case labels for a data node."""
    labels = []
    for key, branch in info.choices:
        choice = schema.choices.get(key)
        labels.append((choice.name + " → " if choice else "") + branch)
    return ", ".join(labels)


def choice_candidates(schema, parent, path, *, mandatory_only=True):
    """List concrete branch nodes that can be added at ``parent``.

    A choice/case is schema-only and therefore cannot be selected as an XML
    node.  This helper resolves it to the actual first data node in each case,
    which is what ``Template.add`` needs.  It is intentionally based on the
    same ``candidates`` result used by the normal child picker so max-elements,
    config and feature checks cannot diverge between the two UI paths.
    """
    present = children(parent)
    active = {}
    for node in present:
        info = schema.lookup(path + (node.tag,))
        if info:
            for key, branch in info.choices:
                active.setdefault(key, set()).add(branch)
    result = []
    direct = candidates(schema, parent, path)
    for key, choice in schema.choices.items():
        if choice.parent != path:
            continue
        if mandatory_only and not choice.mandatory:
            continue
        if choice.conditions or key in active:
            continue
        if not all(active.get(outer_key) == {outer_branch}
                   for outer_key, outer_branch in choice.outer):
            continue
        for candidate in direct:
            branches = [branch for choice_key, branch in candidate.info.choices if choice_key == key]
            for branch in branches:
                result.append(ChoiceCandidate(parent, path, key, choice, branch, candidate))
    return result


def missing_choice_candidates(schema, root, path):
    """Find unresolved mandatory choices throughout a creation template."""
    result = []

    def walk(node, node_path):
        info = schema.lookup(node_path)
        if info is None or info.kind not in {"container", "list"}:
            return
        result.extend(choice_candidates(schema, node, node_path))
        for child in children(node):
            walk(child, node_path + (child.tag,))

    walk(root, path)
    return result


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


def suggested_values(schema, info, reference_values=()):
    """Return non-binding values that can help a user fill a scalar leaf."""
    if info.kind not in {"leaf", "leaf-list"}:
        return ()
    values = [*info.defaults, *scalar_options(schema, info), *(reference_values or ())]
    return tuple(dict.fromkeys(str(value) for value in values if value is not None))


def validate_scalar(schema, info, node):
    """Use compiled types, not textual constraint heuristics. XPath needs server."""
    stmt = schema.statements.get(info.path)
    typ = stmt.search_one("type") if stmt is not None else None
    spec = getattr(typ, "i_type_spec", None)
    if spec is None:
        return  # Hand-built diagnostic schemas may not carry pyang statements.
    text = node.text or ""
    name = getattr(spec, "name", "")
    if name == "leafref":
        target = getattr(spec, "i_target_node", None)
        target_path = next((path for path, statement in schema.statements.items() if statement is target), None)
        if target_path and target_path != info.path:
            return validate_scalar(schema, schema.lookup(target_path), node)
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


def scalar_input(schema, info, node, value):
    """Bind a canonical module:identity choice locally without rebinding siblings."""
    result = deepcopy(node)
    effective = info
    visited = set()
    while effective.path not in visited:
        visited.add(effective.path)
        stmt = schema.statements.get(effective.path)
        typ = stmt.search_one("type") if stmt is not None else None
        spec = getattr(typ, "i_type_spec", None)
        if getattr(spec, "name", "") != "leafref":
            break
        target = getattr(spec, "i_target_node", None)
        path = next((path for path, s in schema.statements.items() if s is target), None)
        if path is None:
            break
        effective = schema.lookup(path)
    if getattr(spec, "name", "") == "identityref" and value in scalar_options(schema, effective):
        module, ident = value.split(":", 1)
        uri = next(uri for uri, name in schema.namespaces.items() if name == module)
        prefix = next((p for p, u in node.nsmap.items() if p and u == uri), None)
        if prefix is None:
            prefix = module
            while prefix in node.nsmap and node.nsmap[prefix] != uri:
                prefix += "_yang"
            result = etree.Element(node.tag, nsmap={**node.nsmap, prefix: uri}, attrib=dict(node.attrib))
            result.tail = node.tail
        value = prefix + ":" + ident
    result.text = value or None
    validate_scalar(schema, info, result)
    return result


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
