"""RFC-oriented operations exposed by the netconf-console2 CLI."""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from lxml import etree

import ncclient
from ncclient.devices import default
from ncclient.operations import retrieve, util
from ncclient.operations.rpc import RPC, RPCError
from ncclient.transport import SessionListener, session
from ncclient.xml_ import (
    BASE_NS_1_0,
    NETCONF_NOTIFICATION_NS,
    YANG_NS_1_0,
    new_ele,
    new_ele_ns,
    qualify,
    sub_ele,
    to_xml,
    validated_element,
)

from . import completions
from .capabilities import capability_rows, negotiated_version
from .namespaces import COMMON_NAMESPACES


NOTIFICATION_NS = NETCONF_NOTIFICATION_NS
TAILF_AAA_NS = "http://tail-f.com/ns/aaa/1.1"
IETF_NACM_NS = "urn:ietf:params:xml:ns:yang:ietf-netconf-acm"
DEFAULTS_NS = "urn:ietf:params:xml:ns:yang:ietf-netconf-with-defaults"
INACTIVE_NS = "http://tail-f.com/ns/netconf/inactive/1.0"
NMDA_NS = "urn:ietf:params:xml:ns:yang:ietf-netconf-nmda"
DATASTORES_NS = "urn:ietf:params:xml:ns:yang:ietf-datastores"
ORIGIN_NS = "urn:ietf:params:xml:ns:yang:ietf-origin"

_QUOTED_XPATH_TEXT = re.compile(r"'(?:[^']*)'|\"(?:[^\"]*)\"")
_PREFIX_REFERENCE = re.compile(r"(?<![\w.-])([A-Za-z_][\w.-]*):(?=[A-Za-z_*])")


if sys.version_info >= (3, 0):
    STDIN = getattr(sys.stdin, "buffer", sys.stdin)
else:  # pragma: no cover - retained for historical source compatibility
    STDIN = sys.stdin


@dataclass
class TextResult:
    """A command result that is intentionally rendered as text."""

    text: str


def rpc_error_details(error: RPCError | etree._Element) -> list[dict[str, Any]]:
    """Extract all RFC 6241 rpc-error fields without losing raw error-info."""

    root = error.xml if isinstance(error, RPCError) else error
    errors = [root] if _local_name(root) == "rpc-error" else [
        element for element in root.iter() if _local_name(element) == "rpc-error"
    ]
    result = []
    for rpc_error in errors:
        values: dict[str, Any] = {}
        for child in rpc_error:
            name = _local_name(child)
            if name == "error-info":
                values[name] = _xml_text(child)
            else:
                values[name] = child.text
        result.append(values)
    return result


class ExecuteRpc(RPC):
    """Custom general-purpose RPC operation."""

    def request(self, rpc: etree._Element) -> Any:
        return self._request(rpc)


def extend_get_node(rpc: RPC, node: etree._Element, filter: Any = None,
                    defaults: str | None = None, inactive: bool = False) -> etree._Element:
    if filter is not None:
        node.append(util.build_filter(filter, rpc._assert))
    if defaults is not None:
        rpc._assert(":with-defaults")
        etree.SubElement(node, qualify("with-defaults", DEFAULTS_NS)).text = defaults
    if inactive:
        etree.SubElement(node, qualify("with-inactive", INACTIVE_NS))
    return node


class GetConfigRpc(retrieve.GetConfig):
    """The ncclient get-config operation with RFC 6243 support."""

    def request(self, source: str, filter: Any = None, defaults: str | None = None,
                nsmap: dict[str | None, str] | None = None, inactive: bool = False) -> Any:
        node = new_ele("get-config", nsmap=nsmap or {})
        node.append(util.datastore_or_url("source", source, self._assert))
        return self._request(extend_get_node(self, node, filter, defaults, inactive))


class GetRpc(retrieve.Get):
    """The ncclient get operation with RFC 6243 support."""

    def request(self, filter: Any = None, defaults: str | None = None,
                nsmap: dict[str | None, str] | None = None, inactive: bool = False) -> Any:
        node = new_ele("get", nsmap=nsmap or {})
        return self._request(extend_get_node(self, node, filter, defaults, inactive))


class ValidateRpc(RPC):
    """Validate a datastore or a supplied configuration element."""

    DEPENDS = [":validate"]

    def request(self, source: Any = "candidate") -> Any:
        node = new_ele("validate")
        if isinstance(source, str):
            src = util.datastore_or_url("source", source, self._assert)
        else:
            validated_element(source, ("config", qualify("config")))
            src = new_ele("source")
            src.append(source)
        node.append(src)
        return self._request(node)


class XRPC(RPC):
    """Send a complete RPC envelope while preserving its message-id."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._listener._id2rpc.pop(self._id, None)
        self._id = None

    def request(self, rpc: etree._Element) -> Any:
        message_ids = rpc.xpath("@message-id")
        if not message_ids:
            raise ValueError("A full <rpc> request must contain message-id")
        self._id = message_ids[0]
        self._listener.register(self._id, self)
        return self._request(rpc)

    def _wrap(self, tree: etree._Element) -> str:
        return to_xml(tree)


class ConsoleDeviceHandler(default.DefaultDeviceHandler):
    """Default ncclient handler plus application-specific operation adapters."""

    def add_additional_operations(self) -> dict[str, type[RPC]]:
        return {
            "rpc": ExecuteRpc,
            "get_config_ncc": GetConfigRpc,
            "get_ncc": GetRpc,
            "validate_ncc": ValidateRpc,
            "xrpc": XRPC,
        }


def _manager(mc: Any) -> Any:
    require = getattr(mc, "require_manager", None)
    return require() if callable(require) else mc


def _root(reply: Any) -> etree._Element:
    root = reply._root
    return root[0] if isinstance(root, list) else root


def _local_name(node_or_tag: Any) -> str:
    tag = node_or_tag.tag if hasattr(node_or_tag, "tag") else node_or_tag
    return etree.QName(tag).localname if isinstance(tag, str) and tag.startswith("{") else str(tag)


def _parse_xml(filename: str) -> etree._Element:
    parser = etree.XMLParser(remove_blank_text=True)
    if filename.lstrip().startswith("<"):
        return etree.fromstring(filename.encode("utf-8"), parser)
    if filename == "-":
        return etree.parse(getattr(sys.stdin, "buffer", sys.stdin), parser).getroot()
    with open(filename, "rb") as stream:
        return etree.parse(stream, parser).getroot()


def _write_text(filename: str, text: str) -> None:
    if filename == "-":
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
        return
    Path(filename).expanduser().write_text(text, encoding="utf-8", newline="")


class Operation:
    name: str | None = None
    option: str | None = None
    aliases: tuple[str, ...] = ()
    help: str | None = None
    nargs: int | str = 0
    dest: str | None = None
    command_opts: list[str] = []
    choices: list[str] | None = None
    arg_completion = staticmethod(completions.no_arg_completion)

    def invoke(self, mc: Any, ns: Any, *args: Any) -> Any:
        raise NotImplementedError


class Hello(Operation):
    name = option = "hello"
    help = "Display client hello, server hello, and negotiated NETCONF version"

    def invoke(self, mc: Any, _ns: Any) -> TextResult:
        manager = _manager(mc)
        server = manager.server_capabilities or []
        session_obj = manager._session
        try:
            client_caps = session_obj._device_handler.get_capabilities()
        except AttributeError:
            client_caps = list(session_obj.client_capabilities or [])
        client = new_ele("hello")
        capabilities = sub_ele(client, "capabilities")
        for capability in client_caps:
            sub_ele(capabilities, "capability").text = capability
        server_root = new_ele("hello")
        server_caps = sub_ele(server_root, "capabilities")
        for capability in server:
            sub_ele(server_caps, "capability").text = capability
        return TextResult(
            "Client hello:\n%s\n\nServer hello:\n%s\n\nNegotiated NETCONF version: %s"
            % (_xml_text(client), _xml_text(server_root), negotiated_version(session_obj))
        )


class GetOperation(Operation):
    command_opts = ["style", "wdefaults", "xpath", "filter", "filter_file", "winactive"]
    nargs = "?"

    def invoke(self, mc: Any, ns: Any, path: str | None = None) -> Any:
        replydata = self._invoke(mc, ns, path)
        if "noaaa" in (getattr(ns, "style", None) or []):
            for namespace, name in ((TAILF_AAA_NS, "aaa"), (IETF_NACM_NS, "nacm")):
                node = replydata.find(qualify(name, namespace))
                if node is not None:
                    replydata.remove(node)
        return replydata


class Get(GetOperation):
    name = option = "get"
    help = "Retrieve state/configuration data; accepts XPath or a legacy path argument"

    def _invoke(self, mc: Any, ns: Any, path: str | None = None) -> Any:
        expression = path if path is not None else getattr(ns, "xpath", None)
        reply = _manager(mc).get_ncc(
            data_filter(ns, path),
            getattr(ns, "wdefaults", None),
            nsmap(ns, expression),
            getattr(ns, "winactive", False),
        )
        return reply.data


class GetConfig(GetOperation):
    name = "get_config"
    option = "get-config"
    help = "Retrieve a configuration datastore with XPath, subtree, and with-defaults filters"
    command_opts = GetOperation.command_opts + ["db"]

    def _invoke(self, mc: Any, ns: Any, path: str | None = None) -> Any:
        expression = path if path is not None else getattr(ns, "xpath", None)
        reply = _manager(mc).get_config_ncc(
            getattr(ns, "db", "running"),
            data_filter(ns, path),
            getattr(ns, "wdefaults", None),
            nsmap(ns, expression),
            getattr(ns, "winactive", False),
        )
        return reply.data


class DiscardChanges(Operation):
    name = "discard_changes"
    option = "discard-changes"
    aliases = ("discard",)
    help = "Discard uncommitted candidate changes"

    def invoke(self, mc: Any, _ns: Any) -> Any:
        return _root(_manager(mc).discard_changes())


class Commit(Operation):
    name = option = "commit"
    nargs = "?"
    choices = ["confirmed"]
    command_opts = ["timeout", "persist", "persist_id"]
    arg_completion = staticmethod(completions.commit_arg_completion)
    help = "Commit candidate data; optionally use confirmed, timeout, persist, or persist-id"

    def invoke(self, mc: Any, ns: Any, confirmed: str | bool = False) -> Any:
        kwargs: dict[str, Any] = {"confirmed": confirmed is True or confirmed == "confirmed"}
        if kwargs["confirmed"] and getattr(ns, "timeout", None) is not None:
            kwargs["timeout"] = str(ns.timeout)
        if getattr(ns, "persist", None) is not None:
            kwargs["persist"] = ns.persist
        if getattr(ns, "persist_id", None) is not None:
            kwargs["persist_id"] = ns.persist_id
        return _root(_manager(mc).commit(**kwargs))


class CancelCommit(Operation):
    name = "cancel_commit"
    option = "cancel-commit"
    command_opts = ["persist_id"]
    help = "Cancel an outstanding confirmed commit"

    def invoke(self, mc: Any, ns: Any) -> Any:
        return _root(_manager(mc).cancel_commit(getattr(ns, "persist_id", None)))


class KillSession(Operation):
    name = "kill_session"
    option = "kill-session"
    help = "Terminate another NETCONF session by ID"
    nargs = 1
    dest = "session_id"

    def invoke(self, mc: Any, _ns: Any, session_id: str) -> Any:
        return _root(_manager(mc).kill_session(session_id))


class CloseSession(Operation):
    name = option = "close-session"
    aliases = ("close",)
    help = "Gracefully close the current NETCONF session"

    def invoke(self, mc: Any, _ns: Any) -> Any:
        return _root(_manager(mc).close_session())


class Validate(Operation):
    name = option = "validate"
    help = "Validate candidate or a configuration file (default: stdin)"
    nargs = "?"
    arg_completion = staticmethod(completions.validate_arg_completion)

    def invoke(self, mc: Any, _ns: Any, source: str = "-") -> Any:
        if source == "candidate" or source.startswith(("http://", "https://", "ftp://", "file://")):
            value: Any = source
        else:
            value = _parse_xml(source)
        return _root(_manager(mc).validate_ncc(value))


class CopyRunningToStartup(Operation):
    option = "copy-running-to-startup"
    name = "copy_running_to_startup"
    help = "Copy the running datastore to startup"

    def invoke(self, mc: Any, _ns: Any) -> Any:
        return _root(_manager(mc).copy_config("running", "startup"))


class DeleteConfig(Operation):
    name = "delete_config"
    option = "delete-config"
    command_opts = ["db"]
    help = "Delete a configuration datastore (usually startup)"

    def invoke(self, mc: Any, ns: Any) -> Any:
        return _root(_manager(mc).delete_config(getattr(ns, "db", "startup")))


class CopyConfig(Operation):
    option = "copy-config"
    name = "copy_config"
    nargs = "?"
    arg_completion = staticmethod(completions.filename_arg_completion)
    command_opts = ["db", "source"]
    help = "Copy a datastore or file/stdin configuration into the target datastore"

    def invoke(self, mc: Any, ns: Any, filename: str = "-") -> Any:
        source = getattr(ns, "source", None)
        if source is None and filename in {"running", "startup", "candidate"}:
            source, filename = filename, "-"
        if source is not None:
            return _root(_manager(mc).copy_config(source, getattr(ns, "db", "running")))
        data = _parse_xml(filename)
        config = new_ele("config")
        if _local_name(data) == "config":
            config.extend(list(data))
        else:
            config.append(data)
        copy = new_ele("copy-config")
        sub_ele(sub_ele(copy, "target"), getattr(ns, "db", "running"))
        sub_ele(sub_ele(copy, "source"), "config").append(config)
        return _root(_manager(mc).rpc(copy))


class EditConfig(Operation):
    option = "edit-config"
    name = "edit_config"
    nargs = "*"
    arg_completion = staticmethod(completions.filename_arg_completion)
    command_opts = ["db", "test", "default_operation", "error_option", "winactive"]
    help = "Edit a datastore from one or more XML files or stdin"

    def invoke(self, mc: Any, ns: Any, *filenames: str) -> Any:
        config = new_ele("config")
        parser = etree.XMLParser(remove_blank_text=True)
        for filename in filenames or ("-",):
            if filename == "-":
                data = etree.parse(getattr(sys.stdin, "buffer", sys.stdin), parser).getroot()
            else:
                with open(filename, "rb") as stream:
                    data = etree.parse(stream, parser).getroot()
            if _local_name(data) == "config":
                config.extend(list(data))
            else:
                config.append(data)
        return _root(_manager(mc).edit_config(
            config,
            target=getattr(ns, "db", "running"),
            default_operation=getattr(ns, "default_operation", None),
            test_option=getattr(ns, "test", None),
            error_option=getattr(ns, "error_option", None),
        ))


class EditConfig1(Operation):
    option = "edit-config1"
    name = "edit_config1"
    nargs = "?"
    arg_completion = staticmethod(completions.filename_arg_completion)
    command_opts = EditConfig.command_opts
    help = "Legacy edit-config form that strips the input document's root element"

    def invoke(self, mc: Any, ns: Any, filename: str | None = None) -> Any:
        data = _parse_xml(filename or "-")
        config = new_ele("config")
        config.extend(list(data))
        return _root(_manager(mc).edit_config(
            config,
            target=getattr(ns, "db", "running"),
            default_operation=getattr(ns, "default_operation", None),
            test_option=getattr(ns, "test", None),
            error_option=getattr(ns, "error_option", None),
        ))


class PathExpressionException(Exception):
    pass


class XpathParser:
    """Small path-to-subtree builder retained for the original set/delete UX."""

    QuotedT = r"%(q)s(?P<%(vname)s>[^%(q)s\\]*(?:\\.[^%(q)s\\]*)*)%(q)s"
    DirectValRx = r"(?P<value3>[^\]]*)"
    IdentT = r"(?P<%(iname)s>[^:='\"\[/]+)"
    NodeSpecT = "(?:%s:)?%s"
    PredicateRx = (r"\[ *%s(?: *= *(?:%s|%s|%s))? *\]" %
                   (NodeSpecT % (IdentT % {"iname": "pprefix"}, IdentT % {"iname": "ptag"}),
                    QuotedT % {"q": "'", "vname": "value1"},
                    QuotedT % {"q": '"', "vname": "value2"}, DirectValRx))
    XpathRx = "(?:/%s(?:%s)*)+" % (NodeSpecT % (IdentT % {"iname": "prefix"},
                                                    IdentT % {"iname": "tag"}), PredicateRx)
    XpathExpr = re.compile(XpathRx)
    NodeExpr = re.compile(NodeSpecT % (IdentT % {"iname": "prefix"}, IdentT % {"iname": "tag"}))
    PredicateExpr = re.compile(PredicateRx)

    def __init__(self, expression: str, nsmap: dict[str, str]):
        self.expression = expression
        self.nsmap = nsmap

    def build_tree_on(self, top_node: etree._Element) -> tuple[etree._Element, str]:
        # Set expressions intentionally contain a trailing ``=value``; the
        # caller receives that suffix as ``rest`` and decides whether it is
        # valid for the operation.
        if XpathParser.XpathExpr.match(self.expression) is None:
            raise PathExpressionException("Invalid path expression")
        self.pos = 0
        try:
            return self.build_tree(top_node)
        except PathExpressionException:
            raise
        except Exception as exc:
            raise PathExpressionException("Failed to parse path expression") from exc

    def build_tree(self, element: etree._Element, namespace: str | None = None) -> tuple[etree._Element, str]:
        match = XpathParser.NodeExpr.match(self.expression, self.pos + 1)
        if match is None:
            raise PathExpressionException("Invalid path expression")
        groups = match.groupdict()
        child, namespace = self.build_node(element, groups["tag"], groups["prefix"], namespace)
        self.pos = match.end()
        self.build_predicate(child, namespace)
        if self.pos < len(self.expression) and self.expression[self.pos] == "/":
            return self.build_tree(child, namespace)
        return child, self.expression[self.pos:]

    def build_node(self, element: etree._Element, tag: str, prefix: str | None,
                   namespace: str | None) -> tuple[etree._Element, str | None]:
        element_ns = self.nsmap.get(prefix, namespace)
        qualified = "%s%s" % ("{%s}" % element_ns if element_ns else "", tag)
        node = etree.SubElement(element, qualified)
        return node, element_ns

    def build_predicate(self, node: etree._Element, namespace: str | None) -> None:
        match = XpathParser.PredicateExpr.match(self.expression, self.pos)
        if match is None:
            return
        groups = match.groupdict()
        tag = groups["ptag"].strip()
        child, child_ns = (node, namespace) if tag == "." else self.build_node(
            node, tag, groups["pprefix"], namespace
        )
        values = [groups[key] for key in ("value1", "value2", "value3") if groups[key] is not None]
        if values:
            child.text = values[0]
        self.pos = match.end()
        self.build_predicate(node, namespace)


class ModifOp(Operation):
    def invoke(self, mc: Any, ns: Any, *expressions: str) -> Any:
        pfxmap = nsmap(ns, *expressions)
        config = new_ele("config", nsmap=pfxmap)
        for expression in expressions:
            target, rest = XpathParser(expression, pfxmap).build_tree_on(config)
            self.modify_target(ns, target, rest)
        return _root(_manager(mc).edit_config(
            config,
            target=getattr(ns, "db", "running"),
            test_option=getattr(ns, "test", None),
        ))

    def modify_target(self, ns: Any, target: etree._Element, rest: str) -> None:
        raise NotImplementedError


class Set(ModifOp):
    option = name = "set"
    nargs = "*"
    command_opts = ["db", "test", "operation"]
    help = "Set one or more leaf values using path=value expressions"

    def modify_target(self, ns: Any, target: etree._Element, rest: str) -> None:
        if not rest.startswith("="):
            raise PathExpressionException("The expression needs to take form <path>=<value>")
        target.attrib[qualify("operation", BASE_NS_1_0)] = getattr(ns, "operation", "merge")
        target.text = rest[1:]


class Delete(ModifOp):
    option = name = "delete"
    nargs = "*"
    command_opts = ["db", "test", "deloperation"]
    help = "Delete one or more nodes using simple path expressions"

    def modify_target(self, ns: Any, target: etree._Element, rest: str) -> None:
        if rest:
            raise PathExpressionException("The expression must be a valid path")
        target.attrib[qualify("operation", BASE_NS_1_0)] = getattr(ns, "deloperation", "remove")


class Create(ModifOp):
    option = name = "create"
    nargs = "*"
    command_opts = ["db", "test"]
    help = "Create one or more nodes using simple path expressions"

    def modify_target(self, _ns: Any, target: etree._Element, rest: str) -> None:
        if rest:
            raise PathExpressionException("The expression must be a valid path")
        target.attrib[qualify("operation", BASE_NS_1_0)] = "create"


class GetSchema(Operation):
    option = "get-schema"
    name = "get_schema"
    nargs = "?"
    command_opts = ["schema_model", "schema_version", "schema_format", "out"]
    help = "Retrieve a YANG schema by identifier, optionally saving it with --out"

    def invoke(self, mc: Any, ns: Any, schema_id: str | None = None) -> Any:
        identifier = getattr(ns, "schema_model", None) or schema_id
        if not identifier:
            raise ValueError("get-schema requires an identifier or --model")
        items = identifier.split("/")
        if getattr(ns, "schema_version", None) is not None:
            items.append(ns.schema_version)
        if getattr(ns, "schema_format", None) is not None:
            items.append(ns.schema_format)
        if len(items) > 3:
            raise ValueError("Schema ID must be identifier[/version[/format]]")
        reply = _manager(mc).get_schema(*items)
        reply_data = getattr(reply, "data", None)
        root = _root(reply)
        schema_text = reply_data if isinstance(reply_data, str) else _schema_text(root)
        learner = getattr(mc, "learn_yang_schema", None)
        if callable(learner):
            learner(schema_text, items[0])
        outfile = getattr(ns, "out", None)
        if outfile:
            _write_text(outfile, schema_text)
            return TextResult("Saved schema %s to %s" % (identifier, outfile))
        return TextResult(schema_text)


class NotificationListener(SessionListener):
    def __init__(self, stream: Any = None):
        self.stream = stream or sys.stdout

    def callback(self, root: Any, raw: Any) -> None:
        tag, _attrs = root
        if tag == qualify("notification", NOTIFICATION_NS) or _local_name(tag) == "notification":
            self.stream.write(raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw))
            self.stream.write("\n")
            self.stream.flush()

    def errback(self, _err: Exception) -> None:
        return None


class CreateSubscription(Operation):
    option = "create-subscription"
    name = "create_subscription"
    nargs = "?"
    command_opts = ["stream", "xpath", "filter", "filter_file", "start", "stop"]
    help = "Create an RFC 5277 subscription with stream, filter, and replay time options"
    arg_completion = staticmethod(completions.stream_arg_completion)

    def invoke(self, mc: Any, ns: Any, stream: str | None = None) -> Any:
        manager = _manager(mc)
        stream_name = getattr(ns, "stream", None) or stream
        node = new_ele_ns("create-subscription", NOTIFICATION_NS)
        filter_node = subscription_filter(ns)
        if filter_node is not None:
            node.append(filter_node)
        if stream_name is not None:
            etree.SubElement(node, qualify("stream", NOTIFICATION_NS)).text = stream_name
        start = getattr(ns, "start", None)
        stop = getattr(ns, "stop", None)
        if stop is not None and start is None:
            raise ValueError("--stop requires --start")
        if start is not None:
            etree.SubElement(node, qualify("startTime", NOTIFICATION_NS)).text = start
        if stop is not None:
            etree.SubElement(node, qualify("stopTime", NOTIFICATION_NS)).text = stop
        # Keep the original asynchronous printing behavior.  The `subscribe`
        # alias intentionally uses the queue and pairs with `watch`.
        if getattr(self, "option", "") == "create-subscription":
            session_obj = manager._session
            if session_obj.get_listener_instance(NotificationListener) is None:
                session_obj.add_listener(NotificationListener())
        return _root(manager.rpc(node))


class Subscribe(CreateSubscription):
    option = "subscribe"
    name = "subscribe"
    help = "Create an RFC 5277 subscription; use watch afterwards to consume notifications"


class Watch(Operation):
    name = "watch_notifications"
    option = "watch"
    aliases = ("watch-notifications", "notifications")
    help = "Print queued NETCONF notifications until Ctrl+C or session close"

    def invoke(self, mc: Any, _ns: Any) -> None:
        watch = getattr(mc, "watch_notifications", None)
        if callable(watch):
            watch()
            return None
        manager = _manager(mc)
        try:
            while manager.connected:
                notification = manager.take_notification(block=True, timeout=0.5)
                if notification is not None:
                    print(notification.notification_xml)
        except KeyboardInterrupt:
            return None
        return None


class Lock(Operation):
    name = option = "lock"
    command_opts = ["db"]
    help = "Lock a configuration datastore"

    def invoke(self, mc: Any, ns: Any) -> Any:
        return _root(_manager(mc).lock(getattr(ns, "db", "running")))


class Unlock(Operation):
    name = option = "unlock"
    command_opts = ["db"]
    help = "Unlock a configuration datastore"

    def invoke(self, mc: Any, ns: Any) -> Any:
        return _root(_manager(mc).unlock(getattr(ns, "db", "running")))


class Rpc(Operation):
    option = name = "rpc"
    aliases = ("user-rpc",)
    nargs = "?"
    command_opts = ["content", "full"]
    arg_completion = staticmethod(completions.filename_arg_completion)
    help = "Send an XML operation body, or a full message-id preserving <rpc> envelope"

    def invoke(self, mc: Any, ns: Any, filename: str = "-") -> Any:
        content = getattr(ns, "rpc_content", None) or getattr(ns, "content", None)
        if isinstance(content, str):
            filename = content
        root = _parse_xml(filename)
        manager = _manager(mc)
        if _local_name(root) == "rpc":
            children = list(root)
            is_full = bool(getattr(ns, "full", False))
            if is_full and root.get("message-id") is None:
                raise ValueError("--full RPC input must contain message-id")
            if root.get("message-id") is not None and (is_full or not getattr(ns, "content", False)):
                reply = manager.xrpc(root)
            elif len(children) == 1:
                reply = manager.rpc(children[0])
            else:
                raise ValueError("An RPC envelope must contain exactly one operation")
        else:
            reply = manager.rpc(root)
        return reply._root[0] if isinstance(reply._root, list) else reply._root


def _nmda_nsmap(ns: Any, *expressions: str | None) -> dict[str, str]:
    mapping = nsmap(ns, *expressions)
    # These prefixes are used by the RFC 8526 datastore and origin identity
    # values.  Reserve them so a bare `operational` or `system` argument is
    # serialized as a QName the server can resolve.
    mapping["ds"] = DATASTORES_NS
    mapping["or"] = ORIGIN_NS
    return mapping


def _append_nmda_filter(node: etree._Element, ns: Any) -> None:
    xpath = getattr(ns, "xpath", None)
    subtree = getattr(ns, "filter", None)
    filter_file = getattr(ns, "filter_file", None)
    if xpath and (subtree or filter_file):
        raise ValueError("get-data accepts either --xpath or --filter/--filter-file")
    if xpath:
        xpath_node = etree.SubElement(
            node, qualify("xpath-filter", NMDA_NS), nsmap=_nmda_nsmap(ns, xpath)
        )
        xpath_node.text = xpath
        return
    source = filter_file or subtree
    if not source:
        return
    target = etree.SubElement(node, qualify("subtree-filter", NMDA_NS))
    source_text = str(source)
    if source_text.lstrip().startswith("<") or Path(source_text).is_file():
        data = _parse_xml(source_text)
        if _local_name(data) in {"filter", "config"}:
            target.extend(list(data))
        else:
            target.append(data)
        return
    _target, rest = XpathParser(source_text, nsmap(ns, source_text)).build_tree_on(target)
    if rest:
        raise PathExpressionException("The get-data filter must be a simple path")


class GetData(Operation):
    option = "get-data"
    name = "get_data"
    nargs = "?"
    command_opts = [
        "datastore", "xpath", "filter", "filter_file", "wdefaults", "depth", "origin", "with_origin"
    ]
    help = "Retrieve an NMDA datastore using RFC 8526 get-data"

    def invoke(self, mc: Any, ns: Any, datastore: str | None = None) -> Any:
        node = new_ele_ns(
            "get-data", NMDA_NS,
            nsmap=_nmda_nsmap(ns, getattr(ns, "xpath", None)),
        )
        selected_datastore = getattr(ns, "datastore", None) or datastore or "operational"
        etree.SubElement(node, qualify("datastore", NMDA_NS)).text = (
            selected_datastore if ":" in selected_datastore else "ds:" + selected_datastore
        )
        _append_nmda_filter(node, ns)
        if getattr(ns, "wdefaults", None):
            etree.SubElement(node, qualify("with-defaults", DEFAULTS_NS)).text = ns.wdefaults
        if getattr(ns, "depth", None) is not None:
            depth = str(ns.depth)
            if depth != "unbounded" and (not depth.isdigit() or int(depth) < 1):
                raise ValueError("--depth must be a positive integer or unbounded")
            etree.SubElement(node, qualify("max-depth", NMDA_NS)).text = depth
        for origin in getattr(ns, "origin", None) or []:
            etree.SubElement(node, qualify("origin-filter", NMDA_NS)).text = (
                origin if ":" in origin else "or:" + origin
            )
        if getattr(ns, "with_origin", False):
            etree.SubElement(node, qualify("with-origin", NMDA_NS))
        return _root(_manager(mc).rpc(node))


class EditData(Operation):
    option = "edit-data"
    name = "edit_data"
    nargs = "?"
    command_opts = ["datastore", "default_operation"]
    help = "Edit an NMDA datastore using RFC 8526 edit-data"

    def invoke(self, mc: Any, ns: Any, filename: str = "-") -> Any:
        data = _parse_xml(filename)
        node = new_ele_ns("edit-data", NMDA_NS, nsmap=_nmda_nsmap(ns))
        selected_datastore = getattr(ns, "datastore", None) or "operational"
        etree.SubElement(node, qualify("datastore", NMDA_NS)).text = (
            selected_datastore if ":" in selected_datastore else "ds:" + selected_datastore
        )
        config = etree.SubElement(node, qualify("config", NMDA_NS))
        if _local_name(data) == "config":
            config.extend(list(data))
        else:
            config.append(data)
        default_operation = getattr(ns, "default_operation", None)
        if default_operation:
            etree.SubElement(node, qualify("default-operation", NMDA_NS)).text = default_operation
        return _root(_manager(mc).rpc(node))


class Capabilities(Operation):
    name = option = "capabilities"
    command_opts = ["capability_raw"]
    help = "List parsed server capabilities; --raw prints the original capability URIs"

    def invoke(self, mc: Any, ns: Any) -> TextResult:
        manager = _manager(mc)
        values = list(manager.server_capabilities or [])
        if getattr(ns, "capability_raw", False) or getattr(ns, "raw", None) is not None:
            return TextResult("\n".join(values))
        lines = ["Server capabilities (%d):" % len(values)]
        for label, uri in capability_rows(values):
            lines.append("  %-32s %s" % (label, uri))
        return TextResult("\n".join(lines))


class Namespaces(Operation):
    name = option = "namespaces"
    aliases = ("namespace",)
    nargs = "?"
    choices = ["refresh"]
    help = "List learned YANG namespace aliases or refresh the server YANG Library"

    def invoke(self, mc: Any, ns: Any, action: str | None = None) -> TextResult:
        if action == "refresh":
            refresh = getattr(mc, "refresh_namespaces", None)
            if not callable(refresh):
                raise RuntimeError("Namespace refresh requires an application session")
            refresh(force=True)
        registry = getattr(mc, "namespace_registry", None)
        learned = dict(getattr(ns, "discovered_namespaces", {}) or {})
        sources = {alias: "device/cache" for alias in learned}
        if registry is not None:
            learned.update(registry.mapping())
            sources.update({alias: source for alias, _uri, source in registry.rows()})
        explicit = _explicit_nsmap(ns)
        effective = dict(COMMON_NAMESPACES)
        effective.update(learned)
        effective.update(explicit)
        lines = ["YANG namespace aliases (%d):" % len(effective)]
        for alias, uri in sorted(effective.items()):
            if alias in explicit:
                source = "explicit"
            elif alias in learned:
                source = sources.get(alias, "device/cache")
            else:
                source = "built-in"
            lines.append("  %-20s %-12s %s" % (alias, source, uri))
        return TextResult("\n".join(lines))


class Status(Operation):
    name = option = "status"
    help = "Display transport, peer, session, capability, and connection timing information"

    def invoke(self, mc: Any, _ns: Any) -> TextResult:
        lines = getattr(mc, "status_lines", None)
        if callable(lines):
            return TextResult("\n".join(lines()))
        manager = _manager(mc)
        return TextResult("Connected: %s\nTransport: %s\nSession ID: %s\nServer Capabilities: %d" % (
            manager.connected, manager._session.__class__.__name__, manager.session_id,
            len(list(manager.server_capabilities or [])),
        ))


class Connect(Operation):
    name = option = "connect"
    command_opts = ["connection"]
    help = "Connect to a NETCONF server over SSH or TLS"

    def invoke(self, mc: Any, ns: Any) -> TextResult:
        connect = getattr(mc, "connect_from_command", None)
        if not callable(connect):
            raise RuntimeError("connect is only available in the interactive console")
        connect(ns)
        return TextResult("Connected.")


class Listen(Operation):
    name = option = "listen"
    command_opts = ["connection"]
    help = "Wait for one SSH or TLS NETCONF Call Home connection"

    def invoke(self, mc: Any, ns: Any) -> TextResult:
        listen = getattr(mc, "listen_from_command", None)
        if not callable(listen):
            raise RuntimeError("listen is only available in the interactive console")
        listen(ns)
        return TextResult("NETCONF Call Home session established.")


class Disconnect(Operation):
    name = option = "disconnect"
    help = "Close the current session without sending another RPC"

    def invoke(self, mc: Any, _ns: Any) -> TextResult:
        disconnect = getattr(mc, "disconnect", None)
        if not callable(disconnect):
            raise RuntimeError("disconnect is only available in the interactive console")
        disconnect()
        return TextResult("Disconnected.")


class OutputFormat(Operation):
    name = "output_format"
    option = "outputformat"
    nargs = 1
    choices = ["raw", "pretty"]
    help = "Set XML output format for subsequent commands"

    def invoke(self, mc: Any, _ns: Any, value: str) -> TextResult:
        mc.output_mode = value
        return TextResult("Output format: %s" % value)


class Help(Operation):
    name = option = "help"
    nargs = "?"
    help = "Display command names or a command's contextual help"

    def invoke(self, _mc: Any, _ns: Any, command: str | None = None) -> TextResult:
        if command:
            for operation in OPERATIONS:
                if operation.option == command or command in operation.aliases:
                    return TextResult("%s: %s" % (command, operation.help or "No additional help."))
        names = sorted({name for operation in OPERATIONS for name in ((operation.option,) + operation.aliases)
                        if name})
        return TextResult("Available commands:\n" + "\n".join("  " + name for name in names))


class Auth(Operation):
    name = option = "auth"
    help = "Display the effective SSH authentication mode (credentials are masked)"

    def invoke(self, mc: Any, _ns: Any) -> TextResult:
        settings = getattr(mc, "settings", None)
        if settings is None:
            return TextResult("Authentication settings unavailable")
        return TextResult("SSH authentication:\n  username: %s\n  private key: %s\n  agent: %s\n  look for keys: %s\n  password: %s" % (
            settings.username or "-", settings.key or "-", settings.allow_agent,
            settings.look_for_keys, "configured (masked)" if settings.password else "not configured",
        ))


class KnownHosts(Operation):
    name = option = "knownhosts"
    help = "Display known_hosts verification settings"

    def invoke(self, mc: Any, _ns: Any) -> TextResult:
        settings = getattr(mc, "settings", None)
        return TextResult("known_hosts: %s\nverification: %s" % (
            getattr(settings, "known_hosts", None) or "default Paramiko locations",
            "enabled" if getattr(settings, "hostkey_verify", False) else "disabled",
        ))


class Cert(Operation):
    name = option = "cert"
    help = "Display TLS certificate paths without exposing key material"

    def invoke(self, mc: Any, _ns: Any) -> TextResult:
        settings = getattr(mc, "settings", None)
        return TextResult("client certificate: %s\nprivate key: %s\ntrusted CA: %s\nCRL: %s" % (
            getattr(settings, "cert", None) or "-", getattr(settings, "key", None) or "-",
            getattr(settings, "trusted_ca", None) or "system/default store",
            getattr(settings, "crl", None) or "-",
        ))


class Sleep(Operation):
    option = name = "sleep"
    nargs = 1
    help = "Wait for the specified number of seconds"

    def invoke(self, _mc: Any, _ns: Any, timeout: str) -> etree._Element:
        time.sleep(float(timeout))
        return new_ele("ok")


class Action(Operation):
    option = name = "action"
    nargs = 1
    help = "Invoke a YANG 1.1 action with a simple path expression"

    def invoke(self, mc: Any, ns: Any, expression: str) -> Any:
        action = new_ele_ns("action", YANG_NS_1_0)
        _target, rest = XpathParser(expression, nsmap(ns, expression)).build_tree_on(action)
        if rest:
            raise PathExpressionException("The action expression needs to be a simple path")
        return _root(_manager(mc).rpc(action))


class RpcAction(Operation):
    option = name = "run-rpc"
    nargs = 1
    help = "Invoke a YANG RPC using a simple path expression"

    def invoke(self, mc: Any, ns: Any, expression: str) -> Any:
        root = new_ele("root")
        target, rest = XpathParser(expression, nsmap(ns, expression)).build_tree_on(root)
        if rest or len(root) != 1:
            raise PathExpressionException("The RPC needs to be uniquely identified")
        return _root(_manager(mc).rpc(target))


class Exit(Operation):
    option = name = "exit"
    aliases = ("quit",)
    help = "Exit the interactive console"

    def invoke(self, mc: Any, _ns: Any) -> None:
        mc.exit_requested = True
        return None


class FileRpc(Operation):
    def invoke(self, mc: Any, _ns: Any, elem: etree._Element) -> Any:
        return _manager(mc).xrpc(elem)._root


class FilenameOperations:
    """Iterator over an old RFC 4742 v1.0-delimited message file."""

    def __init__(self, filename: str):
        self.filename = filename

    def operations(self) -> Iterable[tuple[Operation, list[Any]]]:
        if self.filename == "-":
            data = STDIN.read()
        else:
            data = Path(self.filename).read_bytes()
        messages = [message.strip() for message in data.split(session.MSG_DELIM)]
        parser = etree.XMLParser(remove_blank_text=True)
        trees = [etree.fromstring(message, parser) for message in messages if message]
        for tree in trees:
            if _local_name(tree) == "hello":
                continue
            if len(tree) and _local_name(tree[0]) == "close-session":
                continue
            yield FileRpc(), [tree]


def run_rpc_dry() -> None:
    """Make ncclient RPC requests return their generated XML for legacy --dry."""

    class Reply:
        def __init__(self, rpc: RPC, operation: etree._Element):
            root = new_ele("rpc", {"message-id": rpc._id})
            root.append(operation)
            self.data = root
            self._root = [root]

    def convert_query(rpc: RPC, operation: etree._Element) -> Reply:
        return Reply(rpc, operation)

    RPC._request = convert_query


OPERATIONS: list[type[Operation]] = [
    Hello, Get, GetConfig, KillSession, CloseSession, DiscardChanges, Lock, Unlock, Commit,
    CancelCommit, Validate, CopyRunningToStartup, DeleteConfig, CopyConfig,
    EditConfig, EditConfig1, Set, Delete, Create, GetSchema, CreateSubscription,
    Subscribe, Watch, Rpc, GetData, EditData, Capabilities, Namespaces, Status, Connect,
    Listen, Disconnect, OutputFormat, Auth, KnownHosts, Cert, Help, Action,
    RpcAction, Sleep, Exit,
]
OPERATION_OPTS = {
    name: operation() for operation in OPERATIONS for name in ((operation.option,) + operation.aliases)
    if name
}


def data_filter(ns: Any, path: str | None = None) -> Any:
    path = path if path is not None else getattr(ns, "xpath", None)
    if path is not None:
        return ("xpath", (nsmap(ns, path), path))
    filter_file = getattr(ns, "filter_file", None)
    if filter_file:
        return _parse_xml(filter_file)
    subtree = getattr(ns, "filter", None)
    if subtree is None:
        return None
    if str(subtree).lstrip().startswith("<") or Path(str(subtree)).is_file():
        return _parse_xml(str(subtree))
    filter_node = new_ele("filter")
    _target, rest = XpathParser(str(subtree), nsmap(ns, str(subtree))).build_tree_on(filter_node)
    if rest:
        raise PathExpressionException("The filter expression needs to be a simple path")
    return filter_node


def subscription_filter(ns: Any, allow_simple_path: bool = False) -> etree._Element | None:
    xpath = getattr(ns, "xpath", None)
    if xpath:
        return util.build_filter(("xpath", (nsmap(ns, xpath), xpath)))
    filter_file = getattr(ns, "filter_file", None)
    subtree = getattr(ns, "filter", None)
    if filter_file or subtree:
        if filter_file:
            return _parse_xml(filter_file)
        if str(subtree).lstrip().startswith("<") or Path(str(subtree)).is_file():
            return _parse_xml(str(subtree))
        if allow_simple_path or str(subtree).startswith("/"):
            filter_node = new_ele("filter")
            _target, rest = XpathParser(
                str(subtree), nsmap(ns, str(subtree))
            ).build_tree_on(filter_node)
            if rest:
                raise PathExpressionException("The filter expression needs to be a simple path")
            return filter_node
    return None


def _explicit_nsmap(ns: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    values = getattr(ns, "ns", None)
    if values is None:
        return result
    if isinstance(values, str):
        values = [values]
    for assignment in values:
        if "=" not in assignment:
            raise ValueError("Namespace must use prefix=URI")
        prefix, uri = (part.strip() for part in assignment.split("=", 1))
        if not prefix or not uri:
            raise ValueError("Namespace must use a non-empty prefix=URI")
        result[prefix] = uri
    return result


def nsmap(ns: Any, *expressions: str | None) -> dict[str, str]:
    """Resolve explicit, device-discovered, and built-in XPath prefixes.

    Device and built-in aliases are added only when referenced, keeping emitted
    XML compact even when a YANG Library advertises hundreds of modules.
    Explicit mappings take precedence over device mappings, which take
    precedence over built-ins. Unknown prefixes fail locally with an actionable
    message instead of producing a remote ``Failed to resolve prefix`` error.
    """

    result = _explicit_nsmap(ns)
    discovered = getattr(ns, "discovered_namespaces", None)
    discovered = discovered if isinstance(discovered, dict) else {}

    referenced: set[str] = set()
    for expression in expressions:
        if not expression:
            continue
        unquoted = _QUOTED_XPATH_TEXT.sub("", str(expression))
        referenced.update(_PREFIX_REFERENCE.findall(unquoted))

    missing = []
    for prefix in sorted(referenced):
        if prefix in result:
            continue
        uri = discovered.get(prefix) or COMMON_NAMESPACES.get(prefix)
        if uri is None:
            missing.append(prefix)
        else:
            result[prefix] = uri
    if missing:
        assignments = " ".join("%s=URI" % prefix for prefix in missing)
        raise ValueError(
            "Unknown YANG namespace prefix %s; add --ns %s when starting the console"
            % (", ".join(repr(prefix) for prefix in missing), assignments)
        )
    return result


def _xml_text(element: etree._Element) -> str:
    return etree.tostring(element, encoding="unicode", pretty_print=True).rstrip()


def _schema_text(root: etree._Element) -> str:
    for element in root.iter():
        if _local_name(element) in {"schema", "data"} and element.text and not len(element):
            return element.text
    if root.text and not len(root):
        return root.text
    return _xml_text(root)
