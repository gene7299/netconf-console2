"""Audit every expanded decimal64 leaf against a supplied YANG release.

python packaging/audit_decimal64.py --yang-directory PATH --report build/decimal64-audit.json
Pass --catalog to regenerate the small offline template reference metadata.
"""
import argparse
from collections import Counter
import hashlib
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ncc"))

from netconf_console.gui.schema import SchemaIndex
from netconf_console.gui.decimal_validation import validate_decimal_statement
from netconf_console.gui.model import EditError
from netconf_console.gui.subscription_templates import _type_details, validate_value
from netconf_console.yang_decimal import Decimal64Error, Decimal64Rules, decimal_members, format_scaled


def audit(directory):
    paths = sorted(Path(directory).rglob("*.yang"))
    sources = {path.stem: path.read_text(encoding="utf-8-sig") for path in paths}
    if len(sources) != len(paths):
        raise ValueError("Duplicate YANG filenames; select one unambiguous release")
    schema = SchemaIndex.compile(sources)
    if not schema.complete:
        raise ValueError("Reference YANG compilation failed: " + "\n".join(schema.warnings))
    rows, failures = [], []
    checks = 0

    def qname(stmt):
        module = stmt.i_module
        namespace = module.search_one("namespace")
        if namespace is None:
            namespace = stmt.i_main_module.search_one("namespace")
        return "{%s}%s" % (namespace.arg, stmt.arg)

    def walk(stmt, path, scope):
        nonlocal checks
        if stmt.keyword in {"choice", "case"}:
            for child in getattr(stmt, "i_children", []):
                walk(child, path, scope)
            return
        if stmt.keyword == "notification":
            scope = "notification"
        elif stmt.keyword in {"rpc", "action"}:
            scope = "rpc"
        elif stmt.keyword in {"input", "output"}:
            scope = "rpc-" + stmt.keyword
        path += (qname(stmt),)
        if stmt.keyword in {"leaf", "leaf-list"}:
            typ = stmt.search_one("type")
            spec = getattr(typ, "i_type_spec", None)
            for branch, rules in enumerate(decimal_members(spec)):
                # Compare independently against pyang's exact compiled type,
                # including typedef ranges and min/max/disjoint restrictions.
                target = spec
                while getattr(target, "name", "") == "leafref":
                    target = target.i_target_node.search_one("type").i_type_spec
                if getattr(target, "name", "") == "union":
                    raise ValueError("Audit requires an explicit per-union-branch oracle")
                row = dict(module=stmt.i_module.arg, path=path, scope=scope,
                           config=getattr(stmt, "i_config", None), type=typ.arg,
                           constraints=rules.constraints(), source=str(stmt.pos), branch=branch)
                rows.append(row)
                declared, base, choices, constraints = _type_details(stmt)
                template_field = dict(path=path, type=declared, base=base,
                                      values=choices, constraints=constraints)
                if Decimal64Rules.from_constraints(row["constraints"]) != rules:
                    failures.append(dict(path=path, error="Constraint metadata did not round-trip"))
                values = {"0", "+1.0", "-0.0", "1e0", "NaN", "Infinity", " 1", "1 ", "1\n", "١.٠",
                          "0." + "0" * (rules.fraction_digits + 1)}
                for lo, hi in rules.ranges:
                    for number in (lo - 1, lo, lo + 1, hi - 1, hi, hi + 1):
                        values.add(format_scaled(number, rules.fraction_digits))
                for number in (-(2**63) - 1, 2**63):
                    values.add(format_scaled(number, rules.fraction_digits))
                for value in sorted(values):
                    checks += 1
                    errors = []
                    parsed = target.str_to_val(errors, stmt.pos, value, stmt.i_module)
                    # pyang's '$'-anchored pattern also accepts a trailing LF.
                    # XML decimal lexical syntax (RFC 7950 section 9.3.1) does
                    # not; require a full ASCII lexical match independently.
                    lexical = re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value) is not None
                    expected = lexical and not errors and parsed is not None and bool(target.validate(errors, stmt.pos, parsed, stmt.i_module)) and not errors
                    try:
                        rules.validate(value)
                        accepted = True
                    except Decimal64Error:
                        accepted = False
                    if accepted != bool(expected):
                        failures.append(dict(path=path, value=value, expected=bool(expected), accepted=accepted))
                    for name, validate in (
                            ("editor/RPC", lambda: validate_decimal_statement(stmt, value, stmt.arg)),
                            ("notification/template", lambda: validate_value(template_field, value))):
                        try:
                            validate()
                            accepted = True
                        except EditError:
                            accepted = False
                        if accepted != bool(expected):
                            failures.append(dict(path=path, validator=name, value=value,
                                                 expected=bool(expected), accepted=accepted))
        for child in getattr(stmt, "i_children", []):
            walk(child, path, scope)

    for module in schema.modules.values():
        for child in getattr(module, "i_children", []):
            walk(child, (), "data")
    metadata = dict(source=Path(directory).name, modules=schema.module_count,
                    source_files=[dict(path=str(path.relative_to(directory)).replace("\\", "/"),
                                       sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths],
                    leaves=rows)
    report = dict(source=metadata["source"], modules=schema.module_count,
                  decimal64_leaves=len(rows), scopes=dict(Counter(row["scope"] for row in rows)),
                  fraction_digits=dict(Counter(row["constraints"]["fraction-digits"] for row in rows)),
                  checks=checks, passed=not failures, failures=failures, leaves=rows)
    report["validators"] = ["exact decimal64", "editor/RPC", "notification/template"]
    report["lexical_reference"] = "https://www.rfc-editor.org/rfc/rfc7950.html#section-9.3.1"
    return metadata, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yang-directory", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--catalog", type=Path)
    args = parser.parse_args()
    metadata, report = audit(args.yang_directory)
    for path, data in ((args.catalog, metadata), (args.report, report)):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: (value[:5] if key == "failures" else value)
                      for key, value in report.items() if key != "leaves"}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
