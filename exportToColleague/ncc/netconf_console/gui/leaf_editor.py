"""Native forms for existing scalar data and visible leafref relationships."""
from copy import deepcopy
import tkinter as tk
from tkinter import messagebox, ttk

from lxml import etree

from ..xmloutput import serialize_xml
from .creation import scalar_input, suggested_values
from .leafrefs import resolve
from .model import EditError, Selection, build_plan, children, local, parse_editor
from .workspace import instance_path


class LeafFeatures:
    def _build_leaf_editor(self, tools):
        self.pending_leaf_bookmark = None
        action_bar = getattr(self, "creation_action_bar", self.creation_bar)
        self.leaf_button = ttk.Button(action_bar, text="表單編輯 leaf／引用…", command=self.open_leaf_editor)
        self.leaf_button.pack(side="left", padx=(4, 0))
        tools.add_command(label="表單編輯 leaf／leafref 關聯…", command=self.open_leaf_editor)

    def _sync_leaf_editor(self):
        enabled = (self.selection is not None and self.snapshot is not None and not self.busy
                   and not self.lifecycle_dialog and not self._pending() and self.client.schema.complete
                   and not self._draft_guard().startswith("此範圍"))
        self.leaf_button.configure(state="normal" if enabled else "disabled")

    def _poll_leaf_editor(self):
        if self.pending_leaf_bookmark is not None and not self.busy:
            bookmark, self.pending_leaf_bookmark = self.pending_leaf_bookmark, None
            if not self.lifecycle_dialog and self._bookmark() == bookmark:
                self.open_leaf_editor()

    def open_leaf_editor(self):
        if (not self.selection or not self.snapshot or self.busy or self.lifecycle_dialog
                or self._pending() or not self.client.schema.complete):
            return
        if self._draft_guard().startswith("此範圍"):
            self.status.set(self._draft_guard())
            return
        try:
            edited = parse_editor(self.editor.get())
            if edited.tag != self.selection.node.tag:
                raise EditError("請保留選取根節點名稱與 namespace")
            LeafDialog(self, edited)
        except Exception as exc:
            self._error(exc)

    def jump_reference(self, selection):
        """Locate only an actually visible instance, with no guessed ordinals."""
        from .model import identity
        if not self._preserve_current_draft():
            return
        parent = ""
        for depth, node in enumerate((*selection.ancestors, selection.node), 1):
            matches = [iid for iid in self.tree.get_children(parent) if iid in self.items
                and identity(self.items[iid].node, self.client.schema, self.items[iid].path)
                == identity(node, self.client.schema, selection.path[:depth])]
            if len(matches) != 1:
                self.status.set("引用目標只在此份草稿中或不在 DATA TREE 快照；請在原草稿查看")
                return
            parent = matches[0]
            if depth < len(selection.path):
                self._expand_item(parent)
        self._show_selection(parent)
        self.tree.selection_set(parent)
        self.tree.focus(parent)
        self.tree.see(parent)


class LeafDialog:
    def __init__(self, app, edited):
        from .app import XmlPane
        self.app, self.edited = app, deepcopy(edited)
        self.selection, self.snapshot, self.schema = app.selection, app.snapshot, app.client.schema
        self.original = app.editor.get()
        self.fields = {}
        self.active = None
        self.reference = None
        self.suggestion_values = ()
        self.window = tk.Toplevel(app.root)
        app.lifecycle_dialog = self.window
        self.window.title("表單編輯 leaf — 只更新 XML 草稿")
        self.window.geometry("1060x730")
        self.window.transient(app.root)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=2)
        self.window.rowconfigure(6, weight=2)
        ttk.Label(self.window, text="選取欄位填值；config false、既有 list key 與選取根 leaf-list 的識別值不可直接修改。",
            wraplength=1010).grid(row=0, column=0, sticky="w", padx=10, pady=8)
        self.tree = ttk.Treeview(self.window, columns=("type", "value"), selectmode="browse")
        self.tree.heading("#0", text="節點")
        self.tree.column("#0", width=510)
        self.tree.heading("type", text="型別／狀態")
        self.tree.column("type", width=160)
        self.tree.heading("value", text="值")
        self.tree.column("value", width=260)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=10)
        scroll = ttk.Scrollbar(self.window, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure("readonly", foreground="#64748b")
        self.info = tk.StringVar(self.window)
        ttk.Label(self.window, textvariable=self.info, wraplength=1010).grid(row=2, column=0, sticky="w", padx=10, pady=6)
        bar = ttk.Frame(self.window)
        bar.grid(row=3, column=0, sticky="ew", padx=10)
        bar.columnconfigure(0, weight=1)
        self.value = tk.StringVar(self.window)
        self.entry = ttk.Entry(bar, textvariable=self.value)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<Return>", lambda _e: self.apply_value())
        self.apply_button = ttk.Button(bar, text="套用欄位值", command=self.apply_value)
        self.apply_button.grid(row=0, column=1, padx=6)
        self.reference_button = ttk.Button(bar, text="查看引用目標…", command=self.show_references)
        self.reference_button.grid(row=0, column=2)
        suggestion_bar = ttk.Frame(self.window)
        suggestion_bar.grid(row=4, column=0, sticky="ew", padx=10, pady=(4, 0))
        suggestion_bar.columnconfigure(1, weight=1)
        ttk.Label(suggestion_bar, text="建議值（可直接修改）").grid(row=0, column=0, sticky="w")
        self.suggestion_box = ttk.Combobox(suggestion_bar, state="disabled")
        self.suggestion_box.grid(row=0, column=1, sticky="ew", padx=6)
        self.suggestion_button = ttk.Button(suggestion_bar, text="帶入建議值", command=self.use_suggestion)
        self.suggestion_button.grid(row=0, column=2)
        self.status = tk.StringVar(self.window)
        ttk.Label(self.window, textvariable=self.status, wraplength=1010, foreground="#b45309").grid(row=5, column=0, sticky="w", padx=10, pady=5)
        self.preview = XmlPane(self.window, readonly=True)
        self.preview.grid(row=6, column=0, sticky="nsew", padx=10)
        controls = ttk.Frame(self.window)
        controls.grid(row=7, column=0, sticky="e", padx=10, pady=8)
        ttk.Button(controls, text="取消", command=self.close).pack(side="right")
        ttk.Button(controls, text="更新 XML 草稿", command=self.stage).pack(side="right", padx=6)
        def walk(node, path, indices):
            info = self.schema.lookup(path)
            if info and info.kind in {"leaf", "leaf-list"}:
                readonly = self.readonly(info, indices)
                iid = str(len(self.fields))
                self.fields[iid] = (node, info, indices, readonly)
                secret = self.secret(info)
                self.tree.insert("", "end", iid=iid, text="/".join(map(local, path)),
                    values=(info.type_name + ("（唯讀）" if readonly else ""), "••••••" if secret and node.text else node.text or ""),
                    tags=("readonly",) if readonly else ())
            for index, child in enumerate(children(node)):
                walk(child, path + (child.tag,), indices + (index,))
        walk(self.edited, self.selection.path, ())
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.refresh_preview()
        if self.fields:
            self.tree.selection_set("0")
        else:
            self.status.set("此範圍沒有已存在的 leaf；請使用新增子節點功能")
        app._sync()

    @staticmethod
    def secret(info):
        return any(s in local(info.path[-1]).lower() for s in ("password", "secret", "private-key"))

    def readonly(self, info, indices):
        if info.config is not True or self.snapshot.options.source == "startup":
            return True
        parent = self.schema.lookup(info.path[:-1])
        if parent and info.path[-1] in parent.keys:
            return True
        return info.kind == "leaf-list" and not indices

    def select(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        iid = selected[0]
        if self.active == iid:
            return
        if self.active is not None and self.value.get() != (self.fields[self.active][0].text or ""):
            if not self.apply_value():
                self.tree.selection_set(self.active)
                return
        self.active = iid
        node, info, indices, readonly = self.fields[iid]
        self.value.set(node.text or "")
        self.reference = resolve(self.schema, self.snapshot.data, self.selection, self.edited, indices)
        secret = self.secret(info)
        self.suggestion_values = () if secret else suggested_values(self.schema, info, self.reference.values)
        suggestion_labels = self.suggestion_values or (("（敏感欄位不提供建議值）" if secret else
                                                         "（此欄位沒有 schema／可見資料建議值）"),)
        self.suggestion_box.configure(values=suggestion_labels,
                                      state="readonly" if self.suggestion_values else "disabled")
        self.suggestion_box.set(suggestion_labels[0])
        self.suggestion_button.configure(state="normal" if self.suggestion_values and not readonly else "disabled")
        self.entry.configure(state="disabled" if readonly else "normal", show="•" if secret else "")
        self.apply_button.configure(state="disabled" if readonly else "normal")
        self.reference_button.configure(state="normal" if self.reference.paths else "disabled")
        self.info.set(info.module + " · " + info.kind + " · " + info.type_name + " · Units: " + (info.units or "—")
            + " · Default: " + (", ".join(info.defaults) or "—") + "\n"
            + (info.constraints or info.description)[:400])
        self.status.set("\n".join(dict.fromkeys(self.reference.messages)) or "本機型別檢查；must／when／unique 等完整語意仍由伺服器驗證。")

    def use_suggestion(self):
        index = self.suggestion_box.current()
        if index < 0 or index >= len(self.suggestion_values):
            return
        self.value.set(self.suggestion_values[index])
        self.entry.focus_set()
        self.status.set("已帶入建議值；請確認後按『套用欄位值』。")

    def apply_value(self):
        if self.active is None:
            return True
        node, info, _indices, readonly = self.fields[self.active]
        if readonly:
            return True
        try:
            candidate = scalar_input(self.schema, info, node, self.value.get())
            parent = node.getparent()
            if parent is None:
                self.edited = candidate
            else:
                parent.replace(node, candidate)
            try:
                build_plan(self.selection, etree.tostring(self.edited).decode(), self.schema, self.snapshot.options.source)
            except Exception:
                if parent is None:
                    self.edited = node
                else:
                    parent.replace(candidate, node)
                raise
            node = candidate
            self.fields[self.active] = (node, info, _indices, readonly)
            self.value.set(node.text or "")
            self.tree.set(self.active, "value", "••••••" if self.secret(info) and node.text else node.text or "")
            self.refresh_preview()
            return True
        except Exception as exc:
            self.status.set(str(exc))
            return False

    def refresh_preview(self):
        self.preview.set(serialize_xml(self.edited).decode("utf-8"))

    def show_references(self):
        if not self.reference:
            return
        window = tk.Toplevel(self.window)
        window.title("leafref 引用目標（可見資料）")
        window.geometry("980x420")
        window.transient(self.window)
        window.grab_set()
        ttk.Label(window, text="Path: " + " | ".join(self.reference.paths), wraplength=950).pack(fill="x", padx=8, pady=5)
        tree = ttk.Treeview(window, show="tree", selectmode="browse")
        tree.pack(fill="both", expand=True, padx=8)
        for index, target in enumerate(self.reference.targets):
            tree.insert("", "end", iid=str(index), text=instance_path(target, self.schema))
        def close():
            window.destroy()
            self.window.grab_set()
        def jump():
            ids = tree.selection()
            if not ids:
                return
            target = self.reference.targets[int(ids[0])]
            if not self.stage():
                return
            self.app.jump_reference(target)
        ttk.Button(window, text="保留草稿並跳到目標", command=jump).pack(side="left", padx=8, pady=8)
        ttk.Button(window, text="關閉", command=close).pack(side="right", padx=8)
        window.protocol("WM_DELETE_WINDOW", close)

    def stage(self):
        if not self.apply_value():
            return False
        if (self.app.selection is not self.selection or self.app.snapshot is not self.snapshot
                or self.app.client.schema is not self.schema or self.app.editor.get() != self.original):
            self.status.set("原始快照／草稿已改變，請重新開啟表單")
            return False
        self.app.editor.set(serialize_xml(self.edited).decode("utf-8"))
        self.close()
        self.app._update_preview()
        self.app.status.set("表單已更新本機草稿；尚未送出")
        return True

    def close(self):
        if self.window.winfo_exists():
            self.window.grab_release()
            self.window.destroy()
        self.app.lifecycle_dialog = None
        self.app._sync()
