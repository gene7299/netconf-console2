"""Personal schema-bound templates, with no state or secret values carried over."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile

from lxml import etree

from .creation import Template
from .drafts import schema_fingerprint
from .model import EditError, children, local, parse_editor
from .preferences import _dpapi

MAGIC = b"NCC-TEMPLATE\x01"
MAX_BYTES = 8 * 1024 * 1024
SECRET = re.compile(r"password|passphrase|secret|private.?key|cleartext|ciphertext|encrypted.?key", re.I)


def from_node(schema, selection, node):
    info = schema.lookup(selection.path)
    if not schema.complete or not info or info.config is not True or info.kind not in {"list", "container"}:
        raise EditError("請選取 schema 完整的可寫 list 或 container")
    template = Template(schema, info)
    template.pending.clear()
    removed = []
    count = 0
    def clean(value, path, sensitive=False):
        nonlocal count
        count += 1
        if count > 512 or len(path) > 32:
            raise EditError("範本上限 512 個節點／32 層，請選較小範圍")
        node_info = schema.lookup(path)
        if node_info is None:
            raise EditError("範本包含未知節點，請先更新 YANG")
        if node_info.config is not True or node_info.kind in {"anyxml", "anydata"}:
            removed.append("唯讀／opaque: " + local(value.tag))
            return None
        sensitive = sensitive or bool(SECRET.search(local(value.tag)))
        result = etree.Element(value.tag, nsmap=value.nsmap)
        if node_info.kind in {"leaf", "leaf-list"}:
            result.text = value.text
            if sensitive or "PRIVATE KEY-----" in (value.text or "") or path[:-1] == selection.path and value.tag in info.keys:
                result.text = None
                template.pending.add(result)
                removed.append("需重填: " + local(value.tag))
        for child in children(value):
            cloned = clean(child, path + (child.tag,), sensitive)
            if cloned is not None:
                result.append(cloned)
        return result
    template.root = clean(node, selection.path)
    return template, removed


def save_template(path, template, schema):
    # Re-sanitize at the persistence boundary, even for a caller-created template.
    from .model import Selection
    ancestors = tuple(etree.Element(tag) for tag in template.path[:-1])
    safe, _ = from_node(schema, Selection(template.root, ancestors), template.root)
    nodes = list(safe.root.iter())
    payload = {"version": 1, "schema": schema_fingerprint(schema), "path": list(safe.path),
               "xml": etree.tostring(safe.root, encoding="unicode"),
               "pending": [i for i, n in enumerate(nodes) if n in safe.pending]}
    data = json.dumps(payload, ensure_ascii=False).encode()
    if len(data) > MAX_BYTES:
        raise EditError("範本上限 8 MiB")
    destination = Path(path)
    if destination.suffix.lower() != ".ncctemplate":
        raise EditError("範本請使用 .ncctemplate 副檔名")
    encrypted = MAGIC + _dpapi(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".ncctemplate-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def load_template(path, schema):
    file = Path(path)
    if file.stat().st_size > MAX_BYTES * 2:
        raise EditError("範本檔過大")
    try:
        raw = file.read_bytes()
        if not raw.startswith(MAGIC):
            raise ValueError()
        decoded = _dpapi(raw[len(MAGIC):], decrypt=True)
        if len(decoded) > MAX_BYTES:
            raise ValueError()
        payload = json.loads(decoded)
        if payload.get("version") != 1 or not isinstance(payload.get("path"), list):
            raise ValueError()
        if not schema.complete or payload.get("schema") != schema_fingerprint(schema):
            raise EditError("範本與目前設備 schema 不同，請重新建立；不會猜測欄位對應")
        path = tuple(payload["path"])
        node = parse_editor(payload["xml"])
        if not path or len(path) > 32 or node.tag != path[-1]:
            raise ValueError()
        from .model import Selection
        template, _ = from_node(schema, Selection(node, tuple(etree.Element(t) for t in path[:-1])), node)
        nodes = list(template.root.iter())
        pending = payload.get("pending")
        if not isinstance(pending, list) or any(type(i) is not int or not 0 <= i < len(nodes) for i in pending):
            raise ValueError()
        template.pending.update(nodes[i] for i in pending)
        return template
    except EditError:
        raise
    except Exception:
        raise EditError("範本無法解密／解析；需要原 Windows 帳號及電腦") from None
