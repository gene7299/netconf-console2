"""YANG-derived notification fields and RFC 6241 content-match filters.

The bundled catalogue is generated from MP v17.01 YANG, not a claim of DUT
support. Runtime schema discovery can supply a different module revision.
"""
from functools import lru_cache
from itertools import product
from importlib.resources import files
from decimal import Decimal, InvalidOperation
import json
import math
import re

from lxml import etree

from .model import EditError

MAX_BRANCHES = 128


def _text(stmt, keyword, default=""):
    child = stmt.search_one(keyword) if stmt is not None else None
    return child.arg if child is not None else default


def _type_details(stmt):
    typ = stmt.search_one("type")
    original = typ.arg if typ is not None else "unknown"
    seen = set()
    constraints = {}
    while typ is not None and id(typ) not in seen:
        seen.add(id(typ))
        for key in ("range", "length", "pattern", "fraction-digits"):
            value = _text(typ, key)
            if value:
                constraints.setdefault(key, value)
        spec = getattr(typ, "i_type_spec", None)
        name = getattr(spec, "name", typ.arg)
        if name == "leafref":
            target = getattr(spec, "i_target_node", None)
            if target is not None:
                typ = target.search_one("type")
                continue
        typedef = getattr(typ, "i_typedef", None)
        if typedef is not None:
            typ = typedef.search_one("type")
            continue
        values = []
        if name == "enumeration":
            values = [s.arg for s in typ.search("enum")
                      if _text(s, "status") != "obsolete"]
        elif name == "boolean":
            values = ["true", "false"]
        return original, name, values, constraints
    return original, original, [], constraints


def notification_catalog(modules):
    """Use pyang-expanded children, including uses/typedef/leafref chains."""
    result = {}
    for module in modules.values():
        namespace = _text(module, "namespace")
        if not namespace:
            continue
        for event in getattr(module, "i_children", ()):
            if event.keyword != "notification" or _text(event, "status") == "obsolete":
                continue
            fields = []

            def walk(parent, path=(), tags=(), conditions=(), choices=()):
                for node in getattr(parent, "i_children", ()):
                    if _text(node, "status") == "obsolete" or getattr(node, "i_not_implemented", False):
                        continue
                    guards = conditions + tuple(s.keyword + ": " + s.arg for s in node.substmts
                                                if s.keyword in {"if-feature", "when"})
                    if node.keyword in {"choice", "case"}:
                        branch = choices
                        if node.keyword == "case":
                            branch += (("/".join(path) + "/" + node.parent.arg, node.arg),)
                        walk(node, path, tags, guards, branch)
                        continue
                    if node.keyword not in {"container", "list", "leaf", "leaf-list"}:
                        continue
                    ns = _text(node.i_module, "namespace", namespace)
                    p, t = path + (node.arg,), tags + ("{%s}%s" % (ns, node.arg),)
                    if node.keyword in {"container", "list"}:
                        walk(node, p, t, guards, choices)
                    else:
                        declared, base, values, constraints = _type_details(node)
                        fields.append(dict(path=p, tags=t, type=declared, base=base,
                            values=values, constraints=constraints,
                            description=" ".join(_text(node, "description").split()),
                            conditions=guards, choices=choices,
                            required=_text(node, "mandatory") == "true"))

            walk(event, conditions=tuple("if-feature: " + s.arg for s in event.search("if-feature")))
            result[event.arg] = dict(name=event.arg, module=module.arg,
                namespace=namespace, revision=_text(module, "revision"), fields=fields,
                description=" ".join(_text(event, "description").split()))
    return result


@lru_cache(maxsize=1)
def bundled_catalog():
    return json.loads(files(__package__).joinpath("assets/notification_catalog.json").read_text(encoding="utf-8"))


def _in_ranges(value, expression, low, high):
    for part in expression.split("|"):
        ends = part.strip().split("..")
        bounds = [low if v.strip() == "min" else high if v.strip() == "max" else int(v) for v in ends]
        if bounds[0] <= value <= bounds[-1]:
            return True
    return False


def validate_value(field, value):
    name = "/".join(field["path"])
    if not value.strip():
        raise EditError(name + "：請選擇或輸入值；空白表示不限制。")
    if field["values"] and value not in field["values"]:
        raise EditError(name + "：值不在此 YANG 列舉內。")
    base, constraints = field["base"], field["constraints"]
    integer = re.fullmatch(r"(u?)int(8|16|32|64)", base)
    if integer:
        bits = int(integer[2])
        low, high = (0, 2 ** bits - 1) if integer[1] else (-2 ** (bits-1), 2 ** (bits-1)-1)
        if not re.fullmatch(r"[+-]?[0-9]+", value) or not low <= int(value) <= high:
            raise EditError(name + "：請輸入 %d..%d 的整數。" % (low, high))
        if constraints.get("range") and not _in_ranges(int(value), constraints["range"], low, high):
            raise EditError(name + "：需符合 range " + constraints["range"])
    if constraints.get("length") and not _in_ranges(len(value), constraints["length"], 0, 2**64-1):
        raise EditError(name + "：需符合 length " + constraints["length"])
    if base == "decimal64":
        try:
            fraction = int(constraints.get("fraction-digits", "18"))
            number = Decimal(value)
            scaled = number * 10**fraction
            if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value) or not number.is_finite() or scaled != scaled.to_integral_value() or not -2**63 <= scaled <= 2**63-1:
                raise ValueError()
        except (ValueError, InvalidOperation):
            raise EditError(name + "：需符合 decimal64，最多 %s 位小數。" % constraints.get("fraction-digits", "18")) from None
    if "date-and-time" in field["type"]:
        from .events import timestamp
        timestamp(value)


def notification_filter(definition, selections, whole_groups=()):
    """Same-field alternatives become sibling event branches (OR).

    Leaf predicates within one sibling set are content-match conditions (AND).
    Repeated list entries follow RFC subtree semantics; this is not XPath.
    """
    if definition["name"] == "measurement-result-stats":
        groups = dict.fromkeys(whole_groups)
        for path, values in selections.items():
            if values:
                groups.setdefault(path[0], None)
        if groups:
            parts = []
            for group in groups:
                if group in whole_groups:
                    if group not in bundled_catalog()["measurement_groups"]:
                        raise EditError("未知 Measurement group：" + group)
                    root = etree.Element("{%s}%s" % (definition["namespace"], definition["name"]),
                                         nsmap={None: definition["namespace"]})
                    etree.SubElement(root, "{%s}%s" % (definition["namespace"], group))
                    parts.append(etree.tostring(root, encoding="unicode", pretty_print=True).rstrip())
                else:
                    terms = {path: values for path, values in selections.items() if path[0] == group}
                    parts.append(_notification_filter(definition, terms))
            return "\n".join(parts)
    return _notification_filter(definition, selections)


def _notification_filter(definition, selections):
    by_path = {tuple(f["path"]): f for f in definition["fields"]}
    terms, cases = [], {}
    for path, values in selections.items():
        path, values = tuple(path), list(dict.fromkeys(values))
        if not values:
            continue
        if path not in by_path:
            raise EditError("此 YANG 沒有欄位：" + "/".join(path))
        field = by_path[path]
        for value in values:
            validate_value(field, value)
        for choice, case in field.get("choices", ()):
            if choice in cases and cases[choice] != case:
                raise EditError("所選欄位屬於互斥的 YANG choice，請分開訂閱：" + choice)
            cases[choice] = case
        terms.append((field, values))
    if math.prod(len(values) for _, values in terms) > MAX_BRANCHES:
        raise EditError("條件組合超過 128 個分支，請縮小選取範圍。")
    roots = []
    for values in product(*(v for _, v in terms)):
        root = etree.Element("{%s}%s" % (definition["namespace"], definition["name"]),
                             nsmap={None: definition["namespace"]})
        for (field, _), value in zip(terms, values):
            node = root
            for tag in field["tags"]:
                child = node.find(tag)
                if child is None:
                    child = etree.SubElement(node, tag)
                node = child
            node.text = value
        roots.append(etree.tostring(root, encoding="unicode", pretty_print=True).rstrip())
    return "\n".join(roots)


def discover_alarm_values(manager):
    ns = "urn:o-ran:fm:1.0"
    root = etree.Element("{%s}active-alarm-list" % ns, nsmap={None: ns})
    alarms = manager.get(filter=("subtree", root)).data.findall(".//{%s}active-alarms" % ns)
    values = {}
    def walk(node, path=()):
        for child in node:
            if not isinstance(child.tag, str) or etree.QName(child).namespace != ns:
                continue
            p = path + (etree.QName(child).localname,)
            if len(child):
                walk(child, p)
            elif child.text:
                values.setdefault(p, set()).add(child.text)
    for alarm in alarms:
        walk(alarm)
    return len(alarms), values
