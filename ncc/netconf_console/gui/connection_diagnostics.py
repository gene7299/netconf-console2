"""Read-only, temporary NETCONF sessions with structured, redacted diagnostics."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import time

from ..session import ConsoleContext, open_direct, open_call_home
from . import VERSION
from .client import GuiClient

LABELS = {"listen": "Call Home 監聽", "jump": "SSH 跳板／轉送", "tcp": "TCP 連線／接入",
          "handshake": "SSH 握手／主機身分", "auth": "SSH 認證／TLS 憑證握手",
          "hello": "NETCONF subsystem／hello", "schema": "YANG schema 載入"}


class DiagnosticRun:
    def __init__(self, settings, cancel, progress=lambda _row: None, require_fixture=False):
        self.settings = settings.copy(raw_file=None, timeout=min(max(settings.timeout or 30, 1), 60),
                                      rpc_timeout=min(max(settings.rpc_timeout or 15, 1), 30))
        self.cancel, self.progress = cancel, progress
        self.require_fixture = require_fixture
        self.rows = []
        self.started = {}
        self.active = None
        self.report = {"version": 1, "gui_version": VERSION, "created": datetime.now(timezone.utc).isoformat(),
            "mode": ("Call Home " if settings.call_home else "Direct ") + settings.transport.upper(),
            "endpoint": {"host": settings.listen_host if settings.call_home else settings.host,
                         "port": settings.listen_port if settings.call_home else settings.port,
                         "username": settings.username, "jump_host": settings.jump_host if settings.jump_enabled else None},
            "verification": {"ssh_host_key": settings.hostkey_verify, "tls_name": settings.verify_hostname,
                             "tls_ca_required": settings.transport == "tls"},
            "stages": self.rows, "passed": False, "scope": "臨時連線／唯讀 schema；未讀取 running 設定或送出修改"}

    def phase(self, name, state, detail=""):
        now = time.monotonic()
        if state == "start":
            if self.cancel.is_set():
                raise InterruptedError("Diagnostic cancelled")
            self.active = name
            self.started[name] = now
            self.progress({"stage": LABELS.get(name, name), "status": "執行中", "seconds": None, "detail": detail})
            return
        row = {"stage": LABELS.get(name, name), "status": "通過", "seconds": round(now - self.started[name], 3) if name in self.started else None,
               "detail": detail}
        self.rows.append(row)
        self.progress(deepcopy(row))
        self.active = None

    def run(self):
        client = GuiClient()
        client.cancel = self.cancel
        settings = self.settings
        try:
            if self.cancel.is_set():
                raise InterruptedError()
            client.context = ConsoleContext(settings)
            if settings.call_home:
                manager, metadata = open_call_home(settings, cancel_event=self.cancel, phase=self.phase)
            else:
                manager, metadata = open_direct(settings, phase=self.phase)
            client.context.manager, client.context.metadata = manager, metadata
            if self.require_fixture and "urn:netconf-console2:gui-test-peer:1.0" not in client.capabilities:
                raise ValueError("Not the synthetic diagnostic peer")
            if self.cancel.is_set():
                raise InterruptedError()
            self.phase("schema", "start")
            schema = client.load_schema()
            self.report["schema"] = {"modules": len(schema.modules), "nodes": len(schema.nodes),
                                      "complete": schema.complete, "warning_count": len(schema.warnings)}
            if not schema.complete:
                raise ValueError("Incomplete schema")
            self.phase("schema", "done", "%d modules / %d nodes" % (len(schema.modules), len(schema.nodes)))
            self.report["capabilities"] = {name: any(":" + name + ":" in cap for cap in client.capabilities)
                for name in ("candidate", "startup", "writable-running", "validate", "rollback-on-error", "notification", "with-defaults")}
            self.report["passed"] = True
        except Exception as exc:
            active = self.active or "連線準備／結束"
            causes, current = [], exc
            while current is not None and len(causes) < 5:
                causes.append(type(current).__name__)
                current = current.__cause__
            hints = {"tcp": "確認路由、防火牆與 port；Call Home 須由 RU 主動連回。",
                "listen": "檢查監聽位址及 port 是否已被另一個 GUI／服務占用。",
                "handshake": "檢查 SSH 協定、主機金鑰與 Known hosts。",
                "auth": "檢查認證方式、帳密／私鑰、CA、憑證有效期與 SAN；本報告不含憑證或密碼內容。",
                "hello": "TCP／認證可能已通過；確認 NETCONF subsystem 與 hello 協定版本。",
                "schema": "確認 get-schema／YANG library 權限、依賴 module 與 feature；不代表 running 可寫。",
                "jump": "確認跳板帳密、host key、轉送權限及跳板到 RU 的路由。"}
            row = {"stage": LABELS.get(active, active), "status": "取消" if self.cancel.is_set() else "失敗",
                "seconds": round(time.monotonic() - self.started[active], 3) if active in self.started else None,
                "detail": hints.get(active, "請檢查連線欄位；未修改設備。"), "error_types": causes}
            self.rows.append(row)
            self.progress(deepcopy(row))
        finally:
            try:
                client.disconnect()
            except Exception:
                self.report["cleanup_warning"] = "臨時 session 關閉異常；未重送任何操作。"
        return deepcopy(self.report)


def report_text(report):
    """No raw errors, XML, credentials, certificate contents or capability URLs."""
    safe = deepcopy(report)
    safe["endpoint"] = {"host": "[已遮蔽]", "port": report["endpoint"]["port"],
                        "username": "[已遮蔽]", "jump_host": "[已遮蔽]"}
    for row in safe["stages"]:
        if row["status"] == "通過" and row["stage"] in {LABELS["listen"], LABELS["tcp"]}:
            row["detail"] = "[位址已遮蔽]"
    return json.dumps(safe, ensure_ascii=False, indent=2)


def loopback_diagnostic(filename):
    from pathlib import Path
    import threading
    from ..session import ConnectionSettings
    settings = ConnectionSettings(**json.loads(Path(filename).read_text(encoding="utf-8")))
    endpoint = settings.listen_host if settings.call_home else settings.host
    if endpoint not in {"127.0.0.1", "::1"} or settings.jump_enabled or not 0 < settings.timeout <= 30:
        raise ValueError("Diagnostic tests require a direct numeric-loopback synthetic peer")
    report = DiagnosticRun(settings, threading.Event(), require_fixture=True).run()
    return json.loads(report_text(report))
