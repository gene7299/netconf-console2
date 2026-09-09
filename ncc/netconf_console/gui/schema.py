"""Compile device YANG schemas; never infer writable state from XML names."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from lxml import etree
from pyang import context, error, repository

from ..config import config_dir
from ..namespaces import NETCONF_BASE_NS, YANG_LIBRARY_NS, yang_library_filter

DATA_KINDS = {"container", "list", "leaf", "leaf-list", "anyxml", "anydata"}
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


@dataclass
class ModuleSpec:
    name: str
    revision: str | None = None
    features: tuple[str, ...] | None = None
    implemented: bool = True


@dataclass(frozen=True)
class NodeInfo:
    path: tuple[str, ...]
    module: str
    kind: str
    config: bool | None
    keys: tuple[str, ...] = ()
    defaults: tuple[str, ...] = ()
    description: str = ""
    ordered_by_user: bool = False


def _text(node, name):
    value = node.find("{%s}%s" % (YANG_LIBRARY_NS, name))
    return value.text if value is not None else None


def discover_modules(manager) -> tuple[list[ModuleSpec], str, list[str]]:
    """Prefer the running schema's module sets, then RFC 7895/monitoring/hello."""
    specs: dict[tuple[str, str | None], ModuleSpec] = {}
    warnings = []
    content_id = ""
    try:
        data = manager.get(yang_library_filter()).data
        library = data.find("{%s}yang-library" % YANG_LIBRARY_NS)
        legacy = data.find("{%s}modules-state" % YANG_LIBRARY_NS)
        parents = []
        if library is not None:
            content_id = _text(library, "content-id") or ""
            selected_sets = set()
            schema_name = None
            for ds in library.findall("{%s}datastore" % YANG_LIBRARY_NS):
                if (_text(ds, "name") or "").split(":")[-1] == "running":
                    schema_name = _text(ds, "schema")
            for item in library.findall("{%s}schema" % YANG_LIBRARY_NS):
                if _text(item, "name") == schema_name:
                    selected_sets.update(x.text for x in item.findall("{%s}module-set" % YANG_LIBRARY_NS))
            parents = [x for x in library.findall("{%s}module-set" % YANG_LIBRARY_NS)
                       if not selected_sets or _text(x, "name") in selected_sets]
        elif legacy is not None:
            content_id = _text(legacy, "module-set-id") or ""
            parents = [legacy]
        for parent in parents:
            for module in parent:
                if etree.QName(module).localname not in {"module", "import-only-module"}:
                    continue
                name = _text(module, "name")
                if not name:
                    continue
                revision = _text(module, "revision") or None
                implemented = (etree.QName(module).localname != "import-only-module"
                               and _text(module, "conformance-type") != "import")
                features = tuple(x.text for x in module.findall("{%s}feature" % YANG_LIBRARY_NS) if x.text)
                specs[name, revision] = ModuleSpec(name, revision, features, implemented)
                for sub in module.findall("{%s}submodule" % YANG_LIBRARY_NS):
                    subname = _text(sub, "name")
                    if subname:
                        rev = _text(sub, "revision") or None
                        specs[subname, rev] = ModuleSpec(subname, rev, None, False)
    except Exception as exc:
        warnings.append("YANG Library unavailable (%s); trying NETCONF monitoring/hello." % type(exc).__name__)
    if not specs:
        try:
            ns = "urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring"
            root = etree.Element("{%s}netconf-state" % ns)
            etree.SubElement(root, "{%s}schemas" % ns)
            data = manager.get(filter=("subtree", root)).data
            for item in data.findall(".//{%s}schema" % ns):
                name = item.findtext("{%s}identifier" % ns)
                rev = item.findtext("{%s}version" % ns) or None
                if name:
                    specs[name, rev] = ModuleSpec(name, rev)
        except Exception:
            pass
    if not specs:
        for cap in manager.server_capabilities:
            params = parse_qs(urlsplit(str(cap)).query)
            name = params.get("module", [None])[0]
            if name:
                rev = params.get("revision", [None])[0]
                specs[name, rev] = ModuleSpec(name, rev)
    return list(specs.values()), content_id, warnings


class MemoryRepository(repository.Repository):
    def __init__(self, sources: dict[str, str]):
        self.sources = sources

    def get_modules_and_revisions(self, ctx):
        return [(name.split("@", 1)[0], name.split("@", 1)[1] if "@" in name else None, name)
                for name in self.sources]

    def get_module_from_handle(self, handle):
        return handle + ".yang", "yang", self.sources[handle]


@dataclass
class SchemaIndex:
    nodes: dict[tuple[str, ...], NodeInfo] = field(default_factory=dict)
    namespaces: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    module_count: int = 0
    complete: bool = True

    def lookup(self, path: tuple[str, ...]) -> NodeInfo | None:
        return self.nodes.get(path)

    @classmethod
    def compile(cls, sources: dict[str, str], specs: list[ModuleSpec] | None = None):
        result = cls()
        if not sources:
            result.complete = False
            result.warnings.append("No YANG schemas loaded; XML is read-only.")
            return result
        ctx = context.Context(MemoryRepository(sources))
        if specs:
            for spec in specs:
                if spec.features is not None:
                    ctx.features[spec.name] = list(spec.features)
        for name, text in sources.items():
            ctx.add_module(name + ".yang", text)
        try:
            ctx.validate()
        except Exception as exc:
            result.complete = False
            result.warnings.append("YANG compilation incomplete: %s" % type(exc).__name__)
        # pyang 2.7 can report a grouping's leafref before propagating an
        # ancestor's disabled if-feature through a uses expansion. Ignore only
        # that diagnostic when EVERY expanded occurrence is below an inactive
        # ancestor. Active leafrefs and all structural errors still fail closed.
        leafref_occurrences: dict[str, list[bool]] = {}
        def collect_inactive(stmt, inactive=False):
            inactive = inactive or bool(getattr(stmt, "i_not_implemented", False))
            if stmt.keyword in {"leaf", "leaf-list"}:
                leafref_occurrences.setdefault(str(stmt.pos), []).append(inactive)
            for child in getattr(stmt, "i_children", []):
                collect_inactive(child, inactive)
        for module in ctx.modules.values():
            if module is not None and module.keyword == "module":
                collect_inactive(module)
        errors = []
        for pos, tag, args in ctx.errors:
            if not error.is_error(error.err_level(tag)):
                continue
            inactive = leafref_occurrences.get(str(pos), [])
            if tag == "LEAFREF_TO_NOT_IMPLEMENTED" and inactive and all(inactive):
                result.warnings.append("Inactive-feature leafref excluded from the data tree: %s" % pos)
            else:
                errors.append((pos, tag, args))
        if errors:
            # Missing imports/invalid deviations can alter config inheritance.
            # Fail closed for writes, but retain known nodes for inspection.
            result.complete = False
            for pos, tag, args in errors[:12]:
                result.warnings.append("%s: %s" % (pos, error.err_to_str(tag, args)))
        implemented = {s.name for s in specs if s.implemented} if specs else None

        def qname(stmt):
            mod = getattr(stmt, "i_module", None)
            if mod is None:
                return None
            namespace = mod.search_one("namespace")
            if namespace is None:
                mod = getattr(stmt, "i_main_module", mod)
                namespace = mod.search_one("namespace")
            return "{%s}%s" % (namespace.arg, stmt.arg) if namespace is not None else None

        def walk(stmt, path):
            if getattr(stmt, "i_not_implemented", False):
                return
            if stmt.keyword in {"choice", "case"}:
                for child in getattr(stmt, "i_children", []):
                    walk(child, path)
                return
            if stmt.keyword not in DATA_KINDS:
                return
            tag = qname(stmt)
            if tag is None:
                return
            path = path + (tag,)
            defaults = [d.arg for d in stmt.search("default")]
            if not defaults and getattr(stmt, "i_default", None) is not None and stmt.keyword == "leaf":
                defaults = [getattr(stmt, "i_default_str", "")]
            if not defaults and stmt.keyword == "leaf-list":
                type_stmt = stmt.search_one("type")
                typedef = getattr(type_stmt, "i_typedef", None)
                if typedef is not None and getattr(typedef, "i_default", None) is not None:
                    defaults = [typedef.i_default_str]
            keys = tuple(qname(key) for key in getattr(stmt, "i_key", []))
            desc = stmt.search_one("description")
            order = stmt.search_one("ordered-by")
            mod = getattr(stmt, "i_main_module", stmt.i_module)
            result.nodes[path] = NodeInfo(
                path, mod.arg, stmt.keyword, getattr(stmt, "i_config", None),
                keys, tuple(defaults), desc.arg if desc is not None else "",
                order is not None and order.arg == "user",
            )
            for child in getattr(stmt, "i_children", []):
                walk(child, path)

        for (name, rev), module in list(ctx.modules.items()):
            if module is None or module.keyword != "module":
                continue
            namespace = module.search_one("namespace")
            if namespace is not None:
                result.namespaces[namespace.arg] = name
            result.module_count += 1
            if implemented is not None and name not in implemented:
                continue
            for child in getattr(module, "i_children", []):
                walk(child, ())
        return result


def load_device_schemas(manager, server_key: str, local_directory: str = "", force: bool = False,
                        progress: Callable[[str], None] = lambda _x: None,
                        cancelled: Callable[[], bool] = lambda: False) -> SchemaIndex:
    specs, content_id, warnings = discover_modules(manager)
    fingerprint = server_key + content_id + repr(sorted(str(c) for c in manager.server_capabilities))
    cache = config_dir() / "gui-schemas" / hashlib.sha256(fingerprint.encode()).hexdigest()[:24]
    sources: dict[str, str] = {}
    local_sources: dict[str, str] = {}
    if local_directory:
        for path in Path(local_directory).expanduser().glob("*.yang"):
            local_sources[path.stem] = path.read_text(encoding="utf-8-sig")
    pending = list(specs)
    advertised = {}
    for spec in specs:
        advertised.setdefault(spec.name, []).append(spec)
    visited = set()
    parser = context.Context(MemoryRepository({}))
    while pending:
        if cancelled():
            raise InterruptedError("Schema loading cancelled")
        spec = pending.pop(0)
        if not NAME.fullmatch(spec.name) or (spec.revision and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", spec.revision)):
            warnings.append("Ignored invalid schema identifier from server.")
            continue
        key = spec.name + ("@" + spec.revision if spec.revision else "")
        if key in visited:
            continue
        visited.add(key)
        progress("YANG %s (%d loaded)" % (key, len(sources)))
        path = cache / (key + ".yang")
        text = None
        if not force and (spec.revision or content_id) and path.is_file():
            text = path.read_text(encoding="utf-8")
        if text is None:
            try:
                # RFC 6022 defaults format to the YANG identity. ncclient emits
                # an explicit unprefixed "yang" in the RPC's NETCONF namespace,
                # which strict servers correctly reject as an identityref.
                reply = manager.get_schema(spec.name, version=spec.revision)
                text = reply.data
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Empty YANG schema")
                cache.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            except Exception as exc:
                text = local_sources.get(key)
                if text is None and not spec.revision:
                    matches = sorted(k for k in local_sources if k == spec.name or k.startswith(spec.name + "@"))
                    text = local_sources[matches[-1]] if matches else None
                if text is None:
                    # A revisionless local filename may still contain the requested revision.
                    text = local_sources.get(spec.name)
                if text is None:
                    reason = " ".join(str(exc).split())[:180]
                    warnings.append("Schema unavailable: %s (%s: %s)" % (key, type(exc).__name__, reason))
                    continue
        parsed = parser.add_module(key + ".yang", text, expect_modulename=spec.name,
                                   expect_revision=spec.revision)
        if parsed is None:
            warnings.append("Schema identifier/revision mismatch: " + key)
            continue
        sources[key] = text
        for dependency in parsed.search("import") + parsed.search("include"):
            revision = dependency.search_one("revision-date")
            candidates = advertised.get(dependency.arg, [])
            if revision is None and len(candidates) == 1:
                pending.append(candidates[0])
            else:
                pending.append(ModuleSpec(dependency.arg, revision.arg if revision is not None else None, None, False))
    if not specs and local_sources:
        sources.update(local_sources)
    progress("Compiling YANG metadata…")
    result = SchemaIndex.compile(sources, specs or None)
    if any(w.startswith(("Schema unavailable", "Schema identifier")) for w in warnings):
        result.complete = False
    result.warnings = warnings + result.warnings
    return result
