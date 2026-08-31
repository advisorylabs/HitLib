"""HitLib brand theme: design tokens, icon loading, and the app-wide stylesheet.

Everything visual that isn't drawn by hand in canvas.py comes from here, so a
palette tweak is a one-file change.

Why Qt Style Sheets rather than a "modern UI toolkit" wrapper: Pattern Studio
is already Qt, and QSS restyles the *existing* widget tree without touching a
line of control logic. It also keeps QSplitter, the QPainter LED canvas
and the group-edit wiring intact, none of which have equivalents in the
Tk-based toolkits.

Colors are lifted from the HitLib logo (the HITLIB wordmark's
pink -> violet -> blue -> cyan gradient, over the near-black shield, with the
bow-tie crimson as the danger accent) and reconciled with the tokens the
documentation site already ships in docs/custom.css.
"""

from __future__ import annotations

import sys
from pathlib import Path
from string import Template

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation
from PySide6.QtGui import QColor, QFont, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QWidget

# ----------------------------------------------------------------------
# Design tokens
# ----------------------------------------------------------------------

#: Surfaces, darkest to lightest. BASE is the window; PANEL is a grouped
#: region; INPUT is anything the user types into or clicks open.
BG_BASE = "#0D0D10"
BG_PANEL = "#131317"
BG_ELEVATED = "#17171C"
BG_INPUT = "#1B1B21"
BG_HOVER = "#212128"

BORDER = "#2A2A31"
BORDER_STRONG = "#3A3A45"

TEXT = "#E5E5E7"
TEXT_MUTED = "#9CA3AF"
TEXT_DIM = "#6B7280"

#: The wordmark gradient, left to right. Used for accent bars, the app header
#: rule, and (later) the splash screen.
BRAND_PINK = "#EC4899"
BRAND_VIOLET = "#A855F7"
BRAND_INDIGO = "#7C3AED"
BRAND_BLUE = "#3B82F6"
BRAND_CYAN = "#22D3EE"

#: Left-to-right stops of the wordmark gradient, for anything that wants to
#: paint the full sweep.
BRAND_GRADIENT = (BRAND_PINK, BRAND_VIOLET, BRAND_INDIGO, BRAND_BLUE, BRAND_CYAN)

#: The same sweep run out and back, for anything that wraps or repeats it.
#: The splash ring and the drifting header rule. Going straight from cyan
#: back to pink interpolates through a desaturated blue-grey, which shows up
#: as a dead patch travelling past; ending where it started hides the seam.
BRAND_SWEEP = BRAND_GRADIENT + BRAND_GRADIENT[-2::-1]

#: Primary interactive accent, matches --primary-color on the docs site.
ACCENT = BRAND_VIOLET
ACCENT_HI = "#BE7BF9"
ACCENT_LO = BRAND_INDIGO

#: Secondary accent for "this is live / selected" states. Reads clearly
#: against the LED preview, where violet would compete with lit pixels.
FOCUS = BRAND_CYAN

#: The logo's bow-tie crimson, matched to the docs site's --warning-color.
DANGER = "#EF4444"
DANGER_HI = "#F87171"

#: Bloom: how far a colored element's light bleeds past its own edges, and
#: how hard it lands. Deliberately small numbers, the point is that a lit
#: control looks like it's emitting, not that it looks like it's on fire.
BLOOM_RADIUS = 16
BLOOM_ALPHA = 120

#: Canvas-specific paints, consumed by StripCanvas.
CANVAS_BG = "#0A0A0D"
CANVAS_LED_BEZEL = "#26262E"
CANVAS_LABEL = "#C9CBD4"
CANVAS_EMPTY_TEXT = TEXT_DIM

#: Font stack. Inter is the docs site's face; the rest are the best native
#: fallbacks so an alpha user without Inter installed still gets a modern UI
#: font rather than Qt's default.
FONT_STACK = [
    "Inter",
    "Segoe UI Variable Text",
    "Segoe UI",
    "Roboto",
    "Helvetica Neue",
    "sans-serif",
]
FONT_POINT_SIZE = 9

# ----------------------------------------------------------------------
# Resources
# ----------------------------------------------------------------------


def resource_dir() -> Path:
    """Directory holding bundled images/icons.

    A frozen PyInstaller build extracts `datas` under sys._MEIPASS, where
    __file__ no longer sits beside a real resources/ tree. Same
    resolution dance app.py does for the window icon.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "pattern_studio" / "resources"


def icon(name: str) -> QIcon:
    """Load a bundled SVG icon by stem, e.g. icon("play")."""
    return QIcon(str(resource_dir() / "icons" / f"{name}.svg"))


def logo_pixmap() -> QPixmap:
    """Full-color logo art. Here for the splash screen to pick up."""
    return QPixmap(str(resource_dir() / "hitliblogo.png"))


def _qss_url(path: Path) -> str:
    """A QSS url() for a filesystem path.

    Qt's stylesheet parser wants forward slashes even on Windows, and quoting
    keeps a space in the install path (C:/Program Files/...) from splitting
    the token.
    """
    return 'url("' + path.as_posix() + '")'


# ----------------------------------------------------------------------
# Bloom
# ----------------------------------------------------------------------
#
# QSS has no box-shadow, so the only way to put light *around* a widget is a
# graphics effect. QGraphicsDropShadowEffect with a zero offset is a glow: it
# blurs the widget's own silhouette in the given color and paints it behind
# the widget.


def bloom(
    widget: QWidget,
    color: str | QColor,
    radius: float = BLOOM_RADIUS,
    alpha: int = BLOOM_ALPHA,
) -> QGraphicsDropShadowEffect:
    """Put a colored glow behind `widget`. Returns the effect, for animating."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setOffset(0, 0)
    effect.setBlurRadius(radius)
    tint = QColor(color)
    tint.setAlpha(alpha)
    effect.setColor(tint)
    widget.setGraphicsEffect(effect)
    return effect


class HoverBloom(QObject):
    """Ramps a widget's bloom up while the pointer is over it.

    Parented to the widget it decorates, so it lives and dies with it. The
    ramp is a real animation rather than an on/off swap, an instant halo
    looks like a bug, a 150ms one looks like the control warming up.

    A widget carrying a graphics effect is rendered through an offscreen
    buffer, which loses subpixel text antialiasing. So unless the caller asks
    for a resting glow (`resting > 0`, for swatches and other wordless
    controls), the effect is attached on enter and dropped again once the
    ramp back down finishes.
    """

    RAMP_MS = 150

    def __init__(
        self,
        widget: QWidget,
        color: str | QColor,
        radius: float = BLOOM_RADIUS,
        alpha: int = BLOOM_ALPHA,
        resting: float = 0.0,
    ):
        super().__init__(widget)
        self._widget = widget
        self._peak = float(radius)
        self._resting = float(resting)
        self._alpha = alpha
        self._color = QColor(color)
        self._effect: QGraphicsDropShadowEffect | None = None
        self._anim: QPropertyAnimation | None = None
        if self._resting > 0:
            self._attach()
        widget.installEventFilter(self)

    def set_color(self, color: str | QColor) -> None:
        """Re-tint the halo, for swatches that carry a user-chosen color."""
        self._color = QColor(color)
        if self._effect is not None:
            tint = QColor(self._color)
            tint.setAlpha(self._alpha)
            self._effect.setColor(tint)

    # ------------------------------------------------------------------
    # Effect lifetime
    # ------------------------------------------------------------------

    def _attach(self) -> QGraphicsDropShadowEffect:
        if self._effect is None:
            self._effect = bloom(self._widget, self._color, self._resting, self._alpha)
            # Parented to self rather than left as a local: a QPropertyAnimation
            # that goes out of scope is collected mid-ramp, and the value it
            # was driving snaps straight to the end.
            self._anim = QPropertyAnimation(self._effect, b"blurRadius", self)
            self._anim.setDuration(self.RAMP_MS)
            self._anim.setEasingCurve(QEasingCurve.OutCubic)
            self._anim.finished.connect(self._on_ramp_finished)
        return self._effect

    def _detach(self) -> None:
        if self._effect is None:
            return
        # setGraphicsEffect() deletes the effect it replaces, so the animation
        # has to let go of it first.
        self._anim.stop()
        self._anim.setTargetObject(None)
        self._anim.deleteLater()
        self._anim = None
        self._effect = None
        self._widget.setGraphicsEffect(None)

    def _on_ramp_finished(self) -> None:
        if self._resting <= 0 and self._effect is not None and self._effect.blurRadius() <= 0.01:
            self._detach()

    def _ramp_to(self, radius: float) -> None:
        if radius <= 0 and self._effect is None:
            return
        effect = self._attach()
        self._anim.stop()
        self._anim.setStartValue(effect.blurRadius())
        self._anim.setEndValue(radius)
        self._anim.start()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        kind = event.type()
        if kind == QEvent.Enter:
            self._ramp_to(self._peak)
        elif kind == QEvent.Leave:
            self._ramp_to(self._resting)
        elif kind == QEvent.EnabledChange and not obj.isEnabled():
            # A disabled control emits nothing.
            self._ramp_to(self._resting)
        return False


# ----------------------------------------------------------------------
# Stylesheet
# ----------------------------------------------------------------------

# $-placeholders rather than str.format: the sheet is mostly CSS braces, and
# doubling every one of them to escape .format() is a mistake waiting to
# happen.
_QSS = Template(
    """
/* ============================ base ============================ */
QWidget {
    color: $text;
}
QMainWindow, QDialog {
    background-color: $bg;
}
QToolTip {
    background-color: $elevated;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 5px 8px;
}

/* ======================== window chrome ======================= */
/* The app's own title bar (window_chrome.TitleBar) stands in for the system
   caption: logo, menus, title, then the caption buttons. */
QWidget#titleBar {
    background-color: $bg;
}
QLabel#appTitle {
    color: $text;
    font-weight: 600;
}
QLabel#appSubtitle {
    color: $dim;
}

/* ========================== menu bar ========================== */
/* No bottom border and no fill of its own: it sits inside the title bar
   now, and the brand rule underneath is what separates chrome from app. */
QMenuBar {
    background: transparent;
    border: none;
    padding: 0px;
}
QMenuBar::item {
    background: transparent;
    padding: 5px 8px;
    border-radius: 6px;
    color: $muted;
}
QMenuBar::item:selected {
    background-color: $hover;
    color: $text;
}
QMenuBar::item:pressed {
    background-color: $accent_lo;
    color: #FFFFFF;
}
QMenu {
    background-color: $elevated;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 5px;
}
QMenu::item {
    padding: 6px 22px 6px 14px;
    border-radius: 5px;
    color: $text;
}
QMenu::item:selected {
    background-color: $accent_lo;
    color: #FFFFFF;
}
QMenu::separator {
    height: 1px;
    background-color: $border;
    margin: 5px 8px;
}

/* ========================== splitter ========================== */
QSplitter::handle {
    background-color: $border;
}
QSplitter::handle:horizontal {
    width: 1px;
    margin: 0px 3px;
}
QSplitter::handle:vertical {
    height: 1px;
    margin: 3px 0px;
}
QSplitter::handle:hover {
    background-color: $accent;
}

/* ======================== scroll areas ======================== */
QScrollArea {
    background-color: transparent;
    border: none;
}
QScrollBar:vertical {
    background: transparent;
    width: 11px;
    margin: 0px;
}
QScrollBar:horizontal {
    background: transparent;
    height: 11px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background-color: $border_strong;
    border-radius: 5px;
    min-height: 28px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background-color: $border_strong;
    border-radius: 5px;
    min-width: 28px;
    margin: 2px;
}
QScrollBar::handle:hover {
    background-color: $accent;
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0px;
    width: 0px;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}

/* ========================== group box ========================= */
QGroupBox {
    background-color: $panel;
    border: 1px solid $border;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0px 6px;
    color: $accent;
}

/* The brand rule under the menu bar is painted by widgets.BrandRule rather
   than styled here: it drifts, and a QSS gradient is a fixed brush. */

/* Thin divider between related control groups. */
QFrame#vSep {
    background-color: $border;
    border: none;
}

/* ================== strand settings strip ===================== */
/* The "hardware identity" row above the preview: one card, so it reads as a
   single block of per-strand settings rather than six loose fields. */
QWidget#strandStrip {
    background-color: $panel;
    border: 1px solid $border;
    border-radius: 8px;
}

/* The Song bar under the preview. Same card as the strand strip: both are
   "settings for the whole thing below/above me" strips rather than chrome. */
QWidget#musicPanel {
    background-color: $panel;
    border: 1px solid $border;
    border-radius: 8px;
}

/* =========================== labels =========================== */
QLabel {
    background: transparent;
    color: $text;
}
QLabel:disabled {
    color: $dim;
}
QLabel[role="sectionHeader"] {
    color: $muted;
    font-weight: 600;
    padding: 2px 0px;
}
QLabel[role="groupCount"] {
    color: $focus;
    font-weight: 600;
}
/* Secondary text: what a field means, or what state something is in. Reads as
   annotation next to the control it belongs to rather than as another label. */
QLabel[role="hint"] {
    color: $muted;
}
QLabel#groupBanner {
    background-color: rgba(168, 85, 247, 0.10);
    border: 1px solid rgba(168, 85, 247, 0.45);
    border-left: 3px solid $accent;
    border-radius: 6px;
    color: $text;
    padding: 7px 9px;
}

/* ========================== push button ======================= */
QPushButton {
    background-color: $elevated;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text;
    padding: 6px 13px;
}
QPushButton:hover {
    background-color: $hover;
    border-color: $border_strong;
}
QPushButton:pressed {
    background-color: $input;
}
QPushButton:disabled {
    background-color: $panel;
    border-color: $border;
    color: $dim;
}

QPushButton[role="primary"] {
    background-color: $accent_lo;
    border: 1px solid $accent;
    color: #FFFFFF;
    font-weight: 600;
}
QPushButton[role="primary"]:hover {
    background-color: $accent;
    border-color: $accent_hi;
}
QPushButton[role="primary"]:pressed {
    background-color: $accent_lo;
}
QPushButton[role="primary"]:disabled {
    background-color: $panel;
    border-color: $border;
    color: $dim;
}

QPushButton[role="danger"] {
    background-color: $elevated;
    border: 1px solid rgba(239, 68, 68, 0.55);
    color: $danger_hi;
}
QPushButton[role="danger"]:hover {
    background-color: rgba(239, 68, 68, 0.16);
    border-color: $danger;
    color: #FFFFFF;
}
QPushButton[role="danger"]:pressed {
    background-color: rgba(239, 68, 68, 0.28);
}
QPushButton[role="danger"]:disabled {
    background-color: $panel;
    border-color: $border;
    color: $dim;
}

/* Transport buttons. Six of them share the controls header with the strand
   strip, so they get tighter padding and a low min-width. QCommonStyle
   otherwise floors every text button at 80px, which alone overflows the
   center column. */
QPushButton[role="transport"] {
    padding: 6px 10px;
    min-width: 50px;
}

/* Compact square buttons: list add/remove and the reorder arrows. */
QPushButton[role="icon"] {
    padding: 4px 6px;
    min-width: 26px;
}

/* =========================== inputs =========================== */
QLineEdit, QSpinBox, QComboBox {
    background-color: $input;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text;
    /* Padding feeds straight into the widget's size hint under QSS, and the
       strand-settings strip lines six of these up in one row. Generous
       side padding here is what pushes that row into needing a scrollbar. */
    padding: 5px 7px;
    selection-background-color: $accent_lo;
    selection-color: #FFFFFF;
}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {
    border-color: $border_strong;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: $accent;
    background-color: $elevated;
}
/* Hovering an already-focused field pushes its border to the brighter
   accent, the one place those two states stack. */
QLineEdit:focus:hover, QSpinBox:focus:hover, QComboBox:focus:hover {
    border-color: $accent_hi;
}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
    background-color: $panel;
    color: $dim;
}

QSpinBox::up-button, QSpinBox::down-button {
    subcontrol-origin: border;
    background-color: transparent;
    border: none;
    border-left: 1px solid $border;
    width: 15px;
}
QSpinBox::up-button {
    subcontrol-position: top right;
    border-top-right-radius: 6px;
}
QSpinBox::down-button {
    subcontrol-position: bottom right;
    border-bottom-right-radius: 6px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background-color: $hover;
}
QSpinBox::up-arrow {
    image: $icon_spin_up;
    width: 10px;
    height: 10px;
}
QSpinBox::down-arrow {
    image: $icon_spin_down;
    width: 10px;
    height: 10px;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    width: 22px;
}
QComboBox::down-arrow {
    image: $icon_chevron_down;
    width: 12px;
    height: 12px;
}
QComboBox QAbstractItemView {
    background-color: $elevated;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text;
    padding: 4px;
    outline: none;
    selection-background-color: $accent_lo;
    selection-color: #FFFFFF;
}

/* ========================== check box ========================= */
QCheckBox {
    background: transparent;
    color: $text;
    spacing: 7px;
}
QCheckBox:disabled {
    color: $dim;
}
QCheckBox::indicator, QListWidget::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid $border_strong;
    border-radius: 4px;
    background-color: $input;
}
QCheckBox::indicator:hover, QListWidget::indicator:hover {
    border-color: $accent;
}
QCheckBox::indicator:checked, QListWidget::indicator:checked {
    background-color: $accent_lo;
    border-color: $accent;
    image: $icon_check;
}
QCheckBox::indicator:disabled, QListWidget::indicator:disabled {
    background-color: $panel;
    border-color: $border;
}

/* ========================== list view ========================= */
QListWidget {
    background-color: $panel;
    border: 1px solid $border;
    border-radius: 8px;
    outline: none;
    padding: 4px;
}
QListWidget::item {
    /* The 3px left border is transparent until selected, so a row's text
       doesn't shift sideways when the accent bar appears. */
    border-left: 3px solid transparent;
    border-radius: 5px;
    color: $text;
    padding: 6px 8px;
}
QListWidget::item:hover {
    background-color: $hover;
}
/* Selected rows are painted by strand_list._RowDelegate, not styled here:
   they carry a dithered wash and, during a group edit, a bloom. Neither of
   which QSS can express. The transparent left border above is what the
   delegate paints its accent bar into. */

/* ======================== message box ======================== */
QMessageBox {
    background-color: $elevated;
}
QMessageBox QLabel {
    color: $text;
}
"""
)


def stylesheet() -> str:
    """The full app stylesheet, with resource paths resolved for this run."""
    icons = resource_dir() / "icons"
    return _QSS.substitute(
        bg=BG_BASE,
        panel=BG_PANEL,
        elevated=BG_ELEVATED,
        input=BG_INPUT,
        hover=BG_HOVER,
        border=BORDER,
        border_strong=BORDER_STRONG,
        text=TEXT,
        muted=TEXT_MUTED,
        dim=TEXT_DIM,
        accent=ACCENT,
        accent_hi=ACCENT_HI,
        accent_lo=ACCENT_LO,
        focus=FOCUS,
        danger=DANGER,
        danger_hi=DANGER_HI,
        icon_spin_up=_qss_url(icons / "spin-up.svg"),
        icon_spin_down=_qss_url(icons / "spin-down.svg"),
        icon_chevron_down=_qss_url(icons / "chevron-down.svg"),
        icon_check=_qss_url(icons / "check.svg"),
    )


# ----------------------------------------------------------------------
# Application hookup
# ----------------------------------------------------------------------


def _palette() -> QPalette:
    """Dark palette backing the stylesheet.

    QSS can't reach everything: native-ish pieces like QColorDialog's swatch
    grid, QFileDialog's sidebar and disabled-state text all read the palette
    directly. Without this they'd stay light-on-light inside a dark app.
    """
    p = QPalette()
    p.setColor(QPalette.Window, QColor(BG_BASE))
    p.setColor(QPalette.WindowText, QColor(TEXT))
    p.setColor(QPalette.Base, QColor(BG_INPUT))
    p.setColor(QPalette.AlternateBase, QColor(BG_PANEL))
    p.setColor(QPalette.Text, QColor(TEXT))
    p.setColor(QPalette.Button, QColor(BG_ELEVATED))
    p.setColor(QPalette.ButtonText, QColor(TEXT))
    p.setColor(QPalette.BrightText, QColor(DANGER))
    p.setColor(QPalette.ToolTipBase, QColor(BG_ELEVATED))
    p.setColor(QPalette.ToolTipText, QColor(TEXT))
    p.setColor(QPalette.Highlight, QColor(ACCENT_LO))
    p.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    p.setColor(QPalette.Link, QColor(ACCENT))
    p.setColor(QPalette.LinkVisited, QColor(BRAND_PINK))
    p.setColor(QPalette.PlaceholderText, QColor(TEXT_DIM))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(TEXT_DIM))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(TEXT_DIM))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_DIM))
    return p


def _font() -> QFont:
    font = QFont()
    font.setFamilies(FONT_STACK)
    font.setPointSize(FONT_POINT_SIZE)
    return font


def apply_theme(app: QApplication) -> None:
    """Install the HitLib look on `app`. Call once, before the first window.

    Fusion first: the native Windows style ignores large parts of a stylesheet
    (it hands those controls to the OS theme engine), so spin buttons and
    check indicators would keep their stock light-mode chrome. Fusion honours
    QSS everywhere and renders identically on every platform.
    """
    app.setStyle("Fusion")
    app.setPalette(_palette())
    app.setFont(_font())
    app.setStyleSheet(stylesheet())
