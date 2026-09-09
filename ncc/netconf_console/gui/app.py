"""Native ttk Windows UI. All transport/schema work runs off the UI thread."""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from lxml import etree

from ..session import ConnectionSettings
from ..trace import redact_secrets
from ..xmloutput import serialize_xml
from . import VERSION
from .client import GuiClient, ReadOptions, Snapshot
from .model import EditError, Selection, build_plan, children, identity, local, node_style, parse_editor, xml_spans
from .preferences import SSH_FIELDS, PreferencesStore, change_profiles, empty_book, public_book, remember_account, remember_connection
from .features import WorkspaceFeatures
from .windows import icon_path, set_app_id

COLORS = {"default": "#fff0bf", "schema_default": "#fff8de", "state": "#e4edf5",
          "changed": "#ffdcc5", "unknown": "#eee9f2"}
MODES = ("Direct SSH", "Direct TLS", "SSH Call Home", "TLS Call Home")


class XmlPane(ttk.Frame):
    def __init__(self, parent, readonly=False):
        super().__init__(parent)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.gutter = tk.Canvas(self, width=45, bg="#f0f3f7", highlightthickness=0)
        self.gutter.grid(row=0, column=0, sticky="ns")
        self.text = tk.Text(self, wrap="word", undo=not readonly, font=("Consolas", 11),
                            bg="#ffffff", fg="#253247", insertbackground="#0b6b9e",
                            selectbackground="#beddf2", relief="flat", padx=9, pady=7,
                            tabs=(32,), borderwidth=0, width=60, height=8)
        self.text.grid(row=0, column=1, sticky="nsew")
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self._scroll)
        self.vbar.grid(row=0, column=2, sticky="ns")
        ttk.Scrollbar(self, orient="horizontal", command=self.text.xview).grid(row=1, column=1, sticky="ew")
        self.text.configure(yscrollcommand=self._yscroll)
        self.text.configure(xscrollcommand=self._xscroll)
        self.hbar = self.grid_slaves(row=1, column=1)[0]
        self.text.tag_configure("xml", foreground="#17689b")
        self.text.tag_configure("comment", foreground="#798796")
        for name, color in COLORS.items():
            self.text.tag_configure(name, background=color)
        self.text.tag_raise("changed")
        self.text.bind("<Configure>", lambda _event: self.after_idle(self.line_numbers))
        self.text.bind("<KeyRelease>", lambda _event: self.after_idle(self.line_numbers), add=True)
        self.text.bind("<Control-a>", self._select_all)
        self.readonly = readonly
        if readonly:
            self.text.configure(state="disabled")

    def _select_all(self, _event):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _xscroll(self, first, last):
        if hasattr(self, "hbar"):
            self.hbar.set(first, last)

    def _yscroll(self, first, last):
        self.vbar.set(first, last)
        self.after_idle(self.line_numbers)

    def _scroll(self, *args):
        self.text.yview(*args)
        self.line_numbers()

    def line_numbers(self):
        if not self.winfo_exists():
            return
        self.gutter.delete("all")
        index = self.text.index("@0,0")
        for _ in range(200):
            box = self.text.dlineinfo(index)
            if box is None:
                break
            self.gutter.create_text(37, box[1], anchor="ne", text=index.split(".")[0],
                                    font=("Consolas", 10), fill="#8391a1")
            index = self.text.index(index + "+1line")

    def get(self):
        return self.text.get("1.0", "end-1c")

    def set(self, value):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        self.text.edit_reset()
        self.text.edit_modified(False)
        if self.readonly:
            self.text.configure(state="disabled")
        self.syntax()
        self.after_idle(self.line_numbers)

    def syntax(self):
        from .model import XML_TOKEN
        text = self.get()
        for name in ("xml", "comment", *COLORS):
            self.text.tag_remove(name, "1.0", "end")
        for match in XML_TOKEN.finditer(text):
            name = "comment" if match.group().startswith("<!--") else "xml"
            self.text.tag_add(name, "1.0+%dc" % match.start(), "1.0+%dc" % match.end())

    def annotate(self, root, path, schema, changed=()):
        paths = {}
        def walk(node, current):
            paths[node] = current
            for child in children(node):
                walk(child, current + (child.tag,))
        walk(root, path)
        for node, start, end, value_start, value_end in xml_spans(self.get(), root):
            name = node_style(node, paths[node], schema, changed)
            if name:
                first, last = (start, end) if children(node) or value_start == value_end else (value_start, value_end)
                self.text.tag_add(name, "1.0+%dc" % first, "1.0+%dc" % last)


class NetconfWindow(WorkspaceFeatures):
    def __init__(self, root: tk.Tk, client=None, *, persist=True, store=None):
        self.root = root
        self.client = client or GuiClient()
        self.events = queue.Queue()
        self.busy = False
        self.job_name = ""
        self.close_requested = False
        self.closed = False
        self.snapshot: Snapshot | None = None
        self.selection: Selection | None = None
        self.selection_iid = ""
        self.plan = None
        self.items = {}
        self.baseline_text = ""
        self.edit_timer = None
        self.uncertain = False
        self.demo = False
        self.last_sent_xml = ""
        self.preferences_store = (store or PreferencesStore()) if persist else None
        self.preferences = empty_book()
        self.preferences_timer = None
        self.restoring_preferences = False
        self.preferences_error = ""
        self.reconnect_settings = None
        self.reconnect_schema_dir = ""
        self.reconnect_enabled = False
        self.reconnect_due = None
        self.reconnect_delay = 2
        self.root.title("NETCONF Console GUI " + VERSION)
        width = min(1440, max(1000, self.root.winfo_screenwidth() - 70))
        height = min(900, max(680, self.root.winfo_screenheight() - 100))
        self.root.geometry("%dx%d" % (width, height))
        self.root.minsize(min(1120, width), min(780, height))
        self.root.configure(bg="#f3f6fa")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        icon = icon_path()
        if icon.is_file():
            try:
                self.root.iconbitmap(str(icon))
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TFrame", background="#f3f6fa")
        style.configure("TLabel", background="#f3f6fa", font=("Segoe UI", 10))
        style.configure("TLabelframe", background="#f3f6fa")
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=26)
        style.configure("TButton", padding=(9, 3))
        self.vars = {}
        for key, value in {
            "mode": MODES[0], "host": "192.168.9.9", "port": "830",
            "listen_host": "0.0.0.0", "listen_port": "4334", "source": "running",
            "username": "", "password": "", "ssh_key": "", "known_hosts": "",
            "ssh_auth": "auto", "key_passphrase": "",
            "cert": "", "tls_key": "", "trusted_ca": "", "crl": "", "tls_server_name": "",
            "tls_version": "auto", "timeout": "30", "rpc_timeout": "30", "schema_dir": "",
            "bind": "", "netconf_version": "auto",
        }.items():
            self.vars[key] = tk.StringVar(root, value)
        for key, value in {"defaults": False, "state": False, "hostkey_verify": False,
                           "verify_hostname": False, "allow_agent": True, "look_for_keys": True,
                           "auto_reconnect": False, "connection_hidden": False}.items():
            self.vars[key] = tk.BooleanVar(root, value)
        self.status = tk.StringVar(root, "尚未連線 · 選擇連線方式並填入認證資料")
        self.schema_status = tk.StringVar(root, "YANG：未載入")
        self.path_status = tk.StringVar(root, "請先連線，再選擇左側節點")
        self.preview_status = tk.StringVar(root, "尚無變更，不會送出任何設定")
        self.wrap_xml = tk.BooleanVar(root, True)
        self.connection_name = tk.StringVar(root, "")
        self.account_name = tk.StringVar(root, "")
        self._build()
        self._build_features()
        self._restore_preferences()
        for variable in (*self.vars.values(), self.wrap_xml, self.connection_name, self.account_name):
            variable.trace_add("write", self._schedule_preferences)
        self.root.after(80, self._poll)
        self._sync()

    def _field(self, parent, label, name, row, column, width=20, secret=False):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(7, 4), pady=2)
        widget = ttk.Entry(parent, textvariable=self.vars[name], width=width, show="●" if secret else "")
        widget.grid(row=row, column=column + 1, sticky="ew", padx=(0, 9), pady=2)
        return widget

    def _path_field(self, parent, label, name, row, column=0, directory=False):
        entry = self._field(parent, label, name, row, column, 43)
        ttk.Button(parent, text="瀏覽…", command=lambda: self._browse(name, directory)).grid(row=row, column=column+2, padx=(0, 8))
        parent.columnconfigure(column+1, weight=1)
        return entry

    def _browse(self, name, directory=False):
        value = filedialog.askdirectory(parent=self.root) if directory else filedialog.askopenfilename(parent=self.root)
        if value:
            self.vars[name].set(value)

    def _build(self):
        self.connection_panel = ttk.Frame(self.root)
        self.connection_panel.pack(fill="x")
        presets = ttk.Frame(self.connection_panel)
        presets.pack(fill="x", padx=13, pady=(4, 0))
        ttk.Label(presets, text="連線設定組").pack(side="left", padx=(0, 8))
        self.connection_box = ttk.Combobox(presets, textvariable=self.connection_name, width=55)
        self.connection_box.pack(side="left", fill="x", expand=True)
        self.connection_box.bind("<<ComboboxSelected>>", self._choose_connection)
        self.save_connection_button = ttk.Button(presets, text="儲存連線設定", command=self.save_connection)
        self.save_connection_button.pack(side="left", padx=6)
        self.new_connection_button = ttk.Button(presets, text="另存新組", command=self.new_connection)
        self.new_connection_button.pack(side="left")
        self.manage_connection_button = ttk.Button(presets, text="管理…", command=lambda: self.manage_profiles("connections"))
        self.manage_connection_button.pack(side="left", padx=(6, 0))
        connection = ttk.LabelFrame(self.connection_panel, text="連線設定", padding=(6, 1))
        connection.pack(fill="x", padx=12, pady=(4, 2))
        ttk.Label(connection, text="模式").grid(row=0, column=0, padx=7)
        self.mode_box = ttk.Combobox(connection, textvariable=self.vars["mode"], values=MODES, state="readonly", width=18)
        self.mode_box.grid(row=0, column=1, padx=4)
        self.mode_box.bind("<<ComboboxSelected>>", self._mode_changed)
        self.host_entry = self._field(connection, "Server host", "host", 0, 2, 22)
        self.port_entry = self._field(connection, "Port", "port", 0, 4, 7)
        ttk.Label(connection, text="Source / Target").grid(row=0, column=6, padx=6)
        self.source_box = ttk.Combobox(connection, textvariable=self.vars["source"], values=("running", "candidate", "startup"), state="readonly", width=10)
        self.source_box.grid(row=0, column=7, padx=4)
        self.source_box.bind("<<ComboboxSelected>>", self._options_changed)
        # The Windows Vista ttk theme ignores button background colours.
        # Use a native Tk button so the primary action stays visibly blue.
        self.connect_button = tk.Button(
            connection, text="連線 / 開始監聽", command=self.connect,
            font=("Segoe UI", 12, "bold"), padx=14, pady=6,
            background="#0969da", foreground="#ffffff",
            activebackground="#0756b3", activeforeground="#ffffff",
            disabledforeground="#64748b", relief="flat", borderwidth=0,
            highlightthickness=2, highlightbackground="#f3f6fa", highlightcolor="#063b80",
            cursor="hand2", takefocus=True,
        )
        self.connect_button.bind("<Return>", lambda _event: self.connect_button.invoke())
        self.connect_button.grid(row=0, column=8, padx=8, pady=1, sticky="ew")
        self.listen_entry = self._field(connection, "Listen host", "listen_host", 1, 0, 18)
        self.listen_port_entry = self._field(connection, "Listen port", "listen_port", 1, 2, 8)
        self._field(connection, "等待秒數", "timeout", 1, 4, 7)
        self._field(connection, "RPC timeout", "rpc_timeout", 1, 6, 10)
        self.disconnect_button = ttk.Button(connection, text="中斷 / 取消等待", command=self.disconnect)
        self.disconnect_button.grid(row=1, column=8, padx=8)
        connection.columnconfigure(3, weight=1)
        auth = ttk.Notebook(connection)
        auth.grid(row=2, column=0, columnspan=9, sticky="ew", padx=5, pady=(2, 3))
        self.auth_tabs = auth
        ssh, tls, advanced = (ttk.Frame(auth, padding=(4, 2)) for _ in range(3))
        auth.add(ssh, text="SSH 認證")
        auth.add(tls, text="TLS 憑證")
        auth.add(advanced, text="進階 / YANG schema")
        accounts = ttk.Frame(ssh)
        accounts.grid(row=0, column=0, columnspan=7, sticky="ew", padx=7, pady=3)
        ttk.Label(accounts, text="SSH 帳號組").pack(side="left", padx=(0, 8))
        self.account_box = ttk.Combobox(accounts, textvariable=self.account_name, width=30)
        self.account_box.pack(side="left", fill="x", expand=True)
        self.account_box.bind("<<ComboboxSelected>>", self._choose_account)
        self.save_account_button = ttk.Button(accounts, text="儲存帳號", command=self.save_account)
        self.save_account_button.pack(side="left", padx=6)
        self.new_account_button = ttk.Button(accounts, text="新增帳號", command=self.new_account)
        self.new_account_button.pack(side="left")
        self.manage_account_button = ttk.Button(accounts, text="管理…", command=lambda: self.manage_profiles("accounts"))
        self.manage_account_button.pack(side="left", padx=(6, 0))
        self._field(ssh, "帳號", "username", 1, 0, 19)
        self._field(ssh, "密碼（加密儲存）", "password", 1, 2, 23, True)
        self._path_field(ssh, "Private key", "ssh_key", 1, 4)
        self._path_field(ssh, "Known hosts", "known_hosts", 2, 0)
        ttk.Checkbutton(ssh, text="驗證 SSH host key", variable=self.vars["hostkey_verify"]).grid(row=2, column=3, sticky="w")
        ttk.Checkbutton(ssh, text="SSH agent", variable=self.vars["allow_agent"]).grid(row=2, column=4, sticky="w")
        ttk.Checkbutton(ssh, text="搜尋本機金鑰", variable=self.vars["look_for_keys"]).grid(row=2, column=5, sticky="w")
        ssh_options = ttk.Frame(auth, padding=(4, 2))
        auth.add(ssh_options, text="SSH 認證方式 / 私鑰密碼")
        ttk.Label(ssh_options, text="認證方式").grid(row=0, column=0, padx=7, sticky="w")
        ttk.Combobox(ssh_options, textvariable=self.vars["ssh_auth"], values=("auto", "password", "private-key", "agent"),
                     state="readonly", width=18).grid(row=0, column=1, sticky="ew", padx=(0, 9))
        self._field(ssh_options, "私鑰密碼（passphrase）", "key_passphrase", 0, 2, 23, True)
        ttk.Label(ssh_options, text="auto：依序嘗試私鑰／本機金鑰／Agent／登入密碼；其他模式只使用指定方法。\n"
                  "登入密碼及私鑰路徑在 SSH 認證分頁。私鑰密碼不會當登入密碼送出；舊加密私鑰設定請重新填此欄位。"
                  ).grid(row=1, column=0, columnspan=4, padx=7, pady=4, sticky="w")
        self._path_field(tls, "Client cert", "cert", 0)
        self._path_field(tls, "Private key", "tls_key", 0, 3)
        self._path_field(tls, "Trusted CA", "trusted_ca", 1)
        self._field(tls, "Server name / SAN", "tls_server_name", 1, 3, 27)
        ttk.Checkbutton(tls, text="驗證名稱", variable=self.vars["verify_hostname"]).grid(row=1, column=5, sticky="w")
        self._path_field(advanced, "本機 YANG 資料夾（備援）", "schema_dir", 0, directory=True)
        self._path_field(advanced, "CRL（選用）", "crl", 0, 3)
        self._field(advanced, "Bind address（選用）", "bind", 1, 0, 24)
        ttk.Label(advanced, text="TLS version").grid(row=1, column=2, padx=6)
        ttk.Combobox(advanced, textvariable=self.vars["tls_version"], values=("auto", "1.2", "1.3"), state="readonly", width=8).grid(row=1, column=3)
        ttk.Label(advanced, text="NETCONF version").grid(row=1, column=4, padx=6)
        ttk.Combobox(advanced, textvariable=self.vars["netconf_version"], values=("auto", "1.0", "1.1"), state="readonly", width=8).grid(row=1, column=5)
        exports = ttk.Frame(advanced)
        exports.grid(row=2, column=0, columnspan=6, sticky="w", padx=7, pady=4)
        ttk.Button(exports, text="匯出設定 JSON（不含密碼）", command=self.export_settings).pack(side="left")
        ttk.Button(exports, text="匯出加密備份（含密碼）", command=lambda: self.export_settings(encrypted=True)).pack(side="left", padx=8)
        self.connection_divider = ttk.Frame(self.root)
        self.connection_divider.pack(fill="x", padx=12, pady=2)
        self.toggle_button = ttk.Button(self.connection_divider, text="▲ 隱藏連線設定", command=self.toggle_connection)
        self.toggle_button.pack(side="left")
        ttk.Checkbutton(self.connection_divider, text="斷線後自動重連", variable=self.vars["auto_reconnect"],
                        command=self._reconnect_option_changed).pack(side="left", padx=12)
        self.retry_stop_button = ttk.Button(self.connection_divider, text="停止重連", command=self.stop_reconnect)
        self.retry_stop_button.pack(side="left")
        main = ttk.Panedwindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=12, pady=(3, 5))
        left = ttk.Frame(main, padding=(2, 0, 8, 0))
        right = ttk.Panedwindow(main, orient="vertical")
        main.add(left, weight=1)
        main.add(right, weight=4)
        left.rowconfigure(4, weight=1)
        left.columnconfigure(0, weight=1)
        tree_header = ttk.Frame(left)
        tree_header.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        ttk.Label(tree_header, text="DATA TREE", font=("Segoe UI", 11, "bold")).pack(side="left")
        self.defaults_check = ttk.Checkbutton(left, text="包含 YANG default 值", variable=self.vars["defaults"], command=self._options_changed)
        self.defaults_check.grid(row=1, column=0, sticky="w", pady=2)
        self.state_check = ttk.Checkbutton(left, text="包含 config false（唯讀）", variable=self.vars["state"], command=self._options_changed)
        self.state_check.grid(row=2, column=0, sticky="w", pady=2)
        tree_buttons = ttk.Frame(left)
        tree_buttons.grid(row=3, column=0, sticky="ew", pady=5)
        self.read_all_button = ttk.Button(tree_buttons, text="重新讀取全部", command=self.read_all)
        self.read_all_button.pack(side="left")
        self.schema_button = ttk.Button(tree_buttons, text="更新 YANG", command=self.refresh_schema)
        self.schema_button.pack(side="left", padx=5)
        self.save_tree_button = ttk.Button(tree_buttons, text="匯出XML", command=self.save_tree_xml)
        self.save_tree_button.pack(side="left")
        tree_frame = ttk.Frame(left)
        tree_frame.grid(row=4, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        self.tree.column("#0", width=310, minwidth=230)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        for name, color in COLORS.items():
            self.tree.tag_configure(name, background=color)
        self.tree.bind("<<TreeviewOpen>>", self._expand)
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<Double-Button-1>", self._tree_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        ttk.Label(left, textvariable=self.schema_status, wraplength=305).grid(row=5, column=0, sticky="w", pady=6)
        self.details_button = ttk.Button(left, text="連線 / Schema 詳情", command=self.show_details)
        self.details_button.grid(row=6, column=0, sticky="w")
        top = ttk.Frame(right)
        bottom = ttk.Frame(right)
        right.add(top, weight=3)
        right.add(bottom, weight=2)
        # Notebook pages have different requested heights; keep both XML panes
        # usable after a window resize instead of letting the tallest tab win.
        right.bind("<Configure>", lambda event: right.sashpos(0, int(event.height * 0.53)))
        top.rowconfigure(3, weight=1)
        top.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(top)
        toolbar.grid(row=0, column=0, sticky="ew", pady=4)
        ttk.Label(toolbar, text="讀取 / 編輯 XML", font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Checkbutton(toolbar, text="自動折行", variable=self.wrap_xml, command=self._wrap_changed).pack(side="left", padx=10)
        self.refresh_button = ttk.Button(toolbar, text="重新讀取", command=self.refresh_selected)
        self.refresh_button.pack(side="right", padx=3)
        self.format_button = ttk.Button(toolbar, text="Pretty", command=self.format_editor)
        self.format_button.pack(side="right", padx=3)
        self.revert_button = ttk.Button(toolbar, text="還原", command=self.revert)
        self.revert_button.pack(side="right", padx=3)
        self.save_button = ttk.Button(toolbar, text="匯出XML", command=self.save_xml)
        self.save_button.pack(side="right", padx=3)
        ttk.Label(top, textvariable=self.path_status, foreground="#526679", wraplength=850).grid(row=1, column=0, sticky="w", pady=(0, 4))
        legend = ttk.Frame(top)
        legend.grid(row=2, column=0, sticky="w", pady=(0, 5))
        for name, text in (("default", "伺服器 default"), ("schema_default", "等於 schema default"),
                           ("state", "config false"), ("changed", "修改中"), ("unknown", "schema 未知")):
            tk.Label(legend, text="  " + text + "  ", bg=COLORS[name], fg="#334457", font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self.editor = XmlPane(top)
        self.editor.grid(row=3, column=0, sticky="nsew")
        self.editor.text.bind("<<Modified>>", self._edited)
        bottom.rowconfigure(2, weight=1)
        bottom.columnconfigure(0, weight=1)
        sendbar = ttk.Frame(bottom)
        sendbar.grid(row=0, column=0, sticky="ew", pady=(9, 3))
        ttk.Label(sendbar, text="實際送出 XML", font=("Segoe UI", 11, "bold")).pack(side="left")
        self.send_button = ttk.Button(sendbar, text="送出修改…", command=self.send)
        self.send_button.pack(side="right", padx=4)
        ttk.Label(bottom, textvariable=self.preview_status, foreground="#925127", wraplength=870).grid(row=1, column=0, sticky="w", pady=(0, 4))
        self.output_tabs = ttk.Notebook(bottom)
        self.output_tabs.grid(row=2, column=0, sticky="nsew")
        self.preview = XmlPane(self.output_tabs, readonly=True)
        self.reply = XmlPane(self.output_tabs, readonly=True)
        self.output_tabs.add(self.preview, text="待送出 RPC（唯讀）")
        self.output_tabs.add(self.reply, text="最後 RPC 回應")
        foot = ttk.Frame(self.root)
        # Reserve the status row before the expandable workspace. At Windows
        # 125% scaling, the XML panes' requested sizes must not hide progress.
        foot.pack(side="bottom", fill="x", padx=13, pady=(1, 7), before=main)
        ttk.Label(foot, textvariable=self.status, wraplength=1200).pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(foot, mode="indeterminate", length=135)
        self.progress.pack(side="right", padx=7)

    def _preference_values(self):
        return {**{name: variable.get() for name, variable in self.vars.items()},
                "wrap_xml": self.wrap_xml.get()}

    def _set_preference_values(self, values):
        self.restoring_preferences = True
        try:
            if "username" in values:
                # Old profiles must not inherit a secret/auth mode from another account.
                self.vars["ssh_auth"].set(values.get("ssh_auth", "auto"))
                self.vars["key_passphrase"].set(values.get("key_passphrase", ""))
            for name, variable in self.vars.items():
                value = values.get(name)
                if type(value) is type(variable.get()):
                    variable.set(value)
            if type(values.get("wrap_xml")) is bool:
                self.wrap_xml.set(values["wrap_xml"])
            # Do not call _mode_changed: custom saved ports must stay exact.
            self.auth_tabs.select(1 if "TLS" in self.vars["mode"].get() else 0)
            self._wrap_changed()
            self._connection_visibility()
        finally:
            self.restoring_preferences = False

    def _profile_lists(self):
        self.connection_box.configure(values=tuple(self.preferences["connections"]))
        self.account_box.configure(values=tuple(self.preferences["accounts"]))

    def export_settings(self, encrypted=False):
        if self.preferences_error:
            self._error(ValueError(self.preferences_error))
            return
        filename = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".dpapi" if encrypted else ".json",
            initialfile="netconf-gui-backup.dpapi" if encrypted else "netconf-gui-settings.json",
            filetypes=[("Encrypted GUI backup", "*.dpapi")] if encrypted else [("JSON (no passwords)", "*.json")])
        if not filename:
            return
        try:
            if self.preferences_store and Path(filename).resolve() == self.preferences_store.path.resolve():
                raise ValueError("請另選匯出檔名，不能覆寫正在使用的設定檔。")
            book = deepcopy(self.preferences)
            book["last"] = {"values": self._preference_values(), "connection": self.connection_name.get(),
                            "account": self.account_name.get()}
            if encrypted:
                PreferencesStore(filename).save(book)
            else:
                Path(filename).write_text(json.dumps(public_book(book), ensure_ascii=False, indent=2), encoding="utf-8")
            self.status.set("已匯出" + ("加密備份（限原 Windows 使用者／電腦）" if encrypted else "設定（不含密碼；仍含帳號、主機與路徑）") + "：" + filename)
        except Exception as exc:
            self._error(exc)

    def _connection_visibility(self):
        hidden = self.vars["connection_hidden"].get()
        if hidden:
            self.connection_panel.pack_forget()
        else:
            self.connection_panel.pack(fill="x", before=self.connection_divider)
        self.toggle_button.configure(text="▼ 顯示連線設定" if hidden else "▲ 隱藏連線設定")

    def toggle_connection(self):
        self.vars["connection_hidden"].set(not self.vars["connection_hidden"].get())
        self._connection_visibility()

    def _restore_preferences(self):
        if self.preferences_store is None:
            return
        try:
            self.preferences = self.preferences_store.load()
            last = self.preferences["last"]
            self._set_preference_values(last.get("values", {}))
            self.connection_name.set(last.get("connection", ""))
            self.account_name.set(last.get("account", ""))
            self._profile_lists()
        except Exception:
            self.preferences_error = "無法解密或讀取 GUI 設定；原檔保留，本次不會覆寫。"
            self.status.set(self.preferences_error)

    def _schedule_preferences(self, *_args):
        if self.closed or self.demo or self.restoring_preferences or self.preferences_store is None:
            return
        if self.preferences_timer is not None:
            self.root.after_cancel(self.preferences_timer)
        self.preferences_timer = self.root.after(600, self._save_preferences)

    def _remember_current(self):
        if self.demo:
            return
        values = self._preference_values()
        if "SSH" in values["mode"]:
            name = self.account_name.get().strip()
            self.account_name.set(remember_account(self.preferences, values, name))
        name = self.connection_name.get().strip()
        self.connection_name.set(remember_connection(self.preferences, values, name))
        self._profile_lists()

    def _save_preferences(self, *, remember=False, notify=False):
        if self.preferences_timer is not None:
            self.root.after_cancel(self.preferences_timer)
            self.preferences_timer = None
        if self.demo:
            return True
        if remember:
            self._remember_current()
        self.preferences["last"] = {"values": self._preference_values(),
                                    "connection": self.connection_name.get(), "account": self.account_name.get()}
        if self.preferences_store is None:
            return True
        try:
            if self.preferences_error:
                raise ValueError(self.preferences_error)
            self.preferences_store.save(self.preferences)
            return True
        except Exception:
            text = self.preferences_error or "設定無法加密儲存；請檢查使用者資料夾權限。沒有改用明文儲存。"
            self.status.set(text)
            if notify:
                messagebox.showerror("GUI 設定儲存失敗", text, parent=self.root)
            return False

    def save_connection(self):
        if self.busy or self.client.connected:
            return
        values = self._preference_values()
        self.connection_name.set(remember_connection(self.preferences, values, self.connection_name.get()))
        if "SSH" in values["mode"]:
            self.account_name.set(remember_account(self.preferences, values, self.account_name.get()))
        self._profile_lists()
        if self._save_preferences(notify=True):
            self.status.set("已儲存連線設定（包含加密認證資料）")

    def save_account(self):
        if self.busy or self.client.connected:
            return
        if not self.vars["username"].get().strip():
            self.status.set("請先輸入 SSH 帳號")
            return
        self.account_name.set(remember_account(self.preferences, self._preference_values(), self.account_name.get()))
        self._profile_lists()
        if self._save_preferences(notify=True):
            self.status.set("已儲存 SSH 帳號與加密密碼")

    def _choose_connection(self, _event=None):
        if self.busy or self.client.connected:
            return
        values = self.preferences["connections"].get(self.connection_name.get())
        if values is not None:
            self._set_preference_values(values)
            account = {key: values[key] for key in SSH_FIELDS if key in values}
            self.account_name.set(next((name for name, item in self.preferences["accounts"].items() if item == account), ""))
            self._save_preferences()
            self._sync()

    def _choose_account(self, _event=None):
        if self.busy or self.client.connected:
            return
        values = self.preferences["accounts"].get(self.account_name.get())
        if values is not None:
            self._set_preference_values(values)
            self._save_preferences()

    def new_connection(self):
        self._save_preferences()
        self.connection_name.set("")
        self.connection_box.focus_set()
        self.status.set("沿用目前欄位；輸入新名稱與設定，再按儲存或連線。")

    def new_account(self):
        self.account_name.set("")
        for name in ("username", "password", "ssh_key", "key_passphrase"):
            self.vars[name].set("")
        self.vars["ssh_auth"].set("auto")
        self.account_box.focus_set()

    def manage_profiles(self, group):
        if self.busy or self.client.connected or self.reconnect_due is not None:
            return
        from .profiles import ProfileManager
        ProfileManager(self, group)

    def change_profile_catalog(self, group, names, new_name=None):
        if self.busy or self.client.connected or self.reconnect_due is not None:
            return False
        try:
            if self.preferences_error:
                raise ValueError(self.preferences_error)
            book = deepcopy(self.preferences)
            book["last"] = {"values": self._preference_values(), "connection": self.connection_name.get(),
                            "account": self.account_name.get()}
            changed = change_profiles(book, group, names, new_name)
            # Commit ciphertext before changing UI state; failures leave both
            # catalogs intact and the debounced save cannot undo the mutation.
            if self.preferences_store is not None and not self.demo:
                self.preferences_store.save(changed)
            self.preferences = changed
            self.connection_name.set(changed["last"].get("connection", ""))
            self.account_name.set(changed["last"].get("account", ""))
            self._profile_lists()
            self.status.set("已重新命名並儲存。" if new_name is not None else "已刪除選取的本機清單項目；目前欄位及其他快照保留。")
            return True
        except Exception as exc:
            self._error(exc)
            return False

    def _mode_changed(self, _event=None):
        tls = "TLS" in self.vars["mode"].get()
        if self.vars["port"].get() in {"830", "6513"}:
            self.vars["port"].set("6513" if tls else "830")
        if self.vars["listen_port"].get() in {"4334", "4335"}:
            self.vars["listen_port"].set("4335" if tls else "4334")
        self.auth_tabs.select(1 if tls else 0)
        self._sync()

    def _wrap_changed(self):
        for pane in (self.editor, self.preview, self.reply):
            pane.text.configure(wrap="word" if self.wrap_xml.get() else "none")
            pane.line_numbers()

    def settings(self):
        value = lambda name: self.vars[name].get()
        tls = "TLS" in value("mode")
        call_home = "Call Home" in value("mode")
        for name in ("port", "listen_port"):
            if not 1 <= int(value(name)) <= 65535:
                raise ValueError(name + " must be 1–65535")
        timeout, rpc_timeout = float(value("timeout")), float(value("rpc_timeout"))
        if timeout < 0 or (not call_home and timeout == 0) or rpc_timeout <= 0:
            raise ValueError("Timeout must be positive; only Call Home waiting allows 0 (until cancelled).")
        if not value("listen_host" if call_home else "host").strip():
            raise ValueError("Host address is required.")
        if tls and (not value("cert") or not value("tls_key")):
            raise ValueError("TLS requires a client certificate and private key.")
        key = value("tls_key" if tls else "ssh_key")
        if not tls and value("ssh_auth") in {"password", "agent"}:
            key = ""
        for filename in ([key, value("cert"), value("trusted_ca"), value("crl")] if tls else [key, value("known_hosts")]):
            if filename and not Path(filename).expanduser().exists():
                raise ValueError("File not found: " + filename)
        if not tls and not value("username").strip():
            raise ValueError("SSH username is required.")
        if not tls and value("ssh_auth") not in {"auto", "password", "private-key", "agent"}:
            raise ValueError("未知 SSH 認證方式。")
        if not tls and value("ssh_auth") == "private-key" and not key:
            raise ValueError("private-key 認證需要指定私鑰檔案。")
        return ConnectionSettings(
            transport="tls" if tls else "ssh", call_home=call_home,
            host=value("host").strip(), port=int(value("port")),
            listen_host=value("listen_host").strip(), listen_port=int(value("listen_port")),
            username=value("username") or None, password=value("password") or None, key=key or None,
            ssh_auth=value("ssh_auth"), key_passphrase=value("key_passphrase") or None,
            known_hosts=value("known_hosts") or None, hostkey_verify=value("hostkey_verify"),
            allow_agent=value("allow_agent"), look_for_keys=value("look_for_keys"),
            cert=value("cert") or None, trusted_ca=value("trusted_ca") or None,
            crl=value("crl") or None, verify_hostname=value("verify_hostname"),
            tls_server_name=value("tls_server_name").strip() or None,
            tls_version=None if value("tls_version") == "auto" else value("tls_version"),
            netconf_version=None if value("netconf_version") == "auto" else value("netconf_version"),
            timeout=timeout, rpc_timeout=rpc_timeout, bind=value("bind") or None, huge_tree=True,
        )

    def options(self):
        return ReadOptions(self.vars["source"].get(), self.vars["defaults"].get(), self.vars["state"].get())

    def _sync(self):
        connected = self.client.connected
        idle = not self.busy
        setup_idle = idle and not connected and self.reconnect_due is None
        for widget in (self.connection_box, self.account_box, self.save_connection_button,
                       self.new_connection_button, self.save_account_button, self.new_account_button,
                       self.manage_connection_button, self.manage_account_button):
            widget.configure(state="normal" if setup_idle else "disabled")
        call_home = "Call Home" in self.vars["mode"].get()
        can_connect = idle and not connected
        self.connect_button.configure(state="normal" if can_connect else "disabled",
                                      background="#0969da" if can_connect else "#e2e8f0",
                                      cursor="hand2" if can_connect else "arrow")
        self.disconnect_button.configure(state="normal" if connected or self.busy or self.reconnect_due is not None else "disabled")
        self.retry_stop_button.configure(state="normal" if self.reconnect_due is not None or self.job_name == "自動重新連線…" and self.busy else "disabled")
        self.mode_box.configure(state="readonly" if setup_idle else "disabled")
        for widget in (self.host_entry, self.port_entry):
            widget.configure(state="normal" if setup_idle and not call_home else "disabled")
        for widget in (self.listen_entry, self.listen_port_entry):
            widget.configure(state="normal" if setup_idle and call_home else "disabled")
        self.source_box.configure(state="readonly" if idle else "disabled")
        self.defaults_check.configure(state="normal" if idle else "disabled")
        self.state_check.configure(state="normal" if idle and self.vars["source"].get() == "running" else "disabled")
        for widget in (self.read_all_button, self.schema_button):
            widget.configure(state="normal" if idle and connected else "disabled")
        self.save_tree_button.configure(state="normal" if idle and self.snapshot is not None else "disabled")
        for widget in (self.refresh_button, self.format_button, self.revert_button, self.save_button):
            widget.configure(state="normal" if idle and self.selection is not None else "disabled")
        self.editor.text.configure(state="normal" if idle and self.selection is not None else "disabled")
        can_send = (connected and idle and self.plan is not None and self.plan.rpc is not None
                    and not self.uncertain and self.snapshot is not None and self.snapshot.options.source != "startup")
        self.send_button.configure(state="normal" if can_send else "disabled")
        self._sync_features()

    def _run(self, label, work, done):
        if self.busy:
            return
        if self.client.connected and not self._rpc_allowed() and label not in {"中斷連線…", "關閉連線…"}:
            self.status.set("事件訂閱中且 server 不支援 interleave；請先停止訂閱（中斷連線）。")
            return
        self.busy, self.job_name = True, label
        self.job_device = self._audit_device()
        self.job_audit_operation = label
        if label == "送出修改中…" and self.plan and self.snapshot:
            self.job_audit_operation = "edit-config %s: %d changes / %d removals; %s" % (
                self.snapshot.options.source, len(self.plan.changes), self.plan.removals,
                "; ".join(self.plan.changes[:8]))
        self.status.set(label)
        self.progress.start(12)
        self._sync()
        def run():
            try:
                value = work()
                self.events.put(("done", (done, value)))
            except Exception as exc:
                self.events.put(("error", exc))
        threading.Thread(target=run, daemon=True, name="netconf-gui-worker").start()

    def _report_progress(self, text):
        self.events.put(("progress", text))

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.status.set(value)
                    continue
                previous_job = self.job_name
                previous_device = self.job_device
                previous_operation = self.job_audit_operation
                self.busy = False
                self.progress.stop()
                if kind == "done":
                    done, result = value
                    done(result)
                    if self.closed:
                        return
                    self._audit_result(previous_operation, "完成；結果待確認" if self.uncertain else "完成", previous_device)
                else:
                    if previous_job == "訂閱事件…" and self.client.connected:
                        # A lost reply does not prove that create-subscription
                        # failed. Keep non-interleave RPCs blocked until close.
                        self.notification_manager = self.client.manager
                        self.notification_status.set("訂閱結果待確認；請停止（中斷連線）後再試，不會自動重訂閱。")
                    if previous_job == "送出修改中…" or previous_job.startswith("設定操作："):
                        self.uncertain = True
                    elif self.snapshot is not None:
                        for name in ("source", "defaults", "state"):
                            self.vars[name].set(getattr(self.snapshot.options, name))
                    if previous_job == "自動重新連線…":
                        self.status.set("自動重連未成功（%s）；不會重送 XML。" % type(value).__name__)
                    else:
                        self._error(value)
                    self._audit_result(previous_operation, "失敗／結果待確認：" + type(value).__name__ if self.uncertain else "失敗：" + type(value).__name__, previous_device)
                if self.close_requested and not self.busy:
                    self._run("關閉連線…", self.client.disconnect, lambda _result: self._destroy())
                    self.close_requested = False
                self._sync()
        except queue.Empty:
            pass
        self._poll_notifications()
        self._monitor_connection()
        if self.root.winfo_exists():
            self.root.after(80, self._poll)

    def _reconnect_option_changed(self):
        if not self.vars["auto_reconnect"].get():
            self.stop_reconnect()
        elif self.client.connected and self.reconnect_settings is not None:
            self.reconnect_enabled = True

    def stop_reconnect(self):
        self.reconnect_enabled = False
        self.reconnect_due = None
        if self.busy and self.job_name == "自動重新連線…":
            self.client.cancel.set()
            self.root.after(150, self._stop_retry_when_idle)
        self.status.set("已停止自動重連；可手動連線重新啟用。")
        self._sync()

    def _stop_retry_when_idle(self):
        if self.busy:
            self.root.after(150, self._stop_retry_when_idle)
        else:
            self._run("停止重連…", self.client.disconnect,
                      lambda _result: self.status.set("已停止重連；編輯內容保留。"))

    def _monitor_connection(self):
        if self.closed or self.close_requested or self.demo or self.busy:
            return
        if self.client.connected:
            self.reconnect_due = None
            return
        if self.snapshot is not None:
            self.uncertain = True
            self._sync()
        if not self.reconnect_enabled or not self.vars["auto_reconnect"].get() or self.reconnect_settings is None:
            self.reconnect_due = None
            return
        if self.reconnect_due is None:
            self.reconnect_due = time.monotonic() + self.reconnect_delay
            self.status.set("連線已中斷，%d 秒後重連／重新監聽；保留編輯內容，不重送 XML。" % self.reconnect_delay)
            self.reconnect_delay = min(30, self.reconnect_delay * 2)
            self._sync()
        elif time.monotonic() >= self.reconnect_due:
            self.reconnect_due = None
            settings = deepcopy(self.reconnect_settings)
            directory = self.reconnect_schema_dir
            self.client.cancel.clear()
            def work():
                self.client.disconnect()
                self.client.connect(settings, self._report_progress)
                self.client.load_schema(directory, False, self._report_progress)
            def done(_result):
                self.reconnect_delay = 2
                self.uncertain = True
                self.status.set("已重新連線；編輯內容已保留。請先重新讀取確認伺服器狀態，才能送出修改。")
            self._run("自動重新連線…", work, done)

    def _error(self, exc):
        from ..session import CallHomeCancelled
        if isinstance(exc, (InterruptedError, CallHomeCancelled)):
            self.status.set("操作已取消")
            return
        text = redact_secrets(str(exc))
        self.status.set("操作失敗：" + text[:200])
        xml = getattr(exc, "xml", None)
        if isinstance(xml, etree._Element):
            self.reply.set(serialize_xml(xml).decode("utf-8"))
            self.output_tabs.select(self.reply)
        if self.uncertain:
            text += "\n\n請先重新讀取確認伺服器結果；不會自動重送。"
        if not self.close_requested:
            messagebox.showerror("NETCONF", text, parent=self.root)

    def _discard(self):
        return not self.dirty or messagebox.askyesno("尚未送出的修改", "放棄目前尚未送出的修改？", parent=self.root)

    @property
    def dirty(self):
        return self.selection is not None and self.editor.get() != self.baseline_text

    def connect(self):
        try:
            settings = self.settings()
        except Exception as exc:
            self._error(exc)
            return
        if not self._save_preferences(remember=True, notify=True):
            return
        if not self._discard():
            return
        self.reconnect_enabled = False
        self.reconnect_due = None
        self.reconnect_settings = deepcopy(settings)
        self.reconnect_schema_dir = self.vars["schema_dir"].get()
        self.client.cancel.clear()
        options = self.options()
        def work():
            self.client.connect(settings, self._report_progress)
            return self.client.read(options)
        def done(snapshot):
            self.demo = False
            self.reconnect_enabled = True
            self.reconnect_delay = 2
            self._accept_snapshot(snapshot)
            if not self.close_requested and not self.client.cancel.is_set():
                self.refresh_schema(force=False)
        self._run("連線並讀取資料…", work, done)

    def disconnect(self):
        if self.busy:
            if self.job_name == "送出修改中…" or self.job_name.startswith("設定操作："):
                messagebox.showinfo("正在送出", "請等待這次 RPC 完成，避免結果不明。", parent=self.root)
                return
            self.reconnect_enabled = False
            self.reconnect_due = None
            self.client.cancel.set()
            self.status.set("已要求取消；握手／正在執行的 RPC 最遲會在 timeout 後結束。")
            self.events.put(("progress", "取消中…"))
            # Once the active job finishes, close through the same serial worker.
            self.root.after(150, self._disconnect_when_idle)
            return
        if not self._discard():
            return
        self.reconnect_enabled = False
        self.reconnect_due = None
        self._run("中斷連線…", self.client.disconnect, lambda _result: self._clear_connection())

    def _disconnect_when_idle(self):
        if self.busy:
            self.root.after(150, self._disconnect_when_idle)
        else:
            self._run("中斷連線…", self.client.disconnect, lambda _result: self._clear_connection())

    def _clear_connection(self):
        self.notification_manager = None
        self.notification_status.set("未訂閱；連線已中斷。")
        self.diff_pane.set("")
        self.snapshot = self.selection = self.plan = None
        self.items.clear()
        self.tree.delete(*self.tree.get_children())
        self.editor.set("")
        self.preview.set("")
        self.baseline_text = ""
        self.schema_status.set("YANG：未載入")
        self.path_status.set("未連線")
        self.preview_status.set("尚無變更")
        self.status.set("已中斷連線")

    def _options_changed(self, _event=None):
        if not self._discard():
            if self.snapshot:
                for name in ("source", "defaults", "state"):
                    self.vars[name].set(getattr(self.snapshot.options, name))
            return
        if self.vars["source"].get() != "running":
            self.vars["state"].set(False)
        if self.client.connected:
            self.read_all(discard_checked=True)
        self._sync()

    def read_all(self, discard_checked=False):
        if not discard_checked and not self._discard():
            return
        options = self.options()
        self._run("讀取 %s%s…" % (options.source, " + state" if options.state else ""),
                  lambda: self.client.read(options), self._accept_snapshot)

    def refresh_schema(self, force=True):
        if not self._discard():
            return
        directory = self.vars["schema_dir"].get()
        self.client.cancel.clear()
        def done(schema):
            self.schema_status.set("YANG：%d modules / %d nodes%s" % (schema.module_count, len(schema.nodes), "" if schema.complete else "（不完整，唯讀）"))
            if self.snapshot:
                self._accept_snapshot(self.snapshot, fresh=False)
            if schema.warnings:
                self.status.set("YANG 載入完成但有提醒；請查看「連線 / Schema 詳情」。" + (" 寫入已停用。" if not schema.complete else ""))
        self._run("取得並編譯 YANG schemas…", lambda: self.client.load_schema(directory, force, self._report_progress), done)

    def _label(self, selection):
        node, path = selection.node, selection.path
        info = self.client.schema.lookup(path)
        label = local(node.tag)
        if not selection.ancestors:
            namespace = etree.QName(node).namespace
            label = self.client.schema.namespaces.get(namespace, label) + " : " + label
        if info and info.kind == "list":
            label += " [" + ", ".join(local(key) + "=" + (node.findtext(key) or "") for key in info.keys) + "]"
        elif not children(node) and node.text:
            # Tree labels must not turn obvious credentials into a visible sidebar.
            secret = any(word in local(node.tag).lower() for word in ("password", "secret", "private-key"))
            label += " = " + ("••••••" if secret else node.text.replace("\n", " ")[:48])
        return label

    def _insert(self, parent, iid, selection):
        self.items[iid] = selection
        style = node_style(selection.node, selection.path, self.client.schema)
        self.tree.insert(parent, "end", iid=iid, text=self._label(selection), tags=(style,) if style else ())
        if children(selection.node):
            self.tree.insert(iid, "end", iid=iid+"/dummy", text="…")

    def _expand_item(self, iid, open_item=True):
        existing = self.tree.get_children(iid)
        if existing and existing[0].endswith("/dummy"):
            self.tree.delete(existing[0])
            selected = self.items[iid]
            for index, node in enumerate(children(selected.node)):
                self._insert(iid, iid+"/"+str(index), Selection(node, selected.ancestors+(selected.node,)))
        if open_item:
            self.tree.item(iid, open=True)

    def _tree_click(self, event):
        # The class binding also changes selection, which used to trigger a
        # fetch/rebuild and reopen ancestors immediately after a collapse.
        if "indicator" in self.tree.identify_element(event.x, event.y).lower():
            iid = self.tree.identify_row(event.y)
            if iid in self.items:
                if self.tree.item(iid, "open"):
                    self.tree.item(iid, open=False)
                else:
                    self._expand_item(iid)
            return "break"

    def _tree_double_click(self, event):
        if "indicator" in self.tree.identify_element(event.x, event.y).lower():
            return self._tree_click(event)

    def _expand(self, _event=None):
        iid = self.tree.focus()
        if iid in self.items:
            self._expand_item(iid)

    def _bookmark(self, selection=None):
        selection = selection or self.selection
        if selection is None:
            return None
        try:
            chain = (*selection.ancestors, selection.node)
            return tuple(identity(node, self.client.schema, tuple(n.tag for n in chain[:i+1])) for i, node in enumerate(chain))
        except EditError:
            return None

    def _accept_snapshot(self, snapshot, bookmark=None, *, fresh=True):
        # Capture at completion, not request time: the user may collapse a
        # parent while a background read is still in flight.
        expanded = {self._bookmark(selection) for iid, selection in self.items.items()
                    if self.tree.item(iid, "open")}
        materialized = {self._bookmark(selection) for iid, selection in self.items.items()
                        if self.tree.get_children(iid) and not self.tree.get_children(iid)[0].endswith("/dummy")}
        expanded.discard(None)
        materialized.discard(None)
        if bookmark is None:
            bookmark = self._bookmark()
        self.snapshot = snapshot
        if fresh:
            self.uncertain = False
        self.selection = self.plan = None
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        for index, node in enumerate(children(snapshot.data)):
            self._insert("", str(index), Selection(node))
        def restore_open(parent):
            for iid in self.tree.get_children(parent):
                if iid in self.items and self._bookmark(self.items[iid]) in materialized:
                    self._expand_item(iid, open_item=False)
                    self.tree.item(iid, open=self._bookmark(self.items[iid]) in expanded)
                    restore_open(iid)
        restore_open("")
        chosen = None
        if bookmark:
            parent = ""
            for wanted in bookmark:
                matches = []
                for iid in self.tree.get_children(parent):
                    selection = self.items.get(iid)
                    if selection:
                        try:
                            if identity(selection.node, self.client.schema, selection.path) == wanted:
                                matches.append(iid)
                        except EditError:
                            pass
                if len(matches) != 1:
                    chosen = None
                    break
                chosen = parent = matches[0]
                self._expand_item(parent, open_item=False)
        if chosen is None and self.tree.get_children():
            chosen = self.tree.get_children()[0]
        if chosen is not None:
            self._show_selection(chosen)
            self.tree.selection_set(chosen)
            self.tree.focus(chosen)
            # see() expands closed ancestors; never undo a deliberate collapse.
        else:
            self.editor.set("")
            self.preview.set("")
            self.path_status.set("此查詢沒有可見資料（可能受 NACM 權限限制）")
        self.status.set(("DEMO · " if self.demo else "") + "已讀取 %s · %d 個根節點%s" % (
            snapshot.options.source, len(children(snapshot.data)), " + config false" if snapshot.options.state else "")
            + (" · " + " ".join(snapshot.warnings) if snapshot.warnings else ""))

    def _show_selection(self, iid):
        self.selection_iid, self.selection = iid, self.items[iid]
        self.baseline_text = self.selection.text()
        self.editor.set(self.baseline_text)
        parts = []
        chain = (*self.selection.ancestors, self.selection.node)
        for index, node in enumerate(chain):
            label = local(node.tag)
            info = self.client.schema.lookup(tuple(item.tag for item in chain[:index+1]))
            if info and info.keys:
                label += "[" + ", ".join(local(key) + "=" + repr(node.findtext(key)) for key in info.keys) + "]"
            parts.append(label)
        self.path_status.set("/" + "/".join(parts))
        self._update_preview()

    def _selected(self, _event=None):
        ids = self.tree.selection()
        if not ids or ids[0] not in self.items:
            return
        iid = ids[0]
        if self.selection is self.items[iid]:
            return
        if self.busy or not self._discard():
            if self.selection_iid and self.tree.exists(self.selection_iid):
                self.tree.selection_set(self.selection_iid)
            return
        self._show_selection(iid)
        # A click presents the snapshot immediately and rereads that root from
        # the peer. Root subtree filters also work without XPath capability.
        self.refresh_selected(discard_checked=True)

    def refresh_selected(self, discard_checked=False):
        if not self.selection or not self.snapshot:
            return
        if not discard_checked and not self._discard():
            return
        root_tag = self.selection.path[0]
        options = self.snapshot.options
        bookmark = self._bookmark()
        def done(part):
            data = deepcopy(self.snapshot.data)
            indexes = [i for i, node in enumerate(data) if node.tag == root_tag]
            position = indexes[0] if indexes else len(data)
            for node in list(data):
                if node.tag == root_tag:
                    data.remove(node)
            for node in reversed(children(part.data)):
                data.insert(position, deepcopy(node))
            self._accept_snapshot(Snapshot(data, options, part.defaults_mode, part.warnings), bookmark)
        self._run("重新讀取所選節點…", lambda: self.client.read(options, root_tag), done)

    def _edited(self, _event=None):
        if not self.editor.text.edit_modified():
            return
        self.editor.text.edit_modified(False)
        self.plan = None
        self.send_button.configure(state="disabled")
        if self.edit_timer:
            self.root.after_cancel(self.edit_timer)
        self.edit_timer = self.root.after(220, self._update_preview)

    def _update_preview(self):
        self.edit_timer = None
        self.plan = None
        self.editor.syntax()
        if not self.selection:
            return
        text = self.editor.get()
        try:
            target = self.snapshot.options.source
            if target == "startup" and self.dirty:
                raise EditError("startup is read-only in this GUI; select running/candidate before editing.")
            self.plan = build_plan(self.selection, text, self.client.schema, target)
            self.editor.annotate(self.plan.edited, self.selection.path, self.client.schema, self.plan.changed)
            self.preview.set(self.plan.wire_xml)
            if self.plan.rpc is not None:
                self.preview_status.set("%d 項變更 / %d 項移除 → %s · 尚未送出；不會自動 commit 或保存 startup" % (len(self.plan.changes), self.plan.removals, target))
            else:
                self.preview_status.set("尚無變更，不會送出任何設定")
        except Exception as exc:
            self.preview.set("")
            self.preview_status.set("無法送出：" + str(exc))
            try:
                self.editor.annotate(parse_editor(text), self.selection.path, self.client.schema)
            except Exception:
                pass
        self._update_diff()
        self._sync()

    def format_editor(self):
        try:
            root = parse_editor(self.editor.get())
            self.editor.set(serialize_xml(root).decode("utf-8"))
            self._update_preview()
        except Exception as exc:
            self._error(exc)

    def revert(self):
        if self._discard():
            self.editor.set(self.baseline_text)
            self._update_preview()

    def save_xml(self):
        filename = filedialog.asksaveasfilename(parent=self.root, defaultextension=".xml", filetypes=[("XML", "*.xml")])
        if filename:
            try:
                parse_editor(self.editor.get())
                Path(filename).write_text(self.editor.get(), encoding="utf-8", newline="\n")
                self.status.set("已匯出 UTF-8 XML：" + filename)
            except Exception as exc:
                self._error(exc)

    def save_tree_xml(self):
        """Export the complete loaded snapshot, not lazy widgets or editor drafts."""
        if self.busy or self.snapshot is None:
            return
        try:
            # Freeze before opening the file dialog; background events may
            # update the UI while the user chooses a filename.
            data = serialize_xml(self.snapshot.data)
            source = self.snapshot.options.source
            filename = filedialog.asksaveasfilename(
                parent=self.root, title="匯出 DATA TREE（目前已讀取的全部 XML）",
                initialfile="netconf-%s-tree.xml" % source,
                defaultextension=".xml", filetypes=[("XML", "*.xml")])
            if filename:
                Path(filename).write_bytes(data)
                self.status.set("已匯出 DATA TREE 的 %s 資料快照（UTF-8；不含未送出的編輯）：%s" % (source, filename))
        except Exception as exc:
            self._error(exc)

    def send(self):
        if not self.client.connected or not self._rpc_allowed():
            return
        self._update_preview()
        if not self.plan or self.plan.rpc is None or self.uncertain or self.busy:
            return
        plan, selection, options = self.plan, self.selection, self.snapshot.options
        summary = "\n".join(plan.changes[:12])
        question = ("%s送出 %d 項變更（含 %d 項移除）到 %s？\n\n%s\n\n"
                    "將先 lock、比對設定，再送出下方預覽的 edit-config，最後 unlock。\n"
                    "不會自動 commit candidate，也不會保存 startup。" % (
                        "[DEMO，無網路] " if self.demo else "", len(plan.changes), plan.removals, options.source, summary))
        if not messagebox.askyesno("確認送出 NETCONF 修改", question, parent=self.root, default="no"):
            return
        self.last_sent_xml = plan.wire_xml
        def done(result):
            self.reply.set(result.reply)
            self.output_tabs.select(self.reply)
            if result.snapshot is not None:
                self._accept_snapshot(result.snapshot)
            else:
                self.uncertain = True
                self.plan = None
            self.status.set(("DEMO · " if self.demo else "") + "edit-config 成功；" + ("candidate 尚未 commit。" if options.source == "candidate" else "未自動保存 startup。") + " ".join(result.warnings))
        self._run("送出修改中…", lambda: self.client.apply(selection, plan, options), done)

    def show_details(self):
        window = tk.Toplevel(self.root)
        window.title("NETCONF / YANG details")
        window.geometry("900x570")
        pane = XmlPane(window, readonly=True)
        pane.pack(fill="both", expand=True, padx=8, pady=8)
        lines = self.client.context.status_lines() if self.client.context is not None else ["Offline / Demo"]
        lines += ["", "YANG modules: %d" % self.client.schema.module_count,
                  "Schema complete: " + str(self.client.schema.complete), "", *self.client.schema.warnings]
        if self.client.connected and not self.demo:
            lines += ["", "Capabilities:", *self.client.capabilities]
        pane.set("\n".join(lines))

    def close(self):
        if self.busy and (self.job_name == "送出修改中…" or self.job_name.startswith("設定操作：")):
            messagebox.showinfo("正在送出", "請等待 RPC 完成後再關閉。", parent=self.root)
            return
        if not self._discard():
            return
        if not self._save_preferences(notify=True):
            if not messagebox.askyesno("尚未儲存設定", "設定無法儲存，仍要關閉視窗？原設定檔不會被覆寫。",
                                       parent=self.root, default="no"):
                return
        self.reconnect_enabled = False
        self.reconnect_due = None
        if self.busy:
            self.close_requested = True
            self.client.cancel.set()
            self.status.set("等待目前操作結束後關閉…")
        else:
            self._run("關閉連線…", self.client.disconnect, lambda _result: self._destroy())

    def _destroy(self):
        self.closed = True
        self.client.cancel.set()
        for callback in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            # Cancel the timer only. Each child widget owns (and will delete)
            # its registered Tcl command during destroy().
            self.root.tk.call("after", "cancel", callback)
        self.root.destroy()

    def load_demo(self):
        from .demo import DemoClient
        self.client = DemoClient()
        self.demo = True
        self.vars["defaults"].set(True)
        self.vars["state"].set(True)
        self.root.title("NETCONF Console GUI " + VERSION + " — DEMO / NO NETWORK")
        self.schema_status.set("YANG：示範 schema（無網路）")
        self._accept_snapshot(self.client.read(self.options()))
        self._sync()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Native NETCONF XML browser/editor")
    parser.add_argument("--demo", action="store_true", help="Open safe offline demonstration data; no network")
    parser.add_argument("--self-test", metavar="REPORT_JSON", help="Run GUI/runtime diagnostics without a remote connection")
    parser.add_argument("--loopback-test", metavar="SETTINGS_JSON", help="Developer diagnostic; requires --self-test and a loopback-only test peer")
    args = parser.parse_args(argv)
    if args.loopback_test and not args.self_test:
        parser.error("--loopback-test requires --self-test REPORT_JSON")
    set_app_id()
    root = tk.Tk()
    window = NetconfWindow(root, persist=not (args.demo or args.self_test))
    if args.demo:
        window.load_demo()
    if args.self_test:
        from .diagnostics import self_test, loopback_test
        try:
            report = loopback_test(args.loopback_test) if args.loopback_test else self_test(window)
            Path(args.self_test).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return 0 if report["passed"] else 1
        finally:
            window._destroy()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
