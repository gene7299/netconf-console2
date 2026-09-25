"""Software Update workspace: inventory, typed RPC forms and workflow progress."""

from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Event

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import software
from .model import EditError
from .software_update import SoftwareUpdate, display_xml
from .software_transfer_widgets import DownloadSourcePanel


class SoftwareUpdatePage(QWidget):
    def __init__(self, window, editor_class):
        super().__init__()
        self.window = window
        self.inventory = None
        self.inventory_manager = None
        self.active = False
        self.runner = None
        self.record = None
        self.updates = Queue()
        self.cancel_event = Event()
        self.manifests = []
        self._rendering = False
        self._manual_download = None
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        top = QHBoxLayout()
        self.refresh = QPushButton("讀取 Software Inventory")
        self.refresh.setObjectName("netconfButton")
        self.refresh.clicked.connect(self.read_inventory)
        self.auto_refresh = QCheckBox("顯示此頁時，每 5 秒更新")
        self.auto_refresh.setChecked(True)
        self.stamp = QLabel("尚未讀取 DUT")
        top.addWidget(self.refresh)
        top.addWidget(self.auto_refresh)
        top.addWidget(self.stamp, 1)
        body.addLayout(top)
        self.inventory_hint = QLabel("active = 下次開機使用；running = 目前執行。選取一列可帶入目標 slot。")
        self.inventory_hint.setWordWrap(True)
        body.addWidget(self.inventory_hint)
        self.table = QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setHeaderLabels(["Slot", "Status", "Active（下次開機）", "Running（目前）", "Access",
                                    "Build name", "Build version", "Build ID", "Product", "Vendor"])
        self.table.setUniformRowHeights(True)
        self.table.horizontalScrollBar().rangeChanged.connect(self._fit_inventory_height)
        self.table.installEventFilter(self)
        self._fit_inventory_height()
        self.table.itemSelectionChanged.connect(self.select_slot)
        body.addWidget(self.table)

        self.forms = QTabWidget()
        settings = QWidget()
        row = QHBoxLayout(settings)
        download_box = QGroupBox("1 · Download：O-RU 從檔案伺服器下載")
        download_form = QVBoxLayout(download_box)
        self.download_source = DownloadSourcePanel(self)
        download_form.addWidget(self.download_source)
        self.uris = QPlainTextEdit()
        self.uris.setPlaceholderText("每行一個完整 URI（可多檔）\nsftp://user@server/path/software.zip")
        self.uris.setFixedHeight(84)
        download_form.addWidget(self.uris)
        self.download_hint = QLabel("可自填 URI，或選擇檔案，自動準備本機／跳板 SFTP 來源。")
        self.download_hint.setWordWrap(True)
        download_form.addWidget(self.download_hint)
        row.addWidget(download_box, 1)

        install_box = QGroupBox("2 · Install／Activate：目標及檔案")
        install_form = QFormLayout(install_box)
        self.slot = QComboBox()
        self.slot.setMinimumContentsLength(18)
        self.slot.addItem("先讀取 DUT inventory", "")
        install_form.addRow("目標 slot", self.slot)
        self.files = QPlainTextEdit()
        self.files.setPlaceholderText("每行一個 manifest fileName；ZIP 名稱不一定是 file-names。")
        self.files.setFixedHeight(64)
        install_form.addRow("Install file-names", self.files)
        manifest_row = QHBoxLayout()
        self.import_manifest = QPushButton("匯入 manifest.xml")
        self.import_manifest.clicked.connect(self.load_manifest)
        self.build = QComboBox()
        self.build.addItem("選擇 manifest build", None)
        self.apply_build = QPushButton("帶入 Build")
        self.apply_build.clicked.connect(self.use_build)
        manifest_row.addWidget(self.import_manifest)
        manifest_row.addWidget(self.build, 1)
        manifest_row.addWidget(self.apply_build)
        install_form.addRow(manifest_row)
        self.expected_version = QLineEdit()
        self.expected_version.setPlaceholderText("選填；填寫後會比對 DUT build-version")
        install_form.addRow("預期版本", self.expected_version)
        row.addWidget(install_box, 1)
        self.forms.addTab(settings, "更新設定")

        advanced = QWidget()
        adv = QHBoxLayout(advanced)
        auth_form = QFormLayout()
        self.auth = QComboBox()
        self.auth.addItem("sFTP password", "password")
        self.auth.addItem("Certificate", "certificate")
        self.auth.addItem("設備既有認證（不帶 credentials）", "device")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.appl_password = QLineEdit()
        self.appl_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.keys = QPlainTextEdit()
        self.keys.setFixedHeight(68)
        self.keys.setPlaceholderText("選填：每行 algorithm base64-key，例如 rsa2048 …\n依 YANG 要求的二進位編碼（RSA 為 DER RSAPublicKey），非整行 ssh-rsa 公鑰。")
        auth_form.addRow("下載認證", self.auth)
        auth_form.addRow("檔案伺服器密碼", self.password)
        auth_form.addRow("FTPES appl-password", self.appl_password)
        auth_form.addRow("Server public keys", self.keys)
        self.keys.setToolTip("可用 algorithm：" + ", ".join(software.ALGORITHMS))
        adv.addLayout(auth_form, 3)
        timeouts = QFormLayout()
        self.install_timeout = QSpinBox()
        self.install_timeout.setRange(1, 86400)
        self.install_timeout.setValue(600)
        self.install_timeout.setSuffix(" 秒")
        self.reconnect_timeout = QSpinBox()
        self.reconnect_timeout.setRange(30, 3600)
        self.reconnect_timeout.setValue(300)
        self.reconnect_timeout.setSuffix(" 秒")
        timeouts.addRow("Install 備用等待時間", self.install_timeout)
        timeouts.addRow("Reset 後重連期限", self.reconnect_timeout)
        note = QLabel("優先採用 DUT 回覆的 timeout。\nDownload／Activate 未提供時依 YANG 使用 30 秒。\nFTPES 僅允許 NETCONF/TLS。\n密碼不儲存至設定，XML 預覽會遮罩。")
        note.setWordWrap(True)
        timeouts.addRow(note)
        adv.addLayout(timeouts, 2)
        self.forms.addTab(advanced, "認證／進階")
        body.addWidget(self.forms)

        manual = QHBoxLayout()
        manual.addWidget(QLabel("手動操作"))
        self.buttons = {}
        for operation, label in (("download", "Software Download"), ("install", "Software Install"),
                                 ("activate", "Software Activate"), ("reset", "Reset O-RU")):
            button = QPushButton(label)
            button.setObjectName("dangerButton" if operation == "reset" else "netconfButton")
            button.clicked.connect(lambda _checked=False, op=operation: self.start((op,)))
            self.buttons[operation] = button
            manual.addWidget(button)
        manual.addStretch(1)
        body.addLayout(manual)
        auto = QHBoxLayout()
        self.workflow = QComboBox()
        for label, stages in software.WORKFLOWS:
            self.workflow.addItem(label, stages)
        self.start_button = QPushButton("開始自動更新（含 Reset）")
        self.start_button.setObjectName("subscriptionButton")
        self.start_button.clicked.connect(lambda: self.start(self.workflow.currentData()))
        self.stop_button = QPushButton("停止後續步驟")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.cancel)
        auto.addWidget(QLabel("自動流程"))
        auto.addWidget(self.workflow, 1)
        auto.addWidget(self.start_button)
        auto.addWidget(self.stop_button)
        body.addLayout(auto)
        self.status = QLabel("依 MP §8／§9.5、CONF §3.1.6／§3.1.7；送出前重讀 slot 狀態。Reset 會重啟設備。")
        self.status.setWordWrap(True)
        body.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        body.addWidget(self.progress)

        outputs = QVBoxLayout()
        preview_row = QHBoxLayout()
        preview_row.addWidget(QLabel("RPC 內容預覽"))
        self.preview_kind = QComboBox()
        for label, op in (("自動流程主要 RPC", "workflow"), ("Software inventory", "inventory"),
                          ("Software Download", "download"), ("Software Install", "install"),
                          ("Software Activate", "activate"), ("Reset", "reset"),
                          ("Software events 訂閱", "subscribe")):
            self.preview_kind.addItem(label, op)
        preview_row.addWidget(self.preview_kind)
        preview_row.addStretch(1)
        self.copy_button = QPushButton("複製 XML（密碼遮罩）")
        self.copy_button.clicked.connect(self.copy_preview)
        preview_row.addWidget(self.copy_button)
        outputs.addLayout(preview_row)
        self.output_tabs = QTabWidget()
        self.preview = editor_class()
        self.reply = editor_class()
        self.event_view = editor_class()
        self.inventory_xml = editor_class()
        for editor in (self.preview, self.reply, self.event_view, self.inventory_xml):
            editor.setReadOnly(True)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        self.file_table = QTreeWidget()
        self.file_table.setRootIsDecorated(False)
        self.file_table.setHeaderLabels(["Slot", "File name", "Version", "Local path", "Integrity"])
        for widget, label in ((self.preview, "待送出 RPC（唯讀）"), (self.reply, "最後 RPC 回應"),
                              (self.event_view, "最後完成通知"), (self.log_view, "流程紀錄"),
                              (self.file_table, "Slot 檔案"), (self.inventory_xml, "Inventory XML")):
            self.output_tabs.addTab(widget, label)
        self.output_tabs.setMinimumHeight(190)
        self.output_tabs.setMaximumHeight(280)
        outputs.addWidget(self.output_tabs, 1)
        body.addLayout(outputs, 1)
        for widget in (self.uris, self.files, self.keys):
            widget.textChanged.connect(self.update_preview)
        for widget in (self.password, self.appl_password, self.expected_version):
            widget.textChanged.connect(self.update_preview)
        for widget in (self.slot, self.auth, self.workflow, self.preview_kind):
            widget.currentIndexChanged.connect(self.update_preview)
        self.download_source.mode_changed.connect(self._download_mode_changed)
        self.download_source.prepared.connect(self._apply_download_source)
        self.download_source.invalidated.connect(self._invalidate_download_source)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.drain_updates)
        self.timer.start()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self.refresh_if_visible)
        self.refresh_timer.start()
        self.update_preview()

    def options(self):
        return {"slot": self.slot.currentData() or "", "uris": software.lines(self.uris.toPlainText()),
                "files": software.lines(self.files.toPlainText()), "auth": self.auth.currentData(),
                "password": self.password.text(), "appl_password": self.appl_password.text(),
                "keys": self.keys.toPlainText(), "install_timeout": self.install_timeout.value(),
                "reconnect_timeout": self.reconnect_timeout.value(),
                "expected_version": self.expected_version.text().strip()}

    def plans(self, stages, options):
        plans = []
        for stage in stages:
            if stage == "download":
                if not options["uris"]:
                    raise EditError("請填入至少一個下載 URI。")
                for uri in options["uris"]:
                    plans.append(software.operation_rpc(stage, options, uri))
            else:
                plans.append(software.operation_rpc(stage, options))
        return plans

    def update_preview(self, *_args):
        if self._rendering:
            return
        try:
            kind = self.preview_kind.currentData()
            if kind == "inventory":
                plans = [software.inventory_rpc()]
            elif kind == "subscribe":
                plans = [software.subscription_rpc()]
            else:
                plans = self.plans(self.workflow.currentData() if kind == "workflow" else (kind,), self.options())
            # Preview IDs are stable; execute builds new IDs per real request.
            parts = []
            for plan in plans:
                plan.rpc.set("message-id", "preview")
                from lxml import etree
                parts.append(display_xml(etree.tostring(plan.rpc, encoding="unicode")))
            self.preview.setPlainText("\n\n".join(parts))
        except Exception as exc:
            self.preview.setPlainText("尚需填寫：" + str(exc))
        has_reset = "reset" in self.workflow.currentData()
        self.start_button.setText("開始自動更新（含 Reset）" if has_reset else "開始自動更新")
        manual_source = not self.download_source.automatic_mode
        self.auth.setEnabled(manual_source)
        self.password.setEnabled(manual_source and self.auth.currentData() == "password")
        self.keys.setEnabled(manual_source and self.auth.currentData() == "password")
        self.appl_password.setEnabled(manual_source)

    def _download_mode_changed(self, automatic):
        if automatic:
            self._manual_download = {"uris": software.lines(self.uris.toPlainText()), "auth": self.auth.currentData(),
                                     "password": self.password.text(), "keys": self.keys.toPlainText(),
                                     "appl_password": self.appl_password.text()}
            self._invalidate_download_source()
        elif self._manual_download is not None:
            self._apply_download_source(self._manual_download)
        self.uris.setReadOnly(automatic)
        self.update_preview()

    def _apply_download_source(self, values):
        self._rendering = True
        self.uris.setPlainText("\n".join(values["uris"]))
        self.auth.setCurrentIndex(max(0, self.auth.findData(values["auth"])))
        self.password.setText(values["password"])
        self.keys.setPlainText(values["keys"])
        self.appl_password.setText(values["appl_password"])
        self._rendering = False
        self.update_preview()
        self.sync()

    def _invalidate_download_source(self):
        if self.download_source.automatic_mode:
            self._apply_download_source({"uris": [], "auth": "password", "password": "", "keys": "", "appl_password": ""})

    def copy_preview(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.preview.toPlainText())

    def select_slot(self):
        if self._rendering or self.active:
            return
        selected = self.table.selectedItems()
        if selected:
            index = self.slot.findData(selected[0].text(0))
            self.slot.setCurrentIndex(max(0, index))
            if index < 0:
                self.status.setText("所選 slot 為 READ_ONLY／factory slot，不可作為一般更新目標。")

    def eventFilter(self, watched, event):
        if watched is self.table and event.type() in {
                QEvent.Type.Show, QEvent.Type.FontChange, QEvent.Type.StyleChange}:
            QTimer.singleShot(0, self._fit_inventory_height)
        return super().eventFilter(watched, event)

    def _fit_inventory_height(self, *_args):
        # Reserve a header and two slots, including the horizontal scrollbar
        # when needed. Extra device slots remain accessible by vertical scroll.
        row_height = self.table.sizeHintForRow(0)
        if row_height <= 0:
            row_height = self.table.fontMetrics().height() + 4
        bar = self.table.horizontalScrollBar()
        scroll_height = bar.sizeHint().height() if bar.maximum() > bar.minimum() else 0
        height = (self.table.header().sizeHint().height() + 2 * row_height
                  + 2 * self.table.frameWidth() + scroll_height)
        if self.table.minimumHeight() != height or self.table.maximumHeight() != height:
            self.table.setFixedHeight(height)

    def show_inventory(self, inventory):
        self.inventory = inventory
        self.inventory_manager = self.window.client.manager if self.window.client.connected else None
        previous = self.slot.currentData()
        self._rendering = True
        self.table.clear()
        self.file_table.clear()
        self.slot.clear()
        self.slot.addItem("選擇目標 slot", "")
        colors = {"VALID": "#15803d", "INVALID": "#b91c1c", "EMPTY": "#64748b"}
        for slot in inventory.slots:
            values = [slot[key] or "—" for key in ("name", "status", "active", "running", "access",
                                                    "build-name", "build-version", "build-id", "product-code", "vendor-code")]
            item = QTreeWidgetItem(values)
            self.table.addTopLevelItem(item)
            item.setForeground(1, QBrush(QColor(colors.get(slot["status"], "#a16207"))))
            for column in (2, 3):
                if values[column] in {"true", "1"}:
                    item.setForeground(column, QBrush(QColor("#15803d")))
                    font = item.font(column)
                    font.setBold(True)
                    item.setFont(column, font)
            if slot["access"] == "READ_WRITE":
                self.slot.addItem("%s · %s · active=%s / running=%s" %
                                  (slot["name"], slot["status"], slot["active"] or "—", slot["running"] or "—"), slot["name"])
                if slot["name"] == previous:
                    item.setSelected(True)
            else:
                item.setToolTip(0, "Factory slot；啟用會回復原廠設定，不提供於一般軟體更新目標。")
            for entry in slot["files"]:
                file_item = QTreeWidgetItem([slot["name"]] + [entry[key] or "—" for key in ("name", "version", "local-path", "integrity")])
                self.file_table.addTopLevelItem(file_item)
                file_item.setForeground(4, QBrush(QColor({"NOK": "#b91c1c", "OK": "#15803d"}.get(entry["integrity"], "#64748b"))))
        self.slot.setCurrentIndex(max(0, self.slot.findData(previous)))
        for table in (self.table, self.file_table):
            for column in range(table.columnCount()):
                table.resizeColumnToContents(column)
        self._fit_inventory_height()
        self._rendering = False
        self.stamp.setText("更新：" + datetime.now().strftime("%H:%M:%S"))
        self.inventory_hint.setText("%d 個 slots · active = 下次開機，running = 目前執行 · 下載模式：%s · 下載完整性檢查：%s" % (
            len(inventory.slots), "逐檔下載（build-content-download）" if inventory.individual_files else "封裝套件",
            "啟用" if inventory.integrity_at_download else "未宣告啟用"))
        self.download_hint.setText(("DUT 要求逐檔下載：請列出 manifest 所需的所有檔案 URI。" if inventory.individual_files else
                                   "DUT 使用封裝套件：請填入套件 URI；Install 使用套件內的 fileName。") + " DUT 必須可連到檔案伺服器。")
        if not self.active:
            self.update_preview()

    def read_inventory(self):
        w = self.window
        if w.busy or self.active or w._task_thread is not None or not w.client.connected or w.demo or w._close_requested:
            return
        from . import raw_rpc
        manager = w.client.manager
        prepared = software.inventory_rpc()
        def work(_progress):
            reply = raw_rpc.execute(w.client, manager, prepared)
            return reply, software.parse_inventory(reply)
        def done(result):
            reply, inventory = result
            self.show_inventory(inventory)
            self.inventory_xml.setPlainText(display_xml(reply))
            w._record_session_action(w.main_session_record, "get software-inventory", "完成")
        def failed(exc):
            self.stamp.setText("讀取失敗；顯示上次資料")
            self.status.setText(str(exc))
            self.auto_refresh.setChecked(False)
        w._run("讀取 Software Inventory…", work, done, failed)

    def refresh_if_visible(self):
        if self.isVisible() and self.auto_refresh.isChecked():
            self.read_inventory()

    def load_manifest(self):
        name, _filter = QFileDialog.getOpenFileName(self, "匯入 Software manifest", "", "XML (*.xml);;All files (*)")
        if not name:
            return
        try:
            self.manifests = software.read_manifest(Path(name).read_text(encoding="utf-8-sig"))
            self.build.clear()
            if len(self.manifests) > 1:
                self.build.addItem("請選擇符合 DUT 的 build", None)
            for build in self.manifests:
                self.build.addItem("%s / %s (id=%s)" % (build["name"], build["version"], build["id"]), build)
            if len(self.manifests) == 1:
                self.use_build()
            else:
                self.status.setText("Manifest 包含多個 build；請選擇符合 DUT product／vendor 的 build，再按「帶入 Build」。")
        except Exception as exc:
            self.status.setText("Manifest 讀取失敗：" + str(exc))

    def use_build(self):
        build = self.build.currentData()
        if build:
            self.files.setPlainText("\n".join(build["files"]))
            self.expected_version.setText(build["version"])
            self.status.setText("已帶入 build 的 fileName 與版本；請確認 product／vendor 相容性並填入下載 URI。")

    def start(self, stages):
        w = self.window
        if w.busy or self.active or w._task_thread is not None or not w.client.connected or w.demo or w._pending() or w._close_requested:
            return
        try:
            options = self.options()
            if "download" in stages and self.download_source.automatic_mode:
                options.update(self.download_source.credentials())
            self.plans(stages, options)
            for uri in options["uris"] if "download" in stages else ():
                software.validate_uri(uri, w.client.context.settings.transport)
            if not w._preserve_current_draft(flush=True):
                return
            settings = w.client.context.settings
            self.record = w._new_session_record("Software Update 專用通知") if any(s in software.EVENTS for s in stages) else None
            if self.record:
                self.record.stream = "NETCONF"
                self.record.software_owned = True
            self.cancel_event.clear()
            watchdog_records = [record for record in w.session_records if record.status == "已訂閱"] if w.auto_supervision_reset.isChecked() else []
            self.runner = SoftwareUpdate(w.client, settings, options, stages, self.record, self.updates, self.cancel_event, watchdog_records)
            self.active = True
            self.log_view.clear()
            self.reply.clear()
            self.event_view.clear()
            self.progress.setRange(0, self.runner.total)
            self.progress.setValue(0)
            self.status.setText("執行中：" + " → ".join(stages) + "；停止只取消後續步驟，已送出操作仍可能在 DUT 執行。")
            self.status.setStyleSheet("")
            self.output_tabs.setCurrentWidget(self.log_view)
            self.sync()
            w._run("Software Update 執行中…", self.runner.run, self.finished,
                   lambda exc: self.finished({"ok": False, "message": self.runner.safe_error(exc)}))
        except Exception as exc:
            self.status.setText(str(exc))

    def cancel(self):
        self.download_source.cancel()
        if self.active:
            self.cancel_event.set()
            self.window.client.cancel.set()
            if self.record and self.record.client:
                self.record.client.cancel.set()
            self.status.setText("已要求停止後續步驟；等待目前 RPC 結束。已送出的 DUT 作業不會被撤銷。")
            self.stop_button.setEnabled(False)

    def drain_updates(self):
        received = False
        while True:
            try:
                kind, value = self.updates.get_nowait()
            except Empty:
                break
            if kind == "inventory":
                self.show_inventory(value)
            elif kind == "rpc":
                self._last_operation, xml = value
                self.preview.setPlainText(xml)
            elif kind == "reply":
                self.reply.setPlainText(value)
                if getattr(self, "_last_operation", "") == "get software-inventory":
                    self.inventory_xml.setPlainText(value)
            elif kind == "event":
                self.event_view.setPlainText(value)
            elif kind == "inventory_xml":
                self.inventory_xml.setPlainText(value)
            elif kind == "log":
                self.log_view.appendPlainText(value)
            elif kind == "transfer_progress":
                self.download_source.status.setText(value)
            elif kind == "progress":
                current, total = value
                self.progress.setRange(0, total)
                self.progress.setValue(current)
            elif kind == "action":
                subscriber, operation, result = value
                record = self.record if subscriber else self.window.main_session_record
                if record:
                    self.window._record_session_action(record, operation, result)
            elif kind == "watchdog":
                record, result = value
                self.window._record_session_action(record, "supervision-watchdog-reset", result)
            elif kind == "notification":
                self.window.notifications.append(value)
                self.window._record_notification(value, self.record)
                received = True
            elif kind == "session":
                self.window._refresh_session_table()
            elif kind == "main_disconnected":
                record = self.window.main_session_record
                if record:
                    record.status = "已中斷"
                    record.manager = None
                self.window._refresh_session_table()
            elif kind == "main_connected":
                self.window._register_main_session()
                self.window.uncertain = True
        if received:
            self.window.notification_count.setText("目前保留 %d 筆通知（最多 100 筆）" % len(self.window.notifications))
            self.window._refresh_notifications(select_latest=True)

    def finished(self, result):
        self.drain_updates()
        self.active = False
        if not self.window._close_requested:
            self.window.client.cancel.clear()
        if self.record and self.record.status == "連線中":
            self.record.status = "建立失敗"
        self.window.uncertain = True
        message = ("完成：" if result["ok"] else "已停止：") + result["message"]
        if self.runner and self.runner.reconnected and self.window.client.connected:
            message += " 主連線已重建；請重新載入 Schema／資料後再編輯。"
        self.status.setText(message)
        self.status.setStyleSheet("color: %s;" % ("#15803d" if result["ok"] else "#b91c1c"))
        self.log_view.appendPlainText(message)
        self.window._audit_result("software-update", "完成" if result["ok"] else "停止／待確認")
        self.window._sync_notification_manager()
        self.window._refresh_session_table()
        self.runner = None
        self.sync()

    def new_main_session(self):
        if self.active:
            return
        self.inventory = None
        self.inventory_manager = None
        self.table.clear()
        self.file_table.clear()
        self.inventory_xml.clear()
        self.slot.clear()
        self.slot.addItem("先讀取 DUT inventory", "")
        self.stamp.setText("新連線：請讀取 inventory")
        self.inventory_hint.setText("active = 下次開機使用；running = 目前執行。Slot 清單來自目前 DUT。")

    def sync(self):
        w = self.window
        enabled = not w.busy and not self.active and not w._close_requested
        connected = w.client.connected and not w.demo
        available = enabled and connected and not w._pending()
        self.refresh.setEnabled(enabled and connected)
        self.forms.setEnabled(enabled)
        self.workflow.setEnabled(enabled)
        self.preview_kind.setEnabled(enabled)
        source_ready = not self.download_source.automatic_mode or self.download_source.resource is not None
        self.start_button.setEnabled(available and (source_ready or "download" not in self.workflow.currentData()))
        for operation, button in self.buttons.items():
            button.setEnabled(available and (source_ready or operation != "download"))
        preparing = self.download_source.working
        self.stop_button.setText("停止檔案準備" if preparing else "停止後續步驟")
        self.stop_button.setEnabled((self.active and not self.cancel_event.is_set()) or
                                   (preparing and not self.download_source.cancel_event.is_set()))
        self.download_source.sync()
        if not connected and not self.active:
            self.stamp.setText("未連線；inventory 為上次讀取資料" if self.inventory else "請先建立主 NETCONF 連線")
