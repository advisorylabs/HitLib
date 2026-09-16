"""Finding a PROS or VEXcode project, and writing an export into one.

The pure half (locating a project, writing the header) is exercised directly.
The GUI half drives MainWindow the way test_export.py does, with QMessageBox
and QFileDialog stubbed - the real ones call exec() and would hang a headless
run waiting for a click. QSettings is redirected at a temp ini file so a test
run never touches whatever project you last deployed to.
"""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QSettings, QUrl
from PySide6.QtWidgets import QFileDialog, QMessageBox

from pattern_studio import deploy
from pattern_studio.main_window import MainWindow
from pattern_studio.platforms import Platform


@pytest.fixture
def project(tmp_path) -> Path:
    """A directory that looks enough like a PROS project to deploy into."""
    root = tmp_path / "TeamProject"
    (root / "include" / "hitlib").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "project.pros").write_text('{"target": "v5"}', encoding="utf-8")
    (root / "include" / "hitlib" / "hitapi.hpp").write_text("#pragma once\n", encoding="utf-8")
    return root


def _with_hitlib(root: Path) -> Path:
    """HitLib as the VEXcode download installs it: headers named .h."""
    (root / "include" / "hitlib").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "include" / "hitlib" / "hitapi.h").write_text("#pragma once\n", encoding="utf-8")
    return root


#: A VEXcode Pro V5 project file, as VEXcode writes it: compact JSON, its
#: files listed before its directories.
_V5CODE = (
    '{"title":"ProV5Robot","description":"Empty V5 C++ Project","icon":"USER921x.bmp",'
    '"version":"23.09.1216","sdk":"20220726_10_00_00","language":"cpp","competition":false,'
    '"files":[{"name":"include/robot-config.h","type":"File","specialType":"device_config"},'
    '{"name":"include/vex.h","type":"File","specialType":""},'
    '{"name":"makefile","type":"File","specialType":""},'
    '{"name":"src/main.cpp","type":"File","specialType":""},'
    '{"name":"include","type":"Directory"},{"name":"src","type":"Directory"}],'
    '"device":{"slot":1,"uid":"276-4810","options":{}},"isVexFileImport":false,"robotconfig":[]}'
)


@pytest.fixture
def vexcode_project(tmp_path) -> Path:
    """A VEXcode Pro V5 project: a .v5code file at the root."""
    root = _with_hitlib(tmp_path / "ProV5Robot")
    (root / "ProV5Robot.v5code").write_text(_V5CODE, encoding="utf-8")
    return root


@pytest.fixture
def vscode_project(tmp_path) -> Path:
    """A project from the VEX VS Code extension."""
    root = _with_hitlib(tmp_path / "VsCodeRobot")
    (root / ".vscode").mkdir()
    (root / ".vscode" / "vex_project_settings.json").write_text(
        '{"project": {"name": "VsCodeRobot", "platform": "V5", "language": "cpp"}}',
        encoding="utf-8",
    )
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


def test_each_kind_of_project_is_recognised_as_its_platform(project, vexcode_project, vscode_project):
    assert deploy.open_project(project).platform is Platform.PROS
    assert deploy.open_project(vexcode_project).platform is Platform.VEXCODE
    assert deploy.open_project(vscode_project / "src").platform is Platform.VEXCODE


@pytest.mark.parametrize(
    "platform, language",
    [("IQ2", "cpp"), ("EXP", "cpp"), ("V5", "python")],
)
def test_a_vs_code_project_hitlib_cannot_build_in_is_not_offered(vscode_project, platform, language):
    (vscode_project / ".vscode" / "vex_project_settings.json").write_text(
        f'{{"project": {{"platform": "{platform}", "language": "{language}"}}}}', encoding="utf-8"
    )
    assert deploy.open_project(vscode_project) is None


def test_an_unreadable_vs_code_settings_file_still_counts_as_a_project(vscode_project):
    (vscode_project / ".vscode" / "vex_project_settings.json").write_text("{ not json", encoding="utf-8")
    assert deploy.open_project(vscode_project).platform is Platform.VEXCODE


def test_the_install_hint_matches_the_project(project, vexcode_project):
    assert any("pros c apply" in line for line in deploy.open_project(project).install_hint_lines)
    vex_hint = " ".join(deploy.open_project(vexcode_project).install_hint_lines)
    assert "hitlib-vexcode" in vex_hint and "pros" not in vex_hint


def test_hitlib_has_to_actually_be_installed(project):
    assert deploy.open_project(project).has_hitlib

    (project / "include" / "hitlib" / "hitapi.hpp").unlink()
    assert not deploy.open_project(project).has_hitlib


def test_a_vexcode_project_needs_the_vexcode_download_not_the_pros_headers(vexcode_project):
    # The export includes hitlib/*.h, so .hpp sources copied from the repo
    # would leave it failing on missing includes.
    assert deploy.open_project(vexcode_project).has_hitlib

    (vexcode_project / "include" / "hitlib" / "hitapi.h").rename(
        vexcode_project / "include" / "hitlib" / "hitapi.hpp"
    )
    assert not deploy.open_project(vexcode_project).has_hitlib


def test_deploying_writes_the_header_where_the_compiler_looks(project):
    written = deploy.open_project(project).deploy("hitlib_studio.hpp", "// code\n")

    assert written == project / "include" / "hitlib_studio.hpp"
    assert written.read_text(encoding="utf-8") == "// code\n"


def test_deploying_writes_exactly_the_generated_text(project):
    # Not Windows' CRLF: the same design has to deploy the same bytes on every
    # platform.
    written = deploy.open_project(project).deploy("hitlib_studio.hpp", "// one\n// two\n")

    assert written.read_bytes() == b"// one\n// two\n"


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


def test_deploy_and_export_all_write_the_same_bytes(qapp, project, isolated_settings, tmp_path, monkeypatch):
    # Two routes to the same header. Saved under the same name they must be
    # identical files, line endings included, or a team comparing them sees a
    # whole-file diff that isn't a change.
    win = MainWindow()
    win._set_project(deploy.open_project(project))
    win._deploy()
    win._deploy_dialog.close()

    exported = tmp_path / "exported" / "hitlib_studio.hpp"
    exported.parent.mkdir()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(exported), ""))
    win._export_all_save()

    deployed = (project / "include" / "hitlib_studio.hpp").read_bytes()
    assert b"\r\n" not in deployed
    assert deployed == exported.read_bytes()


def test_deploying_into_a_vexcode_project_writes_a_vexcode_export(
    qapp, vexcode_project, isolated_settings
):
    win = MainWindow()
    win._set_project(deploy.open_project(vexcode_project))

    win._deploy()

    body = (vexcode_project / "include" / "hitlib_studio.h").read_text(encoding="utf-8")
    assert "namespace hitlib { namespace studio {" in body
    assert "static LedStrand& strand = strand_<>::value;" in body
    paste = win._deploy_dialog.paste_box.toPlainText()
    assert '#include "hitlib_studio.h"' in paste
    assert "void pre_auton() {" in paste
    win._deploy_dialog.close()


# VEXcode Pro V5 lists only .h headers in its file tree, and only the files its
# .v5code names (plus new ones it sees appear while open).


def test_a_vexcode_project_gets_a_dot_h_header_and_a_pros_one_keeps_hpp(project, vexcode_project, vscode_project):
    assert deploy.open_project(project).studio_header_name == "hitlib_studio.hpp"
    assert deploy.open_project(vexcode_project).studio_header_name == "hitlib_studio.h"
    assert deploy.open_project(vscode_project).studio_header_name == "hitlib_studio.h"


def _v5code(root: Path) -> dict:
    return json.loads(next(root.glob("*.v5code")).read_text(encoding="utf-8"))


def test_deploying_lists_the_header_in_the_v5code_once(vexcode_project):
    target = deploy.open_project(vexcode_project)
    target.deploy("hitlib_studio.h", "// first\n")
    target.deploy("hitlib_studio.h", "// second\n")

    names = [f["name"] for f in _v5code(vexcode_project)["files"]]
    assert names.count("include/hitlib_studio.h") == 1
    # With the other files, ahead of the directories, as VEXcode orders them.
    assert names.index("include/hitlib_studio.h") < names.index("include")


def test_listing_the_header_leaves_the_rest_of_the_v5code_as_it_was(vexcode_project):
    deploy.open_project(vexcode_project).deploy("hitlib_studio.h", "// code\n")

    written = next(vexcode_project.glob("*.v5code")).read_text(encoding="utf-8")
    entry = '{"name":"include/hitlib_studio.h","type":"File","specialType":""},'
    # Byte-for-byte the original with one entry inserted, still compact.
    assert written == _V5CODE.replace('{"name":"include","type":"Directory"}', entry + '{"name":"include","type":"Directory"}')


def test_a_v5code_without_an_include_folder_entry_gains_one(vexcode_project):
    solution = next(vexcode_project.glob("*.v5code"))
    solution.write_text('{"title":"Bare","files":[{"name":"makefile","type":"File","specialType":""}]}', encoding="utf-8")

    deploy.open_project(vexcode_project).deploy("hitlib_studio.h", "// code\n")

    assert _v5code(vexcode_project)["files"] == [
        {"name": "makefile", "type": "File", "specialType": ""},
        {"name": "include/hitlib_studio.h", "type": "File", "specialType": ""},
        {"name": "include", "type": "Directory"},
    ]


def test_an_unreadable_v5code_is_not_rewritten(vexcode_project):
    solution = next(vexcode_project.glob("*.v5code"))
    solution.write_text("{ damaged", encoding="utf-8")

    written = deploy.open_project(vexcode_project).deploy("hitlib_studio.h", "// code\n")

    assert written.is_file()
    assert solution.read_text(encoding="utf-8") == "{ damaged"


def test_a_vs_code_project_has_no_file_list_to_add_to(vscode_project):
    deploy.open_project(vscode_project).deploy("hitlib_studio.h", "// code\n")
    assert not list(vscode_project.glob("*.v5code"))


def test_a_pros_project_is_never_given_a_v5code_entry(project):
    (project / "Stray.v5code").write_text('{"files":[]}', encoding="utf-8")
    deploy.open_project(project).deploy("hitlib_studio.hpp", "// code\n")
    assert _v5code(project)["files"] == []


def test_picking_a_project_points_exports_at_its_platform(
    qapp, project, vexcode_project, isolated_settings
):
    win = MainWindow()
    win._set_project(deploy.open_project(vexcode_project))
    assert win._platform is Platform.VEXCODE
    assert win._platform_actions[Platform.VEXCODE].isChecked()

    win._set_project(deploy.open_project(project))
    assert win._platform is Platform.PROS
    assert win._platform_actions[Platform.PROS].isChecked()


def test_the_export_platform_is_remembered_without_a_project(qapp, isolated_settings):
    win = MainWindow()
    win._project = None
    win._platform_actions[Platform.VEXCODE].trigger()

    again = MainWindow()
    again._project = None
    assert again._remembered_platform() is Platform.VEXCODE


def test_deploy_uses_the_projects_platform_even_if_exports_point_elsewhere(
    qapp, vexcode_project, isolated_settings
):
    # The menu choice is for files headed who-knows-where. A project says
    # exactly what it builds with.
    win = MainWindow()
    win._set_project(deploy.open_project(vexcode_project))
    win._set_platform(Platform.PROS)

    win._deploy()

    body = (vexcode_project / "include" / "hitlib_studio.h").read_text(encoding="utf-8")
    assert "namespace hitlib { namespace studio {" in body
    win._deploy_dialog.close()


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
    assert "Robot Project" in win._deploy_action.text()

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
