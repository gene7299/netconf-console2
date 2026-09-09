"""Per-user GUI profiles. Only DPAPI ciphertext is ever written to disk."""
from __future__ import annotations

import ctypes
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

from ..config import config_dir

MAGIC = b"NCCGUI-DPAPI\x01"
SSH_FIELDS = ("username", "password", "ssh_key", "allow_agent", "look_for_keys")
VIEW_FIELDS = {"source", "defaults", "state", "wrap_xml", "connection_hidden", "auto_reconnect"}


class PreferencesError(ValueError):
    pass


def _dpapi(data: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        raise PreferencesError("GUI credential storage requires Windows DPAPI; no plaintext fallback is allowed.")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    source_buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                          ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    # Current-user scope; never CRYPTPROTECT_LOCAL_MACHINE. Never show OS UI.
    if not operation(ctypes.byref(source), None if decrypt else "NETCONF GUI settings",
                     None, None, None, 0x1, ctypes.byref(output)):
        raise PreferencesError("Windows DPAPI failed (error %d)." % ctypes.get_last_error())
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def empty_book():
    return {"version": 1, "last": {}, "connections": {}, "accounts": {}}


def public_book(book):
    """Portable settings, without saved passwords. Key/cert files are not embedded."""
    result = deepcopy(validate_book(book))
    records = [result["last"].get("values", {})]
    records += list(result["connections"].values()) + list(result["accounts"].values())
    for record in records:
        record.pop("password", None)
    return result


def validate_book(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise PreferencesError("Unsupported GUI settings format; the original file was not overwritten.")
    if not all(isinstance(data.get(key), dict) for key in ("last", "connections", "accounts")):
        raise PreferencesError("Invalid GUI settings structure.")
    def values(record):
        return isinstance(record, dict) and all(isinstance(k, str) and type(v) in (str, bool)
                                                for k, v in record.items())
    last = data["last"]
    if last and (not values(last.get("values")) or not isinstance(last.get("connection", ""), str)
                 or not isinstance(last.get("account", ""), str)):
        raise PreferencesError("Invalid saved GUI fields.")
    for group in ("connections", "accounts"):
        if not all(isinstance(k, str) and values(v) for k, v in data[group].items()):
            raise PreferencesError("Invalid saved GUI profile.")
    return data


class PreferencesStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else config_dir() / "gui-settings.dpapi"

    def load(self):
        if not self.path.exists():
            return empty_book()
        try:
            raw = self.path.read_bytes()
            if not raw.startswith(MAGIC):
                raise PreferencesError("Not a supported encrypted GUI settings file.")
            return validate_book(json.loads(_dpapi(raw[len(MAGIC):], decrypt=True).decode("utf-8")))
        except Exception as exc:
            # Do not include decoded content or passwords in exceptions/logs.
            raise PreferencesError("Cannot open encrypted GUI settings (%s). Original file preserved." % type(exc).__name__) from None

    def save(self, data):
        validate_book(data)
        raw = MAGIC + _dpapi(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=self.path.parent, prefix=".gui-settings-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


def remember(group, values, label, explicit_name=""):
    """Deduplicate identical automatic history; named saves explicitly replace."""
    if explicit_name.strip():
        name = explicit_name.strip()
    else:
        for name, existing in group.items():
            if existing == values:
                return name
        name = label.strip() or "Profile"
        number = 2
        while name in group:
            name = "%s (%d)" % (label, number)
            number += 1
    group[name] = deepcopy(values)
    return name


def remember_account(book, values, name=""):
    account = {key: values[key] for key in SSH_FIELDS if key in values}
    if not account.get("username", "").strip():
        return ""
    return remember(book["accounts"], account, account["username"], name)


def remember_connection(book, values, name=""):
    # View-only changes must not create another connection-history entry.
    if not name.strip():
        comparable = {k: v for k, v in values.items() if k not in VIEW_FIELDS}
        for saved_name, saved in book["connections"].items():
            if {k: v for k, v in saved.items() if k not in VIEW_FIELDS} == comparable:
                book["connections"][saved_name] = deepcopy(values)
                return saved_name
    call_home = "Call Home" in values.get("mode", "")
    host = values.get("listen_host" if call_home else "host", "")
    port = values.get("listen_port" if call_home else "port", "")
    label = "%s | %s:%s" % (values.get("mode", "NETCONF"), host, port)
    if "SSH" in values.get("mode", "") and values.get("username"):
        label += " | " + values["username"]
    return remember(book["connections"], values, label, name)


def change_profiles(book, group, names, new_name=None):
    """Prepare an all-or-nothing catalog change without mutating the caller."""
    result = deepcopy(validate_book(book))
    if group not in ("connections", "accounts"):
        raise PreferencesError("不支援的設定組類型。")
    names = list(dict.fromkeys(names))
    if not names or any(name not in result[group] for name in names):
        raise PreferencesError("請重新選取現有的設定組。")
    selection = "connection" if group == "connections" else "account"
    if new_name is not None:
        new_name = new_name.strip()
        if len(names) != 1 or not new_name:
            raise PreferencesError("請選取一組並輸入非空白名稱。")
        old = names[0]
        if new_name != old and new_name in result[group]:
            raise PreferencesError("名稱已存在，請使用其他名稱；不會覆寫另一組設定。")
        result[group] = {new_name if key == old else key: value for key, value in result[group].items()}
        if result["last"].get(selection) == old:
            result["last"][selection] = new_name
    else:
        for name in names:
            del result[group][name]
        if result["last"].get(selection) in names:
            result["last"][selection] = ""
    return result
