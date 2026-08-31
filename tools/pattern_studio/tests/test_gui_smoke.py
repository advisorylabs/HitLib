"""End-to-end wiring checks for the GUI shell: selection, edits propagating to
the engine, add/remove, play/pause. Runs headless via the offscreen Qt
platform.
"""

from pattern_studio.main_window import MainWindow
from pattern_studio.models import AnimationKind


def test_starts_with_one_strand(qapp):
    win = MainWindow()
    assert len(win.sessions) == 1
    assert win._current_index == 0


def test_add_and_select_tracks_newest(qapp):
    win = MainWindow()
    win.add_strand()
    win.add_strand()
    assert len(win.sessions) == 3
    assert win._current_index == 2


def test_animation_kind_edit_propagates_to_engine(qapp):
    win = MainWindow()
    win.strand_list.select(0)

    idx = win.inspector.anim_panel.kind_combo.findData(AnimationKind.TWINKLE)
    win.inspector.anim_panel.kind_combo.setCurrentIndex(idx)
    qapp.processEvents()

    assert win.sessions[0].config.animation.kind == AnimationKind.TWINKLE
    assert win.sessions[0].strand.anim_mode.name == "TWINKLE"


def test_strand_setting_edit_rebuilds_engine_strand(qapp):
    win = MainWindow()
    win.inspector.strand_panel.length_spin.setValue(12)
    qapp.processEvents()

    assert win.sessions[0].config.length == 12
    assert win.sessions[0].strand.length == 12


def test_remove_strand_keeps_selection_consistent(qapp):
    win = MainWindow()
    win.add_strand()
    win.add_strand()

    win.strand_list.select(1)
    win.remove_strand(1)

    assert len(win.sessions) == 2
    assert win._current_index == 1


def test_play_pause_all_affects_every_session(qapp):
    win = MainWindow()
    win.add_strand()
    win.add_strand()

    win._pause_all()
    assert win._running is False
    assert all(not s.running for s in win.sessions)

    win._play_all()
    assert win._running is True
    assert all(s.running for s in win.sessions)


def test_play_pause_selected_only_affects_current_strand(qapp):
    win = MainWindow()
    win.add_strand()
    win.strand_list.select(0)

    win._pause_selected()
    assert win.sessions[0].running is False
    assert win.sessions[1].running is True  # untouched

    win._play_selected()
    assert win.sessions[0].running is True


def test_reset_all_and_reset_selected_do_not_raise(qapp):
    win = MainWindow()
    win.add_strand()
    win._reset_selected()
    win._reset_all()


def test_reset_blanks_the_strand_rather_than_rewinding_it(qapp):
    win = MainWindow()
    session = win.sessions[0]
    session.config.animation.kind = AnimationKind.SOLID
    session.config.animation.color = 0x00FF00
    session.rebuild()
    session.strand.tick()
    assert any(session.strand.pixels)

    win._reset_all()
    assert session.strand.pixels == [0x000000] * session.config.length
    assert not session.running
    assert win._running is False

    # ...and the animation is still there to start over from.
    win._play_all()
    session.strand.tick()
    assert session.strand.pixels == [0x00FF00] * session.config.length


def test_reset_selected_leaves_other_strands_running(qapp):
    win = MainWindow()
    win.add_strand()
    win.strand_list.select(0)

    win._reset_selected()
    assert win.sessions[0].running is False
    assert win.sessions[1].running is True
    assert win._running is True  # only the "All" actions move the default


def test_selected_transport_buttons_disabled_without_a_strand(qapp):
    win = MainWindow()
    win.remove_strand(0)
    assert not win.play_selected_btn.isEnabled()
    assert not win.pause_selected_btn.isEnabled()
    assert not win.reset_selected_btn.isEnabled()

    win.add_strand()
    assert win.play_selected_btn.isEnabled()
    assert win.pause_selected_btn.isEnabled()
    assert win.reset_selected_btn.isEnabled()
