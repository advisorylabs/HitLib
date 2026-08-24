"""Mode-list + mode editor for a strand's Profile.

Mirrors LedStrand's real Profile/ProfileMode/Sequencer model: a strand has a
list of named, prioritized modes; each mode is either a steady-state
animation or a timed sequence of phases. Checking a mode's checkbox calls
the same activate_mode()/deactivate_mode() the priority stack uses on real
hardware, so toggling multiple modes on at once exercises
priority resolution rather than just switching a single active animation.

AnimationPanel/SpliceMaskPanel are reused (not re-implemented) for editing
whichever target is currently selected (a mode's steady animation, or one
of its phases) by rebinding what their `changed` signal commits into.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .inspector import AnimationPanel, SpliceMaskPanel
from .models import ModeConfig, PhaseConfig, StrandConfig


class ModesPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: StrandConfig | None = None
        self._loading = False
        self._mode_idx = -1
        self._phase_idx = -1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        modes_label = QLabel("MODES  (checked = active)")
        modes_label.setProperty("role", "sectionHeader")
        layout.addWidget(modes_label)
        self.mode_list = QListWidget()
        layout.addWidget(self.mode_list)

        mode_btn_row = QHBoxLayout()
        self.add_mode_btn = QPushButton(theme.icon("plus"), " Add")
        self.remove_mode_btn = QPushButton(theme.icon("minus"), " Remove")
        self.remove_mode_btn.setProperty("role", "danger")
        self.mode_up_btn = QPushButton(theme.icon("arrow-up"), "")
        self.mode_down_btn = QPushButton(theme.icon("arrow-down"), "")
        self.mode_up_btn.setToolTip("Move mode up (higher priority)")
        self.mode_down_btn.setToolTip("Move mode down (lower priority)")
        for b in (self.mode_up_btn, self.mode_down_btn):
            b.setProperty("role", "icon")
        for b in (self.add_mode_btn, self.remove_mode_btn, self.mode_up_btn, self.mode_down_btn):
            mode_btn_row.addWidget(b)
        layout.addLayout(mode_btn_row)

        editor_box = QGroupBox("Mode Editor")
        editor_layout = QVBoxLayout(editor_box)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(0, 255)
        form.addRow("Name", self.name_edit)
        form.addRow("Priority", self.priority_spin)
        editor_layout.addLayout(form)

        self.sequenced_check = QCheckBox("Sequenced (timed phases)")
        editor_layout.addWidget(self.sequenced_check)

        # Steady-state editor (reused for phase editing too, rebound below)
        self.anim_panel = AnimationPanel()
        self.splice_panel = SpliceMaskPanel()
        editor_layout.addWidget(self.anim_panel)
        editor_layout.addWidget(self.splice_panel)

        # Phase list (only visible when sequenced_check is on)
        self.phase_container = QWidget()
        phase_layout = QVBoxLayout(self.phase_container)
        phase_layout.setContentsMargins(0, 0, 0, 0)
        phases_label = QLabel("PHASES  (cycle in order)")
        phases_label.setProperty("role", "sectionHeader")
        phase_layout.addWidget(phases_label)
        self.phase_list = QListWidget()
        phase_layout.addWidget(self.phase_list)
        phase_btn_row = QHBoxLayout()
        self.add_phase_btn = QPushButton(theme.icon("plus"), " Add")
        self.remove_phase_btn = QPushButton(theme.icon("minus"), " Remove")
        self.remove_phase_btn.setProperty("role", "danger")
        self.phase_up_btn = QPushButton(theme.icon("arrow-up"), "")
        self.phase_down_btn = QPushButton(theme.icon("arrow-down"), "")
        self.phase_up_btn.setToolTip("Move phase earlier in the cycle")
        self.phase_down_btn.setToolTip("Move phase later in the cycle")
        for b in (self.phase_up_btn, self.phase_down_btn):
            b.setProperty("role", "icon")
        for b in (self.add_phase_btn, self.remove_phase_btn, self.phase_up_btn, self.phase_down_btn):
            phase_btn_row.addWidget(b)
        phase_layout.addLayout(phase_btn_row)
        phase_form = QFormLayout()
        self.phase_name_edit = QLineEdit()
        self.phase_duration_spin = QSpinBox()
        self.phase_duration_spin.setRange(20, 60000)
        self.phase_duration_spin.setSuffix(" ms")
        phase_form.addRow("Phase Name", self.phase_name_edit)
        phase_form.addRow("Duration", self.phase_duration_spin)
        phase_layout.addLayout(phase_form)
        editor_layout.addWidget(self.phase_container)

        layout.addWidget(editor_box)
        editor_box.setEnabled(False)

        self._editor_box = editor_box

        # Wiring
        self.mode_list.currentRowChanged.connect(self._on_mode_selected)
        self.mode_list.itemChanged.connect(self._on_mode_item_changed)
        self.add_mode_btn.clicked.connect(self._add_mode)
        self.remove_mode_btn.clicked.connect(self._remove_mode)
        self.mode_up_btn.clicked.connect(lambda: self._move_mode(-1))
        self.mode_down_btn.clicked.connect(lambda: self._move_mode(1))

        self.name_edit.textChanged.connect(self._on_mode_field_changed)
        self.priority_spin.valueChanged.connect(self._on_mode_field_changed)
        self.sequenced_check.toggled.connect(self._on_sequenced_toggled)

        self.anim_panel.changed.connect(self._on_leaf_changed)
        self.splice_panel.changed.connect(self._on_leaf_changed)

        self.phase_list.currentRowChanged.connect(self._on_phase_selected)
        self.add_phase_btn.clicked.connect(self._add_phase)
        self.remove_phase_btn.clicked.connect(self._remove_phase)
        self.phase_up_btn.clicked.connect(lambda: self._move_phase(-1))
        self.phase_down_btn.clicked.connect(lambda: self._move_phase(1))
        self.phase_name_edit.textChanged.connect(self._on_phase_field_changed)
        self.phase_duration_spin.valueChanged.connect(self._on_phase_field_changed)

    # ------------------------------------------------------------------
    # Load / emit
    # ------------------------------------------------------------------

    def load(self, config: StrandConfig) -> None:
        self._loading = True
        self._config = config
        self._mode_idx = -1
        self._phase_idx = -1
        self._refresh_mode_list()
        if config.profile_modes:
            self.mode_list.setCurrentRow(0)
        else:
            self._editor_box.setEnabled(False)
        self._loading = False

    def save(self, config: StrandConfig) -> None:
        # profile_modes / active_mode_indices are mutated in place as the
        # user edits, so `config` (== self._config) is already up to date.
        pass

    def _emit_changed(self) -> None:
        if not self._loading:
            self.changed.emit()

    # ------------------------------------------------------------------
    # Mode list
    # ------------------------------------------------------------------

    def _refresh_mode_list(self) -> None:
        self.mode_list.blockSignals(True)
        current = self.mode_list.currentRow()
        self.mode_list.clear()
        for i, mode in enumerate(self._config.profile_modes):
            item = QListWidgetItem(f"{mode.name}  (pri {mode.priority})")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if i in self._config.active_mode_indices else Qt.Unchecked)
            self.mode_list.addItem(item)
        if 0 <= current < self.mode_list.count():
            self.mode_list.setCurrentRow(current)
        self.mode_list.blockSignals(False)

    def _on_mode_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading or self._config is None:
            return
        idx = self.mode_list.row(item)
        active = set(self._config.active_mode_indices)
        if item.checkState() == Qt.Checked:
            active.add(idx)
        else:
            active.discard(idx)
        self._config.active_mode_indices = sorted(active)
        self._emit_changed()

    def _add_mode(self) -> None:
        if self._config is None:
            return
        n = len(self._config.profile_modes) + 1
        self._config.profile_modes.append(ModeConfig(name=f"Mode {n}"))
        self._refresh_mode_list()
        self.mode_list.setCurrentRow(len(self._config.profile_modes) - 1)
        self._emit_changed()

    def _remove_mode(self) -> None:
        if self._config is None or not (0 <= self._mode_idx < len(self._config.profile_modes)):
            return
        removed = self._mode_idx
        del self._config.profile_modes[removed]
        self._config.active_mode_indices = sorted(
            (i if i < removed else i - 1) for i in self._config.active_mode_indices if i != removed
        )
        self._refresh_mode_list()
        new_row = min(removed, len(self._config.profile_modes) - 1)
        self.mode_list.setCurrentRow(new_row)
        self._emit_changed()

    def _move_mode(self, delta: int) -> None:
        if self._config is None:
            return
        i = self._mode_idx
        j = i + delta
        modes = self._config.profile_modes
        if not (0 <= i < len(modes) and 0 <= j < len(modes)):
            return
        modes[i], modes[j] = modes[j], modes[i]
        self._config.active_mode_indices = sorted(
            (j if idx == i else (i if idx == j else idx)) for idx in self._config.active_mode_indices
        )
        self._refresh_mode_list()
        self.mode_list.setCurrentRow(j)
        self._emit_changed()

    def _on_mode_selected(self, row: int) -> None:
        self._mode_idx = row
        self._phase_idx = -1
        if self._config is None or not (0 <= row < len(self._config.profile_modes)):
            self._editor_box.setEnabled(False)
            return
        self._editor_box.setEnabled(True)
        mode = self._config.profile_modes[row]

        # Populating these fields to reflect the newly-selected mode must not
        # itself be treated as an edit, suppress for this whole method
        # (not just the setText/setValue calls), since it cascades into
        # sequenced_check.toggled and phase_list.currentRowChanged too.
        # Save/restore rather than unconditionally clearing: this can run
        # nested inside load()'s own suppression, which must stay in effect
        # afterward.
        was_loading = self._loading
        self._loading = True
        try:
            self.name_edit.setText(mode.name)
            self.priority_spin.setValue(mode.priority)
            self.sequenced_check.setChecked(bool(mode.phases))
            self._update_sequenced_visibility()
            self._refresh_phase_list()
            if mode.phases:
                self.phase_list.setCurrentRow(0)
            else:
                self._bind_leaf(mode.animation, mode.splice)
        finally:
            self._loading = was_loading

    def _on_mode_field_changed(self, *_args) -> None:
        if self._loading or self._config is None or not (0 <= self._mode_idx < len(self._config.profile_modes)):
            return
        mode = self._config.profile_modes[self._mode_idx]
        mode.name = self.name_edit.text().strip() or "Mode"
        mode.priority = self.priority_spin.value()
        self._refresh_mode_list()
        self.mode_list.setCurrentRow(self._mode_idx)
        self._emit_changed()

    def _on_sequenced_toggled(self, checked: bool) -> None:
        if self._loading or self._config is None or not (0 <= self._mode_idx < len(self._config.profile_modes)):
            return
        mode = self._config.profile_modes[self._mode_idx]
        if checked and not mode.phases:
            mode.phases.append(PhaseConfig(name="Phase 1"))
        self._update_sequenced_visibility()
        self._refresh_phase_list()
        if checked and mode.phases:
            self.phase_list.setCurrentRow(0)
        else:
            self._bind_leaf(mode.animation, mode.splice)
        self._emit_changed()

    def _update_sequenced_visibility(self) -> None:
        sequenced = self.sequenced_check.isChecked()
        self.phase_container.setVisible(sequenced)
        if not sequenced:
            self.anim_panel.setVisible(True)
            self.splice_panel.setVisible(True)

    # ------------------------------------------------------------------
    # Phase list (only relevant while sequenced_check is checked)
    # ------------------------------------------------------------------

    def _current_mode(self) -> ModeConfig | None:
        if self._config is None or not (0 <= self._mode_idx < len(self._config.profile_modes)):
            return None
        return self._config.profile_modes[self._mode_idx]

    def _refresh_phase_list(self) -> None:
        mode = self._current_mode()
        self.phase_list.blockSignals(True)
        current = self.phase_list.currentRow()
        self.phase_list.clear()
        if mode:
            for phase in mode.phases:
                self.phase_list.addItem(f"{phase.name}  ({phase.duration_ms} ms)")
        if 0 <= current < self.phase_list.count():
            self.phase_list.setCurrentRow(current)
        self.phase_list.blockSignals(False)

    def _add_phase(self) -> None:
        mode = self._current_mode()
        if mode is None:
            return
        n = len(mode.phases) + 1
        mode.phases.append(PhaseConfig(name=f"Phase {n}"))
        self._refresh_phase_list()
        self.phase_list.setCurrentRow(len(mode.phases) - 1)
        self._emit_changed()

    def _remove_phase(self) -> None:
        mode = self._current_mode()
        if mode is None or not (0 <= self._phase_idx < len(mode.phases)):
            return
        del mode.phases[self._phase_idx]
        self._refresh_phase_list()
        if mode.phases:
            self.phase_list.setCurrentRow(min(self._phase_idx, len(mode.phases) - 1))
        else:
            self._phase_idx = -1
            self.anim_panel.setVisible(False)
            self.splice_panel.setVisible(False)
        self._emit_changed()

    def _move_phase(self, delta: int) -> None:
        mode = self._current_mode()
        if mode is None:
            return
        i = self._phase_idx
        j = i + delta
        if not (0 <= i < len(mode.phases) and 0 <= j < len(mode.phases)):
            return
        mode.phases[i], mode.phases[j] = mode.phases[j], mode.phases[i]
        self._refresh_phase_list()
        self.phase_list.setCurrentRow(j)
        self._emit_changed()

    def _on_phase_selected(self, row: int) -> None:
        self._phase_idx = row
        mode = self._current_mode()
        if mode is None or not (0 <= row < len(mode.phases)):
            self.anim_panel.setVisible(False)
            self.splice_panel.setVisible(False)
            return
        phase = mode.phases[row]
        was_loading = self._loading
        self._loading = True
        try:
            self.phase_name_edit.setText(phase.name)
            self.phase_duration_spin.setValue(phase.duration_ms)
        finally:
            self._loading = was_loading
        self._bind_leaf(phase.animation, phase.splice)

    def _on_phase_field_changed(self, *_args) -> None:
        mode = self._current_mode()
        if self._loading or mode is None or not (0 <= self._phase_idx < len(mode.phases)):
            return
        phase = mode.phases[self._phase_idx]
        phase.name = self.phase_name_edit.text().strip() or "Phase"
        phase.duration_ms = self.phase_duration_spin.value()
        self._refresh_phase_list()
        self.phase_list.setCurrentRow(self._phase_idx)
        self._emit_changed()

    # ------------------------------------------------------------------
    # Shared animation/splice editor rebinding
    # ------------------------------------------------------------------

    def _bind_leaf(self, animation, splice) -> None:
        self.anim_panel.setVisible(True)
        self.splice_panel.setVisible(True)
        self.anim_panel.load(animation)
        self.splice_panel.load(splice)

    def _on_leaf_changed(self) -> None:
        if self._loading:
            return
        mode = self._current_mode()
        if mode is None:
            return
        if mode.phases and 0 <= self._phase_idx < len(mode.phases):
            phase = mode.phases[self._phase_idx]
            self.anim_panel.save(phase.animation)
            self.splice_panel.save(phase.splice)
        elif not mode.phases:
            self.anim_panel.save(mode.animation)
            self.splice_panel.save(mode.splice)
        self._emit_changed()
