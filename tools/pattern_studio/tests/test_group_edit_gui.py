"""Group editing end to end: multi-select the strand list, edit once, and every
selected strand follows, without the ones that differ getting flattened.
"""

from pattern_studio.main_window import MainWindow
from pattern_studio.models import AnimationKind


def _window_with(qapp, count: int) -> MainWindow:
    win = MainWindow()
    for _ in range(count - 1):
        win.add_strand()
    return win


def _select(win: MainWindow, rows: list[int]) -> None:
    """Select `rows`, anchored on the first. The same state Ctrl-clicking down
    the list produces."""
    win.strand_list.select(rows[0])
    for row in rows[1:]:
        win.strand_list.list_widget.item(row).setSelected(True)


def test_selecting_several_strands_reports_the_group(qapp):
    win = _window_with(qapp, 3)
    _select(win, [0, 1, 2])

    assert win._selected_indices == [0, 1, 2]
    assert win._current_index == 0
    # isHidden() rather than isVisible(): the window is never shown headless.
    assert not win.inspector.group_banner.isHidden()
    assert "Group edit: 3 strands" in win.inspector.group_banner.text()
    assert win.strand_list.group_label.text() == "3 selected"

    win.strand_list.select(1)
    assert win._selected_indices == [1]
    assert win.inspector.group_banner.isHidden()


def test_strand_setting_edit_applies_to_every_selected_strand(qapp):
    win = _window_with(qapp, 3)
    _select(win, [0, 1, 2])

    win.inspector.strand_panel.brightness_spin.setValue(42)
    qapp.processEvents()

    assert [s.config.brightness for s in win.sessions] == [42, 42, 42]
    assert [s.strand.brightness_pct for s in win.sessions] == [42, 42, 42]


def test_animation_edit_applies_to_every_selected_strand(qapp):
    win = _window_with(qapp, 3)
    _select(win, [0, 1, 2])

    idx = win.inspector.anim_panel.kind_combo.findData(AnimationKind.TWINKLE)
    win.inspector.anim_panel.kind_combo.setCurrentIndex(idx)
    qapp.processEvents()

    assert all(s.config.animation.kind == AnimationKind.TWINKLE for s in win.sessions)
    assert all(s.strand.anim_mode.name == "TWINKLE" for s in win.sessions)


def test_unedited_fields_stay_per_strand(qapp):
    win = _window_with(qapp, 2)
    # Strand 2 deliberately differs in a field the group edit won't touch.
    win.sessions[1].config.animation.speed = 9

    _select(win, [0, 1])
    win.inspector.anim_panel.color_btn.set_color(0x00FF00)
    win.inspector.anim_panel.color_btn.color_changed.emit(0x00FF00)
    qapp.processEvents()

    assert [s.config.animation.color for s in win.sessions] == [0x00FF00, 0x00FF00]
    assert win.sessions[1].config.animation.speed == 9


def test_name_and_adi_port_stay_per_strand(qapp):
    win = _window_with(qapp, 3)
    names_before = [s.config.name for s in win.sessions[1:]]
    ports_before = [s.config.adi_port for s in win.sessions[1:]]

    _select(win, [0, 1, 2])
    win.inspector.strand_panel.name_edit.setText("Bumper")
    win.inspector.strand_panel.adi_port_spin.setValue(7)
    qapp.processEvents()

    assert win.sessions[0].config.name == "Bumper"
    assert win.sessions[0].config.adi_port == 7
    assert [s.config.name for s in win.sessions[1:]] == names_before
    assert [s.config.adi_port for s in win.sessions[1:]] == ports_before


def test_edit_leaves_unselected_strands_untouched(qapp):
    win = _window_with(qapp, 3)
    _select(win, [0, 1])

    win.inspector.strand_panel.length_spin.setValue(11)
    qapp.processEvents()

    assert [s.config.length for s in win.sessions] == [11, 11, 30]


def test_profile_modes_propagate_to_the_group(qapp):
    win = _window_with(qapp, 2)
    _select(win, [0, 1])

    win.inspector.use_profile_check.setChecked(True)
    win.inspector.modes_panel.add_mode_btn.click()
    qapp.processEvents()

    assert all(s.config.use_profile for s in win.sessions)
    assert [len(s.config.profile_modes) for s in win.sessions] == [1, 1]
    # Deep-copied, not shared, so editing one strand's mode later can't bleed.
    assert win.sessions[0].config.profile_modes[0] is not win.sessions[1].config.profile_modes[0]


def test_remove_drops_the_whole_selection(qapp):
    win = _window_with(qapp, 4)
    _select(win, [1, 2])

    win.remove_selected_strands()

    assert len(win.sessions) == 2
    assert [s.config.name for s in win.sessions] == ["Strand 1", "Strand 4"]


def test_selected_transport_covers_the_group(qapp):
    win = _window_with(qapp, 3)
    _select(win, [0, 1])

    win._pause_selected()
    assert [s.running for s in win.sessions] == [False, False, True]

    win._play_selected()
    assert all(s.running for s in win.sessions)
