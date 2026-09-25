"""Visible YANG value choices shared by FM and NETCONF subscription templates."""
from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import events, raw_rpc, subscription_templates as templates


class NotificationFields(QTreeWidget):
    values_changed = Signal()

    def __init__(self, definition, selected=None, whole_groups=(), observations=None):
        super().__init__()
        self.definition = definition
        self._filter_expansion = None
        self.field_items, self.value_items, self.inputs, self.group_items = {}, {}, {}, {}
        self.setHeaderLabels(["欄位 / 可勾選值", "過濾值 / 說明", "YANG 型別 / 限制"])
        self.setColumnWidth(0, 270)
        self.setColumnWidth(1, 320)
        self.setAlternatingRowColors(True)
        self.setMinimumHeight(180)
        if definition["name"] == "measurement-result-stats":
            for group in templates.bundled_catalog()["measurement_groups"]:
                item = QTreeWidgetItem(self, [group, "勾選接收此群組全部 objects", "完整群組"])
                item.setCheckState(0, Qt.CheckState.Checked if group in whole_groups else Qt.CheckState.Unchecked)
                self.group_items[group] = item
        priority = {"fault-severity": 0, "is-cleared": 1, "alarm-type": 2, "fault-id": 3}
        fields = sorted(definition["fields"], key=lambda f: priority.get(f["path"][0], 4))
        for field in fields:
            path = tuple(field["path"])
            detail = field["type"]
            if field["base"] != field["type"]:
                detail += " → " + field["base"]
            detail += " " + "; ".join(k + " " + v for k, v in field["constraints"].items())
            if field["base"].startswith("uint") and field["base"][4:].isdigit() and "range" not in field["constraints"]:
                detail += " 0.." + str(2 ** int(field["base"][4:]) - 1)
            item = QTreeWidgetItem(self, [" / ".join(path), "不限制", detail.strip()])
            item.setToolTip(0, field["description"] + "\n" + "\n".join(field["conditions"]))
            item.setToolTip(2, detail)
            self.field_items[path] = item
            if field["values"]:
                for value in field["values"]:
                    label = {"CRITICAL": "危急", "MAJOR": "重大", "MINOR": "輕微", "WARNING": "警告",
                             "true": "是", "false": "否"}.get(value, "")
                    if path == ("is-cleared",):
                        label = "告警已清除" if value == "true" else "告警發生 / 仍有效"
                    child = QTreeWidgetItem(item, [value, label, "YANG 可選值"])
                    child.setToolTip(0, value)
                    child.setCheckState(0, Qt.CheckState.Unchecked)
                    self.value_items[path, value] = child
                item.setExpanded(len(path) == 1 or len(fields) < 10)
            else:
                box = QComboBox()
                box.setEditable(True)
                box.addItem("")
                box.lineEdit().setPlaceholderText("不限制；可輸入精確值或選擇 DUT 已回報值")
                box.setToolTip(field["description"] + "\n" + detail)
                self.setItemWidget(item, 1, box)
                self.inputs[path] = box
                box.currentTextChanged.connect(lambda _text: self.values_changed.emit())
                if definition["name"] == "alarm-notif" and path == ("fault-id",):
                    for fault in templates.bundled_catalog().get("common_faults", ()):
                        value = str(fault["id"])
                        child = QTreeWidgetItem(item, [value, fault["name"], "MP Annex A · p.%s · DUT 未確認" % fault["page"]])
                        child.setCheckState(0, Qt.CheckState.Unchecked)
                        self.value_items[path, value] = child
                    item.setToolTip(0, "展開可勾選 Annex A 共通 fault IDs，或輸入自訂 ID（0..65535；vendor-specific 1000..65535）。")
        for path, values in (selected or {}).items():
            field = next((f for f in fields if tuple(f["path"]) == tuple(path)), None)
            for value in values:
                if field is None or (field["values"] and value not in field["values"]):
                    item = QTreeWidgetItem(self, [" / ".join(path) + " = " + value,
                        "目前 YANG 未定義，請取消此條件或重新載入範例", "無效條件"])
                    self.value_items[tuple(path), value] = item
        self.set_selections(selected or {})
        self.add_observations(observations or {})
        self.itemChanged.connect(self._changed)

    def _changed(self, item, column):
        if column != 0:
            return
        if item.parent() is not None:
            parent = item.parent()
            values = [parent.child(i).text(0) for i in range(parent.childCount())
                      if parent.child(i).checkState(0) == Qt.CheckState.Checked]
            parent.setText(1, " OR ".join(values) or "不限制")
        self.values_changed.emit()

    def selections(self):
        result = {}
        for (path, value), item in self.value_items.items():
            if item.checkState(0) == Qt.CheckState.Checked:
                result.setdefault(path, []).append(value)
        for path, box in self.inputs.items():
            if box.currentText().strip():
                result.setdefault(path, [])
                if box.currentText().strip() not in result[path]:
                    result[path].append(box.currentText().strip())
        return result

    def whole_groups(self):
        return [group for group, item in self.group_items.items() if item.checkState(0) == Qt.CheckState.Checked]

    def set_selections(self, values):
        self.blockSignals(True)
        try:
            for (path, value), item in self.value_items.items():
                item.setCheckState(0, Qt.CheckState.Checked if value in values.get(path, ()) else Qt.CheckState.Unchecked)
            for path, box in self.inputs.items():
                box.blockSignals(True)
                box.setCurrentText(next((v for v in values.get(path, ()) if (path, v) not in self.value_items), ""))
                box.blockSignals(False)
            for path, item in self.field_items.items():
                if item.childCount():
                    item.setText(1, " OR ".join(values.get(path, ())) or "不限制")
        finally:
            self.blockSignals(False)

    def add_observations(self, observations):
        for path, values in observations.items():
            box = self.inputs.get(tuple(path))
            item = self.field_items.get(tuple(path))
            if item is not None:
                item.setToolTip(1, "DUT 目前值（非全部支援值）：\n" + "\n".join(sorted(values)))
            if box is not None:
                text = box.currentText()
                box.blockSignals(True)
                for value in sorted(values):
                    if box.findText(value) < 0:
                        box.addItem(value)
                box.setCurrentText(text)
                box.blockSignals(False)

    def filter_rows(self, query="", selected_only=False):
        """Filter the view without changing any content-match conditions."""
        query = query.strip().casefold()
        filtering = bool(query or selected_only)
        roots = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        if filtering and self._filter_expansion is None:
            self._filter_expansion = [item.isExpanded() for item in roots]
        values = self.selections()
        path_by_id = {id(item): path for path, item in self.field_items.items()}
        visible = 0
        self.blockSignals(True)
        try:
            for index, item in enumerate(roots):
                path = path_by_id.get(id(item))
                active = bool(values.get(path)) if path else item.checkState(0) == Qt.CheckState.Checked
                row_text = " ".join(item.text(c) for c in ((0, 2) if item.childCount() else (0, 1, 2)))
                box = self.inputs.get(path)
                if box is not None:
                    row_text += " " + box.currentText()
                # Search field names/types/descriptions, and the child value text.
                field_match = not query or query in (row_text + " " + item.toolTip(0)).casefold()
                visible_children = 0
                for child_index in range(item.childCount()):
                    child = item.child(child_index)
                    child_match = field_match or query in " ".join(child.text(c) for c in range(3)).casefold()
                    show = child_match and (not selected_only or child.checkState(0) == Qt.CheckState.Checked)
                    child.setHidden(not show)
                    visible_children += int(show)
                show = (field_match and (not selected_only or active)) or bool(visible_children)
                item.setHidden(not show)
                visible += int(show)
                if filtering and visible_children:
                    item.setExpanded(True)
                elif not filtering and self._filter_expansion is not None:
                    item.setExpanded(self._filter_expansion[index])
            if not filtering:
                self._filter_expansion = None
        finally:
            self.blockSignals(False)
        return visible, len(roots)


class NotificationTemplatePanel(QWidget):
    apply_requested = Signal(str)
    send_requested = Signal(str)

    def __init__(self, editor_class, *, fault_only=False):
        super().__init__()
        self.fault_only = fault_only
        self.catalog = deepcopy(templates.bundled_catalog()["notifications"])
        self.selected, self.groups, self.observations = {}, {}, {}
        self.current_name = ""
        self.fields = None
        self.filter_xml = ""
        self.send_allowed = False
        self.send_reason = "連線後可直接送出；每次訂閱使用獨立 session。"
        self.undo_state = None
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        self.content_scroll.setWidget(content)
        root_layout.addWidget(self.content_scroll, 1)
        self.source = QLabel("來源：MP v17.01 §11.3 / §9.1.7 與配套 YANG；尚未比對 DUT schema。")
        self.source.setWordWrap(True)
        layout.addWidget(self.source)
        presets = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.addItems(("所有告警（不限制欄位）", "§11.3：CRITICAL + MAJOR + MINOR", "只收 CRITICAL", "未清除的 CRITICAL / MAJOR") if fault_only else (
            "§11.3 Case 2：NETCONF 全部通知", "§11.3 Case 1：告警 + Transceiver + RX_ON_TIME",
            "軟體下載 / 安裝 / 啟用（§8）", "軟體下載失敗（§8.5）", "TX / RX 載波狀態（§9.1.8）",
            "同步狀態（§13.1）", "EPE POWER（CONF §3.1.14.3）"))
        presets.addWidget(self.preset, 1)
        load = QPushButton("載入範例")
        load.clicked.connect(self.load_preset)
        presets.addWidget(load)
        clear = QPushButton("清除此通知條件")
        clear.clicked.connect(self.clear_conditions)
        presets.addWidget(clear)
        self.undo_button = QPushButton("復原")
        self.undo_button.setEnabled(False)
        self.undo_button.setToolTip("復原上次載入範例或清除條件；保留目前工作的選擇。")
        self.undo_button.clicked.connect(self.undo)
        presets.addWidget(self.undo_button)
        layout.addLayout(presets)
        hint = QLabel("同欄位多選＝符合任一值；空白欄位＝不限制。文字條件為精確比對。"
                      "載入範例後可自行調整，按「復原」回到載入前的設定。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.all_events = QCheckBox("接收 NETCONF 全部通知（不加 filter）")
        self.all_events.setVisible(not fault_only)
        layout.addWidget(self.all_events)
        split = QSplitter(Qt.Orientation.Horizontal)
        # Preserve usable lists when the main window is short; the content
        # scrolls while the action row stays accessible at the bottom.
        split.setMinimumHeight(260)
        self.event_area = QWidget()
        event_layout = QVBoxLayout(self.event_area)
        event_layout.setContentsMargins(0, 0, 0, 0)
        self.event_search = QLineEdit()
        self.event_search.setPlaceholderText("搜尋通知、分類或模組，例如 software / 載波")
        self.event_search.setClearButtonEnabled(True)
        event_layout.addWidget(self.event_search)
        self.events_selected_only = QCheckBox("只看已選通知")
        event_layout.addWidget(self.events_selected_only)
        self.event_tree = QTreeWidget()
        self.event_tree.setHeaderLabels(["選擇 Notification", "DUT schema"])
        self.event_tree.setColumnWidth(0, 290)
        self.event_tree.setColumnWidth(1, 130)
        self.event_tree.setMinimumWidth(310)
        self.event_items = {}
        for name, definition in sorted(self.catalog.items()):
            if fault_only and name != "alarm-notif":
                continue
            item = QTreeWidgetItem(self.event_tree, [name, "尚未比對"])
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setToolTip(0, definition["module"] + "\n" + definition["description"])
            self.event_items[name] = item
        event_layout.addWidget(self.event_tree, 1)
        self.event_count = QLabel()
        event_layout.addWidget(self.event_count)
        split.addWidget(self.event_area)
        self.event_area.setVisible(not fault_only)
        self.event_search.textChanged.connect(self._filter_events)
        self.events_selected_only.toggled.connect(self._filter_events)
        self.field_area = QWidget()
        self.field_layout = QVBoxLayout(self.field_area)
        self.field_layout.setContentsMargins(0, 0, 0, 0)
        self.field_title = QLabel()
        self.field_title.setWordWrap(True)
        self.field_layout.addWidget(self.field_title)
        field_tools = QHBoxLayout()
        self.field_search = QLineEdit()
        self.field_search.setClearButtonEnabled(True)
        self.field_search.setPlaceholderText("搜尋欄位或可選值，例如 severity / POWER / temperature")
        field_tools.addWidget(self.field_search, 1)
        self.fields_selected_only = QCheckBox("只看已設定條件")
        field_tools.addWidget(self.fields_selected_only)
        self.field_layout.addLayout(field_tools)
        self.field_count = QLabel()
        self.field_layout.addWidget(self.field_count)
        self.field_search.textChanged.connect(self._filter_fields)
        self.fields_selected_only.toggled.connect(self._filter_fields)
        split.addWidget(self.field_area)
        split.setStretchFactor(1, 2)
        split.setSizes([440, 850])
        layout.addWidget(split, 1)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(64)
        self.summary.setMinimumHeight(50)
        self.summary.setStyleSheet("QPlainTextEdit {background: #eaf2fc; border: 1px solid #b9cee6;}")
        layout.addWidget(self.summary)
        self.preview_status = QLabel()
        self.preview_status.setWordWrap(True)
        layout.addWidget(self.preview_status)
        self.preview = editor_class()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(80)
        self.preview.setMaximumHeight(130)
        layout.addWidget(self.preview)
        actions = QHBoxLayout()
        self.copy_button = QPushButton("複製 RPC XML")
        self.copy_button.clicked.connect(self.copy_xml)
        actions.addWidget(self.copy_button)
        self.apply_button = QPushButton("帶入 Subscription（先不送出）")
        self.apply_button.clicked.connect(self.apply)
        actions.addWidget(self.apply_button)
        self.connection_hint = QLabel(self.send_reason)
        self.connection_hint.setWordWrap(True)
        actions.addWidget(self.connection_hint, 1)
        self.send_button = QPushButton("送出Create Subscription")
        self.send_button.setStyleSheet("QPushButton {background: #2563eb; color: white; padding: 6px;} QPushButton:disabled {background: #d8e1ec; color: #708096;}")
        self.send_button.clicked.connect(self.send)
        actions.addWidget(self.send_button)
        root_layout.addLayout(actions)
        self.event_tree.currentItemChanged.connect(self._select_event)
        self.event_tree.itemChanged.connect(self._event_changed)
        self.all_events.toggled.connect(self.refresh_preview)
        self.load_preset()
        self.undo_state = None
        self.undo_button.setEnabled(False)

    def _save_fields(self):
        if self.fields is not None:
            self.selected[self.current_name] = self.fields.selections()
            self.groups[self.current_name] = self.fields.whole_groups()

    def _filter_events(self, *_):
        query = self.event_search.text().strip().casefold()
        visible = selected = 0
        for name, item in self.event_items.items():
            checked = item.checkState(0) == Qt.CheckState.Checked
            selected += int(checked)
            definition = self.catalog[name]
            text = " ".join((name, events.event_category(name), definition["module"], definition["description"]))
            show = (not query or query in text.casefold()) and (not self.events_selected_only.isChecked() or checked)
            item.setHidden(not show)
            visible += int(show)
        self.event_count.setText("顯示 %d / %d 種 · 已選 %d 種（含搜尋隱藏項目）" % (visible, len(self.event_items), selected))

    def _filter_fields(self, *_):
        if self.fields is not None:
            visible, total = self.fields.filter_rows(self.field_search.text(), self.fields_selected_only.isChecked())
            self.field_count.setText("顯示 %d / %d 個欄位 · 搜尋只影響顯示，不會取消條件" % (visible, total))

    def _remember_undo(self):
        if self.fields is None:
            return
        self._save_fields()
        self.undo_state = dict(selected=deepcopy(self.selected), groups=deepcopy(self.groups),
            names=[name for name, item in self.event_items.items() if item.checkState(0) == Qt.CheckState.Checked],
            current=self.current_name, all_events=self.all_events.isChecked())
        self.undo_button.setEnabled(True)

    def undo(self):
        if self.undo_state is None:
            return
        state, self.undo_state = self.undo_state, None
        if self.fields is not None:
            self.field_layout.removeWidget(self.fields)
            self.fields.deleteLater()
            self.fields = None
        self.selected, self.groups = state["selected"], state["groups"]
        self.event_tree.blockSignals(True)
        for name, item in self.event_items.items():
            item.setCheckState(0, Qt.CheckState.Checked if name in state["names"] else Qt.CheckState.Unchecked)
        item = self.event_items[state["current"]]
        self.event_tree.setCurrentItem(item)
        self.event_tree.blockSignals(False)
        self._select_event(item)
        self.all_events.blockSignals(True)
        self.all_events.setChecked(state["all_events"])
        self.all_events.blockSignals(False)
        self.undo_button.setEnabled(False)
        self.refresh_preview()

    def set_send_available(self, enabled, reason):
        self.send_allowed, self.send_reason = bool(enabled), reason
        self.connection_hint.setText(reason)
        self.send_button.setToolTip(reason)
        self.send_button.setEnabled(self.send_allowed and self.apply_button.isEnabled())

    def apply(self):
        self.refresh_preview()
        if self.apply_button.isEnabled():
            self.apply_requested.emit(self.filter_xml)

    def send(self):
        self.refresh_preview()
        if self.send_allowed and self.send_button.isEnabled():
            self.send_requested.emit(self.filter_xml)

    def copy_xml(self):
        self.refresh_preview()
        if self.copy_button.isEnabled():
            QApplication.clipboard().setText(self.preview.toPlainText())
            self.preview_status.setText("RPC XML 已複製（message-id=preview；從範本頁送出時會產生新 ID）。")

    def _summary_text(self, names):
        stream = "fault-management" if self.fault_only else "NETCONF"
        if self.all_events.isChecked():
            return "訂閱範圍：NETCONF 全部通知（不加 filter）；目前儲存的個別條件暫不套用。"
        lines = ["訂閱範圍：%s · %d 種 Notification" % (stream, len(names))]
        for name in names:
            terms = []
            for path, values in self.selected.get(name, {}).items():
                if values:
                    terms.append("/".join(path) + " = " + " 或 ".join(values))
            terms.extend(group + "：完整群組" for group in self.groups.get(name, ()))
            lines.append(name + "：" + ("；".join(terms) or "所有此類通知，不限制欄位"))
        return "\n".join(lines)

    def _select_event(self, item, *_):
        if item is None:
            return
        self._save_fields()
        self.current_name = item.text(0)
        if self.fields is not None:
            self.field_layout.removeWidget(self.fields)
            self.fields.deleteLater()
        definition = self.catalog[self.current_name]
        self.fields = NotificationFields(definition, self.selected.get(self.current_name),
            self.groups.get(self.current_name, ()), self.observations.get(self.current_name))
        self.field_title.setText(self.current_name + " · " + definition["module"] + " @ " + definition["revision"])
        self.field_layout.addWidget(self.fields)
        self.fields.values_changed.connect(self._fields_changed)
        self._filter_fields()

    def _fields_changed(self):
        self._save_fields()
        self.event_items[self.current_name].setCheckState(0, Qt.CheckState.Checked)
        self.all_events.setChecked(False)
        self.refresh_preview()

    def _event_changed(self, item, column):
        if column == 0:
            self.all_events.blockSignals(True)
            self.all_events.setChecked(False)
            self.all_events.blockSignals(False)
            if item.checkState(0) == Qt.CheckState.Checked:
                self.event_tree.setCurrentItem(item)
            self.refresh_preview()

    def clear_conditions(self):
        self._remember_undo()
        if self.fields:
            self.fields.set_selections({})
            self.fields.blockSignals(True)
            for item in self.fields.group_items.values():
                item.setCheckState(0, Qt.CheckState.Unchecked)
            self.fields.blockSignals(False)
            self._save_fields()
        self.refresh_preview()

    def load_preset(self):
        self._remember_undo()
        self.selected, self.groups = {}, {}
        index = self.preset.currentIndex()
        if self.fault_only:
            names = ["alarm-notif"]
            if index:
                self.selected["alarm-notif"] = {("fault-severity",):
                    ["CRITICAL", "MAJOR", "MINOR"] if index == 1 else ["CRITICAL"] if index == 2 else ["CRITICAL", "MAJOR"]}
            if index == 3:
                self.selected["alarm-notif"][("is-cleared",)] = ["false"]
        else:
            names = []
            if index == 1:
                names = ["alarm-notif", "measurement-result-stats"]
                self.selected["alarm-notif"] = {("fault-severity",): ["CRITICAL", "MAJOR", "MINOR"]}
                self.selected["measurement-result-stats"] = {("rx-window-stats", "measurement-object"): ["RX_ON_TIME"]}
                self.groups["measurement-result-stats"] = ["transceiver-stats"]
            elif index in {2, 3}:
                names = ["download-event", "install-event", "activation-event"] if index == 2 else ["download-event"]
                if index == 3:
                    status = next(f for f in self.catalog["download-event"]["fields"] if tuple(f["path"]) == ("status",))
                    self.selected["download-event"] = {("status",): [v for v in status["values"] if v != "COMPLETED"]}
            elif index == 4:
                names = ["tx-array-carriers-state-change", "rx-array-carriers-state-change"]
            elif index == 5:
                names = ["synchronization-state-change", "ptp-state-change", "synce-state-change", "gnss-state-change"]
            elif index == 6:
                names = ["measurement-result-stats"]
                self.selected[names[0]] = {("epe-statistics", "measurement-object"): ["POWER"]}
        self.event_tree.blockSignals(True)
        for name, item in self.event_items.items():
            item.setCheckState(0, Qt.CheckState.Checked if name in names else Qt.CheckState.Unchecked)
        self.event_tree.blockSignals(False)
        # Discard previous editor before selecting, so old conditions cannot
        # overwrite the new preset during the currentItemChanged callback.
        if self.fields is not None:
            self.field_layout.removeWidget(self.fields)
            self.fields.deleteLater()
            self.fields = None
        item = self.event_items[names[0] if names else "download-event"]
        self.event_tree.blockSignals(True)
        self.event_tree.setCurrentItem(item)
        self.event_tree.blockSignals(False)
        self._select_event(item)
        self.all_events.setChecked(not self.fault_only and index == 0)
        self.refresh_preview()

    def refresh_preview(self, *_):
        self._save_fields()
        names = [name for name, item in self.event_items.items() if item.checkState(0) == Qt.CheckState.Checked]
        self.summary.setPlainText(self._summary_text(names))
        self._filter_events()
        self._filter_fields()
        try:
            if not names and not self.all_events.isChecked():
                raise ValueError("請勾選至少一種 Notification，或選擇接收 NETCONF 全部通知。")
            self.filter_xml = "" if self.all_events.isChecked() else "\n".join(
                templates.notification_filter(self.catalog[name], self.selected.get(name, {}), self.groups.get(name, ()))
                for name in names)
            stream = "fault-management" if self.fault_only else "NETCONF"
            plan = raw_rpc.subscription_rpc(events.subscription_options(stream, {}, self.filter_xml))
            plan.rpc.set("message-id", "preview")
            from lxml import etree
            self.preview.setPlainText(etree.tostring(plan.rpc, encoding="unicode", pretty_print=True).rstrip())
            self.preview.setPlainText(raw_rpc.pretty_xml(self.preview.toPlainText()))
            self.preview_status.setText("Create Subscription RPC 預覽（唯讀） · " + stream + " · 尚未送出")
            self.preview_status.setStyleSheet("color: #176b35;")
            self.apply_button.setEnabled(True)
            self.copy_button.setEnabled(True)
            self.send_button.setEnabled(self.send_allowed)
        except Exception as exc:
            self.filter_xml = ""
            self.preview.clear()
            self.preview_status.setText(str(exc))
            self.preview_status.setStyleSheet("color: #b42318; font-weight: 600;")
            self.apply_button.setEnabled(False)
            self.copy_button.setEnabled(False)
            self.send_button.setEnabled(False)

    def update_device_schema(self, modules, observations=None):
        self._save_fields()
        previous = self.catalog
        self.catalog = deepcopy(templates.bundled_catalog()["notifications"])
        for name, definition in previous.items():
            self.catalog.setdefault(name, definition)
        device = templates.notification_catalog(modules)
        # Keep offline definitions visible when device schema is missing.
        # Presence here means schema availability, not implemented capability.
        for name, definition in device.items():
            if self.fault_only and name != "alarm-notif":
                continue
            self.catalog[name] = definition
            if name not in self.event_items:
                self.event_tree.blockSignals(True)
                item = QTreeWidgetItem(self.event_tree, [name, "DUT YANG 有定義"])
                item.setCheckState(0, Qt.CheckState.Unchecked)
                self.event_items[name] = item
                self.event_tree.blockSignals(False)
        for name, item in self.event_items.items():
            item.setText(1, "DUT YANG 有定義" if name in device else "僅範本 / 未取得")
        self.observations = observations or {}
        item = self.event_items.get(self.current_name)
        if item:
            self._select_event(item)
        self.source.setText("已比對目前載入的 DUT YANG；有定義不等於已啟用，仍受 feature、權限與 stream 支援限制。")
        self.refresh_preview()
