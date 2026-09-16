"""Small, headless GnuTLS client adapter used by RFC 8071 Call Home.

Python's :mod:`ssl` API cannot enable the RFC 6520 Heartbeat extension.  A
NETCONF Call Home client, however, is required by RFC 8071 C4 to advertise
``peer_allowed_to_send``.  GnuTLS exposes that policy through a stable C API.

This module deliberately wraps only the socket operations ncclient needs.  It
does not implement NETCONF, certificate parsing, or TLS itself.  Certificate
path and reference-identifier validation remain library operations.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import ipaddress
import os
import select
import socket
import threading
import time
from pathlib import Path
from typing import Any


GNUTLS_CLIENT = 1 << 1
GNUTLS_NONBLOCK = 1 << 3
GNUTLS_CRD_CERTIFICATE = 1
GNUTLS_X509_FMT_PEM = 1
GNUTLS_NAME_DNS = 1
GNUTLS_SHUT_RDWR = 0
GNUTLS_HB_PEER_ALLOWED_TO_SEND = 1
GNUTLS_HB_LOCAL_ALLOWED_TO_SEND = 1 << 2

GNUTLS_E_AGAIN = -28
GNUTLS_E_INTERRUPTED = -52
GNUTLS_E_HEARTBEAT_PING_RECEIVED = -293


class GnuTLSUnavailable(RuntimeError):
    """The RFC 8071-capable TLS backend cannot be loaded."""


class GnuTLSError(OSError):
    """A structured GnuTLS failure that preserves the native error code."""

    def __init__(
        self,
        operation: str,
        code: int,
        message: str,
        *,
        verify_status: int = 0,
        alert: str | None = None,
    ):
        super().__init__(code, f"{operation}: {message}")
        self.operation = operation
        self.code = int(code)
        self.gnutls_message = message
        self.verify_status = int(verify_status)
        self.alert = alert


class _Library:
    _instance: "_Library | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "_Library":
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._load()
                cls._instance = instance
            return cls._instance

    @staticmethod
    def _candidate_names() -> list[str]:
        result: list[str] = []
        override = os.environ.get("NETCONF_CONSOLE2_GNUTLS_LIBRARY")
        if override:
            result.append(override)
        discovered = ctypes.util.find_library("gnutls")
        if discovered:
            result.append(discovered)
        result.extend([
            "libgnutls.so.30",
            "libgnutls.so",
            "libgnutls-30.dll",
            "libgnutls.dylib",
        ])
        return list(dict.fromkeys(result))

    def _load(self) -> None:
        failures = []
        for name in self._candidate_names():
            try:
                self.lib = ctypes.CDLL(name)
                self.library_name = name
                break
            except OSError as exc:
                failures.append(f"{name}: {exc}")
        else:
            raise GnuTLSUnavailable(
                "GnuTLS is required for RFC 8071 TLS Call Home heartbeat support; "
                + "; ".join(failures)
            )

        c_void_p = ctypes.c_void_p
        c_char_p = ctypes.c_char_p
        c_uint = ctypes.c_uint
        c_int = ctypes.c_int
        c_size_t = ctypes.c_size_t
        c_ssize_t = ctypes.c_ssize_t

        signatures: dict[str, tuple[list[Any], Any]] = {
            "gnutls_global_init": ([], c_int),
            "gnutls_check_version": ([c_char_p], c_char_p),
            "gnutls_init": ([ctypes.POINTER(c_void_p), c_uint], c_int),
            "gnutls_deinit": ([c_void_p], None),
            "gnutls_priority_set_direct": ([c_void_p, c_char_p, ctypes.POINTER(c_char_p)], c_int),
            "gnutls_certificate_allocate_credentials": ([ctypes.POINTER(c_void_p)], c_int),
            "gnutls_certificate_free_credentials": ([c_void_p], None),
            "gnutls_certificate_set_x509_trust_file": ([c_void_p, c_char_p, c_int], c_int),
            "gnutls_certificate_set_x509_trust_dir": ([c_void_p, c_char_p, c_int], c_int),
            "gnutls_certificate_set_x509_crl_file": ([c_void_p, c_char_p, c_int], c_int),
            "gnutls_certificate_set_x509_key_file": ([c_void_p, c_char_p, c_char_p, c_int], c_int),
            "gnutls_credentials_set": ([c_void_p, c_int, c_void_p], c_int),
            "gnutls_server_name_set": ([c_void_p, c_uint, c_void_p, c_size_t], c_int),
            "gnutls_session_set_verify_cert": ([c_void_p, c_char_p, c_uint], None),
            "gnutls_session_get_verify_cert_status": ([c_void_p], c_uint),
            "gnutls_heartbeat_enable": ([c_void_p, c_uint], None),
            "gnutls_heartbeat_allowed": ([c_void_p, c_uint], c_uint),
            "gnutls_heartbeat_pong": ([c_void_p, c_uint], c_int),
            "gnutls_transport_set_int2": ([c_void_p, c_int, c_int], None),
            "gnutls_handshake": ([c_void_p], c_int),
            "gnutls_record_get_direction": ([c_void_p], c_int),
            "gnutls_record_recv": ([c_void_p, c_void_p, c_size_t], c_ssize_t),
            "gnutls_record_send": ([c_void_p, c_void_p, c_size_t], c_ssize_t),
            "gnutls_bye": ([c_void_p, c_int], c_int),
            "gnutls_error_is_fatal": ([c_int], c_int),
            "gnutls_strerror": ([c_int], c_char_p),
            "gnutls_strerror_name": ([c_int], c_char_p),
            "gnutls_alert_get": ([c_void_p], c_int),
            "gnutls_alert_get_name": ([c_int], c_char_p),
            "gnutls_protocol_get_version": ([c_void_p], c_int),
            "gnutls_protocol_get_name": ([c_int], c_char_p),
            "gnutls_cipher_get": ([c_void_p], c_int),
            "gnutls_cipher_get_name": ([c_int], c_char_p),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(self.lib, name, None)
            if function is None:
                if name in {"gnutls_certificate_set_x509_trust_dir"}:
                    continue
                raise GnuTLSUnavailable(f"{self.library_name} lacks required symbol {name}")
            function.argtypes = argtypes
            function.restype = restype

        code = self.lib.gnutls_global_init()
        if code < 0:
            raise GnuTLSUnavailable(self.error_text(code))
        version = self.lib.gnutls_check_version(b"3.1.2")
        if not version:
            raise GnuTLSUnavailable("GnuTLS 3.1.2 or newer is required for RFC 6520 Heartbeat")
        self.version = version.decode("ascii", errors="replace")

    def error_text(self, code: int) -> str:
        value = self.lib.gnutls_strerror(int(code))
        return value.decode("utf-8", errors="replace") if value else f"GnuTLS error {code}"

    def error_name(self, code: int) -> str | None:
        value = self.lib.gnutls_strerror_name(int(code))
        return value.decode("ascii", errors="replace") if value else None


def backend_capabilities() -> dict[str, Any]:
    """Return machine-readable availability without opening a connection."""

    try:
        library = _Library()
    except GnuTLSUnavailable as exc:
        return {
            "available": False,
            "library": None,
            "version": None,
            "rfc8071_heartbeat": False,
            "error": str(exc),
        }
    return {
        "available": True,
        "library": library.library_name,
        "version": library.version,
        "rfc8071_heartbeat": True,
        "error": None,
    }


def _path_bytes(value: str) -> bytes:
    return os.fsencode(str(Path(value).expanduser().resolve()))


def _is_ip_address(value: str | None) -> bool:
    if not value:
        return False
    candidate = value.split("%", 1)[0]
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        return False


class GnuTLSSocket:
    """A selector-compatible TLS socket implementing RFC 8071 C4."""

    def __init__(
        self,
        raw_socket: socket.socket,
        *,
        cert: str,
        key: str | None,
        trusted_ca: str | None,
        crl: str | None,
        server_name: str | None,
        verify_hostname: bool,
        tls_version: str | None,
        timeout: float | None,
        advertise_peer_allowed_to_send: bool = True,
    ):
        self._library = _Library()
        self._lib = self._library.lib
        self._raw = raw_socket
        self._session = ctypes.c_void_p()
        self._credentials = ctypes.c_void_p()
        self._closed = False
        self._timeout = float(timeout) if timeout not in (None, 0, 0.0) else None
        self._heartbeat_advertised = False
        self._heartbeat_peer_may_send = bool(advertise_peer_allowed_to_send)

        try:
            self._check(
                "allocate certificate credentials",
                self._lib.gnutls_certificate_allocate_credentials(ctypes.byref(self._credentials)),
            )
            if trusted_ca:
                ca = Path(trusted_ca).expanduser().resolve()
                if ca.is_dir():
                    function = getattr(self._lib, "gnutls_certificate_set_x509_trust_dir", None)
                    if function is None:
                        raise GnuTLSUnavailable(
                            "This GnuTLS build cannot load a CA directory; provide a PEM bundle"
                        )
                    loaded = function(self._credentials, os.fsencode(ca), GNUTLS_X509_FMT_PEM)
                else:
                    loaded = self._lib.gnutls_certificate_set_x509_trust_file(
                        self._credentials, os.fsencode(ca), GNUTLS_X509_FMT_PEM
                    )
                if loaded < 0:
                    self._raise("load trusted CA", loaded)
                if loaded == 0:
                    raise GnuTLSError("load trusted CA", 0, "no CA certificates were loaded")
            else:
                raise GnuTLSError(
                    "load trusted CA", 0,
                    "RFC 8071 Call Home requires an explicit preconfigured issuer",
                )

            if crl:
                loaded = self._lib.gnutls_certificate_set_x509_crl_file(
                    self._credentials, _path_bytes(crl), GNUTLS_X509_FMT_PEM
                )
                if loaded < 0:
                    self._raise("load CRL", loaded)

            if not cert:
                raise GnuTLSError("load client identity", 0, "missing client certificate")
            if not key:
                key = cert
            self._check(
                "load client certificate/private key",
                self._lib.gnutls_certificate_set_x509_key_file(
                    self._credentials, _path_bytes(cert), _path_bytes(key), GNUTLS_X509_FMT_PEM
                ),
            )
            self._check(
                "initialize TLS session",
                self._lib.gnutls_init(
                    ctypes.byref(self._session), GNUTLS_CLIENT | GNUTLS_NONBLOCK
                ),
            )
            priority = self._priority(tls_version)
            self._check(
                "set TLS priority",
                self._lib.gnutls_priority_set_direct(self._session, priority, None),
            )
            self._check(
                "attach certificate credentials",
                self._lib.gnutls_credentials_set(
                    self._session, GNUTLS_CRD_CERTIFICATE, self._credentials
                ),
            )

            expected_name = server_name.encode("utf-8") if server_name else None
            self._lib.gnutls_session_set_verify_cert(
                self._session,
                expected_name if verify_hostname else None,
                0,
            )
            if server_name and not _is_ip_address(server_name):
                name = server_name.encode("idna")
                name_buffer = ctypes.create_string_buffer(name)
                self._check(
                    "set TLS server name",
                    self._lib.gnutls_server_name_set(
                        self._session,
                        GNUTLS_NAME_DNS,
                        ctypes.cast(name_buffer, ctypes.c_void_p),
                        len(name),
                    ),
                )

            if advertise_peer_allowed_to_send:
                self._lib.gnutls_heartbeat_enable(
                    self._session, GNUTLS_HB_PEER_ALLOWED_TO_SEND
                )
                self._heartbeat_advertised = True

            self._raw.setblocking(False)
            self._lib.gnutls_transport_set_int2(
                self._session, self._raw.fileno(), self._raw.fileno()
            )
            self._run_until_complete("TLS handshake", self._lib.gnutls_handshake)
            verify_status = int(self._lib.gnutls_session_get_verify_cert_status(self._session))
            if verify_status:
                raise GnuTLSError(
                    "verify peer certificate",
                    0,
                    f"certificate verification status 0x{verify_status:x}",
                    verify_status=verify_status,
                )
        except BaseException:
            self.close(abort=True)
            raise

    @staticmethod
    def _priority(value: str | None) -> bytes:
        normalized = str(value or "auto").lower().replace("tls", "").replace("v", "").replace("_", ".")
        if normalized in {"", "auto", "default"}:
            return b"NORMAL"
        if normalized == "1.2":
            return b"NORMAL:-VERS-ALL:+VERS-TLS1.2"
        if normalized == "1.3":
            return b"NORMAL:-VERS-ALL:+VERS-TLS1.3"
        raise ValueError("TLS version must be auto, 1.2, or 1.3")

    @property
    def heartbeat_advertised(self) -> bool:
        return self._heartbeat_advertised

    @property
    def tls_backend(self) -> str:
        return "gnutls"

    @property
    def tls_backend_version(self) -> str:
        return self._library.version

    @property
    def tls_protocol(self) -> str | None:
        if not self._session:
            return None
        value = self._lib.gnutls_protocol_get_name(
            self._lib.gnutls_protocol_get_version(self._session)
        )
        return value.decode("ascii", errors="replace") if value else None

    @property
    def cipher(self) -> tuple[str | None, str | None, None]:
        if not self._session:
            return (None, self.tls_protocol, None)
        value = self._lib.gnutls_cipher_get_name(self._lib.gnutls_cipher_get(self._session))
        name = value.decode("ascii", errors="replace") if value else None
        return (name, self.tls_protocol, None)

    def _verify_status(self) -> int:
        if not self._session:
            return 0
        status = int(self._lib.gnutls_session_get_verify_cert_status(self._session))
        # UINT_MAX means the handshake ended before peer-certificate
        # verification produced a status. Treating its set bits as a
        # hostname mismatch would fabricate an N5 result from a bare EOF.
        return 0 if status == 0xFFFFFFFF else status

    def _alert_name(self) -> str | None:
        if not self._session:
            return None
        alert = self._lib.gnutls_alert_get(self._session)
        if alert < 0:
            return None
        value = self._lib.gnutls_alert_get_name(alert)
        return value.decode("ascii", errors="replace") if value else str(alert)

    def _raise(self, operation: str, code: int) -> None:
        raise GnuTLSError(
            operation,
            code,
            self._library.error_text(code),
            verify_status=self._verify_status(),
            alert=self._alert_name(),
        )

    def _check(self, operation: str, code: int) -> int:
        if int(code) < 0:
            self._raise(operation, int(code))
        return int(code)

    def _wait(self, deadline: float | None) -> None:
        timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
        if timeout == 0.0:
            raise socket.timeout("GnuTLS operation timed out")
        if self._lib.gnutls_record_get_direction(self._session):
            _readable, writable, _errors = select.select([], [self._raw], [self._raw], timeout)
            if not writable:
                raise socket.timeout("GnuTLS operation timed out")
        else:
            readable, _writable, _errors = select.select([self._raw], [], [self._raw], timeout)
            if not readable:
                raise socket.timeout("GnuTLS operation timed out")

    def _run_until_complete(self, operation: str, function: Any) -> int:
        deadline = time.monotonic() + self._timeout if self._timeout else None
        while True:
            code = int(function(self._session))
            if code >= 0:
                return code
            if code in {GNUTLS_E_AGAIN, GNUTLS_E_INTERRUPTED}:
                self._wait(deadline)
                continue
            self._raise(operation, code)

    def fileno(self) -> int:
        return self._raw.fileno()

    def getpeername(self) -> Any:
        return self._raw.getpeername()

    def getsockname(self) -> Any:
        return self._raw.getsockname()

    def settimeout(self, value: float | None) -> None:
        self._timeout = None if value is None else float(value)

    def gettimeout(self) -> float | None:
        return self._timeout

    def send(self, data: bytes | bytearray | memoryview) -> int:
        payload = bytes(data)
        if not payload:
            return 0
        buffer = ctypes.create_string_buffer(payload, len(payload))
        deadline = time.monotonic() + self._timeout if self._timeout else None
        while True:
            code = int(self._lib.gnutls_record_send(
                self._session, ctypes.cast(buffer, ctypes.c_void_p), len(payload)
            ))
            if code >= 0:
                return code
            if code in {GNUTLS_E_AGAIN, GNUTLS_E_INTERRUPTED}:
                self._wait(deadline)
                continue
            self._raise("send TLS record", code)

    def sendall(self, data: bytes | bytearray | memoryview) -> None:
        payload = memoryview(data)
        sent = 0
        while sent < len(payload):
            count = self.send(payload[sent:])
            if count <= 0:
                raise GnuTLSError("send TLS record", count, "connection closed")
            sent += count

    def recv(self, size: int, _flags: int = 0) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        deadline = time.monotonic() + self._timeout if self._timeout else None
        while True:
            code = int(self._lib.gnutls_record_recv(
                self._session, ctypes.cast(buffer, ctypes.c_void_p), size
            ))
            if code > 0:
                return buffer.raw[:code]
            if code == 0:
                return b""
            if code == GNUTLS_E_HEARTBEAT_PING_RECEIVED:
                # RFC 8071's peer_allowed_to_send promise includes handling
                # HeartbeatRequest, not merely advertising an extension byte.
                pong = int(self._lib.gnutls_heartbeat_pong(self._session, 0))
                if pong < 0 and pong not in {GNUTLS_E_AGAIN, GNUTLS_E_INTERRUPTED}:
                    self._raise("send TLS heartbeat response", pong)
                continue
            if code in {GNUTLS_E_AGAIN, GNUTLS_E_INTERRUPTED}:
                self._wait(deadline)
                continue
            self._raise("receive TLS record", code)

    def close(self, abort: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._session and not abort:
                try:
                    self._lib.gnutls_bye(self._session, GNUTLS_SHUT_RDWR)
                except Exception:
                    pass
        finally:
            if self._session:
                self._lib.gnutls_deinit(self._session)
                self._session = ctypes.c_void_p()
            if self._credentials:
                self._lib.gnutls_certificate_free_credentials(self._credentials)
                self._credentials = ctypes.c_void_p()
            try:
                self._raw.close()
            except OSError:
                pass


def wrap_client_socket(raw_socket: socket.socket, settings: Any, server_name: str | None) -> GnuTLSSocket:
    """Wrap an already-connected TCP socket as a verified GnuTLS client."""

    return GnuTLSSocket(
        raw_socket,
        cert=settings.cert,
        key=settings.key,
        trusted_ca=settings.trusted_ca,
        crl=settings.crl,
        server_name=server_name,
        verify_hostname=settings.verify_hostname,
        tls_version=settings.tls_version,
        timeout=settings.timeout,
        advertise_peer_allowed_to_send=True,
    )
