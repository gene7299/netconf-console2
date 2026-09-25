"""PySide6 NETCONF/YANG workspace.

This is the Qt GUI layer for netconf-console2.  The protocol, schema and
sysrepo services remain in the existing tested modules; this module only
owns Qt widgets, model/view state and worker-thread dispatch.  The original
Tk GUI is intentionally kept available while the Qt surface is migrated in
small, verifiable slices.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from ..session import ConnectionSettings
from ..trace import redact_secrets
from ..xmloutput import serialize_xml
from . import VERSION
from .client import GuiClient, ReadOptions, Snapshot
from .creation import (Candidate, Template, append_template, candidates, choice_label,
                       missing_choice_candidates, new_root_selection, scalar_input,
                       suggested_values, validate_subtree)
from .leafrefs import resolve as resolve_leafref
from .model import (EditError, Selection, build_plan, children, identity, local,
                    node_style, parse_editor, xml_spans)
from .preferences import (CONNECTION_ACCOUNT_FIELD, PreferencesStore, change_profiles,
                          empty_book, remember_account, remember_connection)
from . import backups, events, lifecycle, reconcile, safety, system_backup, templates
from .audit import AuditLog
from .connection_diagnostics import DiagnosticRun, report_text
from .drafts import DraftShelf, schema_fingerprint
from .profile_exchange import merge_profiles, read_import
from .workspace import (import_selection, instance_path, search_snapshot, value_changes,
                        xml_diff)
from .windows import icon_path, set_app_id
from .i18n import (LANGUAGE_LABELS, LANGUAGES, language_label,
                   normalize_language, retranslate_widget_tree, resolve_language,
                   set_language, translate as tr)

from PySide6.QtCore import (QAbstractAnimation, QEvent, QObject, QPoint, QRect, QSize,
                            Qt, QThread, QTimer, Signal, Slot)
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QIcon, QKeySequence, QPalette,
                           QPainter, QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (
    QApplication, QCheckBox as _QtCheckBox, QComboBox, QDialog as _QtDialog,
    QDialogButtonBox as _QtDialogButtonBox, QFileDialog as _QtFileDialog, QFormLayout, QFrame,
    QGridLayout, QGroupBox as _QtGroupBox, QHBoxLayout, QLabel as _QtLabel,
    QLineEdit as _QtLineEdit, QListWidget, QListWidgetItem,
    QMainWindow as _QtMainWindow, QMessageBox as _QtMessageBox,
    QPlainTextEdit as _QtPlainTextEdit, QInputDialog as _QtInputDialog,
    QMenu as _QtMenu, QProgressBar, QPushButton as _QtPushButton, QScrollArea,
    QSizePolicy, QSplitter, QStatusBar, QTabWidget as _QtTabWidget,
    QTextEdit as _QtTextEdit, QTreeWidget as _QtTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)


class _TranslatedTextMixin:
    """Translate widget text while retaining its original source string."""

    def __init__(self, *args, **kwargs):
        source = args[0] if args and isinstance(args[0], str) else None
        if source is not None:
            args = args[1:]
        super().__init__(*args, **kwargs)
        self._ncc_source_text = source
        if source is not None:
            self.setText(source)

    def setText(self, text):  # noqa: N802 - Qt API name
        self._ncc_source_text = "" if text is None else str(text)
        super().setText(tr(self._ncc_source_text))

    def _ncc_retranslate(self):
        if hasattr(self, "_ncc_source_text"):
            super().setText(tr(self._ncc_source_text))


class QLabel(_TranslatedTextMixin, _QtLabel):
    pass


class QPushButton(_TranslatedTextMixin, _QtPushButton):
    pass


class QCheckBox(_TranslatedTextMixin, _QtCheckBox):
    pass


class QGroupBox(_QtGroupBox):
    """Translate a group-box title while retaining its source string."""

    def __init__(self, *args, **kwargs):
        source = args[0] if args and isinstance(args[0], str) else None
        if source is not None:
            args = args[1:]
        super().__init__(*args, **kwargs)
        self._ncc_source_title = source
        if source is not None:
            self.setTitle(source)

    def setTitle(self, title):  # noqa: N802 - Qt API name
        self._ncc_source_title = "" if title is None else str(title)
        super().setTitle(tr(self._ncc_source_title))

    def _ncc_retranslate(self):
        if hasattr(self, "_ncc_source_title"):
            super().setTitle(tr(self._ncc_source_title))


class _TranslatedWindowMixin:
    def setWindowTitle(self, title):  # noqa: N802 - Qt API name
        self._ncc_source_title = "" if title is None else str(title)
        super().setWindowTitle(tr(self._ncc_source_title))

    def _ncc_retranslate(self):
        if hasattr(self, "_ncc_source_title"):
            super().setWindowTitle(tr(self._ncc_source_title))


class QDialog(_TranslatedWindowMixin, _QtDialog):
    pass


class QMainWindow(_TranslatedWindowMixin, _QtMainWindow):
    pass


class QLineEdit(_QtLineEdit):
    def setPlaceholderText(self, text):  # noqa: N802 - Qt API name
        self._ncc_source_placeholder = "" if text is None else str(text)
        super().setPlaceholderText(tr(self._ncc_source_placeholder))

    def _ncc_retranslate(self):
        if hasattr(self, "_ncc_source_placeholder"):
            super().setPlaceholderText(tr(self._ncc_source_placeholder))


class QPlainTextEdit(_QtPlainTextEdit):
    def setPlaceholderText(self, text):  # noqa: N802 - Qt API name
        self._ncc_source_placeholder = "" if text is None else str(text)
        super().setPlaceholderText(tr(self._ncc_source_placeholder))

    def _ncc_retranslate(self):
        if hasattr(self, "_ncc_source_placeholder"):
            super().setPlaceholderText(tr(self._ncc_source_placeholder))


class QTextEdit(_QtTextEdit):
    def setPlaceholderText(self, text):  # noqa: N802 - Qt API name
        self._ncc_source_placeholder = "" if text is None else str(text)
        super().setPlaceholderText(tr(self._ncc_source_placeholder))

    def _ncc_retranslate(self):
        if hasattr(self, "_ncc_source_placeholder"):
            super().setPlaceholderText(tr(self._ncc_source_placeholder))


class QTreeWidget(_QtTreeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ncc_header_sources = []

    def setHeaderLabels(self, labels):  # noqa: N802 - Qt API name
        self._ncc_header_sources = [str(label) for label in labels]
        super().setHeaderLabels([tr(label) for label in self._ncc_header_sources])

    def _ncc_retranslate(self):
        if self._ncc_header_sources:
            super().setHeaderLabels([tr(label) for label in self._ncc_header_sources])
        def refresh(item):
            source = item.data(0, ITEM_SOURCE_ROLE)
            if source is not None:
                item.setText(0, tr(str(source)))
            for index in range(item.childCount()):
                refresh(item.child(index))
        for index in range(self.topLevelItemCount()):
            refresh(self.topLevelItem(index))


class QTabWidget(_QtTabWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ncc_tab_sources = {}

    def addTab(self, widget, label):  # noqa: N802 - Qt API name
        index = super().addTab(widget, tr(label))
        self._ncc_tab_sources[index] = str(label)
        return index

    def setTabText(self, index, text):  # noqa: N802 - Qt API name
        self._ncc_tab_sources[index] = "" if text is None else str(text)
        super().setTabText(index, tr(self._ncc_tab_sources[index]))

    def _ncc_retranslate(self):
        for index, source in self._ncc_tab_sources.items():
            if index < self.count():
                super().setTabText(index, tr(source))


class QMenu(_QtMenu):
    def addAction(self, *args):  # noqa: N802 - Qt API name
        if args and isinstance(args[0], str):
            source = args[0]
            action = super().addAction(tr(source), *args[1:])
            action.setProperty("_ncc_source_text", source)
            return action
        return super().addAction(*args)

    def addMenu(self, *args):  # noqa: N802 - Qt API name
        if args and isinstance(args[0], str):
            return _MenuProxy(super().addMenu(tr(args[0])), args[0])
        return super().addMenu(*args)


class _MenuProxy:
    """Keep source labels for menus returned by QMenuBar's C++ overload."""

    def __init__(self, menu, title=None):
        self.menu = menu
        self.title = title
        if title is not None:
            self.menu.setProperty("_ncc_source_title", title)

    def addAction(self, *args):
        if args and isinstance(args[0], str):
            source = args[0]
            action = self.menu.addAction(tr(source), *args[1:])
            action.setProperty("_ncc_source_text", source)
            return action
        return self.menu.addAction(*args)

    def addMenu(self, title):
        return _MenuProxy(self.menu.addMenu(tr(title)), title)

    def addSeparator(self):
        return self.menu.addSeparator()


class QMessageBox:
    StandardButton = _QtMessageBox.StandardButton

    @staticmethod
    def question(parent, title, text, *args, **kwargs):
        return _QtMessageBox.question(parent, tr(title), tr(text), *args, **kwargs)

    @staticmethod
    def information(parent, title, text, *args, **kwargs):
        return _QtMessageBox.information(parent, tr(title), tr(text), *args, **kwargs)

    @staticmethod
    def warning(parent, title, text, *args, **kwargs):
        return _QtMessageBox.warning(parent, tr(title), tr(text), *args, **kwargs)

    @staticmethod
    def critical(parent, title, text, *args, **kwargs):
        return _QtMessageBox.critical(parent, tr(title), tr(text), *args, **kwargs)


class QFileDialog:
    @staticmethod
    def getSaveFileName(parent, title, directory="", file_filter="", *args, **kwargs):
        return _QtFileDialog.getSaveFileName(parent, tr(title), directory, tr(file_filter), *args, **kwargs)

    @staticmethod
    def getOpenFileName(parent, title, directory="", file_filter="", *args, **kwargs):
        return _QtFileDialog.getOpenFileName(parent, tr(title), directory, tr(file_filter), *args, **kwargs)

    @staticmethod
    def getExistingDirectory(parent, title, directory="", *args, **kwargs):
        return _QtFileDialog.getExistingDirectory(parent, tr(title), directory, *args, **kwargs)


class QInputDialog:
    @staticmethod
    def getText(parent, title, label, *args, **kwargs):
        return _QtInputDialog.getText(parent, tr(title), tr(label), *args, **kwargs)

    @staticmethod
    def getInt(parent, title, label, *args, **kwargs):
        return _QtInputDialog.getInt(parent, tr(title), tr(label), *args, **kwargs)


class QDialogButtonBox(_QtDialogButtonBox):
    _STANDARD_LABELS = {
        _QtDialogButtonBox.StandardButton.Ok: "確定",
        _QtDialogButtonBox.StandardButton.Cancel: "取消",
        _QtDialogButtonBox.StandardButton.Close: "關閉",
        _QtDialogButtonBox.StandardButton.Save: "儲存",
        _QtDialogButtonBox.StandardButton.Yes: "是",
        _QtDialogButtonBox.StandardButton.No: "否",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ncc_retranslate()

    def _ncc_retranslate(self):
        for standard, source in self._STANDARD_LABELS.items():
            button = self.button(standard)
            if button is not None:
                custom = button.property("_ncc_source_text")
                button.setText(tr(str(custom) if custom is not None else source))


COLORS = {
    "default": "#fff0bf",
    "schema_default": "#fff8de",
    "state": "#e4edf5",
    "changed": "#ffdcc5",
    "unknown": "#eee9f2",
}
MODES = ("Direct SSH", "Direct TLS", "SSH Call Home", "TLS Call Home")
USER_ROLE = Qt.ItemDataRole.UserRole
ITEM_SOURCE_ROLE = Qt.ItemDataRole.UserRole + 1
INITIAL_WINDOW_SIZE = QSize(1280, 800)


class TaskRunner(QObject):
    """Run one backend operation on a QThread and report progress by signal."""

    succeeded = Signal(object)
    failed = Signal(object)
    progress = Signal(str)

    def __init__(self, function):
        super().__init__()
        self.function = function

    @Slot()
    def run(self):
        try:
            self.succeeded.emit(self.function(self.progress.emit))
        except BaseException as exc:  # always hand the exception to the UI thread
            self.failed.emit(exc)


class LineNumberArea(QWidget):
    """Small native gutter matching the Tk XML panes."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):  # noqa: N802 - Qt API name
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):  # noqa: N802 - Qt API name
        self.editor.line_number_area_paint_event(event)


class CodeEditor(QPlainTextEdit):
    """QPlainTextEdit with a compact Consolas line-number gutter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_number_width(0)
        self._highlight_current_line()

    def line_number_area_width(self):
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 8 + self.fontMetrics().horizontalAdvance("9") * digits

    @Slot(int)
    def _update_line_number_width(self, _count):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    @Slot(QRect, int)
    def _update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_width(0)

    def resizeEvent(self, event):  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.line_number_area.setGeometry(QRect(rect.left(), rect.top(),
                                                self.line_number_area_width(), rect.height()))

    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#f0f3f7"))
        painter.setPen(QColor("#8391a1"))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self.line_number_area.width() - 4,
                                 self.fontMetrics().height(), Qt.AlignmentFlag.AlignRight,
                                 str(block_number + 1))
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def _highlight_current_line(self):
        if self.isReadOnly():
            self.setExtraSelections([s for s in self.extraSelections()
                                     if s.format.property(QTextCharFormat.Property.FullWidthSelection) is not True])
            return
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#f7fbff"))
        selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection] + [s for s in self.extraSelections()
                                               if s.format.property(QTextCharFormat.Property.FullWidthSelection) is not True])


class XmlHighlighter:
    """Small, dependency-free XML colourizer for QPlainTextEdit."""

    def __init__(self, editor: QPlainTextEdit):
        self.editor = editor
        self._formats = self._make_formats()
        self._apply()

    @staticmethod
    def _make_formats():
        tag = QTextCharFormat()
        tag.setForeground(QColor("#17689b"))
        attribute = QTextCharFormat()
        attribute.setForeground(QColor("#7c3aed"))
        value = QTextCharFormat()
        value.setForeground(QColor("#9a3412"))
        comment = QTextCharFormat()
        comment.setForeground(QColor("#798796"))
        return tag, attribute, value, comment

    def _apply(self):
        document = self.editor.document()
        self.editor.setExtraSelections([])
        # QSyntaxHighlighter is normally the cleanest option, but keeping this
        # tiny implementation local avoids a second editor document and lets
        # the annotation overlay share the same extra-selection list.
        cursor = QTextCursor(document)
        text = self.editor.toPlainText()
        import re
        token = re.compile(r"<!--[\s\S]*?-->|<[^>]+>|\"[^\"]*\"|'[^']*'")
        selections = []
        for match in token.finditer(text):
            cursor.setPosition(match.start())
            cursor.setPosition(match.end(), QTextCursor.MoveMode.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(cursor)
            if match.group().startswith("<!--"):
                selection.format = self._formats[3]
            elif match.group().startswith("<"):
                selection.format = self._formats[0]
            else:
                selection.format = self._formats[2]
            selections.append(selection)
        self.editor.setExtraSelections(selections)


class CreationDialog(QDialog):
    """Complete schema-driven node builder; it only creates a local XML draft.

    YANG ``choice``/``case`` are schema metadata, not XML elements.  Missing
    mandatory choices therefore expose their concrete branch nodes (for
    example ``transport → ssh``) as actions in this dialog.
    """

    def __init__(self, schema, parent_node, parent_path, owner=None, *, initial_node=None,
                 initial_path=None):
        super().__init__(owner)
        self.schema = schema
        self.parent_node = deepcopy(parent_node)
        self.parent_path = parent_path
        self.owner_window = owner
        self.initial_node = deepcopy(initial_node) if initial_node is not None else None
        self.initial_path = initial_path
        self.selected_candidate: Candidate | None = None
        self.template: Template | None = None
        self.result_node = None
        self._catalog = []
        self._nodes = {}
        self._child_options = []
        self._choice_options = []
        self._suggestion_values = ()
        self._xml_dirty = False
        self.setWindowTitle("新增 YANG 節點 — 本機 XML 草稿")
        self.resize(1160, 820)
        self.setMinimumSize(900, 680)

        root_layout = QVBoxLayout(self)
        title = QLabel("選擇節點 → 填值／新增分支 → 加入草稿 → 檢查 RPC 後再送出")
        title.setStyleSheet("font-size:14px; font-weight:700")
        root_layout.addWidget(title)
        intro = QLabel(
            "未讀到不代表不存在（可能是 NACM、default 或條件造成）。新增使用 create；"
            "when、must、leafref、unique 與權限仍由伺服器驗證。本視窗不會自動送出。"
        )
        intro.setWordWrap(True)
        root_layout.addWidget(intro)

        split = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(split, 3)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        candidate_header = QHBoxLayout()
        candidate_header.addWidget(QLabel("候選節點／搜尋"))
        self.show_unavailable = QCheckBox("顯示不可新增節點")
        self.show_unavailable.setToolTip("包含已存在、config false、數量上限及互斥 choice 分支等項目")
        self.show_unavailable.setChecked(False)
        self.show_unavailable.stateChanged.connect(self._fill_picker)
        candidate_header.addWidget(self.show_unavailable)
        candidate_header.addStretch(1)
        left_layout.addLayout(candidate_header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜尋 module、節點、型別或說明")
        left_layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        left_layout.addWidget(self.list, 1)
        split.addWidget(left)

        right = QWidget()
        form = QVBoxLayout(right)
        form.setContentsMargins(4, 0, 0, 0)
        self.template_tree = QTreeWidget()
        self.template_tree.setHeaderLabels(["範本階層（選取欄位填值）", "值／狀態"])
        self.template_tree.setColumnWidth(0, 330)
        self.template_tree.setIndentation(18)
        form.addWidget(self.template_tree, 1)
        self.info_label = QLabel("請選取候選節點")
        self.info_label.setWordWrap(True)
        self.info_label.setMinimumHeight(42)
        form.addWidget(self.info_label)

        value_row = QHBoxLayout()
        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText("選取 leaf 後填入值")
        self.value_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        value_row.addWidget(self.value_edit, 1)
        self.apply_value_button = QPushButton("套用欄位值")
        self.apply_value_button.clicked.connect(self._apply_value)
        value_row.addWidget(self.apply_value_button)
        form.addLayout(value_row)

        suggestion_row = QHBoxLayout()
        suggestion_row.addWidget(QLabel("建議值（仍可自行輸入）"))
        self.suggestions = QComboBox()
        suggestion_row.addWidget(self.suggestions, 1)
        self.apply_suggestion = QPushButton("帶入建議值")
        self.apply_suggestion.clicked.connect(self._apply_suggestion)
        suggestion_row.addWidget(self.apply_suggestion)
        form.addLayout(suggestion_row)

        choice_row = QHBoxLayout()
        choice_row.addWidget(QLabel("缺少必填 choice 分支"))
        self.choice_box = QComboBox()
        choice_row.addWidget(self.choice_box, 1)
        self.choice_button = QPushButton("加入 choice 分支")
        self.choice_button.clicked.connect(self._add_choice)
        choice_row.addWidget(self.choice_button)
        form.addLayout(choice_row)

        child_row = QHBoxLayout()
        self.child_box = QComboBox()
        child_row.addWidget(self.child_box, 1)
        self.child_button = QPushButton("新增子節點／項目")
        self.child_button.clicked.connect(self._add_child)
        child_row.addWidget(self.child_button)
        self.remove_button = QPushButton("移除範本節點")
        self.remove_button.setObjectName("dangerButton")
        self.remove_button.clicked.connect(self._remove_node)
        child_row.addWidget(self.remove_button)
        form.addLayout(child_row)
        self.reference_label = QLabel("")
        self.reference_label.setWordWrap(True)
        self.reference_label.setStyleSheet("color:#64748b")
        form.addWidget(self.reference_label)
        split.addWidget(right)
        split.setSizes([360, 780])

        self.status_label = QLabel("請選擇要新增的節點。")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color:#a64b00")
        root_layout.addWidget(self.status_label)
        self.editor = CodeEditor()
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setFont(QFont("Consolas", 10))
        self.editor.setMinimumHeight(190)
        self.editor.setPlaceholderText("這裡是可自由編輯的本機 XML 草稿")
        root_layout.addWidget(self.editor, 2)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setProperty("_ncc_source_text", "加入 XML 草稿")
        ok_button.setText(tr("加入 XML 草稿"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root_layout.addWidget(self.buttons)

        self.search.textChanged.connect(self._fill_picker)
        self.list.currentItemChanged.connect(self._candidate_changed)
        self.template_tree.currentItemChanged.connect(self._field_changed)
        self.value_edit.returnPressed.connect(self._apply_value)
        self.editor.textChanged.connect(self._editor_changed)
        self._load_candidates()

    @staticmethod
    def _schema_text(info):
        metadata = " · ".join(value for value in (info.module, info.kind, info.type_name) if value)
        details = [metadata]
        if info.presence:
            details.append("presence：" + " ".join(info.presence.split()))
        if info.constraints:
            details.append("constraints：" + " ".join(info.constraints.split()))
        elif info.description:
            details.append("description：" + " ".join(info.description.split()))
        return "\n".join(details)[:900]

    def _load_candidates(self):
        try:
            self._catalog = candidates(self.schema, self.parent_node, self.parent_path)
        except Exception as exc:
            self.info_label.setText("無法建立候選清單：" + redact_secrets(str(exc)))
            return
        self._fill_picker()
        preferred = None
        for row in range(self.list.count()):
            candidate = self.list.item(row).data(USER_ROLE)
            if candidate.allowed and (self.initial_path is None or candidate.info.path == self.initial_path):
                preferred = row
                break
        if preferred is None:
            preferred = next((row for row in range(self.list.count())
                              if self.list.item(row).data(USER_ROLE).allowed), None)
        if preferred is not None:
            self.list.setCurrentRow(preferred)
        elif not self._catalog:
            self.info_label.setText("此層沒有可由目前 schema 建立的子節點。")

    def _fill_picker(self, *_args):
        current = self.selected_candidate
        query = self.search.text().casefold().strip()
        self.list.blockSignals(True)
        self.list.clear()
        chosen = None
        for candidate in self._catalog:
            info = candidate.info
            if candidate.reason and not self.show_unavailable.isChecked():
                continue
            label = "%s:%s" % (info.module, local(info.path[-1]))
            branch = choice_label(self.schema, info)
            if branch:
                label += " [choice %s]" % branch
            label += "  (%s)" % info.kind
            if candidate.reason:
                label += "  — " + candidate.reason
            haystack = " ".join((label, info.description or "", info.type_name or "")).casefold()
            if query and query not in haystack:
                continue
            item = QListWidgetItem(tr(label))
            item.setData(USER_ROLE, candidate)
            if not candidate.allowed:
                item.setForeground(QBrush(QColor("#8793a4")))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(item)
            if candidate is current:
                chosen = item
        self.list.blockSignals(False)
        if chosen is not None:
            self.list.setCurrentItem(chosen)
        elif self.list.count() == 0:
            self.info_label.setText(
                "沒有符合條件的可新增節點；需要檢查原因時可勾選「顯示不可新增節點」。"
            )

    def _candidate_changed(self, current, _previous=None):
        if current is None:
            return
        candidate = current.data(USER_ROLE)
        self.selected_candidate = candidate
        if not candidate.allowed:
            self.info_label.setText(candidate.reason)
            return
        try:
            self.template = Template(self.schema, candidate.info)
            if self.initial_node is not None and candidate.info.path == self.initial_path:
                self.template.root = deepcopy(self.initial_node)
                self._recompute_pending()
            self._render_template(self.template.root)
        except Exception as exc:
            self.template = None
            self.info_label.setText(redact_secrets(str(exc)))
            self.editor.clear()

    def _recompute_pending(self):
        if self.template is None:
            return
        pending = set()
        for node in self.template.root.iter():
            info = self.schema.lookup(self.template.node_path(node))
            if info is None or info.kind not in {"leaf", "leaf-list"} or node.text is not None:
                continue
            statement = self.schema.statements.get(info.path)
            type_statement = statement.search_one("type") if statement is not None else None
            type_name = getattr(getattr(type_statement, "i_type_spec", None), "name", info.type_name)
            if type_name != "empty" and not info.defaults:
                pending.add(node)
        self.template.pending = pending

    def _selected_node(self):
        item = self.template_tree.currentItem()
        return item.data(0, USER_ROLE) if item is not None else None

    def _selected_indices(self):
        node = self._selected_node()
        if node is None or self.template is None:
            return ()
        result = []
        while node is not self.template.root:
            parent = node.getparent()
            if parent is None:
                return ()
            result.append(children(parent).index(node))
            node = parent
        return tuple(reversed(result))

    @staticmethod
    def _node_at(root, indices):
        node = root
        for index in indices:
            items = children(node)
            if index >= len(items):
                return root
            node = items[index]
        return node

    def _sync_from_editor(self):
        if self.template is None:
            raise EditError("請先選擇要新增的節點。")
        indices = self._selected_indices()
        if self._xml_dirty:
            node = parse_editor(self.editor.toPlainText())
            if node.tag != self.template.path[-1]:
                raise EditError("範本根節點必須維持 %s。" % local(self.template.path[-1]))
            self.template.root = node
            self._recompute_pending()
            self._xml_dirty = False
        return self._node_at(self.template.root, indices)

    def _render_template(self, selected=None, *, update_editor=True):
        self.template_tree.blockSignals(True)
        self.template_tree.clear()
        self._nodes.clear()
        selected_item = None

        def walk(node, parent=None):
            nonlocal selected_item
            pending = node in self.template.pending
            value = "待填" if pending else (node.text or "")
            item = QTreeWidgetItem(parent if parent is not None else self.template_tree,
                                   [local(node.tag), value])
            item.setData(0, USER_ROLE, node)
            if pending:
                item.setForeground(1, QBrush(QColor("#b45309")))
            item.setExpanded(True)
            self._nodes[id(node)] = item
            if node is selected:
                selected_item = item
            for child in children(node):
                walk(child, item)

        walk(self.template.root)
        self.template_tree.blockSignals(False)
        if update_editor:
            self.editor.blockSignals(True)
            self.editor.setPlainText(serialize_xml(self.template.root, compact_namespaces=True).decode("utf-8"))
            self.editor.blockSignals(False)
            self._xml_dirty = False
        self.template_tree.setCurrentItem(selected_item or self.template_tree.topLevelItem(0))
        self._refresh_choices()
        self._refresh_status()

    def _editor_changed(self):
        self._xml_dirty = True
        self.status_label.setText("XML 草稿已修改；切換欄位或加入草稿時會重新解析並檢查。")

    def _field_changed(self, current, _previous=None):
        if current is None or self.template is None:
            return
        node = current.data(0, USER_ROLE)
        try:
            info = self.schema.lookup(self.template.node_path(node))
            scalar = info.kind in {"leaf", "leaf-list"}
            self.info_label.setText(self._schema_text(info))
            self.value_edit.setEnabled(scalar)
            self.apply_value_button.setEnabled(scalar)
            self.value_edit.setText(node.text or "")
            self.value_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._suggestion_values = ()
            self.suggestions.clear()
            self.reference_label.clear()
            reference_values = ()
            if scalar:
                reference_values = self._reference_values(node)
                self._suggestion_values = suggested_values(self.schema, info, reference_values)
            self.suggestions.addItems(list(self._suggestion_values))
            self.apply_suggestion.setEnabled(bool(self._suggestion_values))
            self._child_options = ([candidate.info for candidate in candidates(self.schema, node, info.path)
                                    if candidate.allowed] if not scalar else [])
            self.child_box.clear()
            self.child_box.addItems(["%s:%s (%s)" % (child.module, local(child.path[-1]), child.kind)
                                     for child in self._child_options])
            self.child_button.setEnabled(bool(self._child_options))
            self.remove_button.setEnabled(node is not self.template.root)
        except Exception as exc:
            self.status_label.setText(redact_secrets(str(exc)))

    def _reference_values(self, node):
        owner = self.owner_window
        snapshot = getattr(owner, "snapshot", None)
        selection = getattr(owner, "selection", None)
        if snapshot is None or selection is None:
            return ()
        indices = []
        child = node
        while child is not self.template.root:
            parent = child.getparent()
            indices.append(children(parent).index(child))
            child = parent
        try:
            if self.parent_path:
                context = deepcopy(self.parent_node)
                offset = len(children(context))
                context.append(deepcopy(self.template.root))
                lookup_indices = (offset, *reversed(indices))
            else:
                context = self.template.root
                selection = new_root_selection(context, self.schema)
                lookup_indices = tuple(reversed(indices))
            references = resolve_leafref(self.schema, snapshot.data, selection, context, lookup_indices)
            self.reference_label.setText("\n".join(dict.fromkeys(references.messages)))
            return references.values
        except (EditError, AttributeError, IndexError, TypeError):
            self.reference_label.setText("草稿上下文尚未完整，引用候選待伺服器驗證。")
            return ()

    def _apply_value(self):
        if self.template is None:
            return
        try:
            node = self._sync_from_editor()
            info = self.schema.lookup(self.template.node_path(node))
            if info.kind not in {"leaf", "leaf-list"}:
                raise EditError("請先選取 leaf 或 leaf-list。")
            self.template.set_value(node, self.value_edit.text())
            self._render_template(node)
        except Exception as exc:
            self.status_label.setText(redact_secrets(str(exc)))

    def _apply_suggestion(self):
        index = self.suggestions.currentIndex()
        if index < 0 or index >= len(self._suggestion_values):
            return
        self.value_edit.setText(self._suggestion_values[index])
        self.value_edit.setFocus()
        self.status_label.setText("已帶入建議值；確認後按「套用欄位值」。")

    def _refresh_choices(self):
        self._choice_options = (missing_choice_candidates(
            self.schema, self.template.root, self.template.path) if self.template else [])
        self.choice_box.clear()
        self.choice_box.addItems([option.label for option in self._choice_options])
        if not self._choice_options:
            self.choice_box.addItem("目前沒有待選的必填 choice")
        self.choice_box.setEnabled(bool(self._choice_options))
        self.choice_button.setEnabled(bool(self._choice_options))

    def _add_choice(self):
        wanted = self.choice_box.currentText()
        try:
            self._sync_from_editor()
            self._refresh_choices()
            option = next((item for item in self._choice_options if item.label == wanted), None)
            if option is None:
                raise EditError("請選擇待加入的必填 choice 分支。")
            new = self.template.add(option.parent, option.info)
            self._render_template(new)
            self.status_label.setText("已加入 choice %s → %s；請繼續填寫必要欄位。" %
                                      (option.choice.name, option.branch))
        except Exception as exc:
            self.status_label.setText(redact_secrets(str(exc)))

    def _add_child(self):
        index = self.child_box.currentIndex()
        try:
            node = self._sync_from_editor()
            info = self.schema.lookup(self.template.node_path(node))
            options = [candidate.info for candidate in candidates(self.schema, node, info.path)
                       if candidate.allowed]
            if index < 0 or index >= len(options):
                raise EditError("請先選擇可新增的子節點或項目。")
            new = self.template.add(node, options[index])
            self._render_template(new)
        except Exception as exc:
            self.status_label.setText(redact_secrets(str(exc)))

    def _remove_node(self):
        try:
            node = self._sync_from_editor()
            if node is self.template.root:
                raise EditError("不能移除範本根節點。")
            parent = node.getparent()
            parent.remove(node)
            self._recompute_pending()
            self._render_template(parent)
        except Exception as exc:
            self.status_label.setText(redact_secrets(str(exc)))

    def _refresh_status(self):
        issues = self.template.issues() if self.template else ["請選擇節點"]
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not issues)
        message = ("；".join(issues[:3]) if issues else
                   "本機檢查通過；完整 YANG 條件與權限仍需伺服器驗證。尚未送出。")
        if any("choice" in issue for issue in issues):
            message += " 請從「缺少必填 choice 分支」選擇實際分支並加入。"
        self.status_label.setText(message)

    def accept(self):
        candidate = self.selected_candidate
        if candidate is None or not candidate.allowed or self.template is None:
            QMessageBox.information(self, "新增節點", "請選擇可建立的節點。")
            return
        try:
            self._sync_from_editor()
            issues = self.template.issues()
            if issues:
                raise EditError("\n".join(issues[:8]))
            append_template(self.schema, self.parent_node, self.parent_path, self.template)
        except Exception as exc:
            QMessageBox.warning(self, "範本檢查", redact_secrets(str(exc)))
            return
        self.result_node = deepcopy(self.template.root)
        super().accept()


class ProfileDialog(QDialog):
    """Small profile catalog editor used by both connection/account lists."""

    def __init__(self, window, group):
        super().__init__(window)
        self.window_ref = window
        self.group = group
        self.setWindowTitle("管理連線設定組" if group == "connections" else "管理 SSH 帳號組")
        self.resize(700, 430)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        bar = QHBoxLayout()
        load = QPushButton("載入並編輯")
        load.clicked.connect(self.load_selected)
        rename = QPushButton("重新命名")
        rename.clicked.connect(self.rename_selected)
        delete = QPushButton("刪除選取項目")
        delete.setObjectName("dangerButton")
        delete.clicked.connect(self.delete_selected)
        close = QPushButton("關閉")
        close.clicked.connect(self.accept)
        for button in (load, rename, delete):
            bar.addWidget(button)
        bar.addStretch(1)
        bar.addWidget(close)
        layout.addLayout(bar)
        self.list.currentRowChanged.connect(self._describe)
        self._refresh()

    def _refresh(self, selected=""):
        self.list.clear()
        for name in self.window_ref.preferences[self.group]:
            item = QListWidgetItem(name)
            self.list.addItem(item)
            if name == selected:
                self.list.setCurrentItem(item)
        self._describe()

    def _selected_name(self):
        item = self.list.currentItem()
        return item.text() if item else ""

    def _describe(self):
        name = self._selected_name()
        if not name:
            self.detail.setText("共 %d 組；請選取一組。" % len(self.window_ref.preferences[self.group]))
            return
        record = self.window_ref.preferences[self.group][name]
        if self.group == "accounts":
            text = "帳號：%s；Private key：%s；已存密碼：%s" % (
                record.get("username", ""), "有" if record.get("ssh_key") else "無",
                "有" if record.get("password") else "無")
        else:
            call_home = "Call Home" in record.get("mode", "")
            text = "%s | %s:%s | 帳號：%s" % (
                record.get("mode", ""), record.get("listen_host" if call_home else "host", ""),
                record.get("listen_port" if call_home else "port", ""), record.get("username", ""))
        self.detail.setText("共 %d 組\n%s" % (len(self.window_ref.preferences[self.group]), text))

    def load_selected(self):
        name = self._selected_name()
        if not name:
            return
        if self.group == "connections":
            self.window_ref._apply_values(self.window_ref.preferences[self.group][name])
            self.window_ref.profile_box.setCurrentText(name)
        else:
            self.window_ref._apply_account(self.window_ref.preferences[self.group][name])
            self.window_ref.account_box.setCurrentText(name)
        self.accept()

    def rename_selected(self):
        name = self._selected_name()
        if not name:
            return
        new_name, ok = QInputDialog.getText(self, "重新命名", "新名稱：", text=name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            return
        if self.window_ref.rename_profile(self.group, name, new_name):
            self._refresh(new_name)

    def delete_selected(self):
        name = self._selected_name()
        if not name:
            return
        answer = QMessageBox.question(
            self, "確認刪除", "刪除本機清單項目「%s」？\n\n不會刪除 RU 帳號、憑證或金鑰檔。" % name,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes and self.window_ref.delete_profile(self.group, name):
            self._refresh()


class QtMainWindow(QMainWindow):
    """Responsive PySide6 XML workspace."""

    def __init__(self, client=None, *, persist=True, language=None):
        if language is None and persist:
            try:
                language = PreferencesStore().load().get("last", {}).get("language")
            except Exception:
                language = None
        self.language = resolve_language(language)
        set_language(self.language)
        super().__init__()
        self.client = client or GuiClient()
        self.persist = persist
        self.preferences_store = PreferencesStore() if persist else None
        self.demo = False
        self.closed = False
        self.busy = False
        self.job_name = ""
        self.snapshot: Snapshot | None = None
        self.selection: Selection | None = None
        self.baseline_text = ""
        self.plan = None
        self.uncertain = False
        self._task_thread = None
        self._task_runner = None
        self._task_done_callback = None
        self._task_failed_callback = None
        self._prefs_loading = False
        self.preferences_error = ""
        self.preferences = empty_book()
        self.language_actions = {}
        self.connection_save_as = False
        self.connection_hidden = False
        self.lifecycle_dialog = None
        self.admin_requires_refresh = False
        self.last_sent_xml = ""
        self.notification_manager = None
        self.notifications = deque(maxlen=100)
        self.streams = {}
        self.event_filter_xml = ""
        self.event_start = ""
        self.event_stop = ""
        self.event_records = deque(maxlen=100)
        self.alarm_rows = {}
        self.last_attempt = None
        self.result_rows = []
        self.result_pending = True
        self.diagnostic_dialog = None
        self.draft_fingerprint_cache = None
        self.draft_status = ""
        self.draft_timer = QTimer(self)
        self.draft_timer.setSingleShot(True)
        self.draft_timer.setInterval(700)
        self.draft_timer.timeout.connect(self._save_drafts)
        self.notification_timer = QTimer(self)
        self.notification_timer.setInterval(250)
        self.notification_timer.timeout.connect(self._poll_notifications)
        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.setInterval(250)
        self.reconnect_timer.timeout.connect(self._monitor_connection)
        self.reconnect_settings = None
        self.reconnect_schema_dir = ""
        self.reconnect_enabled = False
        self.reconnect_due = None
        self.reconnect_delay = 2
        self.drafts = DraftShelf(self.preferences_store.path.parent / "gui-drafts.nccdrafts"
                                 if self.preferences_store else None)
        self.drafts.load()
        self.audit = AuditLog(
            persist=self.preferences_store is not None,
            path=self.preferences_store.path.parent / "gui-operations.json"
            if self.preferences_store else None,
        )
        self.admin_connection = None
        self.admin_settings = None
        self.editor_timer = QTimer(self)
        self.editor_timer.setSingleShot(True)
        self.editor_timer.setInterval(220)
        self.editor_timer.timeout.connect(self.update_preview)
        self.preference_timer = QTimer(self)
        self.preference_timer.setSingleShot(True)
        self.preference_timer.setInterval(800)
        self.preference_timer.timeout.connect(self.save_preferences)

        self.setWindowTitle("NETCONF Console GUI %s · PySide6" % VERSION)
        self.setMinimumSize(1180, 760)
        self.resize(INITIAL_WINDOW_SIZE)
        icon = icon_path()
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self._build_ui()
        self._apply_values(self._default_values())
        if persist:
            self._restore_preferences()
        self._mode_changed()
        self._sync_controls()
        self.center_on_screen()
        self.reconnect_timer.start()

    def center_on_screen(self):
        """Open at a compact working size in the centre of the active display."""
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = min(INITIAL_WINDOW_SIZE.width(), available.width())
        height = min(INITIAL_WINDOW_SIZE.height(), available.height())
        self.resize(max(self.minimumWidth(), width), max(self.minimumHeight(), height))
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    # ---------- UI construction ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 4, 10, 3)
        outer.setSpacing(3)

        self.connection_area = QWidget()
        connection_layout = QVBoxLayout(self.connection_area)
        connection_layout.setContentsMargins(0, 0, 0, 0)
        connection_layout.setSpacing(2)
        self.fields = {}
        self.checks = {}

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # The settings pages deliberately use compact rows so the XML workspace
        # gets most of the window height, including at high-DPI scaling.
        self.tabs.setMinimumHeight(130)
        self.tabs.setMaximumHeight(175)
        self.tabs.setStyleSheet(
            "QTabBar::tab { background:#dbe4ef; color:#24364b; border:1px solid #94a3b8; "
            "padding:6px 12px; min-height:20px; font-weight:600; }"
            "QTabBar::tab:selected { background:#0969da; color:white; }"
            "QTabBar::tab:hover:!selected { background:#bfdbfe; color:#12345b; }"
            "QTabWidget::pane { border:1px solid #cbd5e1; background:#f3f6fa; }"
        )
        connection_layout.addWidget(self.tabs)
        self._build_connection_tab()
        self._build_ssh_tab()
        self._build_tls_tab()
        self._build_advanced_tab()
        self._build_ssh_method_tab()
        self._build_jump_tab()
        self._build_admin_tab()
        self._build_backup_tab()

        outer.addWidget(self.connection_area, 0)
        divider = QHBoxLayout()
        self.connection_divider_layout = divider
        divider.setContentsMargins(3, 0, 3, 0)
        divider.setSpacing(5)
        self.toggle_connection_button = QPushButton("▼ 隱藏連線設定")
        self.toggle_connection_button.setObjectName("toggleConnectionButton")
        self.toggle_connection_button.clicked.connect(self.toggle_connection)
        divider.addStretch(1)
        divider.addWidget(self.toggle_connection_button)
        divider.addStretch(1)
        outer.addLayout(divider)

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setChildrenCollapsible(False)
        workspace.setHandleWidth(5)
        outer.addWidget(workspace, 1)
        workspace.addWidget(self._build_tree_panel())
        workspace.addWidget(self._build_xml_panel())
        workspace.setStretchFactor(0, 4)
        workspace.setStretchFactor(1, 6)
        workspace.setSizes([620, 930])

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setContentsMargins(0, 0, 0, 0)
        self.status_bar.addWidget(self.schema_status)
        self.details_button = QPushButton("連線 / Schema 詳情")
        self.details_button.setObjectName("summaryDetailsButton")
        self.details_button.clicked.connect(self.show_details)
        self.status_bar.addWidget(self.details_button)
        self.status_label = QLabel("尚未連線 · 選擇連線方式並填入認證資料")
        self.status_bar.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFixedWidth(170)
        self.progress.setTextVisible(False)
        self.status_bar.addPermanentWidget(self.progress)

        settings_menu = _MenuProxy(self.menuBar().addMenu(tr("設定操作")), "設定操作")
        language_menu = settings_menu.addMenu("語言 / Language")
        for code in LANGUAGES:
            action = language_menu.addAction(LANGUAGE_LABELS[code])
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, selected=code: self.set_gui_language(selected)
            )
            self.language_actions[code] = action
        self._sync_language_actions()
        settings_menu.addSeparator()
        for operation, (label, *_rest) in lifecycle.OPERATIONS.items():
            action = settings_menu.addAction(label + "…")
            action.triggered.connect(lambda _checked=False, op=operation: self.datastore_action(op))
        settings_menu.addSeparator()
        settings_menu.addAction("限時提交：確認保留…", lambda: self.finish_confirmed(True))
        settings_menu.addAction("限時提交：取消回復…", lambda: self.finish_confirmed(False))
        settings_menu.addSeparator()
        draft_action = settings_menu.addAction("驗證 XML 草稿（test-only）…")
        draft_action.triggered.connect(self.validate_draft)
        settings_menu.addAction("查看功能停用原因…", self.show_availability)

        tools_menu = _MenuProxy(self.menuBar().addMenu(tr("工具")), "工具")
        export_action = tools_menu.addAction("匯出目前 XML…")
        export_action.triggered.connect(self.export_editor)
        export_tree = tools_menu.addAction("匯出 DATA TREE…")
        export_tree.triggered.connect(self.export_tree)
        add_action = tools_menu.addAction("新增 YANG 節點…")
        add_action.triggered.connect(self.new_node)
        tools_menu.addAction("新增根 YANG 節點…", self.new_root_node)
        delete_action = tools_menu.addAction("刪除整個選取節點…")
        delete_action.triggered.connect(self.delete_selected)
        tools_menu.addAction("匯入 XML 到選取節點…", self.import_xml)
        tools_menu.addAction("搜尋 DATA TREE / 路徑…", self.search_tree)
        tools_menu.addAction("搜尋編輯區 XML…", self.search_xml)
        tools_menu.addAction("所選節點 YANG 說明…", self.show_node_info)
        tools_menu.addSeparator()
        tools_menu.addAction("草稿清單／加密保存與恢復…", self.show_drafts)
        tools_menu.addAction("表單編輯 leaf／leafref 關聯…", self.open_leaf_editor)
        tools_menu.addAction("複製所選 list 項目…", self.clone_list)
        tools_menu.addAction("保存至個人範本庫…", self.save_personal_template)
        tools_menu.addAction("開啟個人範本庫…", self.load_personal_template)
        tools_menu.addAction("匯入連線／SSH 帳號設定…", self.import_profiles)
        tools_menu.addAction("原始值／設備最新值／草稿：三方比對…", self.open_reconcile)
        tools_menu.addAction("一鍵連線診斷／遮蔽問題報告…", self.open_connection_diagnostics)
        tools_menu.addSeparator()
        tools_menu.addAction("建立加密設定備份…", self.create_backup)
        tools_menu.addAction("開啟備份／選擇性還原…", self.restore_backup)
        tools_menu.addAction("事件 streams／篩選／回放…", self.event_settings)
        tools_menu.addAction("告警表格…", self.show_alarms)
        tools_menu.addAction("系統 SSH／sysrepocfg 修改草稿…", self.sysrepo_modify)
        tools_menu.addSeparator()
        details_action = tools_menu.addAction("連線 / Schema 詳情")
        details_action.triggered.connect(self.show_details)
        close_action = tools_menu.addAction("關閉")
        close_action.triggered.connect(self.close)

    def _sync_language_actions(self):
        for code, action in self.language_actions.items():
            action.setChecked(code == self.language)

    def set_gui_language(self, language):
        """Apply a GUI language immediately and persist the explicit choice."""
        selected = resolve_language(normalize_language(language))
        if selected == self.language:
            self._sync_language_actions()
            return
        self.language = selected
        set_language(selected)
        retranslate_widget_tree(self)
        self._sync_language_actions()
        self.save_preferences()
        self.status_label.setText(
            tr("語言已切換為 %s；重新開啟後也會保留此選擇。") % language_label(selected)
        )

    def _line(self, layout, name, row, column, span=1, *, password=False):
        edit = QLineEdit()
        edit.setObjectName(name)
        edit.setMinimumWidth(90 if span == 1 else (230 if password else 160))
        # The operator explicitly requested visible credentials in this local
        # desktop tool.  Persistence remains protected by the existing DPAPI
        # store and diagnostic/audit output still redacts secrets.
        edit.setEchoMode(QLineEdit.EchoMode.Normal)
        layout.addWidget(edit, row, column, 1, span)
        self.fields[name] = edit
        edit.textChanged.connect(self._schedule_preferences)
        return edit

    def _build_connection_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(3)
        profile_row = QHBoxLayout()
        self.connection_profile_layout = profile_row
        profile_row.setSpacing(5)
        profile_row.addWidget(QLabel("連線設定組"))
        self.profile_box = QComboBox()
        self.profile_box.setEditable(True)
        self.profile_box.setMinimumWidth(430)
        self.profile_box.currentTextChanged.connect(self._profile_changed)
        profile_row.addWidget(self.profile_box, 1)
        self.save_profile_button = QPushButton("儲存連線設定")
        self.save_profile_button.clicked.connect(self.save_connection)
        profile_row.addWidget(self.save_profile_button)
        self.new_profile_button = QPushButton("另存新組")
        self.new_profile_button.clicked.connect(self.new_connection)
        profile_row.addWidget(self.new_profile_button)
        self.manage_profile_button = QPushButton("管理…")
        self.manage_profile_button.clicked.connect(lambda: self.manage_profiles("connections"))
        profile_row.addWidget(self.manage_profile_button)
        self.checks["auto_reconnect"] = QCheckBox("斷線後自動重連")
        self.checks["auto_reconnect"].stateChanged.connect(self._reconnect_option_changed)
        profile_row.addWidget(self.checks["auto_reconnect"])
        self.stop_reconnect_button = QPushButton("停止重連")
        self.stop_reconnect_button.setObjectName("warningButton")
        self.stop_reconnect_button.clicked.connect(self.stop_reconnect)
        profile_row.addWidget(self.stop_reconnect_button)
        layout.addLayout(profile_row)

        grid = QGridLayout()
        self.connection_grid = grid
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(3)
        grid.addWidget(QLabel("模式"), 0, 0)
        self.mode_box = QComboBox()
        self.mode_box.addItems(MODES)
        self.mode_box.currentTextChanged.connect(self._mode_changed)
        grid.addWidget(self.mode_box, 0, 1)
        grid.addWidget(QLabel("Server host"), 0, 2)
        self.fields["host"] = self._line(grid, "host", 0, 3, 2)
        grid.addWidget(QLabel("Port"), 0, 5)
        self.fields["port"] = self._line(grid, "port", 0, 6)
        grid.addWidget(QLabel("Source / Target"), 0, 7)
        self.source_box = QComboBox()
        self.source_box.addItems(["running", "candidate", "startup"])
        self.source_box.currentTextChanged.connect(self._source_changed)
        grid.addWidget(self.source_box, 0, 8)
        self.connect_button = QPushButton("連線 / 開始監測")
        self.connect_button.setObjectName("connectButton")
        self.connect_button.clicked.connect(self.connect)
        grid.addWidget(self.connect_button, 0, 9)

        grid.addWidget(QLabel("Listen host"), 1, 0)
        self.fields["listen_host"] = self._line(grid, "listen_host", 1, 1, 2)
        grid.addWidget(QLabel("Listen port"), 1, 3)
        self.fields["listen_port"] = self._line(grid, "listen_port", 1, 4)
        grid.addWidget(QLabel("等待秒數"), 1, 5)
        self.fields["timeout"] = self._line(grid, "timeout", 1, 6)
        grid.addWidget(QLabel("RPC timeout"), 1, 7)
        self.fields["rpc_timeout"] = self._line(grid, "rpc_timeout", 1, 8)
        self.disconnect_button = QPushButton("中斷 / 取消等待")
        self.disconnect_button.setObjectName("warningButton")
        self.disconnect_button.clicked.connect(self.disconnect)
        grid.addWidget(self.disconnect_button, 1, 9)
        grid.setColumnStretch(3, 3)
        grid.setColumnStretch(4, 1)
        grid.setColumnStretch(8, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
        self.tabs.addTab(page, "NETCONF連線")

    def _path_line(self, layout, label, name, row, column, *, password=False):
        layout.addWidget(QLabel(label), row, column)
        edit = self._line(layout, name, row, column + 1, 2, password=password)
        button = QPushButton("瀏覽…")
        button.clicked.connect(lambda: self._browse(name))
        layout.addWidget(button, row, column + 3)
        return edit

    def _build_ssh_tab(self):
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(page)
        self.ssh_grid = grid
        grid.setContentsMargins(5, 2, 5, 2)
        grid.setVerticalSpacing(3)
        grid.addWidget(QLabel("SSH 帳號組"), 0, 0)
        self.account_box = QComboBox()
        self.account_box.setEditable(True)
        self.account_box.setMinimumWidth(300)
        self.account_box.currentTextChanged.connect(self._account_changed)
        grid.addWidget(self.account_box, 0, 1, 1, 3)
        save = QPushButton("儲存帳號")
        save.clicked.connect(self.save_account)
        grid.addWidget(save, 0, 4)
        new = QPushButton("新增帳號")
        new.clicked.connect(self.new_account)
        grid.addWidget(new, 0, 5)
        manage = QPushButton("管理…")
        manage.clicked.connect(lambda: self.manage_profiles("accounts"))
        grid.addWidget(manage, 0, 6)
        self._labeled_field(grid, "帳號", "username", 1, 0)
        self._labeled_field(grid, "密碼（加密儲存）", "password", 1, 3, password=True)
        self._path_line(grid, "Private key", "ssh_key", 1, 6)
        self._path_line(grid, "Known hosts", "known_hosts", 2, 0)
        self.checks["hostkey_verify"] = QCheckBox("驗證 SSH host key")
        grid.addWidget(self.checks["hostkey_verify"], 2, 4)
        self.checks["allow_agent"] = QCheckBox("SSH agent")
        self.checks["allow_agent"].setChecked(True)
        grid.addWidget(self.checks["allow_agent"], 2, 6)
        self.checks["look_for_keys"] = QCheckBox("搜尋本機金鑰")
        self.checks["look_for_keys"].setChecked(True)
        grid.addWidget(self.checks["look_for_keys"], 2, 8)
        grid.setColumnStretch(2, 2)
        grid.setColumnStretch(5, 1)
        self.tabs.addTab(page, "SSH 認證")

    def _build_tls_tab(self):
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(page)
        grid.setContentsMargins(5, 2, 5, 2)
        grid.setVerticalSpacing(3)
        self._path_line(grid, "Client cert", "cert", 0, 0)
        self._path_line(grid, "Private key", "tls_key", 0, 4)
        self._path_line(grid, "Trusted CA", "trusted_ca", 1, 0)
        self._labeled_field(grid, "Server name / SAN", "tls_server_name", 1, 4)
        self.checks["verify_hostname"] = QCheckBox("驗證名稱")
        grid.addWidget(self.checks["verify_hostname"], 1, 7)
        note = QLabel("TLS 驗證名稱預設不勾選；若伺服器憑證 SAN 是 DNS 名稱而非連線 IP，請填名稱後再勾選。")
        note.setWordWrap(True)
        grid.addWidget(note, 2, 0, 1, 8)
        grid.setColumnStretch(2, 2)
        grid.setColumnStretch(6, 2)
        self.tabs.addTab(page, "TLS 憑證")

    def _build_jump_tab(self):
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(page)
        grid.setContentsMargins(5, 2, 5, 2)
        grid.setVerticalSpacing(3)
        grid.addWidget(QLabel("跳板 host"), 0, 0)
        self._line(grid, "jump_host", 0, 1, 3)
        grid.addWidget(QLabel("Port"), 0, 4)
        self._line(grid, "jump_port", 0, 5)
        grid.addWidget(QLabel("認證方式"), 0, 6)
        self.jump_auth = QComboBox()
        self.jump_auth.addItems(["password", "private-key", "agent", "auto"])
        grid.addWidget(self.jump_auth, 0, 7)
        self.checks["jump_enabled"] = QCheckBox("啟用（僅 Direct SSH）")
        grid.addWidget(self.checks["jump_enabled"], 0, 8, 1, 2)
        grid.addWidget(QLabel("跳板帳號"), 1, 0)
        self._line(grid, "jump_username", 1, 1, 3)
        grid.addWidget(QLabel("跳板登入密碼"), 1, 4)
        self._line(grid, "jump_password", 1, 5, 3, password=True)
        self.checks["jump_verify"] = QCheckBox("驗證跳板 host key")
        grid.addWidget(self.checks["jump_verify"], 1, 8, 1, 2)
        self._path_line(grid, "跳板 Private key", "jump_key", 2, 0)
        grid.addWidget(QLabel("私鑰密碼"), 2, 4)
        self._line(grid, "jump_passphrase", 2, 5, 3, password=True)
        self._path_line(grid, "跳板 Known hosts", "jump_known_hosts", 2, 8)
        grid.setColumnStretch(3, 2)
        grid.setColumnStretch(7, 2)
        self.tabs.addTab(page, "SSH 跳板")

    def _build_advanced_tab(self):
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(page)
        self.advanced_grid = grid
        grid.setContentsMargins(5, 2, 5, 2)
        grid.setVerticalSpacing(3)
        grid.setHorizontalSpacing(7)
        # Keep the most-used schema refresh and fallback directory first.
        self.refresh_schema_button = QPushButton("更新 YANG schema")
        self.refresh_schema_button.clicked.connect(self.refresh_schema)
        grid.addWidget(self.refresh_schema_button, 0, 0)
        grid.addWidget(QLabel("本機 YANG 資料夾（備援）"), 0, 1)
        schema_dir = self._line(grid, "schema_dir", 0, 2, 2)
        schema_dir.setMaximumWidth(360)
        schema_browse = QPushButton("瀏覽…")
        schema_browse.clicked.connect(lambda: self._browse("schema_dir"))
        grid.addWidget(schema_browse, 0, 4)
        grid.addWidget(QLabel("NETCONF version"), 0, 5)
        self.netconf_version = QComboBox()
        self.netconf_version.addItems(["auto", "1.0", "1.1"])
        self.netconf_version.setMaximumWidth(100)
        grid.addWidget(self.netconf_version, 0, 6)
        grid.addWidget(QLabel("TLS version"), 0, 7)
        self.tls_version = QComboBox()
        self.tls_version.addItems(["auto", "1.2", "1.3"])
        self.tls_version.setMaximumWidth(100)
        grid.addWidget(self.tls_version, 0, 8)

        grid.addWidget(QLabel("Bind address（選用）"), 1, 0)
        bind = self._line(grid, "bind", 1, 1, 2)
        bind.setMaximumWidth(250)
        grid.addWidget(QLabel("CRL（選用）"), 1, 3)
        crl = self._line(grid, "crl", 1, 4, 2)
        crl.setMaximumWidth(300)
        crl_browse = QPushButton("瀏覽…")
        crl_browse.clicked.connect(lambda: self._browse("crl"))
        grid.addWidget(crl_browse, 1, 6)

        self.export_public_button = QPushButton("匯出設定 JSON（不含密碼）")
        self.export_public_button.clicked.connect(lambda: self.export_settings(False))
        grid.addWidget(self.export_public_button, 2, 0, 1, 2)
        self.export_private_button = QPushButton("匯出加密備份（含密碼）")
        self.export_private_button.clicked.connect(lambda: self.export_settings(True))
        grid.addWidget(self.export_private_button, 2, 2, 1, 2)
        self.import_settings_button = QPushButton("匯入設定…")
        self.import_settings_button.clicked.connect(self.import_profiles)
        grid.addWidget(self.import_settings_button, 2, 4)
        grid.setColumnStretch(3, 2)
        grid.setColumnStretch(9, 1)
        self.tabs.addTab(page, "進階")

    def _build_ssh_method_tab(self):
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(page)
        grid.setContentsMargins(5, 2, 5, 2)
        grid.setVerticalSpacing(3)
        grid.addWidget(QLabel("認證方式"), 0, 0)
        self.ssh_auth = QComboBox()
        self.ssh_auth.addItems(["auto", "password", "private-key", "agent"])
        self.ssh_auth.currentTextChanged.connect(self._schedule_preferences)
        grid.addWidget(self.ssh_auth, 0, 1)
        self._labeled_field(grid, "私鑰密碼（passphrase）", "key_passphrase", 0, 3, password=True)
        note = QLabel(
            "auto：依序嘗試私鑰／本機金鑰／Agent／登入密碼；其他模式只使用指定方法。\n"
            "登入密碼及私鑰路徑在 SSH 認證分頁；私鑰密碼不會當登入密碼送出。"
        )
        note.setWordWrap(True)
        grid.addWidget(note, 1, 0, 1, 8)
        grid.setColumnStretch(2, 2)
        self.tabs.addTab(page, "SSH 認證方式 / 私鑰密碼")

    def _build_admin_tab(self):
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(page)
        self.admin_grid = grid
        grid.setContentsMargins(5, 2, 5, 2)
        grid.setVerticalSpacing(3)
        grid.setHorizontalSpacing(7)
        # Row 1: normal password-login fields, ordered by everyday use.
        grid.addWidget(QLabel("SSH host"), 0, 0)
        admin_host = self._line(grid, "admin_host", 0, 1, 2)
        admin_host.setMinimumWidth(130)
        admin_host.setMaximumWidth(240)
        grid.addWidget(QLabel("Port"), 0, 3)
        admin_port = self._line(grid, "admin_port", 0, 4)
        admin_port.setMinimumWidth(50)
        admin_port.setMaximumWidth(65)
        grid.addWidget(QLabel("系統帳號"), 0, 5)
        admin_username = self._line(grid, "admin_username", 0, 6)
        admin_username.setMinimumWidth(85)
        admin_username.setMaximumWidth(150)
        grid.addWidget(QLabel("登入密碼"), 0, 7)
        admin_password = self._line(grid, "admin_password", 0, 8, 2, password=True)
        admin_password.setMinimumWidth(150)
        admin_password.setMaximumWidth(210)

        # Row 2: connection policy and sysrepocfg execution settings.
        grid.addWidget(QLabel("認證方式"), 1, 0)
        self.admin_auth = QComboBox()
        self.admin_auth.addItems(["password", "private-key", "agent", "auto"])
        self.admin_auth.currentTextChanged.connect(self._schedule_preferences)
        self.admin_auth.setMaximumWidth(120)
        grid.addWidget(self.admin_auth, 1, 1)
        grid.addWidget(QLabel("Timeout 秒"), 1, 2)
        admin_timeout = self._line(grid, "admin_timeout", 1, 3)
        admin_timeout.setMinimumWidth(50)
        admin_timeout.setMaximumWidth(65)
        grid.addWidget(QLabel("sysrepocfg 路徑"), 1, 4)
        admin_program = self._line(grid, "admin_program", 1, 5, 2)
        admin_program.setMinimumWidth(120)
        admin_program.setMaximumWidth(170)
        self.checks["admin_verify"] = QCheckBox("驗證 host key")
        grid.addWidget(self.checks["admin_verify"], 1, 7)
        self.checks["admin_jump"] = QCheckBox("經 SSH 跳板頁主機")
        grid.addWidget(self.checks["admin_jump"], 1, 8, 1, 2)

        # Row 3: less frequently used key authentication paths.
        grid.addWidget(QLabel("Private key"), 2, 0)
        admin_key = self._line(grid, "admin_key", 2, 1, 2)
        admin_key.setMinimumWidth(130)
        admin_key.setMaximumWidth(200)
        admin_key_browse = QPushButton("瀏覽…")
        admin_key_browse.clicked.connect(lambda: self._browse("admin_key"))
        grid.addWidget(admin_key_browse, 2, 3)
        grid.addWidget(QLabel("私鑰密碼"), 2, 4)
        admin_passphrase = self._line(grid, "admin_passphrase", 2, 5, password=True)
        admin_passphrase.setMinimumWidth(80)
        admin_passphrase.setMaximumWidth(130)
        grid.addWidget(QLabel("Known hosts"), 2, 6)
        admin_known_hosts = self._line(grid, "admin_known_hosts", 2, 7, 2)
        admin_known_hosts.setMinimumWidth(130)
        admin_known_hosts.setMaximumWidth(200)
        admin_known_hosts_browse = QPushButton("瀏覽…")
        admin_known_hosts_browse.clicked.connect(lambda: self._browse("admin_known_hosts"))
        grid.addWidget(admin_known_hosts_browse, 2, 9)

        self.admin_connect_button = QPushButton("連線系統 SSH")
        self.admin_connect_button.setObjectName("adminConnectButton")
        self.admin_connect_button.setMinimumWidth(110)
        self.admin_connect_button.clicked.connect(self.connect_admin)
        grid.addWidget(self.admin_connect_button, 0, 12)
        self.admin_disconnect_button = QPushButton("中斷 SSH")
        self.admin_disconnect_button.setObjectName("warningButton")
        self.admin_disconnect_button.setMinimumWidth(110)
        self.admin_disconnect_button.clicked.connect(self.disconnect_admin)
        grid.addWidget(self.admin_disconnect_button, 1, 12)
        self.admin_status = QLabel("系統 SSH 未連線（獨立於 NETCONF）")
        self.admin_status.setMinimumWidth(0)
        self.admin_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.admin_status.setToolTip(self.admin_status.text())
        grid.addWidget(self.admin_status, 2, 10, 1, 3)
        page.setToolTip("系統 SSH/sysrepocfg 是獨立的 OS 管理通道，不會繞過 NETCONF NACM；執行前會顯示命令與 XML 預覽。")
        grid.setColumnStretch(10, 1)
        self.tabs.addTab(page, "系統SSH")

    def _build_backup_tab(self):
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(page)
        grid.setContentsMargins(5, 2, 5, 2)
        grid.setVerticalSpacing(3)
        self._labeled_field(grid, "遠端 BASE", "backup_base", 0, 0)
        self._labeled_field(grid, "sysrepoctl 路徑", "backup_sysrepoctl", 0, 4)
        self._labeled_field(grid, "初始 YANG module", "backup_init_module", 1, 0)
        grid.addWidget(QLabel("備份 datastore"), 1, 4)
        running = QLabel("☑ running（必要）")
        grid.addWidget(running, 1, 5)
        self.checks["backup_candidate"] = QCheckBox("candidate")
        self.checks["backup_startup"] = QCheckBox("startup")
        grid.addWidget(self.checks["backup_candidate"], 1, 6)
        grid.addWidget(self.checks["backup_startup"], 1, 7)
        self.backup_check_button = QPushButton("檢查 YANG 初始化")
        self.backup_check_button.clicked.connect(self.check_yang_initialization)
        grid.addWidget(self.backup_check_button, 2, 0)
        self.backup_create_button = QPushButton("建立遠端備份")
        self.backup_create_button.setObjectName("backupCreateButton")
        self.backup_create_button.clicked.connect(self.create_system_backup)
        grid.addWidget(self.backup_create_button, 2, 1)
        self.backup_restore_button = QPushButton("從最新備份還原 running")
        self.backup_restore_button.setObjectName("backupRestoreButton")
        self.backup_restore_button.clicked.connect(self.restore_system_backup)
        grid.addWidget(self.backup_restore_button, 2, 2, 1, 3)
        self.backup_status = QLabel("尚未執行；備份使用 UTC 時間目錄，還原會自動找最新並驗證 SHA256。")
        self.backup_status.setWordWrap(True)
        self.backup_status.setStyleSheet("color:#925127")
        grid.addWidget(self.backup_status, 2, 5, 1, 5)
        grid.setColumnStretch(2, 2)
        grid.setColumnStretch(8, 2)
        self.tabs.addTab(page, "備份 / 還原")

    def _labeled_field(self, layout, label, name, row, column, *, password=False):
        layout.addWidget(QLabel(label), row, column)
        return self._line(layout, name, row, column + 1, 2, password=password)

    def _build_tree_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(2)
        header = QHBoxLayout()
        title = QLabel("DATA TREE")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(QLabel("搜尋"))
        self.tree_search = QLineEdit()
        self.tree_search.setObjectName("treeSearch")
        self.tree_search.setPlaceholderText("節點名稱、值或路徑")
        self.tree_search.setClearButtonEnabled(True)
        self.tree_search.setMinimumWidth(180)
        self.tree_search.setMaximumWidth(340)
        header.addWidget(self.tree_search)
        self.tree_search_count = QLabel()
        self.tree_search_count.setObjectName("treeSearchCount")
        self.tree_search_count.setMinimumWidth(44)
        header.addWidget(self.tree_search_count)
        layout.addLayout(header)

        options = QHBoxLayout()
        self.tree_options_layout = options
        options.setSpacing(10)
        self.checks["defaults"] = QCheckBox("包含 YANG default 值")
        self.checks["defaults"].stateChanged.connect(self._options_changed)
        options.addWidget(self.checks["defaults"])
        self.checks["state"] = QCheckBox("包含 config false（唯讀）")
        self.checks["state"].stateChanged.connect(self._options_changed)
        options.addWidget(self.checks["state"])
        self.checks["show_candidates"] = QCheckBox("顯示可新增節點")
        self.checks["show_candidates"].setChecked(False)
        self.checks["show_candidates"].stateChanged.connect(lambda: self._rebuild_tree())
        options.addWidget(self.checks["show_candidates"])
        options.addStretch(1)
        layout.addLayout(options)
        buttons = QHBoxLayout()
        self.read_button = QPushButton("重新讀取全部")
        self.read_button.clicked.connect(self.read_all)
        buttons.addWidget(self.read_button)
        self.tree_schema_button = QPushButton("更新 YANG")
        self.tree_schema_button.clicked.connect(self.refresh_schema)
        buttons.addWidget(self.tree_schema_button)
        self.tree_export_button = QPushButton("匯出XML")
        self.tree_export_button.clicked.connect(self.export_tree)
        buttons.addWidget(self.tree_export_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        edit_buttons = QHBoxLayout()
        add = QPushButton("新增子節點…")
        add.clicked.connect(self.new_node)
        self.add_child_button = add
        edit_buttons.addWidget(add)
        add_root = QPushButton("新增根節點…")
        add_root.clicked.connect(self.new_root_node)
        self.add_root_button = add_root
        edit_buttons.addWidget(add_root)
        delete = QPushButton("刪除整個節點…")
        delete.setObjectName("deleteNodeButton")
        delete.clicked.connect(self.delete_selected)
        self.delete_button = delete
        edit_buttons.addWidget(delete)
        self.draft_button = QPushButton("草稿清單（0）")
        self.draft_button.clicked.connect(self.show_drafts)
        edit_buttons.addWidget(self.draft_button)
        self.leaf_button = QPushButton("表單編輯 leaf／引用…")
        self.leaf_button.clicked.connect(self.open_leaf_editor)
        edit_buttons.addWidget(self.leaf_button)
        edit_buttons.addStretch(1)
        layout.addLayout(edit_buttons)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(18)
        self.tree.setAnimated(False)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_context_menu)
        self.tree.itemExpanded.connect(self._item_expanded)
        self.tree.itemClicked.connect(self._item_clicked)
        self.tree.itemDoubleClicked.connect(self._item_double_clicked)
        self.tree_search.textChanged.connect(self.filter_tree)
        layout.addWidget(self.tree, 1)
        self.schema_status = QLabel("YANG：未載入")
        self.schema_status.setObjectName("treeFooter")
        return panel

    def _build_xml_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)
        upper = QSplitter(Qt.Orientation.Vertical)
        upper.setChildrenCollapsible(False)
        layout.addWidget(upper, 1)

        editor_page = QWidget()
        editor_layout = QVBoxLayout(editor_page)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(2)
        bar = QHBoxLayout()
        title = QLabel("讀取 / 編輯 XML")
        title.setObjectName("sectionTitle")
        bar.addWidget(title)
        self.wrap_xml = QCheckBox("自動折行")
        self.wrap_xml.setChecked(True)
        self.wrap_xml.stateChanged.connect(self._wrap_changed)
        bar.addWidget(self.wrap_xml)
        bar.addStretch(1)
        self.export_editor_button = QPushButton("匯出XML")
        self.export_editor_button.clicked.connect(self.export_editor)
        bar.addWidget(self.export_editor_button)
        self.revert_button = QPushButton("還原")
        self.revert_button.clicked.connect(self.revert)
        bar.addWidget(self.revert_button)
        self.pretty_button = QPushButton("Pretty")
        self.pretty_button.clicked.connect(self.pretty_editor)
        bar.addWidget(self.pretty_button)
        self.refresh_selected_button = QPushButton("重新讀取")
        self.refresh_selected_button.clicked.connect(self.refresh_selected)
        bar.addWidget(self.refresh_selected_button)
        editor_layout.addLayout(bar)
        self.path_label = QLabel("請先連線，再選擇左側節點")
        self.path_label.setStyleSheet("color: #526679")
        editor_layout.addWidget(self.path_label)
        legend = QLabel("伺服器 default · 等於 schema default · config false · 修改中 · schema 未知")
        legend.setStyleSheet("color: #526679")
        editor_layout.addWidget(legend)
        self.editor = CodeEditor()
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setFont(QFont("Consolas", 11))
        self.editor.setTabStopDistance(4 * self.editor.fontMetrics().horizontalAdvance(" "))
        self.editor.textChanged.connect(self._editor_changed)
        self.editor_highlighter = XmlHighlighter(self.editor)
        editor_layout.addWidget(self.editor, 1)
        upper.addWidget(editor_page)

        output_page = QWidget()
        output_layout = QVBoxLayout(output_page)
        output_layout.setContentsMargins(0, 5, 0, 0)
        send_bar = QHBoxLayout()
        send_title = QLabel("實際送出 XML")
        send_title.setObjectName("sectionTitle")
        send_bar.addWidget(send_title)
        send_bar.addStretch(1)
        self.netconf_button = QPushButton("NETCONF方式修改")
        self.netconf_button.setObjectName("netconfButton")
        self.netconf_button.clicked.connect(self.send)
        send_bar.addWidget(self.netconf_button)
        self.sysrepo_button = QPushButton("使用系統sysrepocfg修改")
        self.sysrepo_button.setObjectName("sysrepoButton")
        self.sysrepo_button.clicked.connect(self.sysrepo_modify)
        send_bar.addWidget(self.sysrepo_button)
        output_layout.addLayout(send_bar)
        self.preview_status = QLabel("尚無變更，不會送出任何設定")
        self.preview_status.setStyleSheet("color: #925127")
        output_layout.addWidget(self.preview_status)
        self.output_tabs = QTabWidget()
        self.output_tabs.setDocumentMode(False)
        self.output_tabs.setStyleSheet(
            "QTabBar::tab { background:#dbe4ef; color:#24364b; border:1px solid #c0cad6; "
            "padding:3px 8px; min-height:18px; }"
            "QTabBar::tab:selected { background:#0969da; color:white; font-weight:600; }"
        )
        self.preview = self._output_edit()
        self.reply = self._output_edit()
        self.diff = self._output_edit()
        self.output_tabs.addTab(self.preview, "待送出 RPC（唯讀）")
        self.output_tabs.addTab(self.reply, "最後 RPC 回應")
        self.output_tabs.addTab(self.diff, "修改差異")
        self.audit_pane = self._output_edit()
        audit_page = QWidget()
        audit_layout = QVBoxLayout(audit_page)
        audit_layout.setContentsMargins(0, 0, 0, 0)
        audit_bar = QHBoxLayout()
        audit_bar.addWidget(QLabel("本機操作紀錄；密碼與 XML 不寫入此紀錄。"))
        audit_bar.addStretch(1)
        audit_export = QPushButton("匯出紀錄 JSON")
        audit_export.clicked.connect(self.export_audit)
        audit_bar.addWidget(audit_export)
        audit_layout.addLayout(audit_bar)
        audit_layout.addWidget(self.audit_pane, 1)
        self.output_tabs.addTab(audit_page, "操作紀錄")
        self.event_page = self._build_event_page()
        self.output_tabs.addTab(self.event_page, "事件通知")
        self.result_frame = self._build_result_page()
        self.output_tabs.addTab(self.result_frame, "送出結果核對")
        output_layout.addWidget(self.output_tabs, 1)
        upper.addWidget(output_page)
        upper.setSizes([560, 360])
        return panel

    def _build_event_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Stream"))
        self.stream_box = QComboBox()
        self.stream_box.addItem("NETCONF")
        bar.addWidget(self.stream_box)
        self.subscribe_button = QPushButton("開始訂閱")
        self.subscribe_button.clicked.connect(self.subscribe)
        bar.addWidget(self.subscribe_button)
        self.stop_subscription_button = QPushButton("停止（中斷連線）")
        self.stop_subscription_button.clicked.connect(self.stop_subscription)
        bar.addWidget(self.stop_subscription_button)
        export = QPushButton("匯出通知…")
        export.clicked.connect(self.export_notifications)
        bar.addWidget(export)
        bar.addStretch(1)
        layout.addLayout(bar)
        self.notification_status = QLabel("未訂閱；使用目前 session，斷線後不會自動重新訂閱。")
        self.notification_status.setWordWrap(True)
        layout.addWidget(self.notification_status)
        self.notification_pane = self._output_edit()
        layout.addWidget(self.notification_pane, 1)
        return page

    def _build_result_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.result_status = QLabel("尚未送出；此頁只保留本次執行的最近一次修改。")
        self.result_status.setWordWrap(True)
        layout.addWidget(self.result_status)
        self.reread_result_button = QPushButton("重新讀回核對（不重送）")
        self.reread_result_button.clicked.connect(self.reread_attempt)
        layout.addWidget(self.reread_result_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.result_tree = QTreeWidget()
        self.result_tree.setHeaderLabels(["節點／instance", "預期值", "讀回值", "結果"])
        self.result_tree.itemDoubleClicked.connect(self.show_result_xml)
        layout.addWidget(self.result_tree, 1)
        self.rollback_check = QCheckBox("NETCONF rollback-on-error（需 server 支援；不適用 sysrepocfg）")
        self.checks["rollback_on_error"] = self.rollback_check
        self.rollback_check.stateChanged.connect(self.update_preview)
        layout.addWidget(self.rollback_check)
        return page

    @staticmethod
    def _output_edit():
        edit = CodeEditor()
        edit.setReadOnly(True)
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        edit.setFont(QFont("Consolas", 10))
        return edit

    # ---------- settings/profile handling ----------

    def _default_values(self):
        return {
            "mode": "Direct SSH", "host": "127.0.0.1", "port": "830",
            "listen_host": "0.0.0.0", "listen_port": "4334", "source": "running",
            "timeout": "30", "rpc_timeout": "30", "username": "", "password": "",
            "ssh_key": "", "known_hosts": "", "ssh_auth": "auto", "key_passphrase": "",
            "cert": "", "tls_key": "", "trusted_ca": "", "crl": "",
            "tls_server_name": "", "tls_version": "auto", "netconf_version": "auto",
            "bind": "", "schema_dir": "", "jump_host": "", "jump_port": "22",
            "jump_username": "", "jump_password": "", "jump_key": "",
            "jump_known_hosts": "", "jump_passphrase": "", "jump_auth": "password",
            "admin_host": "127.0.0.1", "admin_port": "22", "admin_username": "root",
            "admin_password": "", "admin_key": "", "admin_passphrase": "",
            "admin_auth": "password", "admin_known_hosts": "", "admin_program": "sysrepocfg",
            "admin_timeout": "10",
            "defaults": False, "state": False, "show_candidates": False,
            "wrap_xml": True, "connection_hidden": False, "auto_reconnect": False,
            "rollback_on_error": False, "jump_enabled": False, "jump_verify": False,
            "hostkey_verify": False, "verify_hostname": False, "allow_agent": True,
            "look_for_keys": True, "admin_verify": False, "admin_jump": False,
            "backup_base": system_backup.DEFAULT_BASE,
            "backup_sysrepoctl": system_backup.DEFAULT_SYSREPOCTL,
            "backup_init_module": system_backup.DEFAULT_INIT_MODULE,
            "backup_candidate": False, "backup_startup": False,
            "tls_version": "auto", "netconf_version": "auto",
        }

    def _values(self):
        result = self._default_values()
        result.update({name: widget.text() for name, widget in self.fields.items()})
        result["mode"] = self.mode_box.currentText()
        result["source"] = self.source_box.currentText()
        result["ssh_auth"] = self._combo_text("ssh_auth", "auto")
        result["jump_auth"] = self.jump_auth.currentText()
        result["admin_auth"] = self.admin_auth.currentText()
        result["tls_version"] = self.tls_version.currentText()
        result["netconf_version"] = self.netconf_version.currentText()
        result["wrap_xml"] = self.wrap_xml.isChecked()
        result["connection_hidden"] = self.connection_hidden
        result.update({name: box.isChecked() for name, box in self.checks.items()})
        result[CONNECTION_ACCOUNT_FIELD] = self.account_box.currentText().strip()
        return result

    def _combo_text(self, name, default):
        combo = getattr(self, name, None)
        return combo.currentText() if combo is not None else default

    def _apply_values(self, values):
        self._prefs_loading = True
        try:
            for name, widget in self.fields.items():
                if name in values:
                    widget.setText(str(values[name]))
            if "mode" in values:
                self.mode_box.setCurrentText(str(values["mode"]))
            if "source" in values:
                self.source_box.setCurrentText(str(values["source"]))
            if "ssh_auth" in values:
                self.ssh_auth.setCurrentText(str(values["ssh_auth"]))
            if "jump_auth" in values:
                self.jump_auth.setCurrentText(str(values["jump_auth"]))
            if "admin_auth" in values:
                self.admin_auth.setCurrentText(str(values["admin_auth"]))
            if "tls_version" in values:
                self.tls_version.setCurrentText(str(values["tls_version"]))
            if "netconf_version" in values:
                self.netconf_version.setCurrentText(str(values["netconf_version"]))
            if "wrap_xml" in values:
                self.wrap_xml.setChecked(bool(values["wrap_xml"]))
            if "connection_hidden" in values:
                hidden = bool(values["connection_hidden"])
                self.connection_hidden = hidden
                self.connection_area.setVisible(not hidden)
                self.toggle_connection_button.setText("▲ 顯示連線設定" if hidden else "▼ 隱藏連線設定")
            for name, box in self.checks.items():
                if name in values:
                    box.setChecked(bool(values[name]))
            self._mode_changed()
        finally:
            self._prefs_loading = False

    def _apply_account(self, values):
        self._prefs_loading = True
        try:
            for name in ("username", "password", "ssh_key", "allow_agent", "look_for_keys",
                         "ssh_auth", "key_passphrase"):
                if name not in values:
                    continue
                if name in self.fields:
                    self.fields[name].setText(str(values[name]))
                elif name in self.checks:
                    self.checks[name].setChecked(bool(values[name]))
                elif name == "ssh_auth":
                    self.ssh_auth.setCurrentText(str(values[name]))
        finally:
            self._prefs_loading = False

    def _restore_preferences(self):
        try:
            self.preferences = PreferencesStore().load()
            last = self.preferences.get("last", {})
            self._apply_values(last.get("values", {}))
            self.profile_box.blockSignals(True)
            self.profile_box.setCurrentText(last.get("connection", ""))
            self.profile_box.blockSignals(False)
            self.account_box.blockSignals(True)
            self.account_box.setCurrentText(last.get("account", ""))
            self.account_box.blockSignals(False)
            self._refresh_profile_boxes()
        except Exception as exc:
            self.preferences_error = "無法讀取 GUI 設定（%s）；本次不覆寫原設定。" % type(exc).__name__
            self.status_label.setText(self.preferences_error)
        self._refresh_profile_boxes()

    def _refresh_profile_boxes(self):
        connection = self.profile_box.currentText()
        account = self.account_box.currentText()
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        self.profile_box.addItems(list(self.preferences["connections"]))
        self.profile_box.setCurrentText(connection)
        self.profile_box.blockSignals(False)
        self.account_box.blockSignals(True)
        self.account_box.clear()
        self.account_box.addItems(list(self.preferences["accounts"]))
        self.account_box.setCurrentText(account)
        self.account_box.blockSignals(False)

    def _profile_changed(self, name):
        if self._prefs_loading or not name or name not in self.preferences["connections"]:
            return
        self._apply_values(self.preferences["connections"][name])
        self.status_label.setText("已載入連線設定組「%s」；尚未連線。" % name)

    def _account_changed(self, name):
        if self._prefs_loading or not name or name not in self.preferences["accounts"]:
            return
        self._apply_account(self.preferences["accounts"][name])

    def _schedule_preferences(self, *_args):
        if not self.persist or self.demo or self._prefs_loading:
            return
        self.preference_timer.start()

    def save_preferences(self):
        if not self.persist or self.demo or self.preferences_error:
            return
        try:
            values = self._values()
            connection_name = self.profile_box.currentText().strip()
            if connection_name not in self.preferences["connections"]:
                connection_name = ""
            if connection_name and connection_name in self.preferences["connections"]:
                self.preferences["connections"][connection_name] = deepcopy(values)
            self.preferences["last"] = {
                "values": values,
                "connection": connection_name,
                "account": self.account_box.currentText().strip(),
                "language": self.language,
            }
            PreferencesStore().save(self.preferences)
        except Exception as exc:
            self.preferences_error = "設定保存失敗（%s）；原設定檔保留。" % type(exc).__name__
            self.status_label.setText(self.preferences_error)

    def save_connection(self):
        if self.busy or self.client.connected:
            return
        try:
            values = self._values()
            requested = self.profile_box.currentText().strip() if self.connection_save_as else self.profile_box.currentText().strip()
            if self.connection_save_as and requested in self.preferences["connections"]:
                raise EditError("連線設定組名稱已存在，請使用其他名稱；不會覆寫舊設定。")
            name = remember_connection(self.preferences, values, requested)
            self.profile_box.setCurrentText(name)
            self.connection_save_as = False
            self._refresh_profile_boxes()
            self.save_preferences()
            self.status_label.setText("已儲存連線設定（包含 NETCONF、SSH/TLS、跳板與系統 SSH 設定）。")
        except Exception as exc:
            self.show_error(exc)

    def new_connection(self):
        if self.busy or self.client.connected:
            return
        name, accepted = QInputDialog.getText(self, "另存新組", "新的連線設定組名稱：")
        if not accepted or not name.strip():
            return
        name = name.strip()
        if name in self.preferences["connections"]:
            self.show_error(EditError("連線設定組名稱已存在，請使用其他名稱。"))
            return
        try:
            # "另存新組" is an immediate snapshot, not a name-only draft.
            # This also captures SSH/TLS credentials, jump-host and system-SSH
            # fields through _values().
            remember_connection(self.preferences, self._values(), name)
            self.connection_save_as = False
            self._refresh_profile_boxes()
            self.profile_box.setCurrentText(name)
            self.save_preferences()
            self.status_label.setText("已另存並儲存連線設定組「%s」（包含帳密、跳板與系統 SSH）。" % name)
        except Exception as exc:
            self.show_error(exc)

    def save_account(self):
        if self.busy or self.client.connected:
            return
        try:
            name = remember_account(self.preferences, self._values(), self.account_box.currentText().strip())
            if not name:
                raise EditError("SSH 帳號不可為空白。")
            self.account_box.setCurrentText(name)
            self._refresh_profile_boxes()
            self.save_preferences()
            self.status_label.setText("已儲存 SSH 帳號組（密碼仍由 Windows DPAPI 保護）。")
        except Exception as exc:
            self.show_error(exc)

    def new_account(self):
        self.account_box.setCurrentText("")
        for name in ("username", "password", "ssh_key", "key_passphrase"):
            self.fields[name].clear()
        self.ssh_auth.setCurrentText("auto")

    def manage_profiles(self, group):
        if self.busy or self.client.connected:
            return
        ProfileDialog(self, group).exec()

    def rename_profile(self, group, old, new):
        try:
            book = deepcopy(self.preferences)
            book["last"] = {"values": self._values(), "connection": self.profile_box.currentText(),
                            "account": self.account_box.currentText()}
            changed = change_profiles(book, group, [old], new)
            PreferencesStore().save(changed)
            self.preferences = changed
            self._refresh_profile_boxes()
            return True
        except Exception as exc:
            self.show_error(exc)
            return False

    def delete_profile(self, group, name):
        try:
            book = deepcopy(self.preferences)
            book["last"] = {"values": self._values(), "connection": self.profile_box.currentText(),
                            "account": self.account_box.currentText()}
            changed = change_profiles(book, group, [name])
            PreferencesStore().save(changed)
            self.preferences = changed
            self._refresh_profile_boxes()
            return True
        except Exception as exc:
            self.show_error(exc)
            return False

    def export_settings(self, encrypted=False):
        suffix = ".dpapi" if encrypted else ".json"
        filename, _ = QFileDialog.getSaveFileName(self, "匯出 GUI 設定", "netconf-gui-backup" + suffix,
                                                  "Encrypted GUI backup (*.dpapi)" if encrypted else "JSON (*.json)")
        if not filename:
            return
        try:
            book = deepcopy(self.preferences)
            book["last"] = {"values": self._values(), "connection": self.profile_box.currentText(),
                            "account": self.account_box.currentText()}
            if encrypted:
                PreferencesStore(filename).save(book)
            else:
                from .preferences import public_book
                Path(filename).write_text(json.dumps(public_book(book), ensure_ascii=False, indent=2), encoding="utf-8")
            self.status_label.setText("已匯出設定：" + filename)
        except Exception as exc:
            self.show_error(exc)

    def _browse(self, name):
        if name == "schema_dir":
            filename = QFileDialog.getExistingDirectory(self, "選擇 YANG 資料夾")
        else:
            filename, _ = QFileDialog.getOpenFileName(self, "選擇檔案")
        if filename:
            self.fields[name].setText(filename)

    def toggle_connection(self):
        hidden = self.connection_area.isVisible()
        self.connection_hidden = hidden
        self.connection_area.setVisible(not hidden)
        self.toggle_connection_button.setText("▲ 顯示連線設定" if hidden else "▼ 隱藏連線設定")
        self._schedule_preferences()

    # ---------- settings validation/backend ----------

    def _text(self, name):
        return self.fields[name].text().strip()

    def _settings(self):
        mode = self.mode_box.currentText()
        tls = "TLS" in mode
        call_home = "Call Home" in mode
        port = int(self._text("port"))
        listen_port = int(self._text("listen_port"))
        if not 1 <= port <= 65535 or not 1 <= listen_port <= 65535:
            raise EditError("Port 必須介於 1–65535。")
        timeout = float(self._text("timeout"))
        rpc_timeout = float(self._text("rpc_timeout"))
        if timeout < 0 or (not call_home and timeout == 0) or rpc_timeout <= 0:
            raise EditError("Timeout 必須為正數；只有 Call Home 等待可使用 0。")
        endpoint = self._text("listen_host" if call_home else "host")
        if not endpoint:
            raise EditError("Host address is required.")
        if tls and (not self._text("cert") or not self._text("tls_key")):
            raise EditError("TLS requires a client certificate and private key.")
        auth = self._combo_text("ssh_auth", "auto")
        key = self._text("tls_key" if tls else "ssh_key")
        if not tls and not self._text("username"):
            raise EditError("SSH username is required.")
        if not tls and auth == "private-key" and not key:
            raise EditError("private-key 認證需要指定私鑰檔案。")
        files = [key, self._text("cert"), self._text("trusted_ca"), self._text("crl")] if tls else [key, self._text("known_hosts")]
        for filename in files:
            if filename and not Path(filename).expanduser().exists():
                raise EditError("File not found: " + filename)
        jump = {}
        if self.checks["jump_enabled"].isChecked():
            if tls or call_home:
                raise EditError("SSH 跳板目前只支援 Direct SSH。")
            if not self._text("jump_host") or not self._text("jump_username"):
                raise EditError("請填入跳板 host 與帳號。")
            jump_port = int(self._text("jump_port"))
            jump_auth = self.jump_auth.currentText()
            jump_key = self._text("jump_key") if jump_auth in {"auto", "private-key"} else ""
            if not 1 <= jump_port <= 65535:
                raise EditError("跳板 port 必須介於 1–65535。")
            if jump_auth == "private-key" and not jump_key:
                raise EditError("請指定跳板私鑰。")
            for filename in (jump_key, self._text("jump_known_hosts")):
                if filename and not Path(filename).expanduser().is_file():
                    raise EditError("File not found: " + filename)
            jump = dict(jump_enabled=True, jump_host=self._text("jump_host"), jump_port=jump_port,
                        jump_username=self._text("jump_username"), jump_password=self._text("jump_password") or None,
                        jump_key=jump_key or None, jump_passphrase=self._text("jump_passphrase") or None,
                        jump_auth=jump_auth, jump_verify=self.checks["jump_verify"].isChecked(),
                        jump_known_hosts=self._text("jump_known_hosts") or None)
        return ConnectionSettings(
            transport="tls" if tls else "ssh", call_home=call_home,
            host=self._text("host"), port=port, listen_host=self._text("listen_host"), listen_port=listen_port,
            username=self._text("username") or None, password=self._text("password") or None,
            key=key or None, ssh_auth=auth, key_passphrase=self._text("key_passphrase") or None,
            known_hosts=self._text("known_hosts") or None, hostkey_verify=self.checks["hostkey_verify"].isChecked(),
            allow_agent=self.checks["allow_agent"].isChecked(), look_for_keys=self.checks["look_for_keys"].isChecked(),
            cert=self._text("cert") or None, trusted_ca=self._text("trusted_ca") or None,
            crl=self._text("crl") or None, verify_hostname=self.checks["verify_hostname"].isChecked(),
            tls_server_name=self._text("tls_server_name") or None,
            tls_version=None if self.tls_version.currentText() == "auto" else self.tls_version.currentText(),
            netconf_version=None if self.netconf_version.currentText() == "auto" else self.netconf_version.currentText(),
            timeout=timeout, rpc_timeout=rpc_timeout, bind=self._text("bind") or None, huge_tree=True,
            **jump,
        )

    def _options(self):
        source = self.source_box.currentText()
        state = self.checks["state"].isChecked()
        if source != "running" and state:
            self.checks["state"].setChecked(False)
            state = False
        return ReadOptions(source, self.checks["defaults"].isChecked(), state)

    def _mode_changed(self, *_args):
        mode = self.mode_box.currentText()
        tls = "TLS" in mode
        call_home = "Call Home" in mode
        if hasattr(self, "tabs") and self.tabs.currentIndex() in {1, 2}:
            self.tabs.setCurrentIndex(2 if tls else 1)
        self.fields["host"].setEnabled(not call_home)
        self.fields["port"].setEnabled(not call_home)
        self.fields["listen_host"].setEnabled(call_home)
        self.fields["listen_port"].setEnabled(call_home)
        if self.fields["port"].text() in {"830", "6513"}:
            self.fields["port"].setText("6513" if tls else "830")
        if self.fields["listen_port"].text() in {"4334", "4335"}:
            self.fields["listen_port"].setText("4335" if tls else "4334")
        self.checks["jump_enabled"].setEnabled(not tls and not call_home)

    def _source_changed(self, *_args):
        if self.source_box.currentText() != "running":
            self.checks["state"].setChecked(False)
        self._options_changed()

    def _reconnect_option_changed(self, *_args):
        if not self.checks["auto_reconnect"].isChecked():
            self.reconnect_enabled = False
            self.reconnect_due = None
            self.status_label.setText("已停止自動重連；可手動連線。")
        elif self.client.connected and self.reconnect_settings is not None:
            self.reconnect_enabled = True
        self._sync_controls()

    def stop_reconnect(self):
        self.reconnect_enabled = False
        self.reconnect_due = None
        if self.busy and self.job_name == "自動重新連線…":
            self.client.cancel.set()
        self.status_label.setText("已停止自動重連；編輯內容保留。")
        self._sync_controls()

    def _monitor_connection(self):
        if self.closed or self.demo or self.busy:
            return
        if self.client.connected:
            self.reconnect_due = None
            return
        if not self.reconnect_enabled or not self.checks["auto_reconnect"].isChecked() or self.reconnect_settings is None:
            self.reconnect_due = None
            return
        if self.reconnect_due is None:
            self.reconnect_due = time.monotonic() + self.reconnect_delay
            self.status_label.setText("連線已中斷，%d 秒後重連；保留編輯內容，不重送 XML。" % self.reconnect_delay)
            self.reconnect_delay = min(30, self.reconnect_delay * 2)
        elif time.monotonic() >= self.reconnect_due:
            self.reconnect_due = None
            settings = deepcopy(self.reconnect_settings)
            directory = self.reconnect_schema_dir
            def work(progress):
                self.client.disconnect()
                self.client.connect(settings, progress)
                self.client.load_schema(directory, False, progress)
                return self.client.read(self._options())
            def done(snapshot):
                self.reconnect_delay = 2
                self.uncertain = True
                self._accept_snapshot(snapshot)
                self.status_label.setText("已重新連線；編輯內容已保留。請先重新讀取確認，才能送出修改。")
            self._run("自動重新連線…", work, done)

    def _options_changed(self, *_args):
        if self.busy or not self.client.connected or self.snapshot is None:
            return
        self.read_all()

    # ---------- worker dispatch ----------

    def _run(self, label, function, done=None, failed=None):
        if self.busy:
            return
        self.busy = True
        self.job_name = label
        self.status_label.setText(label)
        self.progress.setRange(0, 0)
        self._sync_controls()
        thread = QThread(self)
        runner = TaskRunner(function)
        runner.moveToThread(thread)
        thread.started.connect(runner.run)
        # Connect worker results to bound QObject slots with an explicit queued
        # connection.  A plain nested Python callback may otherwise execute in
        # the worker thread and touch widgets there, which can crash Qt after a
        # successful NETCONF/SSH connection.
        self._task_done_callback = done
        self._task_failed_callback = failed
        runner.succeeded.connect(self._task_succeeded, Qt.ConnectionType.QueuedConnection)
        runner.failed.connect(self._task_failed, Qt.ConnectionType.QueuedConnection)
        runner.progress.connect(self._task_progress, Qt.ConnectionType.QueuedConnection)
        runner.succeeded.connect(runner.deleteLater)
        runner.failed.connect(runner.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._task_thread_finished, Qt.ConnectionType.QueuedConnection)
        self._task_thread, self._task_runner = thread, runner
        thread.start()

    @Slot(str)
    def _task_progress(self, message):
        if self._task_runner is not None:
            self.status_label.setText(message)

    @Slot(object)
    def _task_succeeded(self, value):
        if self._task_runner is None:
            return
        thread = self._task_thread
        callback = self._task_done_callback
        self._finish_task()
        try:
            if callback:
                callback(value)
        except BaseException as exc:
            self.show_error(exc)
        finally:
            self._sync_controls()
            if thread is not None:
                thread.quit()

    @Slot(object)
    def _task_failed(self, exc):
        if self._task_runner is None:
            return
        thread = self._task_thread
        callback = self._task_failed_callback
        self._finish_task()
        try:
            if callback:
                callback(exc)
            else:
                self.show_error(exc)
        except BaseException as failure_exc:
            self.show_error(failure_exc)
        finally:
            self._sync_controls()
            if thread is not None:
                thread.quit()

    @Slot()
    def _task_thread_finished(self):
        thread = self.sender()
        if self._task_thread is thread:
            self._task_thread = None
            self._task_runner = None
            self._task_done_callback = None
            self._task_failed_callback = None

    def _finish_task(self):
        self.busy = False
        self.job_name = ""
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._sync_controls()

    def connect(self):
        if self.busy or self.client.connected:
            return
        try:
            settings = self._settings()
            options = self._options()
            self.save_preferences()
        except Exception as exc:
            self.show_error(exc)
            return

        self.reconnect_settings = deepcopy(settings)
        self.reconnect_schema_dir = self._text("schema_dir")
        self.reconnect_delay = 2
        self.reconnect_enabled = self.checks["auto_reconnect"].isChecked()
        def work(progress):
            self.client.connect(settings, progress)
            self.client.load_schema(self._text("schema_dir"), False, progress)
            return self.client.read(options)

        def done(snapshot):
            self._accept_snapshot(snapshot)
            self.reconnect_delay = 2
            self.admin_requires_refresh = False
            self.status_label.setText("已連線並讀取 %s；YANG schema 已載入。" % options.source)

        self._run("連線、讀取 schema 與資料…", work, done)

    def disconnect(self):
        if self.busy:
            self.client.cancel.set()
            self.status_label.setText("已要求取消；等待目前 backend 操作結束…")
            return
        if not self.client.connected:
            self.reconnect_enabled = False
            self.reconnect_due = None
            self.status_label.setText("已停止自動重連；目前沒有 NETCONF session。")
            self._sync_controls()
            return
        if not self._preserve_current_draft(flush=True):
            return
        self.reconnect_enabled = False
        self.reconnect_due = None
        self._run("中斷連線…", lambda _progress: self.client.disconnect(), self._clear_session)

    def _clear_session(self, _value=None):
        self.notification_manager = None
        self.notification_timer.stop()
        self.uncertain = False
        self.admin_requires_refresh = False
        self.snapshot = None
        self.selection = None
        self.plan = None
        self.tree.clear()
        self.editor.clear()
        self.preview.clear()
        self.path_label.setText("未連線")
        self.preview_status.setText("尚無變更，不會送出任何設定")
        self.schema_status.setText("YANG：未載入")
        self.status_label.setText("已中斷連線")

    def read_all(self):
        if not self.client.connected or self.busy:
            return
        try:
            options = self._options()
        except Exception as exc:
            self.show_error(exc)
            return
        self._run("讀取 %s%s…" % (options.source, " + state" if options.state else ""),
                  lambda _progress: self.client.read(options), self._accept_snapshot)

    def refresh_schema(self):
        if not self.client.connected or self.busy:
            return
        directory = self._text("schema_dir")

        def done(schema):
            self.schema_status.setText("YANG：%d modules / %d nodes%s" % (
                schema.module_count, len(schema.nodes), "" if schema.complete else "（不完整，唯讀）"))
            if self.snapshot:
                self._accept_snapshot(self.snapshot)
            if schema.warnings:
                self.status_label.setText("YANG 載入完成但有提醒；" + " ".join(schema.warnings[:2]))

        self._run("取得並編譯 YANG schema…",
                  lambda progress: self.client.load_schema(directory, True, progress), done)

    # ---------- DATA TREE ----------

    def _label(self, selection):
        node, path = selection.node, selection.path
        info = self.client.schema.lookup(path)
        label = local(node.tag)
        if not selection.ancestors:
            label = self.client.schema.namespaces.get(etree.QName(node).namespace, label) + " : " + label
        if info and info.kind == "list":
            label += " [" + ", ".join(local(key) + "=" + (node.findtext(key) or "") for key in info.keys) + "]"
        elif not children(node) and node.text:
            secret = any(word in local(node.tag).lower() for word in ("password", "secret", "private-key"))
            label += " = " + ("••••••" if secret else node.text.replace("\n", " ")[:48])
        return label

    def _set_item_style(self, item, style):
        if style:
            item.setBackground(0, QBrush(QColor(COLORS[style])))

    def _make_item(self, parent, selection):
        item = QTreeWidgetItem(parent, [self._label(selection)])
        item.setData(0, USER_ROLE, selection)
        style = node_style(selection.node, selection.path, self.client.schema)
        self._set_item_style(item, style)
        if children(selection.node):
            dummy = QTreeWidgetItem(item, ["…"])
            dummy.setData(0, USER_ROLE, "dummy")
        return item

    def _candidate_item(self, parent, candidate):
        info = candidate.info
        branch = choice_label(self.client.schema, info)
        label = "%s : %s" % (info.module, local(info.path[-1]))
        if branch:
            label = "choice %s → %s : %s" % (branch, info.module, local(info.path[-1]))
        label += "（未讀到／可建立）" if not candidate.reason else "（%s）" % candidate.reason
        item = QTreeWidgetItem(parent, [tr(label)])
        item.setData(0, ITEM_SOURCE_ROLE, label)
        item.setData(0, USER_ROLE, candidate)
        item.setForeground(0, QBrush(QColor("#7b8798")))
        item.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Normal))
        if candidate.reason:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        else:
            # Keep the add marker in the native QTreeWidget indicator column,
            # aligned with real expandable nodes.  Creation is available from
            # the right-click menu; expanding its marker intentionally does nothing.
            dummy = QTreeWidgetItem(item, [""])
            dummy.setData(0, USER_ROLE, "candidate-dummy")
        return item

    def _populate_candidates(self, parent_item, parent_node, path):
        if not self.checks["show_candidates"].isChecked() or not self.client.schema.complete:
            return
        for candidate in candidates(self.client.schema, parent_node, path):
            if candidate.reason.startswith("已存在"):
                continue
            self._candidate_item(parent_item, candidate)

    def _item_expanded(self, item):
        if isinstance(item.data(0, USER_ROLE), Candidate):
            item.setExpanded(False)
            return
        if item.childCount() and item.child(0).data(0, USER_ROLE) == "dummy":
            dummy = item.takeChild(0)
            del dummy
            selection = item.data(0, USER_ROLE)
            for child in children(selection.node):
                self._make_item(item, Selection(child, selection.ancestors + (selection.node,)))
            self._populate_candidates(item, selection.node, selection.path)

    def _item_clicked(self, item, _column):
        value = item.data(0, USER_ROLE)
        if isinstance(value, Selection):
            self._show_selection(value, item)

    def _item_double_clicked(self, item, _column):
        """Keep native expand/collapse and leaf selection; never create nodes."""
        value = item.data(0, USER_ROLE)
        if isinstance(value, Selection):
            info = self.client.schema.lookup(value.path)
            if info and info.kind in {"leaf", "leaf-list"}:
                self._show_selection(value, item)

    def _activate_tree_selection(self, item):
        """Select a real tree item before running a context-menu edit action."""
        value = item.data(0, USER_ROLE) if item is not None else None
        if not isinstance(value, Selection):
            return False
        if self.selection is not value and not self._preserve_current_draft(flush=True):
            return False
        self.tree.setCurrentItem(item)
        if self.selection is not value:
            self._show_selection(value, item)
        return True

    def _create_tree_candidate(self, item):
        """Open creation with the candidate under the pointer preselected."""
        candidate = item.data(0, USER_ROLE) if item is not None else None
        if not isinstance(candidate, Candidate) or candidate.reason or not self.snapshot:
            return
        parent = item.parent()
        parent_value = parent.data(0, USER_ROLE) if parent else None
        if isinstance(parent_value, Selection):
            if not self._activate_tree_selection(parent):
                return
            try:
                parent_node = parse_editor(self.editor.toPlainText())
            except Exception as exc:
                self.show_error(exc)
                return
            self._open_creation(candidate, parent_node, parent_value.path)
        elif parent is None:
            self._open_creation(candidate, self.snapshot.data, ())

    def _build_tree_context_menu(self, item):
        """Build the contextual node-edit menu for one tree item."""
        value = item.data(0, USER_ROLE) if item is not None else None
        menu = QMenu(self.tree)

        if isinstance(value, Candidate) and not value.reason:
            create_candidate = menu.addAction("建立此候選節點…")
            create_candidate.triggered.connect(lambda _checked=False, target=item:
                                                self._create_tree_candidate(target))
            menu.addSeparator()

        if isinstance(value, Selection):
            info = self.client.schema.lookup(value.path)
            add_child = menu.addAction("新增子節點／list 項目…")
            add_child.setEnabled(bool(info and info.kind in {"container", "list"}
                                      and info.config is True and not value.delete))
            add_child.triggered.connect(
                lambda _checked=False, target=item:
                self.new_node() if self._activate_tree_selection(target) else None
            )

        add_root = menu.addAction("新增根 YANG 節點…")
        add_root.setEnabled(self.snapshot is not None)
        add_root.triggered.connect(self.new_root_node)

        if isinstance(value, Selection):
            menu.addSeparator()
            delete = menu.addAction("刪除整個節點…")
            info = self.client.schema.lookup(value.path)
            parent_info = self.client.schema.lookup(value.path[:-1])
            is_key = bool(parent_info and value.path[-1] in parent_info.keys)
            delete.setEnabled(bool(value.exists and not value.delete and info and info.config is True
                                   and not is_key and self.snapshot
                                   and self.snapshot.options.source != "startup"))
            delete.triggered.connect(
                lambda _checked=False, target=item:
                self.delete_selected() if self._activate_tree_selection(target) else None
            )

        return menu

    def _tree_context_menu(self, position):
        """Offer node edits at the pointer; double-click is navigation only."""
        if self.busy:
            return
        menu = self._build_tree_context_menu(self.tree.itemAt(position))
        if not menu.actions():
            return
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _rebuild_tree(self):
        if self.snapshot is None:
            self.tree_search_count.setText("")
            return
        self.tree.clear()
        query = self.tree_search.text().casefold().strip()
        if query:
            self._populate_search_tree(query)
            return
        self.tree_search_count.setText("")
        bookmark = self.selection.path if self.selection else None
        for node in children(self.snapshot.data):
            item = self._make_item(self.tree, Selection(node))
            if bookmark and len(bookmark) == 1 and node.tag == bookmark[0]:
                self.tree.setCurrentItem(item)
        if self.checks["show_candidates"].isChecked() and self.client.schema.complete:
            for candidate in candidates(self.client.schema, self.snapshot.data, ()):
                if candidate.reason.startswith("已存在"):
                    continue
                self._candidate_item(self.tree, candidate)
        if self.tree.topLevelItemCount() and self.tree.currentItem() is None:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _populate_search_tree(self, query):
        """Render complete-snapshot matches and their ancestors, including collapsed nodes."""
        found = search_snapshot(self.snapshot.data, self.client.schema, query, limit=500)
        included = {
            id(node)
            for _path, selection in found
            for node in (*selection.ancestors, selection.node)
        }
        selected_item = None

        def add(parent, node, ancestors):
            nonlocal selected_item
            if id(node) not in included:
                return
            selection = Selection(node, ancestors)
            item = self._make_item(parent, selection)
            # Search results are materialised immediately rather than leaving
            # lazy-loading dummies that cannot participate in filtering.
            while item.childCount():
                removed = item.takeChild(0)
                del removed
            for child in children(node):
                add(item, child, (*ancestors, node))
            if item.childCount():
                item.setExpanded(True)
            if self.selection is not None and node is self.selection.node:
                selected_item = item

        for node in children(self.snapshot.data):
            add(self.tree, node, ())

        if not found:
            empty_label = "找不到符合「%s」的節點" % self.tree_search.text().strip()
            empty = QTreeWidgetItem(self.tree, [tr(empty_label)])
            empty.setData(0, ITEM_SOURCE_ROLE, empty_label)
            empty.setData(0, USER_ROLE, "tree-search-empty")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            empty.setForeground(0, QBrush(QColor("#7b8798")))
        elif selected_item is not None:
            self.tree.setCurrentItem(selected_item)
        elif self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.tree_search_count.setText("500 筆上限" if len(found) >= 500 else "%d 筆" % len(found))

    def _accept_snapshot(self, snapshot):
        self.uncertain = False
        self.admin_requires_refresh = False
        self.snapshot = snapshot
        self.selection = None
        self.plan = None
        self._rebuild_tree()
        actual = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
                  if isinstance(self.tree.topLevelItem(i).data(0, USER_ROLE), Selection)]
        if actual:
            self.tree.setCurrentItem(actual[0])
            self._show_selection(actual[0].data(0, USER_ROLE), actual[0])
        else:
            self.editor.clear()
            self.path_label.setText("此查詢沒有可見資料（可能受 NACM 權限限制）")
        schema = self.client.schema
        self.schema_status.setText("YANG：%d modules / %d nodes%s" % (
            schema.module_count, len(schema.nodes), "" if schema.complete else "（不完整，唯讀）"))
        message = "已讀取 %s · %d 個根節點" % (snapshot.options.source, len(children(snapshot.data)))
        if snapshot.options.state:
            message += " + config false"
        if snapshot.warnings:
            message += " · " + " ".join(snapshot.warnings[:2])
        self.status_label.setText(message)

    def _show_selection(self, selection, item=None):
        self.selection = selection
        self.baseline_text = selection.text()
        self._set_editor(self.baseline_text)
        parts = []
        chain = (*selection.ancestors, selection.node)
        for node in chain:
            parts.append(local(node.tag))
        self.path_label.setText("/" + "/".join(parts))
        self.update_preview()

    def filter_tree(self, text):
        del text
        if self.snapshot is None:
            self.tree_search_count.setText("")
            return
        self._rebuild_tree()

    # ---------- XML editing ----------

    def _set_editor(self, text):
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)
        self.editor_highlighter._apply()
        self._annotate_editor()

    def _editor_changed(self):
        if not self.busy:
            self.editor_timer.start()
            try:
                self._capture_draft()
            except EditError as exc:
                self.status_label.setText("草稿尚未保存：" + str(exc))

    def _annotate_editor(self):
        selections = []
        if not self.selection:
            self.editor.setExtraSelections([])
            self.editor_highlighter._apply()
            return
        try:
            root = parse_editor(self.editor.toPlainText())
            paths = {}
            def walk(node, path):
                paths[node] = path
                for child in children(node):
                    walk(child, path + (child.tag,))
            walk(root, self.selection.path)
            for node, start, end, value_start, value_end in xml_spans(self.editor.toPlainText(), root):
                style = node_style(node, paths[node], self.client.schema, self.plan.changed if self.plan else ())
                if not style:
                    continue
                cursor = QTextCursor(self.editor.document())
                first, last = (start, end) if children(node) or value_start == value_end else (value_start, value_end)
                cursor.setPosition(first)
                cursor.setPosition(last, QTextCursor.MoveMode.KeepAnchor)
                extra = QTextEdit.ExtraSelection()
                extra.cursor = cursor
                extra.format.setBackground(QColor(COLORS[style]))
                selections.append(extra)
        except Exception:
            pass
        self.editor_highlighter._apply()
        current = self.editor.extraSelections()
        self.editor.setExtraSelections(current + selections)

    @property
    def dirty(self):
        return (self.selection is not None and
                (self.selection.delete or self.editor.toPlainText() != self.baseline_text))

    def update_preview(self):
        if not self.selection or not self.snapshot:
            return
        self.plan = None
        text = self.editor.toPlainText()
        try:
            target = self.snapshot.options.source
            if target == "startup" and self.dirty:
                raise EditError("startup 在此 GUI 中唯讀；請選 running/candidate。")
            self.plan = build_plan(self.selection, text, self.client.schema, target)
            if self.checks["rollback_on_error"].isChecked() and self.plan.rpc is not None:
                nc = "urn:ietf:params:xml:ns:netconf:base:1.0"
                operation = self.plan.rpc.find("{%s}edit-config" % nc)
                if operation is not None and operation.find("{%s}error-option" % nc) is None:
                    etree.SubElement(operation, "{%s}error-option" % nc).text = "rollback-on-error"
                    from ncclient.xml_ import to_xml
                    self.plan.wire_xml = to_xml(self.plan.rpc)
            self.preview.setPlainText(self.plan.wire_xml if self.plan.rpc is not None else "")
            if self.plan.rpc is None:
                self.preview_status.setText("尚無變更，不會送出任何設定")
            else:
                self.preview_status.setText("%d 項變更 / %d 項移除 → %s · 尚未送出；不會自動 commit 或保存 startup" % (
                    len(self.plan.changes), self.plan.removals, target))
            self._update_diff()
        except Exception as exc:
            self.plan = None
            self.preview.clear()
            self.preview_status.setText("無法送出：" + redact_secrets(str(exc)))
        self._annotate_editor()
        # Programmatic edits (create/delete/import/Pretty/revert) block the
        # editor's textChanged signal, so capture them here as well as from
        # _editor_changed.  This keeps the encrypted draft shelf consistent.
        try:
            self._capture_draft()
        except EditError as exc:
            self.draft_status = "草稿尚未保存：" + str(exc)
            self.status_label.setText(self.draft_status)
        self._sync_controls()

    def pretty_editor(self):
        try:
            root = parse_editor(self.editor.toPlainText())
            self._set_editor(serialize_xml(root).decode("utf-8"))
            self.update_preview()
        except Exception as exc:
            self.show_error(exc)

    def revert(self):
        if self.selection:
            if self.selection.delete:
                self.selection = Selection(self.selection.node, self.selection.ancestors,
                                           self.selection.exists, delete=False)
            self._set_editor(self.baseline_text)
            self.update_preview()

    def export_editor(self):
        if not self.selection:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "匯出 XML", "selection.xml", "XML (*.xml)")
        if not filename:
            return
        try:
            parse_editor(self.editor.toPlainText())
            Path(filename).write_text(self.editor.toPlainText(), encoding="utf-8", newline="\n")
            self.status_label.setText("已匯出 UTF-8 XML：" + filename)
        except Exception as exc:
            self.show_error(exc)

    def export_tree(self):
        if not self.snapshot:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "匯出 DATA TREE", "netconf-running-tree.xml", "XML (*.xml)")
        if filename:
            Path(filename).write_bytes(serialize_xml(self.snapshot.data))
            self.status_label.setText("已匯出目前 DATA TREE 快照：" + filename)

    def refresh_selected(self):
        if not self.client.connected or not self.selection or not self.snapshot or self.busy:
            return
        root_tag = self.selection.path[0]
        options = self.snapshot.options
        self._run("重新讀取所選根節點…", lambda _progress: self.client.read(options, root_tag), self._replace_root)

    def _replace_root(self, part):
        if not self.snapshot:
            return
        data = deepcopy(self.snapshot.data)
        root_tag = self.selection.path[0] if self.selection else None
        if root_tag:
            for node in list(data):
                if node.tag == root_tag:
                    data.remove(node)
            data.extend(deepcopy(node) for node in children(part.data))
        self._accept_snapshot(Snapshot(data, part.options, part.defaults_mode, part.warnings))

    def _update_diff(self):
        if not self.selection or not self.plan:
            self.diff.clear()
            return
        lines = ["Target: " + self.snapshot.options.source, "/" + "/".join(local(x) for x in self.selection.path), ""]
        lines += self.plan.changes
        self.diff.setPlainText("\n".join(lines) + "\n\n" + self.plan.wire_xml)

    # ---------- node creation/deletion ----------

    def new_root_node(self):
        if not self.snapshot:
            self.status_label.setText("請先讀取 running/candidate 與完整 YANG schema。")
            return
        try:
            dialog = CreationDialog(self.client.schema, self.snapshot.data, (), self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            node = dialog.result_node
            virtual = new_root_selection(node, self.client.schema)
            self.selection = virtual
            self.baseline_text = virtual.text()
            self._set_editor(serialize_xml(node).decode("utf-8"))
            self.path_label.setText("/" + local(node.tag) + "（新增根節點）")
            self.update_preview()
            self.status_label.setText("已建立新增根節點草稿；請檢查 RPC 後送出。")
        except Exception as exc:
            self.show_error(exc)

    def new_node(self):
        if not self.selection or not self.snapshot:
            self.status_label.setText("請先選取要加入子節點的 container/list。")
            return
        info = self.client.schema.lookup(self.selection.path)
        if not info or info.kind not in {"container", "list"}:
            self.status_label.setText("請選取 container/list；choice/case 會在候選清單中顯示實際分支。")
            return
        try:
            parent = parse_editor(self.editor.toPlainText())
            if parent.tag != self.selection.node.tag:
                raise EditError("請保留選取節點的名稱與 namespace。")
        except Exception as exc:
            self.show_error(exc)
            return
        self._open_creation(None, parent, self.selection.path)

    def _open_creation(self, selected_candidate, parent_node, parent_path):
        dialog = CreationDialog(self.client.schema, parent_node, parent_path, self)
        if selected_candidate:
            for index in range(dialog.list.count()):
                candidate = dialog.list.item(index).data(USER_ROLE)
                if candidate is not None and candidate.info.path == selected_candidate.info.path:
                    dialog.list.setCurrentRow(index)
                    break
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not parent_path:
            try:
                node = dialog.result_node
                virtual = new_root_selection(node, self.client.schema)
                self.selection = virtual
                self.baseline_text = virtual.text()
                self._set_editor(serialize_xml(node).decode("utf-8"))
                self.path_label.setText("/" + local(node.tag) + "（新增根節點）")
                self.update_preview()
                self.status_label.setText("已建立新增根節點草稿；請檢查 RPC 後送出。")
            except Exception as exc:
                self.show_error(exc)
            return
        if not self.selection:
            return
        try:
            edited = parse_editor(self.editor.toPlainText())
            node = dialog.result_node
            wanted = identity(node, self.client.schema, parent_path + (node.tag,))
            if any(child.tag == node.tag and identity(child, self.client.schema, parent_path + (child.tag,)) == wanted
                   for child in children(edited)):
                raise EditError("此節點／list key／leaf-list 值已存在。")
            edited.append(node)
            self._set_editor(serialize_xml(edited).decode("utf-8"))
            self.update_preview()
            self.status_label.setText("已加入 XML 草稿；choice 分支與完整語意會由 server 再驗證。")
        except Exception as exc:
            self.show_error(exc)

    def _find_item(self, wanted):
        result = None

        def same_selection(value):
            if not isinstance(value, Selection) or value.path != wanted.path:
                return False
            if value.node is wanted.node:
                return True
            try:
                return identity(value.node, self.client.schema, value.path) == identity(
                    wanted.node, self.client.schema, wanted.path
                )
            except Exception:
                return False

        def visit(item):
            nonlocal result
            value = item.data(0, USER_ROLE)
            if same_selection(value):
                result = item
                return
            for index in range(item.childCount()):
                visit(item.child(index))
        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))
        return result

    def delete_selected(self):
        selection = self.selection
        if not selection or not self.snapshot:
            return
        if not selection.exists:
            self.status_label.setText("尚未存在於設備的新增節點不能刪除；請取消草稿或修改內容。")
            return
        if self.snapshot.options.source == "startup":
            self.status_label.setText("startup 唯讀；請切換 running/candidate。")
            return
        info = self.client.schema.lookup(selection.path)
        if not info or info.config is not True:
            self.status_label.setText("config false／未知 schema 節點不能刪除。")
            return
        parent_info = self.client.schema.lookup(selection.path[:-1])
        if parent_info and selection.path[-1] in parent_info.keys:
            self.status_label.setText("list key 是識別欄位，不能單獨刪除；請刪除整個 list 項目。")
            return
        answer = QMessageBox.question(
            self, "確認刪除整個節點", "將在%s XML 草稿中移除：\n/%s\n\n只建立 edit-config remove，不會立即修改設備。" %
            ("目前選取範圍" if not selection.ancestors else "父層",
            "/".join(local(x) for x in selection.path),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            # Root data nodes have no visible <data> parent in the editor.  A
            # delete-marked selection lets build_plan emit one atomic remove
            # operation for the entire presence container/list/root leaf.
            if not selection.ancestors:
                self.selection = Selection(selection.node, (), selection.exists, delete=True)
                self.baseline_text = selection.text()
                self.update_preview()
                self.status_label.setText("已標記刪除整個根節點；尚未送出。")
                return
            parent_node = selection.ancestors[-1]
            parent_selection = Selection(parent_node, selection.ancestors[:-1])
            self.selection = parent_selection
            self.baseline_text = parent_selection.text()
            edited = parse_editor(self.baseline_text)
            wanted = identity(selection.node, self.client.schema, selection.path)
            matches = [child for child in children(edited) if child.tag == selection.node.tag and
                       identity(child, self.client.schema, selection.path) == wanted]
            if len(matches) != 1:
                raise EditError("父層 XML 找不到唯一的選取節點；請重新讀取後再試。")
            edited.remove(matches[0])
            self._set_editor(serialize_xml(edited).decode("utf-8"))
            self.path_label.setText("/" + "/".join(local(x) for x in parent_selection.path))
            self.update_preview()
            self.status_label.setText("已標記刪除整個 %s 節點；尚未送出。" % local(selection.node.tag))
        except Exception as exc:
            self.show_error(exc)

    # ---------- NETCONF/sysrepo operations ----------

    def send(self):
        self.update_preview()
        if (not self.client.connected or not self.plan or self.plan.rpc is None or self.uncertain or self.busy
                or not self._rpc_allowed() or self._pending()):
            return
        guard = self._draft_guard()
        if guard:
            self.status_label.setText(guard)
            return
        if self.snapshot.options.source == "startup":
            self.status_label.setText("startup 唯讀，不能送出修改。")
            return
        question = "送出 %d 項變更（含 %d 項移除）到 %s？\n\n不會自動 commit candidate，也不會保存 startup。" % (
            len(self.plan.changes), self.plan.removals, self.snapshot.options.source)
        answer = QMessageBox.question(self, "確認送出 NETCONF 修改", question,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        plan, selection, options = self.plan, self.selection, self.snapshot.options
        self.last_sent_xml = plan.wire_xml
        self._begin_attempt(selection, plan, options, "NETCONF edit-config")

        def done(result):
            self.reply.setPlainText(result.reply)
            self.output_tabs.setCurrentWidget(self.reply)
            if result.snapshot is not None:
                self._accept_snapshot(result.snapshot)
                self._finish_attempt(result.snapshot)
            else:
                self.uncertain = True
                self._finish_attempt(error=EditError("未取得 NETCONF 讀回"))
            self._capture_draft()
            self.status_label.setText("edit-config 成功；" + " ".join(result.warnings or []))
            self._audit_result("edit-config", "完成" if not result.warnings else "完成；結果待確認")
            self._sync_controls()

        def failed(exc):
            # xrpc/lock/transport exceptions do not prove that no write was
            # accepted by the peer.  Keep the send button disabled until a
            # fresh read establishes the new baseline.
            self.uncertain = True
            self._finish_attempt(error=exc)
            self._audit_result("edit-config", "失敗／結果待確認：" + type(exc).__name__)
            self.show_error(exc)
            self._sync_controls()

        self._run("送出 NETCONF 修改中…", lambda _progress: self.client.apply(selection, plan, options), done, failed)

    def _admin_settings(self):
        host, username = self._text("admin_host"), self._text("admin_username")
        port = int(self._text("admin_port"))
        if not host or not username or not 1 <= port <= 65535:
            raise EditError("請填系統 SSH host、帳號及 1–65535 的 port。")
        auth = self.admin_auth.currentText()
        key = self._text("admin_key") if auth in {"auto", "private-key"} else ""
        if auth == "private-key" and not key:
            raise EditError("請填系統 SSH 私鑰路徑。")
        for filename in (key, self._text("admin_known_hosts")):
            if filename and not Path(filename).expanduser().is_file():
                raise EditError("File not found: " + filename)
        settings = ConnectionSettings(host=host, port=port, username=username,
                                      password=self._text("admin_password") or None, key=key or None,
                                      ssh_auth=auth, key_passphrase=self._text("admin_passphrase") or None,
                                      known_hosts=self._text("admin_known_hosts") or None,
                                      hostkey_verify=self.checks["admin_verify"].isChecked(),
                                      allow_agent=auth in {"auto", "agent"}, look_for_keys=False,
                                      timeout=float(self._text("admin_timeout") or "15"))
        if self.checks["admin_jump"].isChecked():
            main = self._settings()
            if not main.jump_enabled:
                raise EditError("請先在 SSH 跳板分頁啟用跳板。")
            settings = settings.copy(jump_enabled=True, jump_host=main.jump_host, jump_port=main.jump_port,
                                     jump_username=main.jump_username, jump_password=main.jump_password,
                                     jump_key=main.jump_key, jump_passphrase=main.jump_passphrase,
                                     jump_auth=main.jump_auth, jump_verify=main.jump_verify,
                                     jump_known_hosts=main.jump_known_hosts)
        return settings

    def connect_admin(self):
        if self.busy or (self.admin_connection and self.admin_connection.connected):
            return
        try:
            settings = self._admin_settings()
            from .sysrepo import ShellConnection
        except Exception as exc:
            self.show_error(exc)
            return
        def work(_progress):
            shell = ShellConnection(settings)
            shell.connect()
            return shell
        def done(shell):
            self.admin_connection = shell
            self.admin_settings = settings
            self.admin_status.setText("系統 SSH：%s@%s:%s（非 NETCONF）" % (settings.username, settings.host, settings.port))
        self._run("連線系統 SSH…", work, done)

    def disconnect_admin(self):
        if self.busy or not self.admin_connection:
            return
        shell = self.admin_connection
        self._run("中斷系統 SSH…", lambda _progress: shell.close(), lambda _value: self._clear_admin())

    def _clear_admin(self):
        self.admin_connection = None
        self.admin_settings = None
        self.admin_status.setText("系統 SSH 已中斷；NETCONF 連線不受影響。")

    def sysrepo_modify(self):
        if not self.admin_connection or not self.admin_connection.connected:
            self.status_label.setText("請先在「系統SSH」分頁連線。")
            return
        if not self.selection or not self.snapshot or self.snapshot.options.source != "running":
            self.status_label.setText("系統 SSH 修改只支援已讀取 running 的選取草稿。")
            return
        if self.admin_requires_refresh:
            self.status_label.setText("上次系統 SSH 修改後需先重新讀取 NETCONF；暫停再次修改。")
            return
        self.update_preview()
        try:
            from .sysrepo import prepare, execute
            prepared = prepare(self.plan, self.client.schema, self._text("admin_program"),
                               int(self._text("admin_timeout") or "10"), self.snapshot.options.defaults)
        except Exception as exc:
            self.show_error(exc)
            return
        question = "使用已連線的系統 SSH 執行：\n%s\n\nXML 會透過 stdin 傳給 sysrepocfg；不會自動保存 startup。\n\n確定執行？" % prepared.command
        answer = QMessageBox.question(self, "確認系統 SSH／sysrepocfg 修改", question,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        shell, selection, plan, schema = self.admin_connection, self.selection, self.plan, self.client.schema
        timeout = int(self._text("admin_timeout") or "10")
        self.last_sent_xml = prepared.payload.decode("utf-8")
        self.admin_requires_refresh = True
        self.uncertain = True
        def done(result):
            text, warnings = result
            self.reply.setPlainText(text + "\n" + "\n".join(warnings))
            self.output_tabs.setCurrentWidget(self.reply)
            self.uncertain = True
            self.status_label.setText("系統 SSH 修改完成；請重新讀取 NETCONF 確認。" + " ".join(warnings))
            self._audit_result("sysrepocfg", "完成；結果待確認")
            self._sync_controls()
        self._run("系統 SSH／sysrepocfg 修改中…",
                  lambda _progress: execute(shell, prepared, selection, plan, schema, timeout,
                                             self.snapshot.options.defaults), done)

    # ---------- shared workspace helpers ----------

    def _wrap_changed(self, *_args):
        mode = (QPlainTextEdit.LineWrapMode.WidgetWidth if self.wrap_xml.isChecked()
                else QPlainTextEdit.LineWrapMode.NoWrap)
        for widget in (self.editor, self.preview, self.reply, self.diff, self.audit_pane,
                       self.notification_pane):
            if widget is not None:
                widget.setLineWrapMode(mode)
        self._schedule_preferences()

    def _audit_device(self):
        context = getattr(self.client, "context", None)
        metadata = getattr(context, "metadata", None)
        if metadata:
            peer = metadata.peer_address or "%s:%s" % (metadata.remote_host, metadata.remote_port)
            return "%s %s %s" % (metadata.mode, metadata.transport, peer)
        settings = self.reconnect_settings
        if settings:
            return "%s %s:%s" % (settings.transport,
                                   settings.listen_host if settings.call_home else settings.host,
                                   settings.listen_port if settings.call_home else settings.port)
        return "DEMO" if self.demo else "Offline"

    def _audit_result(self, operation, result, device=None):
        self.audit.add(device or self._audit_device(), operation, result)
        if hasattr(self, "audit_pane"):
            text = self.audit.text()
            if self.audit.error:
                text += "\n" + self.audit.error
            self.audit_pane.setPlainText(text)

    def _text_window(self, title, text, *, width=950, height=600):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(width, height)
        layout = QVBoxLayout(dialog)
        pane = self._output_edit()
        pane.setPlainText(str(text))
        layout.addWidget(pane, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()
        return dialog

    def _pending(self):
        pending = getattr(self.client, "pending_commit", None)
        return pending if isinstance(pending, safety.PendingCommit) else None

    def _rpc_allowed(self):
        if self.notification_manager is None:
            return True
        try:
            return bool(self.client.connected and self.client.manager is self.notification_manager
                        and any(":interleave:" in str(c) for c in self.client.capabilities))
        except Exception:
            return False

    def _draft_scope(self):
        context = getattr(self.client, "context", None)
        if self.demo:
            return "DEMO"
        if context is None:
            return "OFFLINE"
        settings = context.settings
        return json.dumps({
            "endpoint": context._namespace_server_key(),
            "username": settings.username,
            "tls_name": settings.tls_server_name,
            "cert": settings.cert,
            "ssh_key": settings.key,
            "ssh_auth": settings.ssh_auth,
            "jump": [settings.jump_enabled, settings.jump_host, settings.jump_port,
                     settings.jump_username],
        }, sort_keys=True, ensure_ascii=False)

    def _draft_session(self):
        return (self.client if self.demo else self.client.manager) if self.client.connected else None

    def _active_draft(self):
        if self.selection is None or self.snapshot is None or not self.drafts.entries:
            return None
        try:
            return self.drafts.matching(self._draft_scope(), self.snapshot.options.source,
                                        self.selection, self.client.schema)
        except (EditError, AttributeError):
            return None

    def _capture_draft(self):
        if not self.selection or not self.snapshot or not self.client.schema.complete:
            return
        entry = self._active_draft()
        if not self.dirty:
            if entry:
                self.drafts.entries.pop(entry.key, None)
                self._schedule_drafts()
            return
        if self.draft_fingerprint_cache is None or self.draft_fingerprint_cache[0] is not self.client.schema:
            self.draft_fingerprint_cache = (self.client.schema, schema_fingerprint(self.client.schema))
        old = entry.text if entry else None
        self.drafts.capture(self._draft_scope(), self.snapshot.options.source, self.selection,
                            self.editor.toPlainText(), self.client.schema, self._draft_session(),
                            self.draft_fingerprint_cache[1])
        if old != self.editor.toPlainText():
            self._schedule_drafts()
        self.draft_button.setText("草稿清單（%d）" % len(self.drafts.entries))

    def _schedule_drafts(self):
        if self.draft_timer:
            self.draft_timer.start()

    def _save_drafts(self):
        try:
            if self.drafts.load_error and not self.drafts.entries:
                self.draft_status = self.drafts.load_error
                return False
            if self.drafts.entries or (self.drafts.path and self.drafts.path.exists()):
                self.drafts.save()
            self.draft_status = ("草稿已加密保存（僅原 Windows 帳號／電腦）" if self.drafts.path
                                 else "草稿僅保存在記憶體（示範／測試模式）")
            return True
        except Exception as exc:
            self.draft_status = self.drafts.load_error or "草稿保存失敗：" + type(exc).__name__
            self.status_label.setText(self.draft_status)
            return False

    def _preserve_current_draft(self, flush=False):
        try:
            self._capture_draft()
            if flush and not self._save_drafts():
                raise EditError(self.draft_status)
            return True
        except Exception as exc:
            self.show_error(exc)
            return False

    def _forget_active_draft(self):
        entry = self._active_draft()
        if entry:
            self.drafts.entries.pop(entry.key, None)
            self._schedule_drafts()

    def _related_drafts(self):
        if not self.drafts.entries:
            return False
        scope = self._draft_scope()
        return any(entry.scope == scope for entry in self.drafts.entries.values())

    def _draft_guard(self):
        if not self.drafts.entries or not self.selection or not self.snapshot:
            return ""
        entry = self._active_draft()
        if entry and self.client.schema.complete:
            current_hash = schema_fingerprint(self.client.schema)
            if current_hash != entry.schema_hash:
                return "草稿 schema 與目前設備不同，請核對後重建草稿"
            if entry.session is not self._draft_session() or entry.session is None:
                return "草稿尚未比對此 session；請從草稿清單按『重新比對／載入』"
        try:
            overlap = self.drafts.overlapping(self._draft_scope(), self.snapshot.options.source,
                                              self.selection, self.client.schema)
            if overlap:
                return "此範圍與既有草稿重疊；請先從草稿清單開啟：" + overlap.label
        except EditError as exc:
            return str(exc)
        return ""

    def _sync_drafts(self):
        if hasattr(self, "draft_button"):
            self.draft_button.setText("草稿清單（%d）" % len(self.drafts.entries))
            self.draft_button.setEnabled(not self.busy and self.lifecycle_dialog is None)

    def show_drafts(self):
        if self.busy or self.lifecycle_dialog or not self._preserve_current_draft(flush=True):
            return
        dialog = QDialog(self)
        self.lifecycle_dialog = dialog
        dialog.setWindowTitle("草稿清單 — 只載入本機，不會批次送出")
        dialog.resize(1060, 680)
        layout = QVBoxLayout(dialog)
        intro = QLabel("依設備／帳號、source、節點路徑隔離；父子範圍重疊時請回原草稿修改。\n"
                       "重新開啟後須先連線，再重新比對。每份草稿仍須個別預覽、確認送出；不會自動重播。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        tree = QTreeWidget()
        tree.setHeaderLabels(["節點路徑", "Source", "狀態"])
        tree.setColumnWidth(0, 620)
        layout.addWidget(tree, 0)
        detail = QLabel()
        detail.setWordWrap(True)
        layout.addWidget(detail)
        pane = self._output_edit()
        layout.addWidget(pane, 1)
        status = QLabel(self.draft_status or self.drafts.load_error)
        status.setWordWrap(True)
        layout.addWidget(status)
        controls = QHBoxLayout()
        restore = QPushButton("重新比對／載入")
        remove = QPushButton("刪除草稿")
        save = QPushButton("加密保存")
        close = QPushButton("關閉")
        controls.addWidget(restore)
        controls.addWidget(remove)
        controls.addWidget(save)
        controls.addStretch(1)
        controls.addWidget(close)
        layout.addLayout(controls)
        entries = {}
        for entry in self.drafts.entries.values():
            item = QTreeWidgetItem(tree, [entry.label, entry.source, entry.status])
            item.setData(0, USER_ROLE, entry.key)
            entries[entry.key] = item

        def selected():
            item = tree.currentItem()
            return self.drafts.entries.get(item.data(0, USER_ROLE)) if item else None

        def preview(_item=None, _column=0):
            entry = selected()
            if entry:
                detail.setText("設備：%s\n%s · XML 可能含敏感值，分享前請遮蔽" % (entry.scope, entry.updated))
                pane.setPlainText(entry.text)
            else:
                detail.setText("請選取一份草稿。")
                pane.clear()

        def close_dialog():
            self.lifecycle_dialog = None
            dialog.close()
            self._sync_controls()

        def remove_entry():
            entry = selected()
            if entry is None:
                return
            if QMessageBox.question(dialog, "刪除草稿", "移除此本機草稿？不會修改設備。\n" + entry.label,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            active = self._active_draft()
            self.drafts.entries.pop(entry.key, None)
            if not self._save_drafts():
                self.drafts.entries[entry.key] = entry
                status.setText(self.draft_status)
                return
            item = entries.pop(entry.key, None)
            if item:
                tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
            pane.clear()
            if active is entry:
                self.selection = self.plan = None
                self._set_editor("")
                self.preview.clear()
            self._sync_controls()

        def restore_entry():
            entry = selected()
            if entry is None or not self.client.connected or not self.snapshot or self._pending():
                detail.setText("請先連線並讀取目標 datastore；限時提交進行中不能載入。")
                return
            if entry.scope != self._draft_scope() or entry.source != self.snapshot.options.source:
                detail.setText("目前設備／帳號／連線路徑或 source 與此草稿不同。")
                return
            if QMessageBox.question(dialog, "比對並載入草稿", "重新讀取 config 並比對原始值；不會送出修改。\n確定？",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            close_dialog()
            session = self._draft_session()
            options = self.snapshot.options
            def work(_progress):
                fresh = self.client.read(ReadOptions(entry.source, options.defaults, False), entry.selection.path[0])
                self.drafts.verify(entry, self._draft_scope(), entry.source, self.client.schema,
                                   fresh.data, session)
                return fresh
            def done(_fresh):
                if session is not self._draft_session():
                    entry.session = None
                    raise EditError("連線已變更，請重新比對。")
                self.selection = entry.selection
                self.baseline_text = entry.selection.text()
                self._set_editor(entry.text)
                self.uncertain = False
                self.update_preview()
                self.status_label.setText("草稿原值已重新比對；請檢查 XML 後再自行送出。")
            self._run("重新比對草稿…", work, done)

        tree.currentItemChanged.connect(preview)
        restore.clicked.connect(restore_entry)
        remove.clicked.connect(remove_entry)
        save.clicked.connect(lambda: (self._save_drafts(), status.setText(self.draft_status)))
        close.clicked.connect(close_dialog)
        dialog.finished.connect(lambda _code: setattr(self, "lifecycle_dialog", None))
        preview()
        dialog.show()

    def open_leaf_editor(self):
        if (not self.selection or not self.snapshot or self.busy or self.lifecycle_dialog
                or self._pending() or not self.client.schema.complete):
            return
        try:
            edited = parse_editor(self.editor.toPlainText())
            if edited.tag != self.selection.node.tag:
                raise EditError("請保留選取根節點名稱與 namespace。")
        except Exception as exc:
            self.show_error(exc)
            return
        dialog = QDialog(self)
        self.lifecycle_dialog = dialog
        original_text = self.editor.toPlainText()
        selection = self.selection
        snapshot = self.snapshot
        schema = self.client.schema
        dialog.setWindowTitle("表單編輯 leaf — 只更新 XML 草稿")
        dialog.resize(1060, 700)
        layout = QVBoxLayout(dialog)
        hint = QLabel("選取欄位填值；config false、既有 list key 與選取根 leaf-list 的識別值不可直接修改。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        tree = QTreeWidget()
        tree.setHeaderLabels(["節點", "型別／狀態", "值"])
        tree.setColumnWidth(0, 500)
        tree.setColumnWidth(1, 220)
        layout.addWidget(tree, 1)
        info_label = QLabel()
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        value_row = QHBoxLayout()
        value_row.addWidget(QLabel("值"))
        value = QLineEdit()
        value_row.addWidget(value, 1)
        apply_button = QPushButton("套用欄位值")
        value_row.addWidget(apply_button)
        reference_button = QPushButton("查看引用目標…")
        value_row.addWidget(reference_button)
        layout.addLayout(value_row)
        suggestion_row = QHBoxLayout()
        suggestion_row.addWidget(QLabel("建議值（可直接修改）"))
        suggestions = QComboBox()
        suggestion_row.addWidget(suggestions, 1)
        suggestion_button = QPushButton("帶入建議值")
        suggestion_row.addWidget(suggestion_button)
        layout.addLayout(suggestion_row)
        layout.addWidget(QLabel("XML 預覽（只更新本機草稿，不會送出）"))
        preview = self._output_edit()
        layout.addWidget(preview, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setProperty("_ncc_source_text", "更新 XML 草稿")
        ok_button.setText(tr("更新 XML 草稿"))
        layout.addWidget(buttons)
        fields = []
        reference = None

        def is_secret(info):
            return bool(info and any(word in local(info.path[-1]).lower()
                                     for word in ("password", "passphrase", "secret", "private-key")))

        def is_readonly(info, path, indices):
            if info is None or info.config is not True or snapshot.options.source == "startup":
                return True
            if info.kind == "leaf-list" and not indices:
                return True
            for depth in range(1, len(path)):
                parent_info = schema.lookup(path[:depth])
                if parent_info and parent_info.kind == "list" and path[depth] in parent_info.keys:
                    return True
            return False

        def walk(node, path, indices=(), parent_item=None):
            info = schema.lookup(path)
            if info and info.kind in {"leaf", "leaf-list"}:
                readonly = is_readonly(info, path, indices)
                display = node.text or ""
                item = QTreeWidgetItem(parent_item or tree, [local(node.tag), info.type_name or info.kind,
                                                              display])
                item.setData(0, USER_ROLE, (node, info, path, indices, readonly))
                if readonly:
                    item.setForeground(0, QBrush(QColor("#64748b")))
                    item.setForeground(1, QBrush(QColor("#64748b")))
                fields.append(item)
                return
            container = QTreeWidgetItem(parent_item or tree, [local(node.tag), info.kind if info else "container", ""])
            for index, child in enumerate(children(node)):
                walk(child, path + (child.tag,), indices + (index,), container)
        walk(edited, selection.path)

        def selected_field():
            item = tree.currentItem()
            data = item.data(0, USER_ROLE) if item else None
            return item, data

        def select_field(item, _column=0):
            if item is None:
                return
            data = item.data(0, USER_ROLE)
            if not data:
                return
            node, node_info, path, indices, readonly = data
            nonlocal reference
            value.setText(node.text or "")
            value.setEchoMode(QLineEdit.EchoMode.Normal)
            suggestions.clear()
            try:
                reference = resolve_leafref(schema, snapshot.data, selection, edited, indices)
            except Exception as exc:
                reference = None
                info_label.setText(redact_secrets(str(exc)))
            reference_values = reference.values if reference is not None else ()
            values = () if is_secret(node_info) else suggested_values(schema, node_info, reference_values)
            suggestions.addItems(list(values))
            value.setReadOnly(readonly)
            apply_button.setEnabled(not readonly)
            suggestion_button.setEnabled(not readonly and suggestions.count() > 0)
            reference_button.setEnabled(bool(reference and reference.paths))
            details = "%s · %s · %s\n%s" % (node_info.module, node_info.kind,
                                               node_info.type_name,
                                               (node_info.constraints or node_info.description or "")[:500])
            if reference and reference.messages:
                details += "\n" + "\n".join(dict.fromkeys(reference.messages))
            info_label.setText(details)

        def apply_field():
            nonlocal edited
            item, data = selected_field()
            if not data:
                return True
            node, node_info, path, indices, readonly = data
            if readonly or value.isReadOnly():
                return True
            try:
                candidate = scalar_input(schema, node_info, node, value.text())
                parent = node.getparent()
                if parent is None:
                    edited = candidate
                else:
                    parent.replace(node, candidate)
                item.setText(2, "••••••" if is_secret(node_info) and candidate.text else candidate.text or "")
                item.setData(0, USER_ROLE, (candidate, node_info, path, indices, readonly))
                preview.setPlainText(serialize_xml(edited).decode("utf-8"))
                select_field(item)
                return True
            except Exception as exc:
                info_label.setText(redact_secrets(str(exc)))
                return False

        def use_suggestion():
            if suggestions.currentText():
                value.setText(suggestions.currentText())
                apply_field()

        def show_references():
            if not reference or not reference.paths:
                return
            reference_dialog = QDialog(dialog)
            reference_dialog.setWindowTitle("leafref 引用目標（可見資料）")
            reference_dialog.resize(900, 480)
            reference_layout = QVBoxLayout(reference_dialog)
            reference_layout.addWidget(QLabel("Path：" + " | ".join(reference.paths)))
            reference_list = QListWidget()
            reference_layout.addWidget(reference_list, 1)
            for target in reference.targets:
                reference_list.addItem(instance_path(target, schema))
            reference_status = QLabel("候選僅代表目前可見快照；未列出不等於設備不存在。")
            reference_status.setWordWrap(True)
            reference_layout.addWidget(reference_status)
            reference_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            jump_button = reference_buttons.addButton("保留草稿並跳到目標", QDialogButtonBox.ButtonRole.ActionRole)
            reference_layout.addWidget(reference_buttons)

            def jump():
                if not reference_list.currentRow() in range(len(reference.targets)):
                    reference_status.setText("請先選取一個可見的引用目標。")
                    return
                if not apply_field():
                    return
                try:
                    text = serialize_xml(edited).decode("utf-8")
                    build_plan(selection, text, schema, snapshot.options.source)
                    target = reference.targets[reference_list.currentRow()]
                    if self.selection is not selection or self.snapshot is not snapshot or self.editor.toPlainText() != original_text:
                        raise EditError("原始 XML 草稿已改變，請關閉表單後重新開啟。")
                    self._set_editor(text)
                    self.update_preview()
                    dialog.accept()
                    item = self._find_item(target)
                    if item is not None:
                        self.tree.setCurrentItem(item)
                        self.tree.scrollToItem(item)
                    else:
                        self.status_label.setText("已保存原草稿，但引用目標目前不在 DATA TREE 可見範圍。")
                    reference_dialog.accept()
                except Exception as exc:
                    reference_status.setText(redact_secrets(str(exc)))

            jump_button.clicked.connect(jump)
            reference_buttons.rejected.connect(reference_dialog.reject)
            reference_dialog.exec()

        def accept_form():
            if not apply_field():
                return
            try:
                if self.selection is not selection or self.snapshot is not snapshot or self.editor.toPlainText() != original_text:
                    raise EditError("原始 XML 草稿已改變，請關閉表單後重新開啟。")
                text = serialize_xml(edited).decode("utf-8")
                # Existing subtrees may contain config false children.  The
                # normal edit planner ignores unchanged state nodes and is
                # the correct validator for this form; validate_subtree is
                # intentionally reserved for creating new subtrees.
                build_plan(selection, text, schema, snapshot.options.source)
            except Exception as exc:
                info_label.setText(redact_secrets(str(exc)))
                return
            self._set_editor(text)
            self.update_preview()
            self.status_label.setText("表單已更新本機草稿；尚未送出。")
            dialog.accept()

        tree.itemClicked.connect(select_field)
        apply_button.clicked.connect(apply_field)
        reference_button.clicked.connect(show_references)
        value.returnPressed.connect(apply_field)
        suggestion_button.clicked.connect(use_suggestion)
        buttons.accepted.connect(accept_form)
        buttons.rejected.connect(dialog.reject)
        dialog.finished.connect(lambda _code: setattr(self, "lifecycle_dialog", None))
        if fields:
            tree.setCurrentItem(fields[0])
            select_field(fields[0])
        preview.setPlainText(serialize_xml(edited).decode("utf-8"))
        dialog.show()

    def import_xml(self):
        if self.busy or not self.selection or not self.snapshot:
            self.status_label.setText("請先讀取並選擇要匯入的節點。")
            return
        if self.snapshot.options.source == "startup" or self.uncertain:
            self.show_error(EditError("請先重新讀取 running/candidate，再匯入。"))
            return
        if not self._preserve_current_draft(flush=True):
            return
        filename, _ = QFileDialog.getOpenFileName(self, "匯入 XML（只更新選取節點的本機草稿）", "", "XML (*.xml)")
        if not filename:
            return
        try:
            if Path(filename).stat().st_size > 32 * 1024 * 1024:
                raise EditError("XML 匯入上限為 32 MiB。")
            imported = import_selection(Path(filename).read_bytes(), self.selection, self.client.schema)
            text = serialize_xml(imported).decode("utf-8")
            plan = build_plan(self.selection, text, self.client.schema, self.snapshot.options.source)
            if QMessageBox.question(self, "確認載入 XML 草稿",
                                    "只處理目前選取的 subtree；檔案中其他根節點不會套用。\n"
                                    "%d 項變更（含 %d 項移除）。只載入草稿，不會送出。\n是否繼續？" %
                                    (len(plan.changes), plan.removals),
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self._set_editor(text)
            self.update_preview()
            self.output_tabs.setCurrentWidget(self.diff)
            self.status_label.setText("XML 已載入本機草稿；請檢查差異及 RPC，再按送出。")
        except Exception as exc:
            self.show_error(exc)

    def search_xml(self):
        query, accepted = QInputDialog.getText(self, "搜尋 XML", "輸入編輯區要搜尋的文字（不分大小寫）：")
        if not accepted or not query:
            return
        count = 0
        cursor = self.editor.textCursor()
        cursor.setPosition(0)
        self.editor.setTextCursor(cursor)
        while self.editor.find(query):
            count += 1
            if count >= 5000:
                break
        self.status_label.setText("XML 搜尋：%d 筆（最多標示 5000 筆）" % count)

    def search_tree(self):
        if self.busy or self.snapshot is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("搜尋 DATA TREE（目前快照；名稱、module 路徑、值、description）")
        dialog.resize(1000, 550)
        layout = QVBoxLayout(dialog)
        bar = QHBoxLayout()
        entry = QLineEdit()
        search_button = QPushButton("搜尋（最多 500 筆）")
        jump_button = QPushButton("跳到所選節點")
        bar.addWidget(entry, 1)
        bar.addWidget(search_button)
        bar.addWidget(jump_button)
        layout.addLayout(bar)
        results = QListWidget()
        layout.addWidget(results, 1)
        found = []
        snapshot = self.snapshot
        def search():
            found[:] = search_snapshot(snapshot.data, self.client.schema, entry.text())
            results.clear()
            for label, _selection in found:
                results.addItem(label)
        def jump():
            row = results.currentRow()
            if row < 0 or row >= len(found) or self.snapshot is not snapshot:
                return
            self._show_selection(found[row][1])
            dialog.accept()
        search_button.clicked.connect(search)
        jump_button.clicked.connect(jump)
        entry.returnPressed.connect(search)
        results.itemDoubleClicked.connect(lambda _item: jump())
        dialog.show()

    def show_node_info(self):
        if not self.selection:
            return
        info = self.client.schema.lookup(self.selection.path)
        lines = ["/" + "/".join(local(x) for x in self.selection.path), ""]
        if info:
            lines += ["Module: " + info.module, "Kind: " + info.kind,
                      "config: " + str(info.config), "Type: " + info.type_name,
                      "Units: " + info.units, "Default: " + repr(info.defaults),
                      "", "Constraints (含 typedef；完整語意以 server 驗證為準):",
                      info.constraints or "（未提供）", "", "Description:",
                      info.description or "（未提供）"]
        else:
            lines.append("Schema 未知，唯讀。")
        self._text_window("YANG 欄位說明", "\n".join(lines))

    def show_availability(self):
        lines = ["設定操作："]
        for operation, (title, *_rest) in lifecycle.OPERATIONS.items():
            try:
                reason = "可使用" if lifecycle.supported(self.client, operation) else "缺少 server capability"
            except Exception as exc:
                reason = type(exc).__name__
            lines.append(title + "：" + reason)
        lines += ["", "按鈕與目前狀態：",
                  "NETCONF方式修改：" + ("可使用" if self.netconf_button.isEnabled() else "目前不可用"),
                  "系統 SSH：" + ("已連線" if self.admin_connection else "未連線"),
                  "事件訂閱：" + ("已訂閱" if self.notification_manager else "未訂閱")]
        self._text_window("功能狀態 / 停用原因", "\n".join(lines))

    def validate_draft(self):
        if self.busy or not self.client.connected or not self.plan or self.plan.rpc is None:
            self.status_label.setText("請先連線並建立有效的 XML 草稿。")
            return
        if not safety.has_cap(self.client, "validate:1.1"):
            self.status_label.setText("Server 未宣告 :validate:1.1；不會降級成實際修改。")
            return
        try:
            rpc = safety.draft_rpc(self.plan)
            from ncclient.xml_ import to_xml
            wire = to_xml(rpc)
            self.preview.setPlainText(wire)
            self.output_tabs.setCurrentWidget(self.preview)
            if QMessageBox.question(self, "驗證 XML 草稿",
                                    "將送出 test-only RPC，由 server 驗證但不套用設定。\n是否繼續？",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                self.update_preview()
                return
            selection, plan, options = self.selection, self.plan, self.snapshot.options
            def done(reply):
                self.reply.setPlainText(reply)
                self.output_tabs.setCurrentWidget(self.reply)
                self.status_label.setText("test-only 驗證成功；XML 草稿保留，尚未套用或保存設定。")
                self._audit_result("test-only validate", "完成")
            self._run("驗證 XML 草稿…",
                      lambda _progress: safety.test_draft(self.client, selection, plan, options, rpc), done)
        except Exception as exc:
            self.show_error(exc)

    def finish_confirmed(self, confirm):
        pending = self._pending()
        if not pending or self.busy or not self.client.connected or self.client.manager is not pending.manager:
            return
        try:
            rpc = safety.pending_rpc(pending, confirm)
            from ncclient.xml_ import to_xml
            self.preview.setPlainText(to_xml(rpc))
            question = ("確認保留這次限時提交？不會保存 startup。" if confirm else
                        "取消這次限時提交並要求 server 回復先前 running？")
            if QMessageBox.question(self, "限時提交", question,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            options = self.snapshot.options if self.snapshot else ReadOptions()
            def work(_progress):
                reply, warnings = safety.finish_pending(self.client, pending, confirm, rpc)
                snapshot = None
                try:
                    snapshot = self.client.read(options)
                except Exception:
                    warnings.append("讀回失敗，請重新連線／讀取確認。")
                return reply, warnings, snapshot
            def done(result):
                reply, warnings, snapshot = result
                if snapshot is not None:
                    self._accept_snapshot(snapshot)
                self.reply.setPlainText(reply)
                self.output_tabs.setCurrentWidget(self.reply)
                self.uncertain = bool(warnings)
                self.status_label.setText(("已確認保留；未保存 startup。" if confirm else "取消 RPC 成功，請核對讀回設定。")
                                         + " " + " ".join(warnings))
            self._run("確認限時提交…" if confirm else "取消限時提交…", work, done)
        except Exception as exc:
            self.show_error(exc)

    def datastore_action(self, operation):
        if self.busy or not self.client.connected or self._pending():
            self.status_label.setText("目前無法執行設定操作；請確認已連線且沒有限時提交。")
            return
        if not self._rpc_allowed():
            self.status_label.setText("事件訂閱中且 server 不支援 interleave；請先停止訂閱。")
            return
        timeout = 120
        if operation == "confirmed":
            value, ok = QInputDialog.getInt(self, "限時提交", "幾秒內必須確認保留？（30–600 秒）",
                                            120, 30, 600)
            if not ok:
                return
            timeout = value
        source = self.snapshot.options.source if self.snapshot else self.source_box.currentText()
        def prepared_done(prepared):
            from ncclient.xml_ import to_xml
            wire = to_xml(prepared.rpc)
            self.preview.setPlainText(wire)
            self.output_tabs.setCurrentWidget(self.preview)
            if operation == "compare":
                self._text_window(prepared.operation, "唯讀比較（不同權限／default 表示法也可能造成差異）\n\n" + prepared.diff)
                self.status_label.setText("running / startup 比較完成；沒有修改設備。")
                return
            text = ("整份 datastore 操作，可能包含其他使用者的設定。\n"
                    "commit 不會保存 startup；discard 會捨棄整份 candidate 的未提交變更。\n\n"
                    + prepared.diff + "\n\n實際 RPC：\n" + wire)
            if QMessageBox.question(self, prepared.operation, text + "\n\n確認送出？",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            options = ReadOptions(prepared.source if prepared.source else source)
            def work(_progress):
                reply, warnings = lifecycle.execute(self.client, prepared)
                snapshot = None
                if operation != "validate" and self.client.connected:
                    try:
                        snapshot = self.client.read(ReadOptions(prepared.target or source))
                    except Exception:
                        warnings.append("RPC 成功但讀回失敗；結果待確認，不可直接重送。")
                return reply, warnings, snapshot
            def done(result):
                reply, warnings, snapshot = result
                self.reply.setPlainText(reply)
                self.output_tabs.setCurrentWidget(self.reply)
                if snapshot is not None:
                    self._accept_snapshot(snapshot)
                self.uncertain = bool(warnings)
                self.status_label.setText(prepared.operation + " RPC 完成。" + " ".join(warnings))
                self._audit_result(prepared.operation, "完成" if not warnings else "完成；結果待確認")
            self._run("設定操作：" + prepared.operation, work, done)
        self._run("準備設定操作預覽…",
                  lambda _progress: lifecycle.prepare(self.client, operation, source, timeout),
                  prepared_done)

    # ---------- local backup / restore ----------

    def create_backup(self):
        if self.busy or not self.client.connected or self._pending():
            self.status_label.setText("請在已連線且閒置時建立備份。")
            return
        source = self.snapshot.options.source if self.snapshot else self.source_box.currentText()
        directory = Path.cwd() / "backups"
        filename = "netconf-%s-%s.nccbackup" % (source, datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        name, _ = QFileDialog.getSaveFileName(self, "建立加密設定版本（不含本機草稿）",
                                              str(directory / filename), "Encrypted config snapshot (*.nccbackup)")
        if not name:
            return
        device = self._audit_device()
        def work(_progress):
            snapshot = self.client.read(ReadOptions(source, True, False))
            return backups.save_backup(name, snapshot, device, self.client.schema)
        self._run("建立加密設定備份…", work,
                  lambda created: self.status_label.setText("已建立 %s 加密設定版本（%s）；不含未送出草稿。" % (source, created)))

    def restore_backup(self):
        if self.busy or self._pending() or not self.selection or not self.snapshot:
            self.status_label.setText("請先讀取並選擇 running/candidate 的目標 subtree。")
            return
        if self.snapshot.options.source == "startup" or self.uncertain:
            self.status_label.setText("startup 唯讀或結果待確認；請先重新讀取。")
            return
        name, _ = QFileDialog.getOpenFileName(self, "選擇加密設定版本（只還原勾選的項目）", "",
                                              "Encrypted config snapshot (*.nccbackup)")
        if name:
            self._run("開啟加密設定備份…", lambda _progress: backups.load_backup(name), self._restore_dialog)

    def _restore_dialog(self, payload):
        try:
            choices = backups.restore_choices(self.selection, payload["xml"], self.client.schema,
                                              self.snapshot.options.source)
        except Exception as exc:
            self.show_error(exc)
            return
        dialog = QDialog(self)
        self.lifecycle_dialog = dialog
        dialog.setWindowTitle("選擇性還原（只載入本機草稿）")
        dialog.resize(1050, 680)
        layout = QVBoxLayout(dialog)
        label = QLabel("備份：%s | %s | %s\n目標：%s；僅目前選取 subtree，不是整台設備還原。"
                       "新增／移除容器或 list instance 是一組原子項目。" %
                       (payload["device"], payload["created"], payload["source"], self._audit_device()))
        label.setWordWrap(True)
        layout.addWidget(label)
        tree = QTreeWidget()
        tree.setHeaderLabels(["選取", "項目"])
        layout.addWidget(tree, 0)
        pane = self._output_edit()
        layout.addWidget(pane, 1)
        checked = set()
        for index, choice in enumerate(choices):
            item = QTreeWidgetItem(tree, ["☐", choice.label])
            item.setData(0, USER_ROLE, index)
        def preview():
            lines = []
            for index in sorted(checked):
                choice = choices[index]
                before = etree.tostring(choice.before, encoding="unicode", pretty_print=True) if choice.before is not None else "（不存在）"
                after = etree.tostring(choice.after, encoding="unicode", pretty_print=True) if choice.after is not None else "（移除）"
                lines += [choice.label, "原值：\n" + before, "備份值：\n" + after, ""]
            pane.setPlainText("\n".join(lines) or "點擊項目勾選；預設不勾選任何項目。")
        def toggle(item, _column=0):
            index = item.data(0, USER_ROLE)
            if index is None:
                return
            if index in checked:
                checked.remove(index)
                item.setText(0, "☐")
            else:
                checked.add(index)
                item.setText(0, "☑")
            preview()
        tree.itemClicked.connect(toggle)
        controls = QHBoxLayout()
        all_button = QPushButton("全選")
        clear_button = QPushButton("清除選取")
        stage_button = QPushButton("載入選定項目到草稿")
        cancel_button = QPushButton("取消")
        controls.addWidget(all_button)
        controls.addWidget(clear_button)
        controls.addStretch(1)
        controls.addWidget(stage_button)
        controls.addWidget(cancel_button)
        layout.addLayout(controls)
        def select_all(enabled):
            checked.clear()
            if enabled:
                checked.update(range(len(choices)))
            for index in range(tree.topLevelItemCount()):
                tree.topLevelItem(index).setText(0, "☑" if enabled else "☐")
            preview()
        def close_dialog():
            self.lifecycle_dialog = None
            dialog.close()
            self._sync_controls()
        def stage():
            if not checked:
                return
            try:
                text = backups.apply_choices(self.selection, choices, sorted(checked), self.client.schema,
                                             self.snapshot.options.source)
                plan = build_plan(self.selection, text, self.client.schema, self.snapshot.options.source)
                if QMessageBox.question(dialog, "載入還原草稿", "%d 項變更，含 %d 項移除。只載入草稿，仍須另按送出。繼續？" %
                                        (len(plan.changes), plan.removals),
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return
                self._set_editor(text)
                self.update_preview()
                self.output_tabs.setCurrentWidget(self.diff)
                close_dialog()
                self.status_label.setText("選定的還原項目已載入草稿；請檢查差異／test-only，再自行送出。")
            except Exception as exc:
                QMessageBox.critical(dialog, "還原草稿", redact_secrets(str(exc)))
        all_button.clicked.connect(lambda: select_all(True))
        clear_button.clicked.connect(lambda: select_all(False))
        stage_button.clicked.connect(stage)
        cancel_button.clicked.connect(close_dialog)
        dialog.finished.connect(lambda _code: setattr(self, "lifecycle_dialog", None))
        preview()
        dialog.show()

    # ---------- events / audit ----------

    def event_settings(self):
        dialog = QDialog(self)
        self.lifecycle_dialog = dialog
        dialog.setWindowTitle("事件訂閱設定 / streams / replay")
        dialog.resize(950, 600)
        layout = QVBoxLayout(dialog)
        form = QGridLayout()
        form.addWidget(QLabel("Stream"), 0, 0)
        stream = QComboBox()
        stream.setEditable(True)
        stream.addItems(list(self.streams) or ["NETCONF"])
        stream.setCurrentText(self.stream_box.currentText())
        form.addWidget(stream, 0, 1)
        update = QPushButton("更新 streams")
        form.addWidget(update, 0, 2)
        form.addWidget(QLabel("startTime"), 1, 0)
        start = QLineEdit(self.event_start)
        form.addWidget(start, 1, 1)
        form.addWidget(QLabel("stopTime"), 2, 0)
        stop = QLineEdit(self.event_stop)
        form.addWidget(stop, 2, 1)
        form.addWidget(QLabel("例：2026-09-09T08:00:00+08:00；stopTime 必須搭配 startTime"), 1, 2, 2, 2)
        form.setColumnStretch(1, 1)
        layout.addLayout(form)
        detail = QLabel("可更新設備 streams；時間留白即即時訂閱。")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        layout.addWidget(QLabel("Subtree filter XML（選用；填通知 payload 的根節點與 namespace，不要包 filter／rpc）"))
        filter_edit = CodeEditor()
        filter_edit.setPlainText(self.event_filter_xml)
        layout.addWidget(filter_edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        def selected():
            value = self.streams.get(stream.currentText())
            detail.setText(("Replay: %s | 最早: %s | %s" % (value.replay, value.earliest, value.description))
                           if value else "Stream 尚未查到；回放前需先確認 replaySupport。")

        def refresh():
            if self.busy or not self.client.connected or self.demo or not self._rpc_allowed():
                detail.setText("目前無法讀取 streams；請連線且停止不支援 interleave 的訂閱。")
                return
            def done(values):
                self.streams = values
                stream.clear()
                stream.addItems(list(values) or ["NETCONF"])
                self.stream_box.clear()
                self.stream_box.addItems(list(values) or ["NETCONF"])
                selected()
            self._run("讀取事件 streams…", lambda _progress: events.discover_streams(self.client.manager), done)

        def save():
            try:
                events.subscription_options(stream.currentText(), self.streams, filter_edit.toPlainText(),
                                           start.text(), stop.text())
                self.stream_box.setCurrentText(stream.currentText())
                self.event_filter_xml, self.event_start, self.event_stop = (filter_edit.toPlainText(),
                                                                              start.text(), stop.text())
                self.lifecycle_dialog = None
                dialog.accept()
                self.status_label.setText("訂閱條件已設定；按『開始訂閱』才送出，不會更改現有訂閱。")
            except Exception as exc:
                QMessageBox.critical(dialog, "訂閱條件", redact_secrets(str(exc)))

        def close_dialog():
            self.lifecycle_dialog = None
            dialog.reject()
            self._sync_controls()

        stream.currentTextChanged.connect(lambda _text: selected())
        update.clicked.connect(refresh)
        buttons.accepted.connect(save)
        buttons.rejected.connect(close_dialog)
        dialog.finished.connect(lambda _code: setattr(self, "lifecycle_dialog", None))
        selected()
        dialog.show()

    def subscribe(self):
        if self.busy or not self.client.connected or self.notification_manager is not None or self.demo or self._pending():
            return
        if not any(":notification:" in str(c) for c in self.client.capabilities):
            self.show_error(EditError("Server 不支援 RFC 5277 notifications。"))
            return
        try:
            options = events.subscription_options(self.stream_box.currentText(), self.streams,
                                                  self.event_filter_xml, self.event_start, self.event_stop)
            manager = self.client.manager
        except Exception as exc:
            self.show_error(exc)
            return
        interleave = any(":interleave:" in str(c) for c in self.client.capabilities)
        def done(_value):
            self.notification_manager = manager
            self.notification_timer.start()
            self.notification_status.setText("已訂閱 %s%s。" %
                                             (options["stream_name"], "；可同時讀寫" if interleave else "；讀寫暫停，停止須中斷連線"))
            self._sync_controls()
        self._run("訂閱事件…", lambda _progress: manager.create_subscription(**options), done)

    def stop_subscription(self):
        if self.notification_manager is None:
            return
        if QMessageBox.question(self, "停止訂閱", "停止訂閱會中斷目前 NETCONF session；不會自動重訂閱。\n確定？",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.notification_manager = None
            self.notification_timer.stop()
            self.disconnect()

    def _poll_notifications(self):
        manager = self.notification_manager
        if manager is None or not self.client.connected:
            self.notification_timer.stop()
            return
        received = False
        try:
            while True:
                notification = manager.take_notification(block=False)
                if notification is None:
                    break
                text = redact_secrets(notification.notification_xml)
                self.notifications.append(text)
                self._record_notification(text)
                received = True
                if self.notification_manager is None:
                    break
        except Exception as exc:
            self.notification_status.setText("通知讀取失敗；請中斷連線以結束訂閱。")
            self._audit_result("notification", "讀取失敗：" + type(exc).__name__)
        if received:
            self.notification_pane.setPlainText("\n\n".join(self.notifications))
            self._audit_result("notification", "收到通知（內容僅保留記憶體，不寫入操作紀錄）")

    def _record_notification(self, text):
        record = events.event_record(text)
        self.event_records.append(record)
        if record.completed:
            self.notification_manager = None
            self.notification_timer.stop()
            self.notification_status.setText("Server 已送出 notificationComplete，訂閱結束；可重新訂閱。")
        if hasattr(self, "alarm_tree"):
            self._refresh_alarms()

    def export_notifications(self):
        if not self.notifications:
            self.status_label.setText("目前沒有可匯出的通知。")
            return
        filename, _ = QFileDialog.getSaveFileName(self, "匯出通知", "netconf-notifications.txt", "Text (*.txt)")
        if filename:
            try:
                Path(filename).write_text("\n\n".join(self.notifications), encoding="utf-8", newline="\n")
                self.status_label.setText("已匯出通知：" + filename)
            except Exception as exc:
                self.show_error(exc)

    def show_alarms(self):
        if getattr(self, "alarm_dialog", None) is not None and self.alarm_dialog.isVisible():
            self.alarm_dialog.raise_()
            return
        dialog = self.alarm_dialog = QDialog(self)
        dialog.setWindowTitle("NETCONF 告警表格（最近 100 筆；篩選不影響 server 訂閱）")
        dialog.resize(1100, 700)
        layout = QVBoxLayout(dialog)
        bar = QHBoxLayout()
        severity = QComboBox()
        for value in ("全部", "critical", "major", "minor", "warning", "indeterminate", "cleared", "unknown"):
            severity.addItem(tr(value), value)
        search = QLineEdit()
        search.setPlaceholderText("搜尋時間、來源、事件或 XML")
        refresh = QPushButton("篩選")
        bar.addWidget(severity)
        bar.addWidget(search, 1)
        bar.addWidget(refresh)
        layout.addLayout(bar)
        table = QTreeWidget()
        table.setHeaderLabels(["時間", "嚴重度", "來源", "事件"])
        table.setColumnWidth(0, 250)
        layout.addWidget(table, 0)
        detail = self._output_edit()
        layout.addWidget(detail, 1)
        self.alarm_tree, self.alarm_detail = table, detail
        self.alarm_filter_box, self.alarm_search_box = severity, search
        def update():
            self._refresh_alarms()
        refresh.clicked.connect(update)
        severity.currentTextChanged.connect(lambda _text: update())
        search.returnPressed.connect(update)
        table.currentItemChanged.connect(lambda item, _old: detail.setPlainText(
            self.alarm_rows.get(item.data(0, USER_ROLE), events.EventRecord("", "", "", "", "")).xml
            if item else ""))
        dialog.finished.connect(lambda _code: setattr(self, "alarm_dialog", None))
        self._refresh_alarms()
        dialog.show()

    def _refresh_alarms(self):
        if not hasattr(self, "alarm_tree") or self.alarm_tree is None:
            return
        self.alarm_tree.clear()
        self.alarm_rows.clear()
        query = self.alarm_search_box.text().casefold()
        selected = str(self.alarm_filter_box.currentData() or self.alarm_filter_box.currentText()).lower()
        for index, record in enumerate(self.event_records):
            if selected != "全部" and record.severity != selected:
                continue
            if query not in (record.time + " " + record.source + " " + record.event + " " + record.xml).casefold():
                continue
            item = QTreeWidgetItem(self.alarm_tree, [record.time, record.severity, record.source, record.event])
            item.setData(0, USER_ROLE, index)
            if record.severity == "critical":
                item.setBackground(0, QBrush(QColor("#ffd3d3")))
            elif record.severity == "major":
                item.setBackground(0, QBrush(QColor("#ffe0bb")))
            elif record.severity == "minor":
                item.setBackground(0, QBrush(QColor("#fff4bd")))
            self.alarm_rows[index] = record

    # ---------- profiles / templates / workspace tools ----------

    def export_audit(self):
        filename, _ = QFileDialog.getSaveFileName(self, "匯出操作紀錄", "netconf-operations.json", "JSON (*.json)")
        if not filename:
            return
        try:
            if self.audit.path and Path(filename).resolve() == self.audit.path.resolve():
                raise EditError("請另選匯出位置，不能覆寫使用中的歷史紀錄。")
            Path(filename).write_text(self.audit.json(), encoding="utf-8", newline="\n")
            self.status_label.setText("已匯出操作紀錄：" + filename)
        except Exception as exc:
            self.show_error(exc)

    def import_profiles(self):
        if self.busy or self.lifecycle_dialog or self.preferences_error:
            self.status_label.setText("請先完成其他操作，且設定檔必須可正常保存。")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "匯入連線／SSH 帳號設定", "",
                                                  "GUI 設定 JSON／加密備份 (*.json *.dpapi)")
        if not filename:
            return
        try:
            book, encrypted = read_import(filename, self._values())
        except Exception as exc:
            self.show_error(exc)
            return
        dialog = QDialog(self)
        self.lifecycle_dialog = dialog
        dialog.setWindowTitle("匯入設定 — 勾選與命名後合併，不會自動連線")
        dialog.resize(1040, 620)
        layout = QVBoxLayout(dialog)
        intro = QLabel("預設不勾選、不帶密碼；同名會產生新名稱，只有勾選取代同名才會取代。\n"
                       "憑證／私鑰檔案不在匯入檔內，請在本機重新指定路徑。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        tree = QTreeWidget()
        tree.setHeaderLabels(["匯入", "類型", "來源名稱", "匯入名稱", "取代同名"])
        layout.addWidget(tree, 1)
        records = {}
        used = {group: set(self.preferences[group]) for group in ("connections", "accounts")}
        for group in ("connections", "accounts"):
            for old in book[group]:
                new, number = old, 2
                while new in used[group]:
                    new, number = "%s（匯入 %d）" % (old, number), number + 1
                used[group].add(new)
                item = QTreeWidgetItem(tree, ["☐", group, old, new, "否"])
                item.setData(0, USER_ROLE, (group, old, new, False))
                records[id(item)] = item
        detail = QLabel("請勾選要匯入的設定組。")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        edit_row = QHBoxLayout()
        edit_row.addWidget(QLabel("匯入名稱"))
        name_edit = QLineEdit()
        replace = QCheckBox("取代同名（須確認）")
        apply_name = QPushButton("套用名稱／選項")
        edit_row.addWidget(name_edit, 1)
        edit_row.addWidget(replace)
        edit_row.addWidget(apply_name)
        layout.addLayout(edit_row)
        options = QHBoxLayout()
        secrets = QCheckBox("匯入加密備份中的密碼（僅本機）")
        secrets.setEnabled(encrypted)
        paths = QCheckBox("保留原檔案路徑（限同一電腦）")
        options.addWidget(secrets)
        options.addWidget(paths)
        options.addStretch(1)
        layout.addLayout(options)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_button = controls.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setProperty("_ncc_source_text", "確認匯入選取項目")
        ok_button.setText(tr("確認匯入選取項目"))
        layout.addWidget(controls)

        def current_item():
            return tree.currentItem()

        def select_item(item, _column=0):
            if not item:
                return
            group, old, new, do_replace = item.data(0, USER_ROLE)
            name_edit.setText(new)
            replace.setChecked(do_replace)
            record = book[group][old]
            detail.setText("預覽：%s | host=%s | listen=%s | username=%s\n密碼不顯示；憑證與私鑰檔案本身不在匯入檔內。" %
                           (record.get("mode", "SSH 帳號"), record.get("host", ""),
                            record.get("listen_host", ""), record.get("username", "")))

        def toggle():
            item = current_item()
            if not item:
                return
            group, old, new, do_replace = item.data(0, USER_ROLE)
            item.setText(0, "☐" if item.text(0) == "☑" else "☑")
            item.setData(0, USER_ROLE, (group, old, new, do_replace))

        def rename():
            item = current_item()
            if not item:
                return
            group, old, _new, _replace = item.data(0, USER_ROLE)
            new = name_edit.text().strip()
            do_replace = replace.isChecked()
            item.setText(3, new)
            item.setText(4, "是" if do_replace else "否")
            item.setData(0, USER_ROLE, (group, old, new, do_replace))

        def apply():
            try:
                rename()
                choices = []
                for index in range(tree.topLevelItemCount()):
                    item = tree.topLevelItem(index)
                    if item.text(0) == "☑":
                        group, old, new, do_replace = item.data(0, USER_ROLE)
                        choices.append((group, old, new, do_replace))
                result = merge_profiles(self.preferences, book, choices,
                                        include_secrets=encrypted and secrets.isChecked(),
                                        keep_paths=paths.isChecked())
                if QMessageBox.question(dialog, "確認匯入", "合併 %d 組設定？不會連線或修改設備。" % len(choices),
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return
                if self.preferences_store:
                    self.preferences_store.save(result)
                self.preferences = result
                self._refresh_profile_boxes()
                self.status_label.setText("已匯入設定；請確認帳密與本機憑證路徑後再自行連線。")
                self.lifecycle_dialog = None
                dialog.accept()
            except Exception as exc:
                detail.setText(redact_secrets(str(exc)))

        tree.itemClicked.connect(select_item)
        tree.itemDoubleClicked.connect(lambda _item, _column: toggle())
        apply_name.clicked.connect(rename)
        controls.accepted.connect(apply)
        controls.rejected.connect(lambda: (setattr(self, "lifecycle_dialog", None), dialog.reject()))
        dialog.finished.connect(lambda _code: setattr(self, "lifecycle_dialog", None))
        dialog.show()

    def _template_parent(self):
        if not self.selection or not self.snapshot:
            raise EditError("請先選取可寫 container/list 父節點。")
        info = self.client.schema.lookup(self.selection.path)
        if not info or info.kind not in {"container", "list"} or info.config is not True:
            raise EditError("請先選取可寫 container/list 父節點。")
        return parse_editor(self.editor.toPlainText()), self.selection.path

    def clone_list(self):
        if not self.selection:
            return
        try:
            info = self.client.schema.lookup(self.selection.path)
            if not info or info.kind != "list":
                raise EditError("請選取要複製的 list 項目。")
            source = parse_editor(self.editor.toPlainText())
            template, removed = templates.from_node(self.client.schema, self.selection, source)
            parent_path = self.selection.path[:-1]
            parent = self.selection.ancestors[-1] if self.selection.ancestors else self.snapshot.data
            dialog = CreationDialog(self.client.schema, parent, parent_path, self,
                                    initial_node=template.root, initial_path=self.selection.path)
            for index in range(dialog.list.count()):
                candidate = dialog.list.item(index).data(USER_ROLE)
                if candidate is not None and candidate.info.path == info.path:
                    dialog.list.setCurrentRow(index)
                    break
            if dialog.exec() == QDialog.DialogCode.Accepted:
                node = dialog.result_node
                # For a selected list item, the sibling belongs to its real parent.
                if self.selection.ancestors:
                    parent_selection = Selection(self.selection.ancestors[-1], self.selection.ancestors[:-1])
                    self.selection = parent_selection
                    self.baseline_text = parent_selection.text()
                    edited = parse_editor(self.baseline_text)
                    edited.append(node)
                else:
                    # A top-level list has no visible <data> editor parent.
                    # Stage it as a virtual root creation so the normal planner
                    # can emit one atomic create operation for the new instance.
                    self.selection = new_root_selection(node, self.client.schema)
                    self.baseline_text = self.selection.text()
                    edited = deepcopy(node)
                self._set_editor(serialize_xml(edited).decode("utf-8"))
                self.path_label.setText("/" + "/".join(local(x) for x in self.selection.path))
                self.update_preview()
                detail = "；".join(removed[:3]) if removed else "一般可寫值已保留"
                self.status_label.setText("已建立 list 複製草稿（%s）；請重新填寫 key 後再檢查送出。" % detail)
        except Exception as exc:
            self.show_error(exc)

    def save_personal_template(self):
        if not self.selection or not self.snapshot or self.busy:
            return
        try:
            template, _ = templates.from_node(self.client.schema, self.selection,
                                              parse_editor(self.editor.toPlainText()))
            filename, _ = QFileDialog.getSaveFileName(self, "保存個人範本", "", "加密個人範本 (*.ncctemplate)")
            if filename:
                templates.save_template(filename, template, self.client.schema)
                self.status_label.setText("已保存個人範本；不含 state／密碼／私鑰內容。")
        except Exception as exc:
            self.show_error(exc)

    def load_personal_template(self):
        if not self.selection or not self.snapshot or not self.client.schema.complete:
            self.status_label.setText("請先連線、讀取 running/candidate 並載入完整 schema。")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "個人範本庫 — 選擇範本", "",
                                                  "加密個人範本 (*.ncctemplate)")
        if not filename:
            return
        try:
            template = templates.load_template(filename, self.client.schema)
            parent = parse_editor(self.editor.toPlainText())
            result = append_template(self.client.schema, parent, self.selection.path, template)
            self._set_editor(serialize_xml(result).decode("utf-8"))
            self.update_preview()
            self.status_label.setText("已將個人範本加入本機草稿；敏感欄位／新 key 仍需重新填寫。")
        except Exception as exc:
            self.show_error(exc)

    # ---------- three-way result checking / diagnostics ----------

    def _begin_attempt(self, selection, plan, options, route):
        self.last_attempt = {
            "selection": deepcopy(selection), "mine": deepcopy(plan.edited), "options": options,
            "scope": self._draft_scope(), "schema": self.client.schema,
            "hash": schema_fingerprint(self.client.schema), "route": route,
            "session": self._draft_session(),
        }
        self.result_tree.clear()
        self.result_rows = []
        self.result_pending = True
        if self.snapshot:
            _, self.result_rows = reconcile.compare(selection, plan.edited, self.snapshot.data,
                                                    self.client.schema, options.source)
        for index, row in enumerate(self.result_rows):
            item = QTreeWidgetItem(self.result_tree, [row.path, reconcile.display(row.mine), "未讀回", "待確認"])
            item.setData(0, USER_ROLE, index)
        self.result_status.setText(route + " 已開始；尚未讀回，不能假定已成功或已回復。")

    def _finish_attempt(self, snapshot=None, error=None):
        attempt = self.last_attempt
        if not attempt:
            return
        if snapshot is None:
            self.result_pending = True
            self.result_status.setText("結果待確認；%s。可重新讀回，不會重送。" %
                                       (type(error).__name__ if error else "未取得 NETCONF 讀回"))
            self.output_tabs.setCurrentWidget(self.result_frame)
            return
        _, rows = reconcile.compare(attempt["selection"], attempt["mine"], snapshot.data,
                                    attempt["schema"], attempt["options"].source)
        self.result_rows = rows
        self.result_tree.clear()
        self.result_pending = False
        matched = 0
        for index, row in enumerate(rows):
            path = attempt["selection"].path + tuple(key[0] for key in row.address)
            ok = reconcile.equal(row.mine, row.latest, path, attempt["schema"])
            matched += int(ok)
            item = QTreeWidgetItem(self.result_tree, [row.path, reconcile.display(row.mine),
                                                      reconcile.display(row.latest),
                                                      "符合預期" if ok else "不符／需核對"])
            item.setData(0, USER_ROLE, index)
            item.setForeground(3, QBrush(QColor("#15803d" if ok else "#b45309")))
        if rows and matched == len(rows):
            entry = self.drafts.matching(attempt["scope"], attempt["options"].source,
                                         attempt["selection"], attempt["schema"])
            if entry:
                self.drafts.entries.pop(entry.key, None)
                self._schedule_drafts()
        self.result_status.setText("%d / %d 項符合預期；雙擊查看子樹 XML。不代表已 commit 或保存 startup。" %
                                   (matched, len(rows)))

    def show_result_xml(self, item=None, _column=0):
        if item is None:
            item = self.result_tree.currentItem()
        if item is None or self.busy:
            return
        index = item.data(0, USER_ROLE)
        if index is None or index >= len(self.result_rows):
            return
        row = self.result_rows[index]
        expected = serialize_xml(row.mine).decode("utf-8") if row.mine is not None else "（不存在）"
        actual = ("尚未讀回，結果未知" if self.result_pending else
                  serialize_xml(row.latest).decode("utf-8") if row.latest is not None else "（不存在）")
        self._text_window("預期／實際 XML（可能含敏感值）", "預期：\n" + expected + "\n設備：\n" + actual,
                          width=1000, height=550)

    def reread_attempt(self):
        attempt = self.last_attempt
        if not attempt or not self.client.connected or self.busy or self._pending():
            return
        if not self._rpc_allowed():
            self.status_label.setText("目前通知訂閱不允許交錯 RPC，請先停止訂閱。")
            return
        if attempt["scope"] != self._draft_scope() or attempt["hash"] != schema_fingerprint(self.client.schema):
            self.status_label.setText("不是原設備／帳號／schema，不能核對這次修改。")
            return
        options = attempt["options"]
        self._run("重新讀回修改結果…",
                  lambda _progress: self.client.read(ReadOptions(options.source, options.defaults, False),
                                                     attempt["selection"].path[0]),
                  self._finish_attempt)

    def open_reconcile(self):
        if not self.selection or not self.snapshot or not self.client.connected or self.busy or self._pending():
            self.status_label.setText("請連線、選取草稿，並完成其他操作後再比對。")
            return
        if not self._rpc_allowed():
            self.status_label.setText("目前通知訂閱不允許交錯 RPC，請先停止訂閱。")
            return
        try:
            mine = parse_editor(self.editor.toPlainText())
            selection, before = deepcopy(self.selection), self.editor.toPlainText()
            options, session, schema = self.snapshot.options, self._draft_session(), self.client.schema
            def work(_progress):
                fresh = self.client.read(ReadOptions(options.source, options.defaults, False), selection.path[0])
                latest, rows = reconcile.compare(selection, mine, fresh.data, schema, options.source)
                return latest, rows
            def done(result):
                if session is not self._draft_session() or self.editor.toPlainText() != before:
                    raise EditError("連線或草稿已改變，請重新比對。")
                self._show_reconcile_dialog(selection, *result, options, session, schema)
            self._run("三方比對：讀取設備最新值…", work, done)
        except Exception as exc:
            self.show_error(exc)

    def _show_reconcile_dialog(self, selection, latest, rows, options, session, schema):
        dialog = QDialog(self)
        self.lifecycle_dialog = dialog
        dialog.setWindowTitle("三方比對 — 明確選擇後重建本機草稿")
        dialog.resize(1220, 780)
        layout = QVBoxLayout(dialog)
        intro = QLabel("保留設備的無關變更；衝突須逐項選擇。合併只產生草稿，送出前仍會再次讀取比對。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        table = QTreeWidget()
        table.setHeaderLabels(["節點／instance", "原始值", "設備最新值", "我的草稿", "採用／狀態"])
        layout.addWidget(table, 0)
        for index, row in enumerate(rows):
            item = QTreeWidgetItem(table, [row.path, reconcile.display(row.before),
                                          reconcile.display(row.latest), reconcile.display(row.mine), row.status])
            item.setData(0, USER_ROLE, index)
            item.setForeground(4, QBrush(QColor("#b91c1c" if not row.choice else "#15803d")))
        status = QLabel("尚有 %d 項衝突；請選擇後才能合併。" % sum(not row.choice for row in rows))
        status.setWordWrap(True)
        layout.addWidget(status)
        panes = QSplitter(Qt.Orientation.Horizontal)
        pane_before, pane_latest, pane_mine = (self._output_edit() for _ in range(3))
        for title, pane in (("原始值", pane_before), ("設備最新值", pane_latest), ("我的草稿", pane_mine)):
            frame = QWidget()
            frame_layout = QVBoxLayout(frame)
            frame_layout.addWidget(QLabel(title))
            frame_layout.addWidget(pane, 1)
            panes.addWidget(frame)
        layout.addWidget(panes, 1)
        controls = QHBoxLayout()
        latest_button = QPushButton("此項採用設備最新值")
        mine_button = QPushButton("此項採用我的草稿")
        stage_button = QPushButton("確認合併為新草稿")
        cancel_button = QPushButton("取消")
        controls.addWidget(latest_button)
        controls.addWidget(mine_button)
        controls.addStretch(1)
        controls.addWidget(stage_button)
        controls.addWidget(cancel_button)
        layout.addLayout(controls)
        def select(item, _column=0):
            if not item:
                return
            row = rows[item.data(0, USER_ROLE)]
            pane_before.setPlainText(serialize_xml(row.before).decode() if row.before is not None else "（不存在）")
            pane_latest.setPlainText(serialize_xml(row.latest).decode() if row.latest is not None else "（不存在）")
            pane_mine.setPlainText(serialize_xml(row.mine).decode() if row.mine is not None else "（不存在）")
        def choose(choice):
            item = table.currentItem()
            if not item:
                return
            row = rows[item.data(0, USER_ROLE)]
            row.choice = choice
            item.setText(4, "設備最新值" if choice == "latest" else "我的草稿")
            status.setText("尚有 %d 項待選擇" % sum(not row.choice for row in rows))
        def close_dialog():
            self.lifecycle_dialog = None
            dialog.reject()
            self._sync_controls()
        def stage():
            try:
                if session is not self._draft_session() or schema is not self.client.schema:
                    raise EditError("連線／schema 已改變，請重新比對。")
                baseline, text = reconcile.resolve_rows(selection, latest, rows, schema, options.source)
                if QMessageBox.question(dialog, "確認重建草稿", "依選擇合併設備最新值與草稿？不會送出。",
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return
                self.selection = baseline
                self.baseline_text = baseline.text()
                self._set_editor(text)
                self.uncertain = False
                self.update_preview()
                close_dialog()
                self.status_label.setText("已重建本機草稿；未送出，請檢查差異與 RPC。")
            except Exception as exc:
                status.setText(redact_secrets(str(exc)))
        table.currentItemChanged.connect(lambda item, _old: select(item))
        latest_button.clicked.connect(lambda: choose("latest"))
        mine_button.clicked.connect(lambda: choose("mine"))
        stage_button.clicked.connect(stage)
        cancel_button.clicked.connect(close_dialog)
        dialog.finished.connect(lambda _code: setattr(self, "lifecycle_dialog", None))
        if table.topLevelItemCount():
            table.setCurrentItem(table.topLevelItem(0))
        dialog.show()

    def open_connection_diagnostics(self):
        if self.busy or self.client.connected:
            self.status_label.setText("診斷會建立臨時連線；請先中斷目前 NETCONF。")
            return
        try:
            settings = self._settings()
        except Exception as exc:
            self.show_error(exc)
            return
        dialog = QDialog(self)
        self.diagnostic_dialog = dialog
        self.lifecycle_dialog = dialog
        dialog.setWindowTitle("連線診斷 — 臨時連線／不修改設備")
        dialog.resize(1080, 720)
        layout = QVBoxLayout(dialog)
        intro = QLabel("按開始才連線；診斷只載入 schema 後關閉，不套用草稿、不修改設備。\n"
                       "匯出內容會遮蔽 host、帳號及接入資訊，不包含密碼、憑證、私鑰或 XML。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        table = QTreeWidget()
        table.setHeaderLabels(["階段", "結果", "耗時（秒）", "本機詳細資料／接入來源"])
        layout.addWidget(table, 0)
        status = QLabel("尚未開始；下方是匯出前的遮蔽報告預覽。")
        status.setWordWrap(True)
        layout.addWidget(status)
        preview = self._output_edit()
        layout.addWidget(preview, 1)
        controls = QHBoxLayout()
        start = QPushButton("開始診斷")
        cancel = QPushButton("取消診斷")
        export = QPushButton("匯出遮蔽後報告 JSON")
        export.setEnabled(False)
        close = QPushButton("關閉")
        controls.addWidget(start)
        controls.addWidget(cancel)
        controls.addWidget(export)
        controls.addStretch(1)
        controls.addWidget(close)
        layout.addLayout(controls)
        report = {"value": None}
        def run():
            start.setEnabled(False)
            export.setEnabled(False)
            diagnostic = DiagnosticRun(settings, self.client.cancel)
            def done(value):
                report["value"] = value
                preview.setPlainText(report_text(value))
                start.setEnabled(True)
                export.setEnabled(True)
                status.setText("診斷通過（不代表具有寫入權限）" if value["passed"] else "診斷未通過；未修改設備。")
            self._run("連線診斷中…", lambda _progress: diagnostic.run(), done)
        def export_report():
            if report["value"] is None:
                return
            filename, _ = QFileDialog.getSaveFileName(dialog, "匯出遮蔽後報告", "netconf-diagnostic-redacted.json", "JSON (*.json)")
            if filename:
                Path(filename).write_text(report_text(report["value"]), encoding="utf-8", newline="\n")
                status.setText("已匯出遮蔽報告；不含帳密、憑證／私鑰內容、XML 設定。")
        def close_dialog():
            if self.busy:
                self.client.cancel.set()
                status.setText("正在取消診斷；等待目前 timeout 結束。")
                return
            self.diagnostic_dialog = None
            self.lifecycle_dialog = None
            dialog.close()
            self._sync_controls()
        start.clicked.connect(run)
        cancel.clicked.connect(self.client.cancel.set)
        export.clicked.connect(export_report)
        close.clicked.connect(close_dialog)
        dialog.finished.connect(lambda _code: (setattr(self, "diagnostic_dialog", None), setattr(self, "lifecycle_dialog", None)))
        dialog.show()

    # ---------- system SSH / Sysrepo backup and restore ----------

    def _admin_shell_for_operation(self):
        shell = self.admin_connection
        if not shell or not shell.connected:
            raise EditError("請先在『系統SSH』分頁連線。")
        settings = self._admin_settings()
        if shell.settings != settings:
            raise EditError("系統 SSH 欄位已改變，請中斷並重新連線。")
        return shell, settings

    def _system_backup_request(self):
        stores = ["running"]
        if self.checks["backup_candidate"].isChecked():
            stores.append("candidate")
        if self.checks["backup_startup"].isChecked():
            stores.append("startup")
        return system_backup.prepare(base=self._text("backup_base"),
                                     sysrepocfg=self._text("admin_program"),
                                     sysrepoctl=self._text("backup_sysrepoctl"),
                                     init_module=self._text("backup_init_module"),
                                     datastores=stores)

    @staticmethod
    def _script_text(stdout, stderr):
        if isinstance(stdout, bytes):
            raw = stdout + (b"\n" if stderr else b"") + (stderr if isinstance(stderr, bytes) else str(stderr).encode())
            return raw.decode("utf-8", errors="replace")
        return str(stdout) + ("\n" if stderr else "") + str(stderr)

    def _system_script_timeout(self, restore=False):
        timeout = int(self._text("admin_timeout") or "10")
        return max(60 if restore else 30, timeout + (60 if restore else 30))

    def check_yang_initialization(self):
        if self.busy:
            return
        try:
            shell, settings = self._admin_shell_for_operation()
            request = self._system_backup_request()
        except Exception as exc:
            self.show_error(exc)
            return
        def work(_progress):
            if shell.settings != settings:
                raise EditError("系統 SSH 設定已改變；不會執行遠端命令。")
            return shell.run(system_backup.shell_command(),
                             system_backup.initialization_script(request).encode("utf-8"),
                             timeout=self._system_script_timeout())
        def done(result):
            text = self._script_text(*result)
            if "NCC_YANG_INITIALIZED=" not in text:
                raise EditError("遠端沒有確認 YANG module 初始化。")
            self.backup_status.setText("YANG 初始化已確認：%s" % request.init_module)
            self.status_label.setText("系統 SSH 已確認 YANG module %s 存在；尚未備份或還原。" % request.init_module)
        self._run("系統備份：檢查初始化…", work, done)

    def create_system_backup(self):
        if self.busy:
            return
        try:
            shell, settings = self._admin_shell_for_operation()
            request = self._system_backup_request()
        except Exception as exc:
            self.show_error(exc)
            return
        def work(_progress):
            if shell.settings != settings:
                raise EditError("系統 SSH 設定已改變；不會執行遠端命令。")
            return shell.run(system_backup.shell_command(), system_backup.backup_script(request).encode("utf-8"),
                             timeout=self._system_script_timeout())
        def done(result):
            output = self._script_text(*result)
            directory = system_backup.parse_marker(output, "NCC_BACKUP_DIR", request.base)
            stores = system_backup.marker_value(output, "NCC_BACKUP_DATASTORES") if "NCC_BACKUP_DATASTORES=" in output else ",".join(request.datastores)
            self.backup_status.setText("已建立遠端備份：%s（%s）" % (directory, stores))
            self.status_label.setText("遠端 Sysrepo 備份完成；running XML、module 清單與 SHA256 已保存。")
        self._run("系統備份：建立…", work, done)

    def restore_system_backup(self):
        if self.busy:
            return
        if self.client.connected and not self.demo:
            self.status_label.setText("還原前請先中斷 NETCONF，避免停止 netopeer2-server 時留下連線結果待確認。")
            return
        try:
            shell, settings = self._admin_shell_for_operation()
            request = self._system_backup_request()
        except Exception as exc:
            self.show_error(exc)
            return
        def work(_progress):
            result = shell.run(system_backup.shell_command(), system_backup.latest_script(request).encode("utf-8"),
                               timeout=self._system_script_timeout())
            output = self._script_text(*result)
            return system_backup.parse_marker(output, "NCC_LATEST_BACKUP_DIR", request.base), output
        self._run("系統備份：尋找最新…", work,
                  lambda result: self._confirm_system_restore(result, shell, settings, request))

    def _confirm_system_restore(self, result, shell, settings, request):
        latest, output = result
        dialog = QDialog(self)
        self.lifecycle_dialog = dialog
        dialog.setWindowTitle("確認還原最新 Sysrepo 備份")
        dialog.resize(1000, 650)
        layout = QVBoxLayout(dialog)
        info = QPlainTextEdit()
        info.setReadOnly(True)
        info.setPlainText("系統 SSH：%s@%s:%s\n遠端 BASE：%s\n\n將還原最新備份：\n%s\n\n"
                         "已先完成 SHA256 驗證；執行時會再次確認最新目錄與 checksum。\n"
                         "會停止可能修改 Sysrepo 的服務：\n  %s\n\n最新備份檢查輸出：\n%s" %
                         (settings.username, settings.host, settings.port, request.base, latest,
                          "\n  ".join(system_backup.STOP_SERVICES), output[-6000:]))
        layout.addWidget(info, 1)
        acknowledged = QCheckBox("我確認主機、備份版本、服務停止／重啟行為，並有系統管理授權。")
        layout.addWidget(acknowledged)
        controls = QHBoxLayout()
        cancel = QPushButton("取消")
        send = QPushButton("確認停止服務並還原 running")
        send.setObjectName("sysrepoButton")
        controls.addStretch(1)
        controls.addWidget(cancel)
        controls.addWidget(send)
        layout.addLayout(controls)
        def close_dialog():
            self.lifecycle_dialog = None
            dialog.reject()
            self._sync_controls()
        def restore():
            if not acknowledged.isChecked():
                QMessageBox.information(dialog, "確認還原", "請先核對備份版本與服務行為，並勾選確認。")
                return
            if not shell.connected or shell.settings != self._admin_settings():
                self.show_error(EditError("系統 SSH 已中斷或欄位已改變；請重新連線並重新尋找最新備份。"))
                return
            if QMessageBox.question(dialog, "送出 Sysrepo 還原", "確定停止服務並將最新備份覆寫到 running？\n還原後必須重新連線 NETCONF 並重新讀取。",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            close_dialog()
            self.admin_requires_refresh = True
            self.uncertain = True
            def work(_progress):
                return shell.run(system_backup.shell_command(),
                                  system_backup.restore_script(request, latest).encode("utf-8"),
                                  timeout=self._system_script_timeout(restore=True))
            def done(result):
                output2 = self._script_text(*result)
                applied = system_backup.parse_marker(output2, "NCC_RESTORE_APPLIED", request.base)
                self.backup_status.setText("已還原 running：%s；服務已嘗試恢復。" % applied)
                self.status_label.setText("系統 Sysrepo 還原完成；請重新連線 NETCONF 並重新讀取 running。")
            self._run("系統備份：還原中…", work, done)
        cancel.clicked.connect(close_dialog)
        send.clicked.connect(restore)
        dialog.finished.connect(lambda _code: setattr(self, "lifecycle_dialog", None))
        dialog.show()

    # ---------- details/error/lifecycle ----------

    def show_details(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("NETCONF / YANG details")
        dialog.resize(900, 600)
        layout = QVBoxLayout(dialog)
        edit = self._output_edit()
        lines = self.client.context.status_lines() if self.client.context is not None else ["Offline / Demo"]
        lines += ["", "YANG modules: %d" % self.client.schema.module_count,
                  "Schema complete: " + str(self.client.schema.complete), "", *self.client.schema.warnings]
        if self.client.connected and not self.demo:
            lines += ["", "Capabilities:", *self.client.capabilities]
        edit.setPlainText("\n".join(lines))
        layout.addWidget(edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def show_error(self, exc):
        text = redact_secrets(str(exc))
        self.status_label.setText("操作失敗：" + text[:300])
        if not self.closed:
            QMessageBox.critical(self, "NETCONF", text)

    def _sync_controls(self):
        connected = bool(self.client.connected)
        idle = not self.busy
        setup_idle = idle and not connected and self.reconnect_due is None
        for widget in (self.profile_box, self.account_box, self.save_profile_button,
                       self.new_profile_button, self.manage_profile_button):
            widget.setEnabled(setup_idle)
        self.mode_box.setEnabled(setup_idle)
        self.connect_button.setEnabled(idle and not connected)
        self.connect_button.setProperty("connected", connected)
        self.connect_button.setText("已連線" if connected else "連線 / 開始監測")
        self.connect_button.style().unpolish(self.connect_button)
        self.connect_button.style().polish(self.connect_button)
        self.disconnect_button.setEnabled(connected or self.busy or self.reconnect_due is not None)
        self.stop_reconnect_button.setEnabled(bool(self.reconnect_enabled or self.reconnect_due is not None
                                                  or self.job_name == "自動重新連線…"))
        has_rpc = self.plan is not None and self.plan.rpc is not None
        can_rpc = connected and idle and has_rpc and not self.uncertain and self.snapshot is not None
        self.netconf_button.setEnabled(can_rpc and self.snapshot.options.source != "startup")
        self.sysrepo_button.setEnabled(idle and bool(self.admin_connection and self.admin_connection.connected)
                                       and can_rpc and self.snapshot.options.source == "running"
                                       and not self.admin_requires_refresh)
        self.read_button.setEnabled(connected and idle)
        self.tree_schema_button.setEnabled(connected and idle)
        self.refresh_schema_button.setEnabled(connected and idle)
        self.tree_export_button.setEnabled(bool(self.snapshot) and idle)
        self.checks["defaults"].setEnabled(idle)
        self.checks["state"].setEnabled(idle and self.source_box.currentText() == "running")
        self.checks["show_candidates"].setEnabled(idle)
        selection_editable = self.selection is not None and not self.selection.delete
        self.add_child_button.setEnabled(idle and selection_editable and not self._draft_guard().startswith("此範圍"))
        self.add_root_button.setEnabled(idle and self.snapshot is not None)
        self.delete_button.setEnabled(idle and selection_editable)
        for button in (self.pretty_button, self.revert_button, self.refresh_selected_button,
                       self.export_editor_button):
            button.setEnabled(idle and self.selection is not None)
        self.editor.setReadOnly(not (idle and selection_editable))
        admin_online = bool(self.admin_connection and self.admin_connection.connected)
        self.admin_connect_button.setEnabled(idle and not admin_online)
        self.admin_disconnect_button.setEnabled(idle and admin_online)
        if admin_online:
            self.admin_connect_button.setText("系統 SSH 已連線")
        else:
            self.admin_connect_button.setText("連線系統 SSH")
        self.admin_connect_button.setProperty("connected", admin_online)
        self.admin_connect_button.style().unpolish(self.admin_connect_button)
        self.admin_connect_button.style().polish(self.admin_connect_button)
        backup_ready = idle and admin_online and not self.admin_requires_refresh
        for button in (self.backup_check_button, self.backup_create_button, self.backup_restore_button):
            button.setEnabled(backup_ready)
        self.subscribe_button.setEnabled(connected and idle and self.notification_manager is None and not self.demo
                                         and any(":notification:" in str(c) for c in self.client.capabilities))
        self.stop_subscription_button.setEnabled(self.notification_manager is not None and idle)
        self.reread_result_button.setEnabled(self.last_attempt is not None and connected and idle)
        self._sync_drafts()

    def load_demo(self):
        from .demo import DemoClient
        self.client = DemoClient()
        self.demo = True
        self.checks["defaults"].setChecked(True)
        self.checks["state"].setChecked(True)
        self.setWindowTitle("NETCONF Console GUI %s · PySide6 · DEMO / NO NETWORK" % VERSION)
        self._accept_snapshot(self.client.read(self._options()))
        self._sync_controls()

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, "正在執行", "請等待目前操作完成後再關閉。")
            event.ignore()
            return
        if (self.persist and (self.dirty or self.drafts.entries)
                and not self._preserve_current_draft(flush=True)):
            event.ignore()
            return
        self.closed = True
        self.reconnect_timer.stop()
        self.notification_timer.stop()
        self.draft_timer.stop()
        self.preference_timer.stop()
        self.editor_timer.stop()
        try:
            self.notification_manager = None
            if self.admin_connection:
                self.admin_connection.close()
            if self.client.connected:
                self.client.disconnect()
        finally:
            self.save_preferences()
        event.accept()


def build_application():
    """Create a Qt application with a native Windows style where available."""
    app = QApplication.instance() or QApplication(sys.argv)
    if os.name == "nt" and "WindowsVista" in QStyleFactory_keys():
        app.setStyle("WindowsVista")
    elif "Fusion" in QStyleFactory_keys():
        app.setStyle("Fusion")
    app.setStyleSheet(
        """
        QMainWindow, QWidget { background: #f3f6fa; color: #243247; }
        QGroupBox { border: 1px solid #c7d2df; border-radius: 4px; margin-top: 8px; padding-top: 8px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; font-weight: 600; }
        QTabWidget::pane { border: 1px solid #c7d2df; background: #f3f6fa; }
        QTabBar::tab { background: #dbe4ef; border: 1px solid #94a3b8; padding: 7px 14px; margin-right: 2px; font-weight: 600; }
        QTabBar::tab:selected { background: #0969da; color: white; }
        QTabBar::tab:hover:!selected { background: #bfdbfe; }
        QPushButton { padding: 5px 10px; }
        QPushButton#connectButton { background: #0969da; color: white; border: 1px solid #0756b3; font-weight: 700; padding: 5px 10px; }
        QPushButton#connectButton:disabled { background: #d8e1ec; color: #708096; }
        QPushButton#connectButton[connected="true"], QPushButton#connectButton[connected="true"]:disabled { background: #15803d; color: white; border: 1px solid #166534; }
        QPushButton#adminConnectButton { background: #0969da; color: white; font-weight: 700; }
        QPushButton#adminConnectButton:disabled { background: #d8e1ec; color: #708096; }
        QPushButton#adminConnectButton[connected="true"], QPushButton#adminConnectButton[connected="true"]:disabled { background: #15803d; color: white; border: 1px solid #166534; }
        QPushButton#netconfButton { background: #15803d; color: white; font-weight: 700; padding: 7px 14px; }
        QPushButton#netconfButton:disabled { background: #d8e1ec; color: #708096; }
        QPushButton#sysrepoButton { background: #b91c1c; color: white; font-weight: 700; padding: 7px 14px; }
        QPushButton#sysrepoButton:disabled { background: #d8e1ec; color: #708096; }
        QPushButton#backupCreateButton { background: #2563eb; color: white; font-weight: 700; }
        QPushButton#backupRestoreButton { background: #b91c1c; color: white; font-weight: 700; }
        QPushButton#backupCreateButton:disabled, QPushButton#backupRestoreButton:disabled { background: #d8e1ec; color: #708096; }
        QPushButton#dangerButton, QPushButton#deleteNodeButton { background: #b91c1c; color: white; font-weight: 700; }
        QPushButton#dangerButton:disabled, QPushButton#deleteNodeButton:disabled { background: #d8e1ec; color: #708096; }
        QPushButton#warningButton { background: #b45309; color: white; font-weight: 600; }
        QPushButton#warningButton:disabled { background: #d8e1ec; color: #708096; }
        QPushButton#toggleConnectionButton { background: #111827; color: white; border: 1px solid #030712; border-radius: 3px; font-weight: 700; padding: 5px 22px; }
        QPushButton#toggleConnectionButton:hover { background: #374151; }
        QLabel#sectionTitle { font-size: 14px; font-weight: 700; }
        QLabel#treeFooter { color: #526679; padding-top: 3px; }
        QPlainTextEdit { background: white; border: 1px solid #c7d2df; selection-background-color: #beddf2; }
        QTreeWidget { background: white; border: 1px solid #c7d2df; }
        QMenu { background: white; color: #243247; border: 1px solid #94a3b8; padding: 3px; }
        QMenu::item { padding: 6px 24px 6px 10px; }
        QMenu::item:selected { background: #0969da; color: white; }
        QMenu::item:disabled { color: #94a3b8; }
        QMenu::separator { height: 1px; background: #cbd5e1; margin: 3px 6px; }
        """
    )
    return app


def QStyleFactory_keys():
    from PySide6.QtWidgets import QStyleFactory
    return QStyleFactory.keys()


def install_qt_exception_hook():
    """Keep an unexpected Qt callback exception from terminating a frozen GUI."""
    previous = sys.excepthook

    def handle(exc_type, exc, traceback):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            previous(exc_type, exc, traceback)
            return
        try:
            message = "%s：%s" % (exc_type.__name__, redact_secrets(str(exc)))
            QMessageBox.critical(None, "NETCONF GUI 未預期錯誤", message + "\n\n程式會保持開啟；請保存 XML 草稿後再重試。")
        except Exception:
            # Last resort only; never recurse through the replacement hook.
            pass

    sys.excepthook = handle
    return previous


def self_test(window):
    required = {
        "connection_modes": list(MODES),
        "settings_tabs": [window.tabs.tabText(i) for i in range(window.tabs.count())],
        "output_tabs": [window.output_tabs.tabText(i) for i in range(window.output_tabs.count())],
        "source_default": window.source_box.currentText(),
        "ssh_hostkey_default": window.checks["hostkey_verify"].isChecked(),
        "tls_hostname_default": window.checks["verify_hostname"].isChecked(),
        "main_splitter": isinstance(window.centralWidget().findChild(QSplitter), QSplitter),
        "tree": window.tree is not None,
        "xml_editor": window.editor is not None,
        "preview": window.preview is not None,
        "creation_picker": CreationDialog.__doc__ is not None,
    }
    passed = (required["source_default"] == "running"
              and required["settings_tabs"][:1] == ["NETCONF連線"]
              and len(required["settings_tabs"]) == 8
              and len(required["output_tabs"]) == 6
              and required["ssh_hostkey_default"] is False
              and required["tls_hostname_default"] is False
              and required["main_splitter"] and required["tree"]
              and required["xml_editor"] and required["preview"]
              and required["creation_picker"])
    return {"passed": passed, "qt": "PySide6", "version": VERSION, "checks": required}


def main(argv=None):
    parser = argparse.ArgumentParser(description="PySide6 NETCONF XML browser/editor")
    parser.add_argument("--demo", action="store_true", help="Open safe offline demonstration data; no network")
    parser.add_argument("--self-test", metavar="REPORT_JSON", help="Run Qt UI diagnostics without a remote connection")
    args = parser.parse_args(argv)
    set_app_id()
    app = build_application()
    previous_hook = install_qt_exception_hook()
    window = QtMainWindow(persist=not (args.demo or args.self_test))
    if args.demo:
        window.load_demo()
    if args.self_test:
        report = self_test(window)
        Path(args.self_test).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        window.close()
        sys.excepthook = previous_hook
        return 0 if report["passed"] else 1
    window.show()
    # Re-centre after native window decorations have been created so the
    # visible frame, not only the client area, is centred on Windows.
    window.center_on_screen()
    try:
        return app.exec()
    finally:
        sys.excepthook = previous_hook


if __name__ == "__main__":
    raise SystemExit(main())
