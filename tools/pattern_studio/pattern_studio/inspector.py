"""Strand settings / animation / splice-mask editor for the currently selected strand.

Each panel owns a `changed` signal; StrandSettingsPanel's
changes require recreating the engine Strand (length/port/refresh_ms affect
buffer sizing), the others only need re-issuing the animation call, so
InspectorPanel re-exposes them as two signals matching StrandSession's
rebuild() vs reapply_animation() split.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# ModesPanel imports AnimationPanel/SpliceMaskPanel from this module, so it must
# be imported lazily inside InspectorPanel to avoid a circular import.

from . import fill_sources
from .envelope import BAND_HELP, BAND_LABELS, BAND_NAMES
from .models import (
    ANIMATION_KIND_LABELS,
    GAUGE_BLEND_LABELS,
    GAUGE_STYLE_LABELS,
    OVERLAY_ANIMATION_KIND_LABELS,
    AnimationConfig,
    AnimationKind,
    GaugeBlendKind,
    GaugeStopConfig,
    GaugeStyleKind,
    OverlayAnimationConfig,
    OverlayAnimationKind,
    SpliceMaskConfig,
    SpliceModeKind,
    SpliceRegionConfig,
    StrandConfig,
)
from . import theme
from .widgets import ColorButton, enum_data, format_palette, parse_palette


def _duration_spin() -> QSpinBox:
    """Millisecond duration field, as used by the flash on/off times."""
    spin = QSpinBox()
    spin.setRange(10, 10000)
    spin.setSingleStep(10)
    spin.setSuffix(" ms")
    return spin


def _reading_spin() -> QSpinBox:
    """A Fill meter's Empty At / Full At field.

    Deliberately wide open and signed: these are readings in the source's own
    units, which run from a battery percentage to 36000 centidegrees, and a
    negative end is how a meter that reads a reversed motor is written. Whole
    numbers only - fractional bounds are rare enough on a robot that they are
    not worth a second spin-box style, and levelSource() takes doubles for the
    hand-written cases that do need them.
    """
    spin = QSpinBox()
    spin.setRange(-1000000, 1000000)
    return spin


_VISIBLE_FIELDS: dict[AnimationKind, set[str]] = {
    AnimationKind.OFF: set(),
    AnimationKind.SOLID: {"color"},
    AnimationKind.PULSE: {"color", "bg_color", "run_length", "speed", "invert", "bounce"},
    AnimationKind.FLASH: {"color", "bg_color", "on_ms", "off_ms"},
    AnimationKind.FLOW: {"color", "color2", "speed", "invert", "seamless"},
    AnimationKind.RAINBOW: {"speed"},
    AnimationKind.TWINKLE: {"palette", "bg_color", "density_pct", "fade_step"},
    AnimationKind.BITSCROLL: {
        "color", "bg_color", "segment_width", "spacing", "speed", "invert", "bounce", "repeating",
    },
    AnimationKind.FILL: {
        "fill_hint", "source", "source_port", "source_empty", "source_full", "source_wrap",
        "smoothing", "preview_sweep", "preview_level",
        "gradient", "color", "color2", "bg_color", "invert",
    },
    AnimationKind.MUSIC: {
        "music_hint", "band", "gradient", "color", "color2", "bg_color", "invert", "sensitivity",
    },
}


class StrandSettingsPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._suspend = False
        # A plain QWidget ignores a stylesheet background unless it opts in,
        # Qt only styles the backgrounds of widgets that ask for it, so the
        # strip would otherwise sit borderless on the window color.
        self.setObjectName("strandStrip")
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        # Explicit widths, unlike the rest of the app. A styled QSpinBox sizes
        # itself from the stylesheet's padding and declared button width, not
        # from the digits it holds, so an 8-max port field ends up as wide as
        # a 5-digit one; and six of them side by side overflow the center
        # column. Each width below fits its widest value ("Direct", "500 ms",
        # "100 %") with room to spare.
        self.name_edit = QLineEdit()
        self.name_edit.setMaximumWidth(120)
        self.adi_port_spin = QSpinBox()
        self.adi_port_spin.setRange(1, 8)
        self.adi_port_spin.setFixedWidth(62)
        self.smart_port_spin = QSpinBox()
        self.smart_port_spin.setRange(0, 21)
        self.smart_port_spin.setSpecialValueText("Direct")
        self.smart_port_spin.setFixedWidth(86)
        self.length_spin = QSpinBox()
        self.length_spin.setRange(1, 64)
        self.length_spin.setFixedWidth(66)
        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(5, 500)
        self.refresh_spin.setSuffix(" ms")
        self.refresh_spin.setFixedWidth(90)
        self.brightness_spin = QSpinBox()
        self.brightness_spin.setRange(0, 100)
        self.brightness_spin.setSuffix(" %")
        self.brightness_spin.setFixedWidth(86)

        def add_field(label_text: str, widget) -> None:
            layout.addWidget(QLabel(label_text))
            layout.addWidget(widget)

        add_field("Name", self.name_edit)
        add_field("ADI", self.adi_port_spin)
        add_field("Smart", self.smart_port_spin)
        add_field("Len", self.length_spin)
        add_field("Refresh", self.refresh_spin)
        add_field("Bright", self.brightness_spin)
        layout.addStretch(1)

        self.name_edit.textChanged.connect(self._emit_changed)
        self.adi_port_spin.valueChanged.connect(self._emit_changed)
        self.smart_port_spin.valueChanged.connect(self._emit_changed)
        self.length_spin.valueChanged.connect(self._emit_changed)
        self.refresh_spin.valueChanged.connect(self._emit_changed)
        self.brightness_spin.valueChanged.connect(self._emit_changed)

    def _emit_changed(self, *_args) -> None:
        if not self._suspend:
            self.changed.emit()

    def load(self, cfg: StrandConfig) -> None:
        self._suspend = True
        self.name_edit.setText(cfg.name)
        self.name_edit.setCursorPosition(0)  # setText() leaves the cursor (and view) at the end
        self.adi_port_spin.setValue(cfg.adi_port)
        self.smart_port_spin.setValue(cfg.smart_port)
        self.length_spin.setValue(cfg.length)
        self.refresh_spin.setValue(cfg.refresh_ms)
        self.brightness_spin.setValue(cfg.brightness)
        self._suspend = False

    def save(self, cfg: StrandConfig) -> None:
        cfg.name = self.name_edit.text().strip() or "Strand"
        cfg.adi_port = self.adi_port_spin.value()
        cfg.smart_port = self.smart_port_spin.value()
        cfg.length = self.length_spin.value()
        cfg.refresh_ms = self.refresh_spin.value()
        cfg.brightness = self.brightness_spin.value()


class AnimationPanel(QGroupBox):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("Animation", parent)
        self._suspend = False
        outer = QVBoxLayout(self)

        self.kind_combo = QComboBox()
        for kind in AnimationKind:
            self.kind_combo.addItem(ANIMATION_KIND_LABELS[kind], kind)
        outer.addWidget(self.kind_combo)

        form = QFormLayout()
        outer.addLayout(form)

        self.color_btn = ColorButton(0xFF0000)
        self.color2_btn = ColorButton(0x0000FF)
        self.bg_btn = ColorButton(0x000000)
        self.run_length_spin = QSpinBox()
        self.run_length_spin.setRange(1, 64)
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(1, 64)
        self.on_ms_spin = _duration_spin()
        self.off_ms_spin = _duration_spin()
        self.invert_check = QCheckBox()
        self.bounce_check = QCheckBox()
        self.seamless_check = QCheckBox()
        self.density_spin = QSpinBox()
        self.density_spin.setRange(0, 100)
        self.density_spin.setSuffix(" %")
        self.fade_spin = QSpinBox()
        self.fade_spin.setRange(1, 255)
        self.palette_edit = QLineEdit()
        self.palette_edit.setToolTip("Comma-separated hex colors, e.g. FF0000, 00FF00, 0000FF")
        self.segment_width_spin = QSpinBox()
        self.segment_width_spin.setRange(1, 64)
        self.spacing_spin = QSpinBox()
        self.spacing_spin.setRange(0, 64)
        self.repeating_check = QCheckBox()
        self.gradient_combo = QComboBox()
        self.gradient_combo.addItem("Solid Color", False)
        self.gradient_combo.addItem("2-Color Gradient", True)
        # Fill. The source leads the panel,
        # and the fields under it re-range and re-label themselves to whatever
        # it reads - see _sync_source_fields().
        self.source_combo = QComboBox()
        for source_id in fill_sources.ORDER:
            self.source_combo.addItem(fill_sources.SOURCES[source_id].label, source_id)
        self.source_port_spin = QSpinBox()
        self.source_port_spin.setRange(1, 21)
        self.source_empty_spin = _reading_spin()
        self.source_empty_spin.setToolTip("The reading that shows an empty strip.")
        self.source_full_spin = _reading_spin()
        self.source_full_spin.setToolTip(
            "The reading that fills it. Put it below Empty At to reverse the "
            "meter, so the bar drains as the number climbs."
        )
        self.source_wrap_check = QCheckBox()
        self.source_wrap_check.setToolTip(
            "Past Full At, start over from empty instead of pinning at full - "
            "what a continuously turning motor or a heading wants."
        )
        self.smoothing_spin = QSpinBox()
        self.smoothing_spin.setRange(0, 99)
        self.smoothing_spin.setSuffix(" %")
        self.smoothing_spin.setToolTip(
            "How much of the previous frame's fill to keep each tick. 0 follows "
            "the reading exactly; higher glides toward it, which is worth "
            "having on anything noisy."
        )
        # Preview stand-ins. The desktop has no motor to read, so these decide
        # what the meter follows *here* - neither is exported.
        self.preview_sweep_check = QCheckBox()
        self.preview_sweep_check.setToolTip(
            "Preview only: run a made-up reading through the range so the bar "
            "moves. Never exported."
        )
        self.preview_level_spin = QSpinBox()
        self.preview_level_spin.setRange(0, 100)
        self.preview_level_spin.setSuffix(" %")
        self.preview_level_spin.setToolTip(
            "Preview only: hold the reading this far through the range. Never "
            "exported."
        )
        self.fill_hint = QLabel()
        self.fill_hint.setWordWrap(True)
        self.fill_hint.setProperty("role", "hint")
        # Per-strand rather than per-song: two strips on the same robot look far
        # better following different parts of the track than the same one.
        self.band_combo = QComboBox()
        for band in BAND_NAMES:
            self.band_combo.addItem(BAND_LABELS[band], band)
        self.band_combo.setToolTip("\n".join(BAND_HELP[b] for b in BAND_NAMES))
        self.band_combo.setMaximumWidth(150)
        self.sensitivity_spin = QSpinBox()
        # Above 100% the meter reaches further up the strip for the same music
        # and clips at the top; below it, only the loudest passages fill.
        self.sensitivity_spin.setRange(10, 255)
        self.sensitivity_spin.setSuffix(" %")
        self.sensitivity_spin.setToolTip(
            "Gain on the baked envelope. Adjustable on the robot too - "
            "exported as musicSync()'s sensitivity argument."
        )
        # The song itself is a document-level thing edited in the Song bar, so
        # this panel says where to find it rather than duplicating the controls.
        self.music_hint = QLabel(
            "Fills in time with the song in the Song bar, under the preview."
        )
        self.music_hint.setWordWrap(True)
        self.music_hint.setProperty("role", "hint")
        self.invert_check.setToolTip("Reverse the direction")
        self.seamless_check.setToolTip(
            "Loop the gradient back to Color instead of cutting straight from "
            "Color 2 to Color at the wrap."
        )

        self._rows: dict[str, tuple] = {}

        def add_row(name: str, label: str, widget) -> None:
            form.addRow(label, widget)
            self._rows[name] = (form.labelForField(widget), widget)

        add_row("color", "Color", self.color_btn)
        add_row("color2", "Color 2", self.color2_btn)
        add_row("bg_color", "Background", self.bg_btn)
        add_row("run_length", "Run Length", self.run_length_spin)
        add_row("speed", "Speed", self.speed_spin)
        add_row("on_ms", "On Time", self.on_ms_spin)
        add_row("off_ms", "Off Time", self.off_ms_spin)
        add_row("invert", "Invert", self.invert_check)
        add_row("bounce", "Bounce", self.bounce_check)
        add_row("seamless", "Seamless", self.seamless_check)
        add_row("density_pct", "Density", self.density_spin)
        add_row("fade_step", "Fade Step", self.fade_spin)
        add_row("palette", "Palette", self.palette_edit)
        add_row("segment_width", "Seg. Width", self.segment_width_spin)
        add_row("spacing", "Spacing", self.spacing_spin)
        add_row("repeating", "Repeating", self.repeating_check)
        add_row("band", "Follows", self.band_combo)
        add_row("gradient", "Fill Style", self.gradient_combo)
        add_row("sensitivity", "Sensitivity", self.sensitivity_spin)
        add_row("source", "Follows", self.source_combo)
        add_row("source_port", "Port", self.source_port_spin)
        add_row("source_empty", "Empty At", self.source_empty_spin)
        add_row("source_full", "Full At", self.source_full_spin)
        add_row("source_wrap", "Wrap", self.source_wrap_check)
        add_row("smoothing", "Smoothing", self.smoothing_spin)
        add_row("preview_sweep", "Sweep Preview", self.preview_sweep_check)
        add_row("preview_level", "Preview At", self.preview_level_spin)
        form.addRow(self.fill_hint)
        self._rows["fill_hint"] = (None, self.fill_hint)
        form.addRow(self.music_hint)
        self._rows["music_hint"] = (None, self.music_hint)

        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        for widget, signal_name in (
            (self.color_btn, "color_changed"),
            (self.color2_btn, "color_changed"),
            (self.bg_btn, "color_changed"),
            (self.run_length_spin, "valueChanged"),
            (self.speed_spin, "valueChanged"),
            (self.on_ms_spin, "valueChanged"),
            (self.off_ms_spin, "valueChanged"),
            (self.invert_check, "toggled"),
            (self.bounce_check, "toggled"),
            (self.seamless_check, "toggled"),
            (self.density_spin, "valueChanged"),
            (self.fade_spin, "valueChanged"),
            (self.palette_edit, "textChanged"),
            (self.segment_width_spin, "valueChanged"),
            (self.spacing_spin, "valueChanged"),
            (self.repeating_check, "toggled"),
            (self.sensitivity_spin, "valueChanged"),
            (self.band_combo, "currentIndexChanged"),
            (self.source_empty_spin, "valueChanged"),
            (self.source_full_spin, "valueChanged"),
            (self.smoothing_spin, "valueChanged"),
            (self.preview_level_spin, "valueChanged"),
        ):
            getattr(widget, signal_name).connect(self._emit_changed)
        # Not in the loop above: each of these also decides what else is worth
        # showing, so they refresh visibility before emitting.
        self.gradient_combo.currentIndexChanged.connect(self._on_gradient_changed)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.source_port_spin.valueChanged.connect(self._on_port_changed)
        self.source_wrap_check.toggled.connect(self._on_visible_field_changed)
        self.preview_sweep_check.toggled.connect(self._on_visible_field_changed)

        self._sync_source_fields()
        self._update_visibility()

    def _emit_changed(self, *_args) -> None:
        if not self._suspend:
            self.changed.emit()

    def _on_kind_changed(self, *_args) -> None:
        self._update_visibility()
        self._emit_changed()

    def _on_gradient_changed(self, *_args) -> None:
        self._update_visibility()
        self._emit_changed()

    def _on_visible_field_changed(self, *_args) -> None:
        """For fields that decide whether another field is worth showing."""
        self._update_visibility()
        self._emit_changed()

    def _on_port_changed(self, *_args) -> None:
        self._sync_source_fields()  # the hint names the port
        self._emit_changed()

    def _on_source_changed(self, *_args) -> None:
        self._sync_source_fields()
        # Only a deliberate pick re-ranges the fields. During load() the values
        # coming out of the file are the answer, not the source's defaults.
        if not self._suspend:
            self._prefill_source_defaults()
        self._update_visibility()
        self._emit_changed()

    def _sync_source_fields(self) -> None:
        """Re-range and re-label the source fields for whatever is selected.

        The numbers a Fill meter takes only mean anything in the source's own
        units, so the fields carry them: picking Motor Temperature makes the
        bounds read "70 °C", and picking a Rotation Sensor makes them
        centidegrees. Values are left alone - see _prefill_source_defaults()
        for the part that overwrites them.
        """
        source = fill_sources.get(self.source_combo.currentData())
        low, high = fill_sources.port_range(source.id)
        if high:
            self.source_port_spin.setRange(low, high)
        self.source_empty_spin.setSuffix(source.unit)
        self.source_full_spin.setSuffix(source.unit)
        self.fill_hint.setText(self._fill_hint_text(source))

    def _prefill_source_defaults(self) -> None:
        """Fill the range in with something that already works for this source.

        A meter is far more likely to want 20-70 °C for a motor than whatever
        the last source's numbers happened to be, so switching sources starts
        from the useful answer and leaves it there to be adjusted.
        """
        source = fill_sources.get(self.source_combo.currentData())
        self.source_empty_spin.setValue(source.empty_default)
        self.source_full_spin.setValue(source.full_default)
        self.source_wrap_check.setChecked(source.wrap_default)

    def _fill_hint_text(self, source: fill_sources.FillSource) -> str:
        """What this source does, plus what the export will do about it."""
        if source.id == fill_sources.MANUAL:
            return f"{source.hint} Exported as levelFill() alone, with no reader attached."
        if source.id == fill_sources.CUSTOM:
            return (
                f"{source.hint} Export leaves a LevelFn in the strand's source:: "
                f"namespace - assign it a lambda returning a double before the mode runs."
            )
        where = ""
        if source.port_kind == fill_sources.PORT_SMART:
            where = f" Reads port {self.source_port_spin.value()}."
        elif source.port_kind == fill_sources.PORT_ADI:
            where = f" Reads ADI port {self.source_port_spin.value()}."
        return f"{source.hint}{where} Exported ready to run."

    def _update_visibility(self) -> None:
        kind = self.kind_combo.currentData()
        visible = set(_VISIBLE_FIELDS.get(kind, set()))
        # A meter's second color only means anything when it has a gradient to
        # be the far end of.
        if kind in (AnimationKind.MUSIC, AnimationKind.FILL) and not self.gradient_combo.currentData():
            visible.discard("color2")
        if kind == AnimationKind.FILL:
            source = self.source_combo.currentData()
            if not fill_sources.get(source).port_kind:
                visible.discard("source_port")
            if not fill_sources.polls_a_device(source):
                # Manual means the robot's code calls setLevel(0-255) itself.
                # Nothing here maps a reading, so none of the mapping applies.
                visible -= {"source_empty", "source_full", "source_wrap", "smoothing"}
            if self.preview_sweep_check.isChecked():
                visible.discard("preview_level")
        for name, (label, widget) in self._rows.items():
            show = name in visible
            widget.setVisible(show)
            if label is not None:
                label.setVisible(show)

    def load(self, a: AnimationConfig) -> None:
        self._suspend = True
        idx = self.kind_combo.findData(a.kind)
        if idx >= 0:
            self.kind_combo.setCurrentIndex(idx)
        self.color_btn.set_color(a.color)
        self.color2_btn.set_color(a.color2)
        self.bg_btn.set_color(a.bg_color)
        self.run_length_spin.setValue(a.run_length)
        self.speed_spin.setValue(a.speed)
        self.on_ms_spin.setValue(a.on_ms)
        self.off_ms_spin.setValue(a.off_ms)
        self.invert_check.setChecked(a.invert)
        self.bounce_check.setChecked(a.bounce)
        self.seamless_check.setChecked(a.seamless)
        self.density_spin.setValue(a.density_pct)
        self.fade_spin.setValue(a.fade_step)
        self.palette_edit.setText(format_palette(a.palette))
        self.segment_width_spin.setValue(a.segment_width)
        self.spacing_spin.setValue(a.spacing)
        self.repeating_check.setChecked(a.repeating)
        # Before the values below it: picking the source is what re-ranges the
        # port field and re-labels the bounds they land in.
        source_idx = self.source_combo.findData(a.source)
        if source_idx >= 0:
            self.source_combo.setCurrentIndex(source_idx)
        self._sync_source_fields()
        self.source_port_spin.setValue(a.source_port)
        self.source_empty_spin.setValue(a.source_empty)
        self.source_full_spin.setValue(a.source_full)
        self.source_wrap_check.setChecked(a.source_wrap)
        self.smoothing_spin.setValue(a.smoothing)
        self.preview_sweep_check.setChecked(a.preview_sweep)
        self.preview_level_spin.setValue(a.preview_level)
        gradient_idx = self.gradient_combo.findData(a.gradient)
        if gradient_idx >= 0:
            self.gradient_combo.setCurrentIndex(gradient_idx)
        self.sensitivity_spin.setValue(a.sensitivity)
        band_idx = self.band_combo.findData(a.band)
        if band_idx >= 0:
            self.band_combo.setCurrentIndex(band_idx)
        self._suspend = False
        self._sync_source_fields()  # the hint quotes the port that was just set
        self._update_visibility()

    def save(self, a: AnimationConfig) -> None:
        a.kind = enum_data(self.kind_combo, AnimationKind)
        a.color = self.color_btn.color()
        a.color2 = self.color2_btn.color()
        a.bg_color = self.bg_btn.color()
        a.run_length = self.run_length_spin.value()
        a.speed = self.speed_spin.value()
        a.on_ms = self.on_ms_spin.value()
        a.off_ms = self.off_ms_spin.value()
        a.invert = self.invert_check.isChecked()
        a.bounce = self.bounce_check.isChecked()
        a.seamless = self.seamless_check.isChecked()
        a.density_pct = self.density_spin.value()
        a.fade_step = self.fade_spin.value()
        parsed = parse_palette(self.palette_edit.text())
        if parsed:
            a.palette = parsed
        a.segment_width = self.segment_width_spin.value()
        a.spacing = self.spacing_spin.value()
        a.repeating = self.repeating_check.isChecked()
        a.gradient = bool(self.gradient_combo.currentData())
        a.sensitivity = self.sensitivity_spin.value()
        a.band = self.band_combo.currentData()
        a.source = self.source_combo.currentData()
        a.source_port = self.source_port_spin.value()
        a.source_empty = self.source_empty_spin.value()
        a.source_full = self.source_full_spin.value()
        a.source_wrap = self.source_wrap_check.isChecked()
        a.smoothing = self.smoothing_spin.value()
        a.preview_sweep = self.preview_sweep_check.isChecked()
        a.preview_level = self.preview_level_spin.value()


_OVERLAY_VISIBLE_FIELDS: dict[OverlayAnimationKind, set[str]] = {
    OverlayAnimationKind.OFF: set(),
    OverlayAnimationKind.SOLID: {"color"},
    OverlayAnimationKind.PULSE: {"color", "bg_color", "run_length", "speed"},
    OverlayAnimationKind.FLASH: {"color", "bg_color", "on_ms", "off_ms"},
    OverlayAnimationKind.FLOW: {"color", "color2", "speed", "seamless"},
    OverlayAnimationKind.RAINBOW: {"speed"},
    OverlayAnimationKind.TWINKLE: {"bg_color", "density_pct", "fade_step", "palette"},
    OverlayAnimationKind.BITSCROLL: {
        "color", "bg_color", "speed", "invert", "segment_width", "spacing", "repeating",
    },
    OverlayAnimationKind.GAUGE: {
        "fill_hint", "source", "source_port", "source_empty", "source_full", "source_wrap",
        "smoothing", "style", "blend", "stops", "invert", "bg_color",
        "preview_sweep", "preview_level", "color", "color2",
    },
}


class GaugeStopsEditor(QWidget):
    """The color scale of one Gauge region: a row per stop, all visible at once.

    Deliberately not a list-plus-editor like the region and mode lists. A scale
    is read as a whole - "green until 45, then it starts warning" - so hiding
    five of six stops behind a selection would hide the shape of it. Six rows
    fit, which is more stops than a strip can show distinctly.
    """

    changed = Signal()
    reset_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._suspend = False
        self._unit = ""
        self._rows: list[tuple[QWidget, QSpinBox, ColorButton]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        self._rows_box = QVBoxLayout()
        self._rows_box.setContentsMargins(0, 0, 0, 0)
        self._rows_box.setSpacing(2)
        outer.addLayout(self._rows_box)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton(theme.icon("plus"), " Stop")
        self.add_btn.setToolTip("Add a color stop to the scale.")
        self.reset_btn = QPushButton(" Source Default")
        self.reset_btn.setToolTip(
            "Replace the scale with the one this source ships with - for a motor's "
            "temperature, the points the motor itself changes behaviour at."
        )
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        self.add_btn.clicked.connect(self._add_stop)
        self.reset_btn.clicked.connect(self.reset_requested)

    def set_unit(self, unit: str) -> None:
        """Re-label every row in the source's own units, so a scale reads as
        "55 °C" rather than as a bare number."""
        self._unit = unit
        for _row, spin, _btn in self._rows:
            spin.setSuffix(unit)

    def set_stops(self, stops: list[GaugeStopConfig], unit: str = "") -> None:
        self._suspend = True
        self._unit = unit
        try:
            self._clear()
            # Sorted on the way in rather than as they are typed: the runtime
            # sorts anyway, and reordering rows under the cursor mid-edit is
            # worse than a briefly out-of-order scale.
            for stop in sorted(stops, key=lambda s: s.at):
                self._append_row(stop.at, stop.color)
        finally:
            self._suspend = False

    def stops(self) -> list[GaugeStopConfig]:
        return [GaugeStopConfig(at=float(spin.value()), color=btn.color())
                for _row, spin, btn in self._rows]

    def _clear(self) -> None:
        for row, _spin, _btn in self._rows:
            self._rows_box.removeWidget(row)
            row.deleteLater()
        self._rows = []

    def _append_row(self, at: float, color: int) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        spin = _reading_spin()
        spin.setSuffix(self._unit)
        spin.setValue(int(at))
        color_btn = ColorButton(color)
        remove_btn = QPushButton(theme.icon("minus"), "")
        remove_btn.setProperty("role", "danger")
        remove_btn.setToolTip("Remove this stop.")
        remove_btn.setMaximumWidth(36)

        layout.addWidget(spin, 1)
        layout.addWidget(color_btn, 1)
        layout.addWidget(remove_btn)
        self._rows_box.addWidget(row)
        self._rows.append((row, spin, color_btn))

        spin.valueChanged.connect(self._emit_changed)
        color_btn.color_changed.connect(self._emit_changed)
        remove_btn.clicked.connect(lambda _checked=False, w=row: self._remove_row(w))

    def _add_stop(self) -> None:
        # A new stop lands past the last one rather than on top of it, so it is
        # somewhere visible on the scale before it is dragged into place.
        last = self._rows[-1][1].value() if self._rows else 0
        self._append_row(last + 5, 0xFFFFFF)
        self._emit_changed()

    def _remove_row(self, row: QWidget) -> None:
        for i, (candidate, _spin, _btn) in enumerate(self._rows):
            if candidate is row:
                self._rows_box.removeWidget(candidate)
                candidate.deleteLater()
                del self._rows[i]
                break
        self._emit_changed()

    def _emit_changed(self, *_args) -> None:
        if not self._suspend:
            self.changed.emit()


class OverlayAnimationPanel(QGroupBox):
    """Editor for an OverlayAnimationConfig, reused for two different
    things that happen to share the same overlay*() vocabulary: Split mode's
    single shared overlay buffer (SpliceMaskConfig.overlay), and each Custom
    region's own independent animation (SpliceRegionConfig.animation). The
    `title` param exists so the group box can say which one it is.
    """

    changed = Signal()

    def __init__(self, title: str = "Overlay Animation", parent=None, allow_gauge: bool = False):
        super().__init__(title, parent)
        self._suspend = False
        outer = QVBoxLayout(self)

        self.kind_combo = QComboBox()
        for kind in OverlayAnimationKind:
            # Split mode's overlay is one shared buffer for every masked bin, so
            # a gauge there would be a single meter smeared across all of them.
            # It is only offered where it means something: a Custom region.
            if kind == OverlayAnimationKind.GAUGE and not allow_gauge:
                continue
            self.kind_combo.addItem(OVERLAY_ANIMATION_KIND_LABELS[kind], kind)
        outer.addWidget(self.kind_combo)

        form = QFormLayout()
        outer.addLayout(form)

        self.color_btn = ColorButton(0xFFFFFF)
        self.color2_btn = ColorButton(0x0000FF)
        self.bg_btn = ColorButton(0x000000)
        self.run_length_spin = QSpinBox()
        self.run_length_spin.setRange(1, 64)
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(1, 64)
        self.on_ms_spin = _duration_spin()
        self.off_ms_spin = _duration_spin()
        self.seamless_check = QCheckBox()
        self.seamless_check.setToolTip(
            "Loop the gradient back to Color instead of cutting straight from "
            "Color 2 to Color at the wrap."
        )
        # Twinkle / Bitscroll. Same fields as their whole-strand counterparts,
        # minus Bounce: an overlay and a region are each a single scrolling
        # buffer, and bouncing needs a wider master pattern to slide over.
        self.density_spin = QSpinBox()
        self.density_spin.setRange(0, 100)
        self.density_spin.setSuffix(" %")
        self.fade_spin = QSpinBox()
        self.fade_spin.setRange(1, 255)
        self.palette_edit = QLineEdit()
        self.palette_edit.setToolTip("Comma-separated hex colors, e.g. FF0000, 00FF00, 0000FF")
        self.segment_width_spin = QSpinBox()
        self.segment_width_spin.setRange(1, 64)
        self.spacing_spin = QSpinBox()
        self.spacing_spin.setRange(0, 64)
        self.repeating_check = QCheckBox()
        # Gauge. Same source fields as the Fill animation, under the same names
        # and with the same meanings - a gauge region is a Fill meter scoped to
        # a few pixels, so anything learned on one applies to the other.
        self.source_combo = QComboBox()
        for source_id in fill_sources.ORDER:
            self.source_combo.addItem(fill_sources.SOURCES[source_id].label, source_id)
        self.source_port_spin = QSpinBox()
        self.source_port_spin.setRange(1, 21)
        self.source_empty_spin = _reading_spin()
        self.source_empty_spin.setToolTip("The reading at the bottom of this gauge's scale.")
        self.source_full_spin = _reading_spin()
        self.source_full_spin.setToolTip(
            "The reading at the top of it. Put it below Empty At to reverse the "
            "gauge, so it climbs as the number falls."
        )
        self.source_wrap_check = QCheckBox()
        self.source_wrap_check.setToolTip(
            "Past Full At, start over from the bottom instead of pinning at the top."
        )
        self.smoothing_spin = QSpinBox()
        self.smoothing_spin.setRange(0, 99)
        self.smoothing_spin.setSuffix(" %")
        self.smoothing_spin.setToolTip(
            "How much of the previous frame's level to keep each tick. The V5 "
            "reports motor temperature in coarse steps; smoothing turns those "
            "steps into a creep."
        )
        self.style_combo = QComboBox()
        for style in GaugeStyleKind:
            self.style_combo.addItem(GAUGE_STYLE_LABELS[style], style)
        self.style_combo.setToolTip(
            "Whole Segment colors every pixel off the scale at once. Fill Bar "
            "fills the segment proportionally instead, like a small meter."
        )
        self.blend_combo = QComboBox()
        for blend in GaugeBlendKind:
            self.blend_combo.addItem(GAUGE_BLEND_LABELS[blend], blend)
        self.blend_combo.setToolTip(
            "Blend slides between neighbouring stops. Hold keeps each stop's "
            "color until the next is actually reached - the honest choice when "
            "the stops are thresholds something crosses."
        )
        self.stops_editor = GaugeStopsEditor()
        self.preview_sweep_check = QCheckBox()
        self.preview_sweep_check.setToolTip(
            "Preview only: run a made-up reading through the range so the gauge "
            "moves. Never exported."
        )
        self.preview_level_spin = QSpinBox()
        self.preview_level_spin.setRange(0, 100)
        self.preview_level_spin.setSuffix(" %")
        self.preview_level_spin.setToolTip(
            "Preview only: hold the reading this far through the range. Never exported."
        )
        self.invert_check = QCheckBox()
        self.invert_check.setToolTip(
            "Reverse the direction: a bitscroll scrolls the other way, a gauge "
            "bar fills from the far end of the segment."
        )
        self.fill_hint = QLabel()
        self.fill_hint.setWordWrap(True)
        self.fill_hint.setProperty("role", "hint")

        self._rows: dict[str, tuple] = {}

        def add_row(name: str, label: str, widget) -> None:
            form.addRow(label, widget)
            self._rows[name] = (form.labelForField(widget), widget)

        add_row("color", "Color", self.color_btn)
        add_row("color2", "Color 2", self.color2_btn)
        add_row("bg_color", "Background", self.bg_btn)
        add_row("run_length", "Run Length", self.run_length_spin)
        add_row("speed", "Speed", self.speed_spin)
        add_row("on_ms", "On Time", self.on_ms_spin)
        add_row("off_ms", "Off Time", self.off_ms_spin)
        add_row("seamless", "Seamless", self.seamless_check)
        add_row("density_pct", "Density", self.density_spin)
        add_row("fade_step", "Fade Step", self.fade_spin)
        add_row("palette", "Palette", self.palette_edit)
        add_row("segment_width", "Seg. Width", self.segment_width_spin)
        add_row("spacing", "Spacing", self.spacing_spin)
        add_row("repeating", "Repeating", self.repeating_check)
        add_row("source", "Follows", self.source_combo)
        add_row("source_port", "Port", self.source_port_spin)
        add_row("source_empty", "Empty At", self.source_empty_spin)
        add_row("source_full", "Full At", self.source_full_spin)
        add_row("source_wrap", "Wrap", self.source_wrap_check)
        add_row("smoothing", "Smoothing", self.smoothing_spin)
        add_row("style", "Shows", self.style_combo)
        add_row("blend", "Between Stops", self.blend_combo)
        add_row("invert", "Invert", self.invert_check)
        add_row("stops", "Scale", self.stops_editor)
        add_row("preview_sweep", "Sweep Preview", self.preview_sweep_check)
        add_row("preview_level", "Preview At", self.preview_level_spin)
        form.addRow(self.fill_hint)
        self._rows["fill_hint"] = (None, self.fill_hint)

        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        for widget, signal_name in (
            (self.color_btn, "color_changed"),
            (self.color2_btn, "color_changed"),
            (self.bg_btn, "color_changed"),
            (self.run_length_spin, "valueChanged"),
            (self.speed_spin, "valueChanged"),
            (self.on_ms_spin, "valueChanged"),
            (self.off_ms_spin, "valueChanged"),
            (self.density_spin, "valueChanged"),
            (self.fade_spin, "valueChanged"),
            (self.palette_edit, "textChanged"),
            (self.segment_width_spin, "valueChanged"),
            (self.spacing_spin, "valueChanged"),
            (self.repeating_check, "toggled"),
            (self.source_empty_spin, "valueChanged"),
            (self.source_full_spin, "valueChanged"),
            (self.smoothing_spin, "valueChanged"),
            (self.blend_combo, "currentIndexChanged"),
            (self.preview_level_spin, "valueChanged"),
        ):
            getattr(widget, signal_name).connect(self._emit_changed)
        # Not in the loop above: each of these also decides what else is worth
        # showing, so they refresh visibility before emitting.
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.source_port_spin.valueChanged.connect(self._on_port_changed)
        self.source_wrap_check.toggled.connect(self._on_visible_field_changed)
        self.preview_sweep_check.toggled.connect(self._on_visible_field_changed)
        self.style_combo.currentIndexChanged.connect(self._on_visible_field_changed)
        self.invert_check.toggled.connect(self._emit_changed)
        self.seamless_check.toggled.connect(self._emit_changed)
        self.stops_editor.changed.connect(self._on_visible_field_changed)
        self.stops_editor.reset_requested.connect(self._reset_stops_to_source_default)

        self._sync_source_fields()
        self._update_visibility()

    def _emit_changed(self, *_args) -> None:
        if not self._suspend:
            self.changed.emit()

    def _on_kind_changed(self, *_args) -> None:
        self._update_visibility()
        self._emit_changed()

    def _on_visible_field_changed(self, *_args) -> None:
        """For fields that decide whether another field is worth showing."""
        self._update_visibility()
        self._emit_changed()

    def _on_port_changed(self, *_args) -> None:
        self._sync_source_fields()  # the hint names the port
        self._emit_changed()

    def _on_source_changed(self, *_args) -> None:
        self._sync_source_fields()
        # Only a deliberate pick re-ranges the fields. During load() the values
        # coming out of the file are the answer, not the source's defaults.
        if not self._suspend:
            self._prefill_source_defaults()
        self._update_visibility()
        self._emit_changed()

    def _sync_source_fields(self) -> None:
        """Re-range and re-label the source fields for whatever is selected.

        The numbers a Fill meter takes only mean anything in the source's own
        units, so the fields carry them: picking Motor Temperature makes the
        bounds read "70 °C", and picking a Rotation Sensor makes them
        centidegrees. Values are left alone - see _prefill_source_defaults()
        for the part that overwrites them.
        """
        source = fill_sources.get(self.source_combo.currentData())
        low, high = fill_sources.port_range(source.id)
        if high:
            self.source_port_spin.setRange(low, high)
        self.source_empty_spin.setSuffix(source.unit)
        self.source_full_spin.setSuffix(source.unit)
        self.stops_editor.set_unit(source.unit)
        self.fill_hint.setText(self._fill_hint_text(source))

    def _prefill_source_defaults(self) -> None:
        """Fill the range in with something that already works for this source.

        A gauge is far more likely to want 20-70 °C for a motor than whatever
        the last source's numbers happened to be, so switching sources starts
        from the useful answer and leaves it there to be adjusted. The scale
        comes with it: picking Motor Temperature is what puts the six stops of
        the V5's own derating schedule on the segment, which is the difference
        between designing this and typing it.
        """
        source = fill_sources.get(self.source_combo.currentData())
        self.source_empty_spin.setValue(source.empty_default)
        self.source_full_spin.setValue(source.full_default)
        self.source_wrap_check.setChecked(source.wrap_default)
        self._reset_stops_to_source_default()

    def _reset_stops_to_source_default(self) -> None:
        """Load this source's shipped scale, or clear the scale when it has
        none - in which case the gauge falls back to Color -> Color 2, which is
        all most readings warrant."""
        source = fill_sources.get(self.source_combo.currentData())
        stops = [GaugeStopConfig(at=at, color=color)
                 for at, color in fill_sources.default_stops(source.id)]
        self.stops_editor.set_stops(stops, source.unit)
        self._update_visibility()  # a scale hides the two fallback colors
        self._emit_changed()

    def _fill_hint_text(self, source: fill_sources.FillSource) -> str:
        """What this source does, plus what the export will do about it."""
        if source.id == fill_sources.MANUAL:
            return (
                f"{source.hint.replace('setLevel', 'setRegionLevel')} Exported with no "
                f"reader attached - call setRegionLevel(regionIndex, 0-255) yourself."
            )
        if source.id == fill_sources.CUSTOM:
            return (
                f"{source.hint} Export leaves a LevelFn in the strand's source:: "
                f"namespace - assign it a lambda returning a double before the mode runs."
            )
        where = ""
        if source.port_kind == fill_sources.PORT_SMART:
            where = f" Reads port {self.source_port_spin.value()}."
        elif source.port_kind == fill_sources.PORT_ADI:
            where = f" Reads ADI port {self.source_port_spin.value()}."
        return f"{source.hint}{where} Exported ready to run."

    def _update_visibility(self) -> None:
        kind = self.kind_combo.currentData()
        visible = set(_OVERLAY_VISIBLE_FIELDS.get(kind, set()))
        if kind == OverlayAnimationKind.GAUGE:
            source = self.source_combo.currentData()
            if not fill_sources.get(source).port_kind:
                visible.discard("source_port")
            if not fill_sources.polls_a_device(source):
                # Nothing is being read, so there is nothing to wrap or smooth.
                # Empty At / Full At stay: unlike a Fill meter's, they are what
                # place the color stops, not just what maps a reading.
                visible -= {"source_wrap", "smoothing"}
            if self.preview_sweep_check.isChecked():
                visible.discard("preview_level")
            if self.style_combo.currentData() != GaugeStyleKind.BAR:
                # A whole-segment gauge covers every pixel it owns, so it has
                # no unlit part to color and no direction to reverse.
                visible -= {"invert", "bg_color"}
            if self.stops_editor.stops():
                # Color / Color 2 are only the fallback scale for a gauge with
                # no stops of its own. Showing them next to a real scale invites
                # editing the one thing that has no effect.
                visible -= {"color", "color2"}
        for name, (label, widget) in self._rows.items():
            show = name in visible
            widget.setVisible(show)
            if label is not None:
                label.setVisible(show)

    def load(self, o: OverlayAnimationConfig) -> None:
        self._suspend = True
        idx = self.kind_combo.findData(o.kind)
        if idx >= 0:
            self.kind_combo.setCurrentIndex(idx)
        self.color_btn.set_color(o.color)
        self.color2_btn.set_color(o.color2)
        self.bg_btn.set_color(o.bg_color)
        self.run_length_spin.setValue(o.run_length)
        self.speed_spin.setValue(o.speed)
        self.seamless_check.setChecked(o.seamless)
        self.density_spin.setValue(o.density_pct)
        self.fade_spin.setValue(o.fade_step)
        self.palette_edit.setText(format_palette(o.palette))
        self.segment_width_spin.setValue(o.segment_width)
        self.spacing_spin.setValue(o.spacing)
        self.repeating_check.setChecked(o.repeating)
        # Before the values below it: picking the source is what re-ranges the
        # port field and re-labels the units the bounds and stops land in.
        source_idx = self.source_combo.findData(o.source)
        if source_idx >= 0:
            self.source_combo.setCurrentIndex(source_idx)
        self._sync_source_fields()
        self.source_port_spin.setValue(o.source_port)
        self.source_empty_spin.setValue(o.source_empty)
        self.source_full_spin.setValue(o.source_full)
        self.source_wrap_check.setChecked(o.source_wrap)
        self.smoothing_spin.setValue(o.smoothing)
        self.invert_check.setChecked(o.invert)
        style_idx = self.style_combo.findData(o.style)
        if style_idx >= 0:
            self.style_combo.setCurrentIndex(style_idx)
        blend_idx = self.blend_combo.findData(o.blend)
        if blend_idx >= 0:
            self.blend_combo.setCurrentIndex(blend_idx)
        self.stops_editor.set_stops(o.stops, fill_sources.get(o.source).unit)
        self.preview_sweep_check.setChecked(o.preview_sweep)
        self.preview_level_spin.setValue(o.preview_level)
        self.on_ms_spin.setValue(o.on_ms)
        self.off_ms_spin.setValue(o.off_ms)
        self._suspend = False
        self._update_visibility()

    def save(self, o: OverlayAnimationConfig) -> None:
        o.kind = enum_data(self.kind_combo, OverlayAnimationKind)
        o.color = self.color_btn.color()
        o.color2 = self.color2_btn.color()
        o.bg_color = self.bg_btn.color()
        o.run_length = self.run_length_spin.value()
        o.speed = self.speed_spin.value()
        o.on_ms = self.on_ms_spin.value()
        o.off_ms = self.off_ms_spin.value()
        o.seamless = self.seamless_check.isChecked()
        o.density_pct = self.density_spin.value()
        o.fade_step = self.fade_spin.value()
        parsed = parse_palette(self.palette_edit.text())
        if parsed:
            o.palette = parsed
        o.segment_width = self.segment_width_spin.value()
        o.spacing = self.spacing_spin.value()
        o.repeating = self.repeating_check.isChecked()
        o.source = self.source_combo.currentData()
        o.source_port = self.source_port_spin.value()
        o.source_empty = self.source_empty_spin.value()
        o.source_full = self.source_full_spin.value()
        o.source_wrap = self.source_wrap_check.isChecked()
        o.smoothing = self.smoothing_spin.value()
        o.invert = self.invert_check.isChecked()
        o.style = enum_data(self.style_combo, GaugeStyleKind)
        o.blend = enum_data(self.blend_combo, GaugeBlendKind)
        o.stops = self.stops_editor.stops()
        o.preview_sweep = self.preview_sweep_check.isChecked()
        o.preview_level = self.preview_level_spin.value()


class SpliceMaskPanel(QGroupBox):
    """Splits the strip into equal alternating bins sharing one overlay
    (Split) or lets each region be placed/sized freely with its own
    independent animation (Custom) - see SpliceMaskConfig. Custom regions
    are edited like ModesPanel's mode/phase lists: Add/Remove buttons plus an
    editor (start/width fields + a per-region OverlayAnimationPanel) bound to
    whichever region is selected, with a Divide action for the common case of
    wanting N equal segments rather than N hand-placed ones. Region edits
    mutate the loaded
    SpliceMaskConfig's `regions` list in place rather than a local copy, so
    save() intentionally leaves `regions` untouched. Callers are expected
    (as ModesPanel does) to call save() with the same config object that was
    last passed to load().

    Whether this is the active mask (SpliceMaskConfig.enabled) is decided by
    the enclosing MasksPanel's dropdown, not here - load()/save() leave
    `enabled` alone and only touch this mask's own fields.
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("Splice Mask", parent)
        self._suspend = False
        self._splice: SpliceMaskConfig | None = None
        self._region_idx = -1
        # What Divide splits up. The panel edits a mask, not a strand, so the
        # length has to be handed to it - see set_strip_length().
        self._strip_length = 64
        outer = QVBoxLayout(self)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Split", SpliceModeKind.SPLIT)
        self.mode_combo.addItem("Custom", SpliceModeKind.CUSTOM)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch(1)
        outer.addLayout(mode_row)

        # --- Split mode: sections + 1 equal bins, optionally alternating,
        # sharing a single overlay animation across every masked bin ---
        self.split_widget = QWidget()
        split_layout = QVBoxLayout(self.split_widget)
        split_layout.setContentsMargins(0, 0, 0, 0)
        split_form = QFormLayout()
        self.sections_spin = QSpinBox()
        self.sections_spin.setRange(0, 16)
        self.invert_check = QCheckBox()
        self.alternating_check = QCheckBox()
        self.alt_period_spin = QSpinBox()
        self.alt_period_spin.setRange(50, 5000)
        self.alt_period_spin.setSuffix(" ms")
        self.split_content_combo = QComboBox()
        self.split_content_combo.addItem("Solid Color", False)
        self.split_content_combo.addItem("Animation", True)
        self.split_bg_btn = ColorButton(0x000000)
        split_form.addRow("Sections", self.sections_spin)
        split_form.addRow("Invert", self.invert_check)
        split_form.addRow("Alternating", self.alternating_check)
        split_form.addRow("Alt. Period", self.alt_period_spin)
        split_form.addRow("Content", self.split_content_combo)
        split_form.addRow("Background", self.split_bg_btn)
        split_layout.addLayout(split_form)
        # Shared overlay animation, shown only when Content == "Animation".
        self.overlay_panel = OverlayAnimationPanel("Overlay Animation")
        split_layout.addWidget(self.overlay_panel)
        outer.addWidget(self.split_widget)

        # --- Custom mode: arbitrarily placed/sized regions, each with its
        # own independent animation ---
        self.custom_widget = QWidget()
        custom_layout = QVBoxLayout(self.custom_widget)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        regions_label = QLabel("Regions")
        regions_label.setProperty("role", "sectionHeader")
        custom_layout.addWidget(regions_label)
        self.region_list = QListWidget()
        custom_layout.addWidget(self.region_list)
        region_btn_row = QHBoxLayout()
        self.add_region_btn = QPushButton(theme.icon("plus"), " Add")
        self.remove_region_btn = QPushButton(theme.icon("minus"), " Remove")
        self.remove_region_btn.setProperty("role", "danger")
        region_btn_row.addWidget(self.add_region_btn)
        region_btn_row.addWidget(self.remove_region_btn)
        custom_layout.addLayout(region_btn_row)

        # Placing six equal segments by hand is six start/width pairs to work
        # out and re-work out every time the strip length changes. This is the
        # same arithmetic, done once: set one segment up the way you want it,
        # then divide and only the ports differ between them.
        divide_row = QHBoxLayout()
        self.divide_count_spin = QSpinBox()
        self.divide_count_spin.setRange(1, 32)
        self.divide_count_spin.setValue(6)
        self.divide_count_spin.setToolTip("How many equal segments to split the strip into.")
        self.divide_gap_check = QCheckBox("Gap")
        self.divide_gap_check.setChecked(True)
        self.divide_gap_check.setToolTip(
            "Leave one unlit pixel between segments. Worth having - without it, "
            "two neighbouring segments at similar levels read as one long one."
        )
        self.divide_btn = QPushButton(" Divide")
        self.divide_btn.setToolTip(
            "Replace the regions with this many equal segments, keeping the "
            "animation each one already had (and copying the first onto any new ones)."
        )
        divide_row.addWidget(QLabel("Segments"))
        divide_row.addWidget(self.divide_count_spin)
        divide_row.addWidget(self.divide_gap_check)
        divide_row.addWidget(self.divide_btn)
        divide_row.addStretch(1)
        custom_layout.addLayout(divide_row)

        self.region_editor = QWidget()
        region_editor_layout = QVBoxLayout(self.region_editor)
        region_editor_layout.setContentsMargins(0, 0, 0, 0)
        region_form = QFormLayout()
        self.region_start_spin = QSpinBox()
        self.region_start_spin.setRange(0, 63)
        self.region_width_spin = QSpinBox()
        self.region_width_spin.setRange(1, 64)
        region_form.addRow("Start", self.region_start_spin)
        region_form.addRow("Width", self.region_width_spin)
        region_editor_layout.addLayout(region_form)
        # This region's own animation, independent of every other region.
        # allow_gauge: a Custom region is the one place a gauge means anything.
        self.region_anim_panel = OverlayAnimationPanel("Region Animation", allow_gauge=True)
        region_editor_layout.addWidget(self.region_anim_panel)
        custom_layout.addWidget(self.region_editor)
        self.region_editor.setEnabled(False)
        outer.addWidget(self.custom_widget)

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.sections_spin.valueChanged.connect(self._emit_changed)
        self.invert_check.toggled.connect(self._emit_changed)
        self.alternating_check.toggled.connect(self._emit_changed)
        self.alt_period_spin.valueChanged.connect(self._emit_changed)
        self.split_content_combo.currentIndexChanged.connect(self._on_split_content_changed)
        self.split_bg_btn.color_changed.connect(self._emit_changed)
        self.overlay_panel.changed.connect(self._emit_changed)

        self.region_list.currentRowChanged.connect(self._on_region_selected)
        self.add_region_btn.clicked.connect(self._add_region)
        self.remove_region_btn.clicked.connect(self._remove_region)
        self.divide_btn.clicked.connect(self._divide_strip)
        self.region_start_spin.valueChanged.connect(self._on_region_field_changed)
        self.region_width_spin.valueChanged.connect(self._on_region_field_changed)
        self.region_anim_panel.changed.connect(self._on_region_anim_changed)

        self._update_mode_visibility()
        self._update_overlay_visibility()

    def _emit_changed(self, *_args) -> None:
        if not self._suspend:
            self.changed.emit()

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _on_mode_changed(self, *_args) -> None:
        self._update_mode_visibility()
        self._update_overlay_visibility()
        self._emit_changed()

    def _update_mode_visibility(self) -> None:
        is_split = self.mode_combo.currentData() == SpliceModeKind.SPLIT
        self.split_widget.setVisible(is_split)
        self.custom_widget.setVisible(not is_split)

    def _update_overlay_visibility(self) -> None:
        # Custom mode's per-region animation lives inside region_editor and
        # is always relevant there, only Split's shared overlay needs to
        # hide/show based on the Content choice.
        self.overlay_panel.setVisible(bool(self.split_content_combo.currentData()))

    def _on_split_content_changed(self, *_args) -> None:
        is_animation = bool(self.split_content_combo.currentData())
        self.split_bg_btn.setVisible(not is_animation)
        self._update_overlay_visibility()
        self._emit_changed()

    # ------------------------------------------------------------------
    # Custom region list
    # ------------------------------------------------------------------

    def _region_label(self, r: SpliceRegionConfig) -> str:
        kind_label = OVERLAY_ANIMATION_KIND_LABELS[r.animation.kind]
        return f"{r.start}-{r.start + r.width - 1}  {kind_label}"

    def _refresh_region_list(self) -> None:
        self.region_list.blockSignals(True)
        current = self.region_list.currentRow()
        self.region_list.clear()
        if self._splice is not None:
            for r in self._splice.regions:
                self.region_list.addItem(self._region_label(r))
        if 0 <= current < self.region_list.count():
            self.region_list.setCurrentRow(current)
        self.region_list.blockSignals(False)

    def _add_region(self) -> None:
        if self._splice is None:
            return
        self._splice.regions.append(SpliceRegionConfig())
        self._refresh_region_list()
        self.region_list.setCurrentRow(len(self._splice.regions) - 1)
        self._emit_changed()

    def set_strip_length(self, length: int) -> None:
        """Tell the panel how many pixels Divide has to share out."""
        self._strip_length = max(1, length)

    def _divide_strip(self) -> None:
        """Replace the regions with N equal segments spanning the strip.

        Each segment keeps the animation the region in its place already had,
        so dividing again after a length change re-spaces them without losing
        six configured motor ports. New segments copy the first region's
        animation, which makes "set one up, then divide" the natural flow.
        """
        if self._splice is None:
            return
        count = self.divide_count_spin.value()
        if count > self._strip_length:
            return  # fewer pixels than segments: nothing sensible to make

        existing = list(self._splice.regions)
        template = existing[0].animation if existing else None
        gap = self.divide_gap_check.isChecked()

        # Same share-out as LedStrand::rebuildSpliceMask(): the remainder goes
        # to the first bins, so no pixel is left stranded at the end.
        base, remainder = divmod(self._strip_length, count)
        regions: list[SpliceRegionConfig] = []
        start = 0
        for i in range(count):
            size = base + (1 if i < remainder else 0)
            width = max(1, size - 1) if gap else size
            if i < len(existing):
                animation = copy.deepcopy(existing[i].animation)
            elif template is not None:
                animation = copy.deepcopy(template)
            else:
                animation = OverlayAnimationConfig()
            regions.append(SpliceRegionConfig(start=start, width=width, animation=animation))
            start += size

        self._splice.regions = regions
        self._refresh_region_list()
        self.region_list.setCurrentRow(0)
        self._emit_changed()

    def _remove_region(self) -> None:
        if self._splice is None or not (0 <= self._region_idx < len(self._splice.regions)):
            return
        del self._splice.regions[self._region_idx]
        self._refresh_region_list()
        if self._splice.regions:
            self.region_list.setCurrentRow(min(self._region_idx, len(self._splice.regions) - 1))
        else:
            self._region_idx = -1
            self.region_editor.setEnabled(False)
        self._emit_changed()

    def _on_region_selected(self, row: int) -> None:
        self._region_idx = row
        if self._splice is None or not (0 <= row < len(self._splice.regions)):
            self.region_editor.setEnabled(False)
            return
        self.region_editor.setEnabled(True)
        r = self._splice.regions[row]
        was_suspend = self._suspend
        self._suspend = True
        try:
            self.region_start_spin.setValue(r.start)
            self.region_width_spin.setValue(r.width)
            self.region_anim_panel.load(r.animation)
        finally:
            self._suspend = was_suspend

    def _on_region_field_changed(self, *_args) -> None:
        if self._suspend or self._splice is None or not (0 <= self._region_idx < len(self._splice.regions)):
            return
        r = self._splice.regions[self._region_idx]
        r.start = self.region_start_spin.value()
        r.width = self.region_width_spin.value()
        self._refresh_region_list()
        self.region_list.setCurrentRow(self._region_idx)
        self._emit_changed()

    def _on_region_anim_changed(self) -> None:
        if self._suspend or self._splice is None or not (0 <= self._region_idx < len(self._splice.regions)):
            return
        r = self._splice.regions[self._region_idx]
        self.region_anim_panel.save(r.animation)
        self._refresh_region_list()
        self._emit_changed()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def load(self, s: SpliceMaskConfig) -> None:
        self._suspend = True
        self._splice = s
        self._region_idx = -1
        idx = self.mode_combo.findData(s.mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.sections_spin.setValue(s.sections)
        self.invert_check.setChecked(s.invert)
        self.alternating_check.setChecked(s.alternating)
        self.alt_period_spin.setValue(s.alt_period_ms)
        content_idx = self.split_content_combo.findData(s.use_overlay)
        if content_idx >= 0:
            self.split_content_combo.setCurrentIndex(content_idx)
        self.split_bg_btn.set_color(s.bg_color)
        self.split_bg_btn.setVisible(not s.use_overlay)
        self.overlay_panel.load(s.overlay)
        self._refresh_region_list()
        if s.regions:
            self.region_list.setCurrentRow(0)
        else:
            self.region_editor.setEnabled(False)
        self._update_mode_visibility()
        self._update_overlay_visibility()
        self._suspend = False

    def save(self, s: SpliceMaskConfig) -> None:
        s.mode = enum_data(self.mode_combo, SpliceModeKind)
        s.sections = self.sections_spin.value()
        s.invert = self.invert_check.isChecked()
        s.alternating = self.alternating_check.isChecked()
        s.alt_period_ms = self.alt_period_spin.value()
        s.use_overlay = bool(self.split_content_combo.currentData())
        s.bg_color = self.split_bg_btn.color()
        self.overlay_panel.save(s.overlay)
        # s.regions is not touched here - see class docstring.


# A strand has exactly one mask active at a time (or none), despite `enabled`
# being an independent flag on the model (that is what LedStrand's API takes)
# - MasksPanel is what enforces that at the UI level, the same way
# AnimationPanel's kind_combo makes only one AnimationKind selectable even
# though nothing stops hand-edited JSON from doing otherwise. Kept as its own
# dropdown-plus-panel structure (rather than folding straight into
# SpliceMaskPanel) so a future second mask kind has somewhere to go.
MASK_NONE = "none"
MASK_SPLICE = "splice"

MASK_KIND_LABELS = {
    MASK_NONE: "None",
    MASK_SPLICE: "Splice Mask",
}


class MasksPanel(QGroupBox):
    """Picks which mask (if any) is active, mirroring AnimationPanel's
    kind_combo: one dropdown, and selecting an entry reveals that mask's own
    panel below it instead of showing every mask's fields side by side.

    SpliceMaskConfig still carries its own `enabled` flag on the model (that
    is what LedStrand's API takes), but this panel owns that decision - save()
    sets it to match whatever the dropdown shows. SpliceMaskPanel no longer
    owns that decision itself (see its docstring).
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("Masks", parent)
        self._suspend = False
        outer = QVBoxLayout(self)

        self.mask_kind_combo = QComboBox()
        for kind in (MASK_NONE, MASK_SPLICE):
            self.mask_kind_combo.addItem(MASK_KIND_LABELS[kind], kind)
        outer.addWidget(self.mask_kind_combo)

        self.splice_panel = SpliceMaskPanel()
        outer.addWidget(self.splice_panel)

        self.mask_kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        self.splice_panel.changed.connect(self._emit_changed)

        self._update_visibility()

    def _emit_changed(self, *_args) -> None:
        if not self._suspend:
            self.changed.emit()

    def _on_kind_changed(self, *_args) -> None:
        self._update_visibility()
        self._emit_changed()

    def _update_visibility(self) -> None:
        kind = self.mask_kind_combo.currentData()
        self.splice_panel.setVisible(kind == MASK_SPLICE)

    def set_strip_length(self, length: int) -> None:
        """Forwarded to SpliceMaskPanel - see its own set_strip_length()."""
        self.splice_panel.set_strip_length(length)

    def load(self, splice: SpliceMaskConfig) -> None:
        self._suspend = True
        kind = MASK_SPLICE if splice.enabled else MASK_NONE
        idx = self.mask_kind_combo.findData(kind)
        if idx >= 0:
            self.mask_kind_combo.setCurrentIndex(idx)
        self.splice_panel.load(splice)
        self._update_visibility()
        self._suspend = False

    def save(self, splice: SpliceMaskConfig) -> None:
        self.splice_panel.save(splice)
        splice.enabled = self.mask_kind_combo.currentData() == MASK_SPLICE


class InspectorPanel(QWidget):
    strand_settings_changed = Signal()
    animation_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        from .modes_panel import ModesPanel  # local import: avoids circular import with modes_panel

        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # strand_panel is owned/wired here but deliberately NOT added to this
        # widget's own layout. MainWindow places it in a compact strip above
        # the preview canvas instead, since it's "set once" hardware identity
        # rather than something tweaked as often as the animation, and the
        # right-hand column was getting crowded.
        self.strand_panel = StrandSettingsPanel()

        # Shown only while several strands are selected, the fields below
        # display the anchor strand's values, and MainWindow replays whatever
        # the user touches onto the rest of the group.
        self.group_banner = QLabel()
        self.group_banner.setObjectName("groupBanner")
        self.group_banner.setWordWrap(True)
        banner_font = self.group_banner.font()
        banner_font.setBold(True)
        self.group_banner.setFont(banner_font)
        # A steady violet halo, no hover ramp: the banner only exists while a
        # group edit is armed, so it should read as the one lit thing in the
        # column the whole time it's up.
        theme.bloom(self.group_banner, theme.ACCENT, radius=22, alpha=70)
        self.group_banner.setVisible(False)

        self.use_profile_check = QCheckBox("Use Profile")
        self.use_profile_check.setToolTip(
            "Switch this strand from a single animation to a prioritized list of modes"
        )
        self.anim_panel = AnimationPanel()
        self.masks_panel = MasksPanel()
        self.modes_panel = ModesPanel()

        layout.addWidget(self.group_banner)
        layout.addWidget(self.use_profile_check)
        layout.addWidget(self.anim_panel)
        layout.addWidget(self.masks_panel)
        layout.addWidget(self.modes_panel)
        layout.addStretch(1)

        self.strand_panel.changed.connect(self.strand_settings_changed)
        self.anim_panel.changed.connect(self.animation_changed)
        self.masks_panel.changed.connect(self.animation_changed)
        self.modes_panel.changed.connect(self.animation_changed)
        self.use_profile_check.toggled.connect(self._on_use_profile_toggled)

    def set_group_size(self, count: int) -> None:
        """Tell the inspector how many strands the next edit will hit, so it
        can warn that it's showing one strand but editing several."""
        if count > 1:
            self.group_banner.setText(
                f"Group edit: {count} strands. Fields show the highlighted one; "
                "edits apply to all of them."
            )
        self.group_banner.setVisible(count > 1)

    def _on_use_profile_toggled(self, checked: bool) -> None:
        self._apply_mode_visibility(checked)
        if not self._loading:
            self.animation_changed.emit()

    def _apply_mode_visibility(self, use_profile: bool) -> None:
        self.anim_panel.setVisible(not use_profile)
        self.masks_panel.setVisible(not use_profile)
        self.modes_panel.setVisible(use_profile)

    def load(self, cfg: StrandConfig) -> None:
        self._loading = True
        self.strand_panel.load(cfg)
        self.use_profile_check.setChecked(cfg.use_profile)
        self._apply_mode_visibility(cfg.use_profile)
        self.anim_panel.load(cfg.animation)
        # Before load(): Divide shares out this strand's pixels, not the last one's.
        self.masks_panel.set_strip_length(cfg.length)
        self.masks_panel.load(cfg.splice)
        self.modes_panel.load(cfg)
        self._loading = False

    def save(self, cfg: StrandConfig) -> None:
        self.strand_panel.save(cfg)
        cfg.use_profile = self.use_profile_check.isChecked()
        self.anim_panel.save(cfg.animation)
        self.masks_panel.save(cfg.splice)
        self.modes_panel.save(cfg)
