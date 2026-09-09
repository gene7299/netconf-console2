"""Modal profile catalog management; never display saved password values."""
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk


class ProfileManager:
    def __init__(self, app, group):
        self.app, self.group = app, group
        self.window = tk.Toplevel(app.root)
        self.window.title("管理連線設定組" if group == "connections" else "管理 SSH 帳號組")
        self.window.geometry("820x470")
        self.window.minsize(620, 360)
        self.window.transient(app.root)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        self.search = tk.StringVar(self.window)
        search_row = ttk.Frame(self.window, padding=10)
        search_row.grid(row=0, column=0, sticky="ew")
        ttk.Label(search_row, text="搜尋名稱").pack(side="left", padx=(0, 8))
        ttk.Entry(search_row, textvariable=self.search).pack(side="left", fill="x", expand=True)
        frame = ttk.Frame(self.window, padding=(10, 0))
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.list = tk.Listbox(frame, selectmode="extended", exportselection=False, font=("Segoe UI", 10))
        self.list.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.list.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.list.configure(yscrollcommand=bar.set)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.list.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.list.configure(xscrollcommand=horizontal.set)
        self.description = tk.StringVar(self.window)
        ttk.Label(self.window, textvariable=self.description, wraplength=760).grid(row=2, column=0, sticky="w", padx=10, pady=8)
        ttk.Label(self.window, text="Ctrl / Shift 可多選。刪除只移除本機清單項目，不刪除 RU 帳號、憑證或金鑰檔。",
                  wraplength=760).grid(row=3, column=0, sticky="w", padx=10)
        buttons = ttk.Frame(self.window, padding=10)
        buttons.grid(row=4, column=0, sticky="ew")
        ttk.Button(buttons, text="載入並編輯", command=self.load).pack(side="left")
        ttk.Button(buttons, text="重新命名", command=self.rename).pack(side="left", padx=6)
        ttk.Button(buttons, text="刪除選取項目…", command=self.delete).pack(side="left")
        ttk.Button(buttons, text="關閉", command=self.window.destroy).pack(side="right")
        self.list.bind("<<ListboxSelect>>", self.describe)
        self.search.trace_add("write", lambda *_args: self.refresh())
        self.refresh()
        self.window.grab_set()

    def selected(self):
        return [self.list.get(index) for index in self.list.curselection()]

    def refresh(self, selected=None):
        query = self.search.get().casefold()
        self.list.delete(0, "end")
        for name in self.app.preferences[self.group]:
            if query in name.casefold():
                self.list.insert("end", name)
                if name == selected:
                    self.list.selection_set(self.list.size() - 1)
        self.describe()

    def describe(self, _event=None):
        names = self.selected()
        prefix = "共 %d 組；顯示 %d 組；已選 %d 組。" % (len(self.app.preferences[self.group]), self.list.size(), len(names))
        if len(names) == 1:
            record = self.app.preferences[self.group][names[0]]
            if self.group == "accounts":
                detail = "帳號：%s；Private key：%s；已存密碼：%s" % (
                    record.get("username", ""), "有" if record.get("ssh_key") else "無", "有" if record.get("password") else "無")
            else:
                call_home = "Call Home" in record.get("mode", "")
                detail = "%s | %s:%s | 帳號：%s" % (record.get("mode", ""),
                    record.get("listen_host" if call_home else "host", ""),
                    record.get("listen_port" if call_home else "port", ""), record.get("username", ""))
            prefix += "\n" + detail
        self.description.set(prefix)

    def load(self):
        names = self.selected()
        if len(names) != 1:
            self.description.set("請選取一組設定以載入編輯。")
            return
        variable = self.app.connection_name if self.group == "connections" else self.app.account_name
        variable.set(names[0])
        if self.group == "connections":
            self.app._choose_connection()
        else:
            self.app._choose_account()
        self.window.destroy()
        self.app.status.set("已載入設定，可在上排編輯後按儲存；尚未連線。")

    def rename(self):
        names = self.selected()
        if len(names) != 1:
            self.description.set("請選取一組設定以重新命名。")
            return
        name = simpledialog.askstring("重新命名", "新名稱：", initialvalue=names[0], parent=self.window)
        if name is not None and self.app.change_profile_catalog(self.group, names, name):
            self.refresh(name.strip())

    def delete(self):
        names = self.selected()
        if not names:
            self.description.set("請先選取要刪除的項目。")
            return
        question = "刪除選取的 %d 組本機清單項目？\n\n%s\n\n目前欄位與其他連線快照不受影響，可能仍保存相同帳密。\n關閉視窗不會自動加回；再次手動連線／儲存可重新加入。\n刪除會立即保存，無復原按鈕；若需備份，請先取消並至進階頁匯出。" % (
            len(names), "\n".join(names[:8]) + ("\n…" if len(names) > 8 else ""))
        if messagebox.askyesno("確認刪除", question, parent=self.window, default="no"):
            if self.app.change_profile_catalog(self.group, names):
                self.refresh()
