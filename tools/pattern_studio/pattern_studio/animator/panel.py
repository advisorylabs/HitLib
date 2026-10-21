"""The Animator's editing surface.

Three columns, at the same proportions the Design tab uses so switching does
not feel like switching applications:

    left    the document's animations, over the selected one's layers
    centre  a live preview, because an animator you cannot watch is a config
            file with buttons
    right   the selected layer, its drivers and its bindings

The panel owns no document state. It is handed the library, mutates it in
place the way every other panel in this app does, and emits `changed`; the
window pushes one undo step per burst and re-issues the animation on any
strand playing it.
"""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import fill_sources, presets, theme
from ..canvas import StripCanvas
from ..inspector import GaugeStopsEditor, _reading_spin
from ..models import (
    MAX_BINDINGS,
    MAX_DRIVERS,
    MAX_LAYERS,
    AnimationConfig,
    AnimationKind,
    BindingConfig,
    BitScrollSegmentConfig,
    CompositionConfig,
    DRIVER_KIND_LABELS,
    DriverConfig,
    DriverKind,
    LAYER_BLEND_HELP,
    LAYER_BLEND_LABELS,
    LAYER_KIND_LABELS,
    LAYER_PARAM_LABELS,
    LFO_SHAPE_LABELS,
    LayerBlend,
    LayerConfig,
    LayerKind,
    LayerParam,
    LfoShape,
)
from ..session import StrandSession
from ..widgets import ColorButton, SpeedEdit
from .curve_editor import CurveDialog, is_linear
from .pixel_grid import PixelGridEditor
from .strips import SpanRuler, TimelineStrip

#: What the scratch preview runs at when no strand says otherwise. Twenty is
#: what a real strip is - HitLib caps at 64, but teams run a few short
#: strands on separate ports rather than one long one.
DEFAULT_PREVIEW_LENGTH = 20

#: Which fields each layer kind actually uses. Anything not listed is hidden,
#: which is what keeps a SOLID layer from showing a bitscroll's spacing.
_VISIBLE: dict[LayerKind, set[str]] = {
    LayerKind.OFF: set(),
    LayerKind.SOLID: {"color"},
    LayerKind.PULSE: {"color", "bg", "run_length", "speed", "invert", "bounce"},
    LayerKind.FLASH: {"color", "bg", "on_ms", "off_ms"},
    LayerKind.FLOW: {"color", "color2", "speed", "invert", "seamless"},
    LayerKind.RAINBOW: {"speed", "invert"},
    LayerKind.TWINKLE: {"bg", "palette", "density", "fade_step"},
    LayerKind.BITSCROLL: {"bg", "segments", "spacing", "repeating", "speed",
                          "invert", "bounce"},
    LayerKind.GAUGE: {"color", "color2", "bg", "style", "gauge_blend", "stops",
                      "range", "invert"},
    LayerKind.METER: {"color", "color2", "bg", "gradient", "invert"},
    LayerKind.FRAMES: {"frames", "speed", "invert"},
}


def _combo(labels: dict) -> QComboBox:
    box = QComboBox()
    for value, text in labels.items():
        box.addItem(text, value)
    return box


def _spin(lo: int, hi: int, value: int = 0, suffix: str = "") -> QSpinBox:
    box = QSpinBox()
    box.setRange(lo, hi)
    box.setValue(value)
    if suffix:
        box.setSuffix(suffix)
    return box


def _menu_header(menu, text: str) -> None:
    """A heading inside a menu, in the look of the column headings.

    QMenu.addSection() would be the obvious call, but under Fusion and the
    app's stylesheet a section draws as a bare separator and its text never
    appears. Disabled, so it cannot be hovered or clicked like an entry.
    """
    from PySide6.QtWidgets import QWidgetAction

    label = QLabel(text.upper())
    label.setProperty("role", "sectionHeader")
    label.setContentsMargins(10, 4, 14, 2)
    action = QWidgetAction(menu)
    action.setDefaultWidget(label)
    action.setEnabled(False)
    menu.addAction(action)


def _form(parent=None) -> QFormLayout:
    """A form whose rows wrap when the column is narrow.

    The right-hand column holds five stacked groups, and without this the
    widest row in any of them sets a minimum width for all of them - which
    is what was clipping the panel at its default size.
    """
    form = QFormLayout(parent) if parent is not None else QFormLayout()
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    return form


class SegmentsEditor(QWidget):
    """The lit runs of a Bitscroll layer.

    This is where multi-segment bitscroll becomes designable. The C++ has
    always taken a vector of them; codegen.py and engine.py have both
    hard-coded exactly one since the feature shipped, so a preset bitscroll
    can only ever be one colour.
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[QWidget, ColorButton, QSpinBox]] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self.add_btn = QPushButton(theme.icon("plus"), " Segment")
        self.add_btn.clicked.connect(lambda: self._append(0xFF0000, 3, emit=True))
        self._layout.addWidget(self.add_btn)

    def _append(self, color: int, width: int, emit: bool = False) -> None:
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        swatch = ColorButton(color)
        spin = _spin(1, 64, width, " px")
        remove = QPushButton(theme.icon("minus"), "")
        remove.setProperty("role", "icon")
        box.addWidget(swatch)
        box.addWidget(spin, 1)
        box.addWidget(remove)
        swatch.color_changed.connect(self.changed)
        spin.valueChanged.connect(self.changed)
        remove.clicked.connect(lambda: self._remove(row))
        self._layout.insertWidget(self._layout.count() - 1, row)
        self._rows.append((row, swatch, spin))
        if emit:
            self.changed.emit()

    def _remove(self, row: QWidget) -> None:
        for entry in list(self._rows):
            if entry[0] is row:
                self._rows.remove(entry)
                row.setParent(None)
                row.deleteLater()
                self.changed.emit()
                return

    def set_segments(self, segments: list[BitScrollSegmentConfig]) -> None:
        for row, _s, _w in list(self._rows):
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        for seg in segments or [BitScrollSegmentConfig()]:
            self._append(seg.color, seg.width)

    def segments(self) -> list[BitScrollSegmentConfig]:
        return [BitScrollSegmentConfig(color=s.color(), width=w.value())
                for _r, s, w in self._rows]


class BindingsPanel(QGroupBox):
    """Up to four drivers wired to this layer's parameters.

    Hidden entirely until the composition has a driver: a row of empty
    dropdowns is the fastest way to make a novice think this is complicated.
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("Bindings", parent)
        self._loading = False
        self._drivers: list[DriverConfig] = []
        self._rows: list[dict] = []

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(4)
        self.add_btn = QPushButton(theme.icon("plus"), " Binding")
        self.add_btn.clicked.connect(self._add)
        self._layout.addWidget(self.add_btn)

    def set_drivers(self, drivers: list[DriverConfig]) -> None:
        self._drivers = drivers
        for row in self._rows:
            self._fill_driver_combo(row["driver"])
        self.setVisible(bool(drivers))

    def _fill_driver_combo(self, combo: QComboBox) -> None:
        keep = combo.currentIndex()
        combo.blockSignals(True)
        combo.clear()
        for i, drv in enumerate(self._drivers):
            combo.addItem(drv.name or f"Driver {i + 1}", i)
        if 0 <= keep < combo.count():
            combo.setCurrentIndex(keep)
        combo.blockSignals(False)

    def _add(self) -> None:
        if len(self._rows) >= MAX_BINDINGS:
            return
        self._append(BindingConfig(param=LayerParam.OPACITY))
        self._emit()

    def _append(self, binding: BindingConfig) -> None:
        row = QWidget()
        outer = QVBoxLayout(row)
        outer.setContentsMargins(0, 2, 0, 6)
        outer.setSpacing(2)
        box = QHBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        below = QHBoxLayout()
        below.setContentsMargins(12, 0, 0, 0)
        below.setSpacing(4)
        outer.addLayout(box)
        outer.addLayout(below)

        param = _combo(LAYER_PARAM_LABELS)
        param.setCurrentIndex(max(0, list(LAYER_PARAM_LABELS).index(binding.param)))
        driver = QComboBox()
        self._fill_driver_combo(driver)
        if 0 <= binding.driver < driver.count():
            driver.setCurrentIndex(binding.driver)
        at_zero = _spin(-255, 255, binding.at_zero)
        at_zero.setMaximumWidth(70)
        at_full = _spin(-255, 255, binding.at_full)
        at_full.setMaximumWidth(70)
        at_zero.setToolTip("What the parameter reads when the driver is at 0")
        at_full.setToolTip("What it reads when the driver is at 255. Below the "
                           "other value reverses the binding.")
        curve_btn = QPushButton()
        curve_btn.setToolTip("Reshape the driver before it is applied. A curve "
                            "that steps makes this a threshold.")
        remove = QPushButton(theme.icon("minus"), "")
        remove.setProperty("role", "icon")

        entry = {"row": row, "param": param, "driver": driver, "at_zero": at_zero,
                 "at_full": at_full, "curve_btn": curve_btn,
                 "curve": list(binding.curve)}
        self._set_curve_text(entry)

        for w in (param, driver):
            w.currentIndexChanged.connect(self._emit)
        for w in (at_zero, at_full):
            w.valueChanged.connect(self._emit)
        curve_btn.clicked.connect(lambda: self._edit_curve(entry))
        remove.clicked.connect(lambda: self._remove(entry))

        # What is bound to what, then - indented under it - the range it
        # maps onto. One line held six controls and wanted 790px in a column
        # half that wide, which is what was clipping the panel.
        box.addWidget(param, 3)
        box.addWidget(QLabel("←"))
        box.addWidget(driver, 3)
        box.addWidget(remove)
        below.addWidget(QLabel("0 →"))
        below.addWidget(at_zero, 1)
        below.addWidget(QLabel("255 →"))
        below.addWidget(at_full, 1)
        below.addWidget(curve_btn, 1)

        self._layout.insertWidget(self._layout.count() - 1, row)
        self._rows.append(entry)
        self.add_btn.setEnabled(len(self._rows) < MAX_BINDINGS)

    @staticmethod
    def _set_curve_text(entry: dict) -> None:
        entry["curve_btn"].setText("Linear" if is_linear(entry["curve"]) else "Curve")

    def _edit_curve(self, entry: dict) -> None:
        dialog = CurveDialog(entry["curve"], self)
        if dialog.exec():
            entry["curve"] = dialog.curve()
            self._set_curve_text(entry)
            self._emit()
        # Parented so it opens over the panel, which also means nothing else
        # frees it: every curve edit would leave a dialog behind.
        dialog.deleteLater()

    def _remove(self, entry: dict) -> None:
        if entry in self._rows:
            self._rows.remove(entry)
            entry["row"].setParent(None)
            entry["row"].deleteLater()
            self.add_btn.setEnabled(True)
            self._emit()

    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit()

    def load(self, layer: LayerConfig) -> None:
        self._loading = True
        for entry in list(self._rows):
            entry["row"].setParent(None)
            entry["row"].deleteLater()
        self._rows = []
        for binding in layer.bindings[:MAX_BINDINGS]:
            self._append(binding)
        self.add_btn.setEnabled(len(self._rows) < MAX_BINDINGS)
        self._loading = False

    def save(self, layer: LayerConfig) -> None:
        out = []
        for entry in self._rows:
            param = entry["param"].currentData()
            if param == LayerParam.NONE:
                continue
            out.append(BindingConfig(
                param=param,
                driver=max(0, entry["driver"].currentIndex()),
                at_zero=entry["at_zero"].value(),
                at_full=entry["at_full"].value(),
                curve=list(entry["curve"]),
            ))
        layer.bindings = out


class DriversPanel(QGroupBox):
    """What the composition watches. Composition-scoped, not per layer."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("Drivers", parent)
        self._loading = False
        self._comp: CompositionConfig | None = None

        self.list = QListWidget()
        self.list.setMaximumHeight(90)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.add_btn = QPushButton(theme.icon("plus"), " Add")
        self.remove_btn = QPushButton(theme.icon("minus"), " Remove")
        self.remove_btn.setProperty("role", "danger")
        buttons.addWidget(self.add_btn)
        buttons.addWidget(self.remove_btn)
        buttons.addStretch(1)

        form = _form()
        form.setContentsMargins(0, 6, 0, 0)
        self.name_edit = QLineEdit()
        self.kind_combo = _combo(DRIVER_KIND_LABELS)
        self.value_spin = _spin(0, 255, 128)
        self.source_combo = QComboBox()
        for source_id in fill_sources.ORDER:
            self.source_combo.addItem(fill_sources.get(source_id).label, source_id)
        self.port_spin = _spin(1, 21, 1)
        # The Design tab's own range fields. A source reads in its own units,
        # up to 36000 centidegrees, and a spin narrower than that clamped a
        # range opened from a preset - which the next edit to any field here
        # then saved.
        self.empty_spin = _reading_spin()
        self.full_spin = _reading_spin()
        self.full_spin.setValue(100)
        self.wrap_check = QCheckBox()
        self.smoothing_spin = _spin(0, 99, 0)
        self.band_combo = QComboBox()
        from ..envelope import BAND_LABELS
        for band, label in BAND_LABELS.items():
            self.band_combo.addItem(label, band)
        self.sensitivity_spin = _spin(0, 255, 100, " %")
        self.shape_combo = _combo(LFO_SHAPE_LABELS)
        self.period_spin = _spin(1, 60000, 1000, " ms")
        self.phase_spin = _spin(0, 99, 0, " %")

        self._rows: dict[str, tuple] = {}

        def add_row(key: str, label: str, widget) -> None:
            caption = QLabel(label)
            form.addRow(caption, widget)
            self._rows[key] = (caption, widget)

        add_row("name", "Name", self.name_edit)
        add_row("kind", "Kind", self.kind_combo)
        add_row("value", "Value", self.value_spin)
        add_row("source", "Reads", self.source_combo)
        add_row("port", "Port", self.port_spin)
        add_row("empty", "Empty At", self.empty_spin)
        add_row("full", "Full At", self.full_spin)
        add_row("wrap", "Wrap", self.wrap_check)
        add_row("smoothing", "Smoothing", self.smoothing_spin)
        add_row("band", "Band", self.band_combo)
        add_row("sensitivity", "Sensitivity", self.sensitivity_spin)
        add_row("shape", "Shape", self.shape_combo)
        add_row("period", "Period", self.period_spin)
        add_row("phase", "Phase", self.phase_spin)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        layout.addLayout(form)

        self.add_btn.clicked.connect(self._add)
        self.remove_btn.clicked.connect(self._remove)
        self.list.currentRowChanged.connect(self._on_selected)
        self.kind_combo.currentIndexChanged.connect(self._on_kind)
        self.name_edit.textChanged.connect(self._on_field)
        for w in (self.value_spin, self.port_spin, self.empty_spin, self.full_spin,
                  self.smoothing_spin, self.sensitivity_spin, self.period_spin,
                  self.phase_spin):
            w.valueChanged.connect(self._on_field)
        self.wrap_check.toggled.connect(self._on_field)
        for w in (self.source_combo, self.band_combo, self.shape_combo):
            w.currentIndexChanged.connect(self._on_field)

    def load(self, comp: CompositionConfig | None) -> None:
        self._loading = True
        self._comp = comp
        self.list.clear()
        if comp is not None:
            for drv in comp.drivers:
                self.list.addItem(f"{drv.name}  ·  {DRIVER_KIND_LABELS[drv.kind]}")
            if comp.drivers:
                self.list.setCurrentRow(0)
        self._loading = False
        self._show_selected()

    def _current(self) -> DriverConfig | None:
        if self._comp is None:
            return None
        row = self.list.currentRow()
        if 0 <= row < len(self._comp.drivers):
            return self._comp.drivers[row]
        return None

    def _add(self) -> None:
        if self._comp is None or len(self._comp.drivers) >= MAX_DRIVERS:
            return
        self._comp.drivers.append(DriverConfig(name=f"Driver {len(self._comp.drivers) + 1}"))
        self.load(self._comp)
        self.list.setCurrentRow(len(self._comp.drivers) - 1)
        self.changed.emit()

    def _remove(self) -> None:
        if self._comp is None:
            return
        # By row, not by value: two drivers added from the same preset are
        # equal, and list.index() found the first of them whichever one was
        # selected - rewiring the other layer's bindings.
        index = self.list.currentRow()
        if not 0 <= index < len(self._comp.drivers):
            return
        # A binding naming a driver past the end validates as an error, so
        # every binding above the removed index shifts down with it and any
        # binding that pointed *at* it is dropped.
        self._comp.drivers.pop(index)
        for layer in self._comp.layers:
            kept = []
            for b in layer.bindings:
                if b.driver == index:
                    continue
                if b.driver > index:
                    b.driver -= 1
                kept.append(b)
            layer.bindings = kept
        self.load(self._comp)
        self.changed.emit()

    def _on_selected(self, _row: int) -> None:
        if not self._loading:
            self._show_selected()

    def _on_kind(self, _index: int) -> None:
        if self._loading:
            return
        self._on_field()
        self._show_selected()

    def _on_field(self, *_args) -> None:
        if self._loading:
            return
        drv = self._current()
        if drv is None:
            return
        drv.name = self.name_edit.text() or drv.name
        drv.kind = self.kind_combo.currentData()
        drv.value = self.value_spin.value()
        drv.source = self.source_combo.currentData()
        drv.source_port = self.port_spin.value()
        drv.source_empty = self.empty_spin.value()
        drv.source_full = self.full_spin.value()
        drv.source_wrap = self.wrap_check.isChecked()
        drv.smoothing = self.smoothing_spin.value()
        drv.band = self.band_combo.currentData()
        drv.sensitivity = self.sensitivity_spin.value()
        drv.shape = self.shape_combo.currentData()
        drv.period_ms = self.period_spin.value()
        drv.phase_pct = self.phase_spin.value()

        row = self.list.currentRow()
        if 0 <= row < self.list.count():
            self.list.item(row).setText(f"{drv.name}  ·  {DRIVER_KIND_LABELS[drv.kind]}")
        self.changed.emit()

    def _show_selected(self) -> None:
        drv = self._current()
        enabled = drv is not None
        for caption, widget in self._rows.values():
            caption.setEnabled(enabled)
            widget.setEnabled(enabled)
        self.remove_btn.setEnabled(enabled)
        if drv is None:
            self._set_visible(set())
            return

        self._loading = True
        self.name_edit.setText(drv.name)
        self.kind_combo.setCurrentIndex(list(DRIVER_KIND_LABELS).index(drv.kind))
        self.value_spin.setValue(drv.value)
        idx = self.source_combo.findData(drv.source)
        if idx >= 0:
            self.source_combo.setCurrentIndex(idx)
        self.port_spin.setValue(drv.source_port)
        self.empty_spin.setValue(int(drv.source_empty))
        self.full_spin.setValue(int(drv.source_full))
        self.wrap_check.setChecked(drv.source_wrap)
        self.smoothing_spin.setValue(drv.smoothing)
        band_idx = self.band_combo.findData(drv.band)
        if band_idx >= 0:
            self.band_combo.setCurrentIndex(band_idx)
        self.sensitivity_spin.setValue(drv.sensitivity)
        self.shape_combo.setCurrentIndex(list(LFO_SHAPE_LABELS).index(drv.shape))
        self.period_spin.setValue(drv.period_ms)
        self.phase_spin.setValue(drv.phase_pct)
        self._loading = False

        shown = {"name", "kind"}
        if drv.kind == DriverKind.CONSTANT:
            shown |= {"value"}
        elif drv.kind == DriverKind.SENSOR:
            shown |= {"source", "empty", "full", "wrap", "smoothing"}
            if fill_sources.get(drv.source).port_kind:
                shown |= {"port"}
        elif drv.kind == DriverKind.MUSIC:
            shown |= {"band", "sensitivity"}
        elif drv.kind == DriverKind.LFO:
            shown |= {"shape", "period", "phase"}
        self._set_visible(shown)

    def _set_visible(self, keys: set[str]) -> None:
        for key, (caption, widget) in self._rows.items():
            visible = key in keys
            caption.setVisible(visible)
            widget.setVisible(visible)


class LayerInspector(QWidget):
    """Everything about one layer: what it draws, where, when, and how it
    lands on the layers below it."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._layer: LayerConfig | None = None
        # The preview strip's length, which a new drawing takes its width
        # from. The panel keeps it current.
        self._strip_length = DEFAULT_PREVIEW_LENGTH

        self.kind_combo = _combo(LAYER_KIND_LABELS)
        self.name_edit = QLineEdit()

        # -- what it draws
        gen = QGroupBox("Generator")
        gen_form = _form(gen)
        self.color_btn = ColorButton(0xFFFFFF)
        self.color2_btn = ColorButton(0x0000FF)
        self.bg_btn = ColorButton(0x000000)
        self.run_length_spin = _spin(1, 64, 5, " px")
        # Pixels every so often, as the Design tab's presets. 0 px holds a
        # layer still; a binding on SPEED moves the pixels, not the time.
        self.speed_edit = SpeedEdit(min_pixels=0, max_pixels=255)
        self.speed_spin = self.speed_edit.pixels_spin
        self.invert_check = QCheckBox()
        self.bounce_check = QCheckBox()
        self.seamless_check = QCheckBox()
        self.on_ms_spin = _spin(1, 10000, 250, " ms")
        self.off_ms_spin = _spin(1, 10000, 250, " ms")
        self.palette_edit = QLineEdit()
        self.palette_edit.setToolTip("Comma-separated hex colors, e.g. FF0000, 00FF00")
        self.density_spin = _spin(0, 100, 30, " %")
        self.fade_spin = _spin(1, 255, 16)
        self.segments_editor = SegmentsEditor()
        self.spacing_spin = _spin(0, 64, 5, " px")
        self.repeating_check = QCheckBox()
        self.gradient_check = QCheckBox()
        self.style_combo = QComboBox()
        from ..models import GAUGE_BLEND_LABELS, GAUGE_STYLE_LABELS
        for value, label in GAUGE_STYLE_LABELS.items():
            self.style_combo.addItem(label, value)
        self.gauge_blend_combo = QComboBox()
        for value, label in GAUGE_BLEND_LABELS.items():
            self.gauge_blend_combo.addItem(label, value)
        self.stops_editor = GaugeStopsEditor()
        self.empty_spin = QDoubleSpinBox()
        self.empty_spin.setRange(-10000, 10000)
        self.full_spin = QDoubleSpinBox()
        self.full_spin.setRange(-10000, 10000)
        self.full_spin.setValue(100)
        self.frames_editor = PixelGridEditor()

        self._gen_rows: dict[str, tuple] = {}

        def gen_row(key: str, label: str, widget) -> None:
            caption = QLabel(label)
            gen_form.addRow(caption, widget)
            self._gen_rows[key] = (caption, widget)

        gen_row("color", "Color", self.color_btn)
        gen_row("color2", "To Color", self.color2_btn)
        gen_row("bg", "Background", self.bg_btn)
        gen_row("run_length", "Run Length", self.run_length_spin)
        gen_row("speed", "Speed", self.speed_edit)
        gen_row("invert", "Reverse", self.invert_check)
        gen_row("bounce", "Bounce", self.bounce_check)
        gen_row("seamless", "Seamless", self.seamless_check)
        gen_row("on_ms", "On", self.on_ms_spin)
        gen_row("off_ms", "Off", self.off_ms_spin)
        gen_row("palette", "Palette", self.palette_edit)
        gen_row("density", "Density", self.density_spin)
        gen_row("fade_step", "Fade Step", self.fade_spin)
        gen_row("segments", "Segments", self.segments_editor)
        gen_row("spacing", "Spacing", self.spacing_spin)
        gen_row("repeating", "Tile", self.repeating_check)
        gen_row("gradient", "Gradient", self.gradient_check)
        gen_row("style", "Style", self.style_combo)
        gen_row("gauge_blend", "Scale", self.gauge_blend_combo)
        gen_row("stops", "Stops", self.stops_editor)
        gen_row("range", "Empty At", self.empty_spin)
        gen_row("range2", "Full At", self.full_spin)
        gen_row("frames", "Drawing", self.frames_editor)

        # -- where it lands
        place = QGroupBox("Placement")
        place_form = _form(place)
        self.start_spin = _spin(0, 63, 0, " px")
        self.width_spin = _spin(0, 64, 0, " px")
        self.width_spin.setSpecialValueText("to end")
        self.stride_spin = _spin(0, 64, 0, " px")
        self.stride_spin.setSpecialValueText("once")
        self.stride_spin.setToolTip("Repeat this layer every N pixels along the strip")
        self.continuous_check = QCheckBox()
        self.continuous_check.setToolTip(
            "Repeats sample one continuous animation rather than each "
            "restarting it")
        place_form.addRow("Start", self.start_spin)
        place_form.addRow("Width", self.width_spin)
        place_form.addRow("Repeat Every", self.stride_spin)
        place_form.addRow("Continuous", self.continuous_check)

        # -- how it lands
        mix = QGroupBox("Compositing")
        mix_form = _form(mix)
        self.blend_combo = _combo(LAYER_BLEND_LABELS)
        for i, value in enumerate(LAYER_BLEND_LABELS):
            self.blend_combo.setItemData(i, LAYER_BLEND_HELP[value], Qt.ToolTipRole)
        self.opacity_spin = _spin(0, 255, 255)
        mix_form.addRow("Blend", self.blend_combo)
        mix_form.addRow("Opacity", self.opacity_spin)

        # -- when it is on
        self.timing_group = QGroupBox("Timing")
        timing_form = _form(self.timing_group)
        self.always_check = QCheckBox("Always on")
        self.always_check.setChecked(True)
        self.start_ms_spin = _spin(0, 60000, 0, " ms")
        self.end_ms_spin = _spin(0, 60000, 0, " ms")
        self.end_ms_spin.setSpecialValueText("loop end")
        self.fade_in_spin = _spin(0, 60000, 0, " ms")
        self.fade_out_spin = _spin(0, 60000, 0, " ms")
        self.retrigger_check = QCheckBox()
        self.retrigger_check.setToolTip(
            "Restart this layer from its first frame each time its window opens")
        self._timing_rows: list[tuple] = []
        for label, widget in (("Starts", self.start_ms_spin), ("Ends", self.end_ms_spin),
                              ("Fade In", self.fade_in_spin),
                              ("Fade Out", self.fade_out_spin),
                              ("Retrigger", self.retrigger_check)):
            caption = QLabel(label)
            timing_form.addRow(caption, widget)
            self._timing_rows.append((caption, widget))
        timing_form.insertRow(0, "", self.always_check)

        self.bindings_panel = BindingsPanel()

        head = _form()
        head.addRow("Name", self.name_edit)
        head.addRow("Kind", self.kind_combo)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(head)
        layout.addWidget(gen)
        layout.addWidget(place)
        layout.addWidget(mix)
        layout.addWidget(self.timing_group)
        layout.addWidget(self.bindings_panel)
        layout.addStretch(1)

        self.kind_combo.currentIndexChanged.connect(self._on_kind)
        self.always_check.toggled.connect(self._on_always)
        # Ahead of _emit below, so a blank drawing is refitted before the
        # edit is saved rather than after.
        for w in (self.start_spin, self.width_spin):
            w.valueChanged.connect(self._on_placement)
        self.invert_check.toggled.connect(self.frames_editor.set_reverse)
        self.name_edit.textChanged.connect(self._emit)
        for w in (self.color_btn, self.color2_btn, self.bg_btn):
            w.color_changed.connect(self._emit)
        self.speed_edit.changed.connect(self._emit)
        for w in (self.run_length_spin, self.on_ms_spin,
                  self.off_ms_spin, self.density_spin, self.fade_spin,
                  self.spacing_spin, self.start_spin, self.width_spin,
                  self.stride_spin, self.opacity_spin, self.start_ms_spin,
                  self.end_ms_spin, self.fade_in_spin, self.fade_out_spin):
            w.valueChanged.connect(self._emit)
        for w in (self.empty_spin, self.full_spin):
            w.valueChanged.connect(self._emit)
        for w in (self.invert_check, self.bounce_check, self.seamless_check,
                  self.repeating_check, self.gradient_check,
                  self.continuous_check, self.retrigger_check):
            w.toggled.connect(self._emit)
        for w in (self.style_combo, self.gauge_blend_combo, self.blend_combo):
            w.currentIndexChanged.connect(self._emit)
        self.palette_edit.textChanged.connect(self._emit)
        self.segments_editor.changed.connect(self._emit)
        self.stops_editor.changed.connect(self._emit)
        self.frames_editor.changed.connect(self._emit)
        self.bindings_panel.changed.connect(self._emit)

    # -- visibility -------------------------------------------------------

    def _on_kind(self, _index: int) -> None:
        if self._loading:
            return
        self._update_visibility()
        if self.kind_combo.currentData() == LayerKind.FRAMES:
            self._sync_frame_fit()
        self._emit()

    def _on_placement(self, *_args) -> None:
        if not self._loading:
            self._sync_frame_fit()

    # -- drawn frames' width ------------------------------------------------

    def set_strip_length(self, length: int) -> None:
        """The preview strip's length. A drawing with nothing in it yet
        follows it, so a new drawing is exactly as wide as the strip."""
        self._strip_length = max(1, length)
        self._sync_frame_fit()

    def _frame_span(self, start: int, width: int) -> int:
        """How many pixels this layer covers on the preview strip: its own
        width when it has one, else from its start to the end."""
        return width if width else max(1, self._strip_length - min(start, self._strip_length))

    def _sync_frame_fit(self) -> None:
        self.frames_editor.set_fit_width(
            self._frame_span(self.start_spin.value(), self.width_spin.value()),
            self._strip_length)

    def _on_always(self, _on: bool) -> None:
        self._update_timing_visibility()
        if not self._loading:
            self._emit()

    def _update_timing_visibility(self) -> None:
        # Collapsed behind one checkbox: timing is the part of a layer most
        # people never touch, and five always-visible fields would make the
        # panel look like it demands them.
        shown = not self.always_check.isChecked()
        for caption, widget in self._timing_rows:
            caption.setVisible(shown)
            widget.setVisible(shown)

    def _update_visibility(self) -> None:
        kind = self.kind_combo.currentData()
        shown = set(_VISIBLE.get(kind, set()))
        if "range" in shown:
            shown.add("range2")
        for key, (caption, widget) in self._gen_rows.items():
            visible = key in shown
            caption.setVisible(visible)
            widget.setVisible(visible)
        self._update_timing_visibility()

    def _emit(self, *_args) -> None:
        if not self._loading:
            self.changed.emit()

    # -- load / save ------------------------------------------------------

    def load(self, layer: LayerConfig | None, drivers: list[DriverConfig]) -> None:
        self.setEnabled(layer is not None)
        self.bindings_panel.set_drivers(drivers)
        if layer is None:
            return
        self._loading = True
        self._layer = layer
        from ..widgets import format_palette

        self.name_edit.setText(layer.name)
        self.kind_combo.setCurrentIndex(list(LAYER_KIND_LABELS).index(layer.kind))
        self.color_btn.set_color(layer.color)
        self.color2_btn.set_color(layer.color2)
        self.bg_btn.set_color(layer.bg_color)
        self.run_length_spin.setValue(max(1, layer.run_length))
        self.speed_edit.set_speed(layer.speed, layer.step_ms)
        self.invert_check.setChecked(layer.invert)
        self.bounce_check.setChecked(layer.bounce)
        self.seamless_check.setChecked(layer.seamless)
        self.on_ms_spin.setValue(layer.on_ms)
        self.off_ms_spin.setValue(layer.off_ms)
        self.palette_edit.setText(format_palette(layer.palette))
        self.density_spin.setValue(layer.density_pct)
        self.fade_spin.setValue(layer.fade_step)
        self.segments_editor.set_segments(layer.segments)
        self.spacing_spin.setValue(layer.spacing)
        self.repeating_check.setChecked(layer.repeating)
        self.gradient_check.setChecked(layer.gradient)
        self.style_combo.setCurrentIndex(max(0, self.style_combo.findData(layer.style)))
        self.gauge_blend_combo.setCurrentIndex(
            max(0, self.gauge_blend_combo.findData(layer.gauge_blend)))
        self.stops_editor.set_stops(layer.stops)
        self.empty_spin.setValue(layer.empty_at)
        self.full_spin.setValue(layer.full_at)
        # The span first: a blank drawing takes its width from it as it loads.
        self.frames_editor.set_fit_width(self._frame_span(layer.start, layer.width),
                                         self._strip_length)
        self.frames_editor.load(layer)

        self.start_spin.setValue(layer.start)
        self.width_spin.setValue(layer.width)
        self.stride_spin.setValue(layer.stride)
        self.continuous_check.setChecked(layer.continuous)
        self.blend_combo.setCurrentIndex(list(LAYER_BLEND_LABELS).index(layer.blend))
        self.opacity_spin.setValue(layer.opacity)

        self.always_check.setChecked(layer.start_ms == 0 and layer.end_ms == 0)
        self.start_ms_spin.setValue(layer.start_ms)
        self.end_ms_spin.setValue(layer.end_ms)
        self.fade_in_spin.setValue(layer.fade_in_ms)
        self.fade_out_spin.setValue(layer.fade_out_ms)
        self.retrigger_check.setChecked(layer.retrigger)

        self.bindings_panel.load(layer)
        self._update_visibility()
        self._loading = False

    def load_placement(self, layer: LayerConfig) -> None:
        """Only where and when @p layer is on - all that dragging it on the
        span or timeline strips can change.

        A drag lands here once per mouse-move. The full load() rebuilt every
        binding row and reloaded the drawing each time, which was most of
        what a move cost, and it put the drawing's brush back to its first
        colour mid-drag.
        """
        self._loading = True
        self.start_spin.setValue(layer.start)
        self.width_spin.setValue(layer.width)
        self.always_check.setChecked(layer.start_ms == 0 and layer.end_ms == 0)
        self.start_ms_spin.setValue(layer.start_ms)
        self.end_ms_spin.setValue(layer.end_ms)
        # A blank drawing follows its layer's new span, as it does when the
        # span is typed in.
        self._sync_frame_fit()
        self._loading = False

    def save(self, layer: LayerConfig) -> None:
        from ..widgets import parse_palette

        layer.name = self.name_edit.text() or layer.name
        layer.kind = self.kind_combo.currentData()
        layer.color = self.color_btn.color()
        layer.color2 = self.color2_btn.color()
        layer.bg_color = self.bg_btn.color()
        layer.run_length = self.run_length_spin.value()
        layer.speed = self.speed_edit.pixels()
        layer.step_ms = self.speed_edit.step_ms()
        layer.invert = self.invert_check.isChecked()
        layer.bounce = self.bounce_check.isChecked()
        layer.seamless = self.seamless_check.isChecked()
        layer.on_ms = self.on_ms_spin.value()
        layer.off_ms = self.off_ms_spin.value()
        parsed = parse_palette(self.palette_edit.text())
        if parsed:
            layer.palette = parsed
        layer.density_pct = self.density_spin.value()
        layer.fade_step = self.fade_spin.value()
        layer.segments = self.segments_editor.segments()
        layer.spacing = self.spacing_spin.value()
        layer.repeating = self.repeating_check.isChecked()
        layer.gradient = self.gradient_check.isChecked()
        layer.style = self.style_combo.currentData()
        layer.gauge_blend = self.gauge_blend_combo.currentData()
        layer.stops = self.stops_editor.stops()
        layer.empty_at = self.empty_spin.value()
        layer.full_at = self.full_spin.value()
        self.frames_editor.save(layer)

        layer.start = self.start_spin.value()
        layer.width = self.width_spin.value()
        layer.stride = self.stride_spin.value()
        layer.continuous = self.continuous_check.isChecked()
        layer.blend = self.blend_combo.currentData()
        layer.opacity = self.opacity_spin.value()

        if self.always_check.isChecked():
            layer.start_ms = 0
            layer.end_ms = 0
        else:
            layer.start_ms = self.start_ms_spin.value()
            layer.end_ms = self.end_ms_spin.value()
        layer.fade_in_ms = self.fade_in_spin.value()
        layer.fade_out_ms = self.fade_out_spin.value()
        layer.retrigger = self.retrigger_check.isChecked()

        self.bindings_panel.save(layer)


class AnimatorPanel(QWidget):
    """The whole Animate tab."""

    changed = Signal()
    #: Emitted when the user asks for a composition to be played by the
    #: strand they were last editing - the "use this" shortcut back to Design.
    use_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        # Whether the user wants the preview running. Held separately from
        # whether a session exists, so switching tabs or rebuilding the
        # strand does not silently start something they had paused.
        self._playing = True
        self._compositions: list[CompositionConfig] = []
        # Set by the window, which owns id allocation - ids must stay
        # unique across an import too, not just within this panel.
        self._new_id = None
        self._offered_templates = False

        # -- left: the library, then its layers
        self.library = QListWidget()
        self.library.setMinimumWidth(140)
        self.library.setTextElideMode(Qt.ElideRight)
        lib_buttons = QHBoxLayout()
        lib_buttons.setSpacing(4)
        # Text on the one that starts something, icons on the rest: every
        # pixel this column takes is one the preview does not get.
        self.add_comp_btn = QPushButton(theme.icon("plus"), " New")
        self.dup_comp_btn = QPushButton(theme.icon("plus"), "")
        self.dup_comp_btn.setProperty("role", "icon")
        self.dup_comp_btn.setToolTip("Duplicate this animation")
        self.del_comp_btn = QPushButton(theme.icon("minus"), "")
        self.del_comp_btn.setProperty("role", "icon")
        self.del_comp_btn.setToolTip("Delete this animation")
        for b in (self.add_comp_btn, self.dup_comp_btn, self.del_comp_btn):
            lib_buttons.addWidget(b)
        lib_buttons.addStretch(1)

        self.layers = QListWidget()
        self.layers.setMinimumWidth(140)
        self.layers.setTextElideMode(Qt.ElideRight)
        layer_buttons = QHBoxLayout()
        layer_buttons.setSpacing(4)
        self.add_layer_btn = QPushButton(theme.icon("plus"), " Layer")
        self.preset_btn = QPushButton("Preset")
        self.preset_btn.setToolTip(
            "Add a preset animation as a layer, or a custom preset's layers")
        self.del_layer_btn = QPushButton(theme.icon("minus"), "")
        self.del_layer_btn.setProperty("role", "danger")
        self.up_btn = QPushButton(theme.icon("arrow-up"), "")
        self.down_btn = QPushButton(theme.icon("arrow-down"), "")
        for b in (self.del_layer_btn, self.up_btn, self.down_btn):
            b.setProperty("role", "icon")
        self.up_btn.setToolTip("Move up - later layers paint over earlier ones")
        self.down_btn.setToolTip("Move down")
        for b in (self.add_layer_btn, self.preset_btn, self.del_layer_btn,
                  self.up_btn, self.down_btn):
            layer_buttons.addWidget(b)
        layer_buttons.addStretch(1)

        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(6, 6, 6, 6)
        left_box.setSpacing(4)
        lib_label = QLabel("ANIMATIONS")
        lib_label.setProperty("role", "sectionHeader")
        layers_label = QLabel("LAYERS  (bottom first)")
        layers_label.setProperty("role", "sectionHeader")
        left_box.addWidget(lib_label)
        left_box.addWidget(self.library, 1)
        left_box.addLayout(lib_buttons)
        left_box.addSpacing(6)
        left_box.addWidget(layers_label)
        left_box.addWidget(self.layers, 2)
        left_box.addLayout(layer_buttons)
        # No hand-picked floor: the layer buttons are the widest row, and a
        # guessed 190px sat under them and cut "Layer" and "Preset" off. The
        # layout's own minimum is exactly what they need. Capped, though -
        # this column lists things, and letting it grow to its longest row
        # takes the width from the preview.
        left.setMaximumWidth(max(300, left.sizeHint().width()))

        # -- centre: the preview, and what it is previewing on
        self.canvas = StripCanvas()
        self.name_edit = QLineEdit()
        self.loop_spin = _spin(0, 60000, 0, " ms")
        self.loop_spin.setSpecialValueText("none")
        self.loop_spin.setToolTip(
            "How long the timeline is before it repeats. Layers with a timing "
            "window are placed inside it.")
        self.bg_btn = ColorButton(0x000000)
        self.length_spin = _spin(1, 64, DEFAULT_PREVIEW_LENGTH, " px")
        self.length_spin.setToolTip("Preview length. A real strip is about 20.")
        self.use_btn = QPushButton("Use on Strand")
        self.use_btn.setToolTip(
            "Play this animation on the strand selected in the Design tab")

        # The preview's own transport. No Reset: a composition has no state
        # worth clearing that playing again does not already start over, and
        # the Design tab's Reset exists to blank a strip, which this scratch
        # strand is not.
        self.play_btn = QPushButton(theme.icon("play"), " Play")
        self.pause_btn = QPushButton(theme.icon("pause"), " Pause")
        for b in (self.play_btn, self.pause_btn):
            b.setProperty("role", "transport")
            b.setIconSize(QSize(13, 13))
            theme.HoverBloom(b, theme.FOCUS, radius=14, alpha=105)
        self.play_btn.setToolTip("Run the preview")
        self.pause_btn.setToolTip("Hold the preview where it is")

        # Two rows: what this is and whether it is running, then the three
        # settings of the timeline and preview under it. On one row the full
        # labels wanted more width than the centre column has once the left
        # one is wide enough for its buttons, and abbreviating them to "BG"
        # and "Len" was the price of squeezing it in.
        self.name_edit.setMinimumWidth(110)
        self.loop_spin.setMaximumWidth(96)
        self.length_spin.setMaximumWidth(86)
        self.bg_btn.setToolTip("What the strip starts as, under the bottom layer")
        # No "Name" caption: the field is first in the row and shows the
        # name, and the label was costing more header width than it earned.
        self.name_edit.setPlaceholderText("Animation name")
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_row.addWidget(self.name_edit, 1)
        title_row.addWidget(self.play_btn)
        title_row.addWidget(self.pause_btn)

        settings_row = QHBoxLayout()
        settings_row.setSpacing(6)
        settings_row.addWidget(QLabel("Loop"))
        settings_row.addWidget(self.loop_spin)
        settings_row.addSpacing(8)
        settings_row.addWidget(QLabel("Background"))
        settings_row.addWidget(self.bg_btn)
        settings_row.addSpacing(8)
        length_label = QLabel("Length")
        length_label.setToolTip("How long the preview strip is. A real strip "
                                "is about 20 pixels.")
        settings_row.addWidget(length_label)
        settings_row.addWidget(self.length_spin)
        settings_row.addStretch(1)

        head = QVBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        head.addLayout(title_row)
        head.addLayout(settings_row)

        self.budget_label = QLabel()
        self.budget_label.setProperty("role", "hint")

        # Directly under the canvas, on the same pixel axis, so a bar lines
        # up with the LEDs its layer owns. This is what replaces doing
        # arithmetic in a Start and a Width spin box.
        self.span_ruler = SpanRuler()
        span_label = QLabel("SPAN")
        span_label.setProperty("role", "sectionHeader")
        self.timeline = TimelineStrip()
        time_label = QLabel("TIMELINE")
        time_label.setProperty("role", "sectionHeader")

        # The header has a minimum width of its own - three fields, a name
        # and two buttons - and left in the layout it would hold the centre
        # column open and squeeze the right one down to its minimum. Wrapped
        # in a scroll area it keeps any overflow to itself, which is the same
        # thing the Design tab does with its transport strip.
        head_host = QWidget()
        head_host.setLayout(head)
        head_scroll = QScrollArea()
        head_scroll.setWidget(head_host)
        head_scroll.setWidgetResizable(True)
        head_scroll.setFrameShape(QScrollArea.NoFrame)
        head_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        head_scroll.setFixedHeight(
            head_host.sizeHint().height() + 4
            + head_scroll.horizontalScrollBar().sizeHint().height())

        centre = QWidget()
        centre.setMinimumWidth(280)
        centre_box = QVBoxLayout(centre)
        centre_box.setContentsMargins(8, 6, 8, 8)
        centre_box.setSpacing(6)
        centre_box.addWidget(head_scroll)
        centre_box.addWidget(self.canvas, 1)
        centre_box.addWidget(span_label)
        centre_box.addWidget(self.span_ruler)
        centre_box.addWidget(time_label)
        centre_box.addWidget(self.timeline)

        # The budget on the left, the way out of the tab on the right. This
        # button used to sit in the header strip, where it was the first
        # thing to scroll out of sight on a narrow window.
        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addWidget(self.budget_label)
        foot.addStretch(1)
        foot.addWidget(self.use_btn)
        centre_box.addLayout(foot)

        # -- right: drivers over the selected layer
        self.drivers_panel = DriversPanel()
        self.layer_inspector = LayerInspector()
        self.layer_inspector.set_strip_length(self.length_spin.value())

        right_inner = QWidget()
        right_box = QVBoxLayout(right_inner)
        right_box.setContentsMargins(6, 6, 6, 6)
        right_box.setSpacing(8)
        right_box.addWidget(self.drivers_panel)
        right_box.addWidget(self.layer_inspector, 1)

        right = QScrollArea()
        right.setWidget(right_inner)
        right.setWidgetResizable(True)
        right.setMinimumWidth(420)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left)
        splitter.addWidget(centre)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        # The same three proportions the Design tab uses, so switching tabs
        # does not feel like switching applications.
        # Wider right column than the Design tab's, and measured rather
        # than guessed: a layer stacks a generator, placement,
        # compositing, timing and bindings, whose worst case wants ~515
        # even with the form rows wrapping. At 400 it clipped. The left one
        # opens at what its buttons need, for the same reason.
        splitter.setSizes([left.sizeHint().width(), 740, 560])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(splitter)

        # The preview runs on a throwaway strand, so exploring here does not
        # change what a teammate sees on the Design tab.
        self._session: StrandSession | None = None
        self.canvas.set_sessions([])

        self.library.currentRowChanged.connect(self._on_composition_selected)
        self.layers.currentRowChanged.connect(self._on_layer_selected)
        self.layers.itemChanged.connect(self._on_layer_item_changed)
        self.add_comp_btn.clicked.connect(self._new_composition)
        self.dup_comp_btn.clicked.connect(self._duplicate_composition)
        self.del_comp_btn.clicked.connect(self._delete_composition)
        self.add_layer_btn.clicked.connect(self._add_layer)
        self.preset_btn.clicked.connect(self._add_preset_layer)
        self.del_layer_btn.clicked.connect(self._delete_layer)
        self.up_btn.clicked.connect(lambda: self._move_layer(-1))
        self.down_btn.clicked.connect(lambda: self._move_layer(1))
        self.layer_inspector.changed.connect(self._on_layer_edited)
        self.drivers_panel.changed.connect(self._on_drivers_changed)
        self.name_edit.textChanged.connect(self._on_head_changed)
        self.loop_spin.valueChanged.connect(self._on_head_changed)
        self.bg_btn.color_changed.connect(self._on_head_changed)
        self.length_spin.valueChanged.connect(self.layer_inspector.set_strip_length)
        self.length_spin.valueChanged.connect(self._rebuild_preview)
        self.length_spin.valueChanged.connect(self._refresh_strips)
        self.use_btn.clicked.connect(self._on_use)
        self.play_btn.clicked.connect(self._on_play)
        self.pause_btn.clicked.connect(self._on_pause)
        for strip in (self.span_ruler, self.timeline):
            strip.changed.connect(self._on_strip_dragged)
            strip.selected.connect(self._on_strip_selected)

    # -- document ---------------------------------------------------------

    def set_library(self, compositions: list[CompositionConfig], next_id) -> None:
        """Point the panel at the document's library. `next_id` is a callable
        so the window keeps id allocation, which has to stay unique across
        imports too."""
        self._compositions = compositions
        self._new_id = next_id
        self.refresh()

    def refresh(self) -> None:
        # Held until the list is rebuilt, and _on_composition_selected waits
        # for it: clear() and setCurrentRow() both emit, and answering them
        # tore the layer list and the preview down and put the selection
        # back on the first layer - on every undo and every return to the tab.
        self._loading = True
        row = self.library.currentRow()
        self.library.clear()
        for comp in self._compositions:
            self.library.addItem(comp.name or "Animation")
        if self._compositions:
            self.library.setCurrentRow(min(max(row, 0), len(self._compositions) - 1))
        self._loading = False
        self._on_composition_selected(self.library.currentRow())

    def current_composition(self) -> CompositionConfig | None:
        row = self.library.currentRow()
        if 0 <= row < len(self._compositions):
            return self._compositions[row]
        return None

    def _current_layer(self) -> LayerConfig | None:
        comp = self.current_composition()
        row = self.layers.currentRow()
        if comp is not None and 0 <= row < len(comp.layers):
            return comp.layers[row]
        return None

    # -- library ----------------------------------------------------------

    def offer_templates_if_empty(self) -> None:
        """Open the gallery the first time an empty Animator is shown.

        An empty layer stack asks the user to know what a layer is before
        they can see one. A grid of running animations does not.

        Only once, and only when the tab is actually on screen: a modal
        dialog raised against a window nobody is looking at has nothing to
        return to, and would block whatever put it there.
        """
        if self._compositions or self._new_id is None or self._offered_templates:
            return
        if not self.isVisible():
            return
        self._offered_templates = True
        from .gallery import TemplateGalleryDialog

        dialog = TemplateGalleryDialog(self._new_id, self)
        if dialog.exec() and dialog.chosen() is not None:
            self._new_composition(comp=dialog.chosen())
        # A card's composition is a plain object, so the chosen one outlives
        # its card. The dialog and its fifteen strands need not.
        dialog.deleteLater()

    def _new_composition(self, _checked: bool = False,
                         comp: CompositionConfig | None = None) -> None:
        if comp is None:
            comp = CompositionConfig(
                id=self._new_id(), name=f"Animation {len(self._compositions) + 1}",
                layers=[LayerConfig(name="Base", kind=LayerKind.SOLID, color=0x2040FF)],
            )
        self._compositions.append(comp)
        self.refresh()
        self.library.setCurrentRow(len(self._compositions) - 1)
        self.changed.emit()

    def _duplicate_composition(self) -> None:
        comp = self.current_composition()
        if comp is None:
            return
        copy = deepcopy(comp)
        copy.id = self._new_id()
        copy.name = f"{comp.name} copy"
        self._new_composition(comp=copy)

    def _delete_composition(self) -> None:
        comp = self.current_composition()
        if comp is None:
            return
        self._compositions.remove(comp)
        self.refresh()
        self.changed.emit()

    def _on_composition_selected(self, _row: int) -> None:
        if self._loading:
            return
        comp = self.current_composition()
        has = comp is not None
        for w in (self.name_edit, self.loop_spin, self.bg_btn, self.add_layer_btn,
                  self.preset_btn, self.dup_comp_btn, self.del_comp_btn,
                  self.use_btn):
            w.setEnabled(has)
        self.drivers_panel.load(comp)
        self._refresh_layers()
        if has:
            self._loading = True
            self.name_edit.setText(comp.name)
            self.loop_spin.setValue(comp.loop_ms)
            self.bg_btn.set_color(comp.bg_color)
            self._loading = False
        self._reapply_preview()

    def _on_head_changed(self, *_args) -> None:
        comp = self.current_composition()
        if comp is None or self._loading:
            return
        comp.name = self.name_edit.text() or comp.name
        comp.loop_ms = self.loop_spin.value()
        comp.bg_color = self.bg_btn.color()
        row = self.library.currentRow()
        if 0 <= row < self.library.count():
            self._loading = True
            self.library.item(row).setText(comp.name)
            self._loading = False
        self._refresh_strips()
        self._reapply_preview()
        self.changed.emit()

    # -- layers -----------------------------------------------------------

    def _layer_text(self, layer: LayerConfig) -> str:
        where = "whole strip" if not layer.width else f"{layer.start}-{layer.start + layer.width - 1}"
        return f"{layer.name}  ·  {LAYER_KIND_LABELS[layer.kind]}  ·  {where}"

    def _refresh_layers(self) -> None:
        self._loading = True
        row = self.layers.currentRow()
        self.layers.clear()
        comp = self.current_composition()
        if comp is not None:
            for layer in comp.layers:
                item = QListWidgetItem(self._layer_text(layer))
                # A checkbox for mute, exactly as ModesPanel marks an active
                # mode - checked is on, which is the way round people read a
                # list of layers.
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked if layer.muted else Qt.Checked)
                self.layers.addItem(item)
            if comp.layers:
                self.layers.setCurrentRow(min(max(row, 0), len(comp.layers) - 1))
        self._loading = False
        self._on_layer_selected(self.layers.currentRow())
        self._refresh_strips()
        self._update_budget()

    def _on_layer_selected(self, _row: int) -> None:
        comp = self.current_composition()
        layer = self._current_layer()
        self.layer_inspector.load(layer, comp.drivers if comp else [])
        for b in (self.del_layer_btn, self.up_btn, self.down_btn):
            b.setEnabled(layer is not None)
        self.span_ruler.set_current(self.layers.currentRow())
        self.timeline.set_current(self.layers.currentRow())

    def _refresh_strips(self) -> None:
        comp = self.current_composition()
        layers = comp.layers if comp else []
        row = self.layers.currentRow()
        self.span_ruler.set_length(self.length_spin.value())
        self.span_ruler.set_layers(layers, row)
        self.timeline.set_loop_ms(comp.loop_ms if comp else 0)
        self.timeline.set_layers(layers, row)

    def _on_strip_dragged(self) -> None:
        """A bar was moved. The layer is already written; the panel catches
        up, rather than the drag having to know about every field."""
        layer = self._current_layer()
        if layer is None:
            return
        self.layer_inspector.load_placement(layer)
        row = self.layers.currentRow()
        if 0 <= row < self.layers.count():
            self._loading = True
            self.layers.item(row).setText(self._layer_text(layer))
            self._loading = False
        self._reapply_preview()
        self.changed.emit()

    def _on_strip_selected(self, row: int) -> None:
        if 0 <= row < self.layers.count():
            self.layers.setCurrentRow(row)

    def _on_layer_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        comp = self.current_composition()
        row = self.layers.row(item)
        if comp is None or not (0 <= row < len(comp.layers)):
            return
        comp.layers[row].muted = item.checkState() == Qt.Unchecked
        # The budget counts only live layers, and the strips grey a muted one.
        self._refresh_strips()
        self._update_budget()
        self._reapply_preview()
        self.changed.emit()

    def _add_layer(self, _checked: bool = False,
                   layer: LayerConfig | None = None) -> None:
        comp = self.current_composition()
        if comp is None or len(comp.layers) >= MAX_LAYERS:
            return
        if layer is None:
            # KEY, not OVER, for every layer after the first: stacking is
            # what a second layer is for, and OVER would blank the one below
            # it. This one default is most of what makes the Animator work
            # without anyone learning what coverage is.
            layer = LayerConfig(
                name=f"Layer {len(comp.layers) + 1}",
                kind=LayerKind.TWINKLE,
                blend=LayerBlend.KEY if comp.layers else LayerBlend.OVER,
            )
        comp.layers.append(layer)
        self._refresh_layers()
        self.layers.setCurrentRow(len(comp.layers) - 1)
        self._reapply_preview()
        self.changed.emit()

    def _add_preset_layer(self) -> None:
        menu = self._preset_menu()
        if menu is not None:
            menu.exec(self.preset_btn.mapToGlobal(self.preset_btn.rect().bottomLeft()))
            # Built fresh on every click, so freed on the way out rather than
            # kept as the panel's child - a starter composition held by each
            # entry - for the rest of the session.
            menu.deleteLater()

    def _preset_menu(self):
        """The same two lists the gallery opens with: every preset as one
        layer, then the custom presets as the layers they are made of.

        Labelled with the preset's own name rather than the layer kind it
        becomes - Fill and Music Sync both become a meter layer, and naming
        them by that is how two of them came to read "Solid Color".

        The presets share presets.layer_for_animation() with the Design tab's
        "Open as Custom" and with the reproduction tests, so a faithless
        conversion breaks all three at once.
        """
        from PySide6.QtWidgets import QMenu

        from ..models import ANIMATION_KIND_LABELS
        from .gallery import preset_kinds, starters

        comp = self.current_composition()
        if comp is None:
            return None
        menu = QMenu(self)
        # Each entry's description goes in its tooltip, as on its gallery card.
        menu.setToolTipsVisible(True)
        free_layers = MAX_LAYERS - len(comp.layers)
        full = f"This animation already has all {MAX_LAYERS} layers"

        _menu_header(menu, "Presets")
        for kind in preset_kinds():
            action = menu.addAction(ANIMATION_KIND_LABELS[kind])
            action.setToolTip("The preset, as a layer you can add to")
            if free_layers < 1:
                action.setEnabled(False)
                action.setToolTip(full)
            action.triggered.connect(lambda _c=False, k=kind: self._insert_preset(k))

        menu.addSeparator()
        _menu_header(menu, "Custom Presets")
        for title, note, template in starters():
            action = menu.addAction(title)
            action.setToolTip(note)
            needs = len(template.layers)
            if needs > free_layers:
                action.setEnabled(False)
                action.setToolTip(f"Needs {needs} free layers - this animation has "
                                  f"{max(0, free_layers)}")
            elif len(comp.drivers) + len(template.drivers) > MAX_DRIVERS:
                action.setEnabled(False)
                action.setToolTip(f"Needs {len(template.drivers)} free driver"
                                  f"{'s' if len(template.drivers) != 1 else ''} - this "
                                  f"animation has {MAX_DRIVERS - len(comp.drivers)}")
            action.triggered.connect(lambda _c=False, t=template: self._insert_template(t))
        return menu

    def _insert_preset(self, kind: AnimationKind) -> None:
        comp = self.current_composition()
        if comp is None:
            return
        layer, drivers = presets.layer_for_animation(AnimationConfig(kind=kind))
        if drivers and len(comp.drivers) < MAX_DRIVERS:
            base = len(comp.drivers)
            comp.drivers.extend(drivers)
            for binding in layer.bindings:
                binding.driver += base
        elif drivers:
            layer.bindings = []
        if comp.layers:
            layer.blend = LayerBlend.KEY
        self.drivers_panel.load(comp)
        self._add_layer(layer=layer)

    def _insert_template(self, template: CompositionConfig) -> None:
        """Add a custom preset's layers on top of this animation's.

        All of them or none: half a template is a different animation. Its
        drivers come too, renumbered to sit after the ones already here, and
        a template built on a timeline brings its loop length when this
        animation has none - without it, a layer timed to start at one
        second would never start at all.
        """
        comp = self.current_composition()
        if comp is None or not template.layers:
            return
        if (len(comp.layers) + len(template.layers) > MAX_LAYERS
                or len(comp.drivers) + len(template.drivers) > MAX_DRIVERS):
            return
        layers = deepcopy(template.layers)
        base = len(comp.drivers)
        for layer in layers:
            for binding in layer.bindings:
                binding.driver += base
        # Same rule as a single preset: stacked on something, the bottom
        # layer keys over it rather than replacing it outright.
        if comp.layers and layers[0].blend == LayerBlend.OVER:
            layers[0].blend = LayerBlend.KEY
        comp.drivers.extend(deepcopy(template.drivers))
        comp.layers.extend(layers)
        if template.loop_ms and not comp.loop_ms:
            comp.loop_ms = template.loop_ms
        # Reloads the drivers, the layer list and the loop field together.
        self._on_composition_selected(self.library.currentRow())
        self.layers.setCurrentRow(len(comp.layers) - 1)
        self.changed.emit()

    def _delete_layer(self) -> None:
        comp = self.current_composition()
        row = self.layers.currentRow()
        if comp is None or not (0 <= row < len(comp.layers)):
            return
        # By row, not list.remove(): two layers from the same preset are
        # equal, and remove() took the lower one out from under the
        # selection, reordering the stack.
        del comp.layers[row]
        self._refresh_layers()
        self._reapply_preview()
        self.changed.emit()

    def _move_layer(self, delta: int) -> None:
        comp = self.current_composition()
        row = self.layers.currentRow()
        if comp is None or not (0 <= row < len(comp.layers)):
            return
        target = row + delta
        if not (0 <= target < len(comp.layers)):
            return
        comp.layers[row], comp.layers[target] = comp.layers[target], comp.layers[row]
        self._refresh_layers()
        self.layers.setCurrentRow(target)
        self._reapply_preview()
        self.changed.emit()

    def _on_layer_edited(self) -> None:
        layer = self._current_layer()
        if layer is None or self._loading:
            return
        self.layer_inspector.save(layer)
        row = self.layers.currentRow()
        if 0 <= row < self.layers.count():
            self._loading = True
            self.layers.item(row).setText(self._layer_text(layer))
            self._loading = False
        self._reapply_preview()
        self._refresh_strips()
        self._update_budget()
        self.changed.emit()

    def _on_drivers_changed(self) -> None:
        comp = self.current_composition()
        bindings = self.layer_inspector.bindings_panel
        bindings.set_drivers(comp.drivers if comp else [])
        # Removing a driver rewrites the bindings that name it - dropping the
        # ones on it, renumbering the ones past it - so the rows are reloaded
        # from the layer. Left as they were, the next edit anywhere in the
        # inspector saved the old rows back over the repair.
        layer = self._current_layer()
        if layer is not None:
            bindings.load(layer)
        self._reapply_preview()
        self._update_budget()
        self.changed.emit()

    def _on_play(self) -> None:
        self._playing = True
        self.start_preview()
        self._sync_transport()

    def _on_pause(self) -> None:
        self._playing = False
        self.stop_preview()
        self._sync_transport()

    def _sync_transport(self) -> None:
        self.play_btn.setEnabled(not self._playing)
        self.pause_btn.setEnabled(self._playing)

    def _on_use(self) -> None:
        comp = self.current_composition()
        if comp is not None:
            self.use_requested.emit(comp.id)

    # -- preview ----------------------------------------------------------

    def _reapply_preview(self) -> None:
        """Re-issue the animation on the strand that is already running.

        This is what an *edit* costs, and it has to stay cheap: a drag fires
        a change per mouse-move, and rebuilding the session each time - a new
        Strand, a new QTimer, a new engine - locks the window up hard enough
        that nothing feels draggable at all. Only a change of geometry needs
        a new strand; everything else is a re-compose.
        """
        if self._session is None:
            self._rebuild_preview()
            return
        comp = self.current_composition()
        if comp is None:
            self._rebuild_preview()
            return
        self._session.config.name = comp.name or "Preview"
        self._session.compositions = {comp.id: comp}
        self._session.config.animation.composition_id = comp.id
        self._session.reapply_animation()

    def _rebuild_preview(self) -> None:
        """Build a fresh scratch strand. Non-destructive: it copies the
        selected strand's geometry but never touches the document's own
        sessions."""
        from ..models import StrandConfig

        comp = self.current_composition()
        if self._session is not None:
            self._session.stop()
            self._session.deleteLater()
            self._session = None
        if comp is None:
            self.canvas.set_sessions([])
            self.canvas.update()
            return

        cfg = StrandConfig(
            name=comp.name or "Preview",
            length=self.length_spin.value(),
            animation=AnimationConfig(kind=AnimationKind.CUSTOM, composition_id=comp.id),
        )
        self._session = StrandSession(cfg, parent=self, compositions={comp.id: comp})
        self._session.ticked.connect(self.canvas.update)
        # The playhead rides the preview's own clock rather than a timer of
        # its own, so what the bar says and what the strip shows cannot drift.
        self._session.ticked.connect(self._sync_playhead)
        self.canvas.set_sessions([self._session])
        if self._playing:
            self._session.start()
        self.span_ruler.set_length(self.length_spin.value())
        self.canvas.update()

    def _sync_playhead(self) -> None:
        if self._session is not None:
            # The composition's clock, which the layer windows are timed
            # against. Every edit re-issues the animation and restarts it;
            # the strand's own clock carries on, so it put the playhead in
            # a different part of the loop from the one playing.
            self.timeline.set_position_ms(
                self._session.strand.compose_music_position_ms())

    def stop_preview(self) -> None:
        if self._session is not None:
            self._session.stop()

    def start_preview(self) -> None:
        if self._session is not None:
            self._session.start()

    def _update_budget(self) -> None:
        comp = self.current_composition()
        if comp is None:
            self.budget_label.setText("")
            return
        live = [layer for layer in comp.layers if not layer.muted]
        # Shown rather than only enforced at export: a ceiling you discover
        # when you try to ship is a ceiling you hit twice.
        self.budget_label.setText(
            f"{len(live)} of {MAX_LAYERS} layers   ·   "
            f"{len(comp.drivers)} of {MAX_DRIVERS} drivers"
        )
        over = len(live) > MAX_LAYERS or len(comp.drivers) > MAX_DRIVERS
        near = len(live) >= MAX_LAYERS - 1 or len(comp.drivers) >= MAX_DRIVERS - 1
        color = theme.DANGER if over else (theme.BRAND_PINK if near else theme.TEXT_DIM)
        self.budget_label.setStyleSheet(f"color: {color};")
