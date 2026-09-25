"""Notification unread badge painted over the tab's upper right corner."""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QTabBar


class NotificationTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._badge_index = -1
        self._unread_count = 0
        self.setExpanding(False)

    def set_unread_count(self, index, count):
        changed_tab = self._badge_index != index
        self._badge_index = index
        self._unread_count = max(0, int(count))
        if changed_tab:
            self.updateGeometry()
        self.update()

    def tabSizeHint(self, index):  # noqa: N802 - Qt API
        size = super().tabSizeHint(index)
        if index == self._badge_index:
            # Keep the title clear of the badge, including at larger font sizes.
            # Reserve this space even when read, so tabs don't jump on arrival.
            size.setWidth(size.width() + 24)
            size.setHeight(max(24, size.height()))
        return size

    def paintEvent(self, event):  # noqa: N802 - Qt API
        super().paintEvent(event)
        if self._unread_count <= 0 or not 0 <= self._badge_index < self.count():
            return
        tab = self.tabRect(self._badge_index)
        if not tab.intersects(self.rect()) or not self.isTabVisible(self._badge_index):
            return
        badge = QRect(tab.right() - 23, tab.top() + 1, 22, 22)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(tab.intersected(self.rect()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#dc2626"))
        painter.drawEllipse(badge)
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        text = str(self._unread_count) if self._unread_count <= 99 else "99+"
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
