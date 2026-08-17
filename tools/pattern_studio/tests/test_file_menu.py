"""Exercises the File menu actions (New/Open/Save/Save As/Import) through
MainWindow itself, bypassing the file-picker dialogs (which need a display)
by calling the same private handlers the dialogs would call into with a
concrete path -- what's under test is the save/load/session wiring, not Qt's
native dialog widget.
"""

from pathlib import Path

from pattern_studio.main_window import MainWindow
from pattern_studio.models import AnimationKind
from pattern_studio.serialization import load_document, save_document


def test_save_as_writes_current_sessions(qapp, tmp_path):
    win = MainWindow()
    win.sessions[0].config.name = "Only"
    win.sessions[0].config.animation.kind = AnimationKind.RAINBOW

    path = tmp_path / "out.hlprofile"
    win._write_to(path)
    win._current_file_path = path

    assert path.exists()
    loaded = load_document(path)
    assert len(loaded) == 1
    assert loaded[0].name == "Only"
    assert loaded[0].animation.kind == AnimationKind.RAINBOW


def test_save_reuses_current_path_without_dialog(qapp, tmp_path):
    win = MainWindow()
    path = tmp_path / "reuse.hlprofile"
    win._current_file_path = path

    win._file_save()  # should NOT open a dialog since a path is already set

    assert path.exists()


def test_open_replaces_sessions(qapp, tmp_path):
    win = MainWindow()
    win.add_strand()
    assert len(win.sessions) == 2

    path = tmp_path / "loaded.hlprofile"
    from pattern_studio.models import StrandConfig

    save_document(path, [StrandConfig(name="A"), StrandConfig(name="B"), StrandConfig(name="C")])

    # Mirrors _file_open()'s body without the QFileDialog call.
    win._clear_sessions()
    for cfg in load_document(path):
        win._add_session(cfg)
    win._current_file_path = path
    win._update_title()

    assert [s.config.name for s in win.sessions] == ["A", "B", "C"]
    assert win.windowTitle().endswith(path.name)


def test_import_appends_without_clearing(qapp, tmp_path):
    win = MainWindow()
    original_name = win.sessions[0].config.name

    from pattern_studio.models import StrandConfig

    path = tmp_path / "extra.hlprofile"
    save_document(path, [StrandConfig(name="Imported")])

    for cfg in load_document(path):
        win._add_session(cfg)

    assert [s.config.name for s in win.sessions] == [original_name, "Imported"]


def test_new_resets_to_single_default_strand(qapp, tmp_path):
    win = MainWindow()
    win.add_strand()
    win.add_strand()
    win._current_file_path = Path("whatever.hlprofile")

    win._file_new()

    assert len(win.sessions) == 1
    assert win._current_file_path is None
    assert win.windowTitle() == "HitLib Pattern Studio"
