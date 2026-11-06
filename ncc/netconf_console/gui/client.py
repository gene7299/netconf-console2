"""GUI service layer using the CLI's tested Direct/Call Home transports."""

from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from lxml import etree

from ..session import ConnectionSettings, ConsoleContext
from .model import EditError, EditPlan, Selection, children, identity, typed_semantic
from .operational import read_operational
from .schema import SchemaIndex, load_device_schemas


@dataclass(frozen=True)
class ReadOptions:
    source: str = "running"
    defaults: bool = False
    state: bool = False


@dataclass
class Snapshot:
    data: etree._Element
    options: ReadOptions
    defaults_mode: str | None = None
    warnings: list[str] = field(default_factory=list)
    state_failures: dict[str, str] = field(default_factory=dict)
    value_errors: dict[tuple[str, ...], str] = field(default_factory=dict)

    def replace_root(self, part, root_tag):
        data = deepcopy(self.data)
        positions = [i for i, node in enumerate(data) if node.tag == root_tag]
        position = positions[0] if positions else len(data)
        for node in list(data):
            if node.tag == root_tag:
                data.remove(node)
        for node in reversed(children(part.data)):
            data.insert(position, deepcopy(node))
        failures = {tag: detail for tag, detail in self.state_failures.items() if tag != root_tag}
        failures.update(part.state_failures)
        warnings = list(dict.fromkeys(self.warnings + part.warnings))
        errors = {path: detail for path, detail in self.value_errors.items() if path[0] != root_tag}
        errors.update(part.value_errors)
        return Snapshot(data, part.options, part.defaults_mode, warnings, failures, errors)


@dataclass
class ApplyResult:
    reply: str
    snapshot: Snapshot | None
    warnings: list[str] = field(default_factory=list)


def defaults_mode(capabilities, requested):
    if not requested:
        return None, []
    supported = set()
    for cap in capabilities:
        if ":with-defaults:" in str(cap):
            params = parse_qs(urlsplit(str(cap)).query)
            supported.update(params.get("basic-mode", []))
            for value in params.get("also-supported", []):
                supported.update(value.split(","))
    if "report-all-tagged" in supported:
        return "report-all-tagged", []
    if "report-all" in supported:
        return "report-all", ["Server supports report-all without tags; schema-default colour does not prove a value was implicit."]
    return None, ["Server cannot report all defaults. Showing its native reply; missing defaults are not fabricated."]


def locate(data, selection, schema):
    """Find the same instance, never use a guessed ordinal for a writable list."""
    parent = data
    path = ()
    for old in (*selection.ancestors, selection.node):
        path += (old.tag,)
        wanted = identity(old, schema, path)
        matches = [node for node in children(parent)
                   if node.tag == old.tag and identity(node, schema, path) == wanted]
        if len(matches) != 1:
            raise EditError("The selected instance disappeared or is ambiguous; reload the tree.")
        parent = matches[0]
    return parent


def config_semantic(node, path, schema):
    info = schema.lookup(path)
    if info is None or info.config is not True:
        return None
    if info.kind in {"leaf", "leaf-list"}:
        return typed_semantic(node, schema, path)
    values = [config_semantic(child, path + (child.tag,), schema) for child in children(node)]
    return node.tag, tuple(value for value in values if value is not None)


def check_selection_current(data, selection, schema):
    if not selection.exists:
        if selection.ancestors:
            raise EditError("An absent baseline must be a root node.")
        wanted = identity(selection.node, schema, selection.path)
        if any(identity(n, schema, selection.path) == wanted for n in children(data)
               if n.tag == selection.node.tag):
            raise EditError("The new instance already exists on the server. Reload before sending.")
        return
    latest = locate(data, selection, schema)
    if config_semantic(latest, selection.path, schema) != config_semantic(selection.node, selection.path, schema):
        raise EditError("Configuration changed on the server since this read. Reload before sending.")


class GuiClient:
    def __init__(self):
        self.context: ConsoleContext | None = None
        self.schema = SchemaIndex(complete=False)
        self.cancel = threading.Event()
        self.pending_commit = None

    @property
    def connected(self):
        return bool(self.context and self.context.connected)

    @property
    def manager(self):
        if self.context is None:
            raise EditError("Not connected.")
        return self.context.require_manager()

    @property
    def capabilities(self):
        return [str(cap) for cap in self.manager.server_capabilities]

    def connect(self, settings: ConnectionSettings, progress=lambda _text: None):
        if self.pending_commit is not None:
            import time
            if time.monotonic() < self.pending_commit.release_after:
                raise EditError("前次限時提交仍在等待結束；請稍候，不會自動換 session 確認。")
            self.pending_commit = None
        self.cancel.clear()
        self.context = ConsoleContext(settings)
        try:
            if settings.call_home:
                self.context.listen(cancel_event=self.cancel,
                                    on_waiting=lambda value: progress("Waiting for Call Home: " + value),
                                    on_accepted=lambda value: progress("Peer accepted: " + value))
            else:
                self.context.connect()
            if self.cancel.is_set():
                raise InterruptedError("Connection cancelled")
        except Exception:
            self.disconnect()
            raise

    def disconnect(self):
        context = self.context
        try:
            if context is not None:
                context.close(graceful_timeout=1.5)
        finally:
            self.context = None
            self.schema = SchemaIndex(complete=False)

    def load_schema(self, directory="", force=False, progress=lambda _text: None):
        self.schema = load_device_schemas(
            self.manager, self.context._namespace_server_key(), directory, force, progress,
            self.cancel.is_set,
        )
        return self.schema

    def read(self, options: ReadOptions, root_tag=None, progress=lambda _text: None) -> Snapshot:
        if options.source not in {"running", "candidate", "startup"}:
            raise EditError("Unsupported source datastore.")
        if options.state and options.source != "running":
            raise EditError("config false is read using <get> (running + state), not candidate/startup.")
        for source, capability in (("candidate", ":candidate:"), ("startup", ":startup:")):
            if options.source == source and not any(capability in c for c in self.capabilities):
                raise EditError("Server does not advertise " + source)
        mode, warnings = defaults_mode(self.capabilities, options.defaults)
        from .decimal_validation import decimal_diagnostics
        query_filter = ("subtree", etree.Element(root_tag)) if root_tag else None
        if options.state:
            def read_state(_module, tags):
                selected = ([etree.Element(tag) for tag in tags] if tags else query_filter)
                return self.manager.get_ncc(filter=selected, defaults=mode).data

            result = read_operational(
                read_state,
                lambda: self.manager.get_config_ncc(
                    source=options.source, filter=query_filter, defaults=mode).data,
                self.schema, root_tag, progress, self.cancel.is_set)
            return Snapshot(result.data, options, mode,
                            warnings + result.warnings, result.state_failures,
                            decimal_diagnostics(result.data, self.schema))
        else:
            reply = self.manager.get_config_ncc(source=options.source, filter=query_filter, defaults=mode)
        data = deepcopy(reply.data)
        return Snapshot(data, options, mode, warnings, value_errors=decimal_diagnostics(data, self.schema))

    def apply(self, selection: Selection, plan: EditPlan, options: ReadOptions) -> ApplyResult:
        if self.pending_commit is not None:
            raise EditError("限時提交尚未結束，不能繼續修改。")
        if plan.rpc is None:
            raise EditError("No changes to send.")
        if options.source not in {"running", "candidate"}:
            raise EditError("The GUI edits running/candidate only. startup is read-only; persistence must be explicit.")
        required = ":writable-running:" if options.source == "running" else ":candidate:"
        if not any(required in cap for cap in self.capabilities):
            raise EditError("Server does not advertise " + required)
        from ncclient.xml_ import to_xml
        if to_xml(plan.rpc) != plan.wire_xml:
            raise EditError("Outgoing XML changed since preview; regenerate it before sending.")
        from .decimal_validation import validate_rpc_decimals
        validate_rpc_decimals(plan.rpc, self.schema)
        target = plan.rpc.find("{urn:ietf:params:xml:ns:netconf:base:1.0}edit-config/{urn:ietf:params:xml:ns:netconf:base:1.0}target")
        if target is None or len(target) != 1 or etree.QName(target[0]).localname != options.source:
            raise EditError("Preview target differs from the source snapshot; reload before sending.")
        error_option = plan.rpc.findtext("{urn:ietf:params:xml:ns:netconf:base:1.0}edit-config/{urn:ietf:params:xml:ns:netconf:base:1.0}error-option")
        if error_option == "rollback-on-error" and not any(cap.split("?", 1)[0] == "urn:ietf:params:netconf:capability:rollback-on-error:1.0" for cap in self.capabilities):
            raise EditError("Server does not advertise :rollback-on-error; no edit sent.")
        # A lock spans conflict detection and this single edit. Never force an
        # unlock, commit candidate, or copy running to startup automatically.
        manager = self.manager
        locked = False
        warnings = []
        try:
            manager.lock(target=options.source)
            locked = True
            current = self.read(ReadOptions(options.source, options.defaults, False), selection.path[0])
            check_selection_current(current.data, selection, self.schema)
            reply = manager.xrpc(deepcopy(plan.rpc))
            reply_xml = reply.xml
        finally:
            if locked:
                try:
                    manager.unlock(target=options.source)
                except Exception as exc:
                    # Close our own session to release its lock, not another user's.
                    warnings.append("Unlock failed (%s); closing this session to release its lock." % type(exc).__name__)
                    self.disconnect()
        snapshot = None
        if self.connected:
            try:
                snapshot = self.read(options)
            except Exception as exc:
                warnings.append("Edit succeeded but reread failed (%s). Do not resend; reconnect/reread first." % type(exc).__name__)
        return ApplyResult(reply_xml, snapshot, warnings)
