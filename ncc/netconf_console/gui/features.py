"""GUI workspace tools, kept separate from connection and tree event handling."""
from collections import deque
from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..trace import redact_secrets
from ..xmloutput import serialize_xml
from .audit import AuditLog
from . import lifecycle, events
from .advanced import AdvancedFeatures
from .creation_ui import CreationFeatures
from .model import EditError, build_plan, identity
from .workspace import import_selection, instance_path, search_snapshot, value_changes, xml_diff


class WorkspaceFeatures(CreationFeatures, AdvancedFeatures):
    def _build_features(self):
        from .app import XmlPane
        self.audit = AuditLog(persist=self.preferences_store is not None,
            path=self.preferences_store.path.parent / "gui-operations.json" if self.preferences_store is not None else None)
        self.notification_manager = None
        self.notifications = deque(maxlen=100)
        self.lifecycle_dialog = None
        self.diff_pane = XmlPane(self.output_tabs, readonly=True)
        self.diff_pane.gutter.configure(height=1)
        self.output_tabs.add(self.diff_pane, text="修改差異")
        log_frame = ttk.Frame(self.output_tabs)
        self.output_tabs.add(log_frame, text="操作紀錄")
        ttk.Button(log_frame, text="匯出紀錄 JSON（不含 XML／密碼）", command=self.export_audit).pack(anchor="w")
        self.audit_pane = XmlPane(log_frame, readonly=True)
        self.audit_pane.text.configure(height=4)
        self.audit_pane.gutter.configure(height=1)
        self.audit_pane.pack(fill="both", expand=True)
        self.audit_pane.set(self.audit.text() + ("\n" + self.audit.error if self.audit.error else ""))
        event_frame = ttk.Frame(self.output_tabs)
        self.output_tabs.add(event_frame, text="事件通知")
        self.event_frame = event_frame
        bar = ttk.Frame(event_frame)
        bar.pack(fill="x")
        ttk.Label(bar, text="Stream").pack(side="left")
        self.stream = tk.StringVar(self.root, "NETCONF")
        self.stream_box = ttk.Combobox(bar, textvariable=self.stream, values=("NETCONF",), width=18)
        self.stream_box.pack(side="left", padx=5)
        self.subscribe_button = ttk.Button(bar, text="開始訂閱", command=self.subscribe)
        self.subscribe_button.pack(side="left")
        self.stop_subscription_button = ttk.Button(bar, text="停止（中斷連線）", command=self.stop_subscription)
        self.stop_subscription_button.pack(side="left", padx=4)
        ttk.Button(bar, text="匯出通知…", command=self.export_notifications).pack(side="left")
        self.notification_status = tk.StringVar(self.root, "未訂閱；使用目前 session，斷線後不會自動重新訂閱。")
        ttk.Label(event_frame, textvariable=self.notification_status, wraplength=750).pack(anchor="w")
        self.notification_pane = XmlPane(event_frame, readonly=True)
        self.notification_pane.text.configure(height=3)
        self.notification_pane.gutter.configure(height=1)
        self.notification_pane.pack(fill="both", expand=True)
        menu = tk.Menu(self.root)
        self.root.configure(menu=menu)
        self.datastore_menu = tk.Menu(menu, tearoff=False)
        menu.add_cascade(label="設定操作", menu=self.datastore_menu)
        for operation, (label, *_rest) in lifecycle.OPERATIONS.items():
            self.datastore_menu.add_command(label=label + "…", command=lambda op=operation: self.datastore_action(op))
        tools = tk.Menu(menu, tearoff=False)
        menu.add_cascade(label="工具", menu=tools)
        tools.add_command(label="匯入 XML 到選取節點…", command=self.import_xml)
        tools.add_command(label="搜尋 DATA TREE / 路徑…", command=self.search_tree)
        tools.add_command(label="搜尋編輯區 XML…", command=self.search_xml)
        tools.add_command(label="所選節點 YANG 說明…", command=self.show_node_info)
        self.editor.text.bind("<Control-f>", lambda _e: (self.search_xml(), "break")[-1])
        self.tree.bind("<Control-f>", lambda _e: (self.search_tree(), "break")[-1])
        self.tree.bind("<F1>", lambda _e: self.show_node_info())
        self._build_advanced(tools)
        self._build_creation(tools)

    def _rpc_allowed(self):
        if self.notification_manager is None:
            return True
        if not self.client.connected or self.client.manager is not self.notification_manager:
            return False
        return any(":interleave:" in c for c in self.client.capabilities)

    def _sync_features(self):
        online = self.client.connected and not self.demo
        idle = not self.busy and self.lifecycle_dialog is None
        allowed = online and self._rpc_allowed()
        for index, operation in enumerate(lifecycle.OPERATIONS):
            enabled = allowed and idle and not self.uncertain and not self.dirty and lifecycle.supported(self.client, operation)
            self.datastore_menu.entryconfigure(index, state="normal" if enabled else "disabled")
        self.subscribe_button.configure(state="normal" if online and idle and not self.notification_manager
            and any(":notification:" in c for c in self.client.capabilities) else "disabled")
        self.stop_subscription_button.configure(state="normal" if self.notification_manager and idle else "disabled")
        if online and not self._rpc_allowed():
            for widget in (self.read_all_button, self.schema_button, self.refresh_button,
                           self.send_button, self.source_box, self.defaults_check, self.state_check):
                widget.configure(state="disabled")
        self._sync_advanced()
        self._sync_creation()

    def _audit_device(self):
        context = getattr(self.client, "context", None)
        metadata = getattr(context, "metadata", None)
        if metadata:
            return "%s %s %s" % (metadata.mode, metadata.transport,
                metadata.peer_address or "%s:%s" % (metadata.remote_host, metadata.remote_port))
        settings = self.reconnect_settings
        if settings:
            return "%s %s:%s" % (settings.transport, settings.listen_host if settings.call_home else settings.host,
                                   settings.listen_port if settings.call_home else settings.port)
        return "DEMO" if self.demo else "Offline"

    def _audit_result(self, operation, result, device=None):
        self.audit.add(device or self._audit_device(), operation, result)
        self.audit_pane.set(self.audit.text() + ("\n" + self.audit.error if self.audit.error else ""))

    def export_audit(self):
        name = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", initialfile="netconf-operations.json")
        if name:
            try:
                if Path(name).resolve() == self.audit.path.resolve():
                    raise EditError("請另選匯出位置，不能覆寫使用中的歷史紀錄。")
                Path(name).write_text(self.audit.json(), encoding="utf-8")
            except Exception as exc:
                self._error(exc)

    def _update_diff(self):
        if not self.selection or not self.plan:
            self.diff_pane.set("")
            return
        changes = value_changes(self.selection, self.plan.edited, self.client.schema)
        lines = ["Target: " + self.snapshot.options.source, instance_path(self.selection, self.client.schema), ""]
        for item in changes:
            lines += [item.path, "  原值: " + item.before, "  新值: " + item.after]
        # Values are intentionally visible to the local operator, never to the audit log.
        self.diff_pane.set("\n".join(lines) + "\n\n" + xml_diff(self.selection.node, self.plan.edited))

    def import_xml(self):
        if self._pending():
            self.status.set("限時提交尚未結束，暫停匯入草稿。")
            return
        if self.busy or not self.selection or not self.snapshot:
            self.status.set("請先讀取並選擇要匯入的節點。")
            return
        if self.snapshot.options.source == "startup" or self.uncertain:
            self._error(EditError("請先重新讀取 running/candidate，再匯入。"))
            return
        if not self._discard():
            return
        name = filedialog.askopenfilename(parent=self.root, title="匯入 XML（只更新選取節點的本機草稿）", filetypes=[("XML", "*.xml")])
        if not name:
            return
        try:
            if Path(name).stat().st_size > 32 * 1024 * 1024:
                raise EditError("XML 匯入上限為 32 MiB。")
            imported = import_selection(Path(name).read_bytes(), self.selection, self.client.schema)
            text = serialize_xml(imported).decode("utf-8")
            plan = build_plan(self.selection, text, self.client.schema, self.snapshot.options.source)
            if not messagebox.askyesno("確認載入 XML 草稿", "只處理目前選取的 subtree，檔案中其他根節點不會套用。\n"
                    "選取範圍內省略的可寫節點會視為刪除；config false 使用目前讀取值。\n"
                    "%d 項變更（含 %d 項移除）。只載入草稿，不會送出。\n是否繼續？" % (len(plan.changes), plan.removals),
                    parent=self.root, default="no"):
                return
            self.editor.set(text)
            self._update_preview()
            self.output_tabs.select(self.diff_pane)
            self.status.set("XML 已載入本機草稿；請檢查差異及 RPC，再按送出。YANG 完整語意仍由 server 驗證。")
        except Exception as exc:
            self._error(exc)

    def show_node_info(self):
        if not self.selection:
            return
        info = self.client.schema.lookup(self.selection.path)
        lines = [instance_path(self.selection, self.client.schema), ""]
        if info:
            lines += ["Module: " + info.module, "Kind: " + info.kind, "config: " + str(info.config),
                      "Type: " + info.type_name, "Units: " + info.units, "Default: " + repr(info.defaults),
                      "\nConstraints (含 typedef；完整語意以 server 驗證為準):", info.constraints,
                      "\nDescription:", info.description or "（未提供）"]
        else:
            lines.append("Schema 未知，唯讀。")
        self._text_window("YANG 欄位說明", "\n".join(lines))

    def _text_window(self, title, text):
        from .app import XmlPane
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("950x600")
        pane = XmlPane(window, readonly=True)
        pane.pack(fill="both", expand=True)
        pane.set(text)
        return window

    def search_xml(self):
        from tkinter import simpledialog
        query = simpledialog.askstring("搜尋 XML", "輸入編輯區要搜尋的文字（不分大小寫）：", parent=self.root)
        if not query:
            return
        text = self.editor.text
        text.tag_remove("search", "1.0", "end")
        text.tag_configure("search", background="#a9e7ba", foreground="#112b19")
        start, first, count = "1.0", None, 0
        while count < 5000:
            pos = text.search(query, start, stopindex="end", nocase=True)
            if not pos:
                break
            end = "%s+%dc" % (pos, len(query))
            text.tag_add("search", pos, end)
            first = first or pos
            start, count = end, count + 1
        text.tag_raise("search")
        if first:
            text.see(first)
        self.status.set("XML 搜尋：%d 筆（最多標示 5000 筆）" % count)

    def search_tree(self):
        if self.busy or self.snapshot is None:
            return
        window = tk.Toplevel(self.root)
        window.title("搜尋 DATA TREE（目前快照；名稱、module 路徑、值、description）")
        window.geometry("1000x550")
        window.transient(self.root)
        window.grab_set()
        query = tk.StringVar(window)
        bar = ttk.Frame(window)
        bar.pack(fill="x")
        entry = ttk.Entry(bar, textvariable=query)
        entry.pack(side="left", fill="x", expand=True)
        results = tk.Listbox(window, font=("Consolas", 10))
        results.pack(fill="both", expand=True)
        found = []
        snapshot = self.snapshot
        def search():
            found[:] = search_snapshot(snapshot.data, self.client.schema, query.get())
            results.delete(0, "end")
            for label, _selection in found:
                results.insert("end", label)
        def jump(_event=None):
            chosen = results.curselection()
            if not chosen or self.busy or self.snapshot is not snapshot or not self._discard():
                return
            selection = found[chosen[0]][1]
            parent = ""
            for depth, node in enumerate((*selection.ancestors, selection.node), 1):
                matches = [iid for iid in self.tree.get_children(parent) if iid in self.items and
                    identity(self.items[iid].node, self.client.schema, self.items[iid].path)
                    == identity(node, self.client.schema, selection.path[:depth])]
                if len(matches) != 1:
                    return
                parent = matches[0]
                if depth < len(selection.path):
                    self._expand_item(parent)
            self._show_selection(parent)
            self.tree.selection_set(parent)
            self.tree.focus(parent)
            self.tree.see(parent)
            window.destroy()
            self.status.set("已跳到搜尋結果（目前快照）；需要最新值請按重新讀取。")
        ttk.Button(bar, text="搜尋（最多 500 筆）", command=search).pack(side="left")
        ttk.Button(bar, text="跳到所選節點", command=jump).pack(side="left")
        entry.bind("<Return>", lambda _e: search())
        results.bind("<Double-Button-1>", jump)
        entry.focus_set()

    def datastore_action(self, operation):
        reason = self.action_reason(operation)
        if reason:
            self.status.set(reason)
            return
        if self.busy or self.lifecycle_dialog or not self.client.connected or not self._rpc_allowed():
            return
        if self.dirty or self.uncertain:
            self._error(EditError("請先送出／還原本機草稿，並重新讀取確認設備狀態。"))
            return
        source = self.snapshot.options.source if self.snapshot else self.vars["source"].get()
        confirm_timeout = 120
        if operation == "confirmed":
            from tkinter import simpledialog
            confirm_timeout = simpledialog.askinteger("限時提交", "幾秒內必須確認保留？（30–600 秒）\n"
                "逾時由 server 回復；使用 persist token，斷線仍須等待逾時，不會立刻回復。",
                initialvalue=120, minvalue=30, maxvalue=600, parent=self.root)
            if confirm_timeout is None:
                return
        def preview(prepared):
            title = lifecycle.OPERATIONS[operation][0]
            if operation == "compare":
                self._text_window(title, "唯讀比較（不同權限／default 表示法也可能造成差異）\n\n" + prepared.diff)
                self.status.set("running / startup 比較完成；沒有修改設備。")
                return
            extra = ("限時提交：%d 秒內需從設定操作選單確認保留或取消；不會自動確認。\n"
                     "期間保留 running/candidate 鎖；斷線不會立即回復，須等待 server 逾時。\n" % confirm_timeout
                     if operation == "confirmed" else "")
            window = self._text_window(title, extra + "整份 datastore 操作，可能包含其他使用者的設定。\n"
                "預覽只包含目前帳號可讀取的資料；整份操作也可能影響不可見設定。\n"
                "commit 不會保存 startup；discard 會捨棄整份 candidate 的未提交變更。\n"
                "送出前會 lock 並重新比對；不會自動重送或復原。\n\n" + prepared.diff + "\n\n實際 RPC:\n" + prepared.wire_xml)
            self.lifecycle_dialog = window
            window.transient(self.root)
            window.grab_set()
            def close():
                self.lifecycle_dialog = None
                window.destroy()
                self._sync()
            def confirm():
                if self.busy or not self.client.connected:
                    return
                close()
                options = self.snapshot.options if self.snapshot else self.options()
                self.last_sent_xml = prepared.wire_xml
                self.preview.set(prepared.wire_xml)
                def work():
                    reply, warnings = lifecycle.execute(self.client, prepared)
                    snapshot = None
                    if self.client.connected:
                        try:
                            snapshot = self.client.read(options)
                        except Exception:
                            warnings.append("更新 DATA TREE 失敗；請重新讀取，勿重送。")
                    return reply, warnings, snapshot
                def done(value):
                    reply, warnings, snapshot = value
                    self.reply.set(reply)
                    self.output_tabs.select(self.reply)
                    if snapshot is not None:
                        self._accept_snapshot(snapshot)
                    self.uncertain = bool(warnings) or snapshot is None
                    self.status.set(title + " RPC 成功。" + " ".join(warnings))
                self._run("設定操作：" + title, work, done)
            bar = ttk.Frame(window)
            bar.pack(fill="x", side="bottom", before=window.winfo_children()[0])
            ttk.Button(bar, text="取消", command=close).pack(side="right", padx=10, pady=8)
            ttk.Button(bar, text="確認送出整份 datastore 操作", command=confirm).pack(side="right")
            window.protocol("WM_DELETE_WINDOW", close)
            self._sync()
        self._run("準備設定操作預覽…", lambda: lifecycle.prepare(self.client, operation, source, confirm_timeout), preview)

    def subscribe(self):
        if self.busy or not self.client.connected or self.notification_manager is not None or self.demo or self._pending():
            return
        if not any(":notification:" in c for c in self.client.capabilities):
            self._error(EditError("Server 不支援 RFC 5277 notifications。"))
            return
        stream = self.stream.get().strip()
        try:
            subscription = events.subscription_options(stream, self.streams, self.event_filter_xml, self.event_start, self.event_stop)
        except Exception as exc:
            self._error(exc)
            return
        if not stream:
            self._error(EditError("請輸入 Stream 名稱（預設 NETCONF）。"))
            return
        interleave = any(":interleave:" in c for c in self.client.capabilities)
        if not interleave and not messagebox.askyesno("事件訂閱", "Server 不支援 interleave。訂閱後將停用本 session 的讀寫 RPC；"
                "停止訂閱需要中斷連線。是否開始？", parent=self.root, default="no"):
            return
        manager = self.client.manager
        def done(reply):
            self.notification_manager = manager
            self.reply.set(reply.xml)
            self.notification_status.set("已訂閱 " + stream + ("；可同時讀寫。" if interleave else "；讀寫暫停，停止須中斷連線。"))
            self.output_tabs.select(self.event_frame)
        self._run("訂閱事件…", lambda: manager.create_subscription(**subscription), done)

    def stop_subscription(self):
        if self.notification_manager is not None and not self.busy and messagebox.askyesno(
                "停止訂閱", "RFC 5277 訂閱將隨 session 關閉；是否中斷此連線？", parent=self.root, default="no"):
            self.disconnect()

    def _poll_notifications(self):
        manager = self.notification_manager
        if manager is None:
            return
        if not self.client.connected or self.client.manager is not manager:
            self.notification_manager = None
            self.notification_status.set("訂閱已隨連線結束；不會自動重新訂閱。")
            self._sync()
            return
        updated = False
        try:
            for _ in range(50):
                notification = manager.take_notification(block=False)
                if notification is None:
                    break
                text = redact_secrets(notification.notification_xml)
                if len(text) > 65536:
                    text = text[:65536] + "\n[通知過長，已截斷]"
                self.notifications.append(text)
                self._record_notification(text)
                updated = True
                if self.notification_manager is None:
                    self._sync()
                    break
        except Exception:
            # Do not unlock other RPCs: the server may still have an active subscription.
            self.notification_status.set("通知讀取失敗；請中斷連線以結束訂閱。")
        if updated:
            self.notification_pane.set("\n\n".join(self.notifications))
            self._audit_result("notification", "收到通知（內容僅保留記憶體，不寫入操作紀錄）")

    def export_notifications(self):
        if not self.notifications:
            return
        if not messagebox.askyesno("匯出通知", "只遮蔽常見密碼／私鑰欄位；通知仍可能包含設備敏感資料。\n"
                "匯出最近 100 筆，每筆最多 64 KiB 的文字紀錄？", parent=self.root, default="no"):
            return
        name = filedialog.asksaveasfilename(parent=self.root, defaultextension=".txt", initialfile="netconf-notifications.txt")
        if name:
            try:
                Path(name).write_text("\n\n".join(self.notifications), encoding="utf-8")
            except Exception as exc:
                self._error(exc)
