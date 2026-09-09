"""Encrypted, versioned configuration snapshots and opt-in atomic restore choices."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from lxml import etree

from .model import EditError, Selection, build_plan, children, identity, parse_editor, semantic
from .preferences import _dpapi
from .workspace import import_selection, instance_path

MAGIC = b"NCC-CONFIG-BACKUP\x01"
MAX_BYTES = 32 * 1024 * 1024


def save_backup(path, snapshot, device, schema):
    if snapshot.options.state:
        raise EditError("備份僅接受 config-only 讀取結果。")
    text = etree.tostring(snapshot.data, encoding="unicode")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise EditError("備份 XML 上限 32 MiB。")
    payload = {"version": 1, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "device": device, "source": snapshot.options.source, "namespaces": schema.namespaces,
               "xml": text}
    encrypted = MAGIC + _dpapi(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    destination = Path(path)
    if destination.suffix.lower() != ".nccbackup":
        raise EditError("加密設定備份請使用 .nccbackup 副檔名。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent, prefix=".nccbackup-", delete=False) as out:
            temporary = Path(out.name)
            out.write(encrypted)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return payload["created"]


def load_backup(path):
    file = Path(path)
    if file.stat().st_size > MAX_BYTES * 2:
        raise EditError("備份檔過大。")
    raw = file.read_bytes()
    if not raw.startswith(MAGIC):
        raise EditError("不是支援的加密設定備份；不會把普通 XML 當備份開啟。")
    try:
        payload = json.loads(_dpapi(raw[len(MAGIC):], decrypt=True).decode("utf-8"))
        if payload.get("version") != 1 or payload.get("source") not in {"running", "candidate", "startup"}:
            raise ValueError()
        if not all(isinstance(payload.get(k), str) for k in ("created", "device", "xml")):
            raise ValueError()
        if len(payload["xml"].encode("utf-8")) > MAX_BYTES:
            raise ValueError()
        parse_editor(payload["xml"])
        return payload
    except Exception:
        raise EditError("無法解密或解析備份（需原 Windows 帳號／電腦，且檔案完整）。") from None


@dataclass
class RestoreChange:
    address: tuple
    path: str
    before: object
    after: object

    @property
    def label(self):
        return ("新增 " if self.before is None else "移除 " if self.after is None else "修改 ") + self.path


def restore_choices(selection, backup_xml, schema, target):
    imported = import_selection(backup_xml.encode("utf-8"), selection, schema)
    # Reject unsupported key renames, opaque edits and ordering before presenting choices.
    build_plan(selection, etree.tostring(imported).decode(), schema, target)
    changes = []
    def walk(old, new, path, ancestors, address):
        if old is not None and new is not None and semantic(old) == semantic(new):
            return
        info = schema.lookup(path)
        node = new if new is not None else old
        if info is None or info.config is not True:
            return
        if old is None or new is None or info.kind in {"leaf", "leaf-list"}:
            changes.append(RestoreChange(address, instance_path(Selection(node, ancestors), schema),
                                         deepcopy(old), deepcopy(new)))
            return
        old_items = {identity(n, schema, path + (n.tag,)): n for n in children(old)}
        new_items = {identity(n, schema, path + (n.tag,)): n for n in children(new)}
        for key in dict.fromkeys((*old_items, *new_items)):
            walk(old_items.get(key), new_items.get(key), path + (key[0],), (*ancestors, node), (*address, key))
    walk(selection.node, imported, selection.path, selection.ancestors, ())
    return changes


def apply_choices(selection, changes, indexes, schema, target):
    selected = sorted(set(indexes))
    if any(type(i) is not int or i < 0 or i >= len(changes) for i in selected):
        raise EditError("還原項目已改變，請重新預覽。")
    edited = deepcopy(selection.node)
    for index in selected:
        change = changes[index]
        if not change.address:
            edited = deepcopy(change.after)
            continue
        parent, path = edited, selection.path
        for key in change.address[:-1]:
            matches = [n for n in children(parent) if identity(n, schema, path + (n.tag,)) == key]
            if len(matches) != 1:
                raise EditError("還原定位不唯一。")
            parent = matches[0]
            path += (parent.tag,)
        key = change.address[-1]
        matches = [n for n in children(parent) if identity(n, schema, path + (n.tag,)) == key]
        if change.before is None:
            if matches:
                raise EditError("要還原的新增 instance 已存在。")
            parent.append(deepcopy(change.after))
        elif len(matches) != 1:
            raise EditError("要還原的 instance 已消失。")
        elif change.after is None:
            parent.remove(matches[0])
        else:
            parent.replace(matches[0], deepcopy(change.after))
    if edited is None:
        raise EditError("不能移除目前選取的整個根；請選擇父節點。")
    text = etree.tostring(edited, encoding="unicode")
    build_plan(selection, text, schema, target)
    return text
