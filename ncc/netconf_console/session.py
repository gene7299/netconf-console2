"""Application-owned connection and Call Home adapters.

The NETCONF protocol engine remains ncclient.  This module supplies the
application concerns ncclient intentionally does not expose as a CLI: profile
settings, metadata, redacted tracing, a Windows-friendly listener, and TLS
Call Home from an accepted socket.
"""

from __future__ import annotations

import logging
import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ncclient import manager, transport
from ncclient.transport.errors import TLSError

from .namespaces import NamespaceRegistry, yang_library_filter
from .trace import TraceSink


LOGGER = logging.getLogger(__name__)


@dataclass
class ConnectionSettings:
    """Resolved connection settings shared by direct and Call Home flows."""

    transport: str = "ssh"
    host: str = "127.0.0.1"
    port: int = 830
    username: str | None = None
    password: str | None | bool = None
    key: str | None = None
    known_hosts: str | None = None
    hostkey_verify: bool = False
    ssh_config: str | bool | None = None
    allow_agent: bool = True
    look_for_keys: bool = True
    bind: str | None = None
    keepalive: int | None = None
    timeout: float = 30.0
    rpc_timeout: float = 30.0
    cert: str | None = None
    trusted_ca: str | None = None
    crl: str | None = None
    tls_version: str | None = None
    verify_hostname: bool = True
    tls_server_name: str | None = None
    netconf_version: str | None = None
    huge_tree: bool = False
    call_home: bool = False
    listen_host: str = "0.0.0.0"
    listen_port: int = 4334
    raw_file: str | None = None
    # legacy preserves CLI behaviour; GUI explicitly opts into separated secrets.
    ssh_auth: str = "legacy"
    key_passphrase: str | None = None

    def copy(self, **changes: Any) -> "ConnectionSettings":
        return replace(self, **changes)


@dataclass
class SessionMetadata:
    mode: str
    transport: str
    remote_host: str | None
    remote_port: int | None
    local_address: str | None
    peer_address: str | None
    listen_address: str | None
    username: str | None
    connected_since: datetime


class NotConnectedError(RuntimeError):
    """Raised when an operation requiring a NETCONF session is requested."""


class CallHomeCancelled(RuntimeError):
    """Raised when a Call Home listener is cancelled before accepting a peer."""


class TracedSSHSession(transport.SSHSession):
    """ncclient SSH session with application-level trace hooks."""

    def __init__(self, device_handler: Any, trace: TraceSink | None = None, raw_file: str | None = None):
        self._console_trace = trace
        self._console_raw_file = raw_file
        self._console_raw_stream = None
        super().__init__(device_handler)

    def send(self, message: str) -> None:
        if self._console_trace:
            self._console_trace.send(message)
        super().send(message)

    def _auth(self, username, password, key_filenames, allow_agent, look_for_keys):
        mode = getattr(self, "_console_auth_mode", "legacy")
        if mode == "legacy":
            return super()._auth(username, password, key_filenames, allow_agent, look_for_keys)
        from .sshauth import authenticate
        return authenticate(self._transport, username, password, key_filenames,
                            allow_agent, look_for_keys, mode,
                            getattr(self, "_console_key_passphrase", None))

    def _dispatch_message(self, raw: str | bytes) -> Any:
        if self._console_trace:
            self._console_trace.receive(raw)
        if self._console_raw_file:
            if self._console_raw_stream is None:
                self._console_raw_stream = Path(self._console_raw_file).expanduser().open(
                    "a", encoding="utf-8", newline=""
                )
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            self._console_raw_stream.write(text)
            self._console_raw_stream.flush()
        return super()._dispatch_message(raw)

    def close(self) -> None:
        try:
            super().close()
        finally:
            if self._console_raw_stream is not None:
                self._console_raw_stream.close()
                self._console_raw_stream = None


def _tls_version(value: str | None) -> ssl.TLSVersion | None:
    if value is None or str(value).lower() in {"", "auto", "default"}:
        return None
    normalized = str(value).lower().replace("tls", "").replace("v", "").replace("_", ".")
    versions = {
        "1.2": ssl.TLSVersion.TLSv1_2,
        "1.3": ssl.TLSVersion.TLSv1_3,
    }
    try:
        return versions[normalized]
    except KeyError as exc:
        raise ValueError("TLS version must be auto, 1.2, or 1.3") from exc


def build_tls_context(settings: ConnectionSettings) -> ssl.SSLContext:
    """Build a verified client context for direct or Call Home TLS."""

    if not settings.cert:
        raise TLSError("Missing TLS client certificate; use --cert")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # Hostname verification is separate from certificate-chain verification.
    # --no-hostname-verify is useful for Call Home peers identified by a
    # certificate rather than DNS, but it must not silently disable trust
    # validation.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    if settings.trusted_ca:
        ca_path = Path(settings.trusted_ca).expanduser()
        if ca_path.is_dir():
            context.load_verify_locations(capath=str(ca_path))
        else:
            context.load_verify_locations(cafile=str(ca_path))
    else:
        context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    context.check_hostname = settings.verify_hostname

    version = _tls_version(settings.tls_version)
    if version is not None:
        context.minimum_version = version
        context.maximum_version = version

    try:
        context.load_cert_chain(certfile=str(Path(settings.cert).expanduser()), keyfile=(
            str(Path(settings.key).expanduser()) if settings.key else None
        ))
    except (OSError, ssl.SSLError) as exc:
        raise TLSError("Unable to load TLS client certificate/private key") from exc

    if settings.crl:
        crl_path = Path(settings.crl).expanduser()
        if crl_path.is_dir():
            context.load_verify_locations(capath=str(crl_path))
        else:
            context.load_verify_locations(cafile=str(crl_path))
        crl_flag = getattr(ssl, "VERIFY_CRL_CHECK_LEAF", 0)
        if crl_flag:
            context.verify_flags |= crl_flag
        else:
            LOGGER.warning("This Python/OpenSSL build cannot enable CRL checking")

    return context


class TracedTLSSession(transport.TLSSession):
    """ncclient TLS session with direct and accepted-socket entry points."""

    def __init__(self, device_handler: Any, trace: TraceSink | None = None, raw_file: str | None = None):
        self._console_trace = trace
        self._console_raw_file = raw_file
        self._console_raw_stream = None
        super().__init__(device_handler)

    def send(self, message: str) -> None:
        if self._console_trace:
            self._console_trace.send(message)
        super().send(message)

    def _dispatch_message(self, raw: str | bytes) -> Any:
        if self._console_trace:
            self._console_trace.receive(raw)
        if self._console_raw_file:
            if self._console_raw_stream is None:
                self._console_raw_stream = Path(self._console_raw_file).expanduser().open(
                    "a", encoding="utf-8", newline=""
                )
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            self._console_raw_stream.write(text)
            self._console_raw_stream.flush()
        return super()._dispatch_message(raw)

    def _connect_socket(
        self,
        sock: socket.socket,
        settings: ConnectionSettings,
        host: str | None,
    ) -> None:
        context = build_tls_context(settings)
        server_name = settings.tls_server_name or host
        wrapped = None
        try:
            wrapped = context.wrap_socket(
                sock,
                server_hostname=server_name if server_name else None,
                do_handshake_on_connect=False,
            )
            wrapped.settimeout(settings.timeout if settings.timeout else None)
            wrapped.do_handshake()
            self._host = host
            self._socket = wrapped
            self._connected = True
            self._closing.clear()
            self._post_connect(timeout=settings.timeout)
        except Exception as exc:
            self._connected = False
            self._socket = None
            self._closing.set()
            if wrapped is not None:
                try:
                    wrapped.close()
                except OSError:
                    pass
            else:
                try:
                    sock.close()
                except OSError:
                    pass
            if isinstance(exc, TLSError):
                raise
            raise TLSError("TLS handshake or NETCONF hello failed") from exc

    def connect(self, settings: ConnectionSettings) -> None:
        if not settings.host:
            raise TLSError("Missing host")
        try:
            sock = socket.create_connection((settings.host, settings.port), timeout=settings.timeout)
        except OSError as exc:
            raise TLSError("Could not connect to %s:%s" % (settings.host, settings.port)) from exc
        self._connect_socket(sock, settings, settings.host)

    def connect_accepted(
        self,
        sock: socket.socket,
        settings: ConnectionSettings,
        peer_host: str | None,
    ) -> None:
        """Start TLS as the client over a TCP socket accepted for Call Home."""

        self._connect_socket(sock, settings, peer_host)

    def close(self) -> None:
        self._closing.set()
        sock = getattr(self, "_socket", None)
        self._connected = False
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
            self._socket = None
        if self._console_raw_stream is not None:
            self._console_raw_stream.close()
            self._console_raw_stream = None


class CallHomeListener:
    """Small, reusable IPv4/IPv6 listener with timeout and cancellation."""

    def __init__(self, host: str, port: int, timeout: float | None = None):
        self.host = host
        self.port = port
        self.timeout = float(timeout) if timeout not in (None, 0, 0.0) else None
        self.socket: socket.socket | None = None
        self._deadline: float | None = None

    def __enter__(self) -> "CallHomeListener":
        infos = socket.getaddrinfo(
            self.host,
            self.port,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            socket.AI_PASSIVE,
        )
        last_error: OSError | None = None
        for family, socktype, proto, _canonname, address in infos:
            candidate = socket.socket(family, socktype, proto)
            try:
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if family == socket.AF_INET6:
                    # Keep an explicit IPv6 listener predictable across Windows
                    # and Linux; callers can bind both families separately if
                    # they need dual-stack behavior.
                    try:
                        candidate.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    except OSError:
                        pass
                candidate.bind(address)
                candidate.listen(1)
                candidate.settimeout(0.5)
                self.socket = candidate
                self._deadline = time.monotonic() + self.timeout if self.timeout else None
                return self
            except OSError as exc:
                last_error = exc
                candidate.close()
        raise OSError("Could not bind Call Home listener %s:%s" % (self.host, self.port)) from last_error

    @property
    def listen_address(self) -> str | None:
        if self.socket is None:
            return None
        address = self.socket.getsockname()
        if isinstance(address, tuple):
            return "%s:%s" % (address[0], address[1])
        return str(address)

    def accept(self, cancel_event: threading.Event | None = None) -> tuple[socket.socket, Any]:
        if self.socket is None:
            raise RuntimeError("Call Home listener is not open")
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise CallHomeCancelled("Call Home listener cancelled")
            if self._deadline is not None and time.monotonic() >= self._deadline:
                raise TimeoutError("Call Home listener timed out")
            try:
                return self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                if cancel_event is not None and cancel_event.is_set():
                    raise CallHomeCancelled("Call Home listener cancelled")
                raise

    def close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()


def _handler_for(settings: ConnectionSettings) -> Any:
    from .operations import ConsoleDeviceHandler

    handler = ConsoleDeviceHandler()
    if settings.netconf_version == "1.0":
        handler._BASE_CAPABILITIES = [
            capability for capability in handler._BASE_CAPABILITIES
            if capability != "urn:ietf:params:netconf:base:1.1"
        ]
    elif settings.netconf_version == "1.1":
        handler._BASE_CAPABILITIES = [
            capability for capability in handler._BASE_CAPABILITIES
            if capability != "urn:ietf:params:netconf:base:1.0"
        ]
    return handler


def _manager_for_session(session: Any, handler: Any, settings: ConnectionSettings) -> Any:
    from ncclient.manager import Manager

    client = Manager(session, handler, timeout=settings.rpc_timeout)
    client.huge_tree = settings.huge_tree
    return client


def _prepare_known_hosts(session: Any, settings: ConnectionSettings) -> None:
    session._console_auth_mode = settings.ssh_auth
    session._console_key_passphrase = settings.key_passphrase
    if settings.known_hosts:
        session.load_known_hosts(str(Path(settings.known_hosts).expanduser()))


def open_direct(
    settings: ConnectionSettings,
    trace: TraceSink | None = None,
) -> tuple[Any, SessionMetadata]:
    """Open a direct SSH or TLS manager and return its metadata."""

    handler = _handler_for(settings)
    raw_file = settings.raw_file
    session: Any | None = None
    try:
        if settings.transport == "tls":
            session = TracedTLSSession(handler, trace, raw_file)
            session.connect(settings)
        elif settings.transport == "ssh":
            session = TracedSSHSession(handler, trace, raw_file)
            _prepare_known_hosts(session, settings)
            session.connect(
                host=settings.host,
                port=settings.port,
                timeout=settings.timeout,
                username=settings.username,
                password=settings.password,
                key_filename=settings.key,
                allow_agent=settings.allow_agent,
                look_for_keys=settings.look_for_keys,
                hostkey_verify=settings.hostkey_verify,
                ssh_config=settings.ssh_config,
                keepalive=settings.keepalive,
                bind_addr=settings.bind,
            )
        elif settings.transport == "tcp":
            # The historical TCP mode is non-standard and remains only for the
            # project's legacy ConfD regression suite.  It is kept behind an
            # explicit transport selection and is now safe to instantiate on
            # Windows as well.
            from .nctransport import TCPSession

            session = TCPSession(handler, raw_file)
            session.connect(
                host=settings.host,
                port=settings.port,
                username=settings.username,
                password=settings.password,
                timeout=settings.timeout,
            )
        else:
            raise ValueError("Unsupported transport %r" % settings.transport)
    except Exception:
        if session is not None:
            try:
                session.close()
            except Exception:
                LOGGER.debug("Ignoring transport cleanup failure", exc_info=True)
        raise

    local_address = _local_address(session)
    metadata = SessionMetadata(
        mode="Direct",
        transport=settings.transport.upper(),
        remote_host=settings.host,
        remote_port=settings.port,
        local_address=local_address,
        peer_address=None,
        listen_address=None,
        username=settings.username,
        connected_since=datetime.now(timezone.utc),
    )
    return _manager_for_session(session, handler, settings), metadata


def open_call_home(
    settings: ConnectionSettings,
    trace: TraceSink | None = None,
    cancel_event: threading.Event | None = None,
    on_waiting: Callable[[str], None] | None = None,
    on_accepted: Callable[[str], None] | None = None,
) -> tuple[Any, SessionMetadata]:
    """Listen once and establish an SSH or TLS Call Home NETCONF session."""

    port = settings.listen_port
    with CallHomeListener(settings.listen_host, port, settings.timeout) as listener:
        listen_address = listener.listen_address or "%s:%s" % (settings.listen_host, port)
        if on_waiting:
            on_waiting(listen_address)
        accepted, peer = listener.accept(cancel_event)
        peer_host = peer[0] if isinstance(peer, tuple) and peer else str(peer)
        peer_address = "%s:%s" % (peer[0], peer[1]) if isinstance(peer, tuple) else str(peer)
        peer_port = int(peer[1]) if isinstance(peer, tuple) and len(peer) > 1 else None
        if on_accepted:
            on_accepted(peer_address)
        accepted.settimeout(settings.timeout if settings.timeout else None)

    handler = _handler_for(settings)
    try:
        if settings.transport == "ssh":
            session = TracedSSHSession(handler, trace, settings.raw_file)
            _prepare_known_hosts(session, settings)
            # RFC 8071 reverses the TCP initiator, not the SSH/TLS role.  The
            # accepted socket therefore enters ncclient's normal SSH-client
            # path, including host-key verification and authentication.
            session.connect(
                host=peer_host,
                port=port,
                timeout=settings.timeout,
                username=settings.username,
                password=settings.password,
                key_filename=settings.key,
                allow_agent=settings.allow_agent,
                look_for_keys=settings.look_for_keys,
                hostkey_verify=settings.hostkey_verify,
                ssh_config=settings.ssh_config,
                keepalive=settings.keepalive,
                sock=accepted,
            )
        elif settings.transport == "tls":
            session = TracedTLSSession(handler, trace, settings.raw_file)
            # RFC 8071/RFC 7589 require the NETCONF client to remain the TLS
            # client.  This is the project-local accepted-socket extension;
            # ncclient.manager.call_home() currently routes accepted sockets to
            # connect_ssh instead.
            session.connect_accepted(accepted, settings, peer_host)
        else:
            accepted.close()
            raise ValueError("Call Home transport must be ssh or tls")
    except Exception:
        try:
            accepted.close()
        except OSError:
            pass
        raise

    local_address = _local_address(session)
    metadata = SessionMetadata(
        mode="Call Home",
        transport=settings.transport.upper(),
        remote_host=peer_host,
        remote_port=peer_port,
        local_address=local_address,
        peer_address=peer_address,
        listen_address=listen_address,
        username=settings.username,
        connected_since=datetime.now(timezone.utc),
    )
    return _manager_for_session(session, handler, settings), metadata


def _local_address(session: Any) -> str | None:
    candidates = []
    sock = getattr(session, "_socket", None)
    if sock is not None:
        candidates.append(sock)
    ssh_transport = getattr(session, "_transport", None)
    if ssh_transport is not None:
        candidates.append(getattr(ssh_transport, "sock", None))
        candidates.append(getattr(ssh_transport, "_socket", None))
    for candidate in candidates:
        try:
            address = candidate.getsockname()
        except (AttributeError, OSError):
            continue
        if isinstance(address, tuple):
            return "%s:%s" % (address[0], address[1])
        return str(address)
    return None


class ConsoleContext:
    """Mutable owner for one interactive or batch NETCONF session."""

    def __init__(self, settings: ConnectionSettings, trace: TraceSink | None = None):
        self.settings = settings
        self.trace = trace
        self.manager: Any | None = None
        self.metadata: SessionMetadata | None = None
        self.output_mode = "pretty"
        self.exit_requested = False
        self.namespace_registry = NamespaceRegistry()

    @property
    def connected(self) -> bool:
        return bool(self.manager is not None and self.manager.connected)

    def require_manager(self) -> Any:
        if self.manager is None or not self.manager.connected:
            raise NotConnectedError("Not connected to a NETCONF server")
        return self.manager

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.require_manager(), name)

    def connect(self, settings: ConnectionSettings | None = None) -> Any:
        if settings is not None:
            self.settings = settings
        self.disconnect()
        self.manager, self.metadata = open_direct(self.settings, self.trace)
        self.refresh_namespaces()
        return self.manager

    def listen(
        self,
        settings: ConnectionSettings | None = None,
        cancel_event: threading.Event | None = None,
        on_waiting: Callable[[str], None] | None = None,
        on_accepted: Callable[[str], None] | None = None,
    ) -> Any:
        if settings is not None:
            self.settings = settings
        self.disconnect()
        self.manager, self.metadata = open_call_home(
            self.settings, self.trace, cancel_event, on_waiting, on_accepted
        )
        self.refresh_namespaces()
        return self.manager

    def _namespace_server_key(self) -> str:
        metadata = self.metadata
        if metadata is None:
            return "%s://%s:%s" % (
                self.settings.transport, self.settings.host, self.settings.port
            )
        host = metadata.remote_host or self.settings.host
        if metadata.mode == "Call Home":
            return "%s+call-home://%s:%s" % (
                self.settings.transport, host, self.settings.listen_port
            )
        return "%s://%s:%s" % (
            self.settings.transport, host, metadata.remote_port or self.settings.port
        )

    def refresh_namespaces(self, force: bool = False) -> int:
        """Learn module namespaces from hello and the server YANG Library.

        Discovery is best effort: a server with no YANG Library support, or a
        broken implementation, must not make an otherwise valid NETCONF
        connection fail. Cached mappings remain available in that case.
        """

        manager_obj = self.require_manager()
        self.namespace_registry.select_server(self._namespace_server_key())
        capabilities = list(manager_obj.server_capabilities or [])
        self.namespace_registry.learn_capabilities(capabilities)
        if not self.namespace_registry.should_query_yang_library(capabilities, force):
            return 0

        old_timeout = getattr(manager_obj, "timeout", None)
        try:
            if old_timeout is not None:
                manager_obj.timeout = min(float(old_timeout), 5.0)
            reply = manager_obj.get(yang_library_filter())
            return self.namespace_registry.learn_yang_library(getattr(reply, "data", None))
        except Exception as exc:
            LOGGER.info("YANG Library namespace discovery unavailable: %s", exc)
            return 0
        finally:
            if old_timeout is not None:
                manager_obj.timeout = old_timeout

    def learn_yang_schema(self, text: str, module_hint: str | None = None) -> Any:
        """Learn and cache a module name/prefix after get-schema succeeds."""

        if self.namespace_registry.server_key is None:
            self.namespace_registry.select_server(self._namespace_server_key())
        return self.namespace_registry.learn_schema(text, module_hint)

    def disconnect(self) -> None:
        if self.manager is not None:
            try:
                self.manager.close_session()
            except Exception:
                try:
                    self.manager._session.close()
                except Exception:
                    pass
            self.manager = None

    def close(self) -> None:
        self.disconnect()
        if self.trace:
            self.trace.close()

    def status_lines(self) -> list[str]:
        if self.metadata is None or self.manager is None:
            return ["Connected         : No"]
        session = self.manager._session
        base = "1.1" if getattr(session, "_base", 1) == 2 else "1.0"
        caps = list(self.manager.server_capabilities or [])
        lines = [
            "Connected         : %s" % ("Yes" if self.connected else "No"),
            "Connection Mode   : %s" % self.metadata.mode,
            "Transport         : %s" % self.metadata.transport,
            "Remote Host       : %s" % (self.metadata.remote_host or "-"),
            "Remote Port       : %s" % (self.metadata.remote_port or "-"),
            "Local Address     : %s" % (self.metadata.local_address or "-"),
            "Session ID        : %s" % (self.manager.session_id or "-"),
            "Username          : %s" % (self.metadata.username or "-"),
            "NETCONF Version   : %s" % base,
            "Server Capabilities: %s" % len(caps),
            "Connected Since   : %s" % self.metadata.connected_since.astimezone().isoformat(timespec="seconds"),
        ]
        if self.metadata.mode == "Call Home":
            lines.extend([
                "Peer Address      : %s" % (self.metadata.peer_address or "-"),
                "Listen Address    : %s" % (self.metadata.listen_address or "-"),
            ])
        return lines

    def watch_notifications(
        self,
        emit: Callable[[str], None] = print,
        cancel_event: threading.Event | None = None,
    ) -> None:
        manager_obj = self.require_manager()
        while self.connected:
            if cancel_event is not None and cancel_event.is_set():
                return
            notification = manager_obj.take_notification(block=True, timeout=0.5)
            if notification is not None:
                emit(notification.notification_xml)
