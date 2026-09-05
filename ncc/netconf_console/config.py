"""Connection profile loading for netconf-console2."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    """Return the per-user configuration directory on the current platform."""

    # Path.home() is the right fallback for Windows, but USERPROFILE makes the
    # intended native location explicit and also helps controlled test homes.
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())
    return home / ".netconf-console2"


def default_config_path() -> Path:
    return config_dir() / "config.toml"


def load_profiles(filename: str | os.PathLike[str] | None = None) -> dict[str, dict[str, Any]]:
    """Load ``[profiles.<name>]`` values, returning an empty mapping if absent.

    TOML is part of Python 3.11+.  ``tomli`` remains an optional fallback for
    supported older Python installations; no profile file is created by the
    client.
    """

    path = Path(filename).expanduser() if filename else default_config_path()
    if not path.is_file():
        return {}
    try:
        import tomllib
    except ImportError:  # pragma: no cover - only used on Python < 3.11
        try:
            import tomli as tomllib
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Reading profiles on Python < 3.11 requires tomli") from exc
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    profiles = document.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("The profiles entry in %s must be a TOML table" % path)
    result: dict[str, dict[str, Any]] = {}
    for name, values in profiles.items():
        if not isinstance(values, dict):
            raise ValueError("Profile %s in %s must be a TOML table" % (name, path))
        # Do not allow a profile to smuggle credentials into trace or output
        # configuration.  Passwords are intentionally never loaded.
        result[str(name)] = {str(key): value for key, value in values.items() if key != "password"}
    return result


def get_profile(name: str, filename: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    profiles = load_profiles(filename)
    try:
        return dict(profiles[name])
    except KeyError as exc:
        path = Path(filename).expanduser() if filename else default_config_path()
        raise ValueError("Profile %r was not found in %s" % (name, path)) from exc
