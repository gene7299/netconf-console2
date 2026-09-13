"""Bounded profile import preparation, never connects or changes live fields."""
from copy import deepcopy
import json
from pathlib import Path

from .preferences import MAGIC, PreferencesError, PreferencesStore, public_book, validate_book

MAX_BYTES = 4 * 1024 * 1024
PATH_FIELDS = {"ssh_key", "tls_key", "cert", "trusted_ca", "crl", "known_hosts", "schema_dir",
               "jump_key", "jump_known_hosts", "admin_key", "admin_known_hosts"}


def read_import(path, allowed_fields):
    file = Path(path)
    if file.stat().st_size > MAX_BYTES:
        raise PreferencesError("設定匯入上限 4 MiB")
    raw = file.read_bytes()
    encrypted = raw.startswith(MAGIC)
    try:
        book = PreferencesStore(file).load() if encrypted else validate_book(json.loads(raw.decode("utf-8-sig")))
        records = [book["last"].get("values", {})] + list(book["connections"].values()) + list(book["accounts"].values())
        if len(records) > 1000 or any(set(record) - set(allowed_fields) for record in records):
            raise PreferencesError("包含未知設定欄位或超過 1,000 組，請檢查檔案版本")
        if any(len(k) > 200 or len(str(v)) > 4096 for record in records for k, v in record.items()):
            raise PreferencesError("設定欄位過長")
        if any(not name.strip() or len(name) > 200 for group in ("connections", "accounts") for name in book[group]):
            raise PreferencesError("設定組名稱不合法")
        # Plain JSON never imports secrets, even if somebody hand-added them.
        return deepcopy(book) if encrypted else public_book(book), encrypted
    except PreferencesError:
        raise
    except Exception:
        raise PreferencesError("無法解析設定檔；原設定不受影響") from None


def merge_profiles(current, imported, choices, *, include_secrets=False, keep_paths=False):
    """choices: (group, old name, new name, explicit replace). All or nothing."""
    result = deepcopy(validate_book(current))
    source = deepcopy(validate_book(imported)) if include_secrets else public_book(imported)
    if not choices:
        raise PreferencesError("請至少勾選一組設定")
    selected = set()
    for group, old, new, replace in choices:
        new = new.strip()
        if group not in {"connections", "accounts"} or old not in source[group] or not new or len(new) > 200:
            raise PreferencesError("請選取有效項目與名稱")
        if (group, new) in selected or new in result[group] and not replace:
            raise PreferencesError("名稱重複，請重新命名或明確選擇取代：" + new)
        selected.add((group, new))
        record = deepcopy(source[group][old])
        if not keep_paths:
            for field in PATH_FIELDS & record.keys():
                record[field] = ""
        result[group][new] = record
    # Deliberately preserve last-used fields and selected names of this instance.
    return result
