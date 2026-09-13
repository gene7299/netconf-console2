"""Preview/check/rename profile import with atomic persistence and no connection."""
from copy import deepcopy
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .profile_exchange import read_import, merge_profiles


class ProfileImportFeatures:
    def _build_profile_import(self, tools):
        tools.add_command(label="匯入連線／SSH 帳號設定…", command=self.import_profiles)
        ttk.Button(self.settings_exports, text="匯入設定…", command=self.import_profiles).pack(side="left")

    def import_profiles(self):
        if self.busy or self.lifecycle_dialog or self.reconnect_enabled or self.preferences_error:
            self.status.set("請先完成其他操作／停止自動重連，且設定檔必須可正常保存")
            return
        name = filedialog.askopenfilename(parent=self.root, filetypes=[("GUI 設定 JSON／加密備份", "*.json *.dpapi")])
        if not name:
            return
        try:
            book, encrypted = read_import(name, self._preference_values())
            return ProfileImportDialog(self, book, encrypted)
        except Exception as exc:
            self._error(exc)


class ProfileImportDialog:
    def __init__(self, app, book, encrypted):
        self.app, self.book, self.encrypted = app, deepcopy(book), encrypted
        self.original = deepcopy(app.preferences)
        self.window = tk.Toplevel(app.root)
        app.lifecycle_dialog = self.window
        self.window.title("匯入設定 — 勾選與命名後合併，不會自動連線")
        self.window.geometry("1040x620")
        self.window.transient(app.root)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        ttk.Label(self.window, text="預設不勾選、不帶密碼，清空憑證／金鑰／YANG 路徑，請在本機重新指定。\n"
            "同名預設產生新名稱；只有勾選『取代同名』才會取代。現有連線及上次欄位不變。",
            wraplength=1000).pack(fill="x", padx=10, pady=8)
        self.tree = ttk.Treeview(self.window, columns=("group", "name", "target", "replace"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="勾選")
        self.tree.column("#0", width=55, stretch=False)
        for key, title in (("group", "類型"), ("name", "來源名稱"), ("target", "匯入名稱"), ("replace", "取代同名")):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=95 if key in {"group", "replace"} else 300)
        self.tree.pack(fill="both", expand=True, padx=10)
        self.rows = {}
        for group in ("connections", "accounts"):
            used = set(app.preferences[group])
            for old in book[group]:
                new, number = old, 2
                while new in used:
                    new, number = old + "（匯入 %d）" % number, number + 1
                used.add(new)
                iid = str(len(self.rows))
                self.rows[iid] = [False, group, old, new, False]
                self.tree.insert("", "end", iid=iid, text="☐", values=(group, old, new, "否"))
        self.detail = tk.StringVar(self.window)
        ttk.Label(self.window, textvariable=self.detail, wraplength=1000).pack(fill="x", padx=10, pady=5)
        bar = ttk.Frame(self.window)
        bar.pack(fill="x", padx=10, pady=5)
        self.name = tk.StringVar(self.window)
        self.replace = tk.BooleanVar(self.window, False)
        ttk.Label(bar, text="匯入名稱").pack(side="left")
        ttk.Entry(bar, textvariable=self.name, width=40).pack(side="left", padx=5)
        ttk.Checkbutton(bar, text="取代同名（須確認）", variable=self.replace).pack(side="left")
        ttk.Button(bar, text="套用名稱／選項", command=self.rename).pack(side="left", padx=5)
        options = ttk.Frame(self.window)
        options.pack(fill="x", padx=10, pady=5)
        self.secrets = tk.BooleanVar(self.window, False)
        self.paths = tk.BooleanVar(self.window, False)
        ttk.Checkbutton(options, text="匯入加密備份中的密碼（僅本機）", variable=self.secrets,
                        state="normal" if encrypted else "disabled").pack(side="left")
        ttk.Checkbutton(options, text="保留原檔案路徑（限同一電腦）", variable=self.paths).pack(side="left", padx=10)
        controls = ttk.Frame(self.window)
        controls.pack(fill="x", padx=10, pady=8)
        ttk.Button(controls, text="勾選／取消此項", command=self.toggle).pack(side="left")
        ttk.Button(controls, text="確認匯入選取項目", command=self.apply).pack(side="right")
        ttk.Button(controls, text="取消", command=self.close).pack(side="right", padx=6)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.tree.bind("<Double-1>", lambda _e: self.toggle())
        self.tree.bind("<space>", lambda _e: self.toggle())
        app._sync()

    def selected(self):
        ids = self.tree.selection()
        return ids[0] if ids else None

    def select(self, _event=None):
        iid = self.selected()
        if iid is None:
            return
        _, group, old, new, replace = self.rows[iid]
        self.name.set(new)
        self.replace.set(replace)
        record = self.book[group][old]
        self.detail.set("預覽：%s | host=%s | listen=%s | username=%s\n密碼不顯示；憑證與私鑰檔案本身不在匯入檔內。" % (
            record.get("mode", "SSH 帳號"), record.get("host", ""), record.get("listen_host", ""), record.get("username", "")))

    def toggle(self):
        iid = self.selected()
        if iid is not None:
            self.rows[iid][0] = not self.rows[iid][0]
            self.tree.item(iid, text="☑" if self.rows[iid][0] else "☐")

    def rename(self):
        iid = self.selected()
        if iid is not None:
            row = self.rows[iid]
            row[3], row[4] = self.name.get().strip(), self.replace.get()
            self.tree.item(iid, values=(row[1], row[2], row[3], "是" if row[4] else "否"))

    def apply(self):
        try:
            if self.app.preferences != self.original or self.app.busy:
                raise ValueError("目前設定清單已改變，請關閉後重新匯入")
            self.rename()
            choices = [tuple(row[1:]) for row in self.rows.values() if row[0]]
            result = merge_profiles(self.original, self.book, choices,
                include_secrets=self.encrypted and self.secrets.get(), keep_paths=self.paths.get())
            if not messagebox.askyesno("確認匯入", "合併 %d 組設定%s？不會連線或修改設備。" % (
                    len(choices), "，包含明確選取的同名取代" if any(c[3] for c in choices) else ""), parent=self.window, default="no"):
                return
            if self.app.preferences_store:
                self.app.preferences_store.save(result)
            self.app.preferences = result
            self.app._profile_lists()
            self.app.status.set("已匯入設定；請載入設定組、確認帳密與本機憑證路徑後再自行連線。")
            self.close()
        except Exception as exc:
            self.detail.set(str(exc))

    def close(self):
        self.app.lifecycle_dialog = None
        self.window.grab_release()
        self.window.destroy()
        self.app._sync()
