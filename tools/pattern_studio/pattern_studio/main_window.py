"""Top-level window: strand list (left) + live preview (center) + inspector (right)."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import __version__, deploy, theme
from .canvas import StripCanvas
from .codegen import (
    generate_cpp,
    generate_document_cpp,
    paste_block,
    suggested_header_name,
    validate_document_for_export,
    validate_for_export,
)
from .deploy_dialog import DeployDialog
from .engine import make_music_binding
from .envelope import BAND_BASS
from .group_edit import apply_changes, diff_config
from .inspector import InspectorPanel
from .models import AnimationKind, Document, MusicConfig, StrandConfig
from .music_panel import MusicPanel
from .serialization import load_document, save_document
from .session import StrandSession
from .strand_list import StrandListPanel
from .widgets import BrandRule
from . import window_chrome

_FILE_FILTER = "HitLib Pattern Studio Profile (*.hlprofile);;JSON (*.json);;All Files (*)"
_DEFAULT_SUFFIX = ".hlprofile"
_CPP_FILE_FILTER = "C++ Header (*.hpp);;All Files (*)"
#: Default filename offered by the Export All save dialog.
_DOCUMENT_HEADER_NAME = "led_profiles.hpp"

#: What Deploy always writes, whatever the design is called.
#:
#: Fixed, not derived from the design name: renaming a strand would otherwise
#: deploy under a new name and leave the previous header in place, still
#: included by main.cpp. The file also defines hitlib::studio, so a project can
#: only carry one.
_STUDIO_HEADER_NAME = "hitlib_studio.hpp"

#: Where the remembered deploy target lives. Named explicitly rather than left
#: to QApplication, so a MainWindow built outside app.main() reads the same
#: store the app does.
_SETTINGS_ORG = "AdvisoryLabs"
_SETTINGS_APP = "HitLib Pattern Studio"
_SETTINGS_PROJECT_KEY = "deploy/project_root"


def _transport_button(label: str, icon_name: str, tooltip: str) -> QPushButton:
    button = QPushButton(theme.icon(icon_name), f" {label}")
    button.setProperty("role", "transport")
    button.setIconSize(QSize(13, 13))
    button.setToolTip(tooltip)
    # Cyan, not the violet accent: cyan is the app's "this is live" color
    # (selected rows, the group outline on the canvas).
    theme.HoverBloom(button, theme.FOCUS, radius=14, alpha=105)
    return button


def _scope_label(text: str) -> QLabel:
    """The "ALL" / "SELECTED" heading over a transport triad."""
    label = QLabel(text)
    label.setProperty("role", "sectionHeader")
    return label


def _music_band(config: StrandConfig) -> str:
    """The band this strand's Music Sync animation follows.

    A profile strand has one animation per mode, so the first Music Sync among
    them stands in for the strand: the Song bar shows a single waveform.
    """
    if config.use_profile:
        for mode in config.profile_modes:
            leaves = [p.animation for p in mode.phases] if mode.phases else [mode.animation]
            for animation in leaves:
                if animation.kind == AnimationKind.MUSIC:
                    return animation.band
        return BAND_BASS
    return config.animation.band


def _v_separator() -> QFrame:
    """A themed 1px divider. QFrame.VLine draws itself from the palette's
    Mid/Dark roles, which the stylesheet can't reach."""
    line = QFrame()
    line.setObjectName("vSep")
    line.setFixedWidth(1)
    return line


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.sessions: list[StrandSession] = []
        # The anchor (what the inspector shows) plus the wider group an edit
        # is replayed onto - see _apply_group_edit.
        self._current_index = -1
        self._selected_indices: list[int] = []
        self._baseline: StrandConfig | None = None
        self._baseline_index = -1
        self._running = True
        self._current_file_path: Path | None = None
        # The PROS project Deploy writes into, remembered across runs.
        # _deploy_action is held so its label can name that destination.
        self._deploy_action = None
        self._deploy_dialog: DeployDialog | None = None
        self._project = self._remembered_project()
        self.setAcceptDrops(True)
        # The document's one song. Held here rather than on any strand: see
        # models.MusicConfig and the Song bar under the preview.
        self.music = MusicConfig()
        # Frameless, with the logo/menus/title/caption buttons folded into one
        # row. See window_chrome for what that trades away and how it's
        # paid back. Built before _update_title(), which writes into it.
        self.title_bar = window_chrome.install(self)
        self._chrome_hooked = False
        self._update_title()

        self.canvas = StripCanvas()
        self.music_panel = MusicPanel()
        self.strand_list = StrandListPanel()
        self.inspector = InspectorPanel()
        self.inspector.setEnabled(False)

        # Scope lives in a heading over each triad rather than in every label,
        # so the labels stay verbs.
        self.play_all_btn = _transport_button("Play", "play", "Play every strand")
        self.pause_all_btn = _transport_button("Pause", "pause", "Pause every strand")
        self.reset_all_btn = _transport_button(
            "Reset", "reset", "Blank every strand - playing again starts from its first frame"
        )
        self.play_selected_btn = _transport_button("Play", "play", "Play the selected strands")
        self.pause_selected_btn = _transport_button("Pause", "pause", "Pause the selected strands")
        self.reset_selected_btn = _transport_button(
            "Reset", "reset", "Blank the selected strands - playing again starts from their first frame"
        )
        self.play_selected_btn.setEnabled(False)
        self.pause_selected_btn.setEnabled(False)
        self.reset_selected_btn.setEnabled(False)

        left = QWidget()
        left.setMinimumWidth(160)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.strand_list)

        toolbar_row = QHBoxLayout()
        toolbar_row.setSpacing(6)
        toolbar_row.addWidget(_scope_label("ALL"))
        toolbar_row.addWidget(self.play_all_btn)
        toolbar_row.addWidget(self.pause_all_btn)
        toolbar_row.addWidget(self.reset_all_btn)

        toolbar_row.addSpacing(6)
        toolbar_row.addWidget(_v_separator())
        toolbar_row.addSpacing(6)

        toolbar_row.addWidget(_scope_label("SELECTED"))
        toolbar_row.addWidget(self.play_selected_btn)
        toolbar_row.addWidget(self.pause_selected_btn)
        toolbar_row.addWidget(self.reset_selected_btn)
        toolbar_row.addStretch(1)

        # Both rows have a minimum width (6 buttons; 6 fields) that can exceed
        # a narrowed center column. Wrapping them in one scroll area confines
        # the horizontal scrollbar to this strip, instead of clipping the
        # contents or stopping the center column from shrinking.
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addLayout(toolbar_row)
        controls_layout.addWidget(self.inspector.strand_panel)

        controls_scroll = QScrollArea()
        controls_scroll.setWidget(controls)
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Reserve room for the horizontal scrollbar even when hidden: the
        # height is fixed, so a bar appearing would clip the fields.
        controls_scroll.setFixedHeight(
            controls.sizeHint().height() + 4 + controls_scroll.horizontalScrollBar().sizeHint().height()
        )
        controls_scroll.setFrameShape(QScrollArea.NoFrame)

        center = QWidget()
        center.setMinimumWidth(280)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(8, 6, 8, 8)
        center_layout.setSpacing(8)
        center_layout.addWidget(controls_scroll)
        center_layout.addWidget(self.canvas, 1)
        center_layout.addWidget(self.music_panel)

        right = QScrollArea()
        right.setWidget(self.inspector)
        right.setWidgetResizable(True)
        right.setMinimumWidth(260)

        # Resizable columns: fixed-width side panels can force a horizontal
        # scrollbar in the right column with no way to widen it.
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([210, 880, 400])

        # The wordmark gradient as a hairline under the menu bar. It drifts
        # and casts a short falloff onto the window; see widgets.BrandRule.
        brand_rule = BrandRule()

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.title_bar)
        root_layout.addWidget(brand_rule)
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

        self.canvas.set_sessions(self.sessions)

        self.strand_list.add_requested.connect(self.add_strand)
        self.strand_list.remove_requested.connect(self.remove_selected_strands)
        self.strand_list.selection_changed.connect(self._on_selection_changed)
        self.inspector.strand_settings_changed.connect(self._on_strand_settings_changed)
        self.inspector.animation_changed.connect(self._on_animation_changed)
        self.music_panel.song_changed.connect(self._on_song_changed)
        self.music_panel.position_changed.connect(self._on_music_position)
        self.music_panel.playing_changed.connect(self._on_music_playing)
        self.play_all_btn.clicked.connect(self._play_all)
        self.pause_all_btn.clicked.connect(self._pause_all)
        self.reset_all_btn.clicked.connect(self._reset_all)
        self.play_selected_btn.clicked.connect(self._play_selected)
        self.pause_selected_btn.clicked.connect(self._pause_selected)
        self.reset_selected_btn.clicked.connect(self._reset_selected)

        self._build_menu()
        self.music_panel.load(self.music)
        self.add_strand()

        # Called last (not at the top of __init__): resize() on a QMainWindow
        # before setCentralWidget() and its children exist gets overridden by
        # Qt's own layout-driven size once the window is actually shown.
        # Wide enough that the strand-settings strip and the transport row
        # both fit in the center column at default size, without falling back
        # to controls_scroll's horizontal scrollbar.
        self.resize(1500, 800)

    # ------------------------------------------------------------------
    # File menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = self.title_bar.menu_bar.addMenu("&File")
        menu.addAction("&New", self._file_new)
        menu.addAction("&Open...", self._file_open)
        menu.addSeparator()
        menu.addAction("&Save", self._file_save)
        menu.addAction("Save &As...", self._file_save_as)
        menu.addSeparator()
        menu.addAction("&Import...", self._file_import)

        export_menu = self.title_bar.menu_bar.addMenu("&Export")
        export_menu.addAction("Export Current Strand as C++...", self._export_save)
        export_menu.addAction("Export &All Strands as C++...", self._export_all_save)
        export_menu.addSeparator()
        export_menu.addAction("Copy Current Strand C++ to Clipboard", self._export_clipboard)
        export_menu.addSeparator()
        # Label is filled in by _refresh_deploy_action(), which names the project.
        self._deploy_action = export_menu.addAction("", self._deploy)
        export_menu.addAction("Choose PROS Project...", self._choose_project)
        self._refresh_deploy_action()

    def _file_new(self) -> None:
        self._clear_sessions()
        self.music = MusicConfig()
        self.music_panel.load(self.music)
        self._current_file_path = None
        self._update_title()
        self.add_strand()

    def _file_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Profile", "", _FILE_FILTER)
        if not path:
            return
        try:
            document = load_document(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Open Failed", f"Couldn't load {path}:\n{exc}")
            return
        self._clear_sessions()
        self.music = document.music
        self.music_panel.load(self.music)
        for cfg in document.strands:
            self._add_session(cfg)
        if not self.sessions:
            self.add_strand()
        self._current_file_path = Path(path)
        self._update_title()

    def _file_save(self) -> None:
        if self._current_file_path is None:
            self._file_save_as()
            return
        self._write_to(self._current_file_path)

    def _file_save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save Profile", "", _FILE_FILTER)
        if not path:
            return
        p = Path(path)
        if not p.suffix:
            p = p.with_suffix(_DEFAULT_SUFFIX)
        self._write_to(p)
        self._current_file_path = p
        self._update_title()

    def _file_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Profile", "", _FILE_FILTER)
        if not path:
            return
        try:
            document = load_document(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Import Failed", f"Couldn't load {path}:\n{exc}")
            return
        self._adopt_music(document)
        for cfg in document.strands:
            self._add_session(cfg)

    def _write_to(self, path: Path) -> None:
        try:
            save_document(
                path, Document(strands=[s.config for s in self.sessions], music=self.music)
            )
        except OSError as exc:
            QMessageBox.critical(self, "Save Failed", f"Couldn't save {path}:\n{exc}")

    def _update_title(self) -> None:
        name = self._current_file_path.name if self._current_file_path else None
        suffix = f" - {name}" if name else ""
        # Both: the title bar is what's on screen, and the window title is
        # still what the taskbar and Alt-Tab read.
        self.setWindowTitle(f"HitLib Pattern Studio v{__version__}{suffix}")
        self.title_bar.set_title("HitLib Pattern Studio", __version__, name)

    # ------------------------------------------------------------------
    # Window chrome
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if not self._chrome_hooked:
            # Both need a native window to talk to, which only exists once
            # the window has been shown.
            window_chrome.round_corners(self)
            window_chrome.enable_native_snap(self)
            self._chrome_hooked = True

    def nativeEvent(self, event_type, message):  # noqa: N802 (Qt override)
        # Snap layouts: Windows has to be told the maximize button *is* the
        # caption's maximize button before it will offer the flyout.
        handled = window_chrome.handle_native_event(self.title_bar, event_type, message)
        if handled is not None:
            return handled
        return super().nativeEvent(event_type, message)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self.title_bar.resize_grips.reposition()

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self.title_bar.sync_window_state()
            self.title_bar.resize_grips.reposition()

    # ------------------------------------------------------------------
    # C++ export
    # ------------------------------------------------------------------

    def _show_export_errors(self, errors: list[str]) -> None:
        QMessageBox.critical(
            self, "Can't Export", "Fix these before exporting:\n\n" + "\n".join(f"- {e}" for e in errors)
        )

    def _current_config_or_warn(self) -> StrandConfig | None:
        """The selected strand's config, validated, with in-progress inspector
        edits flushed into it first."""
        session = self._current_session()
        if session is None:
            QMessageBox.warning(self, "No Strand Selected", "Select a strand to export first.")
            return None
        self.inspector.save(session.config)  # make sure in-progress edits are flushed
        errors = validate_for_export(session.config, self.music)
        if errors:
            self._show_export_errors(errors)
            return None
        return session.config

    def _all_configs_or_warn(self) -> list[StrandConfig] | None:
        if not self.sessions:
            QMessageBox.warning(self, "Nothing to Export", "Add a strand before exporting.")
            return None
        current = self._current_session()
        if current is not None:
            self.inspector.save(current.config)  # only the selected strand is bound to the inspector
        configs = [s.config for s in self.sessions]
        errors = validate_document_for_export(configs, self.music)
        if errors:
            self._show_export_errors(errors)
            return None
        return configs

    def _generate_export_or_warn(self, header_name: str | None = None) -> str | None:
        config = self._current_config_or_warn()
        if config is None:
            return None
        return generate_cpp(config, header_name, self.music)

    def _write_export(self, code_for: Callable[[str], str], title: str, suggested: str) -> None:
        """Ask for a path, then generate. The chosen filename goes into the
        generated file's #include line, so it can't be rendered until now."""
        path, _ = QFileDialog.getSaveFileName(self, title, suggested, _CPP_FILE_FILTER)
        if not path:
            return
        p = Path(path)
        if not p.suffix:
            p = p.with_suffix(".hpp")
        try:
            p.write_text(code_for(p.name), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Export Failed", f"Couldn't write {p}:\n{exc}")

    def _export_save(self) -> None:
        config = self._current_config_or_warn()
        if config is None:
            return
        self._write_export(
            lambda name: generate_cpp(config, name, self.music),
            "Export C++ Profile",
            suggested_header_name(config.name),
        )

    def _export_all_save(self) -> None:
        configs = self._all_configs_or_warn()
        if configs is None:
            return
        self._write_export(
            lambda name: generate_document_cpp(configs, name, self.music),
            "Export All Strands as C++",
            _DOCUMENT_HEADER_NAME,
        )

    def _export_clipboard(self) -> None:
        code = self._generate_export_or_warn()
        if code is None:
            return
        QGuiApplication.clipboard().setText(code)

    # ------------------------------------------------------------------
    # Deploying into a PROS project
    # ------------------------------------------------------------------

    def _settings(self) -> QSettings:
        return QSettings(_SETTINGS_ORG, _SETTINGS_APP)

    def _remembered_project(self) -> deploy.Project | None:
        """The project this machine last deployed to.

        Machine-local rather than saved in the document: a design gets shared
        with teammates, and the path to someone else's checkout is the one
        thing about it that cannot travel.
        """
        stored = self._settings().value(_SETTINGS_PROJECT_KEY, "", type=str)
        if not stored or not Path(stored).is_dir():
            return None
        return deploy.open_project(Path(stored))

    def _set_project(self, project: deploy.Project) -> None:
        self._project = project
        self._settings().setValue(_SETTINGS_PROJECT_KEY, str(project.root))
        self._refresh_deploy_action()

    def _refresh_deploy_action(self) -> None:
        """Name the destination in the menu item.

        A one-click action that writes into a directory should say which one
        before it is clicked, not after.
        """
        if self._deploy_action is None:
            return
        if self._project is None:
            self._deploy_action.setText("&Deploy to PROS Project...")
            self._deploy_action.setToolTip("Pick a project, then write the export into it")
        else:
            self._deploy_action.setText(f'&Deploy to "{self._project.root.name}"')
            self._deploy_action.setToolTip(str(self._project.include_dir))

    def _choose_project(self) -> deploy.Project | None:
        start = str(self._project.root) if self._project else ""
        path = QFileDialog.getExistingDirectory(self, "Choose PROS Project Folder", start)
        if not path:
            return None
        project = deploy.open_project(Path(path))
        if project is None:
            QMessageBox.warning(
                self,
                "Not a PROS Project",
                f"No {deploy.MANIFEST} in {path}, or in the folders above it.\n\n"
                "Pick the folder that holds your project.pros.",
            )
            return None
        self._set_project(project)
        return project

    def _deploy(self) -> None:
        """Write the whole document into the project's include/ directory.

        Always the whole document, never just the selected strand: begin() wires
        up every strand in the file, and half a document deployed over a whole
        one would silently drop the strands it left out.
        """
        configs = self._all_configs_or_warn()
        if configs is None:
            return
        project = self._project or self._choose_project()
        if project is None:
            return

        # Asked before the write, since afterwards every deploy looks like a
        # repeat one.
        first_time = not project.header_path(_STUDIO_HEADER_NAME).exists()
        code = generate_document_cpp(configs, _STUDIO_HEADER_NAME, self.music)
        try:
            written = project.deploy(_STUDIO_HEADER_NAME, code)
        except OSError as exc:
            QMessageBox.critical(
                self, "Deploy Failed", f"Couldn't write into {project.include_dir}:\n{exc}"
            )
            return

        if first_time:
            # open() rather than exec(): the dialog is something to read and
            # copy from, and freezing the design behind it while that happens
            # is what would make it feel like an error box.
            self._deploy_dialog = DeployDialog(
                written,
                paste_block(code),
                () if project.has_hitlib else deploy.INSTALL_HINT_LINES,
                self,
            )
            self._deploy_dialog.open()
        else:
            QMessageBox.information(
                self, "Deployed", f"Updated {written.name} in {written.parent}."
            )

    # ------------------------------------------------------------------
    # Drag and drop - a project dropped on the window becomes the target
    # ------------------------------------------------------------------

    def _dropped_project(self, mime) -> deploy.Project | None:
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            project = deploy.open_project(Path(url.toLocalFile()))
            if project is not None:
                return project
        return None

    def dragEnterEvent(self, event) -> None:
        # Refusing anything that is not a project is the feedback: the cursor
        # says no before the drop, rather than a dialog saying so after it.
        if self._dropped_project(event.mimeData()) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        project = self._dropped_project(event.mimeData())
        if project is None:
            return
        self._set_project(project)
        event.acceptProposedAction()
        QMessageBox.information(
            self,
            "Project Set",
            f'Deploy now writes into "{project.root.name}".\n\n{project.include_dir}',
        )

    # ------------------------------------------------------------------
    # Strand list management
    # ------------------------------------------------------------------

    def _add_session(self, cfg: StrandConfig) -> StrandSession:
        session = StrandSession(cfg, music=make_music_binding(self.music))
        session.ticked.connect(self.canvas.update)
        self.sessions.append(session)
        self._sync_music([session])
        if self._running:
            session.start()
        self.canvas.update()
        self._refresh_list()
        self.strand_list.select(len(self.sessions) - 1)
        return session

    def _clear_sessions(self) -> None:
        for session in self.sessions:
            session.stop()
            session.deleteLater()
        self.sessions.clear()
        self._current_index = -1
        self._selected_indices = []
        self._baseline = None
        self._baseline_index = -1
        self._refresh_list()
        self.canvas.set_selected(())
        self.canvas.update()

    def add_strand(self) -> None:
        n = len(self.sessions) + 1
        cfg = StrandConfig(name=f"Strand {n}", adi_port=min(n, 8))
        self._add_session(cfg)

    def remove_strand(self, row: int) -> None:
        self.remove_strands([row])

    def remove_selected_strands(self) -> None:
        """Remove every strand in the current group selection, not just the
        anchor. The Remove button acts on what the list shows as selected."""
        self.remove_strands(self._selected_indices)

    def remove_strands(self, rows: list[int]) -> None:
        valid = sorted({r for r in rows if 0 <= r < len(self.sessions)})
        if not valid:
            return
        # Highest row first, so each pop() can't shift the rows still to remove.
        for row in reversed(valid):
            session = self.sessions.pop(row)
            session.stop()
            session.deleteLater()
        self._refresh_list()
        new_row = min(valid[0], len(self.sessions) - 1)
        self.strand_list.select(new_row)
        # setCurrentRow(-1) is a no-op (and won't fire selection_changed) when
        # the row was already -1, which happens when the last strand is
        # removed. Call the handler directly so inspector/transport-button
        # enabled state doesn't go stale in that case.
        self._on_selection_changed()
        self.canvas.update()

    def _refresh_list(self) -> None:
        self.strand_list.set_names([s.config.name for s in self.sessions])

    def _current_session(self) -> StrandSession | None:
        if 0 <= self._current_index < len(self.sessions):
            return self.sessions[self._current_index]
        return None

    # ------------------------------------------------------------------
    # Selection / editing
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        rows = [r for r in self.strand_list.selected_rows() if 0 <= r < len(self.sessions)]
        anchor = self.strand_list.current_row()
        # Qt lets the current row sit outside the selection (Ctrl+arrow); the
        # anchor has to be a strand the edit will actually reach.
        if anchor not in rows:
            anchor = rows[0] if rows else -1
        self._selected_indices = rows
        self._current_index = anchor

        session = self._current_session()
        has_selection = session is not None
        self.inspector.setEnabled(has_selection)
        self.play_selected_btn.setEnabled(has_selection)
        self.pause_selected_btn.setEnabled(has_selection)
        self.reset_selected_btn.setEnabled(has_selection)
        self.strand_list.set_group_size(len(rows))
        self.inspector.set_group_size(len(rows))
        self.canvas.set_selected(rows)
        if session is not None:
            self.inspector.load(session.config)
        self._sync_preview_band()
        self._capture_baseline()

    def _group_sessions(self) -> list[StrandSession]:
        """Every session an edit (or a "Sel" transport button) applies to,
        anchor first."""
        anchor = self._current_session()
        if anchor is None:
            return []
        others = [
            self.sessions[i]
            for i in self._selected_indices
            if 0 <= i < len(self.sessions) and i != self._current_index
        ]
        return [anchor, *others]

    def _capture_baseline(self) -> None:
        """Snapshot the anchor's config so the *next* edit can be diffed
        against it. Taken up front rather than inside the edit because the
        panels mutate nested lists (profile modes, phases, splice regions) in
        place while the user works, not only in save(). By the time a
        changed signal arrives the config already holds the new value.
        """
        session = self._current_session()
        self._baseline = deepcopy(session.config) if session is not None else None
        self._baseline_index = self._current_index

    def _apply_group_edit(self, rebuild: bool) -> None:
        """Commit the inspector's edit to the anchor, replay just the fields it
        changed onto the rest of the selected group, then refresh every engine
        strand it touched.
        """
        anchor = self._current_session()
        if anchor is None:
            return
        self.inspector.save(anchor.config)

        sessions = self._group_sessions()
        # A stale baseline (anchor moved without a selection signal) would diff
        # against the wrong strand, so fall back to anchor-only editing.
        if len(sessions) > 1 and self._baseline is not None and self._baseline_index == self._current_index:
            changes = diff_config(self._baseline, anchor.config)
            for session in sessions[1:]:
                apply_changes(session.config, changes)
        self._capture_baseline()

        for session in sessions:
            if rebuild:
                session.rebuild()
            else:
                session.reapply_animation()
        self._sync_music(sessions)
        self._sync_preview_band()
        if rebuild:
            self._refresh_list()

    def _on_strand_settings_changed(self) -> None:
        self._apply_group_edit(rebuild=True)

    def _on_animation_changed(self) -> None:
        self._apply_group_edit(rebuild=False)

    # ------------------------------------------------------------------
    # Song
    # ------------------------------------------------------------------

    def _adopt_music(self, document: Document) -> bool:
        """Take an imported document's song, but only if this one has none.

        Import brings strands in alongside what is already open, so it must not
        replace a song that is already loaded. It does adopt one when there is
        nothing to lose, otherwise the Music Sync strands arriving with it would
        point at no song at all.
        """
        if self.music.loaded or not document.music.loaded:
            return False
        self.music = document.music
        self.music_panel.load(self.music)
        # Strands that were already open get the adopted song too, not just the
        # ones arriving with it.
        self._on_song_changed()
        return True

    def _sync_music(self, sessions: list[StrandSession]) -> None:
        """Put `sessions` where the Song bar's transport currently is.

        Anything that recreates or re-issues an engine strand restarts its
        playback from zero, so this runs after every rebuild as well as when
        the transport itself moves - otherwise editing a color mid-song would
        silently rewind that strand to the top of the track.
        """
        playing = self.music_panel.playing
        position = self.music_panel.position_ms
        for session in sessions:
            session.strand.music_pause(not playing)
            session.seek_music(position)

    def _sync_preview_band(self) -> None:
        """Draw the selected strand's band in the Song bar's scrubber.

        Which band a strand follows is a per-strand choice, so the waveform
        under the preview should be the one that strand is filling to - not a
        fixed band that happens to disagree with what the LEDs are doing.
        """
        session = self._current_session()
        self.music_panel.set_preview_band(
            _music_band(session.config) if session is not None else BAND_BASS
        )

    def _on_song_changed(self) -> None:
        """A new file, or a bake setting moved: hand every strand the new
        envelope. Only the Music Sync ones will do anything with it."""
        binding = make_music_binding(self.music)
        for session in self.sessions:
            session.set_music(binding)
        self._sync_music(self.sessions)

    def _on_music_position(self, position_ms: int) -> None:
        for session in self.sessions:
            session.seek_music(position_ms)

    def _on_music_playing(self, playing: bool) -> None:
        # Between position pushes a strand advances the song on its own, the
        # same way it will on the robot. Pausing it is what stops a paused
        # transport from drifting forward anyway.
        for session in self.sessions:
            session.strand.music_pause(not playing)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _play_all(self) -> None:
        # Also updates the default new-strand play state (see _add_session)
        # only the "All" actions change that default; per-strand play/pause
        # below is a one-off override for just that strand.
        self._running = True
        for session in self.sessions:
            session.start()

    def _pause_all(self) -> None:
        self._running = False
        for session in self.sessions:
            session.stop()

    def _reset_all(self) -> None:
        # Leaves every strand stopped, and clears the new-strand default so a
        # strand added afterwards does not light up alone.
        self._running = False
        for session in self.sessions:
            session.reset()

    def _play_selected(self) -> None:
        for session in self._group_sessions():
            session.start()

    def _pause_selected(self) -> None:
        for session in self._group_sessions():
            session.stop()

    def _reset_selected(self) -> None:
        for session in self._group_sessions():
            session.reset()
