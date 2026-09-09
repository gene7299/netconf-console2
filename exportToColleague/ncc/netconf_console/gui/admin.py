"""Independent system SSH tab and explicitly confirmed sysrepo edits."""
from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from ..session import ConnectionSettings
from . import sysrepo
from .model import EditError

ADMIN_DEFAULTS = {"admin_host": "127.0.0.1", "admin_port": "22", "admin_username": "root",
    "admin_password": "", "admin_key": "", "admin_passphrase": "", "admin_auth": "password",
    "admin_known_hosts": "", "admin_verify": True, "admin_jump": False,
    "admin_program": "sysrepocfg", "admin_timeout": "10"}


class AdminFeatures:
    def _build_admin(self):
        self.admin_connection = None
        self.admin_requires_refresh = False
        for name, value in ADMIN_DEFAULTS.items():
            self.vars[name] = (tk.BooleanVar if type(value) is bool else tk.StringVar)(self.root, value)
        page = ttk.Frame(self.auth_tabs, padding=(4, 2))
        self.auth_tabs.add(page, text="系統 SSH／sysrepo")
        self._field(page, "SSH host", "admin_host", 0, 0, 17)
        self._field(page, "Port", "admin_port", 0, 2, 5)
        self._field(page, "系統帳號", "admin_username", 0, 4, 11)
        self._field(page, "登入密碼", "admin_password", 0, 6, 12, True)
        ttk.Combobox(page, textvariable=self.vars["admin_auth"], values=("password", "private-key", "agent", "auto"),
            state="readonly", width=11).grid(row=0, column=8, padx=3)
        self._path_field(page, "Private key", "admin_key", 1, 0).configure(width=15)
        self._field(page, "私鑰密碼", "admin_passphrase", 1, 3, 11, True)
        self._path_field(page, "Known hosts", "admin_known_hosts", 1, 5).configure(width=15)
        ttk.Checkbutton(page, text="驗證 host key", variable=self.vars["admin_verify"]).grid(row=1, column=8, sticky="w")
        self._field(page, "sysrepocfg 路徑", "admin_program", 2, 0, 17)
        self._field(page, "Timeout 秒", "admin_timeout", 2, 2, 5)
        ttk.Checkbutton(page, text="經 SSH 跳板頁主機", variable=self.vars["admin_jump"]).grid(row=2, column=4, columnspan=2)
        self.admin_connect_button = ttk.Button(page, text="連線系統 SSH", command=self.connect_admin)
        self.admin_connect_button.grid(row=2, column=6)
        self.admin_disconnect_button = ttk.Button(page, text="中斷 SSH", command=self.disconnect_admin)
        self.admin_disconnect_button.grid(row=2, column=7)
        self.admin_status = tk.StringVar(self.root, "系統 SSH 未連線（獨立於 NETCONF；不會自動使用 root 重試）。")
        # Keep the connection tab at three rows; status uses the existing divider.
        self.admin_status_label = ttk.Label(self.connection_divider, textvariable=self.admin_status, wraplength=430)
        self.admin_status_label.pack(side="left", padx=8)

    def _admin_settings(self):
        value = lambda name: self.vars[name].get()
        host, username = value("admin_host").strip(), value("admin_username").strip()
        port = int(value("admin_port"))
        if not host or not username or not 1 <= port <= 65535:
            raise EditError("請填系統 SSH host、帳號及 1–65535 的 port。")
        mode = value("admin_auth")
        if mode not in {"auto", "password", "private-key", "agent"}:
            raise EditError("未知 SSH 認證方式。")
        key = value("admin_key") if mode in {"auto", "private-key"} else ""
        if mode == "private-key" and not key:
            raise EditError("請填系統 SSH 私鑰路徑。")
        for file in (key, value("admin_known_hosts")):
            if file and not Path(file).expanduser().is_file():
                raise EditError("File not found: " + file)
        settings = ConnectionSettings(host=host, port=port, username=username,
            password=value("admin_password") or None, key=key or None, ssh_auth=mode,
            key_passphrase=value("admin_passphrase") or None, known_hosts=value("admin_known_hosts") or None,
            hostkey_verify=value("admin_verify"), allow_agent=mode in {"auto", "agent"}, look_for_keys=False, timeout=15)
        if value("admin_jump"):
            for name in ("jump_host", "jump_username", "jump_password", "jump_key", "jump_passphrase", "jump_auth", "jump_verify", "jump_known_hosts"):
                setattr(settings, name, value(name))
            settings.jump_enabled, settings.jump_port = True, int(value("jump_port"))
        return settings

    def _sync_admin(self):
        online = bool(self.admin_connection and self.admin_connection.connected)
        idle = not self.busy and not self.lifecycle_dialog
        self.admin_connect_button.configure(state="normal" if idle and not online else "disabled")
        self.admin_disconnect_button.configure(state="normal" if idle and self.admin_connection else "disabled")
        ready = (idle and online and not self.demo and self.selection is not None and self.snapshot is not None
                 and self.snapshot.options.source == "running" and self.plan is not None and self.plan.rpc is not None
                 and not self._pending() and not self.admin_requires_refresh)
        self.admin_edit_button.configure(state="normal" if ready else "disabled")

    def connect_admin(self):
        if self.busy or self.lifecycle_dialog:
            return
        try:
            settings = self._admin_settings()
        except Exception as exc:
            self._error(exc)
            return
        def work():
            if self.admin_connection:
                self.admin_connection.close()
            connection = sysrepo.ShellConnection(settings)
            connection.connect()
            return connection
        def done(connection):
            self.admin_connection = connection
            self.admin_status.set("系統 SSH：%s@%s:%d（非 NETCONF）" % (settings.username, settings.host, settings.port))
            self.status.set("系統 SSH 已連線；尚未執行任何 sysrepocfg 修改。")
        self._run("連線系統 SSH…", work, done)

    def disconnect_admin(self):
        if self.busy:
            return
        def work():
            if self.admin_connection:
                self.admin_connection.close()
        def done(_):
            self.admin_connection = None
            self.admin_status.set("系統 SSH 已中斷；NETCONF 連線不受影響。")
        self._run("中斷系統 SSH…", work, done)

    def open_sysrepo(self):
        if self.busy or self.lifecycle_dialog or self._pending() or self.admin_requires_refresh:
            self.status.set("請先完成其他操作；上次系統 SSH 修改後需重新讀取 NETCONF 快照。")
            return
        try:
            shell = self.admin_connection
            if not shell or not shell.connected:
                raise EditError("請先從「系統 SSH／sysrepo」分頁連線到設備的系統 SSH server。")
            if shell.settings != self._admin_settings():
                raise EditError("系統 SSH 欄位已改變，請中斷並重新連線。")
            if self.snapshot is None or self.snapshot.options.source != "running" or self.demo:
                raise EditError("請先用 NETCONF 讀取 running 並編輯；不支援 startup、candidate 或離線示範。")
            self._update_preview()
            timeout = int(self.vars["admin_timeout"].get())
            prepared = sysrepo.prepare(self.plan, self.client.schema, self.vars["admin_program"].get(), timeout, self.snapshot.options.defaults)
        except Exception as exc:
            self._error(exc)
            return
        plan, selection, snapshot, schema = deepcopy(self.plan), self.selection, self.snapshot, self.client.schema
        settings = shell.settings
        content = "系統 SSH：%s@%s:%s\n目前 NETCONF：%s\n\n" % (settings.username, settings.host, settings.port, self._audit_device())
        content += "此入口使用已授權的 OS 帳號操作本機 sysrepo，不是 NETCONF RPC，也不會更改 NACM 規則。\n"
        content += "只修改 running；不 commit、不保存 startup、不自動重送。修改 Call Home 位址可能立即斷線。\n"
        content += "會先讀取比對、執行，再讀回；這三步不是原子交易。部分版本的 --lock 在 stdin 模式不生效。\n\n"
        content += "讀取命令：\n" + prepared.read_command + "\n\n修改命令（XML 經 stdin；不建立遠端檔案）：\n" + prepared.command
        content += "\n\n實際 stdin XML（不是 rpc/config envelope）：\n" + prepared.payload.decode("utf-8")
        window = self._text_window("確認系統 SSH／sysrepocfg 修改", content)
        self.lifecycle_dialog = window
        window.transient(self.root)
        window.grab_set()
        acknowledged = tk.BooleanVar(window, False)
        bar = ttk.Frame(window)
        bar.pack(side="bottom", fill="x", before=window.winfo_children()[0])
        ttk.Checkbutton(bar, text="我有系統管理授權，並確認 SSH 對應同一台設備／同一個 sysrepo instance", variable=acknowledged).pack(anchor="w")
        def close():
            self.lifecycle_dialog = None
            window.destroy()
            self._sync()
        def send():
            if not acknowledged.get():
                messagebox.showinfo("確認目標", "請先核對 SSH 主機及 sysrepo instance，並勾選管理授權確認。", parent=window)
                return
            if self.busy or self.snapshot is not snapshot or self.selection is not selection or self.admin_connection is not shell:
                return
            if not messagebox.askyesno("送出系統 SSH 修改", "確認透過 %s@%s:%s 執行 %s？\n可能中斷 Call Home；不會保存 startup。" % (
                    settings.username, settings.host, settings.port, prepared.command), parent=window, default="no"):
                return
            close()
            self.admin_requires_refresh = True
            self.reconnect_enabled = False
            self.reconnect_due = None
            self.last_sent_xml = prepared.payload.decode("utf-8")
            self.preview.set(self.last_sent_xml)
            def done(result):
                reply, warnings = result
                self.reply.set(reply + "\n" + "\n".join(warnings))
                self.output_tabs.select(self.reply)
                self.uncertain = True
                self.status.set("系統 SSH 修改完成；" + (" ".join(warnings) if warnings else "SSH 讀回符合草稿。")
                    + " 未保存 startup；請重新讀取 NETCONF 確認，編輯內容保留。")
            self._run("系統 SSH 修改…", lambda: sysrepo.execute(shell, prepared, selection, plan, schema, timeout, snapshot.options.defaults), done)
        ttk.Button(bar, text="確認並執行 sysrepocfg", command=send).pack(side="right", padx=8, pady=6)
        ttk.Button(bar, text="取消", command=close).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", close)
        self._sync()
