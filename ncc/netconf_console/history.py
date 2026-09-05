"""Persistent and credential-safe interactive command history."""

from __future__ import annotations

import re

from prompt_toolkit.history import FileHistory

from .trace import redact_secrets


_PASSWORD_OPTION = re.compile(
    r"(?i)(?P<option>(?<!\S)(?:--password|-p))"
    r"(?:"
    r"=(?:\"[^\"]*\"|'[^']*'|[^\s]+)"
    r"|"
    r"\s+(?!-{1,2}[A-Za-z])(?:\"[^\"]*\"|'[^']*'|[^\s]+)"
    r")?"
)


def sanitize_history_entry(command: str) -> str:
    """Remove credentials while keeping a recalled command executable.

    An explicit password value becomes a bare ``--password``/``-p`` option,
    which causes the CLI to prompt securely when the command is replayed.
    Secret-looking XML leaves are redacted by the same policy as wire traces.
    """

    command = redact_secrets(command)
    return _PASSWORD_OPTION.sub(lambda match: match.group("option"), command)


class SecureFileHistory(FileHistory):
    """prompt_toolkit history that never stores plaintext passwords."""

    def append_string(self, string: str) -> None:
        sanitized = sanitize_history_entry(string)
        if sanitized.strip():
            super().append_string(sanitized)
