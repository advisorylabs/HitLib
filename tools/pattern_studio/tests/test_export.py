"""GUI-level checks for the Export menu wiring.

_generate_export_or_warn() always calls inspector.save(config) first to
flush in-progress edits, so tests must drive the real widgets (as a user
would) rather than mutate session.config directly. A direct mutation would
just get overwritten by whatever the (unrelated, still-default) widgets
currently hold.

QMessageBox.critical/warning are stubbed out wherever a code path can reach
them. The real implementation calls exec() and shows a modal dialog, which
would hang a headless test run waiting for a click that will never come.
"""

from PySide6.QtWidgets import QFileDialog, QMessageBox

from pattern_studio.main_window import MainWindow
from pattern_studio.models import AnimationKind


def test_generates_code_for_current_strand(qapp):
    win = MainWindow()
    idx = win.inspector.anim_panel.kind_combo.findData(AnimationKind.RAINBOW)
    win.inspector.anim_panel.kind_combo.setCurrentIndex(idx)
    win.inspector.anim_panel.speed_spin.setValue(3)
    qapp.processEvents()

    code = win._generate_export_or_warn()

    assert code is not None
    assert "s.rainbow(3);" in code
    assert "namespace hitlib::profiles {" in code


def test_duplicate_mode_names_block_export_and_warn(qapp, monkeypatch):
    win = MainWindow()
    win.inspector.use_profile_check.setChecked(True)
    modes = win.inspector.modes_panel
    modes._add_mode()
    modes._add_mode()
    qapp.processEvents()
    modes.mode_list.setCurrentRow(1)
    modes.name_edit.setText("Mode 1")  # collides with the first mode's default name
    qapp.processEvents()

    warned = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: warned.append(a) or QMessageBox.Ok)

    code = win._generate_export_or_warn()

    assert code is None
    assert len(warned) == 1


def test_no_selection_blocks_export_and_warns(qapp, monkeypatch):
    win = MainWindow()
    win.remove_strand(0)
    assert win._current_session() is None

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a) or QMessageBox.Ok)

    code = win._generate_export_or_warn()

    assert code is None
    assert len(warned) == 1


def test_export_save_writes_file(qapp, monkeypatch, tmp_path):
    win = MainWindow()
    idx = win.inspector.anim_panel.kind_combo.findData(AnimationKind.SOLID)
    win.inspector.anim_panel.kind_combo.setCurrentIndex(idx)
    win.inspector.anim_panel.color_btn.set_color(0x123456)
    win.inspector.anim_panel.color_btn.color_changed.emit(0x123456)
    qapp.processEvents()

    out_path = tmp_path / "exported.hpp"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out_path), ""))

    win._export_save()

    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "s.setColor(0x123456);" in text


def test_export_save_names_the_header_after_the_chosen_filename(qapp, monkeypatch, tmp_path):
    # The generated banner tells the user what to #include, so it has to match
    # whatever they actually saved the file as.
    win = MainWindow()
    out_path = tmp_path / "robot_lights.hpp"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out_path), ""))

    win._export_save()

    assert '#include "robot_lights.hpp"' in out_path.read_text(encoding="utf-8")


def test_export_save_suggests_a_filename_from_the_strand_name(qapp, monkeypatch, tmp_path):
    win = MainWindow()
    win.inspector.strand_panel.name_edit.setText("My Robot")
    qapp.processEvents()

    suggested = []
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda parent, title, start, filt: suggested.append(start) or ("", ""),
    )

    win._export_save()

    assert suggested == ["my_robot.hpp"]


def test_export_all_writes_every_strand_into_one_file(qapp, monkeypatch, tmp_path):
    win = MainWindow()
    win.inspector.strand_panel.name_edit.setText("Left")
    qapp.processEvents()
    win.add_strand()
    win.strand_list.list_widget.setCurrentRow(1)
    win.inspector.strand_panel.name_edit.setText("Right")
    qapp.processEvents()

    out_path = tmp_path / "led_profiles.hpp"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out_path), ""))

    win._export_all_save()

    text = out_path.read_text(encoding="utf-8")
    assert "namespace left {" in text
    assert "namespace right {" in text


def test_export_all_blocks_on_duplicate_strand_names(qapp, monkeypatch):
    # Two strands with the same name would generate `strand` and `strand2`
    # with nothing to say which is which.
    win = MainWindow()
    win.add_strand()
    win.strand_list.list_widget.setCurrentRow(1)
    win.inspector.strand_panel.name_edit.setText("Strand 1")  # collides with the first strand
    qapp.processEvents()

    warned = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: warned.append(a) or QMessageBox.Ok)

    assert win._all_configs_or_warn() is None
    assert len(warned) == 1


def test_export_all_flushes_in_progress_edits_on_the_selected_strand(qapp):
    win = MainWindow()
    idx = win.inspector.anim_panel.kind_combo.findData(AnimationKind.RAINBOW)
    win.inspector.anim_panel.kind_combo.setCurrentIndex(idx)
    win.inspector.anim_panel.speed_spin.setValue(4)
    qapp.processEvents()

    configs = win._all_configs_or_warn()

    assert configs is not None
    assert configs[0].animation.speed == 4
