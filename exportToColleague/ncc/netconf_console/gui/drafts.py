"""Bounded, encrypted local draft shelf. No network or implicit replay."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from lxml import etree

from .client import check_selection_current
from .model import EditError, Selection, _key_shell, identity, parse_editor
from .preferences import _dpapi
from .workspace import instance_path

MAGIC = b"NCC-DRAFTS\x01"
MAX_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 100


def schema_fingerprint(schema):
    body = [asdict(info) for _, info in sorted(schema.nodes.items())]
    return hashlib.sha256(json.dumps([schema.content_hash, body], sort_keys=True).encode()).hexdigest()


def address(selection, schema):
    chain = (*selection.ancestors, selection.node)
    return tuple(identity(node, schema, selection.path[:i + 1]) for i, node in enumerate(chain))


def draft_key(scope, source, selection, schema):
    return hashlib.sha256(json.dumps([scope, source, address(selection, schema)]).encode()).hexdigest()


def selection_copy(selection, schema):
    ancestors = tuple(_key_shell(node, schema.lookup(selection.path[:i + 1]))
                      for i, node in enumerate(selection.ancestors))
    return Selection(deepcopy(selection.node), ancestors, selection.exists)


@dataclass
class Draft:
    key: str
    scope: str
    source: str
    selection: Selection
    text: str
    schema_hash: str
    label: str
    updated: str
    session: object = None
    status: str = "待重新比對"


class DraftShelf:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.entries = {}
        self.load_error = ""

    def matching(self, scope, source, selection, schema):
        return self.entries.get(draft_key(scope, source, selection, schema))

    def overlapping(self, scope, source, selection, schema):
        entries = self.overlapping_entries(scope, source, selection, schema)
        return entries[0] if entries else None

    def overlapping_entries(self, scope, source, selection, schema):
        """Return all parent/child drafts that overlap ``selection``."""
        current = address(selection, schema)
        result = []
        for entry in self.entries.values():
            if entry.scope != scope or entry.source != source:
                continue
            other = address(entry.selection, schema)
            if other != current and (other[:len(current)] == current or current[:len(other)] == other):
                result.append(entry)
        return result

    def descendant_entries(self, scope, source, selection, schema):
        """Return drafts strictly below ``selection``."""
        current = address(selection, schema)
        return [entry for entry in self.entries.values()
                if entry.scope == scope and entry.source == source
                and (other := address(entry.selection, schema)) != current
                and len(other) > len(current) and other[:len(current)] == current]

    def ancestor_entries(self, scope, source, selection, schema):
        """Return drafts strictly above ``selection``."""
        current = address(selection, schema)
        return [entry for entry in self.entries.values()
                if entry.scope == scope and entry.source == source
                and (other := address(entry.selection, schema)) != current
                and len(other) < len(current) and current[:len(other)] == other]

    def capture(self, scope, source, selection, text, schema, session, fingerprint):
        if source not in {"running", "candidate"}:
            return None
        if len(text.encode("utf-8")) > MAX_BYTES // 2:
            raise EditError("單份草稿上限 16 MiB")
        conflict = self.overlapping(scope, source, selection, schema)
        if conflict:
            raise EditError("與既有父／子範圍草稿重疊，請先開啟該草稿：" + conflict.label)
        key = draft_key(scope, source, selection, schema)
        previous = self.entries.get(key)
        if previous:
            if previous.text != text:
                previous.text = text
                previous.updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return previous
        if len(self.entries) >= MAX_ENTRIES:
            raise EditError("草稿清單上限 100 份，請先移除不需要的草稿")
        entry = Draft(key, scope, source, selection_copy(selection, schema), text, fingerprint,
                      instance_path(selection, schema), datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      session, "編輯中")
        self.entries[key] = entry
        return entry

    def verify(self, entry, scope, source, schema, data, session):
        if scope != entry.scope or source != entry.source:
            raise EditError("設備／帳號／連線路徑或 source 與草稿不同，不能載入到此目標")
        if not schema.complete or schema_fingerprint(schema) != entry.schema_hash:
            raise EditError("YANG schema 與草稿不同或不完整，請核對 schema 後重建草稿")
        if entry.key != draft_key(scope, source, entry.selection, schema):
            raise EditError("草稿定位資訊不一致，請核對草稿檔")
        try:
            check_selection_current(data, entry.selection, schema)
        except EditError:
            entry.session = None
            entry.status = "設備資料衝突／需核對"
            raise
        # Invalid partial XML may be recovered for editing; it cannot generate a RPC.
        entry.session = session
        entry.status = "已比對，未送出"

    def invalidate(self, scope=None):
        for entry in self.entries.values():
            if scope is None or entry.scope == scope:
                entry.session = None
                entry.status = "待重新比對"

    def load(self):
        if self.path is None or not self.path.exists():
            return
        try:
            if self.path.stat().st_size > MAX_BYTES * 2:
                raise ValueError()
            raw = self.path.read_bytes()
            if not raw.startswith(MAGIC):
                raise ValueError()
            decoded = _dpapi(raw[len(MAGIC):], decrypt=True)
            if len(decoded) > MAX_BYTES:
                raise ValueError()
            payload = json.loads(decoded)
            if payload.get("version") != 1 or not isinstance(payload.get("drafts"), list) or len(payload["drafts"]) > MAX_ENTRIES:
                raise ValueError()
            loaded = {}
            for item in payload["drafts"]:
                keys = ("key", "scope", "source", "text", "schema_hash", "label", "updated", "node")
                if not all(isinstance(item.get(k), str) for k in keys) or item["source"] not in {"running", "candidate"}:
                    raise ValueError()
                if type(item.get("exists")) is not bool or not isinstance(item.get("ancestors"), list) or len(item["ancestors"]) > 128:
                    raise ValueError()
                selection = Selection(parse_editor(item["node"]), tuple(parse_editor(x) for x in item["ancestors"]), item["exists"])
                entry = Draft(*(item[k] for k in ("key", "scope", "source")), selection,
                              *(item[k] for k in ("text", "schema_hash", "label", "updated")))
                if entry.key in loaded or not selection.exists and selection.ancestors:
                    raise ValueError()
                loaded[entry.key] = entry
            self.entries = loaded
        except Exception:
            self.load_error = "無法解密／解析草稿檔；需要原 Windows 帳號與電腦。原檔保留且停止覆寫。"

    def save(self):
        if self.path is None:
            return
        if self.load_error:
            raise EditError(self.load_error)
        items = []
        for entry in self.entries.values():
            item = {key: getattr(entry, key) for key in ("key", "scope", "source", "text", "schema_hash", "label", "updated")}
            item.update(node=entry.selection.text(), ancestors=[etree.tostring(n, encoding="unicode") for n in entry.selection.ancestors],
                        exists=entry.selection.exists)
            items.append(item)
        raw = json.dumps({"version": 1, "drafts": items}, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise EditError("草稿清單總上限 32 MiB；未覆寫原檔")
        encrypted = MAGIC + _dpapi(raw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=self.path.parent, prefix=".nccdrafts-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
