"""Redacted NETCONF wire tracing for the command-line application."""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from typing import TextIO


_SECRET_TAG = re.compile(
    r"(?is)(<(?:[\w.-]+:)?(?:password|passwd|secret|private-key|privatekey|cleartext-password)"
    r"(?:\s[^>]*)?>)(.*?)(</(?:[\w.-]+:)?(?:password|passwd|secret|private-key|privatekey|cleartext-password)\s*>)"
)
_SECRET_ATTRIBUTE = re.compile(
    r"(?is)((?:password|passwd|secret|private[-_]?key)\s*=\s*[\"'])(.*?)([\"'])"
)


def redact_secrets(value: object) -> str:
    """Return *value* as text with common credential fields masked.

    NETCONF RPCs normally do not contain the SSH/TLS credentials used to open
    the transport, but custom RPCs and configuration payloads can contain
    sensitive leaves.  Redaction is intentionally conservative and only masks
    names that are unambiguously credential-like.
    """

    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = _SECRET_TAG.sub(r"\1***REDACTED***\3", text)
    return _SECRET_ATTRIBUTE.sub(r"\1***REDACTED***\3", text)


class TraceSink:
    """Write redacted SEND/RECV records to stderr and/or a trace file."""

    def __init__(
        self,
        enabled: bool = False,
        filename: str | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self.enabled = enabled or filename is not None
        self.stream = stream if stream is not None else sys.stderr
        self._file: TextIO | None = None
        self._lock = threading.Lock()
        if filename:
            path = Path(filename).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("a", encoding="utf-8", newline="")

    def emit(self, direction: str, payload: object) -> None:
        if not self.enabled:
            return
        body = redact_secrets(payload).rstrip("\r\n")
        record = f"{direction}:\n{body}\n"
        with self._lock:
            if self.stream is not None:
                self.stream.write(record)
                self.stream.flush()
            if self._file is not None:
                self._file.write(record)
                self._file.flush()

    def send(self, payload: object) -> None:
        self.emit("SEND", payload)

    def receive(self, payload: object) -> None:
        self.emit("RECV", payload)

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None
