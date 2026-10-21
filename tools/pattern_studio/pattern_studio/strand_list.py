"""Sidebar: list of strands in the current session, with add/remove.

The list is multi-select (Ctrl/Shift-click, or Select All) so a set of strands
can be edited as one group, MainWindow shows the *anchor* (current row) in
the inspector and replays each edit onto the rest of the selection. Selection
therefore has two parts callers care about: `current_row()` (what's displayed)
and `selected_rows()` (what an edit applies to).
"""

from __future__ import annotations

import random

from PySide6.QtCore import QItemSelectionModel, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from . import theme

#: Cached across rows and repaints - see _dither_tile().
_DITHER_TILE: QPixmap | None = None


def _dither_tile() -> QPixmap:
    """A tiling speck pattern, a couple of levels bright, ~40% coverage.

    Qt fills a gradient from a 1024-entry table rounded to 8 bits and does no
    dithering of its own. That is fine for a steep ramp, and visibly wrong for
    a shallow one: across a selected row the green channel only travels from
    85 to 34, so it lands as a handful of 24px-wide flat bands with hard
    edges between them.

    The pattern is seeded, not random per call: a tile that changed between
    repaints would make selected rows crawl.
    """
    global _DITHER_TILE
    if _DITHER_TILE is None:
        rng = random.Random(0x115B10)
        image = QImage(64, 64, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        speck = QColor(255, 255, 255, 5)
        for y in range(image.height()):
            for x in range(image.width()):
                if rng.random() < 0.4:
                    image.setPixelColor(x, y, speck)
        _DITHER_TILE = QPixmap.fromImage(image)
    return _DITHER_TILE


class RowDelegate(QStyledItemDelegate):
    """Paints selected rows: the wash, the accent bar, and the group bloom.

    Shared by every list that supports group selection - the strands here,
    and a splice mask's custom regions - so a group looks the same wherever
    one can be made.

    This lives in a delegate rather than in the stylesheet because neither of
    those last two is expressible in QSS (there is no dithering and no
    box-shadow) and because a QSS `::item:selected` background would paint
    straight over whatever we drew underneath it.
    """

    #: Row corner radius, matched to the stylesheet's `::item` rule.
    RADIUS = 5
    #: Width of the accent bar down the left edge. The `::item` rule reserves
    #: exactly this much as a transparent border, so text never shifts.
    BAR_W = 3

    #: How far the painted row sits inside its item rect. Rows are
    #: contiguous and full-width, so without this inset a halo would have
    #: nowhere to go: the view clips at the viewport and the neighboring row
    #: covers the rest. Insetting turns each selected row into a lit chip
    #: with a gap around it for the glow to fill.
    INSET_X = 2.0
    INSET_Y = 1.5

    #: Halo layers around a group-selected row, outermost first:
    #: (grow_px, alpha). Only drawn while several strands move together,
    #: the same signal the canvas gives by breathing its outline.
    GROUP_BLOOM = ((4.5, 18), (2.0, 30))

    def __init__(self, parent=None):
        super().__init__(parent)
        self._group = False

    def set_group(self, group: bool) -> None:
        self._group = group

    def _wash(self, rect: QRectF, hovered: bool) -> QLinearGradient:
        """Violet at the accent bar, thinning out across the row.

        One hue family rather than violet -> blue -> cyan: a hue that travels
        while the alpha falls makes some channels crawl and others race, and
        the slow ones are what band.
        """
        lift = 1.35 if hovered else 1.0
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0.0, QColor(168, 85, 247, int(90 * lift)))
        gradient.setColorAt(0.45, QColor(139, 92, 246, int(34 * lift)))
        gradient.setColorAt(1.0, QColor(124, 58, 237, int(14 * lift)))
        return gradient

    def paint(self, painter, option, index) -> None:  # noqa: N802 (Qt override)
        if not option.state & QStyle.State_Selected:
            super().paint(painter, option, index)
            return

        rect = QRectF(option.rect).adjusted(
            self.INSET_X, self.INSET_Y, -self.INSET_X, -self.INSET_Y
        )
        path = QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
        hovered = bool(option.state & QStyle.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        if self._group:
            # Clipped to everything *outside* the row body, so the halo rings
            # the row instead of also brightening it: these are filled
            # shapes, and unclipped they would add their alpha across the
            # whole row on top of the wash.
            surround = QPainterPath()
            surround.addRect(QRectF(option.rect))
            painter.setClipPath(surround.subtracted(path))
            painter.setCompositionMode(QPainter.CompositionMode_Plus)
            for grow, alpha in self.GROUP_BLOOM:
                painter.setBrush(QColor(168, 85, 247, alpha))
                painter.drawRoundedRect(
                    rect.adjusted(-grow, -grow, grow, grow),
                    self.RADIUS + grow,
                    self.RADIUS + grow,
                )
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setClipping(False)

        painter.setBrush(self._wash(rect, hovered))
        painter.drawPath(path)

        painter.setClipPath(path)
        painter.drawTiledPixmap(rect.toAlignedRect(), _dither_tile())
        painter.setClipping(False)

        painter.setBrush(QColor(theme.ACCENT_HI if hovered else theme.ACCENT))
        painter.drawRoundedRect(
            QRectF(rect.left(), rect.top(), self.BAR_W, rect.height()), 1.5, 1.5
        )
        painter.restore()

        # Hand the text to the base delegate with both state flags cleared:
        # left set, the stylesheet would repaint its own selected/hover
        # background over everything above.
        text_option = QStyleOptionViewItem(option)
        text_option.state &= ~QStyle.State_Selected
        text_option.state &= ~QStyle.State_MouseOver
        text_option.palette.setColor(QPalette.Text, QColor("#FFFFFF"))
        super().paint(painter, text_option, index)


def restore_selection(view: QListWidget, rows: list[int], current: int) -> None:
    """Put back a group selection: exactly @p rows, anchored on @p current.

    The anchor goes back without being selected. Qt lets the current row sit
    outside the selection - Ctrl-click it off the group and it stays current -
    and setCurrentRow() would quietly select it again, so the next edit would
    reach a row the user had just taken out. Rows out of range are skipped;
    blocking the view's signals is left to the caller.
    """
    count = view.count()
    if 0 <= current < count:
        view.selectionModel().setCurrentIndex(
            view.model().index(current, 0), QItemSelectionModel.NoUpdate)
    for row in rows:
        if 0 <= row < count:
            view.item(row).setSelected(True)


class _GroupListWidget(QListWidget):
    """A QListWidget that reports each click or key press as one change.

    Qt applies a click in two steps, each with its own signal: the current row
    moves first, while the old row is still the one selected, and only then
    does the selection follow. Forwarded as they came, the window ran its
    whole selection handler on the half-way state - reloading the strand
    being left - and then again for the one clicked. A key press sends the
    same two the other way round, both on the finished state. Either way
    they are held here until Qt is done with the event, and reported once.
    """

    #: The current row and/or the selection changed.
    settled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._holding = 0
        self._pending = False
        self.itemSelectionChanged.connect(self._on_changed)
        # currentRowChanged as well: Ctrl+arrow moves the anchor without
        # changing the selection, and the anchor is what the inspector shows.
        self.currentRowChanged.connect(self._on_changed)

    def _on_changed(self, *_args) -> None:
        if self._holding:
            self._pending = True
        else:
            self.settled.emit()

    def _held(self, handler, *args) -> None:
        """Run @p handler with the signals held, then report once if anything moved."""
        self._holding += 1
        try:
            handler(*args)
        finally:
            self._holding -= 1
        if not self._holding and self._pending:
            self._pending = False
            self.settled.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._held(super().mousePressEvent, event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._held(super().mouseMoveEvent, event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._held(super().mouseReleaseEvent, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._held(super().keyPressEvent, event)

    def setCurrentRow(self, row: int, *args) -> None:  # noqa: N802 (Qt override)
        self._held(super().setCurrentRow, row, *args)


class StrandListPanel(QWidget):
    #: The anchor row and/or the set of selected rows changed.
    selection_changed = Signal()
    add_requested = Signal()
    #: Remove every currently selected strand.
    remove_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 4, 8)
        layout.setSpacing(8)

        title = QLabel("STRANDS")
        title.setProperty("role", "sectionHeader")
        layout.addWidget(title)

        self.list_widget = _GroupListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._row_delegate = RowDelegate(self.list_widget)
        self.list_widget.setItemDelegate(self._row_delegate)
        layout.addWidget(self.list_widget)

        select_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        select_row.addWidget(self.select_all_btn)
        self.group_label = QLabel()
        self.group_label.setProperty("role", "groupCount")
        self.group_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        select_row.addWidget(self.group_label, 1)
        layout.addLayout(select_row)

        btn_row = QHBoxLayout()
        # "Add" rather than "Add Strand": with an icon in front, the longer
        # label plus "Remove" exceeds the sidebar width, and the
        # STRANDS heading directly above already says what's being added.
        self.add_btn = QPushButton(theme.icon("plus"), " Add")
        self.add_btn.setProperty("role", "primary")
        self.add_btn.setToolTip("Add a new strand")
        self.remove_btn = QPushButton(theme.icon("minus"), " Remove")
        self.remove_btn.setProperty("role", "danger")
        self.remove_btn.setToolTip("Remove every selected strand")
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.remove_btn)
        layout.addLayout(btn_row)

        self.add_btn.clicked.connect(self.add_requested)
        self.remove_btn.clicked.connect(self.remove_requested)
        self.select_all_btn.clicked.connect(self.select_all)
        self.list_widget.settled.connect(self.selection_changed)

        self.set_group_size(0)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def current_row(self) -> int:
        """The anchor row - the strand whose values the inspector displays."""
        return self.list_widget.currentRow()

    def selected_rows(self) -> list[int]:
        """Every selected row, ascending. An edit applies to all of them."""
        return sorted(index.row() for index in self.list_widget.selectedIndexes())

    def select(self, row: int) -> None:
        """Select exactly `row`, dropping any wider group selection.

        Reported once, from the finished state: left to the list's own
        signals, the empty selection in between and then the row itself would
        run the window's selection handler three times over.
        """
        self.list_widget.blockSignals(True)
        self.list_widget.clearSelection()
        self.list_widget.setCurrentRow(row)
        self.list_widget.blockSignals(False)
        self.selection_changed.emit()

    def select_all(self) -> None:
        self.list_widget.selectAll()

    def select_rows(self, rows: list[int], current: int = -1) -> None:
        """Restore a whole group selection, anchor included.

        Used by undo, which has to put the user back exactly where they were:
        reversing an edit to strand 3 and landing on strand 1 reads as though
        nothing happened. Signals are blocked because the caller re-runs the
        selection handler itself once, rather than once per row touched here.
        """
        self.list_widget.blockSignals(True)
        self.list_widget.clearSelection()
        restore_selection(self.list_widget, rows, current)
        self.list_widget.blockSignals(False)

    def set_group_size(self, count: int) -> None:
        self.group_label.setText(f"{count} selected" if count > 1 else "")
        self._row_delegate.set_group(count > 1)
        self.list_widget.viewport().update()

    # ------------------------------------------------------------------
    # Contents
    # ------------------------------------------------------------------

    def set_names(self, names: list[str]) -> None:
        selected = self.selected_rows()
        current = self.list_widget.currentRow()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self.list_widget.addItems(names)
        restore_selection(self.list_widget, selected, current)
        self.list_widget.blockSignals(False)
