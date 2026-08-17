"""End-to-end checks for the Phase 3 mode/profile editor, driven through the
actual widgets (not by mutating config directly) so the signal wiring itself
is what's under test.
"""

from PySide6.QtCore import Qt

from pattern_studio.main_window import MainWindow
from pattern_studio.models import AnimationKind


def test_enabling_profile_mode_switches_panels(qapp):
    win = MainWindow()
    win.show()  # isVisible() reflects the whole ancestor chain, not just setVisible() on the widget itself
    inspector = win.inspector

    assert inspector.anim_panel.isVisible()
    assert not inspector.modes_panel.isVisible()

    inspector.use_profile_check.setChecked(True)
    qapp.processEvents()

    assert not inspector.anim_panel.isVisible()
    assert inspector.modes_panel.isVisible()
    assert win.sessions[0].config.use_profile is True
    assert win.sessions[0].strand.active_profile is not None


def test_add_two_modes_and_priority_resolution_via_checkboxes(qapp):
    win = MainWindow()
    inspector = win.inspector
    modes = inspector.modes_panel

    inspector.use_profile_check.setChecked(True)
    qapp.processEvents()

    # First mode: low-priority solid blue.
    modes._add_mode()
    qapp.processEvents()
    modes.name_edit.setText("Idle")
    modes.priority_spin.setValue(10)
    idx = modes.anim_panel.kind_combo.findData(AnimationKind.SOLID)
    modes.anim_panel.kind_combo.setCurrentIndex(idx)
    modes.anim_panel.color_btn.set_color(0x0000FF)
    modes.anim_panel.color_btn.color_changed.emit(0x0000FF)
    qapp.processEvents()

    # Second mode: high-priority solid red.
    modes._add_mode()
    qapp.processEvents()
    modes.name_edit.setText("Alert")
    modes.priority_spin.setValue(90)
    idx = modes.anim_panel.kind_combo.findData(AnimationKind.SOLID)
    modes.anim_panel.kind_combo.setCurrentIndex(idx)
    modes.anim_panel.color_btn.set_color(0xFF0000)
    modes.anim_panel.color_btn.color_changed.emit(0xFF0000)
    qapp.processEvents()

    cfg = win.sessions[0].config
    assert [m.name for m in cfg.profile_modes] == ["Idle", "Alert"]
    assert cfg.profile_modes[0].priority == 10
    assert cfg.profile_modes[1].priority == 90

    # Activate only Idle -> strand should show blue.
    modes.mode_list.item(0).setCheckState(Qt.Checked)
    qapp.processEvents()
    strand = win.sessions[0].strand
    strand.tick()
    assert strand.pixels[0] == 0x0000FF

    # Also activate Alert (higher priority) -> should now win.
    modes.mode_list.item(1).setCheckState(Qt.Checked)
    qapp.processEvents()
    strand.tick()
    assert strand.pixels[0] == 0xFF0000


def test_sequenced_mode_gets_default_phase_and_editor(qapp):
    win = MainWindow()
    win.show()
    inspector = win.inspector
    modes = inspector.modes_panel

    inspector.use_profile_check.setChecked(True)
    modes._add_mode()
    qapp.processEvents()

    modes.sequenced_check.setChecked(True)
    qapp.processEvents()

    mode = win.sessions[0].config.profile_modes[0]
    assert len(mode.phases) == 1
    assert modes.phase_container.isVisible()
    assert modes.phase_list.count() == 1


def test_remove_mode_cleans_up_active_indices(qapp):
    win = MainWindow()
    inspector = win.inspector
    modes = inspector.modes_panel

    inspector.use_profile_check.setChecked(True)
    modes._add_mode()
    modes._add_mode()
    qapp.processEvents()

    cfg = win.sessions[0].config
    cfg.active_mode_indices = [0, 1]

    modes.mode_list.setCurrentRow(0)
    modes._remove_mode()
    qapp.processEvents()

    assert len(cfg.profile_modes) == 1
    assert cfg.active_mode_indices == [0]  # former index 1 shifted down to 0
