"""Per-leaf decimal checks shared by editors, templates and outgoing XML."""
from functools import lru_cache
from importlib.resources import files
import json

from ..yang_decimal import Decimal64Error, Decimal64Rules, decimal_members, decimal_rules
from .model import EditError, NC, children


@lru_cache(maxsize=1)
def reference_rules():
    catalog = json.loads(files(__package__).joinpath("assets/decimal64_v15.json").read_text(encoding="utf-8"))
    return {tuple(row["path"]): Decimal64Rules.from_constraints(row["constraints"])
            for row in catalog["leaves"] if row["scope"] == "data"}


def rules_for(schema, path, reference=False):
    statement = schema.statements.get(path)
    typ = statement.search_one("type") if statement is not None else None
    rules = decimal_rules(getattr(typ, "i_type_spec", None))
    # A loaded DUT statement always takes precedence, including a different
    # non-decimal type. Reference rules only seed offline-generated templates.
    if rules is not None or statement is not None or not reference:
        return rules
    return reference_rules().get(path)


def validate_decimal_statement(statement, text, label):
    typ = statement.search_one("type") if statement is not None else None
    spec = getattr(typ, "i_type_spec", None)
    rules = decimal_rules(spec)
    try:
        if rules is not None:
            rules.validate(text, label)
        elif decimal_members(spec):
            def accepts(current, seen=()):
                if current is None or id(current) in seen:
                    return False
                seen += (id(current),)
                direct = decimal_rules(current)
                if direct is not None:
                    try:
                        direct.validate(text, label)
                        return True
                    except Decimal64Error:
                        return False
                if current.name == "union":
                    return any(accepts(member.i_type_spec, seen) for member in current.types)
                if current.name == "leafref" and hasattr(current, "i_target_node"):
                    return accepts(current.i_target_node.search_one("type").i_type_spec, seen)
                errors = []
                value = current.str_to_val(errors, statement.pos, text, statement.i_module)
                return not errors and value is not None and bool(current.validate(errors, statement.pos, value, statement.i_module)) and not errors
            if not accepts(spec):
                raise Decimal64Error(label + ": no YANG union member accepts this value")
    except Decimal64Error as exc:
        raise EditError(str(exc)) from None


def validate_decimal_tree(root, schema, path=(), content_match=False):
    current = path + (root.tag,)
    operation = root.get("{%s}operation" % NC)
    if not children(root):
        if operation in {"remove", "delete"} and not root.text:
            return
        if content_match and not root.text:
            return  # Empty selection nodes in a NETCONF subtree filter.
        statement = schema.statements.get(current) or schema.value_statements.get(current)
        validate_decimal_statement(statement, root.text or "", "/" + "/".join(current))
    else:
        for child in children(root):
            validate_decimal_tree(child, schema, current, content_match)


def decimal_diagnostics(data, schema):
    """Report invalid received values without rounding or dropping device data."""
    errors = {}
    def walk(node, path):
        path += (node.tag,)
        if not children(node):
            try:
                validate_decimal_statement(schema.statements.get(path), node.text or "", "/" + "/".join(path))
            except EditError as exc:
                errors[path] = str(exc) + "; received " + repr(node.text or "")
        for child in children(node):
            walk(child, path)
    for node in children(data):
        walk(node, ())
    return errors


def validate_rpc_decimals(rpc, schema):
    """Validate known decimal leaves only; preserve opaque RPC passthrough."""
    if schema is None:
        return
    operation = children(rpc)[0]
    if operation.tag in {"{%s}edit-config" % NC, "{%s}copy-config" % NC}:
        config = operation.find("{%s}config" % NC)
        if config is None:
            config = operation.find("{%s}source/{%s}config" % (NC, NC))
        if config is not None:
            for root in children(config):
                validate_decimal_tree(root, schema)
    for filter_node in operation.iter("{%s}filter" % NC,
                                      "{urn:ietf:params:xml:ns:netconf:notification:1.0}filter"):
        if filter_node.get("type", "subtree") == "subtree":
            for root in children(filter_node):
                validate_decimal_tree(root, schema, content_match=True)
    if etree_namespace(operation.tag) != NC:
        validate_decimal_tree(operation, schema)


def etree_namespace(tag):
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""
