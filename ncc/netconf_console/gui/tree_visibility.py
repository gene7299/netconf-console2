"""Conservative, instance-aware comparison of two data snapshots.

Missing means absent in a successful NETCONF read, not proof of NACM denial.
Unknown/ambiguous list identities must never be paired by position.
"""
from collections import defaultdict

from .model import EditError, children, identity


def visibility(sysrepo_data, netconf_data, schema):
    """Return element -> visible/missing/unknown, including every descendant."""
    result = {}

    def mark(node, status):
        result[node] = status
        for child in children(node):
            mark(child, status)

    def walk(repo_parent, nc_parent, path):
        repo_children, nc_children = children(repo_parent), children(nc_parent)
        groups = defaultdict(list)
        for other in nc_children:
            groups[other.tag].append(other)
        indexed = {}
        repo_counts = defaultdict(int)
        for node in repo_children:
            try:
                repo_counts[identity(node, schema, (*path, node.tag))] += 1
            except EditError:
                pass
        for node in repo_children:
            current = (*path, node.tag)
            peers = groups[node.tag]
            if not peers:
                mark(node, "missing")
                continue
            info = schema.lookup(current)
            # Even a single unknown container could actually be a keyed list.
            if info is None or (info.kind == "list" and not info.keys):
                mark(node, "unknown")
                continue
            try:
                wanted = identity(node, schema, current)
                if node.tag not in indexed:
                    by_identity = defaultdict(list)
                    for other in peers:
                        by_identity[identity(other, schema, current)].append(other)
                    indexed[node.tag] = by_identity
                matches = indexed[node.tag].get(wanted, [])
            except EditError:
                mark(node, "unknown")
                continue
            if len(matches) > 1 or repo_counts[wanted] > 1:
                mark(node, "unknown")
            elif not matches:
                mark(node, "missing")
            else:
                result[node] = "visible"
                walk(node, matches[0], current)

    if netconf_data is None:
        for node in children(sysrepo_data):
            mark(node, "unknown")
    else:
        walk(sysrepo_data, netconf_data, ())
    return result
