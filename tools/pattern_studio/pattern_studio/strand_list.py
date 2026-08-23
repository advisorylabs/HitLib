"""Sidebar: list of strands in the current session, with add/remove.

The list is multi-select (Ctrl/Shift-click, or Select All) so a set of strands
can be edited as one group -- MainWindow shows the *anchor* (current row) in
the inspector and replays each edit onto the rest of the selection. Selection
therefore has two parts callers care about: `current_row()` (what's displayed)
and `selected_rows()` (what an edit applies to).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class StrandListPanel(QWidget):
    #: The anchor row and/or the set of selected rows changed.
    selection_changed = Signal()
    add_requested = Signal()
    #: Remove every currently selected strand.
    remove_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.list_widget)

        select_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        select_row.addWidget(self.select_all_btn)
        self.group_label = QLabel()
        font = self.group_label.font()
        font.setBold(True)
        self.group_label.setFont(font)
        select_row.addWidget(self.group_label, 1)
        layout.addLayout(select_row)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("Add Strand")
        self.remove_btn = QPushButton("Remove")
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.remove_btn)
        layout.addLayout(btn_row)

        self.add_btn.clicked.connect(self.add_requested)
        self.remove_btn.clicked.connect(self.remove_requested)
        self.select_all_btn.clicked.connect(self.select_all)
        self.list_widget.itemSelectionChanged.connect(self.selection_changed)
        # currentRowChanged as well: Ctrl+arrow moves the anchor without
        # changing the selection, and the anchor is what the inspector shows.
        self.list_widget.currentRowChanged.connect(self._on_current_row_changed)

        self.set_group_size(0)

    def _on_current_row_changed(self, _row: int) -> None:
        self.selection_changed.emit()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def current_row(self) -> int:
        """The anchor row -- the strand whose values the inspector displays."""
        return self.list_widget.currentRow()

    def selected_rows(self) -> list[int]:
        """Every selected row, ascending. An edit applies to all of them."""
        return sorted(index.row() for index in self.list_widget.selectedIndexes())

    def select(self, row: int) -> None:
        """Select exactly `row`, dropping any wider group selection."""
        self.list_widget.clearSelection()
        self.list_widget.setCurrentRow(row)

    def select_all(self) -> None:
        self.list_widget.selectAll()

    def set_group_size(self, count: int) -> None:
        self.group_label.setText(f"{count} selected" if count > 1 else "")

    # ------------------------------------------------------------------
    # Contents
    # ------------------------------------------------------------------

    def set_names(self, names: list[str]) -> None:
        selected = self.selected_rows()
        current = self.list_widget.currentRow()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self.list_widget.addItems(names)
        if 0 <= current < len(names):
            # Re-selects `current` on its own first, so restoring the rest of
            # the group below has to come after, not before.
            self.list_widget.setCurrentRow(current)
        for row in selected:
            if 0 <= row < len(names):
                self.list_widget.item(row).setSelected(True)
        self.list_widget.blockSignals(False)
