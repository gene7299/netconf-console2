"""Recover read-only snapshots when a remote operational provider fails."""
from copy import deepcopy
from dataclasses import dataclass, field

from lxml import etree
from ncclient.operations.rpc import RPCError

from ..trace import redact_secrets
from .model import EditError, children


@dataclass
class OperationalRead:
    data: etree._Element
    warnings: list[str] = field(default_factory=list)
    state_failures: dict[str, str] = field(default_factory=dict)


def is_provider_failure(exc):
    """Only explicit server callback/data errors permit a read-only fallback."""
    if not isinstance(exc, (RPCError, EditError)):
        return False
    errors = getattr(exc, "errors", None) or [exc]
    if any(getattr(error, "tag", None) in {
            "access-denied", "invalid-value", "unknown-element", "operation-not-supported"
    } for error in errors):
        return False
    text = str(exc).casefold()
    return ("user callback failed" in text or
            ("decimal64" in text and "fraction digits" in text))


def read_operational(read_state, read_config, schema, root_tag=None,
                     progress=lambda _text: None, cancelled=lambda: False):
    """Try one full read, then isolate failing modules above a running baseline.

    read_state(module, tags) selects a module's top-level nodes when tags is
    supplied. A failed module keeps its freshly read running configuration;
    no operational values are invented, rounded, or taken from an old snapshot.
    Transport/authentication/filter errors still propagate to the caller.
    """
    def check_cancelled():
        if cancelled():
            raise InterruptedError("Operational read cancelled")

    check_cancelled()
    try:
        return OperationalRead(deepcopy(read_state(None, None)))
    except Exception as exc:
        if not is_provider_failure(exc):
            raise
        original_error = redact_secrets(str(exc))[:4000]

    check_cancelled()
    progress("Operational provider failed; reading running configuration…")
    baseline = deepcopy(read_config())
    warnings = [
        "RU operational read failed; recovered data is read per module, not an atomic snapshot.",
        original_error,
    ]
    groups = {}
    if root_tag:
        namespace = etree.QName(root_tag).namespace
        groups[schema.namespaces.get(namespace, namespace)] = [root_tag]
    elif schema.complete and schema.nodes:
        for info in schema.child_nodes(()):
            groups.setdefault(info.module, []).append(info.path[0])
        # An outdated schema must not make an unqueried module look complete.
        covered = {tag for tags in groups.values() for tag in tags}
        if any(node.tag not in covered for node in children(baseline)):
            groups.clear()
    if not groups:
        warnings.append("No complete schema is available for module isolation; showing running configuration only.")
        return OperationalRead(baseline, warnings, {"*": original_error})

    failures = {}
    for module, tags in groups.items():
        check_cancelled()
        progress("Reading operational module: " + str(module))
        try:
            part = read_state(module, tags)
        except Exception as exc:
            if not is_provider_failure(exc):
                raise
            detail = redact_secrets(str(exc))[:4000]
            failures.update((tag, detail) for tag in tags)
            continue
        if any(node.tag not in tags for node in children(part)):
            raise EditError("Server ignored the operational module filter; reload without config false.")
        for node in list(baseline):
            if node.tag in tags:
                baseline.remove(node)
        baseline.extend(deepcopy(node) for node in children(part))
    check_cancelled()
    return OperationalRead(baseline, warnings, failures)
