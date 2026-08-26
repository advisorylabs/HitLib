"""End-to-end checks for the Fill animation's editor, driven through the actual
widgets (not by mutating config directly).
"""

from pattern_studio import fill_sources
from pattern_studio.main_window import MainWindow
from pattern_studio.models import AnimationKind


def _fill_panel(win: MainWindow):
    panel = win.inspector.anim_panel
    idx = panel.kind_combo.findData(AnimationKind.FILL)
    panel.kind_combo.setCurrentIndex(idx)
    return panel


def _pick_source(panel, source_id: str) -> None:
    panel.source_combo.setCurrentIndex(panel.source_combo.findData(source_id))


def test_picking_a_source_fills_in_a_range_that_already_works(qapp):
    win = MainWindow()
    win.show()
    panel = _fill_panel(win)

    _pick_source(panel, "motor_temp")
    qapp.processEvents()

    assert panel.source_empty_spin.value() == 20
    assert panel.source_full_spin.value() == 70
    # And the fields say what the numbers mean, in the source's own units.
    assert "C" in panel.source_full_spin.suffix()


def test_a_turning_source_arrives_with_wrap_already_on(qapp):
    win = MainWindow()
    win.show()
    panel = _fill_panel(win)

    _pick_source(panel, "rotation")
    qapp.processEvents()

    assert panel.source_wrap_check.isChecked()
    assert panel.source_full_spin.value() == 36000


def test_the_port_field_only_shows_for_sources_that_read_one(qapp):
    win = MainWindow()
    win.show()
    panel = _fill_panel(win)

    _pick_source(panel, "motor_temp")
    qapp.processEvents()
    assert panel.source_port_spin.isVisible()
    assert panel.source_port_spin.maximum() == 21

    _pick_source(panel, "potentiometer")
    qapp.processEvents()
    assert panel.source_port_spin.maximum() == 8  # ADI ports, not smart ones

    _pick_source(panel, "battery")
    qapp.processEvents()
    assert not panel.source_port_spin.isVisible()


def test_a_manual_meter_hides_the_mapping_it_does_not_use(qapp):
    # Nothing maps a reading for a Manual meter - the robot's code hands it a
    # 0-255 level directly - so the range and its options have nothing to say.
    win = MainWindow()
    win.show()
    panel = _fill_panel(win)

    _pick_source(panel, fill_sources.MANUAL)
    qapp.processEvents()

    assert not panel.source_empty_spin.isVisible()
    assert not panel.source_full_spin.isVisible()
    assert not panel.source_wrap_check.isVisible()
    assert not panel.smoothing_spin.isVisible()
    # The preview still applies: it is what lights the strip in the editor.
    assert panel.preview_sweep_check.isVisible()


def test_the_preview_level_only_shows_when_it_is_not_sweeping(qapp):
    win = MainWindow()
    win.show()
    panel = _fill_panel(win)

    panel.preview_sweep_check.setChecked(True)
    qapp.processEvents()
    assert not panel.preview_level_spin.isVisible()

    panel.preview_sweep_check.setChecked(False)
    qapp.processEvents()
    assert panel.preview_level_spin.isVisible()


def test_the_hint_names_the_port_the_meter_will_read(qapp):
    win = MainWindow()
    win.show()
    panel = _fill_panel(win)

    _pick_source(panel, "motor_temp")
    panel.source_port_spin.setValue(11)
    qapp.processEvents()

    assert "port 11" in panel.fill_hint.text()


def test_editing_the_panel_moves_the_previewed_strip(qapp):
    win = MainWindow()
    win.show()
    panel = _fill_panel(win)

    win.inspector.strand_panel.length_spin.setValue(10)
    win.inspector.strand_panel.changed.emit()
    _pick_source(panel, "battery")
    panel.preview_sweep_check.setChecked(False)
    panel.preview_level_spin.setValue(100)
    panel.color_btn.set_color(0x00FF00)
    panel.color_btn.color_changed.emit(0x00FF00)
    idx = panel.gradient_combo.findData(False)
    panel.gradient_combo.setCurrentIndex(idx)
    qapp.processEvents()

    strand = win.sessions[0].strand
    strand.tick()
    assert strand.pixels == [0x00FF00] * 10

    panel.preview_level_spin.setValue(0)
    qapp.processEvents()
    strand.tick()
    assert strand.pixels == [0x000000] * 10
