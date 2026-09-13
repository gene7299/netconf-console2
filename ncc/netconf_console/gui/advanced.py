"""Operator safety, encrypted backups, jump settings and event management UI."""
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from lxml import etree
from ncclient.xml_ import to_xml

from ..config import config_dir
from . import backups, events, lifecycle, safety
from .client import ReadOptions
from .model import EditError, build_plan
from .admin import AdminFeatures


class ReasonTip:
    def __init__(self, app, widget):
        self.app, self.widget = app, widget
        self.window = None
        self.timer = None
        widget.bind("<Enter>", self.enter, add=True)
        widget.bind("<Leave>", self.hide, add=True)
        widget.bind("<ButtonPress>", self.hide, add=True)

    def enter(self, _event=None):
        self.hide()
        self.timer = self.widget.after(400, self.show)

    def show(self):
        self.timer = None
        reason = self.app.disabled_reasons.get(self.widget)
        if reason and self.widget.winfo_exists():
            self.window = tk.Toplevel(self.widget)
            self.window.overrideredirect(True)
            x = min(self.widget.winfo_rootx(), self.widget.winfo_screenwidth() - 380)
            y = min(self.widget.winfo_rooty() + self.widget.winfo_height(), self.widget.winfo_screenheight() - 90)
            self.window.geometry("+%d+%d" % (max(0, x), max(0, y)))
            tk.Label(self.window, text=reason, background="#fff3cd", foreground="#473a19",
                     padx=8, pady=6, wraplength=360).pack()

    def hide(self, _event=None):
        if self.timer:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window:
            self.window.destroy()
            self.window = None


class AdvancedFeatures(AdminFeatures):
    def _build_advanced(self, tools_menu):
        self.disabled_reasons = {}
        self.disabled_tips = [ReasonTip(self, w) for w in (
            self.connect_button, self.disconnect_button, self.read_all_button, self.schema_button,
            self.send_button, self.refresh_button, self.state_check, self.save_tree_button,
            self.subscribe_button, self.stop_subscription_button)]
        self.datastore_menu.add_separator()
        self.draft_index = self.datastore_menu.index("end") + 1
        self.datastore_menu.add_command(label="驗證 XML 草稿（test-only）…", command=self.validate_draft)
        self.confirm_index = self.datastore_menu.index("end") + 1
        self.datastore_menu.add_command(label="確認保留限時提交…", command=lambda: self.finish_confirmed(True))
        self.cancel_index = self.datastore_menu.index("end") + 1
        self.datastore_menu.add_command(label="取消限時提交／回復…", command=lambda: self.finish_confirmed(False))
        self.datastore_menu.add_separator()
        self.datastore_menu.add_command(label="查看功能停用原因…", command=self.show_availability)
        tools_menu.add_separator()
        tools_menu.add_command(label="建立加密設定備份…", command=self.create_backup)
        tools_menu.add_command(label="開啟備份／選擇性還原…", command=self.restore_backup)
        tools_menu.add_command(label="事件 streams／篩選／回放…", command=self.event_settings)
        tools_menu.add_command(label="告警表格…", command=self.show_alarms)
        tools_menu.add_command(label="系統 SSH／sysrepocfg 修改草稿…", command=self.open_sysrepo)
        self.confirm_status = tk.StringVar(self.root, "")
        ttk.Label(self.connection_divider, textvariable=self.confirm_status, foreground="#a65000",
                  wraplength=480).pack(side="right", padx=5)
        self.streams = {}
        self.event_filter_xml = ""
        self.event_start = ""
        self.event_stop = ""
        self.event_records = deque(maxlen=100)
        self.alarm_window = None
        self.alarm_filter = tk.StringVar(self.root, "全部")
        self.alarm_search = tk.StringVar(self.root, "")
        self.alarm_rows = {}
        self._build_jump_fields()
        self._build_admin()
        tools_menu.add_command(label="系統 Sysrepo 備份／還原…",
                               command=lambda: self.auth_tabs.select(self.system_backup_page))

    def _build_jump_fields(self):
        for name, value in {"jump_host": "", "jump_port": "22", "jump_username": "",
                "jump_password": "", "jump_key": "", "jump_passphrase": "", "jump_auth": "password",
                "jump_known_hosts": ""}.items():
            self.vars[name] = tk.StringVar(self.root, value)
        self.vars["jump_enabled"] = tk.BooleanVar(self.root, False)
        self.vars["jump_verify"] = tk.BooleanVar(self.root, True)
        page = ttk.Frame(self.auth_tabs, padding=(4, 2))
        self.auth_tabs.add(page, text="SSH跳板")
        self._field(page, "跳板 host", "jump_host", 0, 0, 22)
        self._field(page, "Port", "jump_port", 0, 2, 6)
        ttk.Label(page, text="認證方式").grid(row=0, column=4, sticky="e", padx=5)
        ttk.Combobox(page, textvariable=self.vars["jump_auth"], values=("password", "private-key", "agent", "auto"),
                     state="readonly", width=14).grid(row=0, column=5)
        ttk.Checkbutton(page, text="啟用（僅 Direct SSH）", variable=self.vars["jump_enabled"]).grid(row=0, column=6, padx=6)
        # Keep the two most commonly entered jump credentials on one row.
        self.jump_username_entry = self._field(page, "跳板帳號", "jump_username", 1, 0, 16)
        self.jump_password_entry = self._field(page, "跳板登入密碼", "jump_password", 1, 2, 22, True)
        ttk.Checkbutton(page, text="驗證跳板 host key", variable=self.vars["jump_verify"]).grid(row=1, column=5, columnspan=2, sticky="w")
        self._path_field(page, "跳板 Private key", "jump_key", 2, 0).configure(width=22)
        self._field(page, "私鑰密碼", "jump_passphrase", 2, 3, 16, True)
        self._path_field(page, "跳板 Known hosts", "jump_known_hosts", 2, 5).configure(width=22)

    def _pending(self):
        pending = getattr(self.client, "pending_commit", None)
        return pending if isinstance(pending, safety.PendingCommit) else None

    def _pending_close_allowed(self):
        return not self._pending() or messagebox.askyesno("限時提交尚未結束", "斷線不會立即回復 persistent confirmed commit。\n"
            "未確認的變更須等待 server 逾時回復；重新連線後請讀回確認。\n"
            "建議先使用「取消限時提交」。仍要關閉連線？", parent=self.root, default="no")

    def common_reason(self, draft=False):
        if self.demo or not self.client.connected:
            return "尚未連線（或目前為離線示範）"
        if self.busy:
            return "正在執行其他操作"
        if self.lifecycle_dialog:
            return "請先關閉設定操作預覽"
        if self._pending():
            return "限時提交尚未結束；請確認、取消或等待"
        if not self._rpc_allowed():
            return "事件訂閱中，server 不支援 interleave"
        if self.uncertain:
            return "資料或操作結果待確認，請先重新讀取"
        if not draft and self.dirty:
            return "尚有本機草稿，請先送出或還原"
        return ""

    def action_reason(self, operation):
        reason = self.common_reason(draft=operation == "draft")
        if reason:
            return reason
        if operation == "draft" and self._draft_guard():
            return self._draft_guard()
        if operation not in {"draft", "compare", "validate"} and self._related_drafts():
            return "尚有此設備的本機草稿；請先處理草稿清單"
        if operation == "draft":
            if not safety.has_cap(self.client, "validate:1.1"):
                return "server 不支援 :validate:1.1 的 test-only"
            if not self.plan or self.plan.rpc is None:
                return "尚無有效、可送出的 XML 草稿"
            return ""
        if not lifecycle.supported(self.client, operation):
            return "缺少 " + lifecycle.OPERATIONS[operation][3]
        return ""

    def _sync_advanced(self):
        self._sync_admin()
        for index, (operation, (title, *_)) in enumerate(lifecycle.OPERATIONS.items()):
            reason = self.action_reason(operation)
            self.datastore_menu.entryconfigure(index, state="disabled" if reason else "normal",
                label=title + "…" + ("（" + reason + "）" if reason else ""))
        reason = self.action_reason("draft")
        self.datastore_menu.entryconfigure(self.draft_index, state="disabled" if reason else "normal",
            label="驗證 XML 草稿（test-only）…" + ("（" + reason + "）" if reason else ""))
        pending = self._pending()
        same_session = bool(pending and self.client.connected and self.client.manager is pending.manager)
        can_confirm = same_session and not self.busy and pending.state == "pending" and time.monotonic() < pending.deadline
        self.datastore_menu.entryconfigure(self.confirm_index, state="normal" if can_confirm else "disabled")
        self.datastore_menu.entryconfigure(self.cancel_index, state="normal" if same_session and not self.busy else "disabled")
        if pending:
            for widget in (self.send_button, self.subscribe_button, self.source_box, self.format_button, self.revert_button):
                widget.configure(state="disabled")
            self.editor.text.configure(state="disabled")
        self.disabled_reasons.clear()
        generic = "正在執行其他操作" if self.busy else "尚未連線" if not self.client.connected else "目前操作狀態不允許"
        for tip in self.disabled_tips:
            widget = tip.widget
            if str(widget.cget("state")) == "disabled":
                self.disabled_reasons[widget] = generic
        if self.connect_button.cget("state") == "disabled" and self.client.connected:
            self.disabled_reasons[self.connect_button] = "已連線；請先中斷才能切換設備"
        if str(self.send_button.cget("state")) == "disabled":
            self.disabled_reasons[self.send_button] = self.common_reason(draft=True) or (
                "startup 編輯唯讀" if self.snapshot and self.snapshot.options.source == "startup" else
                "沒有有效的修改；請查看待送出 RPC 上方提示")
        if not self._rpc_allowed():
            for widget in (self.read_all_button, self.schema_button, self.refresh_button):
                self.disabled_reasons[widget] = "訂閱中且 server 不支援 interleave；停止訂閱需中斷連線"
        if self.snapshot is None:
            self.disabled_reasons[self.save_tree_button] = "尚未讀取 DATA TREE 快照"
        if self.vars["source"].get() != "running":
            self.disabled_reasons[self.state_check] = "config false 只可搭配 running 的 get"
        if self.client.connected and not self.demo and not any(":notification:" in c for c in self.client.capabilities):
            self.disabled_reasons[self.subscribe_button] = "server 未宣告 :notification: capability"

    def show_availability(self):
        lines = ["設定操作："]
        lines += [title + "：" + (self.action_reason(op) or "可使用") for op, (title, *_) in lifecycle.OPERATIONS.items()]
        lines += ["草稿 test-only：" + (self.action_reason("draft") or "可使用"), "", "按鈕停用原因："]
        lines += [str(w.cget("text")) + "：" + reason for w, reason in self.disabled_reasons.items()]
        self._text_window("功能狀態 / 停用原因", "\n".join(lines))

    def validate_draft(self):
        self._update_preview()
        reason = self.action_reason("draft")
        if reason:
            self.status.set(reason)
            return
        plan, selection, options = self.plan, self.selection, self.snapshot.options
        rpc = safety.draft_rpc(plan)
        self.preview.set(to_xml(rpc))
        self.output_tabs.select(self.preview)
        if not messagebox.askyesno("驗證 XML 草稿", "將送出預覽的 test-only RPC，由 server 驗證但不套用設定。\n"
                "驗證成功不代表稍後設定仍未改變，也不等於已送出修改。是否繼續？", parent=self.root, default="no"):
            self._update_preview()
            return
        self.last_sent_xml = to_xml(rpc)
        def done(reply):
            self.reply.set(reply)
            self.output_tabs.select(self.reply)
            self.status.set("test-only 驗證成功；XML 草稿保留，尚未套用或保存設定。")
        self._run("驗證 XML 草稿…", lambda: safety.test_draft(self.client, selection, plan, options, rpc), done)

    def finish_confirmed(self, confirm):
        pending = self._pending()
        if not pending or self.busy or not self.client.connected or self.client.manager is not pending.manager:
            return
        rpc = safety.pending_rpc(pending, confirm)
        question = "確認保留這次限時提交？不會保存 startup。" if confirm else "取消這次限時提交並要求 server 回復先前 running？"
        if not messagebox.askyesno("限時提交", question, parent=self.root, default="no"):
            return
        self.last_sent_xml = to_xml(rpc)
        self.preview.set(self.last_sent_xml)
        options = self.snapshot.options if self.snapshot else ReadOptions()
        def work():
            reply, warnings = safety.finish_pending(self.client, pending, confirm, rpc)
            snapshot = None
            try:
                snapshot = self.client.read(options)
            except Exception:
                warnings.append("讀回失敗，請重新連線／讀取確認。")
            return reply, warnings, snapshot
        def done(value):
            reply, warnings, snapshot = value
            if snapshot is not None:
                self._accept_snapshot(snapshot)
            self.reply.set(reply)
            self.output_tabs.select(self.reply)
            self.uncertain = bool(warnings)
            self.status.set(("已確認保留；未保存 startup。" if confirm else "取消 RPC 成功，請核對讀回設定。") + " ".join(warnings))
        self._run("確認限時提交…" if confirm else "取消限時提交…", work, done)

    def _poll_confirmed(self):
        pending = self._pending()
        if pending is None:
            self.confirm_status.set("")
            return
        # No automatic connection, write, confirmation or token reuse after a drop.
        self.reconnect_enabled = False
        self.reconnect_due = None
        remaining = max(0, int(pending.deadline - time.monotonic()))
        self.confirm_status.set("限時提交 %ds%s；設定操作 → 確認／取消" % (
            remaining, "（結果待確認）" if pending.state == "unknown" else ""))
        self._sync()
        if time.monotonic() >= pending.release_after and not self.busy:
            def finish():
                self.client.disconnect()
                self.client.pending_commit = None
            def done(_):
                self.uncertain = True
                self.status.set("限時提交等待已結束，已關閉自己的 session 釋放鎖；請重新連線讀回，不能只靠倒數推定回復結果。")
            self._run("結束限時提交等待…", finish, done)

    def create_backup(self):
        if self.busy or not self.client.connected or not self._rpc_allowed() or self._pending():
            self.status.set("請在已連線、閒置且可讀取 RPC 時建立備份。")
            return
        source = self.snapshot.options.source if self.snapshot else self.vars["source"].get()
        directory = config_dir() / "backups"
        filename = "netconf-%s-%s.nccbackup" % (source, datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        name = filedialog.asksaveasfilename(parent=self.root, title="建立加密設定版本（不含本機草稿；限原 Windows 帳號／電腦）",
            initialdir=str(directory if directory.exists() else directory.parent), initialfile=filename,
            defaultextension=".nccbackup", filetypes=[("Encrypted config snapshot", "*.nccbackup")])
        if not name:
            return
        device = self._audit_device()
        def work():
            snapshot = self.client.read(ReadOptions(source, True, False))
            return backups.save_backup(name, snapshot, device, self.client.schema)
        self._run("建立加密設定備份…", work,
            lambda created: self.status.set("已建立 %s 加密設定版本（%s）；只含帳號可讀取的設定，不含未送出草稿。" % (source, created)))

    def restore_backup(self):
        if self.busy or self._pending() or not self.selection or not self.snapshot or self.snapshot.options.source == "startup":
            self.status.set("請先讀取並選擇 running/candidate 的目標 subtree。")
            return
        if self.uncertain or not self._discard():
            return
        directory = config_dir() / "backups"
        name = filedialog.askopenfilename(parent=self.root, title="選擇加密設定版本（只還原勾選的項目）",
            initialdir=str(directory if directory.exists() else directory.parent), filetypes=[("Encrypted config snapshot", "*.nccbackup")])
        if name:
            self._run("開啟加密設定備份…", lambda: backups.load_backup(name), self._restore_dialog)

    def _restore_dialog(self, payload):
        from .app import XmlPane
        selection, snapshot = self.selection, self.snapshot
        try:
            choices = backups.restore_choices(selection, payload["xml"], self.client.schema, snapshot.options.source)
        except Exception as exc:
            self._error(exc)
            return
        window = tk.Toplevel(self.root)
        window.title("選擇性還原（只載入本機草稿）")
        window.geometry("1050x680")
        window.transient(self.root)
        window.grab_set()
        ttk.Label(window, text="備份：%s | %s | %s\n目標：%s；僅目前選取 subtree，不是整台設備還原。"
                  "請核對设备身分與 schema；新增／移除容器或 list instance 是一組原子項目。" % (
                      payload["device"], payload["created"], payload["source"], self._audit_device()), wraplength=1000).pack(fill="x", padx=8)
        entries = ttk.Treeview(window, show="tree", selectmode="browse", height=8)
        entries.pack(fill="x", padx=8, pady=4)
        checked = set()
        for index, choice in enumerate(choices):
            entries.insert("", "end", iid=str(index), text="☐ " + choice.label)
        pane = XmlPane(window, readonly=True)
        controls = ttk.Frame(window)
        controls.pack(fill="x", side="bottom", padx=8, pady=5)
        pane.pack(fill="both", expand=True, padx=8)
        def preview(_event=None):
            indexes = sorted(checked)
            lines = []
            for index in indexes:
                choice = choices[index]
                before = etree.tostring(choice.before, encoding="unicode", pretty_print=True) if choice.before is not None else "（不存在）"
                after = etree.tostring(choice.after, encoding="unicode", pretty_print=True) if choice.after is not None else "（移除）"
                lines += [choice.label, "原值：\n" + before, "備份值：\n" + after, ""]
            pane.set("\n".join(lines) or "點擊項目或按空白鍵勾選；預設不勾選任何項目。")
        def toggle(event):
            iid = entries.identify_row(event.y) if event.type == tk.EventType.ButtonPress else entries.focus()
            if iid:
                index = int(iid)
                checked.symmetric_difference_update({index})
                entries.item(iid, text=("☑ " if index in checked else "☐ ") + choices[index].label)
                preview()
        def select_all(enabled):
            checked.clear()
            if enabled:
                checked.update(range(len(choices)))
            for index, choice in enumerate(choices):
                entries.item(str(index), text=("☑ " if enabled else "☐ ") + choice.label)
            preview()
        def stage():
            if self.busy or self.snapshot is not snapshot or self.selection is not selection:
                return
            indexes = sorted(checked)
            if not indexes:
                return
            try:
                text = backups.apply_choices(selection, choices, indexes, self.client.schema, snapshot.options.source)
                plan = build_plan(selection, text, self.client.schema, snapshot.options.source)
                if not messagebox.askyesno("載入還原草稿", "%d 項變更，含 %d 項移除。只載入草稿，仍須另按送出。繼續？" % (
                        len(plan.changes), plan.removals), parent=window, default="no"):
                    return
                self.editor.set(text)
                self._update_preview()
                self.output_tabs.select(self.diff_pane)
                window.destroy()
                self.status.set("選定的還原項目已載入草稿；請檢查差異／test-only，再自行送出。")
            except Exception as exc:
                messagebox.showerror("還原草稿", str(exc), parent=window)
        ttk.Button(controls, text="全選", command=lambda: select_all(True)).pack(side="left")
        ttk.Button(controls, text="清除選取", command=lambda: select_all(False)).pack(side="left")
        ttk.Button(controls, text="載入選定項目到草稿", command=stage).pack(side="right")
        ttk.Button(controls, text="取消", command=window.destroy).pack(side="right", padx=5)
        entries.bind("<Button-1>", toggle)
        entries.bind("<space>", toggle)
        preview()

    def event_settings(self):
        from .app import XmlPane
        window = tk.Toplevel(self.root)
        window.title("事件訂閱設定 / streams / replay")
        window.geometry("950x600")
        stream = tk.StringVar(window, self.stream.get())
        start, stop = tk.StringVar(window, self.event_start), tk.StringVar(window, self.event_stop)
        top = ttk.Frame(window)
        top.pack(fill="x")
        ttk.Label(top, text="Stream").grid(row=0, column=0)
        combo = ttk.Combobox(top, textvariable=stream, values=tuple(self.streams), width=35)
        combo.grid(row=0, column=1, sticky="ew")
        detail = tk.StringVar(window, "可更新設備 streams；時間留白即即時訂閱。")
        def selected(_event=None):
            info = self.streams.get(stream.get())
            detail.set(("Replay: %s | 最早: %s | %s" % (info.replay, info.earliest, info.description)) if info else "Stream 尚未查到；回放前需先確認 replaySupport。")
        def refresh():
            if self.busy or not self.client.connected or self.demo or not self._rpc_allowed() or self._pending():
                detail.set("目前無法讀取 streams；請連線且停止不支援 interleave 的訂閱。")
                return
            def done(values):
                self.streams = values
                self.stream_box.configure(values=tuple(values))
                if window.winfo_exists():
                    combo.configure(values=tuple(values))
                    selected()
            self._run("讀取事件 streams…", lambda: events.discover_streams(self.client.manager), done)
        ttk.Button(top, text="更新 streams", command=refresh).grid(row=0, column=2, padx=5)
        ttk.Label(top, text="startTime").grid(row=1, column=0)
        ttk.Entry(top, textvariable=start, width=40).grid(row=1, column=1, sticky="ew")
        ttk.Label(top, text="stopTime").grid(row=2, column=0)
        ttk.Entry(top, textvariable=stop, width=40).grid(row=2, column=1, sticky="ew")
        ttk.Label(top, text="例：2026-09-09T08:00:00+08:00\nstopTime 必須搭配 startTime").grid(row=1, column=2, rowspan=2)
        ttk.Label(window, textvariable=detail, wraplength=920).pack(anchor="w", padx=8, pady=5)
        ttk.Label(window, text="Subtree filter XML（選用；填通知 payload 的根節點與 namespace，不要包 filter／rpc）").pack(anchor="w", padx=8)
        pane = XmlPane(window)
        pane.set(self.event_filter_xml)
        controls = ttk.Frame(window)
        controls.pack(side="bottom", fill="x")
        pane.pack(fill="both", expand=True, padx=8, pady=5)
        def save():
            try:
                events.subscription_options(stream.get(), self.streams, pane.get(), start.get(), stop.get())
                self.stream.set(stream.get())
                self.event_filter_xml, self.event_start, self.event_stop = pane.get(), start.get(), stop.get()
                window.destroy()
                self.status.set("訂閱條件已設定；按「開始訂閱」才送出，不會更改現有訂閱。")
            except Exception as exc:
                messagebox.showerror("訂閱條件", str(exc), parent=window)
        ttk.Button(controls, text="保存條件（不送出）", command=save).pack(side="right", padx=8, pady=5)
        combo.bind("<<ComboboxSelected>>", selected)
        selected()
        refresh()

    def show_alarms(self):
        from .app import XmlPane
        if self.alarm_window is not None and self.alarm_window.winfo_exists():
            self.alarm_window.lift()
            return
        window = self.alarm_window = tk.Toplevel(self.root)
        window.title("NETCONF 告警表格（最近 100 筆；此處篩選不影響 server 訂閱）")
        window.geometry("1100x700")
        bar = ttk.Frame(window)
        bar.pack(fill="x")
        severity = ttk.Combobox(bar, textvariable=self.alarm_filter,
            values=("全部", "critical", "major", "minor", "warning", "indeterminate", "cleared", "unknown"), width=18)
        severity.pack(side="left")
        search = ttk.Entry(bar, textvariable=self.alarm_search, width=35)
        search.pack(side="left", padx=8)
        ttk.Button(bar, text="篩選", command=self._refresh_alarms).pack(side="left")
        self.alarm_tree = ttk.Treeview(window, columns=("time", "severity", "source", "event"), show="headings", height=10)
        for name, title in (("time", "時間"), ("severity", "嚴重度"), ("source", "來源"), ("event", "事件")):
            self.alarm_tree.heading(name, text=title)
            self.alarm_tree.column(name, width=250 if name == "time" else 150)
        self.alarm_tree.pack(fill="x", padx=8)
        self.alarm_tree.tag_configure("critical", background="#ffd3d3")
        self.alarm_tree.tag_configure("major", background="#ffe0bb")
        self.alarm_tree.tag_configure("minor", background="#fff4bd")
        self.alarm_detail = XmlPane(window, readonly=True)
        self.alarm_detail.pack(fill="both", expand=True, padx=8, pady=6)
        def show(_event=None):
            chosen = self.alarm_tree.selection()
            if chosen and chosen[0] in self.alarm_rows:
                self.alarm_detail.set(self.alarm_rows[chosen[0]].xml)
        self.alarm_tree.bind("<<TreeviewSelect>>", show)
        severity.bind("<<ComboboxSelected>>", lambda _e: self._refresh_alarms())
        search.bind("<Return>", lambda _e: self._refresh_alarms())
        self._refresh_alarms()

    def _record_notification(self, text):
        record = events.event_record(text)
        self.event_records.append(record)
        if record.completed:
            self.notification_manager = None
            self.notification_status.set("Server 已送出 notificationComplete，訂閱結束；可重新訂閱。")
        self._refresh_alarms()

    def _refresh_alarms(self):
        if self.alarm_window is None or not self.alarm_window.winfo_exists():
            return
        self.alarm_tree.delete(*self.alarm_tree.get_children())
        self.alarm_rows.clear()
        query, severity = self.alarm_search.get().casefold(), self.alarm_filter.get().lower()
        for index, record in enumerate(self.event_records):
            if severity != "全部" and record.severity != severity:
                continue
            if query not in (record.time + " " + record.source + " " + record.event + " " + record.xml).casefold():
                continue
            iid = str(index)
            self.alarm_rows[iid] = record
            self.alarm_tree.insert("", "end", iid=iid, values=(record.time, record.severity, record.source, record.event), tags=(record.severity,))
