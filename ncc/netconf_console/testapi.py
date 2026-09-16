"""Stable, machine-readable transport/handshake API for test automation.

The API reports observations, not conformance verdicts.  In particular, an
expected TLS rejection is a successful *test observation* for a negative
test, but it is still represented here as a rejected connection.  The test
plan remains responsible for deciding whether that observation is expected.
"""

from __future__ import annotations

import errno
import json
import os
import socket
import ssl
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import paramiko
from ncclient.transport.errors import AuthenticationError as NcAuthenticationError
from ncclient.transport.errors import TLSError

from .gnutls import GnuTLSError, backend_capabilities
from .session import CallHomeCancelled, ConnectionSettings, open_call_home, open_direct
from .trace import redact_secrets


API_VERSION = 1

# gnutls_certificate_status_t bits used for precise local-verification causes.
GNUTLS_CERT_SIGNER_NOT_FOUND = 1 << 6
GNUTLS_CERT_NOT_ACTIVATED = 1 << 9
GNUTLS_CERT_EXPIRED = 1 << 10
GNUTLS_CERT_UNEXPECTED_OWNER = 1 << 14
GNUTLS_CERT_MISMATCH = 1 << 17


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe_message(value: Any, limit: int = 1200) -> str:
    message = redact_secrets(str(value)).replace("\x00", "")
    return message if len(message) <= limit else message[: limit - 3] + "..."


def exception_chain(exc: BaseException) -> list[dict[str, Any]]:
    """Return every explicit/root cause without leaking credentials."""

    chain = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        item: dict[str, Any] = {
            "type": type(current).__name__,
            "module": type(current).__module__,
            "message": _safe_message(current),
        }
        for name in ("errno", "verify_code", "verify_message", "code", "operation",
                     "gnutls_message", "verify_status", "alert"):
            value = getattr(current, name, None)
            if value not in (None, "", 0):
                item[name] = _safe_message(value) if isinstance(value, str) else value
        chain.append(item)
        current = current.__cause__ or current.__context__
    return chain


def format_exception_chain(exc: BaseException) -> str:
    """Compact human diagnostic that includes the hidden TLS root cause."""

    return " <- ".join(
        f"{item['type']}: {item['message']}" for item in exception_chain(exc)
    )


def classify_exception(exc: BaseException, failed_phase: str | None) -> dict[str, Any]:
    """Classify the observed layer and direction using concrete exception data."""

    causes: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        causes.append(current)
        current = current.__cause__ or current.__context__

    for item in causes:
        if isinstance(item, GnuTLSError):
            status = int(item.verify_status)
            if status & (GNUTLS_CERT_UNEXPECTED_OWNER | GNUTLS_CERT_MISMATCH):
                return _classification("TLS_SERVER_IDENTITY_REJECTED", "local", "tls", item)
            if status & (GNUTLS_CERT_SIGNER_NOT_FOUND | GNUTLS_CERT_NOT_ACTIVATED | GNUTLS_CERT_EXPIRED):
                return _classification("TLS_SERVER_CERTIFICATE_REJECTED", "local", "tls", item)
            alert = (item.alert or "").lower().replace("_", " ").replace("-", " ")
            if "certificate expired" in alert or "certificate is expired" in alert:
                return _classification("TLS_CLIENT_CERTIFICATE_EXPIRED_REJECTED", "peer", "tls", item)
            if any(value in alert for value in (
                "unknown ca", "ca is unknown", "bad certificate", "certificate unknown",
            )):
                return _classification("TLS_CLIENT_CERTIFICATE_UNTRUSTED_REJECTED", "peer", "tls", item)
            if "certificate verification" in item.gnutls_message.lower():
                return _classification("TLS_SERVER_CERTIFICATE_REJECTED", "local", "tls", item)
            # A peer that closes the TLS connection while the handshake or
            # NETCONF hello is still in progress is a remote observation, not
            # a failure of this client.  GnuTLS exposes this as a
            # ``close_notify`` alert (and often GNUTLS_E_PREMATURE_TERMINATION)
            # instead of a certificate-specific alert.  Preserve that cause
            # so a positive three-observation roll-up can report
            # PARTIAL_PASS rather than hiding it as a local CLIENT_ERROR.
            if (alert in {"close notify", "close-notify"}
                    or "non-properly terminated" in item.gnutls_message.lower()
                    or "premature termination" in item.gnutls_message.lower()):
                return _classification("TLS_PEER_ALERT", "peer", "tls", item)
            if "alert" in item.gnutls_message.lower():
                return _classification("TLS_PEER_ALERT", "peer", "tls", item)

    for item in causes:
        if isinstance(item, ssl.SSLCertVerificationError):
            message = str(item).lower()
            if "hostname mismatch" in message or "ip address mismatch" in message:
                return _classification("TLS_SERVER_IDENTITY_REJECTED", "local", "tls", item)
            return _classification("TLS_SERVER_CERTIFICATE_REJECTED", "local", "tls", item)

    for item in causes:
        if isinstance(item, ssl.SSLError):
            message = str(item).lower().replace("_", " ")
            if "alert" in message and "certificate expired" in message:
                return _classification("TLS_CLIENT_CERTIFICATE_EXPIRED_REJECTED", "peer", "tls", item)
            if "alert" in message and any(value in message for value in (
                "unknown ca", "bad certificate", "certificate unknown",
            )):
                return _classification("TLS_CLIENT_CERTIFICATE_UNTRUSTED_REJECTED", "peer", "tls", item)
            if "alert" in message:
                return _classification("TLS_PEER_ALERT", "peer", "tls", item)
            return _classification("TLS_HANDSHAKE_FAILED", "unknown", "tls", item)

    for item in causes:
        # The ncclient transport wrapper deliberately converts Paramiko's
        # authentication failure into its own AuthenticationError.  Keep both
        # forms as the same peer-authentication observation so callers do not
        # have to scrape the human exception text (or mistake it for a local
        # CLIENT_ERROR).
        if isinstance(item, (
            paramiko.AuthenticationException,
            paramiko.BadAuthenticationType,
            NcAuthenticationError,
        )):
            return _classification("SSH_AUTHENTICATION_REJECTED", "peer", "ssh", item)

    for item in causes:
        if isinstance(item, (socket.timeout, TimeoutError)):
            layer = "tcp" if failed_phase in {None, "listen", "tcp"} else failed_phase
            return _classification("TIMEOUT", "local", layer, item)
        if isinstance(item, ConnectionRefusedError) or getattr(item, "errno", None) == errno.ECONNREFUSED:
            return _classification("TCP_CONNECTION_REFUSED", "peer", "tcp", item)
        if isinstance(item, ConnectionResetError) or getattr(item, "errno", None) == errno.ECONNRESET:
            return _classification("PEER_RESET", "peer", failed_phase or "transport", item)

    if any(isinstance(item, TLSError) for item in causes):
        return _classification("TLS_OR_NETCONF_FAILED", "unknown", failed_phase or "tls", exc)
    if isinstance(exc, CallHomeCancelled):
        return _classification("CANCELLED", "local", "listen", exc)
    return _classification("CLIENT_ERROR", "local", failed_phase or "client", exc)


def _classification(code: str, origin: str, layer: str, exc: BaseException) -> dict[str, Any]:
    return {
        "code": code,
        "origin": origin,
        "layer": layer,
        "summary": _safe_message(exc),
    }


class EventRecorder:
    def __init__(self, filename: str | os.PathLike[str] | None):
        self.path = Path(filename).expanduser().resolve() if filename else None
        self.started = time.monotonic()
        self.sequence = 0
        self.events: list[dict[str, Any]] = []
        self.current_phase: str | None = None
        self.stream = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = self.path.open("w", encoding="utf-8", newline="\n")
            os.chmod(self.path, 0o600)

    def emit(self, phase: str, state: str, detail: str = "") -> None:
        self.sequence += 1
        event = {
            "api_version": API_VERSION,
            "sequence": self.sequence,
            "timestamp": _utc_now(),
            "elapsed_seconds": round(time.monotonic() - self.started, 6),
            "phase": str(phase),
            "state": str(state),
            "detail": _safe_message(detail),
        }
        if state == "start":
            self.current_phase = str(phase)
        elif state == "done" and self.current_phase == phase:
            self.current_phase = None
        self.events.append(event)
        if self.stream:
            self.stream.write(json.dumps(event, sort_keys=True) + "\n")
            self.stream.flush()

    def close(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None


def _tls_details(manager_obj: Any) -> dict[str, Any] | None:
    session = getattr(manager_obj, "_session", None)
    sock = getattr(session, "_socket", None)
    if sock is None:
        return None
    backend = getattr(sock, "tls_backend", "openssl")
    if backend == "gnutls":
        return {
            "backend": "gnutls",
            "backend_version": getattr(sock, "tls_backend_version", None),
            "protocol": getattr(sock, "tls_protocol", None),
            "cipher": (getattr(sock, "cipher", (None,))[0]),
            "rfc8071_peer_allowed_to_send": bool(getattr(sock, "heartbeat_advertised", False)),
        }
    cipher = None
    try:
        value = sock.cipher()
        cipher = value[0] if value else None
    except (AttributeError, OSError):
        pass
    return {
        "backend": "openssl",
        "backend_version": ssl.OPENSSL_VERSION,
        "protocol": sock.version() if hasattr(sock, "version") else None,
        "cipher": cipher,
        "rfc8071_peer_allowed_to_send": False,
    }


def _metadata_dict(metadata: Any) -> dict[str, Any]:
    value = asdict(metadata)
    connected = value.get("connected_since")
    if isinstance(connected, datetime):
        value["connected_since"] = connected.isoformat(timespec="milliseconds")
    return value


def _write_result(filename: str | os.PathLike[str] | None, result: dict[str, Any]) -> None:
    if not filename:
        return
    path = Path(filename).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-%s" % os.getpid())
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def run_probe(
    settings: ConnectionSettings,
    *,
    result_file: str | os.PathLike[str] | None = None,
    events_file: str | os.PathLike[str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Observe one Direct or Call Home handshake and return JSON-safe data."""

    started_wall = _utc_now()
    started = time.monotonic()
    events = EventRecorder(events_file)
    manager_obj = None
    metadata = None
    cleanup: dict[str, Any] = {"status": "NOT_NEEDED", "error": None}
    result: dict[str, Any]
    try:
        events.emit("client", "start", "open NETCONF transport")
        if settings.call_home:
            manager_obj, metadata = open_call_home(settings, phase=events.emit)
        else:
            manager_obj, metadata = open_direct(settings, phase=events.emit)
        events.emit("client", "done", "NETCONF hello received")
        capabilities = list(manager_obj.server_capabilities or [])
        result = {
            "api_version": API_VERSION,
            "success": True,
            "observation": "SESSION_ESTABLISHED",
            "classification": {
                "code": "SESSION_ESTABLISHED",
                "origin": "none",
                "layer": "netconf",
                "summary": "NETCONF hello and session established",
            },
            "failed_phase": None,
            "exception_chain": [],
            "session": {
                "id": str(manager_obj.session_id) if manager_obj.session_id is not None else None,
                "capabilities": capabilities,
                "capability_count": len(capabilities),
                "metadata": _metadata_dict(metadata),
                "tls": _tls_details(manager_obj) if settings.transport == "tls" else None,
            },
        }
        exit_code = 0
    except BaseException as exc:
        failed_phase = events.current_phase
        classification = classify_exception(exc, failed_phase)
        events.emit(failed_phase or "client", "error", classification["summary"])
        result = {
            "api_version": API_VERSION,
            "success": False,
            "observation": classification["code"],
            "classification": classification,
            "failed_phase": failed_phase,
            "exception_chain": exception_chain(exc),
            "session": None,
        }
        if classification["code"] in {
            "SSH_AUTHENTICATION_REJECTED",
            "TLS_CLIENT_CERTIFICATE_UNTRUSTED_REJECTED",
            "TLS_CLIENT_CERTIFICATE_EXPIRED_REJECTED",
            "TLS_SERVER_CERTIFICATE_REJECTED",
            "TLS_SERVER_IDENTITY_REJECTED",
            "TLS_PEER_ALERT",
        }:
            exit_code = 10
        elif classification["code"] in {"TIMEOUT", "TCP_CONNECTION_REFUSED", "PEER_RESET"}:
            exit_code = 11
        else:
            exit_code = 12
    finally:
        if manager_obj is not None:
            try:
                manager_obj.close_session()
                cleanup["status"] = "PASS"
            except BaseException as exc:
                cleanup["status"] = "FAIL"
                cleanup["error"] = {
                    "classification": classify_exception(exc, "cleanup"),
                    "exception_chain": exception_chain(exc),
                }
                try:
                    manager_obj._session.close()
                except BaseException:
                    pass

        result.update({
            "mode": "call-home" if settings.call_home else "direct",
            "transport": settings.transport,
            "started_at": started_wall,
            "finished_at": _utc_now(),
            "duration_seconds": round(time.monotonic() - started, 6),
            "cleanup": cleanup,
            "events_file": str(events.path) if events.path else None,
            "backend_capabilities": {
                "gnutls": backend_capabilities() if settings.transport == "tls" else None,
            },
        })
        events.close()
        _write_result(result_file, result)
    return exit_code, result
