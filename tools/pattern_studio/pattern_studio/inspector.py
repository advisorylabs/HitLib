"""Strand settings / animation / splice-mask editor for the currently selected strand.

Each panel owns a `changed` signal; StrandSettingsPanel's
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
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# ModesPanel imports AnimationPanel/SpliceMaskPanel from this module, so it must
# be imported lazily inside InspectorPanel to avoid a circular import.

from .models import (
    ANIMATION_KIND_LABELS,
    OVERLAY_ANIMATION_KIND_LABELS,
    AnimationConfig,
    AnimationKind,
    OverlayAnimationConfig,
    OverlayAnimationKind,
    SpliceMaskConfig,
    SpliceModeKind,
    SpliceRegionConfig,
    StrandConfig,
)
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
        # decrement arrows alongside the digits/suffix. The scroll area MainWindow wraps this
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


_OVERLAY_VISIBLE_FIELDS: dict[OverlayAnimationKind, set[str]] = {
    OverlayAnimationKind.OFF: set(),
    OverlayAnimationKind.SOLID: {"color"},
    OverlayAnimationKind.PULSE: {"color", "bg_color", "run_length", "speed"},
    OverlayAnimationKind.FLASH: {"color", "bg_color", "speed"},
    OverlayAnimationKind.FLOW: {"color", "color2", "speed"},
    OverlayAnimationKind.RAINBOW: {"speed"},
}


class OverlayAnimationPanel(QGroupBox):
    """Editor for an OverlayAnimationConfig -- reused for two different
    things that happen to share the same overlay*() vocabulary: Split mode's
    single shared overlay buffer (SpliceMaskConfig.overlay), and each Custom
    region's own independent animation (SpliceRegionConfig.animation). The
    `title` param exists so the group box can say which one it is.
    """

    changed = Signal()

    def __init__(self, title: str = "Overlay Animation", parent=None):
        super().__init__(title, parent)
        self._suspend = False
        outer = QVBoxLayout(self)

        self.kind_combo = QComboBox()
        for kind in OverlayAnimationKind:
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

        self._rows: dict[str, tuple] = {}

        def add_row(name: str, label: str, widget) -> None:
            form.addRow(label, widget)
            self._rows[name] = (form.labelForField(widget), widget)

        add_row("color", "Color", self.color_btn)
        add_row("color2", "Color 2", self.color2_btn)
        add_row("bg_color", "Background", self.bg_btn)
        add_row("run_length", "Run Length", self.run_length_spin)
        add_row("speed", "Speed", self.speed_spin)

        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        for widget, signal_name in (
            (self.color_btn, "color_changed"),
            (self.color2_btn, "color_changed"),
            (self.bg_btn, "color_changed"),
            (self.run_length_spin, "valueChanged"),
            (self.speed_spin, "valueChanged"),
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
        visible = _OVERLAY_VISIBLE_FIELDS.get(kind, set())
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
        self._suspend = False
        self._update_visibility()

    def save(self, o: OverlayAnimationConfig) -> None:
        o.kind = self.kind_combo.currentData()
        o.color = self.color_btn.color()
        o.color2 = self.color2_btn.color()
        o.bg_color = self.bg_btn.color()
        o.run_length = self.run_length_spin.value()
        o.speed = self.speed_spin.value()


class SpliceMaskPanel(QGroupBox):
    """Splits the strip into equal alternating bins sharing one overlay
    (Split) or lets each region be placed/sized freely with its own
    independent animation (Custom) -- see SpliceMaskConfig. Custom regions
    are edited like ModesPanel's mode/phase lists: Add/Remove buttons plus an
    editor (start/width fields + a per-region OverlayAnimationPanel) bound to
    whichever region is selected. Region edits mutate the loaded
    SpliceMaskConfig's `regions` list in place rather than a local copy, so
    save() intentionally leaves `regions` untouched -- callers are expected
    (as ModesPanel does) to call save() with the same config object that was
    last passed to load().
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("Splice Mask", parent)
        self._suspend = False
        self._splice: SpliceMaskConfig | None = None
        self._region_idx = -1
        self.setCheckable(True)
        self.setChecked(False)
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
        custom_layout.addWidget(QLabel("Regions"))
        self.region_list = QListWidget()
        custom_layout.addWidget(self.region_list)
        region_btn_row = QHBoxLayout()
        self.add_region_btn = QPushButton("Add")
        self.remove_region_btn = QPushButton("Remove")
        region_btn_row.addWidget(self.add_region_btn)
        region_btn_row.addWidget(self.remove_region_btn)
        custom_layout.addLayout(region_btn_row)

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
        # This region's own animation -- independent of every other region.
        self.region_anim_panel = OverlayAnimationPanel("Region Animation")
        region_editor_layout.addWidget(self.region_anim_panel)
        custom_layout.addWidget(self.region_editor)
        self.region_editor.setEnabled(False)
        outer.addWidget(self.custom_widget)

        self.toggled.connect(self._emit_changed)
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
        # is always relevant there -- only Split's shared overlay needs to
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
        self.setChecked(s.enabled)
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
        s.enabled = self.isChecked()
        s.mode = self.mode_combo.currentData()
        s.sections = self.sections_spin.value()
        s.invert = self.invert_check.isChecked()
        s.alternating = self.alternating_check.isChecked()
        s.alt_period_ms = self.alt_period_spin.value()
        s.use_overlay = bool(self.split_content_combo.currentData())
        s.bg_color = self.split_bg_btn.color()
        self.overlay_panel.save(s.overlay)
        # s.regions is not touched here -- see class docstring.


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
