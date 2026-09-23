"""Read-only sysrepocfg DATA TREE view."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem

from .i18n import translate as tr
from .model import Selection, children, node_style
from .tree_visibility import visibility
from .workspace import instance_path, selections

ROLE = Qt.ItemDataRole.UserRole
MISSING_COLOR = "#b42332"
UNKNOWN_COLOR = "#667085"
STYLE_COLORS = {
    "default": "#fff0bf",
    "schema_default": "#fff8de",
    "state": "#e4edf5",
    "changed": "#d6f5d6",
}


class SysrepoTreePanel(QWidget):
    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.label = label
        self.data = None
        self.netconf_data = None
        self.schema = None
        self.states = {}
        self.populate_candidates = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemExpanded.connect(self.expand)
        layout.addWidget(self.tree, 1)

    def load(self, data, netconf_data, schema, query="", show_candidates=False):
        self.data, self.netconf_data, self.schema = data, netconf_data, schema
        self.states = visibility(data, netconf_data, schema)
        self.rebuild(query, show_candidates)

    def clear(self):
        self.data = None
        self.netconf_data = None
        self.states = {}
        self.tree.clear()

    def make_item(self, parent, selection, lazy=True):
        item = QTreeWidgetItem(parent, [self.label(selection)])
        item.setData(0, ROLE, selection)
        state = self.states.get(selection.node, "unknown")
        if state == "missing":
            # A node present in sysrepocfg but absent from the NETCONF read is
            # deliberately a red *text* warning; it may be NACM/default/
            # conditional data, not proof that the device lacks the node.
            item.setForeground(0, QBrush(QColor(MISSING_COLOR)))
        elif state != "visible":
            item.setForeground(0, QBrush(QColor(UNKNOWN_COLOR)))
        else:
            color = STYLE_COLORS.get(node_style(selection.node, selection.path, self.schema))
            if color:
                item.setBackground(0, QBrush(QColor(color)))
        if lazy and children(selection.node):
            QTreeWidgetItem(item, ["…"])
        return item

    def expand(self, item):
        if not item.childCount() or item.child(0).data(0, ROLE) is not None:
            return
        item.takeChild(0)
        selection = item.data(0, ROLE)
        if not isinstance(selection, Selection):
            item.setExpanded(False)
            return
        for child in children(selection.node):
            self.make_item(item, Selection(child, (*selection.ancestors, selection.node)))
        self._add_candidates(item, selection.node, selection.path)

    def _add_candidates(self, parent_item, parent_node, path):
        if self.populate_candidates is not None:
            self.populate_candidates(parent_item, parent_node, path)

    def rebuild(self, query="", show_candidates=False):
        self.tree.clear()
        if self.data is None:
            return 0
        query = str(query).strip().casefold()
        if not query:
            for node in children(self.data):
                self.make_item(self.tree, Selection(node))
            if show_candidates:
                self._add_candidates(self.tree, self.data, ())
            return 0
        # Search the full XML, not just already-expanded widgets; cap matches.
        included = set()
        matches = 0
        for selection in selections(self.data):
            info = self.schema.lookup(selection.path)
            text = instance_path(selection, self.schema) + " " + (selection.node.text or "")
            if info:
                text += " " + (info.description or "")
            if query in text.casefold():
                included.update((*selection.ancestors, selection.node))
                matches += 1
                if matches >= 500:
                    break

        def add(parent, node, ancestors):
            if node not in included:
                return
            item = self.make_item(parent, Selection(node, ancestors), lazy=False)
            for child in children(node):
                add(item, child, (*ancestors, node))
            item.setExpanded(True)

        for node in children(self.data):
            add(self.tree, node, ())
        if not matches:
            empty = QTreeWidgetItem(self.tree, [tr("找不到符合「%s」的節點" % query)])
            empty.setData(0, ROLE, "tree-search-empty")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            empty.setForeground(0, QBrush(QColor(UNKNOWN_COLOR)))
        return matches
