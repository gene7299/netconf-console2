"""Bounded operation metadata; deliberately never persists XML or credentials."""
from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from ..config import config_dir


class AuditLog:
    FIELDS = ("time", "device", "operation", "result")

    def __init__(self, persist=False, path=None):
        self.path = Path(path) if path else config_dir() / "gui-operations.json"
        self.persist = persist
        self.entries = deque(maxlen=500)
        self.error = ""
        if persist and self.path.exists():
            try:
                if self.path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("oversized")
                records = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(records, list):
                    raise ValueError("invalid records")
                for record in records[-500:]:
                    if not isinstance(record, dict) or set(record) != set(self.FIELDS):
                        raise ValueError("invalid record")
                    self.entries.append({k: str(record[k])[:400] for k in self.FIELDS})
            except Exception:
                self.persist = False
                self.error = "歷史紀錄無法讀取；原檔保留，本次只記錄於記憶體。"

    def add(self, device, operation, result):
        self.entries.append(dict(zip(self.FIELDS, (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            str(device)[:400], str(operation)[:400], str(result)[:400]))))
        if self.persist:
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 prefix=".gui-audit-", delete=False) as out:
                    temporary = Path(out.name)
                    out.write(self.json())
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, self.path)
            except Exception:
                self.error = "操作紀錄無法儲存；設備操作結果不受影響。"
            finally:
                if temporary is not None and temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        pass  # Audit cleanup must not change the device operation's result.

    def json(self):
        return json.dumps(list(self.entries), ensure_ascii=False, indent=2)

    def text(self):
        return "\n".join(" | ".join(record[k] for k in self.FIELDS) for record in self.entries)
