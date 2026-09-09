"""Native YANG candidate picker and template form; stages drafts only."""
from copy import deepcopy
import tkinter as tk
from tkinter import messagebox, ttk

from lxml import etree

from ..xmloutput import serialize_xml
from .creation import Template, append_template, candidates, new_root_selection, scalar_options
from .model import EditError, children, local, parse_editor


class CreationFeatures:
    def _build_creation(self, tools):
        self.creation_candidates = {}
        self.vars["show_candidates"] = tk.BooleanVar(self.root, False)
        ttk.Checkbutton(self.creation_bar, text="顯示可新增節點（雙擊建立）",
            variable=self.vars["show_candidates"], command=self._refresh_creation_candidates).pack(anchor="w")
        buttons = ttk.Frame(self.creation_bar)
        buttons.pack(anchor="w", pady=(2, 5))
        self.add_child_button = ttk.Button(buttons, text="新增子節點…", command=self.open_creation)
        self.add_child_button.pack(side="left")
        self.add_root_button = ttk.Button(buttons, text="新增根節點…", command=lambda: self.open_creation(root=True))
        self.add_root_button.pack(side="left", padx=5)
        self.tree.tag_configure("candidate", foreground="#64748b")
        tools.add_separator()
        tools.add_command(label="新增子節點／list 項目…", command=self.open_creation)
        tools.add_command(label="新增根節點…", command=lambda: self.open_creation(root=True))

    def _creation_ready(self):
        return (self.snapshot is not None and self.client.connected and self.client.schema.complete
            and self.snapshot.options.source in {"running", "candidate"}
            and not self.busy and not self.uncertain and not self._pending()
            and self.lifecycle_dialog is None)

    def _sync_creation(self):
        ready = self._creation_ready()
        info = self.client.schema.lookup(self.selection.path) if self.selection else None
        self.add_root_button.configure(state="normal" if ready else "disabled")
        self.add_child_button.configure(state="normal" if ready and info and info.config is True
            and info.kind in {"container", "list"} else "disabled")

    def _has_creation_children(self, selection):
        return (hasattr(self, "creation_candidates") and self.vars["show_candidates"].get()
            and any(c.allowed for c in candidates(self.client.schema, selection.node, selection.path)))

    def _insert_creation_candidates(self, iid=""):
        if not hasattr(self, "creation_candidates") or not self.vars["show_candidates"].get() or not self.snapshot:
            return
        parent = self.items.get(iid)
        node, path = (parent.node, parent.path) if parent else (self.snapshot.data, ())
        for index, item in enumerate(candidates(self.client.schema, node, path)):
            if not item.allowed:
                continue
            ghost = iid + "/candidate-" + str(index)
            if self.tree.exists(ghost):
                continue
            suffix = "新增項目" if item.info.kind in {"list", "leaf-list"} else "未讀到／可建立"
            self.tree.insert(iid, "end", iid=ghost,
                text="＋ " + item.info.module + ":" + local(item.info.path[-1]) + "（" + suffix + "）",
                tags=("candidate",))
            self.creation_candidates[ghost] = (iid, item.info)

    def _refresh_creation_candidates(self):
        for iid in self.creation_candidates:
            if self.tree.exists(iid):
                self.tree.delete(iid)
        self.creation_candidates.clear()
        if not self.snapshot:
            return
        self._insert_creation_candidates()
        for iid, selection in list(self.items.items()):
            existing = self.tree.get_children(iid)
            if existing and existing[0].endswith("/dummy"):
                if not children(selection.node) and not self._has_creation_children(selection):
                    self.tree.delete(existing[0])
            elif self.tree.item(iid, "open") or existing:
                self._insert_creation_candidates(iid)
            elif self._has_creation_children(selection):
                self.tree.insert(iid, "end", iid=iid + "/dummy", text="…")

    def open_creation(self, root=False, info=None, parent_iid=None):
        if not self._creation_ready():
            self.status.set("請先連線並載入完整 running/candidate schema；此功能只建立本機草稿。")
            return
        if parent_iid is not None and not root and parent_iid != self.selection_iid:
            if not self._discard():
                return
            self._show_selection(parent_iid)
        if root:
            if not self._discard():
                return
            parent, path = self.snapshot.data, ()
        else:
            if not self.selection:
                return
            node_info = self.client.schema.lookup(self.selection.path)
            if not node_info or node_info.kind not in {"container", "list"}:
                self.status.set("請選取 container 或 list；新增 list 項目時請選取它的父節點。")
                return
            try:
                parent, path = parse_editor(self.editor.get()), self.selection.path
                if parent.tag != self.selection.node.tag:
                    raise EditError("請保留選取節點的名稱與 namespace")
            except Exception as exc:
                self._error(exc)
                return
        CreationDialog(self, parent, path, info)


class CreationDialog:
    def __init__(self, app, parent, path, initial=None):
        from .app import XmlPane
        self.app, self.parent, self.path = app, deepcopy(parent), path
        self.schema, self.snapshot = app.client.schema, app.snapshot
        self.selection, self.editor_before = app.selection, app.editor.get()
        self.template = None
        self.nodes = {}
        self.child_options = []
        self.previous_pick = None
        self.editing_node = None
        self.window = tk.Toplevel(app.root)
        app.lifecycle_dialog = self.window
        self.window.title("新增 YANG 節點 — 本機 XML 草稿")
        self.window.geometry("1120x780")
        self.window.minsize(820, 620)
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _e: self.close())
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(2, weight=3)
        self.window.rowconfigure(4, weight=2)
        ttk.Label(self.window, text="選擇節點 → 填值／新增分支 → 加入草稿 → 檢查 RPC 後再送出",
            font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=8)
        ttk.Label(self.window, text="未讀到不代表不存在（可能為 NACM／default／條件）。新增使用 create，遇到既有項目會拒絕覆寫。\n"
            "when、must、leafref、unique 與權限仍由伺服器驗證；可使用 test-only（需 :validate:1.1），不會自動送出。",
            wraplength=1060).grid(row=1, column=0, sticky="w", padx=10)
        body = ttk.Panedwindow(self.window, orient="horizontal")
        body.grid(row=2, column=0, sticky="nsew", padx=10, pady=8)
        pick_frame, form = ttk.Frame(body), ttk.Frame(body)
        body.add(pick_frame, weight=1)
        body.add(form, weight=2)
        self.search = tk.StringVar(self.window)
        ttk.Label(pick_frame, text="候選節點（灰色不可新增）／搜尋").pack(anchor="w")
        ttk.Entry(pick_frame, textvariable=self.search).pack(fill="x")
        self.picker = ttk.Treeview(pick_frame, columns=("kind",), show="tree headings", selectmode="browse")
        self.picker.heading("#0", text="Module : 節點")
        self.picker.column("#0", width=230)
        self.picker.heading("kind", text="類型")
        self.picker.column("kind", width=75, stretch=False)
        self.picker.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(pick_frame, orient="vertical", command=self.picker.yview)
        scroll.pack(side="right", fill="y")
        self.picker.configure(yscrollcommand=scroll.set)
        self.picker.tag_configure("blocked", foreground="#94a3b8")
        self.picker.bind("<<TreeviewSelect>>", self.pick)
        self.catalog = candidates(self.schema, self.parent, self.path)
        self.search.trace_add("write", lambda *_: self.fill_picker())
        form.columnconfigure(0, weight=1)
        form.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(form, columns=("value",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="範本階層（選取欄位填值）")
        self.tree.heading("value", text="值／狀態")
        self.tree.column("#0", width=230)
        self.tree.column("value", width=210)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(form, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.select_field)
        self.tree.tag_configure("pending", foreground="#b45309")
        self.field_info = tk.StringVar(self.window)
        ttk.Label(form, textvariable=self.field_info, wraplength=570).grid(row=1, column=0, sticky="w", pady=4)
        values = ttk.Frame(form)
        values.grid(row=2, column=0, sticky="ew")
        values.columnconfigure(0, weight=1)
        self.value = tk.StringVar(self.window)
        self.value_entry = ttk.Combobox(values, textvariable=self.value)
        self.value_entry.grid(row=0, column=0, sticky="ew")
        self.value_entry.bind("<Return>", lambda _e: self.set_value())
        self.value_button = ttk.Button(values, text="套用欄位值", command=self.set_value)
        self.value_button.grid(row=0, column=1, padx=4)
        more = ttk.Frame(form)
        more.grid(row=3, column=0, sticky="ew", pady=5)
        more.columnconfigure(0, weight=1)
        self.child_box = ttk.Combobox(more, state="readonly")
        self.child_box.grid(row=0, column=0, sticky="ew")
        ttk.Button(more, text="新增子節點／項目", command=self.add_child).grid(row=0, column=1, padx=4)
        ttk.Button(more, text="移除範本節點", command=self.remove).grid(row=1, column=1, pady=4)
        self.status = tk.StringVar(self.window, "請選擇要新增的節點")
        ttk.Label(self.window, textvariable=self.status, wraplength=1060, foreground="#b45309").grid(
            row=3, column=0, sticky="w", padx=10)
        self.preview = XmlPane(self.window, readonly=True)
        self.preview.grid(row=4, column=0, sticky="nsew", padx=10, pady=6)
        self.preview.text.configure(height=8)
        bar = ttk.Frame(self.window)
        bar.grid(row=5, column=0, sticky="e", padx=10, pady=8)
        ttk.Button(bar, text="取消", command=self.close).pack(side="right")
        self.stage_button = ttk.Button(bar, text="加入 XML 草稿", command=self.stage)
        self.stage_button.pack(side="right", padx=8)
        self.fill_picker()
        chosen = next((str(i) for i, c in enumerate(self.catalog) if c.allowed and (initial is None or c.info == initial)), None)
        if chosen is not None:
            self.picker.selection_set(chosen)
            self.picker.see(chosen)
        self.refresh_status()
        app._sync()
        self.window.grab_set()

    def fill_picker(self):
        self.picker.delete(*self.picker.get_children())
        query = self.search.get().casefold()
        for i, item in enumerate(self.catalog):
            label = item.info.module + ":" + local(item.info.path[-1])
            if query in (label + item.info.description + item.info.type_name).casefold():
                self.picker.insert("", "end", iid=str(i), text=label, values=(item.info.kind,),
                    tags=() if item.allowed else ("blocked",))

    def pick(self, _event=None):
        selected = self.picker.selection()
        if not selected or selected[0] == self.previous_pick:
            return
        item = self.catalog[int(selected[0])]
        if not item.allowed:
            self.status.set(item.reason)
            return
        if self.template is not None and not messagebox.askyesno("切換範本", "放棄此視窗內的範本，改選另一種節點？",
                parent=self.window, default="no"):
            if self.previous_pick and self.picker.exists(self.previous_pick):
                self.picker.selection_set(self.previous_pick)
            return
        try:
            self.template = Template(self.schema, item.info)
            self.editing_node = None
            self.previous_pick = selected[0]
            self.rebuild()
        except Exception as exc:
            self.status.set(str(exc))

    def rebuild(self, selected_node=None):
        self.tree.delete(*self.tree.get_children())
        self.nodes.clear()
        if self.template is None:
            return
        def walk(node, parent=""):
            iid = str(len(self.nodes))
            self.nodes[iid] = node
            secret = any(word in local(node.tag).lower() for word in ("password", "secret", "private-key"))
            pending = node in self.template.pending
            value = "待填" if pending else ("••••••" if secret and node.text else node.text or "")
            self.tree.insert(parent, "end", iid=iid, text=local(node.tag), values=(value,), open=True,
                tags=("pending",) if pending else ())
            for child in children(node):
                walk(child, iid)
        walk(self.template.root)
        chosen = next((iid for iid, node in self.nodes.items() if node is selected_node), "0")
        self.tree.selection_set(chosen)
        self.tree.see(chosen)
        self.refresh_status()

    def selected_node(self):
        ids = self.tree.selection()
        return self.nodes.get(ids[0]) if ids else None

    def select_field(self, _event=None):
        node = self.selected_node()
        if node is None or self.template is None:
            return
        previous = self.editing_node
        if previous is not None and previous is not node and previous in self.nodes.values():
            previous_info = self.schema.lookup(self.template.node_path(previous))
            if previous_info.kind in {"leaf", "leaf-list"} and self.value.get() != (previous.text or ""):
                try:
                    self.template.set_value(previous, self.value.get())
                except EditError as exc:
                    self.tree.selection_set(next(iid for iid, n in self.nodes.items() if n is previous))
                    self.status.set(str(exc))
                    return
                self.editing_node = None
                self.rebuild(node)
                return
        self.editing_node = node
        path = self.template.node_path(node)
        info = self.schema.lookup(path)
        scalar = info.kind in {"leaf", "leaf-list"}
        self.value.set(node.text or "")
        secret = any(word in local(node.tag).lower() for word in ("password", "secret", "private-key"))
        self.value_entry.configure(state="normal" if scalar else "disabled", show="•" if secret else "",
            values=scalar_options(self.schema, info) if scalar else ())
        self.value_button.configure(state="normal" if scalar else "disabled")
        self.field_info.set(info.module + " · " + info.kind + " · " + info.type_name
            + (" · presence: " + info.presence if info.presence else "") + "\n"
            + (info.constraints or info.description)[:350])
        self.child_options = [c.info for c in candidates(self.schema, node, path) if c.allowed] if not scalar else []
        self.child_box.configure(values=[i.module + ":" + local(i.path[-1]) + " (" + i.kind + ")" for i in self.child_options])
        self.child_box.set("")
        if self.child_options:
            self.child_box.current(0)

    def set_value(self):
        node = self.selected_node()
        if node is None:
            return
        try:
            self.template.set_value(node, self.value.get())
            self.rebuild(node)
        except Exception as exc:
            self.status.set(str(exc))

    def add_child(self):
        node, index = self.selected_node(), self.child_box.current()
        if node is None or index < 0:
            return
        try:
            new = self.template.add(node, self.child_options[index])
            self.rebuild(new)
        except Exception as exc:
            self.status.set(str(exc))

    def remove(self):
        node = self.selected_node()
        if node is None or node is self.template.root:
            return
        parent = node.getparent()
        parent.remove(node)
        self.rebuild(parent)

    def refresh_status(self):
        issues = self.template.issues() if self.template else ["請選擇節點"]
        self.stage_button.configure(state="disabled" if issues else "normal")
        self.status.set("；".join(issues[:3]) if issues else "本機檢查通過；完整 YANG 條件／權限仍需伺服器驗證。尚未送出。")
        self.preview.set(serialize_xml(self.template.root).decode("utf-8") if self.template else "")

    def stage(self):
        try:
            if self.template is None:
                return
            if (self.app.snapshot is not self.snapshot or self.app.client.schema is not self.schema
                    or self.app.selection is not self.selection or self.app.editor.get() != self.editor_before):
                raise EditError("資料或草稿已改變，請關閉後重新建立範本")
            node = self.selected_node()
            if node is not None:
                info = self.schema.lookup(self.template.node_path(node))
                if info.kind in {"leaf", "leaf-list"} and self.value.get() != (node.text or ""):
                    raise EditError("請先按「套用欄位值」，避免遺漏尚未套用的輸入")
            result = append_template(self.schema, self.parent, self.path, self.template)
            if not self.path:
                self.app.selection = new_root_selection(self.template.root, self.schema)
                self.app.selection_iid = None
                self.app.tree.selection_remove(*self.app.tree.selection())
                self.app.baseline_text = self.app.selection.text()
                self.app.path_status.set("[新增草稿] /" + local(self.template.root.tag))
                result = deepcopy(self.template.root)
            self.app.editor.set(serialize_xml(result).decode("utf-8"))
            self.close()
            self.app._update_preview()
            self.app.status.set("已加入本機 XML 草稿；請檢查實際送出的 RPC，再選 NETCONF 或系統 sysrepocfg 修改。")
        except Exception as exc:
            self.status.set(str(exc))

    def close(self):
        if self.window.winfo_exists():
            self.window.grab_release()
            self.window.destroy()
        if self.app.lifecycle_dialog is self.window:
            self.app.lifecycle_dialog = None
        self.app._sync()
