"""Cross-node draft shelf, explicit restore checks and send guards."""
import json
import tkinter as tk
from tkinter import messagebox, ttk

from .client import ReadOptions
from .drafts import DraftShelf, schema_fingerprint
from .model import EditError
from . import lifecycle


class DraftFeatures:
    def _build_drafts(self, tools):
        path = self.preferences_store.path.parent / "gui-drafts.nccdrafts" if self.preferences_store else None
        self.drafts = DraftShelf(path)
        self.drafts.load()
        self.draft_timer = None
        self.draft_fingerprint_cache = None
        self.draft_status = tk.StringVar(self.root, self.drafts.load_error)
        action_bar = getattr(self, "creation_action_bar", self.creation_bar)
        self.draft_button = ttk.Button(action_bar, text="草稿清單", command=self.show_drafts)
        self.draft_button.pack(side="left", padx=(4, 0))
        tools.add_command(label="草稿清單／加密保存與恢復…", command=self.show_drafts)

    def _draft_scope(self):
        context = getattr(self.client, "context", None)
        if self.demo:
            return "DEMO"
        if context is None:
            return "OFFLINE"
        settings = context.settings
        # No password, key contents, or ephemeral Call Home source port in identity.
        return json.dumps({"endpoint": context._namespace_server_key(),
            "username": settings.username, "tls_name": settings.tls_server_name,
            "cert": settings.cert, "ssh_key": settings.key, "ssh_auth": settings.ssh_auth,
            "jump": [settings.jump_enabled, settings.jump_host, settings.jump_port, settings.jump_username]},
            sort_keys=True, ensure_ascii=False)

    def _draft_session(self):
        return (self.client if self.demo else self.client.manager) if self.client.connected else None

    def _active_draft(self):
        if not hasattr(self, "drafts") or not self.drafts.entries or self.selection is None or self.snapshot is None:
            return None
        try:
            return self.drafts.matching(self._draft_scope(), self.snapshot.options.source, self.selection, self.client.schema)
        except (EditError, AttributeError):
            return None

    def _capture_draft(self):
        if not self.selection or not self.snapshot or not self.client.schema.complete:
            return
        entry = self._active_draft()
        if not self.dirty:
            if entry:
                self.drafts.entries.pop(entry.key, None)
                self._schedule_drafts()
            return
        schema = self.client.schema
        if self.draft_fingerprint_cache is None or self.draft_fingerprint_cache[0] is not schema:
            self.draft_fingerprint_cache = (schema, schema_fingerprint(schema))
        previous_text = entry.text if entry else None
        self.drafts.capture(self._draft_scope(), self.snapshot.options.source, self.selection,
                            self.editor.get(), schema, self._draft_session(), self.draft_fingerprint_cache[1])
        if previous_text != self.editor.get():
            self._schedule_drafts()

    def _schedule_drafts(self):
        if self.draft_timer:
            self.root.after_cancel(self.draft_timer)
        self.draft_timer = self.root.after(700, self._save_drafts)

    def _save_drafts(self):
        if self.draft_timer:
            self.root.after_cancel(self.draft_timer)
            self.draft_timer = None
        try:
            if self.drafts.load_error and not self.drafts.entries:
                # A corrupt/foreign file must not trap a clean app on exit.
                self.draft_status.set(self.drafts.load_error)
                return True
            if self.drafts.entries or self.drafts.path and self.drafts.path.exists():
                self.drafts.save()
            self.draft_status.set("草稿已加密保存（僅原 Windows 帳號／電腦可開啟）" if self.drafts.path else "草稿僅保存在記憶體（示範／測試模式）")
            return True
        except Exception as exc:
            self.draft_status.set(self.drafts.load_error or "草稿保存失敗，請勿關閉未保存內容：" + type(exc).__name__)
            self.status.set(self.draft_status.get())
            return False

    def _preserve_current_draft(self, flush=False):
        try:
            self._capture_draft()
            if flush and not self._save_drafts():
                self._error(EditError(self.draft_status.get()))
                return False
            return True
        except Exception as exc:
            self._error(exc)
            return False

    def _forget_active_draft(self):
        entry = self._active_draft()
        if entry:
            self.drafts.entries.pop(entry.key, None)
            self._schedule_drafts()

    def _draft_guard(self):
        if not self.drafts.entries:
            return ""
        entry = self._active_draft()
        if entry:
            schema = self.client.schema
            if self.draft_fingerprint_cache is None or self.draft_fingerprint_cache[0] is not schema:
                self.draft_fingerprint_cache = (schema, schema_fingerprint(schema))
            if self.draft_fingerprint_cache[1] != entry.schema_hash:
                return "草稿 schema 與目前設備不同，請核對後重建草稿"
        if entry and (entry.session is not self._draft_session() or entry.session is None):
            return "草稿尚未比對此 session；請從草稿清單按「重新比對／載入」"
        if self.selection and self.snapshot:
            try:
                overlap = self.drafts.overlapping(self._draft_scope(), self.snapshot.options.source,
                                                  self.selection, self.client.schema)
                if overlap:
                    return "此範圍與既有草稿重疊；請先從草稿清單開啟：" + overlap.label
            except EditError as exc:
                return str(exc)
        return ""

    def _restore_selected_draft(self):
        entry = self._active_draft()
        if entry:
            self.selection = entry.selection
            self.baseline_text = entry.selection.text()
            self.editor.set(entry.text)

    def _sync_drafts(self):
        self.draft_button.configure(text="草稿清單（%d）" % len(self.drafts.entries),
            state="normal" if not self.busy and not self.lifecycle_dialog else "disabled")
        guard = self._draft_guard()
        if guard:
            self.send_button.configure(state="disabled")
            self.admin_edit_button.configure(state="disabled")
            self.datastore_menu.entryconfigure(self.draft_index, state="disabled")
            self.preview_status.set(self.preview_status.get().split(" · 草稿提醒：")[0] + " · 草稿提醒：" + guard)
            if guard.startswith("此範圍"):
                self.editor.text.configure(state="disabled")
                self.add_child_button.configure(state="disabled")
        # Other drafts must not accidentally be committed/discarded by a whole-store action.
        if self._related_drafts():
            for index, name in enumerate(lifecycle.OPERATIONS):
                if name not in {"compare", "validate"}:
                    self.datastore_menu.entryconfigure(index, state="disabled")

    def _related_drafts(self):
        return any(entry.scope == self._draft_scope() for entry in self.drafts.entries.values())

    def show_drafts(self):
        if self.busy or self.lifecycle_dialog or not self._preserve_current_draft(flush=True):
            return
        from .app import XmlPane
        window = tk.Toplevel(self.root)
        self.lifecycle_dialog = window
        window.title("草稿清單 — 只載入本機，不會批次送出")
        window.geometry("1060x680")
        window.transient(self.root)
        window.grab_set()
        ttk.Label(window, text="依設備／帳號、source、節點路徑隔離；父子範圍重疊時請回原草稿修改。\n"
            "重新開啟後須先連線，再重新比對。每份草稿仍須個別預覽、確認送出；不會自動重播。",
            wraplength=1000).pack(fill="x", padx=10, pady=6)
        rows = ttk.Frame(window)
        rows.pack(fill="x", padx=10)
        tree = ttk.Treeview(rows, columns=("source", "status"), height=7, selectmode="browse")
        tree.heading("#0", text="節點路徑")
        tree.column("#0", width=640)
        tree.heading("source", text="Source")
        tree.column("source", width=85)
        tree.heading("status", text="狀態")
        tree.column("status", width=190)
        scroll = ttk.Scrollbar(rows, orient="vertical", command=tree.yview)
        scroll.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        for entry in self.drafts.entries.values():
            tree.insert("", "end", iid=entry.key, text=entry.label, values=(entry.source, entry.status))
        detail = tk.StringVar(window)
        ttk.Label(window, textvariable=detail, wraplength=1020).pack(fill="x", padx=10, pady=5)
        controls = ttk.Frame(window)
        controls.pack(side="bottom", fill="x", padx=10, pady=8)
        pane = XmlPane(window, readonly=True)
        pane.pack(fill="both", expand=True, padx=10)
        ttk.Label(window, textvariable=self.draft_status, wraplength=1000).pack(fill="x", padx=10)
        def selected():
            ids = tree.selection()
            return self.drafts.entries.get(ids[0]) if ids else None
        def preview(_event=None):
            entry = selected()
            if entry:
                detail.set("設備：" + entry.scope + "\n" + entry.updated + " · XML 可能含敏感值，分享前請遮蔽")
                pane.set(entry.text)
        def close():
            self.lifecycle_dialog = None
            window.grab_release()
            window.destroy()
            self._sync()
        def remove():
            entry = selected()
            if entry and messagebox.askyesno("刪除草稿", "移除此本機草稿？不會修改設備。\n" + entry.label,
                    parent=window, default="no"):
                active = self._active_draft()
                self.drafts.entries.pop(entry.key)
                if not self._save_drafts():
                    self.drafts.entries[entry.key] = entry
                    detail.set(self.draft_status.get())
                    return
                tree.delete(entry.key)
                pane.set("")
                if active is entry:
                    self.selection = self.plan = None
                    self.editor.set("")
                    self.preview.set("")
                self._sync()
        def restore():
            entry = selected()
            if entry is None or not self.client.connected or not self.snapshot or self._pending():
                detail.set("請先關閉視窗、連線並讀取目標 datastore；限時提交進行中不能載入")
                return
            scope, options, session, schema = self._draft_scope(), self.snapshot.options, self._draft_session(), self.client.schema
            if scope != entry.scope or options.source != entry.source:
                detail.set("目前設備／帳號／連線路徑或 source 與此草稿不同")
                return
            if not messagebox.askyesno("比對並載入草稿", "確認目前連線是草稿的原設備與 datastore？\n"
                    "將重新讀取 config 並比對原始值；不會送出修改。", parent=window, default="no"):
                return
            close()
            def work():
                fresh = self.client.read(ReadOptions(entry.source, options.defaults, False), entry.selection.path[0])
                self.drafts.verify(entry, scope, entry.source, schema, fresh.data, session)
            def done(_):
                if session is not self._draft_session():
                    entry.session = None
                    raise EditError("連線已變更，請重新比對")
                self.selection = entry.selection
                self.selection_iid = None
                self.tree.selection_remove(*self.tree.selection())
                self.baseline_text = entry.selection.text()
                self.editor.set(entry.text)
                self.path_status.set("[草稿] " + entry.label)
                self.uncertain = False
                self._update_preview()
                self.status.set("草稿原值已重新比對；請檢查 XML 後再自行送出。")
            self._run("重新比對草稿…", work, done)
        ttk.Button(controls, text="重新比對／載入", command=restore).pack(side="left")
        ttk.Button(controls, text="刪除草稿", command=remove).pack(side="left", padx=5)
        ttk.Button(controls, text="加密保存", command=self._save_drafts).pack(side="left")
        ttk.Button(controls, text="關閉", command=close).pack(side="right")
        tree.bind("<<TreeviewSelect>>", preview)
        window.protocol("WM_DELETE_WINDOW", close)
        self._sync()
