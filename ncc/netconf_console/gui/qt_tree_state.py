"""Instance-aware bookmarks for lazy NETCONF and sysrepocfg trees."""
from dataclasses import dataclass, field

from PySide6.QtCore import Qt

from .model import EditError, Selection, children, identity

ROLE = Qt.ItemDataRole.UserRole


def address(selection, schema):
    if not isinstance(selection, Selection):
        return ()
    result = []
    for depth, node in enumerate((*selection.ancestors, selection.node), 1):
        try:
            result.append(identity(node, schema, selection.path[:depth]))
        except EditError:
            break
    return tuple(result)


def nearest_selection(data, wanted, schema):
    """Resolve keys, never sibling ordinals; fall back to a surviving ancestor."""
    parent, ancestors, result = data, (), None
    for key in wanted:
        path = tuple(node.tag for node in ancestors) + (key[0],)
        matches = []
        for node in children(parent):
            if node.tag != key[0]:
                continue
            try:
                if identity(node, schema, path) == key:
                    matches.append(node)
            except EditError:
                pass
        if len(matches) != 1:
            break
        result = Selection(matches[0], ancestors)
        parent = matches[0]
        ancestors += (parent,)
    return result


@dataclass
class TreeState:
    selected: tuple = ()
    expanded: set = field(default_factory=set)
    vertical: int = 0
    horizontal: int = 0
    selected_visible: bool = False

    @classmethod
    def capture(cls, tree, schema, selection=None):
        current = tree.currentItem()
        if selection is None and current is not None:
            selection = current.data(0, ROLE)
        result = cls(address(selection, schema), vertical=tree.verticalScrollBar().value(),
                     horizontal=tree.horizontalScrollBar().value())
        if current is not None and isinstance(current.data(0, ROLE), Selection):
            result.selected_visible = (address(current.data(0, ROLE), schema) == result.selected
                                       and tree.viewport().rect().intersects(tree.visualItemRect(current)))
        def visit(item):
            value = item.data(0, ROLE)
            if isinstance(value, Selection) and item.isExpanded():
                result.expanded.add(address(value, schema))
            for index in range(item.childCount()):
                visit(item.child(index))
        for index in range(tree.topLevelItemCount()):
            visit(tree.topLevelItem(index))
        return result

    def restore(self, tree, schema, materialize, selection):
        selected = address(selection, schema)
        prefixes = {key[:depth] for key in self.expanded | {selected}
                    for depth in range(1, len(key) + 1)}
        found, states = [], []
        def visit(item):
            value = item.data(0, ROLE)
            if not isinstance(value, Selection):
                return
            key = address(value, schema)
            if key in prefixes:
                materialize(item)  # Populate lazy children without opening parents.
                for index in range(item.childCount()):
                    visit(item.child(index))
            states.append((item, key in self.expanded))
            if key == selected:
                found.append(item)
        for index in range(tree.topLevelItemCount()):
            visit(tree.topLevelItem(index))
        item = found[0] if len(found) == 1 else None
        if item is not None:
            tree.setCurrentItem(item)
        # Apply collapsed parents last so hidden expanded descendants stay hidden.
        for node, expanded in states:
            node.setExpanded(expanded)
        # Expanding lazy rows schedules a layout; calculate the new scrollbar
        # ranges now so restoring a value does not clamp it to the empty tree.
        tree.doItemsLayout()
        tree.verticalScrollBar().setValue(self.vertical)
        tree.horizontalScrollBar().setValue(self.horizontal)
        parent = item.parent() if item else None
        while parent is not None:
            if not parent.isExpanded():
                return item
            parent = parent.parent()
        if self.selected_visible and item is not None and not tree.viewport().rect().intersects(tree.visualItemRect(item)):
            tree.scrollToItem(item)
        return item
