"""Human-friendly capability classification for the interactive console."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def capability_label(uri: str) -> str:
    """Map a NETCONF capability URI to a concise operator-facing label."""

    value = str(uri)
    if value in {"urn:ietf:params:netconf:base:1.0", ":base:1.0"}:
        return "NETCONF Base 1.0"
    if value in {"urn:ietf:params:netconf:base:1.1", ":base:1.1"}:
        return "NETCONF Base 1.1"
    mappings = (
        ("writable-running", "writable-running"),
        ("candidate", "candidate"),
        ("confirmed-commit", "confirmed-commit"),
        ("rollback-on-error", "rollback-on-error"),
        ("startup", "startup"),
        ("validate", "validate"),
        ("xpath", "xpath"),
        ("notification", "notification"),
        ("interleave", "interleave"),
        ("with-defaults", "with-defaults"),
        ("ietf-netconf-nmda", "NMDA (ietf-netconf-nmda)"),
        ("ietf-yang-library", "YANG library"),
        ("yang-push", "YANG Push"),
        ("subscribed-notifications", "subscribed notifications"),
        ("monitoring", "NETCONF monitoring"),
    )
    for needle, label in mappings:
        if needle in value:
            return label
    parsed = urlparse(value)
    params = parse_qs(parsed.query)
    if "module" in params:
        return "YANG module: %s" % params["module"][0]
    return value


def capability_rows(capabilities: list[str]) -> list[tuple[str, str]]:
    """Return ``(label, uri)`` rows, preserving server order."""

    return [(capability_label(uri), uri) for uri in capabilities]


def negotiated_version(session: object) -> str:
    return "1.1" if getattr(session, "_base", 1) == 2 else "1.0"
