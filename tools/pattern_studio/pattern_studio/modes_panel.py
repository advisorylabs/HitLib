"""Mode-list + mode editor for a strand's Profile.

Mirrors LedStrand's real Profile/ProfileMode/Sequencer model: a strand has a
list of named, prioritized modes; each mode is either a steady-state
animation or a timed sequence of phases. Checking a mode's checkbox calls
the same activate_mode()/deactivate_mode() the priority stack uses on real
hardware, so toggling multiple modes on at once exercises
priority resolution rather than just switching a single active animation.

AnimationPanel/MasksPanel are reused (not re-implemented) for editing
whichever target is currently selected (a mode's steady animation, or one
of its phases) by rebinding what their `changed` signal commits into.
"""

from __future__ import annotations

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
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .inspector import (
    AnimationPanel,
    MasksPanel,
    bit_width_spin,
    set_spread_direction,
    spread_direction,
    spread_direction_combo,
)
from .models import (
    TRANSITION_KIND_LABELS,
    ModeConfig,
    PhaseConfig,
    StrandConfig,
    TransitionKind,
)
from .strand_list import RowDelegate
from .widgets import enum_data


class ModesPanel(QWidget):
    changed = Signal()
    #: (mode, phase): play that phase's transition on the preview now.
    preview_transition_requested = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: StrandConfig | None = None
        self._loading = False
        self._mode_idx = -1
        self._phase_idx = -1
        # The (mode, phase) each strand last showed, by the config's id(), so
        # moving between strands - or from a group to one of its strands -
        # comes back to it rather than to the first mode.
        self._memory: dict[int, tuple[int, int]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        modes_label = QLabel("MODES  (checked = active)")
        modes_label.setProperty("role", "sectionHeader")
        layout.addWidget(modes_label)
        self.mode_list = QListWidget()
        # The strand list's selected-row look, so the mode being edited is
        # marked the same way the strand being edited is. Checked says which
        # modes are active; the highlight says which one is in the editor.
        self.mode_list.setItemDelegate(RowDelegate(self.mode_list))
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

        # Steady-state editor (reused for phase editing too, rebound below).
        # No "Open as Custom" here: the window only opens the strand's own
        # animation that way, and nothing would answer this copy's button.
        self.anim_panel = AnimationPanel(show_open_custom=False)
        self.masks_panel = MasksPanel()
        editor_layout.addWidget(self.anim_panel)
        editor_layout.addWidget(self.masks_panel)

        # Phase list (only visible when sequenced_check is on)
        self.phase_container = QWidget()
        phase_layout = QVBoxLayout(self.phase_container)
        phase_layout.setContentsMargins(0, 0, 0, 0)
        phases_label = QLabel("PHASES  (cycle in order)")
        phases_label.setProperty("role", "sectionHeader")
        phase_layout.addWidget(phases_label)
        self.phase_list = QListWidget()
        self.phase_list.setItemDelegate(RowDelegate(self.phase_list))
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
        # How many times round the loop it plays: 0 is every time.
        self.phase_plays_spin = QSpinBox()
        self.phase_plays_spin.setRange(0, 255)
        self.phase_plays_spin.setSpecialValueText("Every loop")
        self.phase_plays_spin.setToolTip(
            "How many times round the loop this phase plays before it is "
            "skipped. 1 makes it an intro: it plays once each time the mode "
            "starts, and the loop carries on without it."
        )
        # How it takes over from the phase before it.
        self.transition_combo = QComboBox()
        for kind in TransitionKind:
            self.transition_combo.addItem(TRANSITION_KIND_LABELS[kind], kind)
        self.transition_combo.setToolTip(
            "How this phase comes in over a different phase before it. The "
            "outgoing frame holds while this phase's animation, already running, "
            "wipes in. It does not play when the mode starts, or when the loop "
            "comes back round to this same phase."
        )
        self.transition_ms_spin = QSpinBox()
        self.transition_ms_spin.setRange(20, 10000)
        self.transition_ms_spin.setSingleStep(50)
        self.transition_ms_spin.setSuffix(" ms")
        self.transition_direction_combo = spread_direction_combo()
        self.transition_bit_width_spin = bit_width_spin()
        self.transition_fade_spin = QSpinBox()
        self.transition_fade_spin.setRange(0, 255)
        self.transition_fade_spin.setSpecialValueText("Instant")
        self.transition_fade_spin.setToolTip(
            "Brightness each bit gains per tick as it comes in, so bigger is "
            "quicker. Instant lights each bit all at once."
        )
        phase_form.addRow("Phase Name", self.phase_name_edit)
        phase_form.addRow("Duration", self.phase_duration_spin)
        phase_form.addRow("Plays", self.phase_plays_spin)
        phase_form.addRow("Transition In", self.transition_combo)
        phase_form.addRow("Over", self.transition_ms_spin)
        phase_form.addRow("Direction", self.transition_direction_combo)
        phase_form.addRow("Bit Width", self.transition_bit_width_spin)
        phase_form.addRow("Fade Step", self.transition_fade_spin)
        # A wipe only plays when the loop moves into this phase, which can be
        # most of a cycle away. This plays it on the preview now.
        self.preview_transition_btn = QPushButton(theme.icon("play"), " Preview Transition")
        theme.HoverBloom(self.preview_transition_btn, theme.FOCUS)
        phase_form.addRow("", self.preview_transition_btn)
        self._phase_form = phase_form
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
        self.masks_panel.changed.connect(self._on_leaf_changed)

        self.phase_list.currentRowChanged.connect(self._on_phase_selected)
        self.add_phase_btn.clicked.connect(self._add_phase)
        self.remove_phase_btn.clicked.connect(self._remove_phase)
        self.phase_up_btn.clicked.connect(lambda: self._move_phase(-1))
        self.phase_down_btn.clicked.connect(lambda: self._move_phase(1))
        self.phase_name_edit.textChanged.connect(self._on_phase_field_changed)
        self.phase_duration_spin.valueChanged.connect(self._on_phase_field_changed)
        self.phase_plays_spin.valueChanged.connect(self._on_plays_changed)
        self.transition_combo.currentIndexChanged.connect(self._on_transition_kind_changed)
        for widget, signal_name in (
            (self.transition_ms_spin, "valueChanged"),
            (self.transition_direction_combo, "currentIndexChanged"),
            (self.transition_bit_width_spin, "valueChanged"),
            (self.transition_fade_spin, "valueChanged"),
        ):
            getattr(widget, signal_name).connect(self._on_phase_field_changed)
        self.preview_transition_btn.clicked.connect(self._on_preview_transition)
        self._update_transition_visibility()

    # ------------------------------------------------------------------
    # Load / emit
    # ------------------------------------------------------------------

    def load(self, config: StrandConfig) -> None:
        self._loading = True
        self._remember()
        # A strand seen before comes back to what it showed. One that has not
        # been - the rest of a group the anchor was just edited as - carries
        # on from where the editor is, rather than jumping to the first mode.
        mode_row, phase_row = self._memory.get(id(config), (self._mode_idx, self._phase_idx))
        self._config = config
        # Divide's length is not taken from config here: for the head of a
        # chain it is the chain's, which only the window knows, and
        # InspectorPanel.load() hands it to masks_panel just before this.
        self._mode_idx = -1
        self._phase_idx = -1
        self._refresh_mode_list()
        if config.profile_modes:
            self._select_mode(min(max(mode_row, 0), len(config.profile_modes) - 1), phase_row)
        else:
            self._select_mode(-1)
        self._loading = False

    def _remember(self) -> None:
        if self._config is not None and self._mode_idx >= 0:
            self._memory[id(self._config)] = (self._mode_idx, self._phase_idx)

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

    def _select_mode(self, row: int, phase_row: int = 0) -> None:
        """Make @p row current and show it in the editor.

        Always re-shown, not left to the list's signals: _refresh_mode_list()
        keeps the row that was current, and setCurrentRow() emits nothing
        when asked for that same row - after a load or a remove it often is,
        and the editor would go on showing (and writing) another mode.
        """
        self.mode_list.blockSignals(True)
        self.mode_list.setCurrentRow(row)
        self.mode_list.blockSignals(False)
        self._on_mode_selected(row, phase_row)

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
        self._select_mode(min(removed, len(self._config.profile_modes) - 1))
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

    def _on_mode_selected(self, row: int, phase_row: int = 0) -> None:
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
                self._select_phase(min(max(phase_row, 0), len(mode.phases) - 1))
            else:
                self._bind_leaf(mode.animation, mode.splice)
        finally:
            self._loading = was_loading
        self._remember()

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
        elif not checked:
            # A mode is sequenced exactly when it has phases - that is what
            # the engine plays and codegen emits - so unchecking drops them
            # rather than just hiding them. Left in place, the mode would go
            # on cycling them, and the steady editor shown below would write
            # into whichever phase was last selected. Undo brings them back.
            mode.phases = []
            self._phase_idx = -1
        self._update_sequenced_visibility()
        self._refresh_phase_list()
        if checked:
            self._select_phase(0)
        else:
            self._bind_leaf(mode.animation, mode.splice)
        self._emit_changed()

    def _update_sequenced_visibility(self) -> None:
        sequenced = self.sequenced_check.isChecked()
        self.phase_container.setVisible(sequenced)
        if not sequenced:
            self.anim_panel.setVisible(True)
            self.masks_panel.setVisible(True)

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
                plays = f", {phase.plays}x" if phase.plays else ""
                self.phase_list.addItem(f"{phase.name}  ({phase.duration_ms} ms{plays})")
        if 0 <= current < self.phase_list.count():
            self.phase_list.setCurrentRow(current)
        self.phase_list.blockSignals(False)
        # A second phase is what gives a transition something to come in over.
        self._update_preview_button()

    def _select_phase(self, row: int) -> None:
        """Make @p row current and show it - see _select_mode() for why the
        list's own signal cannot be relied on to do the showing."""
        self.phase_list.blockSignals(True)
        self.phase_list.setCurrentRow(row)
        self.phase_list.blockSignals(False)
        self._on_phase_selected(row)

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
            self._select_phase(min(self._phase_idx, len(mode.phases) - 1))
        else:
            self._phase_idx = -1
            self.anim_panel.setVisible(False)
            self.masks_panel.setVisible(False)
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
            self.masks_panel.setVisible(False)
            return
        phase = mode.phases[row]
        was_loading = self._loading
        self._loading = True
        try:
            self.phase_name_edit.setText(phase.name)
            self.phase_duration_spin.setValue(phase.duration_ms)
            self.phase_plays_spin.setValue(phase.plays)
            self._sync_plays_suffix()
            t = phase.transition
            self.transition_combo.setCurrentIndex(max(0, self.transition_combo.findData(t.kind)))
            self.transition_ms_spin.setValue(t.duration_ms)
            set_spread_direction(self.transition_direction_combo, t.origin, t.invert)
            self.transition_bit_width_spin.setValue(t.bit_width)
            self.transition_fade_spin.setValue(t.fade_step)
            self._update_transition_visibility()
        finally:
            self._loading = was_loading
        self._bind_leaf(phase.animation, phase.splice)
        self._remember()

    def _on_phase_field_changed(self, *_args) -> None:
        mode = self._current_mode()
        if self._loading or mode is None or not (0 <= self._phase_idx < len(mode.phases)):
            return
        phase = mode.phases[self._phase_idx]
        phase.name = self.phase_name_edit.text().strip() or "Phase"
        phase.duration_ms = self.phase_duration_spin.value()
        phase.plays = self.phase_plays_spin.value()
        t = phase.transition
        t.kind = enum_data(self.transition_combo, TransitionKind)
        t.duration_ms = self.transition_ms_spin.value()
        t.origin, t.invert = spread_direction(self.transition_direction_combo)
        t.bit_width = self.transition_bit_width_spin.value()
        t.fade_step = self.transition_fade_spin.value()
        self._refresh_phase_list()
        self.phase_list.setCurrentRow(self._phase_idx)
        self._emit_changed()

    def _on_plays_changed(self, *_args) -> None:
        self._sync_plays_suffix()
        self._on_phase_field_changed()

    def _sync_plays_suffix(self) -> None:
        """"1 time", "3 times" - Every loop is the special value, at 0."""
        self.phase_plays_spin.setSuffix(" time" if self.phase_plays_spin.value() == 1 else " times")

    def _on_transition_kind_changed(self, *_args) -> None:
        self._update_transition_visibility()
        self._on_phase_field_changed()

    def _update_transition_visibility(self) -> None:
        """Only the fields the picked transition reads."""
        kind = self.transition_combo.currentData()
        shown = {
            self.transition_ms_spin: kind != TransitionKind.CUT,
            self.transition_direction_combo: kind == TransitionKind.SPREAD,
            self.transition_bit_width_spin: kind == TransitionKind.TWINKLE,
            self.transition_fade_spin: kind == TransitionKind.TWINKLE,
        }
        for widget, show in shown.items():
            widget.setVisible(show)
            label = self._phase_form.labelForField(widget)
            if label is not None:
                label.setVisible(show)
        self._update_preview_button()

    def _update_preview_button(self) -> None:
        """Offer Preview Transition only where there is a wipe to see."""
        mode = self._current_mode()
        cut = self.transition_combo.currentData() == TransitionKind.CUT
        lone = mode is None or len(mode.phases) < 2
        self.preview_transition_btn.setEnabled(not cut and not lone)
        if cut:
            why = "Pick a transition first - a cut has nothing to preview."
        elif lone:
            why = ("A transition plays coming in from a different phase, so it "
                   "needs a second phase to come in over.")
        else:
            why = ("Play this phase coming in over the one before it on the "
                   "preview now, then go back to what the strand plays.")
        self.preview_transition_btn.setToolTip(why)

    def _on_preview_transition(self) -> None:
        mode = self._current_mode()
        if mode is not None and 0 <= self._phase_idx < len(mode.phases):
            self.preview_transition_requested.emit(self._mode_idx, self._phase_idx)

    # ------------------------------------------------------------------
    # Shared animation/splice editor rebinding
    # ------------------------------------------------------------------

    def _bind_leaf(self, animation, splice) -> None:
        self.anim_panel.setVisible(True)
        self.masks_panel.setVisible(True)
        self.anim_panel.load(animation)
        self.masks_panel.load(splice)

    def _on_leaf_changed(self) -> None:
        if self._loading:
            return
        mode = self._current_mode()
        if mode is None:
            return
        if mode.phases and 0 <= self._phase_idx < len(mode.phases):
            phase = mode.phases[self._phase_idx]
            self.anim_panel.save(phase.animation)
            self.masks_panel.save(phase.splice)
        elif not mode.phases:
            self.anim_panel.save(mode.animation)
            self.masks_panel.save(mode.splice)
        self._emit_changed()
