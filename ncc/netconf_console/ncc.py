#!/usr/bin/env python

"""Windows-friendly NETCONF CLI and interactive console."""

from __future__ import print_function

import argparse
import getpass
import logging
import re
import shlex
import sys
import traceback
from pathlib import Path

from lxml import etree
from ncclient.operations.rpc import RPCError

from . import completions, operations
from .config import get_profile
from .session import (
    CallHomeCancelled,
    ConnectionSettings,
    ConsoleContext,
    NotConnectedError,
)
from .trace import TraceSink, redact_secrets


LOGGER = logging.getLogger(__name__)


class OperationArgAction(argparse.Action):
    """Append command operations in the order they occur on the CLI."""

    def __init__(self, *args, **kwargs):
        self.operation = kwargs.pop("const")
        super().__init__(*args, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        if self.operation.nargs == "?" and values is self.operation:
            values = []
        operations_list = getattr(namespace, "operations", None)
        if operations_list is None:
            operations_list = []
            namespace.operations = operations_list
        elif not getattr(namespace, "_operations_initialized", False):
            # argparse reuses the parser's default object for repeated
            # parse_args calls.  Copy it on first use so one parse never leaks
            # commands into the next parse.
            namespace.operations = list(operations_list)
            operations_list = namespace.operations
        namespace._operations_initialized = True
        operations_list.append((self.operation, values if isinstance(values, list) else [values]))


def parse_expr_args(expression):
    """Split an interactive command without treating Windows backslashes as escapes."""

    lexer = shlex.shlex(expression, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)


class ExpressionOperation(operations.Operation):
    def __init__(self, exprparser, process_errors=True):
        self.exprparser = exprparser
        self.process_errors = process_errors

    def invoke(self, mc, parent_ns, expression):
        expression = expression.strip()
        if not expression:
            return None
        try:
            ns = self.exprparser.parse_args(
                parse_expr_args(expression),
                namespace=argparse.Namespace(**vars(parent_ns)),
            )
        except (ParserException, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return None
        if getattr(ns, "cmd_parser", None) is not None:
            ns.cmd_parser.print_help()
            return None
        try:
            operation = ns.op
            if hasattr(ns, "value"):
                value = ns.value
                if value is None:
                    return operation.invoke(mc, ns)
                if operation.nargs == "*":
                    return operation.invoke(mc, ns, *value)
                return operation.invoke(mc, ns, value)
            return operation.invoke(mc, ns)
        except RPCError as exc:
            if self.process_errors:
                return exc.xml
            raise
        except Exception as exc:
            if self.process_errors:
                report_exception(exc, getattr(ns, "debug", False))
                return None
            raise


def report_exception(exc, debug=False):
    if debug:
        # Keep useful diagnostics while applying the same secret policy as the
        # normal error path; exception messages can include custom RPC text.
        print(redact_secrets(traceback.format_exc()), file=sys.stderr, end="")
        return
    message = redact_secrets(str(exc))
    if len(message) > 140:
        message = message[:120] + "..."
    print("Operation failed: %s - %s" % (exc.__class__.__name__, message), file=sys.stderr)


class ParserException(Exception):
    pass


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ParserException(message)


def _descriptor(options, **kwargs):
    result = dict(kwargs)
    result["options"] = list(options)
    return result


command_option_args = {
    "style": _descriptor(["-s", "--outputStyle"], dest="style", default=[],
                          choices=["plain", "noaaa"], nargs="*"),
    "db": _descriptor(["--db", "--target"], dest="db", default="running",
                       help="Configuration datastore target/source."),
    "timeout": _descriptor(["--timeout", "--confirm-timeout"], dest="timeout", default=None, type=float,
                             help="Timeout in seconds (connection or confirmed commit)."),
    "wdefaults": _descriptor(["--with-defaults"], dest="wdefaults", default=None,
                             choices=["explicit", "trim", "report-all", "report-all-tagged"],
                             help="RFC 6243 default handling."),
    "winactive": _descriptor(["--with-inactive"], dest="winactive", action="store_true",
                             default=False, help="Request vendor with-inactive data."),
    "xpath": _descriptor(["-x", "--xpath"], dest="xpath", default=None,
                          help="XPath filter."),
    "filter": _descriptor(["--filter"], dest="filter", default=None,
                           help="Subtree path, XML, or filter file."),
    "filter_file": _descriptor(["--filter-file"], dest="filter_file", default=None,
                                help="File containing a subtree filter."),
    "test": _descriptor(["-t", "--test-option"], dest="test", default=None,
                         choices=["test-then-set", "set", "test-only"],
                         help="edit-config test-option."),
    "operation": _descriptor(["-o", "--operation"], dest="operation", default="merge",
                              choices=["merge", "replace", "create"],
                              help="nc:operation for set."),
    "deloperation": _descriptor(["--del-operation"], dest="deloperation", default="remove",
                                 choices=["remove", "delete"],
                                 help="nc:operation for delete."),
    "default_operation": _descriptor(["--default-operation", "--defop"],
                                       dest="default_operation", default=None,
                                       choices=["merge", "replace", "none"],
                                       help="edit-config default-operation."),
    "error_option": _descriptor(["--error-option", "--error"], dest="error_option", default=None,
                                 choices=["stop-on-error", "continue-on-error", "rollback-on-error"],
                                 help="edit-config error-option."),
    "source": _descriptor(["--source"], dest="source", default=None,
                           help="Datastore source for copy-config."),
    "persist": _descriptor(["--persist"], dest="persist", default=None,
                            help="Confirmed commit persist token."),
    "persist_id": _descriptor(["--persist-id"], dest="persist_id", default=None,
                               help="Confirmed commit persist-id token."),
    "stream": _descriptor(["--stream"], dest="stream", default=None,
                           help="RFC 5277 notification stream."),
    "start": _descriptor(["--start-time", "--begin"], dest="start", default=None,
                          help="Notification replay start time."),
    "stop": _descriptor(["--stop-time", "--end"], dest="stop", default=None,
                         help="Notification replay stop time."),
    "out": _descriptor(["--out", "--output-file"], dest="out", default=None,
                        help="Write schema or operation output to a file."),
    "capability_raw": _descriptor(["--raw"], dest="capability_raw", action="store_true",
                                  default=False, help="Print raw capability URIs."),
    "content": _descriptor(["--content"], dest="content", nargs="?", const=True, default=None,
                            help="RPC content file (operation body)."),
    "full": _descriptor(["--full"], dest="full", action="store_true", default=False,
                         help="Treat RPC input as a full envelope."),
    "schema_model": _descriptor(["--model"], dest="schema_model", default=None,
                                 help="YANG schema identifier."),
    "schema_version": _descriptor(["--version"], dest="schema_version", default=None,
                                   help="YANG schema version."),
    "schema_format": _descriptor(["--format"], dest="schema_format", default=None,
                                  help="YANG schema format."),
    "datastore": _descriptor(["--datastore"], dest="datastore", default=None,
                              help="NMDA datastore."),
    "depth": _descriptor(["--depth"], dest="depth", default=None,
                          help="NMDA max-depth (positive integer or unbounded)."),
    "origin": _descriptor(["--origin"], dest="origin", action="append", default=None,
                           help="NMDA origin (repeatable)."),
    "with_origin": _descriptor(["--with-origin"], dest="with_origin", action="store_true",
                                default=False, help="Request NMDA origin metadata."),
}


def _add_connection_options(parser, prefix="cmd_"):
    """Add connection/listen options for an interactive command."""

    def dest(name):
        return prefix + name

    parser.add_argument("--transport", dest=dest("transport"), choices=["ssh", "tls", "tcp"], default=None)
    parser.add_argument("--ssh", dest=dest("ssh"), action="store_true", default=False,
                        help="Select SSH transport.")
    parser.add_argument("--tls", dest=dest("tls"), action="store_true", default=False,
                        help="Select TLS transport.")
    parser.add_argument("--tcp", dest=dest("tcp"), action="store_true", default=False,
                        help="Select the legacy plain TCP transport.")
    parser.add_argument("--host", dest=dest("host"), default=None)
    parser.add_argument("--port", dest=dest("port"), type=int, default=None)
    parser.add_argument("--username", "--user", "--login", dest=dest("username"), default=None)
    parser.add_argument("--password", dest=dest("password"), nargs="?", const=True, default=None)
    parser.add_argument("--key", "--priv-key", "--privKeyFile", dest=dest("key"), default=None)
    parser.add_argument("--known-hosts", dest=dest("known_hosts"), default=None)
    parser.add_argument("--hostkey-verify", dest=dest("hostkey_verify"), action="store_true", default=None)
    parser.add_argument("--no-hostkey-verify", dest=dest("hostkey_verify"), action="store_false")
    parser.add_argument("--ssh-config", dest=dest("ssh_config"), nargs="?", const=True, default=None)
    parser.add_argument("--no-agent", dest=dest("allow_agent"), action="store_false", default=None)
    parser.add_argument("--agent", dest=dest("allow_agent"), action="store_true")
    parser.add_argument("--no-look-for-keys", dest=dest("look_for_keys"), action="store_false", default=None)
    parser.add_argument("--look-for-keys", dest=dest("look_for_keys"), action="store_true")
    parser.add_argument("--keepalive", dest=dest("keepalive"), type=int, default=None)
    parser.add_argument("--bind", dest=dest("bind"), default=None)
    parser.add_argument("--cert", dest=dest("cert"), default=None)
    parser.add_argument("--trusted-ca", "--trusted", dest=dest("trusted_ca"), default=None)
    parser.add_argument("--crl", dest=dest("crl"), default=None)
    parser.add_argument("--tls-version", dest=dest("tls_version"), default=None)
    parser.add_argument("--tls-server-name", "--peername", dest=dest("tls_server_name"), default=None)
    parser.add_argument("--no-hostname-verify", dest=dest("no_hostname_verify"),
                        action="store_true", default=None,
                        help="Disable TLS hostname verification but retain CA validation.")
    parser.add_argument("--netconf-version", dest=dest("netconf_version"),
                        choices=["1.0", "1.1"], default=None,
                        help="Force NETCONF framing version.")
    parser.add_argument("--huge-tree", dest=dest("huge_tree"), action="store_true", default=None,
                        help="Allow very large XML replies.")
    parser.add_argument("--timeout", dest=dest("timeout"), type=float, default=None)
    parser.add_argument("--reply-timeout", dest=dest("rpc_timeout"), type=float, default=None)


def command_options_parser(operation=None):
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_argument_group("Command options")
    options = command_option_args.keys() if operation is None else operation.command_opts
    for option in options:
        # These options are contextual command switches.  The top-level
        # parser owns their global counterparts (`--version` for NETCONF
        # framing and `--raw` for output), while interactive subparsers still
        # receive them for `get-schema --version` and `capabilities --raw`.
        if operation is None and option in {"schema_version", "capability_raw"}:
            continue
        if option == "connection":
            _add_connection_options(group)
            continue
        descriptor = command_option_args[option]
        kwargs = {key: value for key, value in descriptor.items() if key != "options"}
        group.add_argument(*descriptor["options"], **kwargs)
    return parser


def argparser():
    parser = argparse.ArgumentParser(
        prog="netconf-console2",
        description="Windows-native NETCONF CLI for O-RAN O-RU management-plane testing.",
        parents=[command_options_parser()],
    )
    parser.add_argument("-v", "--version", "--netconf-version", dest="netconf_version",
                        choices=["1.0", "1.1"], help="Force NETCONF framing version.")
    parser.add_argument("--transport", choices=["ssh", "tls", "tcp"], default=None,
                        help="Transport (SSH, TLS, or legacy non-standard TCP).")
    parser.add_argument("--tcp", action="store_true", help="Legacy alias for --transport tcp.")
    parser.add_argument("-u", "--user", "--username", "--login", dest="username", default=None,
                        help="SSH username.")
    parser.add_argument("-p", "--password", dest="password", nargs="?", const=True, default=None,
                        help="Password; omit the value to prompt securely.")
    parser.add_argument("--key", "--priv-key", "--privKeyFile", dest="key", default=None,
                        help="SSH private key or TLS private key.")
    parser.add_argument("--known-hosts", dest="known_hosts", default=None,
                        help="SSH known_hosts file.")
    parser.add_argument("--ssh-config", nargs="?", const=True, default=None,
                        help="OpenSSH config file, or the platform default when omitted.")
    parser.add_argument("--hostkey-verify", dest="hostkey_verify", action="store_true", default=None,
                        help="Verify SSH host keys against known_hosts.")
    parser.add_argument("--no-hostkey-verify", dest="hostkey_verify", action="store_false")
    parser.add_argument("--host", dest="host", default=None, help="NETCONF host or address.")
    parser.add_argument("--port", dest="port", type=int, default=None, help="NETCONF port.")
    parser.add_argument("--reply-timeout", "--rpc-timeout", dest="reply_timeout", type=float,
                        default=None, help="RPC reply timeout in seconds.")
    parser.add_argument("--connect-timeout", dest="connect_timeout", type=float, default=None,
                        help="TCP/TLS/SSH connect and hello timeout in seconds.")
    parser.add_argument("--keepalive", dest="keepalive", type=int, default=None,
                        help="SSH keepalive interval in seconds.")
    parser.add_argument("--bind", dest="bind", default=None, help="Local source address for SSH.")
    parser.add_argument("--agent", dest="allow_agent", action="store_true", default=None,
                        help="Use an SSH agent when available.")
    parser.add_argument("--no-agent", dest="allow_agent", action="store_false")
    parser.add_argument("--look-for-keys", dest="look_for_keys", action="store_true", default=None)
    parser.add_argument("--no-look-for-keys", dest="look_for_keys", action="store_false")
    parser.add_argument("--cert", dest="cert", help="TLS client certificate PEM file.")
    parser.add_argument("--trusted-ca", "--trusted", dest="trusted_ca",
                        help="Trusted CA PEM file or directory for TLS peer verification.")
    parser.add_argument("--crl", dest="crl", help="Optional PEM CRL file/directory.")
    parser.add_argument("--tls-version", dest="tls_version", help="TLS version: auto, 1.2, or 1.3.")
    parser.add_argument("--tls-server-name", "--peername", dest="tls_server_name",
                        help="TLS reference/SNI name (important for Call Home).")
    parser.add_argument("--schema-version", dest="schema_version",
                        help="YANG schema version for a top-level --get-schema command.")
    parser.add_argument("--capabilities-raw", dest="capability_raw", action="store_true",
                        help="Print raw capability URIs for --capabilities.")
    parser.add_argument("--no-hostname-verify", dest="no_hostname_verify", action="store_true", default=None,
                        help="Disable TLS hostname verification (certificate chain remains verified).")
    parser.add_argument("--call-home", action="store_true", help="Wait for one Call Home connection.")
    parser.add_argument("--listen-host", default=None, help="Call Home bind address.")
    parser.add_argument("--listen-port", type=int, default=None, help="Call Home listen port.")
    parser.add_argument("--output", choices=["raw", "pretty"], default="pretty",
                        help="XML output format.")
    parser.add_argument("--watch", action="store_true",
                        help="After subscribe/create-subscription, consume notifications until Ctrl+C.")
    parser.add_argument("--raw", nargs="?", const=True, default=None, metavar="FILE",
                        help="Legacy raw output; optionally capture wire XML to FILE.")
    parser.add_argument("--trace", action="store_true", help="Trace redacted NETCONF SEND/RECV.")
    parser.add_argument("--trace-file", help="Write redacted trace records to FILE.")
    parser.add_argument("--verbose", action="store_true", help="Enable application logging.")
    parser.add_argument("--debug", action="store_true", help="Print tracebacks on errors.")
    parser.add_argument("-N", "--ns", dest="ns", nargs="*", default=None,
                        help=("Override/add XPath namespace assignments prefix=URI; device "
                              "YANG Library and common fallback aliases resolve automatically."))
    parser.add_argument("--huge-tree", action="store_true", default=None, help="Allow very large XML replies.")
    parser.add_argument("--profile", help="Connection profile name.")
    parser.add_argument("--profile-file", help="Use a non-default TOML profile file.")

    parser.set_defaults(operations=[])
    command_group = parser.add_argument_group("Commands")
    for operation_class in operations.OPERATIONS:
        operation = operation_class()
        names = (operation.option,) + tuple(operation.aliases)
        for name in names:
            if not name:
                continue
            # argparse already owns --help, and --cert is the global TLS
            # certificate option.  Session-management and display commands
            # are intentionally interactive-only; batch mode has explicit
            # global options for connection/output and --call-home for listen.
            if name in {
                "help", "cert", "watch", "connect", "listen", "disconnect",
                "outputformat", "auth", "knownhosts",
            }:
                continue
            destination = (operation.dest or name).replace("-", "_")
            command_group.add_argument(
                "--" + name,
                dest=destination,
                nargs=operation.nargs,
                action=OperationArgAction,
                const=operation_class(),
                choices=operation.choices,
                help=operation.help,
            )
    expression_parser_obj = expression_parser()
    parser.add_argument("-e", "--expr", action=OperationArgAction, nargs=1,
                        const=ExpressionOperation(expression_parser_obj))
    parser.add_argument("--dry", action="store_true", default=False,
                        help="Return generated RPC XML without sending it.")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Run the persistent interactive console.")
    parser.add_argument("filename", nargs="?",
                        help="Legacy file containing NETCONF 1.0-delimited messages.")
    return parser


def expression_parser(parsercls=argparse.ArgumentParser, custom_help=False, **kwargs):
    parser = parsercls(prog="", **kwargs)
    subparsers = parser.add_subparsers(dest="command", help="NETCONF commands")
    for operation_class in operations.OPERATIONS:
        operation = operation_class()
        parser_args = {
            "parents": [command_options_parser(operation)],
            "help": operation.help,
        }
        if custom_help:
            parser_args["add_help"] = False
        for name in (operation.option,) + tuple(operation.aliases):
            if not name:
                continue
            subparser = subparsers.add_parser(name, **parser_args)
            if custom_help:
                subparser.add_argument("-h", "--help", action="store_const", const=subparser,
                                       dest="cmd_parser", help="show this help message")
            subparser.set_defaults(op=operation_class())
            if operation.nargs == 1:
                subparser.add_argument("value", choices=operation.choices)
            elif operation.nargs in ("?", "*"):
                subparser.add_argument(
                    "value", nargs=operation.nargs, choices=operation.choices
                )
    return parser


def _profile_value(profile, *names):
    for name in names:
        if name in profile:
            return profile[name]
    return None


def resolve_namespace(ns):
    """Apply a named TOML profile and application defaults to argparse output."""

    if ns.operations is None:
        ns.operations = []
    profile = get_profile(ns.profile, ns.profile_file) if ns.profile else {}
    # Command-line values are already present; profile values only fill gaps.
    for attribute, names in {
        "host": ("host",), "port": ("port",), "transport": ("transport",),
        "username": ("username", "user"), "key": ("key", "privKeyFile", "priv_key"),
        "known_hosts": ("known_hosts",), "hostkey_verify": ("hostkey_verify",),
        "ssh_config": ("ssh_config",), "allow_agent": ("allow_agent",),
        "look_for_keys": ("look_for_keys",), "keepalive": ("keepalive",),
        "bind": ("bind",), "cert": ("cert",), "trusted_ca": ("trusted_ca", "trusted"),
        "crl": ("crl",), "tls_version": ("tls_version",),
        "tls_server_name": ("tls_server_name", "peername"),
        "timeout": ("timeout",), "reply_timeout": ("reply_timeout", "rpc_timeout"),
        "netconf_version": ("netconf_version",),
        "no_hostname_verify": ("no_hostname_verify",),
        "huge_tree": ("huge_tree",),
        "listen_host": ("listen_host",), "listen_port": ("listen_port",),
    }.items():
        if getattr(ns, attribute, None) is None:
            value = _profile_value(profile, *names)
            if value is not None:
                setattr(ns, attribute, value)
    if ns.tcp:
        ns.transport = "tcp"
    if ns.transport is None:
        ns.transport = "ssh"
    if ns.host is None:
        ns.host = "127.0.0.1"
    if ns.port is None:
        ns.port = {"ssh": 830, "tls": 6513, "tcp": 2023}.get(ns.transport, 830)
    if ns.listen_host is None:
        ns.listen_host = "0.0.0.0"
    if ns.listen_port is None:
        ns.listen_port = 4335 if ns.transport == "tls" else 4334
    if ns.allow_agent is None:
        ns.allow_agent = True
    if ns.look_for_keys is None:
        ns.look_for_keys = True
    if ns.no_hostname_verify is None:
        ns.no_hostname_verify = False
    if ns.huge_tree is None:
        ns.huge_tree = False
    if ns.hostkey_verify is None:
        ns.hostkey_verify = False
    # Keep credentials caller-controlled.  The historical CLI defaulted to
    # admin/admin; retaining the option names is compatible, but embedding a
    # password in a Windows client would violate the security requirement.
    if ns.connect_timeout is not None:
        ns.timeout = ns.connect_timeout
    elif ns.timeout is None:
        ns.timeout = ns.reply_timeout or 30.0
    if ns.reply_timeout is None:
        ns.reply_timeout = ns.timeout
    if ns.raw is not None:
        ns.output = "raw"
        if isinstance(ns.raw, str) and ns.raw not in {"-", ""}:
            ns.raw_file = ns.raw
        else:
            ns.raw_file = None
    else:
        ns.raw_file = None
    if "plain" in (ns.style or []):
        ns.output = "raw"
    # Explicit username without a key gets the password prompt in interactive
    # mode; non-interactive calls can still rely on agent/key authentication.
    if ns.interactive and ns.password is None and ns.username and not ns.key and ns.transport == "ssh":
        ns.password = True
    return ns


def settings_from_namespace(ns, base=None, command=False, call_home=False):
    if base is None:
        return ConnectionSettings(
            transport=ns.transport,
            host=ns.host,
            port=int(ns.port),
            username=ns.username,
            password=ns.password,
            key=ns.key,
            known_hosts=ns.known_hosts,
            hostkey_verify=ns.hostkey_verify,
            ssh_config=ns.ssh_config,
            allow_agent=ns.allow_agent,
            look_for_keys=ns.look_for_keys,
            bind=ns.bind,
            keepalive=ns.keepalive,
            timeout=ns.timeout,
            rpc_timeout=ns.reply_timeout,
            cert=ns.cert,
            trusted_ca=ns.trusted_ca,
            crl=ns.crl,
            tls_version=ns.tls_version,
            verify_hostname=not getattr(ns, "no_hostname_verify", False),
            tls_server_name=ns.tls_server_name,
            netconf_version=ns.netconf_version,
            huge_tree=ns.huge_tree,
            call_home=call_home,
            listen_host=ns.listen_host,
            listen_port=int(ns.listen_port),
            raw_file=ns.raw_file,
        )

    def pick(name, current):
        value = getattr(ns, "cmd_" + name, None)
        return current if value is None else value

    selected_transport = pick("transport", base.transport)
    if getattr(ns, "cmd_ssh", False):
        selected_transport = "ssh"
    if getattr(ns, "cmd_tls", False):
        selected_transport = "tls"
    if getattr(ns, "cmd_tcp", False):
        selected_transport = "tcp"
    cmd_port = getattr(ns, "cmd_port", None)
    if cmd_port is None and selected_transport != base.transport:
        cmd_port = {"tls": 6513, "tcp": 2023}.get(selected_transport, 830)
    if cmd_port is None:
        cmd_port = base.port
    host = pick("host", base.host)
    username = pick("username", base.username)
    password = pick("password", base.password)
    key = pick("key", base.key)
    if getattr(ns, "cmd_password", None) is None and username and not key and selected_transport == "ssh":
        password = True
    command_version = pick("netconf_version", base.netconf_version)
    no_hostname_verify = pick("no_hostname_verify", not base.verify_hostname)
    listen_host = pick("host", base.listen_host) if call_home else base.listen_host
    if call_home and getattr(ns, "cmd_host", None) is None:
        listen_host = getattr(ns, "listen_host", None) or base.listen_host
    listen_port = int(cmd_port if call_home and cmd_port is not None else base.listen_port)
    return base.copy(
        transport=selected_transport,
        host=host,
        port=int(cmd_port),
        username=username,
        password=password,
        key=key,
        known_hosts=pick("known_hosts", base.known_hosts),
        hostkey_verify=pick("hostkey_verify", base.hostkey_verify),
        ssh_config=pick("ssh_config", base.ssh_config),
        allow_agent=pick("allow_agent", base.allow_agent),
        look_for_keys=pick("look_for_keys", base.look_for_keys),
        bind=pick("bind", base.bind),
        keepalive=pick("keepalive", base.keepalive),
        timeout=pick("timeout", base.timeout),
        rpc_timeout=pick("rpc_timeout", base.rpc_timeout),
        cert=pick("cert", base.cert),
        trusted_ca=pick("trusted_ca", base.trusted_ca),
        crl=pick("crl", base.crl),
        tls_version=pick("tls_version", base.tls_version),
        tls_server_name=pick("tls_server_name", base.tls_server_name),
        verify_hostname=not no_hostname_verify,
        netconf_version=command_version,
        huge_tree=pick("huge_tree", base.huge_tree),
        call_home=call_home,
        listen_host=listen_host,
        listen_port=listen_port,
    )


def _prompt_if_needed(settings):
    if settings.password is True:
        settings.password = getpass.getpass("Password: ")
    return settings


def _context_command_methods(ctx):
    def connect_from_command(ns):
        settings = _prompt_if_needed(settings_from_namespace(ns, ctx.settings, command=True))
        ctx.connect(settings)

    def listen_from_command(ns):
        settings = _prompt_if_needed(settings_from_namespace(ns, ctx.settings, command=True, call_home=True))
        if settings.transport not in {"ssh", "tls"}:
            raise ValueError("Call Home transport must be ssh or tls")
        ctx.listen(
            settings,
            on_waiting=lambda address: print("Waiting for %s Call Home on %s ..." % (
                settings.transport.upper(), address
            )),
            on_accepted=lambda address: print("Connection received from %s" % address),
        )

    ctx.connect_from_command = connect_from_command
    ctx.listen_from_command = listen_from_command


def _print_result(result, output_mode="pretty"):
    if result is None:
        return
    if isinstance(result, operations.TextResult):
        sys.stdout.write(result.text)
        if not result.text.endswith("\n"):
            sys.stdout.write("\n")
        return
    if isinstance(result, (bytes, bytearray)):
        sys.stdout.write(result.decode("utf-8", errors="replace"))
        return
    if hasattr(result, "tag"):
        rendered = etree.tostring(
            result,
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=output_mode != "raw",
        )
        sys.stdout.write(rendered.decode("utf-8"))
        return
    sys.stdout.write(str(result) + "\n")


def _invoke_operation(ctx, operation, ns, args, output_mode):
    try:
        ns.discovered_namespaces = ctx.namespace_registry.mapping()
        result = operation.invoke(ctx, ns, *args)
        _print_result(result, output_mode)
        return 0
    except RPCError as exc:
        _print_result(exc.xml, output_mode)
        return -1
    except KeyboardInterrupt:
        print("^C", file=sys.stderr)
        return 130
    except Exception as exc:
        report_exception(exc, ns.debug)
        return -1


def interactive_data():
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.key_binding import KeyBindings
        from .config import config_dir
        from .history import SecureFileHistory

        history = config_dir() / "history"
        history.parent.mkdir(parents=True, exist_ok=True)
        key_bindings = KeyBindings()

        @key_bindings.add("up")
        def history_previous(event):
            event.current_buffer.history_backward()

        @key_bindings.add("down")
        def history_next(event):
            event.current_buffer.history_forward()

        prompt_session = PromptSession(
            history=SecureFileHistory(str(history)),
            completer=WordCompleter(sorted(operations.OPERATION_OPTS), sentence=True),
            enable_history_search=True,
            key_bindings=key_bindings,
        )
        while True:
            try:
                yield prompt_session.prompt("netconf> ")
            except KeyboardInterrupt:
                print("^C", file=sys.stderr)
            except EOFError:
                return
    except ImportError:
        readline_module = completions.readline
        if readline_module is not None:
            readline_module.parse_and_bind("tab: complete")
            readline_module.set_completer_delims(" ")
            readline_module.set_completer(completions.NCCompleter(
                operations.OPERATION_OPTS, command_option_args
            ))
        while True:
            try:
                yield input("netconf> ")
            except KeyboardInterrupt:
                print("^C", file=sys.stderr)
            except EOFError:
                return


def interactive_operations(exprparser, ctx, ns):
    data = interactive_data() if sys.stdin.isatty() else sys.stdin
    operation = ExpressionOperation(exprparser)
    for command in data:
        if ctx.exit_requested:
            return
        yield operation, [command]


def _prepare_context(ns):
    trace = TraceSink(enabled=ns.trace, filename=ns.trace_file)
    settings = _prompt_if_needed(settings_from_namespace(ns))
    context = ConsoleContext(settings, trace)
    context.output_mode = ns.output
    _context_command_methods(context)
    return context


def connect_and_process(ns):
    context = _prepare_context(ns)
    try:
        if ns.call_home:
            context.listen(
                on_waiting=lambda address: print("Waiting for %s Call Home on %s ..." % (
                    ns.transport.upper(), address
                )),
                on_accepted=lambda address: print("Connection received from %s" % address),
            )
        elif ns.interactive or ns.filename or ns.operations:
            # A plain -i keeps the historical localhost auto-connect behavior,
            # but a failed default attempt leaves a usable disconnected prompt
            # so the operator can issue `connect ...` interactively.
            try:
                context.connect()
            except Exception as exc:
                if not ns.interactive:
                    raise
                report_exception(exc, ns.debug)
                context.manager = None

        if ns.filename:
            operation_iter = operations.FilenameOperations(ns.filename).operations()
        elif ns.interactive:
            parser = expression_parser(SafeParser, custom_help=True)
            parser.set_defaults(debug=ns.debug)
            operation_iter = interactive_operations(parser, context, ns)
        else:
            operation_iter = ns.operations

        exit_code = 0
        for operation, args in operation_iter:
            code = _invoke_operation(context, operation, ns, args, context.output_mode)
            if code != 0 and not ns.interactive:
                exit_code = code
                break
            if context.exit_requested:
                break
            # `--watch` is intentionally a global convenience for a single
            # subscribe operation; interactive users use `watch` explicitly.
            if getattr(ns, "watch", False) and operation.name in {"subscribe", "create_subscription"}:
                context.watch_notifications()
        return exit_code
    except (CallHomeCancelled, KeyboardInterrupt):
        print("Call Home listener cancelled", file=sys.stderr)
        return 130
    finally:
        context.close()


def main(argv=None):
    parser = argparser()
    try:
        ns = parser.parse_args(sys.argv[1:] if argv is None else argv)
        ns = resolve_namespace(ns)
    except (ValueError, ParserException) as exc:
        parser.error(str(exc))
        return 2
    if ns.operations == [] and ns.filename is None:
        ns.interactive = True
    if ns.interactive and ns.operations or ns.interactive and ns.filename is not None:
        parser.error("--interactive cannot be combined with top-level commands or a legacy filename")
    if ns.verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        logging.getLogger("ncclient").setLevel(logging.WARNING)
    if ns.dry:
        operations.run_rpc_dry()
    try:
        return connect_and_process(ns)
    except KeyboardInterrupt:
        print("^C", file=sys.stderr)
        return 130
    except Exception as exc:
        report_exception(exc, ns.debug)
        return -1


if __name__ == "__main__":
    sys.exit(main())
