"""Finding a PROS project, and writing an export into one.

The pure half (locating a project, writing the header) is exercised directly.
The GUI half drives MainWindow the way test_export.py does, with QMessageBox
and QFileDialog stubbed - the real ones call exec() and would hang a headless
run waiting for a click. QSettings is redirected at a temp ini file so a test
run never touches whatever project you last deployed to.
"""

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QSettings, QUrl
from PySide6.QtWidgets import QFileDialog, QMessageBox

from pattern_studio import deploy
from pattern_studio.main_window import MainWindow


@pytest.fixture
def project(tmp_path) -> Path:
    """A directory that looks enough like a PROS project to deploy into."""
    root = tmp_path / "TeamProject"
    (root / "include" / "hitlib").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "project.pros").write_text('{"target": "v5"}', encoding="utf-8")
    (root / "include" / "hitlib" / "hitapi.hpp").write_text("#pragma once\n", encoding="utf-8")
    return root


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Point the remembered-project storage at a temp file."""
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        MainWindow, "_settings", lambda self: QSettings(str(ini), QSettings.IniFormat)
    )
    return ini


# ============================================================================
# Locating a project
# ============================================================================


def test_a_project_is_found_from_anything_inside_it(project):
    # All three are things a user would plausibly drag over from a file
    # manager or an editor tab.
    for dropped in (project, project / "project.pros", project / "src"):
        assert deploy.open_project(dropped).root == project


def test_a_directory_that_is_not_a_project_is_not_one(tmp_path):
    plain = tmp_path / "just_a_folder"
    plain.mkdir()
    assert deploy.open_project(plain) is None


def test_the_search_upward_gives_up_rather_than_reaching_the_drive_root(project):
    deep = project / "a" / "b" / "c" / "d" / "e" / "f" / "g"
    deep.mkdir(parents=True)
    assert deploy.open_project(deep) is None


def test_hitlib_has_to_actually_be_installed(project):
    assert deploy.open_project(project).has_hitlib

    (project / "include" / "hitlib" / "hitapi.hpp").unlink()
    assert not deploy.open_project(project).has_hitlib


def test_deploying_writes_the_header_where_the_compiler_looks(project):
    written = deploy.open_project(project).deploy("hitlib_studio.hpp", "// code\n")

    assert written == project / "include" / "hitlib_studio.hpp"
    assert written.read_text(encoding="utf-8") == "// code\n"


def test_deploying_again_overwrites_rather_than_piling_up(project):
    target = deploy.open_project(project)
    target.deploy("hitlib_studio.hpp", "// first\n")
    target.deploy("hitlib_studio.hpp", "// second\n")

    headers = sorted(p.name for p in (project / "include").glob("*.hpp"))
    assert headers == ["hitlib_studio.hpp"]
    assert (project / "include" / "hitlib_studio.hpp").read_text(encoding="utf-8") == "// second\n"


def test_a_missing_include_directory_is_created(tmp_path):
    root = tmp_path / "Bare"
    root.mkdir()
    (root / "project.pros").write_text("{}", encoding="utf-8")

    written = deploy.open_project(root).deploy("hitlib_studio.hpp", "// code\n")

    assert written.is_file()


# ============================================================================
# The Deploy action
# ============================================================================


def test_deploy_writes_the_design_into_the_project(qapp, project, isolated_settings):
    win = MainWindow()
    win._set_project(deploy.open_project(project))

    win._deploy()

    header = project / "include" / "hitlib_studio.hpp"
    assert header.is_file()
    body = header.read_text(encoding="utf-8")
    assert "namespace hitlib::studio {" in body
    assert "inline LedStrand strand{adiPort, length, refreshMs};" in body


def test_the_first_deploy_hands_over_the_lines_to_paste(qapp, project, isolated_settings):
    win = MainWindow()
    win._set_project(deploy.open_project(project))

    win._deploy()

    paste = win._deploy_dialog.paste_box.toPlainText()
    assert 'include "hitlib_studio.hpp"' in paste
    assert "hitlib::studio::begin();" in paste
    win._deploy_dialog.close()


def test_redeploying_does_not_ask_again(qapp, project, isolated_settings, monkeypatch):
    # The dialog is the instructions. Once the header is there a deploy is a
    # refresh of a file the project already includes, and saying so every time
    # is how a dialog gets dismissed unread.
    told = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **k: told.append(a) or QMessageBox.Ok
    )
    win = MainWindow()
    win._set_project(deploy.open_project(project))

    win._deploy()
    win._deploy_dialog.close()
    win._deploy_dialog = None
    win._deploy()

    assert win._deploy_dialog is None
    assert len(told) == 1


def test_the_design_name_does_not_decide_the_filename(qapp, project, isolated_settings, monkeypatch):
    # A design-derived filename would leave the previous header in place on a
    # rename, still included by main.cpp.
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    win = MainWindow()
    win._set_project(deploy.open_project(project))
    win._deploy()
    win._deploy_dialog.close()

    win.inspector.strand_panel.name_edit.setText("Renamed Entirely")
    qapp.processEvents()
    win._deploy()

    headers = sorted(p.name for p in (project / "include").glob("*.hpp"))
    assert headers == ["hitlib_studio.hpp"]


def test_deploying_with_no_project_asks_for_one(qapp, project, isolated_settings, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(project))
    win = MainWindow()
    win._project = None

    win._deploy()

    assert win._project.root == project
    assert (project / "include" / "hitlib_studio.hpp").is_file()
    win._deploy_dialog.close()


def test_picking_something_that_is_not_a_project_warns_and_changes_nothing(
    qapp, tmp_path, isolated_settings, monkeypatch
):
    plain = tmp_path / "not_a_project"
    plain.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(plain))
    warned = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warned.append(a) or QMessageBox.Ok
    )
    win = MainWindow()
    win._project = None

    assert win._choose_project() is None
    assert win._project is None
    assert len(warned) == 1


def test_the_menu_item_names_where_it_will_write(qapp, project, isolated_settings):
    win = MainWindow()
    win._project = None
    win._refresh_deploy_action()
    assert "PROS Project" in win._deploy_action.text()

    win._set_project(deploy.open_project(project))
    assert project.name in win._deploy_action.text()


def test_the_project_is_remembered_for_next_time(qapp, project, isolated_settings):
    MainWindow()._set_project(deploy.open_project(project))

    assert MainWindow()._project.root == project


def test_a_remembered_project_that_has_gone_away_is_forgotten(
    qapp, project, isolated_settings, monkeypatch
):
    MainWindow()._set_project(deploy.open_project(project))
    monkeypatch.setattr(Path, "is_dir", lambda self: False)

    assert MainWindow()._project is None


# ============================================================================
# Dropping a project on the window
# ============================================================================


def _urls(*paths) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return mime


def test_dropping_a_project_makes_it_the_target(qapp, project, isolated_settings):
    win = MainWindow()
    win._project = None

    assert win._dropped_project(_urls(project)) is not None
    assert win._dropped_project(_urls(project / "src")) is not None


def test_dropping_something_else_is_refused(qapp, tmp_path, isolated_settings):
    stray = tmp_path / "holiday.jpg"
    stray.write_text("", encoding="utf-8")
    win = MainWindow()

    assert win._dropped_project(_urls(stray)) is None
    assert win._dropped_project(QMimeData()) is None


def test_a_dropped_project_is_remembered_like_a_chosen_one(qapp, project, isolated_settings):
    win = MainWindow()
    win._project = None

    win._set_project(win._dropped_project(_urls(project)))

    assert MainWindow()._project.root == project
