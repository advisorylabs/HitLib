"""Small reusable widgets shared across panels."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QRegularExpression, QSize, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGradient,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import theme


def _qcolor(value: int) -> QColor:
    return QColor((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def _packed(color: QColor) -> int:
    return (color.red() << 16) | (color.green() << 8) | color.blue()


# ----------------------------------------------------------------------
# Color picker
# ----------------------------------------------------------------------
#
# Replaces QColorDialog, which brought forty-eight basic colors, sixteen
# custom slots, a screen picker, HSV and RGB spin boxes and an alpha channel
# to a strip of LEDs that has no alpha - all of it in Qt's own layout, which
# no stylesheet makes look like the rest of the app. What choosing an LED
# color actually takes is a hue, how washed out and how bright it is, and
# somewhere to paste a hex code.

#: Colors picked this session, newest first. Session-scoped on purpose: it
#: is a convenience for "the same red again", not a palette anyone manages.
_RECENT: list[int] = []
RECENT_MAX = 8


def remember_color(value: int) -> None:
    value &= 0xFFFFFF
    if value in _RECENT:
        _RECENT.remove(value)
    _RECENT.insert(0, value)
    del _RECENT[RECENT_MAX:]


def recent_colors() -> list[int]:
    return list(_RECENT)


class _SatValField(QWidget):
    """Saturation across, brightness down, for one hue.

    Painted as the hue, then white fading out left to right, then black
    fading in top to bottom - which is exactly HSV, not an approximation of
    it. The ramps are steep (every channel travels its full range across the
    field), so none of the banding a shallow gradient gets.
    """

    changed = Signal()
    RADIUS = 6.0
    #: The handle's radius, and how far in from the edge its travel stops,
    #: so it is never cut in half at a corner. The ramps start and end there
    #: too and run flat to the edge, so where the handle sits is exactly the
    #: color under it.
    HANDLE = 7.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(224, 148)
        self.setCursor(Qt.CrossCursor)
        self._h = 0.0
        self._s = 1.0
        self._v = 1.0

    def set_hsv(self, h: float, s: float, v: float) -> None:
        self._h, self._s, self._v = h, s, v
        self.update()

    def sat_val(self) -> tuple[float, float]:
        return self._s, self._v

    def _travel(self) -> QRectF:
        e = self.HANDLE
        return QRectF(e, e, self.width() - 2 * e, self.height() - 2 * e)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(box, self.RADIUS, self.RADIUS)
        travel = self._travel()

        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor.fromHsvF(self._h, 1.0, 1.0))
        white = QLinearGradient(travel.left(), 0, travel.right(), 0)
        white.setColorAt(0.0, QColor(255, 255, 255, 255))
        white.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(self.rect(), white)
        black = QLinearGradient(0, travel.top(), 0, travel.bottom())
        black.setColorAt(0.0, QColor(0, 0, 0, 0))
        black.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(self.rect(), black)
        painter.setClipping(False)

        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        # The handle is filled with the color it is sitting on and ringed
        # white over a dark halo, so it stays findable on any part of the
        # field - white on the washed-out corner, dark on the black edge.
        centre = QPointF(travel.left() + self._s * travel.width(),
                         travel.top() + (1.0 - self._v) * travel.height())
        painter.setPen(QPen(QColor(0, 0, 0, 150), 3.5))
        painter.drawEllipse(centre, self.HANDLE - 1, self.HANDLE - 1)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.75))
        painter.setBrush(QColor.fromHsvF(self._h, self._s, self._v))
        painter.drawEllipse(centre, self.HANDLE - 1, self.HANDLE - 1)
        painter.end()

    def _take(self, pos) -> None:
        travel = self._travel()
        self._s = min(1.0, max(0.0, (pos.x() - travel.left()) / travel.width()))
        self._v = min(1.0, max(0.0, 1.0 - (pos.y() - travel.top()) / travel.height()))
        self.update()
        self.changed.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton:
            self._take(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.buttons() & Qt.LeftButton:
            self._take(event.position())


class _HueBar(QWidget):
    """The hue, as a rainbow strip with a pill-shaped handle."""

    changed = Signal()
    BAR_H = 12
    #: How far in from each end the handle's travel stops - half the pill
    #: and its outline, so it is whole at hue 0 as well as mid-bar.
    END = 5.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(224, self.BAR_H + 6)
        self.setCursor(Qt.PointingHandCursor)
        self._h = 0.0

    def set_hue(self, h: float) -> None:
        self._h = h
        self.update()

    def hue(self) -> float:
        return self._h

    def _bar(self) -> QRectF:
        return QRectF(0.5, 3.5, self.width() - 1.0, self.BAR_H - 1.0)

    def _travel(self) -> tuple[float, float]:
        return self.END, self.width() - 2 * self.END

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bar = self._bar()
        left, span = self._travel()
        ramp = QLinearGradient(left, 0, left + span, 0)
        for i in range(7):
            ramp.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1.0, 1.0, 1.0))
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        painter.setBrush(ramp)
        painter.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)

        x = left + self._h * span
        pill = QRectF(x - 3.5, 0.5, 7.0, self.height() - 1.0)
        painter.setPen(QPen(QColor(0, 0, 0, 150), 3.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(pill, 3.5, 3.5)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.75))
        painter.setBrush(QColor.fromHsvF(self._h, 1.0, 1.0))
        painter.drawRoundedRect(pill, 3.5, 3.5)
        painter.end()

    def _take(self, pos) -> None:
        left, span = self._travel()
        # Stops just short of 1.0: hue 1.0 *is* hue 0.0, and a handle that
        # jumps from the right end to the left one mid-drag reads as a bug.
        self._h = min(0.9999, max(0.0, (pos.x() - left) / max(1.0, span)))
        self.update()
        self.changed.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton:
            self._take(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.buttons() & Qt.LeftButton:
            self._take(event.position())


class _ChipRow(QWidget):
    """A row of small color chips that each set the picker when clicked.

    Used twice: the before/after comparison, and the recent colors.
    """

    picked = Signal(int)
    CHIP = 20
    GAP = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors: list[int] = []
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._hover = -1

    def set_colors(self, colors: list[int]) -> None:
        self._colors = list(colors)
        self.setFixedSize(self._width(), self.CHIP + 2)
        self.update()

    def colors(self) -> list[int]:
        return list(self._colors)

    def _width(self) -> int:
        n = len(self._colors)
        return max(0, n * self.CHIP + (n - 1) * self.GAP) + 2

    def _rect(self, i: int) -> QRectF:
        return QRectF(1 + i * (self.CHIP + self.GAP), 1, self.CHIP, self.CHIP)

    def _index_at(self, pos) -> int:
        for i in range(len(self._colors)):
            if self._rect(i).contains(pos):
                return i
        return -1

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for i, value in enumerate(self._colors):
            border = theme.ACCENT if i == self._hover else theme.BORDER_STRONG
            painter.setPen(QPen(QColor(border), 1))
            painter.setBrush(_qcolor(value))
            painter.drawRoundedRect(self._rect(i).adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        hover = self._index_at(event.position())
        if hover != self._hover:
            self._hover = hover
            self.setToolTip(f"#{self._colors[hover]:06X}" if hover >= 0 else "")
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._hover = -1
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        i = self._index_at(event.position())
        if event.button() == Qt.LeftButton and i >= 0:
            self.picked.emit(self._colors[i])


class _CompareChip(QWidget):
    """What the color was when the picker opened, beside what it is now.

    Clicking the left half goes back to where it started - the one undo a
    color picker needs, without reaching for Ctrl+Z.
    """

    revert = Signal()

    def __init__(self, before: int, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 26)
        self._before = before
        self._now = before
        self.setToolTip("Before and after. Click the left half to go back.")

    def set_now(self, value: int) -> None:
        self._now = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(box, 6, 6)
        painter.setClipPath(path)
        half = box.width() / 2
        painter.fillRect(QRectF(box.left(), box.top(), half, box.height()), _qcolor(self._before))
        painter.fillRect(QRectF(box.left() + half, box.top(), half, box.height()), _qcolor(self._now))
        painter.setClipping(False)
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton and event.position().x() < self.width() / 2:
            self.revert.emit()


class ColorPicker(QWidget):
    """A color popover in the app's own look.

    Opens under whatever asked for it and edits live: every drag reaches the
    preview as it happens, so the strip itself is the swatch that matters.
    Clicking anywhere else keeps the color; Escape puts back the one it
    opened with. The live edits land in the undo stack as one step per burst,
    the same as holding down a spin box.
    """

    #: Emitted on every change, including a revert.
    color_changed = Signal(int)

    #: Room around the card for its shadow to fall into.
    SHADOW = 12
    #: Shadow rings, outermost first: (grow_px, alpha). Dark rather than
    #: colored - its job is to lift the card off whatever it is covering.
    SHADOW_RINGS = ((12, 10), (8, 22), (4, 44), (1.5, 70))

    def __init__(self, initial: int, parent: QWidget | None = None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._initial = initial & 0xFFFFFF
        self._color = self._initial
        self._anchor: QWidget | None = None
        self._anchor_rect = QRect()
        self._reverted = False
        # A grey has no hue, and QColor reports -1 for it. Held separately so
        # dragging to the grey edge of the field and back does not throw the
        # hue away on the way.
        self._hue = 0.0

        self.field = _SatValField()
        self.hue_bar = _HueBar()
        self.compare = _CompareChip(self._initial)
        self.hex_edit = QLineEdit()
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setValidator(QRegularExpressionValidator(QRegularExpression("#?[0-9A-Fa-f]{0,6}")))
        self.hex_edit.setToolTip("Hex code, like FF3366. Pasting one with a # works too.")
        self.recent = _ChipRow()
        self.recent.set_colors(recent_colors())
        recent_label = QLabel("Recent")
        recent_label.setProperty("role", "hint")

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(self.compare)
        bottom.addWidget(self.hex_edit, 1)

        recent_row = QHBoxLayout()
        recent_row.setSpacing(8)
        recent_row.addWidget(recent_label)
        recent_row.addWidget(self.recent)
        recent_row.addStretch(1)
        self._recent_row = QWidget()
        self._recent_row.setLayout(recent_row)
        recent_row.setContentsMargins(0, 0, 0, 0)
        self._recent_row.setVisible(bool(self.recent.colors()))

        card = QVBoxLayout(self)
        pad = self.SHADOW + 12
        card.setContentsMargins(pad, pad, pad, pad)
        card.setSpacing(10)
        card.addWidget(self.field)
        card.addWidget(self.hue_bar)
        card.addLayout(bottom)
        card.addWidget(self._recent_row)
        card.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self.field.changed.connect(self._from_field)
        self.hue_bar.changed.connect(self._from_hue)
        self.hex_edit.textEdited.connect(self._from_hex)
        self.hex_edit.editingFinished.connect(self._sync_hex)
        self.compare.revert.connect(lambda: self._set(self._initial))
        self.recent.picked.connect(self._set)

        self._show_color(self._initial)

    # -- the color --------------------------------------------------------

    def color(self) -> int:
        return self._color

    def _show_color(self, value: int, keep_hue: bool = False) -> None:
        q = _qcolor(value)
        h = q.hsvHueF()
        if h >= 0 and not keep_hue:
            self._hue = h
        self.field.set_hsv(self._hue, q.hsvSaturationF(), q.valueF())
        self.hue_bar.set_hue(self._hue)
        self.compare.set_now(value)
        self._sync_hex()

    def _commit(self, value: int) -> None:
        value &= 0xFFFFFF
        if value == self._color:
            return
        self._color = value
        self.compare.set_now(value)
        self.color_changed.emit(value)

    def _set(self, value: int) -> None:
        """A whole color arrived at once (the hex field, a chip, a revert)."""
        self._commit(value)
        self._show_color(value & 0xFFFFFF)

    def _from_field(self) -> None:
        s, v = self.field.sat_val()
        value = _packed(QColor.fromHsvF(self._hue, s, v))
        self._commit(value)
        self._sync_hex()

    def _from_hue(self) -> None:
        self._hue = self.hue_bar.hue()
        s, v = self.field.sat_val()
        self.field.set_hsv(self._hue, s, v)
        self._commit(_packed(QColor.fromHsvF(self._hue, s, v)))
        self._sync_hex()

    def _from_hex(self, text: str) -> None:
        digits = text.strip().lstrip("#")
        if len(digits) == 6:
            self._set(int(digits, 16))

    def _sync_hex(self) -> None:
        # Left alone while half a code is being typed into it: rewriting the
        # field under the cursor would fight the typing.
        if not self.hex_edit.hasFocus() or len(self.hex_edit.text().lstrip("#")) == 6:
            self.hex_edit.setText(f"#{self._color:06X}")

    # -- showing it -------------------------------------------------------

    def popup_under(self, anchor: QWidget, rect: QRect | None = None) -> None:
        """Open below @p anchor (or the part of it in @p rect), flipped above
        it when the screen has no room underneath."""
        self._anchor = anchor
        self._anchor_rect = QRect(rect) if rect is not None else anchor.rect()
        self.adjustSize()
        size = self.sizeHint()
        top_left = anchor.mapToGlobal(self._anchor_rect.bottomLeft())
        bottom_left = anchor.mapToGlobal(self._anchor_rect.topLeft())
        screen = anchor.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        # The card's corner, not the shadow's, lines up with the anchor.
        x = top_left.x() - self.SHADOW
        y = top_left.y() + 4 - self.SHADOW
        if y + size.height() > avail.bottom() and bottom_left.y() - size.height() >= avail.top():
            y = bottom_left.y() - size.height() - 4 + self.SHADOW
        x = max(avail.left(), min(x, avail.right() - size.width()))
        y = max(avail.top(), min(y, avail.bottom() - size.height()))
        self.move(QPoint(x, y))
        self.show()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() == Qt.Key_Escape:
            self._reverted = True
            self._set(self._initial)
            self.close()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # A click outside a popup closes it and is then replayed to whatever
        # was under the pointer. Replayed onto the swatch that opened this,
        # it would open a second picker straight away - so that one click is
        # swallowed, and every other one still lands where it was aimed.
        pos = event.position().toPoint()
        if self._anchor is not None and not self.rect().contains(pos):
            local = self._anchor.mapFromGlobal(event.globalPosition().toPoint())
            if self._anchor_rect.contains(local):
                self.setAttribute(Qt.WA_NoMouseReplay)
        elif not self._card().contains(QPointF(pos)):
            # The shadow is part of this window, but reads as outside it.
            self.close()
            return
        super().mousePressEvent(event)

    def _card(self) -> QRectF:
        return QRectF(self.rect()).adjusted(self.SHADOW, self.SHADOW, -self.SHADOW, -self.SHADOW)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if not self._reverted and self._color != self._initial:
            remember_color(self._color)
        super().closeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        card = self._card()
        painter.setPen(Qt.NoPen)
        for grow, alpha in self.SHADOW_RINGS:
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(card.adjusted(-grow, -grow, grow, grow), 10 + grow, 10 + grow)
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        painter.setBrush(QColor(theme.BG_ELEVATED))
        painter.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        painter.end()


class ColorButton(QPushButton):
    """A swatch button that opens a color picker and reports 0xRRGGBB ints."""

    color_changed = Signal(int)

    #: Halo radius at rest and under the pointer. The resting glow renders the
    #: swatch as a light source; hover is about twice that.
    BLOOM_REST = 7
    BLOOM_HOVER = 15
    BLOOM_ALPHA = 150

    def __init__(self, initial: int = 0xFFFFFF, parent=None):
        super().__init__(parent)
        self._color = initial & 0xFFFFFF
        self.setFixedSize(52, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to pick a color")
        self.clicked.connect(self._pick)
        # Tinted with the swatch's own color, and re-tinted on every change.
        self._bloom = theme.HoverBloom(
            self,
            self._qcolor(),
            radius=self.BLOOM_HOVER,
            alpha=self.BLOOM_ALPHA,
            resting=self.BLOOM_REST,
        )
        self._refresh_style()

    def color(self) -> int:
        return self._color

    def _qcolor(self) -> QColor:
        return _qcolor(self._color)

    def set_color(self, value: int) -> None:
        self._color = value & 0xFFFFFF
        self._refresh_style()

    def _refresh_style(self) -> None:
        # Inline rather than themed: the swatch shows an arbitrary user-chosen
        # color, so only its chrome (border, radius, hover) is themed.
        r, g, b = (self._color >> 16) & 0xFF, (self._color >> 8) & 0xFF, self._color & 0xFF
        self.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r},{g},{b});"
            f" border: 1px solid {theme.BORDER_STRONG}; border-radius: 5px; padding: 0px; }}"
            f"QPushButton:hover {{ border: 1px solid {theme.ACCENT}; }}"
        )
        self._bloom.set_color(self._qcolor())

    def _pick(self) -> ColorPicker:
        # Our own popover rather than the platform's panel or QColorDialog -
        # see ColorPicker for what each of those cost.
        picker = ColorPicker(self._color, self)
        picker.color_changed.connect(self._take)
        picker.popup_under(self)
        return picker

    def _take(self, value: int) -> None:
        value &= 0xFFFFFF
        if value == self._color:
            return
        self._color = value
        self._refresh_style()
        self.color_changed.emit(value)


class ColumnScrollArea(QScrollArea):
    """A side column that scrolls down but never sideways.

    Its minimum width follows whatever it is showing, so a splitter makes
    room for the widest thing on screen instead of hiding the right edge of
    it behind a horizontal scrollbar. Which thing is widest changes as the
    user works - a gauge region's color scale needs more room than a
    rainbow does - so the minimum is re-read every time the contents lay
    themselves out again.

    @p floor keeps the column from collapsing when its contents are small;
    @p cap stops one very wide widget from taking the whole window, and past
    it the horizontal scrollbar is still there as a last resort.
    """

    def __init__(self, widget: QWidget, floor: int = 0, cap: int = 10_000, parent=None):
        super().__init__(parent)
        self._floor = floor
        self._cap = cap
        self.setWidget(widget)
        self.setWidgetResizable(True)
        widget.installEventFilter(self)

    def content_width(self) -> int:
        """What the column needs to show its contents without clipping."""
        widget = self.widget()
        if widget is None:
            return self._floor
        needed = (widget.minimumSizeHint().width()
                  + self.verticalScrollBar().sizeHint().width()
                  + 2 * self.frameWidth())
        return max(self._floor, min(self._cap, needed))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        hint = super().minimumSizeHint()
        return QSize(max(hint.width(), self.content_width()), hint.height())

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if obj is self.widget() and event.type() == QEvent.LayoutRequest:
            # Tells the splitter to read minimumSizeHint() again, which grows
            # this column if the contents just got wider.
            self.updateGeometry()
        return super().eventFilter(obj, event)


#: One tile of the drifting rule. theme.BRAND_SWEEP ends on the color it
#: starts with, so the tile repeats without a visible seam.
_RULE_STOPS = theme.BRAND_SWEEP


class BrandRule(QWidget):
    """The hairline of brand color under the menu bar, drifting and lit.

    Replaces a flat QSS gradient frame with two things a stylesheet can't do:
    the gradient travels (one full pass takes half a minute, so it's motion
    you notice only if you look for it), and the crisp line sheds a short
    falloff underneath, as if it were casting onto the window below.
    """

    #: Crisp line, then the falloff beneath it. Alphas step down fast, the
    #: glow suggests spill, not a second, fuzzier rule.
    LINE_H = 2
    FALLOFF_ALPHAS = (105, 58, 30, 14)

    FRAME_MS = 40  # 25fps
    #: One tile of the sweep, in pixels, and how fast it rolls.
    #:
    #: A fixed tile rather than "one tile per window width": stretched across
    #: a wide window the hue gradient gets so shallow that the roll reads as
    #: static, because what the eye picks up is hue change per second or
    #: speed times the steepness of the gradient it's travelling through. At
    #: these numbers a color stop passes any given point about every 1.3s.
    TILE_PX = 560
    SPEED_PX_PER_SEC = 55

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.LINE_H + len(self.FALLOFF_ALPHAS))
        #: Where the current tile starts, in pixels. Wraps at TILE_PX.
        self._offset = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._advance)

    # Driven off visibility rather than started in __init__: no reason to
    # repaint a 6px strip while the window is closed or minimized.
    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().hideEvent(event)
        # Qt hides a widget on its way out, and by then PySide6 has already
        # cleared the Python instance - so _timer can be gone when this runs
        # during teardown. The timer is parented to the widget and dies with
        # it either way; stopping it here is only to spare the repaints.
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.stop()

    def _advance(self) -> None:
        travel = self.SPEED_PX_PER_SEC * self.FRAME_MS / 1000
        self._offset = (self._offset + travel) % self.TILE_PX
        self.update()

    def _gradient(self, alpha: int) -> QLinearGradient:
        """One tile of the wordmark sweep, at the current offset.

        RepeatSpread tiles it across the rest of the widget, so the offset can
        run off the end without leaving a gap. And because the stops are a
        palindrome, tile boundaries are invisible.
        """
        gradient = QLinearGradient(self._offset, 0, self._offset + self.TILE_PX, 0)
        gradient.setSpread(QGradient.RepeatSpread)
        for i, color in enumerate(_RULE_STOPS):
            tint = QColor(color)
            tint.setAlpha(alpha)
            gradient.setColorAt(i / (len(_RULE_STOPS) - 1), tint)
        return gradient

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        width = self.width()
        painter.fillRect(0, 0, width, self.LINE_H, self._gradient(255))
        for i, alpha in enumerate(self.FALLOFF_ALPHAS):
            painter.fillRect(0, self.LINE_H + i, width, 1, self._gradient(alpha))


#: The slowest step a speed field offers. The firmware holds 16 bits of it, but
#: ten seconds a pixel is already slower than anything reads as motion.
STEP_MS_MAX = 10000


class SpeedEdit(QWidget):
    """How fast a moving animation travels: so many pixels, every so often.

    Mirrors LedStrand::Speed. "every refresh" steps on every tick of the
    strand, which is all a speed could say before it could go slower - so a
    design saved before step times existed reads exactly as it plays. A time
    is how anything goes slower than a pixel a tick.

    `pixels_spin` and `step_spin` are public so a panel can keep naming them
    the way it names every other field.
    """

    changed = Signal()

    def __init__(self, min_pixels: int = 1, max_pixels: int = 64, parent=None):
        super().__init__(parent)
        self.pixels_spin = QSpinBox()
        self.pixels_spin.setRange(min_pixels, max_pixels)
        self.pixels_spin.setValue(max(1, min_pixels))
        self.pixels_spin.setSuffix(" px")
        self.pixels_spin.setToolTip("How far each step moves it")

        self.step_spin = QSpinBox()
        self.step_spin.setRange(0, STEP_MS_MAX)
        self.step_spin.setSingleStep(10)
        self.step_spin.setSuffix(" ms")
        # The minimum's text, not a separate checkbox: 0 is a real value to
        # the firmware ("every tick"), and one field reads as one setting.
        self.step_spin.setSpecialValueText("refresh")
        self.step_spin.setToolTip(
            "How long between steps. Longer is slower. \"refresh\" steps on every "
            "refresh of the strand, the fastest there is. On the robot it is "
            "rounded to the strand's refresh interval, so the pace stays even.")

        every = QLabel("every")
        every.setProperty("role", "hint")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.pixels_spin, 1)
        layout.addWidget(every)
        layout.addWidget(self.step_spin, 1)

        self.pixels_spin.valueChanged.connect(self.changed)
        self.step_spin.valueChanged.connect(self.changed)

    def set_speed(self, pixels: int, step_ms: int) -> None:
        """Show a speed without announcing it as an edit."""
        for spin, value in ((self.pixels_spin, pixels), (self.step_spin, step_ms)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def pixels(self) -> int:
        return self.pixels_spin.value()

    def step_ms(self) -> int:
        return self.step_spin.value()


def enum_data(combo, enum_cls):
    """The enum member a combo box's current item carries.

    Qt stores item data as a QVariant, and every kind/mode enum in the models
    is a `str, Enum` - which goes into a QVariant as a string and comes back
    out as a plain `str`, not as the member that was put in. It compares equal
    and hashes the same, so dict lookups and `==` checks never notice; what
    notices is serialization, which calls `.value` on it and gets an
    AttributeError instead of a saved file.

    So every save() that writes an enum field goes through here, and the config
    keeps holding the type it is annotated with.
    """
    return enum_cls(combo.currentData())


def parse_palette(text: str) -> list[int]:
    """Parses a comma-separated list of hex colors ("FF0000, 00FF00") into ints."""
    colors = []
    for chunk in text.split(","):
        chunk = chunk.strip().lstrip("#")
        if not chunk:
            continue
        try:
            colors.append(int(chunk, 16) & 0xFFFFFF)
        except ValueError:
            continue
    return colors


def format_palette(colors: list[int]) -> str:
    return ", ".join(f"{c:06X}" for c in colors)
