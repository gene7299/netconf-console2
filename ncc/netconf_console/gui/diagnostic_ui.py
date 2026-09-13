"""One-click temporary-session diagnostics, explicit start and redacted export."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .connection_diagnostics import DiagnosticRun, report_text


class DiagnosticFeatures:
    def _build_diagnostic_ui(self, tools):
        self.diagnostic_dialog = None
        tools.add_command(label="一鍵連線診斷／遮蔽問題報告…", command=self.open_connection_diagnostics)

    def open_connection_diagnostics(self):
        if self.busy or self.lifecycle_dialog or self._pending():
            return
        if self.client.connected or self.reconnect_enabled:
            self.status.set("診斷會建立臨時連線；請先中斷 NETCONF 並停止自動重連，避免占用 Call Home port。")
            return
        if not self._preserve_current_draft(flush=True):
            return
        try:
            settings = self.settings()
            self.diagnostic_dialog = DiagnosticDialog(self, settings)
            return self.diagnostic_dialog
        except Exception as exc:
            self._error(exc)


class DiagnosticDialog:
    def __init__(self, app, settings):
        from .app import XmlPane
        self.app, self.settings = app, settings
        self.report, self.running = None, False
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.timer = None
        self.window = tk.Toplevel(app.root)
        app.lifecycle_dialog = self.window
        self.window.title("連線診斷 — 臨時連線／不修改設備")
        self.window.geometry("1080x720")
        self.window.transient(app.root)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        ttk.Label(self.window, text="按開始才連線；連線 timeout 上限 60 秒，單一 RPC timeout 上限 30 秒（非總時限）。\n"
            "診斷使用上排欄位建立臨時 NETCONF session，讀取 schema 後關閉；不套用草稿、不改驗證設定。",
            wraplength=1020).pack(fill="x", padx=10, pady=8)
        self.tree = ttk.Treeview(self.window, columns=("status", "seconds", "detail"), height=7)
        self.tree.heading("#0", text="階段")
        self.tree.column("#0", width=230)
        for key, title, width in (("status", "結果", 85), ("seconds", "耗時（秒）", 85), ("detail", "本機詳細資料／接入來源", 560)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width)
        self.tree.pack(fill="x", padx=10)
        self.status = tk.StringVar(self.window, "尚未開始；下方是匯出前的遮蔽報告預覽。")
        ttk.Label(self.window, textvariable=self.status, wraplength=1020).pack(fill="x", padx=10, pady=6)
        self.preview = XmlPane(self.window, readonly=True)
        self.preview.pack(fill="both", expand=True, padx=10)
        controls = ttk.Frame(self.window)
        controls.pack(fill="x", padx=10, pady=8)
        self.start_button = ttk.Button(controls, text="開始診斷", command=self.start)
        self.start_button.pack(side="left")
        ttk.Button(controls, text="取消診斷", command=self.cancel.set).pack(side="left", padx=5)
        self.export_button = ttk.Button(controls, text="匯出遮蔽後報告 JSON", command=self.export, state="disabled")
        self.export_button.pack(side="left")
        ttk.Button(controls, text="關閉", command=self.close).pack(side="right")
        app._sync()

    def start(self):
        if self.running:
            return
        self.running = True
        self.cancel.clear()
        self.tree.delete(*self.tree.get_children())
        self.report = None
        self.preview.set("")
        self.start_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        diagnostic = DiagnosticRun(self.settings, self.cancel, lambda row: self.events.put(("row", row)))
        def work():
            self.events.put(("done", diagnostic.run()))
        threading.Thread(target=work, daemon=True, name="netconf-connection-diagnostic").start()
        self.poll()

    def poll(self):
        self.timer = None
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "done":
                    self.report, self.running = value, False
                    self.preview.set(report_text(value))
                    self.start_button.configure(state="normal")
                    self.export_button.configure(state="normal")
                    self.status.set("診斷通過（不代表具有寫入權限）" if value["passed"] else "診斷未通過；請查看失敗階段。未修改設備。")
                else:
                    iid = value["stage"]
                    values = (value["status"], value["seconds"] if value["seconds"] is not None else "—", value["detail"])
                    if self.tree.exists(iid):
                        self.tree.item(iid, values=values)
                    else:
                        self.tree.insert("", "end", iid=iid, text=iid, values=values)
        except queue.Empty:
            pass
        if self.running:
            self.timer = self.window.after(80, self.poll)

    def export(self):
        if self.report is None or self.running:
            return
        name = filedialog.asksaveasfilename(parent=self.window, defaultextension=".json", initialfile="netconf-diagnostic-redacted.json")
        if name:
            try:
                Path(name).write_text(report_text(self.report), encoding="utf-8")
                self.status.set("已匯出預覽中的遮蔽報告；不含帳密、憑證／私鑰內容、XML 設定。")
            except Exception as exc:
                self.status.set("匯出失敗：" + type(exc).__name__)

    def close(self):
        if self.running:
            self.cancel.set()
            self.status.set("正在取消；監聽可立即取消，進行中的握手／RPC 會等目前逾時後結束。")
            return
        if self.timer:
            self.window.after_cancel(self.timer)
        self.app.lifecycle_dialog = self.app.diagnostic_dialog = None
        self.window.grab_release()
        self.window.destroy()
        self.app._sync()
