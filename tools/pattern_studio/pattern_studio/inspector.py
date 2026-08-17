"""Strand settings / animation / splice-mask editor for the currently selected strand.

StrandSettingsPanel is laid out as a compact horizontal strip (MainWindow
places it above the preview canvas, not in the right-hand column) since its
fields are "set once" hardware identity, not something tweaked as often as
the animation. AnimationPanel/SpliceMaskPanel/ModesPanel stay in the
resizable right column. Each owns a `changed` signal; StrandSettingsPanel's
changes require recreating the engine Strand (length/port/refresh_ms affect
buffer sizing), the others only need re-issuing the animation call, so
InspectorPanel re-exposes them as two signals matching StrandSession's
rebuild() vs reapply_animation() split.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# ModesPanel imports AnimationPanel/SpliceMaskPanel from this module, so it must
# be imported lazily inside InspectorPanel to avoid a circular import.

from .models import ANIMATION_KIND_LABELS, AnimationConfig, AnimationKind, SpliceMaskConfig, StrandConfig
from .widgets import ColorButton, format_palette, parse_palette

_VISIBLE_FIELDS: dict[AnimationKind, set[str]] = {
    AnimationKind.OFF: set(),
    AnimationKind.SOLID: {"color"},
    AnimationKind.PULSE: {"color", "bg_color", "run_length", "speed", "invert", "bounce"},
    AnimationKind.FLASH: {"color", "bg_color", "speed"},
    AnimationKind.FLOW: {"color", "color2", "speed", "invert"},
    AnimationKind.RAINBOW: {"speed"},
    AnimationKind.TWINKLE: {"palette", "bg_color", "density_pct", "fade_step"},
    AnimationKind.BITSCROLL: {
        "color", "bg_color", "segment_width", "spacing", "speed", "invert", "bounce", "repeating",
    },
}


class StrandSettingsPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._suspend = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)

        # No explicit width caps on the spinboxes: QSpinBox's own sizeHint()
        # already reserves the right amount of room for its increment/
        # decrement arrows alongside the digits/suffix -- an earlier version
        # of this row capped them tighter than that and the arrows ended up
        # overlapping the numbers. The scroll area MainWindow wraps this
        # strip in handles the case where the row as a whole doesn't fit.
        self.name_edit = QLineEdit()
        self.name_edit.setMaximumWidth(120)
        self.adi_port_spin = QSpinBox()
        self.adi_port_spin.setRange(1, 8)
        self.smart_port_spin = QSpinBox()
        self.smart_port_spin.setRange(0, 21)
        self.smart_port_spin.setSpecialValueText("Direct")
        self.length_spin = QSpinBox()
        self.length_spin.setRange(1, 64)
        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(5, 500)
        self.refresh_spin.setSuffix(" ms")
        self.brightness_spin = QSpinBox()
        self.brightness_spin.setRange(0, 100)
        self.brightness_spin.setSuffix(" %")

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
        self.invert_check = QCheckBox()
        self.bounce_check = QCheckBox()
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

        self._rows: dict[str, tuple] = {}

        def add_row(name: str, label: str, widget) -> None:
            form.addRow(label, widget)
            self._rows[name] = (form.labelForField(widget), widget)

        add_row("color", "Color", self.color_btn)
        add_row("color2", "Color 2", self.color2_btn)
        add_row("bg_color", "Background", self.bg_btn)
        add_row("run_length", "Run Length", self.run_length_spin)
        add_row("speed", "Speed", self.speed_spin)
        add_row("invert", "Invert", self.invert_check)
        add_row("bounce", "Bounce", self.bounce_check)
        add_row("density_pct", "Density", self.density_spin)
        add_row("fade_step", "Fade Step", self.fade_spin)
        add_row("palette", "Palette", self.palette_edit)
        add_row("segment_width", "Seg. Width", self.segment_width_spin)
        add_row("spacing", "Spacing", self.spacing_spin)
        add_row("repeating", "Repeating", self.repeating_check)

        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        for widget, signal_name in (
            (self.color_btn, "color_changed"),
            (self.color2_btn, "color_changed"),
            (self.bg_btn, "color_changed"),
            (self.run_length_spin, "valueChanged"),
            (self.speed_spin, "valueChanged"),
            (self.invert_check, "toggled"),
            (self.bounce_check, "toggled"),
            (self.density_spin, "valueChanged"),
            (self.fade_spin, "valueChanged"),
            (self.palette_edit, "textChanged"),
            (self.segment_width_spin, "valueChanged"),
            (self.spacing_spin, "valueChanged"),
            (self.repeating_check, "toggled"),
        ):
            getattr(widget, signal_name).connect(self._emit_changed)

        self._update_visibility()

    def _emit_changed(self, *_args) -> None:
        if not self._suspend:
            self.changed.emit()

    def _on_kind_changed(self, *_args) -> None:
        self._update_visibility()
        self._emit_changed()

    def _update_visibility(self) -> None:
        kind = self.kind_combo.currentData()
        visible = _VISIBLE_FIELDS.get(kind, set())
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
        self.invert_check.setChecked(a.invert)
        self.bounce_check.setChecked(a.bounce)
        self.density_spin.setValue(a.density_pct)
        self.fade_spin.setValue(a.fade_step)
        self.palette_edit.setText(format_palette(a.palette))
        self.segment_width_spin.setValue(a.segment_width)
        self.spacing_spin.setValue(a.spacing)
        self.repeating_check.setChecked(a.repeating)
        self._suspend = False
        self._update_visibility()

    def save(self, a: AnimationConfig) -> None:
        a.kind = self.kind_combo.currentData()
        a.color = self.color_btn.color()
        a.color2 = self.color2_btn.color()
        a.bg_color = self.bg_btn.color()
        a.run_length = self.run_length_spin.value()
        a.speed = self.speed_spin.value()
        a.invert = self.invert_check.isChecked()
        a.bounce = self.bounce_check.isChecked()
        a.density_pct = self.density_spin.value()
        a.fade_step = self.fade_spin.value()
        parsed = parse_palette(self.palette_edit.text())
        if parsed:
            a.palette = parsed
        a.segment_width = self.segment_width_spin.value()
        a.spacing = self.spacing_spin.value()
        a.repeating = self.repeating_check.isChecked()


class SpliceMaskPanel(QGroupBox):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("Splice Mask", parent)
        self._suspend = False
        self.setCheckable(True)
        self.setChecked(False)
        form = QFormLayout(self)

        self.sections_spin = QSpinBox()
        self.sections_spin.setRange(0, 16)
        self.invert_check = QCheckBox()
        self.alternating_check = QCheckBox()
        self.alt_period_spin = QSpinBox()
        self.alt_period_spin.setRange(50, 5000)
        self.alt_period_spin.setSuffix(" ms")
        self.bg_btn = ColorButton(0x000000)

        form.addRow("Sections", self.sections_spin)
        form.addRow("Invert", self.invert_check)
        form.addRow("Alternating", self.alternating_check)
        form.addRow("Alt. Period", self.alt_period_spin)
        form.addRow("Background", self.bg_btn)

        self.toggled.connect(self._emit_changed)
        self.sections_spin.valueChanged.connect(self._emit_changed)
        self.invert_check.toggled.connect(self._emit_changed)
        self.alternating_check.toggled.connect(self._emit_changed)
        self.alt_period_spin.valueChanged.connect(self._emit_changed)
        self.bg_btn.color_changed.connect(self._emit_changed)

    def _emit_changed(self, *_args) -> None:
        if not self._suspend:
            self.changed.emit()

    def load(self, s: SpliceMaskConfig) -> None:
        self._suspend = True
        self.setChecked(s.enabled)
        self.sections_spin.setValue(s.sections)
        self.invert_check.setChecked(s.invert)
        self.alternating_check.setChecked(s.alternating)
        self.alt_period_spin.setValue(s.alt_period_ms)
        self.bg_btn.set_color(s.bg_color)
        self._suspend = False

    def save(self, s: SpliceMaskConfig) -> None:
        s.enabled = self.isChecked()
        s.sections = self.sections_spin.value()
        s.invert = self.invert_check.isChecked()
        s.alternating = self.alternating_check.isChecked()
        s.alt_period_ms = self.alt_period_spin.value()
        s.bg_color = self.bg_btn.color()


class InspectorPanel(QWidget):
    strand_settings_changed = Signal()
    animation_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        from .modes_panel import ModesPanel  # local import: avoids circular import with modes_panel

        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # strand_panel is owned/wired here but deliberately NOT added to this
        # widget's own layout -- MainWindow places it in a compact strip above
        # the preview canvas instead, since it's "set once" hardware identity
        # rather than something tweaked as often as the animation, and the
        # right-hand column was getting crowded.
        self.strand_panel = StrandSettingsPanel()
        self.use_profile_check = QCheckBox("Use Profile")
        self.use_profile_check.setToolTip(
            "Switch this strand from a single animation to a prioritized list of modes"
        )
        self.anim_panel = AnimationPanel()
        self.splice_panel = SpliceMaskPanel()
        self.modes_panel = ModesPanel()

        layout.addWidget(self.use_profile_check)
        layout.addWidget(self.anim_panel)
        layout.addWidget(self.splice_panel)
        layout.addWidget(self.modes_panel)
        layout.addStretch(1)

        self.strand_panel.changed.connect(self.strand_settings_changed)
        self.anim_panel.changed.connect(self.animation_changed)
        self.splice_panel.changed.connect(self.animation_changed)
        self.modes_panel.changed.connect(self.animation_changed)
        self.use_profile_check.toggled.connect(self._on_use_profile_toggled)

    def _on_use_profile_toggled(self, checked: bool) -> None:
        self._apply_mode_visibility(checked)
        if not self._loading:
            self.animation_changed.emit()

    def _apply_mode_visibility(self, use_profile: bool) -> None:
        self.anim_panel.setVisible(not use_profile)
        self.splice_panel.setVisible(not use_profile)
        self.modes_panel.setVisible(use_profile)

    def load(self, cfg: StrandConfig) -> None:
        self._loading = True
        self.strand_panel.load(cfg)
        self.use_profile_check.setChecked(cfg.use_profile)
        self._apply_mode_visibility(cfg.use_profile)
        self.anim_panel.load(cfg.animation)
        self.splice_panel.load(cfg.splice)
        self.modes_panel.load(cfg)
        self._loading = False

    def save(self, cfg: StrandConfig) -> None:
        self.strand_panel.save(cfg)
        cfg.use_profile = self.use_profile_check.isChecked()
        self.anim_panel.save(cfg.animation)
        self.splice_panel.save(cfg.splice)
        self.modes_panel.save(cfg)
