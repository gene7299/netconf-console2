"""Portable interactive completion helpers.

The stdlib ``readline`` module is not shipped with a normal Windows Python
installation, so importing it is deliberately optional.  The CLI uses
prompt_toolkit when available and still has a functional input fallback when
neither line-editing package is installed.
"""

from __future__ import annotations

import itertools
import os

try:  # pragma: no cover - availability depends on the host platform
    import readline  # type: ignore
except ImportError:  # pragma: no cover
    readline = None  # type: ignore


def no_arg_completion(_text: str) -> list[str]:
    return []


def commit_arg_completion(_text: str) -> list[str]:
    return ["confirmed"]


def stream_arg_completion(_text: str) -> list[str]:
    return ["NETCONF"]


def fix_completion_dirname(name: str) -> str:
    if os.path.isdir(name) and not name.endswith(("/", "\\")):
        return name + os.sep
    return name


def filename_arg_completion(text: str) -> list[str]:
    directory = os.path.dirname(text)
    basename = os.path.basename(text)
    scan_dir = directory or "."
    try:
        names = os.listdir(scan_dir)
    except OSError:
        return []
    return [
        fix_completion_dirname(os.path.join(directory, name))
        for name in names
        if name.startswith(basename)
    ]


def validate_arg_completion(text: str) -> list[str]:
    return ["candidate"] + filename_arg_completion(text)


class NCCompleter:
    """Completion callback compatible with readline's ``set_completer``."""

    def __init__(self, operations: dict[str, object], option_args: dict[str, object]):
        self.operations = operations
        self.option_args = option_args

    def __call__(self, text: str, state: int) -> str | None:
        if readline is None:
            return None
        return self.complete_with(text, state, readline.get_line_buffer())

    def complete_command_argument(self, operation: object, text: str) -> list[str]:
        completion = getattr(operation, "arg_completion", None)
        return completion(text) if callable(completion) else []

    def lookup_option(self, option_text: str) -> dict[str, object] | None:
        descriptor = self.option_args.get(option_text)
        if isinstance(descriptor, dict):
            return descriptor
        for value in self.option_args.values():
            if isinstance(value, dict) and option_text in value.get("options", []):
                return value
        return None

    def complete_command_options(self, text: str, line_args: list[str]) -> list[str]:
        if not line_args:
            return []
        operation = self.operations.get(line_args[0])
        if operation is None:
            return []
        word_count = len(line_args) + (1 if text == "" else 0)
        if word_count == 2 and getattr(operation, "nargs", 0) != 0:
            completions = self.complete_command_argument(operation, text)
            if getattr(operation, "nargs", 0) == 1:
                return completions
        if word_count > 2:
            descriptor = self.lookup_option(line_args[-2])
            if descriptor:
                choices = descriptor.get("choices")
                if choices:
                    return list(choices)
                if descriptor.get("dest") in {"db", "datastore"}:
                    return ["running", "candidate", "startup", "operational"]
        command_opts = getattr(operation, "command_opts", [])
        options: list[str] = []
        for key in command_opts:
            descriptor = self.option_args.get(key)
            if isinstance(descriptor, dict):
                options.extend(descriptor.get("options", []))
            elif isinstance(descriptor, (tuple, list)):
                options.extend(descriptor[0])
        return options

    def complete_with(self, text: str, state: int, line: str) -> str | None:
        line_args = line.split()
        if len(line_args) > 1 or (len(line_args) == 1 and text == ""):
            variants = self.complete_command_options(text, line_args)
        else:
            variants = self.operations.keys()
        permitted = sorted({variant for variant in variants if variant.startswith(text)})
        return permitted[state] if state < len(permitted) else None
