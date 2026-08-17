"""Sidebar: list of strands in the current session, with add/remove."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QPushButton, QVBoxLayout, QWidget


class StrandListPanel(QWidget):
    selection_changed = Signal(int)
    add_requested = Signal()
    remove_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("Add Strand")
        self.remove_btn = QPushButton("Remove")
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.remove_btn)
        layout.addLayout(btn_row)

        self.add_btn.clicked.connect(self.add_requested)
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        self.list_widget.currentRowChanged.connect(self.selection_changed)

    def _on_remove_clicked(self) -> None:
        row = self.list_widget.currentRow()
        if row >= 0:
            self.remove_requested.emit(row)

    def set_names(self, names: list[str]) -> None:
        current = self.list_widget.currentRow()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self.list_widget.addItems(names)
        if 0 <= current < len(names):
            self.list_widget.setCurrentRow(current)
        self.list_widget.blockSignals(False)

    def select(self, row: int) -> None:
        self.list_widget.setCurrentRow(row)
