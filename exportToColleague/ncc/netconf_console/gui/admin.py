"""Independent system SSH tab and explicitly confirmed sysrepo edits."""
from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from ..session import ConnectionSettings
from . import sysrepo, system_backup
from .model import EditError

ADMIN_DEFAULTS = {"admin_host": "127.0.0.1", "admin_port": "22", "admin_username": "root",
    "admin_password": "", "admin_key": "", "admin_passphrase": "", "admin_auth": "password",
    "admin_known_hosts": "", "admin_verify": False, "admin_jump": False,
    "admin_program": "sysrepocfg", "admin_timeout": "10",
    "backup_base": system_backup.DEFAULT_BASE, "backup_sysrepoctl": system_backup.DEFAULT_SYSREPOCTL,
    "backup_init_module": system_backup.DEFAULT_INIT_MODULE,
    "backup_candidate": False, "backup_startup": False}


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
        self._field(page, "sysrepocfg 路徑", "admin_program", 0, 4, 17)
        self._field(page, "Timeout 秒", "admin_timeout", 0, 6, 5)
        ttk.Combobox(page, textvariable=self.vars["admin_auth"], values=("password", "private-key", "agent", "auto"),
            state="readonly", width=11).grid(row=0, column=8, padx=3)
        # Keep the system account and login password together on the second row.
        self.admin_username_entry = self._field(page, "系統帳號", "admin_username", 1, 0, 11)
        self.admin_password_entry = self._field(page, "登入密碼", "admin_password", 1, 2, 22, True)
        self._path_field(page, "Private key", "admin_key", 1, 4).configure(width=15)
        ttk.Checkbutton(page, text="驗證 host key", variable=self.vars["admin_verify"]).grid(row=1, column=8, sticky="w")
        self._field(page, "私鑰密碼", "admin_passphrase", 2, 0, 11, True)
        self._path_field(page, "Known hosts", "admin_known_hosts", 2, 2).configure(width=15)
        ttk.Checkbutton(page, text="經 SSH 跳板頁主機", variable=self.vars["admin_jump"]).grid(row=2, column=5, columnspan=2)
        self.admin_connect_button = tk.Button(
            page, text="連線系統 SSH", command=self.connect_admin,
            font=("Segoe UI", 10, "bold"), padx=10, pady=3,
            background="#0969da", foreground="#ffffff",
            activebackground="#0756b3", activeforeground="#ffffff",
            disabledforeground="#ffffff", relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground="#1d4ed8",
            cursor="hand2", takefocus=True,
        )
        self.admin_connect_button.grid(row=2, column=7)
        self.admin_disconnect_button = ttk.Button(page, text="中斷 SSH", command=self.disconnect_admin)
        self.admin_disconnect_button.grid(row=2, column=8)
        self.admin_status = tk.StringVar(self.root, "系統 SSH 未連線（獨立於 NETCONF；不會自動使用 root 重試）。")
        # Keep the connection tab at three rows; status uses the existing divider.
        self.admin_status_label = ttk.Label(self.connection_divider, textvariable=self.admin_status, wraplength=430)
        self.admin_status_label.pack(side="left", padx=8)
        self._build_system_backup()

    def _build_system_backup(self):
        page = ttk.Frame(self.auth_tabs, padding=(4, 2))
        self.system_backup_page = page
        self.auth_tabs.add(page, text="備份／還原")
        page.columnconfigure(1, weight=1)
        page.columnconfigure(4, weight=1)
        self._field(page, "遠端 BASE", "backup_base", 0, 0, 31)
        self._field(page, "sysrepoctl 路徑", "backup_sysrepoctl", 0, 3, 17)
        self._field(page, "初始 YANG module", "backup_init_module", 1, 0, 20)
        ttk.Label(page, text="備份 datastore").grid(row=1, column=3, sticky="w", padx=(7, 4), pady=2)
        ttk.Label(page, text="☑ running（必要）").grid(row=1, column=4, sticky="w", padx=(0, 5), pady=2)
        ttk.Checkbutton(page, text="candidate", variable=self.vars["backup_candidate"]).grid(
            row=1, column=5, sticky="w", padx=3, pady=2)
        ttk.Checkbutton(page, text="startup", variable=self.vars["backup_startup"]).grid(
            row=1, column=6, sticky="w", padx=3, pady=2)
        self.backup_check_button = ttk.Button(page, text="檢查 YANG 初始化", command=self.check_yang_initialization)
        self.backup_check_button.grid(row=2, column=0, padx=7, pady=(3, 2), sticky="w")
        self.backup_create_button = ttk.Button(page, text="建立遠端備份", style="Netconf.TButton",
                                                command=self.create_system_backup)
        self.backup_create_button.grid(row=2, column=1, padx=4, pady=(3, 2), sticky="w")
        self.backup_restore_button = ttk.Button(page, text="從最新備份還原 running", style="Sysrepo.TButton",
                                                 command=self.restore_system_backup)
        self.backup_restore_button.grid(row=2, column=2, columnspan=3, padx=4, pady=(3, 2), sticky="w")
        self.backup_status = tk.StringVar(
            self.root, "尚未執行；備份使用 UTC 時間目錄，還原會自動找最新並驗證 SHA256。")
        ttk.Label(page, textvariable=self.backup_status, foreground="#925127", wraplength=700).grid(
            row=2, column=5, columnspan=2, sticky="w", padx=(8, 7), pady=(1, 2))

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
        if online:
            self.admin_connect_button.configure(
                text="系統 SSH 已連線", state="disabled", background="#16a34a",
                activebackground="#15803d", highlightbackground="#15803d",
                cursor="arrow", disabledforeground="#ffffff")
        elif idle:
            self.admin_connect_button.configure(
                text="連線系統 SSH", state="normal", background="#0969da",
                activebackground="#0756b3", highlightbackground="#1d4ed8",
                cursor="hand2", disabledforeground="#ffffff")
        else:
            self.admin_connect_button.configure(
                text="連線系統 SSH", state="disabled", background="#e2e8f0",
                activebackground="#e2e8f0", highlightbackground="#cbd5e1",
                cursor="arrow", disabledforeground="#64748b")
        self.admin_disconnect_button.configure(state="normal" if idle and self.admin_connection else "disabled")
        ready = (idle and online and not self.demo and self.selection is not None and self.snapshot is not None
                 and self.snapshot.options.source == "running" and self.plan is not None and self.plan.rpc is not None
                 and not self._pending() and not self.admin_requires_refresh)
        self.admin_edit_button.configure(state="normal" if ready else "disabled")
        backup_ready = idle and online and not self.admin_requires_refresh
        for widget in (self.backup_check_button, self.backup_create_button, self.backup_restore_button):
            widget.configure(state="normal" if backup_ready else "disabled")

    def _system_backup_request(self):
        datastores = ["running"]
        if self.vars["backup_candidate"].get():
            datastores.append("candidate")
        if self.vars["backup_startup"].get():
            datastores.append("startup")
        return system_backup.prepare(
            base=self.vars["backup_base"].get(),
            sysrepocfg=self.vars["admin_program"].get(),
            sysrepoctl=self.vars["backup_sysrepoctl"].get(),
            init_module=self.vars["backup_init_module"].get(),
            datastores=datastores,
        )

    def _admin_shell_for_operation(self):
        shell = self.admin_connection
        if not shell or not shell.connected:
            raise EditError("請先在「系統 SSH／sysrepo」分頁連線。")
        settings = self._admin_settings()
        if shell.settings != settings:
            raise EditError("系統 SSH 欄位已改變，請中斷並重新連線。")
        return shell, settings

    def _system_script_timeout(self, restore=False):
        timeout = int(self.vars["admin_timeout"].get())
        return max(60 if restore else 30, timeout + (60 if restore else 30))

    @staticmethod
    def _script_text(stdout, stderr):
        raw = stdout + (b"\n" if isinstance(stdout, bytes) and stderr else "\n" if stdout and stderr else b"") + stderr
        return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

    def check_yang_initialization(self):
        if self.busy or self.lifecycle_dialog:
            return
        try:
            shell, settings = self._admin_shell_for_operation()
            request = self._system_backup_request()
        except Exception as exc:
            self._error(exc)
            return
        def work():
            if shell.settings != settings:
                raise EditError("系統 SSH 設定已改變；不會執行遠端命令。")
            return shell.run(system_backup.shell_command(),
                             system_backup.initialization_script(request).encode("utf-8"),
                             timeout=self._system_script_timeout())
        def done(result):
            text = self._script_text(*result)
            if "NCC_YANG_INITIALIZED=" not in text:
                raise EditError("遠端沒有確認 YANG module 初始化。")
            self.backup_status.set("YANG 初始化已確認：%s" % request.init_module)
            self.status.set("系統 SSH 已確認 YANG module %s 存在；尚未備份或還原。" % request.init_module)
        self._run("系統備份：檢查初始化…", work, done)

    def create_system_backup(self):
        if self.busy or self.lifecycle_dialog:
            return
        try:
            shell, settings = self._admin_shell_for_operation()
            request = self._system_backup_request()
        except Exception as exc:
            self._error(exc)
            return
        def work():
            if shell.settings != settings:
                raise EditError("系統 SSH 設定已改變；不會執行遠端命令。")
            return shell.run(system_backup.shell_command(), system_backup.backup_script(request).encode("utf-8"),
                             timeout=self._system_script_timeout())
        def done(result):
            output = self._script_text(*result)
            directory = system_backup.parse_marker(output, "NCC_BACKUP_DIR", request.base)
            stores = system_backup.marker_value(output, "NCC_BACKUP_DATASTORES") \
                if "NCC_BACKUP_DATASTORES=" in output else ",".join(request.datastores)
            self.backup_status.set("已建立遠端備份：%s（%s）" % (directory, stores))
            self.status.set("遠端 Sysrepo 備份完成；running XML、module 清單與 SHA256 已保存。")
        self._run("系統備份：建立…", work, done)

    def restore_system_backup(self):
        if self.busy or self.lifecycle_dialog:
            return
        if self.admin_requires_refresh:
            self.status.set("上次系統 SSH 操作後仍需重新讀取 NETCONF；暫停備份／還原。")
            return
        if self.client.connected and not self.demo:
            self.status.set("還原前請先中斷 NETCONF，避免停止 netopeer2-server 時留下連線結果待確認。")
            return
        try:
            shell, settings = self._admin_shell_for_operation()
            request = self._system_backup_request()
        except Exception as exc:
            self._error(exc)
            return
        def work():
            if shell.settings != settings:
                raise EditError("系統 SSH 設定已改變；不會執行遠端命令。")
            result = shell.run(system_backup.shell_command(), system_backup.latest_script(request).encode("utf-8"),
                               timeout=self._system_script_timeout())
            output = self._script_text(*result)
            return system_backup.parse_marker(output, "NCC_LATEST_BACKUP_DIR", request.base), output
        self._run("系統備份：尋找最新…", work,
                  lambda result: self._open_system_restore_confirmation(result, shell, settings, request))

    def _open_system_restore_confirmation(self, result, shell, settings, request):
        latest, output = result
        content = "系統 SSH：%s@%s:%s\n遠端 BASE：%s\n\n" % (
            settings.username, settings.host, settings.port, request.base)
        content += "將還原最新備份：\n%s\n\n" % latest
        content += "已先完成 SHA256 驗證；執行時會再次確認最新目錄與 checksum。\n"
        content += "會停止可能修改 Sysrepo 的服務：\n  " + "\n  ".join(system_backup.STOP_SERVICES) + "\n"
        content += "還原 running.xml 後，只重新啟動還原前為 active 的服務，啟動順序為：\n  " + \
            "\n  ".join(system_backup.START_SERVICES) + "\n\n"
        content += "最新備份檢查輸出：\n" + output[-6000:]
        window = self._text_window("確認還原最新 Sysrepo 備份", content)
        self.lifecycle_dialog = window
        window.transient(self.root)
        window.grab_set()
        acknowledged = tk.BooleanVar(window, False)
        bar = ttk.Frame(window)
        bar.pack(side="bottom", fill="x", before=window.winfo_children()[0])
        ttk.Checkbutton(bar, text="我確認主機、備份版本、服務停止／重啟行為，並有系統管理授權。",
                        variable=acknowledged).pack(side="left", padx=8)
        def close():
            self.lifecycle_dialog = None
            window.destroy()
            self._sync()
        def send():
            if not acknowledged.get():
                messagebox.showinfo("確認還原", "請先核對備份版本與服務行為，並勾選確認。", parent=window)
                return
            if self.busy or self.admin_connection is not shell:
                return
            try:
                if not shell.connected or shell.settings != self._admin_settings():
                    raise EditError("系統 SSH 已中斷或欄位已改變；請重新連線並重新尋找最新備份。")
            except Exception as exc:
                self._error(exc)
                return
            if not messagebox.askyesno("送出 Sysrepo 還原", "確定停止服務並將最新備份覆寫到 running？\n"
                    "還原後必須重新連線 NETCONF 並重新讀取；不會自動重送其他 XML。",
                    parent=window, default="no"):
                return
            close()
            self.admin_requires_refresh = True
            self.uncertain = True
            def work():
                return shell.run(system_backup.shell_command(),
                                  system_backup.restore_script(request, latest).encode("utf-8"),
                                  timeout=self._system_script_timeout(restore=True))
            def done(result):
                output = self._script_text(*result)
                applied = system_backup.parse_marker(output, "NCC_RESTORE_APPLIED", request.base)
                self.backup_status.set("已還原 running：%s；服務已嘗試恢復。" % applied)
                self.status.set("系統 Sysrepo 還原完成；請重新連線 NETCONF 並重新讀取 running，確認服務狀態。")
            self._run("系統備份：還原中…", work, done)
        ttk.Button(bar, text="確認停止服務並還原 running", style="Sysrepo.TButton", command=send).pack(
            side="right", padx=8, pady=6)
        ttk.Button(bar, text="取消", command=close).pack(side="right", padx=4)
        window.protocol("WM_DELETE_WINDOW", close)
        self._sync()

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
            if self.vars["rollback_on_error"].get():
                raise EditError("rollback-on-error 是 NETCONF 選項，不適用 sysrepocfg；請取消勾選後再使用系統 SSH")
            if self._draft_guard():
                raise EditError(self._draft_guard())
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
            entry = self._active_draft()
            if entry:
                entry.session = None
                entry.status = "系統 SSH 已送出／待重新比對"
            self.reconnect_enabled = False
            self.reconnect_due = None
            self.last_sent_xml = prepared.payload.decode("utf-8")
            self._begin_attempt(selection, plan, snapshot.options, "系統 SSH/sysrepocfg")
            self.preview.set(self.last_sent_xml)
            def done(result):
                reply, warnings = result
                self.reply.set(reply + "\n" + "\n".join(warnings))
                self.output_tabs.select(self.reply)
                self.uncertain = True
                self._finish_attempt()
                self.status.set("系統 SSH 修改完成；" + (" ".join(warnings) if warnings else "SSH 讀回符合草稿。")
                    + " 未保存 startup；請重新讀取 NETCONF 確認，編輯內容保留。")
            self._run("系統 SSH 修改…", lambda: sysrepo.execute(shell, prepared, selection, plan, schema, timeout, snapshot.options.defaults), done)
        ttk.Button(bar, text="確認並執行 sysrepocfg", command=send).pack(side="right", padx=8, pady=6)
        ttk.Button(bar, text="取消", command=close).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", close)
        self._sync()
