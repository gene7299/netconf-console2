"""Key-aware three-way comparisons and explicit conflict resolution, local only."""
from copy import deepcopy
from dataclasses import dataclass

from lxml import etree

from .client import config_semantic, locate
from .model import EditError, Selection, build_plan, children, identity
from .workspace import instance_path


@dataclass
class Row:
    address: tuple
    path: str
    before: object
    latest: object
    mine: object
    choice: str
    status: str


def equal(a, b, path, schema):
    if a is None or b is None:
        return a is b
    return config_semantic(a, path, schema) == config_semantic(b, path, schema)


def find_optional(data, selection, schema):
    # Absence is allowed; ambiguous instances and missing ancestors are not guessed.
    parent = data
    for depth, old in enumerate((*selection.ancestors, selection.node), 1):
        path = selection.path[:depth]
        wanted = identity(old, schema, path)
        matches = [n for n in children(parent) if n.tag == old.tag and identity(n, schema, path) == wanted]
        if len(matches) > 1:
            raise EditError("設備回覆定位不唯一")
        if not matches:
            return None
        parent = matches[0]
    return parent


def compare(selection, mine, data, schema, target):
    build_plan(selection, etree.tostring(mine).decode(), schema, target)
    latest = find_optional(data, selection, schema)
    rows = []
    def walk(old, actual, wanted, path, ancestors, address):
        info = schema.lookup(path)
        if not info or info.config is not True:
            return
        if equal(old, actual, path, schema) and equal(old, wanted, path, schema):
            return
        if old is None or actual is None or wanted is None or info.kind in {"leaf", "leaf-list"}:
            if equal(actual, wanted, path, schema):
                choice, status = "latest", "已符合草稿"
            elif equal(old, actual, path, schema):
                choice, status = "mine", "可套用草稿"
            elif equal(old, wanted, path, schema):
                choice, status = "latest", "僅設備變更"
            else:
                choice, status = "", "衝突：請選擇"
            node = wanted if wanted is not None else actual if actual is not None else old
            rows.append(Row(address, instance_path(Selection(node, ancestors), schema),
                            deepcopy(old), deepcopy(actual), deepcopy(wanted), choice, status))
            return
        maps = []
        for parent in (old, actual, wanted):
            mapping = {identity(n, schema, path + (n.tag,)): n for n in children(parent)}
            if len(mapping) != len(children(parent)):
                raise EditError("資料包含重複節點／list key，不能可靠比對")
            maps.append(mapping)
        for key in dict.fromkeys(k for mapping in maps for k in mapping):
            walk(*(mapping.get(key) for mapping in maps), path + (key[0],), (*ancestors, actual), (*address, key))
    walk(selection.node if selection.exists else None, latest, mine, selection.path, selection.ancestors, ())
    return latest, rows


def resolve_rows(selection, latest, rows, schema, target):
    if any(row.choice not in {"latest", "mine"} for row in rows):
        raise EditError("請逐項選擇尚未處理的衝突")
    if latest is None and selection.ancestors:
        raise EditError("選取節點／祖先已消失，請重新讀取父節點後建立草稿")
    edited = deepcopy(latest)
    for row in rows:
        if row.choice != "mine":
            continue
        value = deepcopy(row.mine)
        if not row.address:
            edited = value
            continue
        parent, path = edited, selection.path
        for key in row.address[:-1]:
            matches = [n for n in children(parent) if identity(n, schema, path + (n.tag,)) == key]
            if len(matches) != 1:
                raise EditError("合併定位不唯一，請重新比對")
            parent = matches[0]
            path += (parent.tag,)
        key = row.address[-1]
        matches = [n for n in children(parent) if identity(n, schema, path + (n.tag,)) == key]
        if len(matches) > 1:
            raise EditError("合併定位不唯一")
        if matches:
            if value is None:
                parent.remove(matches[0])
            else:
                parent.replace(matches[0], value)
        elif value is not None:
            parent.append(value)
    if edited is None:
        raise EditError("目前範圍已移除；請刪除此草稿並重新讀取父節點")
    if latest is None:
        from .creation import new_root_selection
        baseline = new_root_selection(edited, schema)
    else:
        baseline = Selection(deepcopy(latest), selection.ancestors)
    text = etree.tostring(edited, encoding="unicode")
    build_plan(baseline, text, schema, target)
    return baseline, text


def display(node):
    if node is None:
        return "（不存在）"
    if children(node):
        return "（子樹；選取查看 XML）"
    return node.text or "（空值）"
