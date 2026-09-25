"""IP/file picker for a local SFTP source or the configured SSH jump host."""

from copy import deepcopy
from threading import Event

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .model import EditError
from .software_transfer import (JumpSftpSource, LocalSftpSource, jump_addresses,
                                local_addresses, upload_to_jump)
from .workflow_supervision import SupervisionKeeper


class DownloadSourcePanel(QWidget):
    prepared = Signal(object)
    invalidated = Signal()
    mode_changed = Signal(bool)

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.window = owner.window
        self.resource = None
        self.old_uploads = []
        self.paths = []
        self.working = False
        self.cancel_event = Event()
        self.address_identity = None
        self.observed_identity = None
        self._changing = False
        self._detect_scheduled = False
        self._credentials = None
        self._last_read = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        self.mode.addItem("自填 URI", "manual")
        self.mode.addItem("選擇檔案，自動準備 SFTP／URI", "auto")
        layout.addWidget(self.mode)
        self.automatic = QWidget()
        form = QFormLayout(self.automatic)
        form.setContentsMargins(0, 0, 0, 0)
        self.location = QLabel()
        self.location.setWordWrap(True)
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow(self.location)
        address_row = QHBoxLayout()
        self.address = QComboBox()
        self.address.setMinimumContentsLength(16)
        self.address.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.detect_button = QPushButton("偵測 IP")
        self.detect_button.clicked.connect(self.detect)
        address_row.addWidget(self.address, 1)
        address_row.addWidget(self.detect_button)
        form.addRow("DUT 可連到的 IP", address_row)
        file_row = QHBoxLayout()
        self.choose_button = QPushButton("選擇檔案並準備")
        self.choose_button.setObjectName("netconfButton")
        self.choose_button.clicked.connect(self.choose_files)
        self.files_label = QLabel("尚未選檔")
        self.files_label.setWordWrap(True)
        file_row.addWidget(self.choose_button)
        file_row.addWidget(self.files_label, 1)
        form.addRow(file_row)
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setSpecialValueText("自動分配")
        self.port_label = QLabel("本機 SFTP port")
        form.addRow(self.port_label, self.port)
        self.jump_password = QLineEdit()
        self.jump_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.jump_password.setPlaceholderText("僅當跳板設定未提供登入密碼時填寫")
        self.password_label = QLabel("跳板 SFTP 密碼")
        form.addRow(self.password_label, self.jump_password)
        actions = QHBoxLayout()
        self.prepare_button = QPushButton("重新準備")
        self.prepare_button.setObjectName("netconfButton")
        self.prepare_button.clicked.connect(self.prepare)
        self.stop_button = QPushButton("停止本機 SFTP")
        self.stop_button.clicked.connect(self.stop_local)
        self.cleanup_button = QPushButton("清理此次跳板暫存")
        self.cleanup_button.clicked.connect(self.cleanup)
        actions.addWidget(self.prepare_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.cleanup_button)
        actions.addStretch(1)
        form.addRow(actions)
        self.status = QLabel("選擇 DUT 可到達的網卡 IP；偵測 IP 不代表 DUT 路由／防火牆已放行。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow(self.status)
        layout.addWidget(self.automatic)
        self.automatic.hide()
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.address.currentIndexChanged.connect(self._selection_changed)
        self.port.valueChanged.connect(self._selection_changed)
        self.jump_password.textChanged.connect(self._selection_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._show_progress)
        self.timer.start()

    @property
    def automatic_mode(self):
        return self.mode.currentData() == "auto"

    def settings(self):
        if self.window.client.connected and not self.window.demo:
            return deepcopy(self.window.client.context.settings)
        # Local enumeration/serving doesn't require completed NETCONF fields.
        if not self.window.checks["jump_enabled"].isChecked():
            from ..session import ConnectionSettings
            return ConnectionSettings(host=self.window.fields["host"].text().strip())
        return self.window._settings()

    @staticmethod
    def identity(settings):
        if not settings.jump_enabled:
            return ("local",)
        return ("jump", settings.jump_host, settings.jump_port, settings.jump_username,
                settings.jump_auth, settings.jump_password, settings.jump_key,
                settings.jump_passphrase, settings.jump_verify, settings.jump_known_hosts)

    def _mode_changed(self):
        automatic = self.automatic_mode
        self.automatic.setVisible(automatic)
        self.mode_changed.emit(automatic)
        if automatic:
            self.detect()
        else:
            self.invalidate()

    def invalidate(self):
        if isinstance(self.resource, LocalSftpSource):
            self.resource.close()
        elif isinstance(self.resource, JumpSftpSource):
            self.old_uploads.append(self.resource)
        self.resource = None
        self._credentials = None
        self._last_read = None
        self.invalidated.emit()

    def _selection_changed(self, *_args):
        if self._changing:
            return
        self.invalidate()
        self.status.setText("設定已變更，請按「重新準備」，再送出 Download。")

    def _job(self, label, work, done):
        w = self.window
        if w.busy or w._task_thread is not None or w._close_requested:
            return
        self.working = True
        self.cancel_event.clear()
        records = [record for record in w.session_records if record.status == "已訂閱"] if w.auto_supervision_reset.isChecked() else []
        def wrapped(progress):
            keeper = SupervisionKeeper(records, lambda record, result:
                                       self.owner.updates.put(("watchdog", (record, result))))
            def report(message):
                if keeper.failure:
                    raise EditError(keeper.failure)
                progress(message)
                self.owner.updates.put(("transfer_progress", message))
            keeper.start()
            try:
                return work(report)
            finally:
                keeper.close()
        def completed(result):
            self.working = False
            if not w._close_requested:
                w.client.cancel.clear()
            self.owner.drain_updates()
            done(result)
            self.owner.sync()
        def failed(exc):
            self.working = False
            if not w._close_requested:
                w.client.cancel.clear()
            self.owner.drain_updates()
            # Authentication errors are already sanitized by sshauth; avoid
            # exposing arbitrary remote errors alongside saved credentials.
            message = str(exc) if isinstance(exc, (EditError, InterruptedError)) else type(exc).__name__
            if isinstance(exc, OSError):
                message += "：" + (exc.strerror or "請檢查 IP／port 是否可綁定、檔案權限與連線狀態")
            elif "Authentication" in type(exc).__name__:
                message += "：請檢查跳板登入密碼及 SSH server 是否允許密碼登入"
            self.status.setText("準備未完成：" + message + "；可按重新偵測／準備重試。")
            self.owner.sync()
        w._run(label, wrapped, completed, failed)

    def detect(self):
        if not self.automatic_mode or self.window.busy or self.window._task_thread is not None or self.window._close_requested:
            return
        try:
            settings = self.settings()
        except Exception as exc:
            self.status.setText(str(exc))
            return
        identity = self.identity(settings)
        self.invalidate()
        self.address_identity = None
        self.observed_identity = identity
        self._changing = True
        self.address.clear()
        self._changing = False
        self.location.setText("來源：跳板 %s:%s，使用該機 SSH／SFTP 服務" %
                              (settings.jump_host, settings.jump_port) if settings.jump_enabled else
                              "來源：本機臨時 SFTP，僅分享所選檔案；關閉程式時停止。")
        self.port.setVisible(not settings.jump_enabled)
        self.port_label.setVisible(not settings.jump_enabled)
        needs_password = settings.jump_enabled and not settings.jump_password
        self.jump_password.setVisible(needs_password)
        self.password_label.setVisible(needs_password)
        def work(_progress):
            return jump_addresses(settings, self.cancel_event) if settings.jump_enabled else local_addresses(settings.host)
        def done(entries):
            if self.cancel_event.is_set() or self.window._close_requested:
                self.status.setText("已取消 IP 偵測。")
                return
            self.address_identity = identity
            self._changing = True
            for entry in entries:
                label = "%s · %s%s" % (entry["ip"], entry["interface"], " · 前往 DUT 的來源 IP" if entry["preferred"] else "")
                self.address.addItem(label, entry["ip"])
            self._changing = False
            self.status.setText("已偵測 %d 個 IP；選擇 DUT 可到達的 IP，再選檔。" % len(entries) if entries else
                                "未偵測到可用 IP；請確認網卡已啟用／跳板支援 ip 或 hostname -I。")
        self._job("偵測跳板 IP…" if settings.jump_enabled else "偵測本機 IP…", work, done)

    def choose_files(self):
        paths, _filter = QFileDialog.getOpenFileNames(self, "選擇要提供 DUT 下載的檔案", "", "所有檔案 (*)")
        if not paths:
            return
        from pathlib import Path
        try:
            total = sum(Path(p).stat().st_size for p in paths)
        except OSError:
            self.status.setText("選取的檔案無法讀取，請重新選擇。")
            return
        self.invalidate()
        self.paths = paths
        self.files_label.setText("%d 個檔案 · %.1f MiB" % (len(paths), total / 1048576))
        self.files_label.setToolTip("\n".join(paths))
        self.prepare()

    def prepare(self):
        if self.window.busy or self.window._task_thread is not None or self.window._close_requested:
            return
        try:
            settings = self.settings()
            if self.identity(settings) != self.address_identity or not self.address.currentData():
                raise EditError("請先偵測並選擇目前連線的 IP。")
            if not self.paths:
                raise EditError("請先選擇軟體檔案。")
            address = self.address.currentData()
            paths = tuple(self.paths)
            port = self.port.value()
            password = settings.jump_password or self.jump_password.text()
            if settings.jump_enabled and not password:
                raise EditError("請填寫跳板的 SSH 登入密碼；私鑰密碼無法作為 DUT 的 SFTP 密碼。")
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.invalidate()
        self.status.setText("準備中：上傳至跳板 /tmp…" if settings.jump_enabled else "正在啟動本機 SFTP…")
        def work(progress):
            if settings.jump_enabled:
                return upload_to_jump(settings, address, paths, password, progress, self.cancel_event)
            return LocalSftpSource(address, port, paths, self.cancel_event)
        def done(resource):
            if self.window._close_requested or self.cancel_event.is_set():
                if isinstance(resource, LocalSftpSource):
                    resource.close()
                else:
                    self.old_uploads.append(resource)
                self.status.setText("已取消；未帶入 Download。")
                return
            self.resource = resource
            self._credentials = {"uris": resource.uris, "auth": "password", "password": resource.password,
                                 "keys": resource.keys, "appl_password": ""}
            self.prepared.emit(dict(self._credentials))
            if isinstance(resource, LocalSftpSource):
                self.status.setText("SFTP 已啟動於 %s:%d · 帳號 %s · 已產生密碼／URI；等待 DUT 下載。" %
                                    (resource.address, resource.port, resource.username))
            else:
                note = "" if resource.keys else " 此主機金鑰無對應的 MP YANG identity，未帶入 server/keys；DUT 須已信任此檔案伺服器。"
                self.status.setText("已上傳 %d 個檔案至 %s；URI 與跳板登入密碼已帶入。%s" % (len(resource.names), resource.directory, note))
            self.window._audit_result("software-download-source", "本機 SFTP 已準備" if isinstance(resource, LocalSftpSource) else "已上傳至 " + resource.directory)
        self._job("上傳軟體檔案至跳板…" if settings.jump_enabled else "啟動本機 SFTP…", work, done)

    def credentials(self):
        if not self.automatic_mode:
            return None
        if self.resource is None or self._credentials is None:
            raise EditError("請先完成選檔與 SFTP 準備。")
        if self.identity(self.settings()) != self.address_identity:
            raise EditError("連線／跳板設定已改變，請重新偵測 IP 與準備檔案。")
        self.resource.validate()
        return dict(self._credentials)

    def cancel(self):
        if self.working:
            self.cancel_event.set()
            self.status.setText("已要求取消檔案準備；等待目前 SSH／SFTP 操作結束。")
            self.owner.sync()

    def stop_local(self):
        if self.window.busy:
            return
        if isinstance(self.resource, LocalSftpSource):
            self.invalidate()
            self.status.setText("本機 SFTP 已停止；重新準備會產生新的帳密與 URI。")
        self.owner.sync()

    def cleanup(self):
        if self.window.busy or self.window._task_thread is not None:
            return
        if isinstance(self.resource, JumpSftpSource):
            self.invalidate()
        targets = list(self.old_uploads)
        if not targets:
            return
        def work(progress):
            cleaned = []
            errors = []
            for source in targets:
                if self.cancel_event.is_set():
                    break
                try:
                    source.cleanup()
                    cleaned.append(source)
                    progress("已清理 " + source.directory)
                except Exception as exc:
                    errors.append(source.directory + "：" + type(exc).__name__)
            return cleaned, errors
        def done(result):
            cleaned, errors = result
            self.old_uploads = [source for source in self.old_uploads if source not in cleaned]
            self.status.setText("已清理 %d 個此次建立的跳板暫存目錄。%s" % (len(cleaned), "；".join(errors)))
        self._job("清理本次跳板暫存…", work, done)

    def close_local(self):
        if isinstance(self.resource, LocalSftpSource):
            self.resource.close()

    def _show_progress(self):
        if isinstance(self.resource, LocalSftpSource):
            value = self.resource.last_read
            if value and value != self._last_read:
                self._last_read = value
                name, count, size = value
                self.status.setText("DUT 讀取 %s：%d%%（%d / %d bytes）" % (name, min(100, count * 100 // max(1, size)), count, size))

    def sync(self):
        idle = not self.window.busy and not self.owner.active and not self.window._close_requested
        self.detect_button.setEnabled(idle)
        self.choose_button.setEnabled(idle and self.address.count() > 0)
        self.prepare_button.setEnabled(idle and bool(self.paths) and self.address.count() > 0)
        self.stop_button.setEnabled(idle and isinstance(self.resource, LocalSftpSource))
        self.cleanup_button.setEnabled(idle and (bool(self.old_uploads) or isinstance(self.resource, JumpSftpSource)))
        if self.automatic_mode and idle and not self.working:
            try:
                identity = self.identity(self.settings())
            except Exception:
                return
            if identity != self.observed_identity and not self._detect_scheduled:
                self._detect_scheduled = True
                def refresh():
                    self._detect_scheduled = False
                    self.detect()
                QTimer.singleShot(0, refresh)
