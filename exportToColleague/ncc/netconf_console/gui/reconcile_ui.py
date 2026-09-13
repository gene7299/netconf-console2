"""Explicit three-way rebase and per-value readback results."""
from copy import deepcopy
import tkinter as tk
from tkinter import messagebox, ttk

from lxml import etree

from . import reconcile
from .client import ReadOptions
from .drafts import schema_fingerprint
from .model import EditError, NC, parse_editor
from ..xmloutput import serialize_xml


class ReconcileFeatures:
    def _build_reconcile(self, tools):
        self.last_attempt = None
        self.result_rows = []
        self.result_pending = True
        self.result_frame = ttk.Frame(self.output_tabs)
        self.output_tabs.add(self.result_frame, text="送出結果核對")
        self.result_status = tk.StringVar(self.root, "尚未送出；此頁只保留本次執行的最近一次修改。")
        ttk.Label(self.result_frame, textvariable=self.result_status, wraplength=850).pack(fill="x")
        ttk.Button(self.result_frame, text="重新讀回核對（不重送）", command=self.reread_attempt).pack(anchor="w")
        result_table = ttk.Frame(self.result_frame)
        result_table.pack(fill="both", expand=True)
        result_table.rowconfigure(0, weight=1)
        result_table.columnconfigure(0, weight=1)
        self.result_tree = ttk.Treeview(result_table, columns=("expected", "actual", "status"), height=4)
        self.result_tree.heading("#0", text="節點／instance")
        # Keep the notebook's requested width small enough for the DATA TREE
        # toolbar at 1120px. Columns remain resizable; scroll for longer paths.
        self.result_tree.column("#0", width=280, minwidth=160)
        for key, title in (("expected", "預期值"), ("actual", "讀回值"), ("status", "結果")):
            self.result_tree.heading(key, text=title)
            self.result_tree.column(key, width=120, minwidth=80)
        self.result_tree.tag_configure("ok", foreground="#15803d")
        self.result_tree.tag_configure("pending", foreground="#b45309")
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(result_table, orient="vertical", command=self.result_tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(result_table, orient="horizontal", command=self.result_tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.result_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.result_tree.bind("<Double-1>", self._show_result_xml)
        self.vars["rollback_on_error"] = tk.BooleanVar(self.root, False)
        self.rollback_check = ttk.Checkbutton(self.result_frame, text="NETCONF rollback-on-error（需 server 支援；不適用 sysrepocfg）",
            variable=self.vars["rollback_on_error"], command=self._update_preview)
        self.rollback_check.pack(anchor="w")
        tools.add_command(label="原始值／設備最新值／草稿：三方比對…", command=self.open_reconcile)

    def _rollback_supported(self):
        return self.client.connected and any(c.split("?", 1)[0] == "urn:ietf:params:netconf:capability:rollback-on-error:1.0" for c in self.client.capabilities)

    def _sync_reconcile(self):
        self.rollback_check.configure(state="normal" if self._rollback_supported() and not self.busy and not self.lifecycle_dialog else "disabled")

    def _apply_rollback_option(self):
        if self.vars["rollback_on_error"].get() and self.plan and self.plan.rpc is not None:
            if not self._rollback_supported():
                raise EditError("此 server 未宣告 rollback-on-error；請取消此選項")
            from ncclient.xml_ import to_xml
            operation = self.plan.rpc[0]
            option = etree.Element("{%s}error-option" % NC)
            option.text = "rollback-on-error"
            operation.insert(len(operation) - 1, option)
            self.plan.rpc = etree.fromstring(serialize_xml(self.plan.rpc))
            self.plan.wire_xml = to_xml(self.plan.rpc)

    def _begin_attempt(self, selection, plan, options, route):
        self.last_attempt = {"selection": deepcopy(selection), "mine": deepcopy(plan.edited), "options": options,
            "scope": self._draft_scope(), "schema": self.client.schema, "hash": schema_fingerprint(self.client.schema),
            "route": route, "session": self._draft_session()}
        self.result_tree.delete(*self.result_tree.get_children())
        self.result_pending = True
        _, self.result_rows = reconcile.compare(selection, plan.edited, self.snapshot.data, self.client.schema, options.source)
        for i, row in enumerate(self.result_rows):
            self.result_tree.insert("", "end", iid=str(i), text=row.path,
                values=(reconcile.display(row.mine), "未讀回", "待確認"), tags=("pending",))
        self.result_status.set(route + " 已開始；尚未讀回，不能假定已成功或已回復。")

    def _finish_attempt(self, snapshot=None, error=None):
        attempt = self.last_attempt
        if not attempt:
            return
        if snapshot is None:
            self.result_status.set("結果待確認；" + (type(error).__name__ if error else "未取得 NETCONF 讀回") + "。可重新讀回，不會重送。")
            self.output_tabs.select(self.result_frame)
            return
        schema = attempt["schema"]
        _, rows = reconcile.compare(attempt["selection"], attempt["mine"], snapshot.data, schema, attempt["options"].source)
        self.result_tree.delete(*self.result_tree.get_children())
        self.result_rows = rows
        self.result_pending = False
        matched = 0
        for i, row in enumerate(rows):
            path = attempt["selection"].path + tuple(key[0] for key in row.address)
            ok = reconcile.equal(row.mine, row.latest, path, schema)
            matched += int(ok)
            self.result_tree.insert("", "end", iid=str(i), text=row.path,
                values=(reconcile.display(row.mine), reconcile.display(row.latest), "符合預期" if ok else "不符／需核對"),
                tags=("ok" if ok else "pending",))
        self.result_status.set("%d / %d 項符合預期；雙擊查看子樹 XML。不代表已 commit 或保存 startup。" % (matched, len(rows)))

    def _show_result_xml(self, _event=None):
        ids = self.result_tree.selection()
        if not ids or self.busy or self.lifecycle_dialog:
            return
        row = self.result_rows[int(ids[0])]
        window = tk.Toplevel(self.root)
        window.title("預期／實際 XML（可能含敏感值）")
        window.geometry("1000x550")
        from .app import XmlPane
        pane = XmlPane(window, readonly=True)
        pane.pack(fill="both", expand=True)
        pane.set("預期：\n" + _xml(row.mine) + "\n設備：\n" + ("尚未讀回，結果未知" if self.result_pending else _xml(row.latest)))

    def reread_attempt(self):
        attempt = self.last_attempt
        if not attempt or not self.client.connected or self.busy or self.lifecycle_dialog or self._pending():
            return
        if not self._rpc_allowed():
            self.status.set("目前通知訂閱不允許交錯 RPC，請先中斷／重新連線再讀回")
            return
        if attempt["scope"] != self._draft_scope() or attempt["hash"] != schema_fingerprint(self.client.schema):
            self.status.set("不是原設備／帳號／schema，不能核對這次修改")
            return
        options = attempt["options"]
        self._run("重新讀回修改結果…", lambda: self.client.read(ReadOptions(options.source, options.defaults, False), attempt["selection"].path[0]), self._finish_attempt)

    def open_reconcile(self):
        if not self.selection or not self.snapshot or not self.client.connected or self.busy or self.lifecycle_dialog or self._pending():
            self.status.set("請連線、選取草稿，並完成其他操作後再比對")
            return
        if not self._rpc_allowed():
            self.status.set("目前通知訂閱不允許交錯 RPC，請先中斷／重新連線再比對")
            return
        if not self._preserve_current_draft():
            return
        try:
            entry = self._active_draft()
            schema = self.client.schema
            if not schema.complete or entry and entry.schema_hash != schema_fingerprint(schema):
                raise EditError("草稿與目前 schema 不同，請先核對 schema 後重建")
            selection = deepcopy(self.selection)
            mine = parse_editor(self.editor.get())
            before_text = self.editor.get()
            options, scope, session = self.snapshot.options, self._draft_scope(), self._draft_session()
            def work():
                fresh = self.client.read(ReadOptions(options.source, options.defaults, False), selection.path[0])
                latest, rows = reconcile.compare(selection, mine, fresh.data, schema, options.source)
                return latest, rows
            def done(result):
                if session is not self._draft_session() or self.editor.get() != before_text or self.client.schema is not schema:
                    raise EditError("連線或草稿已改變，請重新比對")
                ReconcileDialog(self, selection, *result, options, scope, session, schema)
            self._run("三方比對：唯讀設備最新值…", work, done)
        except Exception as exc:
            self._error(exc)


def _xml(node):
    return serialize_xml(node).decode() if node is not None else "（不存在）"


class ReconcileDialog:
    def __init__(self, app, selection, latest, rows, options, scope, session, schema):
        self.app, self.selection, self.latest, self.rows = app, selection, latest, rows
        self.options, self.scope, self.session, self.schema = options, scope, session, schema
        self.window = tk.Toplevel(app.root)
        app.lifecycle_dialog = self.window
        self.window.title("三方比對 — 明確選擇後重建本機草稿")
        self.window.geometry("1220x780")
        self.window.transient(app.root)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        ttk.Label(self.window, text="保留設備的無關變更；衝突須逐項選擇。合併只產生草稿，送出前仍會再次讀取比對。\n"
            "新增／移除整個子樹視為一項；列表順序、mandatory、must／when 等仍需本機與 server 驗證。",
            wraplength=1170).pack(fill="x", padx=10, pady=7)
        self.tree = ttk.Treeview(self.window, columns=("before", "latest", "mine", "choice"), height=8)
        self.tree.heading("#0", text="節點／instance")
        self.tree.column("#0", width=450)
        for key, title in (("before", "原始值"), ("latest", "設備最新值"), ("mine", "我的草稿"), ("choice", "採用／狀態")):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=170)
        self.tree.pack(fill="x", padx=10)
        self.tree.tag_configure("conflict", foreground="#b91c1c")
        for i, row in enumerate(rows):
            self.tree.insert("", "end", iid=str(i), text=row.path, values=(reconcile.display(row.before),
                reconcile.display(row.latest), reconcile.display(row.mine), row.status), tags=("conflict",) if not row.choice else ())
        self.status = tk.StringVar(self.window, "尚有 %d 項衝突；原始草稿保留，取消不修改。" % sum(not r.choice for r in rows))
        ttk.Label(self.window, textvariable=self.status, wraplength=1170).pack(fill="x", padx=10, pady=6)
        from .app import XmlPane
        panes = ttk.Panedwindow(self.window, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10)
        self.panes = []
        for title in ("原始值", "設備最新值", "我的草稿"):
            frame = ttk.Frame(panes)
            ttk.Label(frame, text=title).pack(anchor="w")
            pane = XmlPane(frame, readonly=True)
            pane.pack(fill="both", expand=True)
            panes.add(frame, weight=1)
            self.panes.append(pane)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        controls = ttk.Frame(self.window)
        controls.pack(fill="x", padx=10, pady=8)
        ttk.Button(controls, text="此項採用設備最新值", command=lambda: self.choose("latest")).pack(side="left")
        ttk.Button(controls, text="此項採用我的草稿", command=lambda: self.choose("mine")).pack(side="left", padx=5)
        ttk.Button(controls, text="確認合併為新草稿", command=self.stage).pack(side="right")
        ttk.Button(controls, text="取消", command=self.close).pack(side="right", padx=5)
        app._sync()

    def select(self, _event=None):
        ids = self.tree.selection()
        if ids:
            row = self.rows[int(ids[0])]
            for pane, node in zip(self.panes, (row.before, row.latest, row.mine)):
                pane.set(_xml(node))

    def choose(self, choice):
        ids = self.tree.selection()
        if ids:
            row = self.rows[int(ids[0])]
            row.choice = choice
            self.tree.set(ids[0], "choice", "設備最新值" if choice == "latest" else "我的草稿")
            self.tree.item(ids[0], tags=())
            self.status.set("尚有 %d 項待選擇" % sum(not r.choice for r in self.rows))

    def stage(self):
        try:
            if self.app.busy or self.session is not self.app._draft_session() or self.schema is not self.app.client.schema:
                raise EditError("連線／schema 已改變，請重新比對")
            baseline, text = reconcile.resolve_rows(self.selection, self.latest, self.rows, self.schema, self.options.source)
            if not messagebox.askyesno("確認重建草稿", "採用剛才讀取的設備資料為新基準，依選擇合併草稿？\n不會送出。請檢查新的 RPC 後再自行確認修改。", parent=self.window, default="no"):
                return
            old_entries = dict(self.app.drafts.entries)
            active = self.app._active_draft()
            if active:
                self.app.drafts.entries.pop(active.key, None)
            try:
                from .model import build_plan
                plan = build_plan(baseline, text, self.schema, self.options.source)
                if plan.rpc is not None:
                    self.app.drafts.capture(self.scope, self.options.source, baseline, text, self.schema, self.session, schema_fingerprint(self.schema))
                self.app.drafts.save()
            except Exception:
                self.app.drafts.entries = old_entries
                raise
            self.app.selection, self.app.selection_iid = baseline, None
            self.app.baseline_text = baseline.text()
            self.app.editor.set(text)
            self.app.uncertain = False
            self.close()
            self.app._update_preview()
            self.app.status.set("已重建本機草稿；未送出，請檢查差異與 RPC。")
        except Exception as exc:
            self.status.set(str(exc))

    def close(self):
        self.app.lifecycle_dialog = None
        self.window.grab_release()
        self.window.destroy()
        self.app._sync()
