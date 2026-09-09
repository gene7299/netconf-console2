"""Namespace-aware RPC error presentation and conservative editor location."""
from copy import deepcopy
from dataclasses import dataclass
import re

from lxml import etree

from .model import NC, children, parse_editor, xml_spans

HINTS = {
    "invalid-value": "欄位值不合法；請檢查 type、range、enum 或關聯限制。",
    "bad-element": "XML 節點內容不合法，請檢查名稱與值。",
    "unknown-element": "設備不認識此節點；請確認 namespace 與 YANG 版本。",
    "unknown-namespace": "設備不支援此 namespace；請更新 YANG。",
    "missing-element": "缺少必要節點或 list key。",
    "data-missing": "目標節點或相依資料不存在。",
    "data-exists": "要建立的 instance 已存在。",
    "access-denied": "帳號權限不足（可能是 NACM）；不代表密碼錯誤。",
    "lock-denied": "Datastore 無法鎖定，可能由其他 session 持有；不會強制解鎖。",
    "in-use": "資源使用中，請確認其他操作／session。",
    "operation-not-supported": "設備不支援此操作或 capability。",
    "operation-failed": "設備無法完成操作；請查看 server 原始訊息與 error-app-tag。",
}


@dataclass
class ErrorDetail:
    tag: str
    app_tag: str
    path: str
    message: str
    namespaces: dict


def parse_errors(exc):
    raw = getattr(exc, "xml", None)
    roots = []
    if isinstance(raw, etree._Element):
        roots = [raw]
    elif isinstance(raw, list):
        for item in raw:
            element = item if isinstance(item, etree._Element) else getattr(item, "xml", None)
            if isinstance(element, etree._Element):
                roots.append(element)
    details = []
    xml = []
    for root in roots:
        errors = [root] if root.tag == "{%s}rpc-error" % NC else root.findall(".//{%s}rpc-error" % NC)
        if errors:
            xml.append(etree.tostring(root, encoding="unicode", pretty_print=True))
        for error in errors[:20]:
            path = error.find("{%s}error-path" % NC)
            details.append(ErrorDetail(error.findtext("{%s}error-tag" % NC) or "",
                error.findtext("{%s}error-app-tag" % NC) or "", path.text or "" if path is not None else "",
                error.findtext("{%s}error-message" % NC) or "",
                {k: v for k, v in (path.nsmap if path is not None else error.nsmap).items() if k}))
    return details, "\n".join(xml)


def describe_errors(details):
    return "\n\n".join("%s\nerror-tag: %s\nerror-app-tag: %s\nerror-path: %s\nserver: %s" % (
        HINTS.get(d.tag, "NETCONF 操作錯誤；請查看原始回應。"), d.tag, d.app_tag, d.path, d.message)
        for d in details)


def locate_errors(text, selection, details):
    """Accept only bounded location paths, not arbitrary server XPath programs."""
    if selection is None:
        return []
    edited = parse_editor(text)
    data = etree.Element("data")
    cursor = data
    for ancestor in selection.ancestors:
        shell = deepcopy(ancestor)
        # Keep sibling keys so predicates can locate this instance.
        for child in list(shell):
            if children(child):
                shell.remove(child)
        cursor.append(shell)
        cursor = shell
    # Remove the previous selected child when it was a leaf in an ancestor shell.
    for child in list(cursor):
        if child.tag == edited.tag:
            cursor.remove(child)
    cursor.append(edited)
    editable = set(edited.iter())
    matches = set()
    for detail in details:
        path = detail.path.strip()
        for prefix, namespace in detail.namespaces.items():
            if namespace == NC:
                envelope = "/%s:rpc/%s:edit-config/%s:config" % (prefix, prefix, prefix)
                if path.startswith(envelope + "/"):
                    path = path[len(envelope):]
                    break
        if not path.startswith("/") or len(path) > 4096:
            continue
        # Permit predicates with literals and positions; reject functions, axes,
        # union and descendant wildcards rather than guessing the wrong leaf.
        stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "''", path)
        if any(value in stripped for value in ("(", ")", "|", "::", "//", "*", "$")):
            continue
        try:
            found = data.xpath("." + path, namespaces=detail.namespaces)
            if len(found) == 1 and found[0] in editable:
                matches.add(found[0])
        except (etree.XPathError, TypeError):
            continue
    return [(start, end) for node, start, end, _a, _b in xml_spans(text, edited) if node in matches]
