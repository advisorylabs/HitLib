"""Top-level window: strand list (left) + live preview (center) + inspector (right)."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QGuiApplication,
    QKeySequence,
    QUndoStack,
)
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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__, deploy, presets, theme
from .canvas import StripCanvas
from .codegen import (
    generate_cpp,
    generate_document_cpp,
    paste_block,
    document_header_name,
    suggested_header_name,
    validate_document_for_export,
    validate_for_export,
)
from .deploy_dialog import DeployDialog
from .engine import make_music_binding, make_strand
from .envelope import BAND_BASS, bake
from .group_edit import apply_changes, diff_config
from .inspector import InspectorPanel
from .models import (
    AnimationKind,
    CompositionConfig,
    Document,
    MusicConfig,
    StrandConfig,
    composition_ids_named,
)
from .music_panel import MusicPanel
from .platforms import Platform
from .serialization import load_document, save_document
from .session import StrandSession
from .strand_list import StrandListPanel
from .widgets import BrandRule, ColumnScrollArea
from .animator import AnimatorPanel
from .undo import DocumentEdit, Snapshot
from .workspace_tabs import ANIMATE, DESIGN, WorkspaceTabs
from . import window_chrome

_FILE_FILTER = "HitLib Pattern Studio Profile (*.hlprofile);;JSON (*.json);;All Files (*)"
_DEFAULT_SUFFIX = ".hlprofile"
_CPP_FILE_FILTER = "C++ Header (*.hpp *.h);;All Files (*)"

#: Where the remembered deploy target lives. Named explicitly rather than left
#: to QApplication, so a MainWindow built outside app.main() reads the same
#: store the app does.
_SETTINGS_ORG = "AdvisoryLabs"
_SETTINGS_APP = "HitLib Pattern Studio"
_SETTINGS_PROJECT_KEY = "deploy/project_root"
_SETTINGS_PLATFORM_KEY = "export/platform"

#: The StrandConfig fields an engine strand is built from. Changing one needs
#: a new strand (StrandSession.rebuild); the rest of the settings strip does not.
_STRAND_BUILD_FIELDS = ("length", "adi_port", "smart_port", "refresh_ms")


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


def _plays_composition(config: StrandConfig) -> bool:
    """Whether anything this strand plays is a custom animation: its own
    animation, or - on a profile strand - any mode's or phase's instead."""
    if config.use_profile:
        leaves = [
            animation
            for mode in config.profile_modes
            for animation in ([p.animation for p in mode.phases] if mode.phases
                              else [mode.animation])
        ]
    else:
        leaves = [config.animation]
    return any(animation.kind == AnimationKind.CUSTOM for animation in leaves)


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
        # The robot project Deploy writes into, remembered across runs.
        # _deploy_action is held so its label can name that destination.
        self._deploy_action = None
        self._deploy_dialog: DeployDialog | None = None
        self._project = self._remembered_project()
        # What file and clipboard exports are generated for. Follows the
        # project whenever one is picked, since that is almost always the
        # robot the export is headed to; Deploy always uses the project's own.
        self._platform_actions = {}
        self._platform = (
            self._project.platform if self._project is not None else self._remembered_platform()
        )
        self.setAcceptDrops(True)
        # The document's one song. Held here rather than on any strand: see
        # models.MusicConfig and the Song bar under the preview.
        self.music = MusicConfig()
        # The document's custom animations. Document-level like the song: a
        # strand plays one by id, and several can play the same one.
        self.compositions: list[CompositionConfig] = []
        # The highest composition id this document has used, deleted ones
        # included. New ids are handed out above it - see _next_composition_id.
        self._comp_id_high = 0

        # Undo covers the strands and the composition library - see undo.py
        # for why the song is left out. `_undo_mark` is the document as the
        # stack last saw it; every edit is the difference from there.
        self.undo_stack = QUndoStack(self)
        self._undo_mark: Snapshot | None = None
        self._undo_epoch = 0
        self._restoring = False
        # A burst of edits (a spin box held down, a colour dragged) collapses
        # into one step. The epoch moves on after a pause, so the next thing
        # the user does is its own step.
        self._undo_idle = QTimer(self)
        self._undo_idle.setSingleShot(True)
        self._undo_idle.setInterval(700)
        self._undo_idle.timeout.connect(self._bump_undo_epoch)
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
        # Separately, since it is about to move out of the inspector - see
        # _on_selection_changed.
        self.inspector.strand_panel.setEnabled(False)

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

        # Sized by what it is showing rather than by a guess. Its widest
        # state - a custom splice region gauging motor temperature, inside a
        # profile mode - wants 480px where a rainbow wants 315, and a fixed
        # width either clipped the first or wasted room on the second.
        # Capped, so that one wide widget cannot take the whole window.
        right = ColumnScrollArea(self.inspector, floor=260, cap=720)

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
        # 460 on the right is the gauge case above, opened at full width
        # rather than grown into the moment a region becomes one.
        splitter.setSizes([210, 820, 460])

        # The wordmark gradient as a hairline under the menu bar. It drifts
        # and casts a short falloff onto the window; see widgets.BrandRule.
        brand_rule = BrandRule()

        # Two workspaces in a stack. The Design page is the splitter above,
        # moved wholesale: switching tabs must not change what the designer
        # does, only what is on screen.
        self.animator = AnimatorPanel()
        self.pages = QStackedWidget()
        self.pages.addWidget(splitter)        # DESIGN
        self.pages.addWidget(self.animator)   # ANIMATE

        self.tabs = WorkspaceTabs()
        self.title_bar.insert_after_menu(self.tabs)
        self.tabs.changed.connect(self._on_workspace_changed)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.title_bar)
        root_layout.addWidget(brand_rule)
        root_layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        self.canvas.set_sessions(self.sessions)

        self.strand_list.add_requested.connect(self.add_strand)
        self.strand_list.remove_requested.connect(self.remove_selected_strands)
        self.strand_list.selection_changed.connect(self._on_selection_changed)
        self.inspector.strand_settings_changed.connect(self._on_strand_settings_changed)
        self.inspector.animation_changed.connect(self._on_animation_changed)
        self.inspector.anim_panel.open_as_custom.connect(self._open_as_custom)
        self.music_panel.song_changed.connect(self._on_song_changed)
        self.music_panel.position_changed.connect(self._on_music_position)
        self.music_panel.playing_changed.connect(self._on_music_playing)
        self.play_all_btn.clicked.connect(self._play_all)
        self.pause_all_btn.clicked.connect(self._pause_all)
        self.reset_all_btn.clicked.connect(self._reset_all)
        self.play_selected_btn.clicked.connect(self._play_selected)
        self.pause_selected_btn.clicked.connect(self._pause_selected)
        self.reset_selected_btn.clicked.connect(self._reset_selected)

        self.animator.set_library(self.compositions, self._next_composition_id)
        self.inspector.set_compositions(self.compositions)
        self.animator.changed.connect(self._on_animator_changed)
        self.animator.use_requested.connect(self._play_composition_on_selection)

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
        window_chrome.release_safe_area(self)

    # ------------------------------------------------------------------
    # File menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = self.title_bar.menu_bar.addMenu("&File")
        new_action = menu.addAction("&New", self._file_new)
        open_action = menu.addAction("&Open...", self._file_open)
        menu.addSeparator()
        save_action = menu.addAction("&Save", self._file_save)
        save_as_action = menu.addAction("Save &As...", self._file_save_as)
        menu.addSeparator()
        menu.addAction("&Import...", self._file_import)
        if window_chrome.IS_MAC:
            self._add_mac_file_keys(menu, new_action, open_action, save_action, save_as_action)

        edit_menu = self.title_bar.menu_bar.addMenu("&Edit")
        self.undo_action = self.undo_stack.createUndoAction(self, "&Undo")
        self.redo_action = self.undo_stack.createRedoAction(self, "&Redo")
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        # Both of the usual redo chords: Ctrl+Y is what Windows tools use and
        # Ctrl+Shift+Z is what every editor does, and a team will try one of
        # the two without thinking about which.
        self.redo_action.setShortcuts([
            QKeySequence.StandardKey.Redo,
            QKeySequence("Ctrl+Shift+Z"),
        ])
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)

        export_menu = self.title_bar.menu_bar.addMenu("&Export")
        export_menu.addAction("Export Current Strand as C++...", self._export_save)
        export_menu.addAction("Export &All Strands as C++...", self._export_all_save)
        export_menu.addSeparator()
        export_menu.addAction("Copy Current Strand C++ to Clipboard", self._export_clipboard)
        export_menu.addSeparator()
        platform_menu = export_menu.addMenu("Target &Platform")
        platform_group = QActionGroup(platform_menu)
        for platform in Platform:
            action = platform_menu.addAction(platform.label)
            action.setCheckable(True)
            action.setChecked(platform is self._platform)
            action.triggered.connect(lambda _checked, p=platform: self._set_platform(p))
            platform_group.addAction(action)
            self._platform_actions[platform] = action
        export_menu.addSeparator()
        # Label is filled in by _refresh_deploy_action(), which names the project.
        self._deploy_action = export_menu.addAction("", self._deploy)
        export_menu.addAction("Choose Robot Project...", self._choose_project)
        self._refresh_deploy_action()

    def _add_mac_file_keys(self, menu, new_action, open_action, save_action, save_as_action) -> None:
        """What the File menu needs on macOS and nowhere else.

        The menus live in the screen's menu bar there, where Alt mnemonics do
        not exist, so without Cmd shortcuts File has no keyboard route at all.
        Windows deliberately gets none of this: nothing in File asks before it
        acts, and a new Ctrl+N or Ctrl+W there would throw the open design
        away on a stray keypress where today it takes a click.

        There is no View menu with a full-screen item either. AppKit adds its
        own "Enter Full Screen" to any menu titled View, so one of ours would
        stand next to it as a second copy; the green button and Globe+F
        already cover it.
        """
        for action, key in (
            (new_action, QKeySequence.StandardKey.New),
            (open_action, QKeySequence.StandardKey.Open),
            (save_action, QKeySequence.StandardKey.Save),
            (save_as_action, QKeySequence.StandardKey.SaveAs),
        ):
            action.setShortcut(key)
        menu.addSeparator()
        close_action = menu.addAction("Close Window", self.close)
        close_action.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Close))

        about = QAction("About HitLib Pattern Studio", self)
        # AboutRole moves it out of File into the application menu, where the
        # About slot already exists and would otherwise sit empty.
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(self._show_about)
        menu.addAction(about)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About HitLib Pattern Studio",
            f"<b>HitLib Pattern Studio</b> {__version__}<br><br>"
            "Designs LED profiles for HitLib and exports them as C++ "
            "you include in a VEX V5 project.<br><br>"
            "Advisory Labs - 96671H Hitmen. MPL-2.0 licensed.<br>"
            '<a href="https://github.com/advisorylabs/HitLib">'
            "github.com/advisorylabs/HitLib</a>",
        )

    def _file_new(self) -> None:
        self._clear_sessions()
        self.music = MusicConfig()
        self.compositions = []
        self._comp_id_high = 0
        self.inspector.set_compositions(self.compositions)
        self.music_panel.load(self.music)
        self._current_file_path = None
        self._update_title()
        self.add_strand()
        self.animator.set_library(self.compositions, self._next_composition_id)
        self.undo_stack.clear()
        self._mark_undo()

    def _load_checked(self, path: str, title: str) -> Document | None:
        """`path` as a Document whose song bakes and whose every strand builds
        and plays, or None once the user has been told why not.

        All of it is proven before the caller touches the open design. Open
        used to clear the window first, so a file that failed partway left it
        with no strands under the old file's name - and Save then wrote that
        over the old file.
        """
        try:
            document = load_document(path)
            music = document.music
            for band in music.analysis.bands:
                bake(music.analysis, band, music.settings)
            library = {c.id: c for c in document.compositions}
            for cfg in document.strands:
                make_strand(cfg, None, library).tick()
        except (OSError, ValueError, TypeError, AttributeError, LookupError,
                ArithmeticError) as exc:
            # Past load_document, whatever the engine raises means the same
            # thing: the file holds a value it cannot play.
            QMessageBox.critical(self, title, f"Couldn't load {path}:\n{exc}")
            return None
        return document

    def _file_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Profile", "", _FILE_FILTER)
        if not path:
            return
        document = self._load_checked(path, "Open Failed")
        if document is None:
            return
        self._clear_sessions()
        self.music = document.music
        self.compositions = document.compositions
        self._comp_id_high = 0
        # Before the strands: the first one is shown as soon as it is added,
        # and its Plays list has to already hold what it plays.
        self.inspector.set_compositions(self.compositions)
        self.music_panel.load(self.music)
        for cfg in document.strands:
            self._add_session(cfg)
        if not self.sessions:
            self.add_strand()
        self._current_file_path = Path(path)
        self._update_title()
        self._note_composition_ids()
        self.animator.set_library(self.compositions, self._next_composition_id)
        self.undo_stack.clear()
        self._mark_undo()

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
        # Only a file that now holds the design becomes the one Save writes to.
        if self._write_to(p):
            self._current_file_path = p
            self._update_title()

    def _file_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Profile", "", _FILE_FILTER)
        if not path:
            return
        document = self._load_checked(path, "Import Failed")
        if document is None:
            return
        # Its own undo step. Left out, the next edit's step reached back past
        # the import, and undoing that edit took the imported strands with it.
        self._bump_undo_epoch()
        self._adopt_music(document)
        self._adopt_compositions(document)
        self.inspector.set_compositions(self.compositions)
        for cfg in document.strands:
            self._add_session(cfg)
        self._sync_compositions()
        self.animator.set_library(self.compositions, self._next_composition_id)
        self._push_undo("Import")

    def _write_to(self, path: Path) -> bool:
        """Save the document to `path`. False, once the user has been told,
        if it could not be written."""
        try:
            save_document(
                path,
                Document(strands=[s.config for s in self.sessions], music=self.music,
                         compositions=self.compositions),
            )
        except OSError as exc:
            QMessageBox.critical(self, "Save Failed", f"Couldn't save {path}:\n{exc}")
            return False
        return True

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
            window_chrome.release_safe_area(self)
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
        errors = validate_for_export(session.config, self.music, self.compositions)
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
        errors = validate_document_for_export(configs, self.music, self.compositions)
        if errors:
            self._show_export_errors(errors)
            return None
        return configs

    def _generate_export_or_warn(self, header_name: str | None = None) -> str | None:
        config = self._current_config_or_warn()
        if config is None:
            return None
        return generate_cpp(config, header_name, self.music, self._platform,
                            self.compositions)

    def _write_export(self, code_for: Callable[[str], str], title: str, suggested: str) -> None:
        """Ask for a path, then generate. The chosen filename goes into the
        generated file's #include line, so it can't be rendered until now."""
        path, _ = QFileDialog.getSaveFileName(self, title, suggested, _CPP_FILE_FILTER)
        if not path:
            return
        p = Path(path)
        if not p.suffix:
            p = p.with_suffix(self._platform.header_suffix)
        try:
            # newline="" for the same reason deploy.Project.deploy() does it:
            # the same design has to export the same bytes on every platform.
            p.write_text(code_for(p.name), encoding="utf-8", newline="")
        except (OSError, UnicodeError) as exc:
            # UnicodeError: a JSON escape in a hand-edited file can put half
            # an emoji into a name, which has no UTF-8 form. That is as much
            # a failed write as a full disk is.
            QMessageBox.critical(self, "Export Failed", f"Couldn't write {p}:\n{exc}")

    def _export_save(self) -> None:
        config = self._current_config_or_warn()
        if config is None:
            return
        self._write_export(
            lambda name: generate_cpp(config, name, self.music, self._platform,
                                      self.compositions),
            "Export C++ Profile",
            suggested_header_name(config.name, self._platform),
        )

    def _export_all_save(self) -> None:
        configs = self._all_configs_or_warn()
        if configs is None:
            return
        self._write_export(
            lambda name: generate_document_cpp(configs, name, self.music, self._platform,
                                               self.compositions),
            "Export All Strands as C++",
            document_header_name(self._platform),
        )

    def _export_clipboard(self) -> None:
        code = self._generate_export_or_warn()
        if code is None:
            return
        QGuiApplication.clipboard().setText(code)

    # ------------------------------------------------------------------
    # Deploying into a robot project
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
        self._set_platform(project.platform)
        self._refresh_deploy_action()

    def _remembered_platform(self) -> Platform:
        return Platform.parse(self._settings().value(_SETTINGS_PLATFORM_KEY, "", type=str))

    def _set_platform(self, platform: Platform) -> None:
        """Make @p platform what exports are generated for, and tick it."""
        self._platform = platform
        self._settings().setValue(_SETTINGS_PLATFORM_KEY, platform.value)
        action = self._platform_actions.get(platform)
        if action is not None:
            action.setChecked(True)

    def _refresh_deploy_action(self) -> None:
        """Name the destination in the menu item.

        A one-click action that writes into a directory should say which one
        before it is clicked, not after.
        """
        if self._deploy_action is None:
            return
        if self._project is None:
            self._deploy_action.setText("&Deploy to Robot Project...")
            self._deploy_action.setToolTip("Pick a PROS or VEXcode project, then write the export into it")
        else:
            self._deploy_action.setText(f'&Deploy to "{self._project.root.name}"')
            self._deploy_action.setToolTip(
                f"{self._project.platform.label} project - {self._project.include_dir}"
            )

    def _choose_project(self) -> deploy.Project | None:
        start = str(self._project.root) if self._project else ""
        path = QFileDialog.getExistingDirectory(self, "Choose Robot Project Folder", start)
        if not path:
            return None
        project = deploy.open_project(Path(path))
        if project is None:
            QMessageBox.warning(
                self,
                "Not a Robot Project",
                f"{path} isn't a PROS or VEXcode V5 C++ project, and neither is any "
                "folder above it.\n\n"
                f"Pick the folder that holds your {deploy.MANIFEST} (PROS), "
                f"your {deploy.V5CODE_SUFFIX} file (VEXcode), or the .vscode "
                "folder a VS Code VEX project keeps its settings in.",
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
        header_name = project.studio_header_name
        first_time = not project.header_path(header_name).exists()
        code = generate_document_cpp(configs, header_name, self.music, project.platform,
                                     self.compositions)
        try:
            written = project.deploy(header_name, code)
        except (OSError, UnicodeError) as exc:  # UnicodeError: see _write_export
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
                () if project.has_hitlib else project.install_hint_lines,
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
            f'Deploy now writes a {project.platform.label} export into "{project.root.name}".'
            f"\n\n{project.include_dir}",
        )

    # ------------------------------------------------------------------
    # Strand list management
    # ------------------------------------------------------------------

    def _add_session(self, cfg: StrandConfig) -> StrandSession:
        session = StrandSession(cfg, music=make_music_binding(self.music),
                                compositions=self._composition_map())
        session.ticked.connect(self.canvas.request_frame)
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
        # The lowest brain port nothing is on yet, rather than one per strand
        # so far: after a remove, that count lands on a port still in use. A
        # strand on an expander (smart_port) is on that expander's ports.
        used = {s.config.adi_port for s in self.sessions if s.config.smart_port == 0}
        port = next((p for p in range(1, 9) if p not in used), 8)
        cfg = StrandConfig(name=f"Strand {n}", adi_port=port)
        self._bump_undo_epoch()
        self._add_session(cfg)
        self._push_undo("Add Strand")

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
        self._bump_undo_epoch()
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
        self._push_undo("Remove Strand" if len(valid) == 1 else "Remove Strands")

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
        # Not reached by the line above: the settings strip sits over the
        # preview, reparented out of the inspector, and would otherwise go on
        # showing a removed strand and taking edits that land nowhere.
        self.inspector.strand_panel.setEnabled(has_selection)
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
        if not self._restoring and self._undo_mark is not None:
            # Moving the cursor ends the current burst, so the next edit is
            # its own undo step. It may refresh *where* the mark says the user
            # was, but never what the document held: _add_session() selects
            # the row it just created, and re-taking the content baseline
            # there would swallow the addition before _push_undo ever saw it.
            self._bump_undo_epoch()
            now = self._snapshot()
            if self._undo_mark.same_content_as(now):
                # A new mark, not an edit of this one: it is also the last
                # command's `after`, and moving it would move where redo lands.
                self._undo_mark = replace(self._undo_mark, current=now.current,
                                          selected=now.selected)

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

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def _snapshot(self) -> Snapshot:
        return Snapshot.of([s.config for s in self.sessions], self.compositions,
                           self._current_index, self._selected_indices)

    def _mark_undo(self) -> None:
        """Take the document as the baseline for the next edit.

        Called after anything that changes the document outside an edit -
        opening a file, a new document, an undo - so the next real edit is
        measured from there rather than from whatever was open before.
        """
        self._undo_mark = self._snapshot()

    def _bump_undo_epoch(self) -> None:
        """End the current burst, so the next edit starts a new undo step."""
        self._undo_epoch += 1
        self._undo_idle.stop()

    def _push_undo(self, text: str) -> None:
        """Record the difference between the last mark and now, if any.

        Every edit path funnels through here rather than building its own
        command, which is what keeps the panels free of undo plumbing.
        """
        if self._restoring:
            return
        after = self._snapshot()
        before = self._undo_mark
        if before is None:
            self._undo_mark = after
            return
        if before.same_content_as(after):
            # Selection moved, or a widget re-emitted the value it already
            # had. Neither is an edit, and neither should cost a Ctrl+Z.
            self._undo_mark = after
            return
        self.undo_stack.push(DocumentEdit(self, text, before, after, self._undo_epoch))
        self._undo_mark = after
        self._undo_idle.start()

    def restore_snapshot(self, snap: Snapshot) -> None:
        """Put the document back to `snap`. Called only by DocumentEdit."""
        self._restoring = True
        try:
            # Play/pause is not part of the document, so it is carried across
            # the rebuild by row: a strand paused on its own stays paused
            # through an undo of some other edit. A row the undo brings back
            # takes the "All" default, as a new strand does.
            running = [s.running for s in self.sessions]
            self._clear_sessions()
            self.compositions = deepcopy(snap.compositions)
            self.inspector.set_compositions(self.compositions)
            library = self._composition_map()
            for row, cfg in enumerate(deepcopy(snap.strands)):
                session = StrandSession(cfg, music=make_music_binding(self.music),
                                        compositions=library)
                session.ticked.connect(self.canvas.request_frame)
                self.sessions.append(session)
                was_running = running[row] if row < len(running) else self._running
                if was_running:
                    session.start()
            self._refresh_list()
            # Undoing an Add lands on a row that no longer exists, so the
            # cursor is clamped rather than left nowhere.
            count = len(self.sessions)
            rows = [r for r in snap.selected if 0 <= r < count] or ([0] if count else [])
            current = snap.current if 0 <= snap.current < count else (0 if count else -1)
            self.strand_list.select_rows(rows, current)
            self._on_selection_changed()
            self._sync_music(self.sessions)
            self._note_composition_ids()
            self.animator.set_library(self.compositions, self._next_composition_id)
            self.canvas.update()
        finally:
            self._restoring = False
        # An undo is a fresh starting point, and the next edit must not merge
        # into the burst that was interrupted by it.
        self._undo_mark = self._snapshot()
        self._bump_undo_epoch()

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
        # Divide shares out the strip as it is now, and Length may be what
        # just changed. Nothing else tells the masks panels between loads.
        self.inspector.masks_panel.set_strip_length(anchor.config.length)
        self.inspector.modes_panel.masks_panel.set_strip_length(anchor.config.length)

        sessions = self._group_sessions()
        # A stale baseline (anchor moved without a selection signal) would diff
        # against the wrong strand, so fall back to anchor-only editing.
        before = self._baseline if self._baseline_index == self._current_index else None
        if len(sessions) > 1 and before is not None:
            changes = diff_config(before, anchor.config)
            for session in sessions[1:]:
                apply_changes(session.config, changes)
        self._capture_baseline()

        settings_edit = rebuild
        reissue = True
        if settings_edit and before is not None:
            # Only what the engine strand is built from needs a new one, and
            # the name never reaches the engine at all. Typed a key at a time,
            # it restarted every animation in the group on each keystroke.
            rebuild = any(getattr(before, f) != getattr(anchor.config, f)
                          for f in _STRAND_BUILD_FIELDS)
            reissue = rebuild or before.brightness != anchor.config.brightness

        if reissue:
            for session in sessions:
                if rebuild:
                    session.rebuild()
                else:
                    session.reapply_animation()
            self._sync_music(sessions)
        self._sync_preview_band()
        if settings_edit:
            self._refresh_list()

    def _on_strand_settings_changed(self) -> None:
        self._apply_group_edit(rebuild=True)
        self._push_undo("Strand Settings")

    def _on_animation_changed(self) -> None:
        self._apply_group_edit(rebuild=False)
        self._push_undo("Animation")

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
        # has_song, not loaded: a document just read from a file has its song
        # but not yet the baked tables `loaded` looks for.
        if self.music.has_song or not document.music.has_song:
            return False
        self.music = document.music
        self.music_panel.load(self.music)
        # Strands that were already open get the adopted song too, not just the
        # ones arriving with it.
        self._on_song_changed()
        return True

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    def _on_workspace_changed(self, index: int) -> None:
        """Switch pages, and stop whichever preview is now off screen.

        Both pages animate. Leaving the hidden one ticking would double the
        paint load for nothing, and the canvas on the hidden page repaints
        even when it cannot be seen.
        """
        self.pages.setCurrentIndex(index)
        if index == ANIMATE:
            self.animator.refresh()
            self.animator.offer_templates_if_empty()
            self.animator.start_preview()
        else:
            self.animator.stop_preview()

    def _open_as_custom(self) -> None:
        """Turn the selected strand's animation into a composition and open it.

        The whole architecture, made clickable: a preset already *is* a
        custom animation, so this converts rather than starting over. The
        strand keeps playing exactly what it was playing a moment ago - just
        through the Animator now, with room to add a layer.
        """
        session = self._current_session()
        if session is None:
            return
        animation = session.config.animation
        if animation.kind == AnimationKind.CUSTOM:
            # Already one. Just go and edit it.
            self.tabs.go_to(ANIMATE)
            for row, comp in enumerate(self.compositions):
                if comp.id == animation.composition_id:
                    self.animator.library.setCurrentRow(row)
                    break
            return

        self._bump_undo_epoch()
        comp = presets.composition_for_animation(
            animation, name=session.config.name or "Animation",
            comp_id=self._next_composition_id())
        self.compositions.append(comp)
        animation.kind = AnimationKind.CUSTOM
        animation.composition_id = comp.id

        self._sync_compositions()
        self.inspector.load(session.config)
        self._capture_baseline()
        self.animator.set_library(self.compositions, self._next_composition_id)
        self.animator.library.setCurrentRow(len(self.compositions) - 1)
        self._push_undo("Open as Custom")
        self.tabs.go_to(ANIMATE)

    def _next_composition_id(self) -> int:
        """An id no composition is using. Never reused within a session, so a
        strand pointing at a deleted animation stays visibly broken rather
        than silently adopting whatever was made next.

        Counted from a mark that only rises, not from the library as it
        stands: once the newest animation is deleted, the library's max + 1
        is that animation's id again.
        """
        return self._note_composition_ids() + 1

    def _note_composition_ids(self) -> int:
        """Raise the id mark past every id in use now - in the library, or
        named by a strand whether the library has it or not - and return it.

        Has to run while an animation is still in the library, so it is
        called on every library change rather than only when an id is needed.
        """
        in_use = [c.id for c in self.compositions]
        in_use.extend(composition_ids_named([s.config for s in self.sessions]))
        self._comp_id_high = max([self._comp_id_high, *in_use])
        return self._comp_id_high

    def _on_animator_changed(self) -> None:
        """An animation was edited. Every strand playing it has to pick the
        change up, and the whole thing is one undo step per burst."""
        self._sync_compositions()
        self._push_undo("Animation Edit")

    def _play_composition_on_selection(self, comp_id: int) -> None:
        """Point the selected strands at this animation and show the result.

        The shortcut back out of the Animator: having built something, the
        next thing anyone wants is to see it on the strip they built it for.
        """
        sessions = self._group_sessions()
        if not sessions:
            QMessageBox.information(
                self, "No Strand Selected",
                "Select a strand on the Design tab first, then this will play "
                "the animation on it.")
            return
        # A profile strand plays its modes, so its own animation is hidden:
        # replacing it would change nothing on the strip, and the user would
        # only find it gone after turning Use Profile off.
        on_profile = [f'"{s.config.name}"' for s in sessions if s.config.use_profile]
        if on_profile:
            if len(on_profile) == 1:
                who = f"{on_profile[0]} plays its profile's modes"
            else:
                who = f"{', '.join(on_profile)} play their profiles' modes"
            QMessageBox.information(
                self, "Strand Plays a Profile",
                f"{who} rather than a single animation. Pick this animation "
                "for a mode on the Design tab, or turn Use Profile off first.")
            return
        self._bump_undo_epoch()
        for session in sessions:
            session.config.animation.kind = AnimationKind.CUSTOM
            session.config.animation.composition_id = comp_id
        self._sync_compositions()
        session = self._current_session()
        if session is not None:
            self.inspector.load(session.config)
        self._capture_baseline()
        self._push_undo("Play Custom Animation")
        self.tabs.go_to(DESIGN)

    def _composition_map(self) -> dict[int, CompositionConfig]:
        """The library as the engine wants it: id -> composition."""
        return {c.id: c for c in self.compositions}

    def _sync_compositions(self) -> None:
        """Hand every session the current library, and re-issue the animation
        of each one that plays a composition.

        Called after anything edits a composition, because a strand playing
        one has to pick the change up without being touched itself. No other
        strand is re-issued: that restarts its animation, and this runs on
        every drag in the Animator.
        """
        self._note_composition_ids()
        library = self._composition_map()
        reissued = []
        for session in self.sessions:
            plays = _plays_composition(session.config)
            session.set_compositions(library, reapply=plays)
            if plays:
                reissued.append(session)
        # Re-issuing restarted their songs - see _sync_music.
        self._sync_music(reissued)
        # A rename or a new animation shows up in the Plays lists too.
        self.inspector.set_compositions(self.compositions)
        self.canvas.update()

    def _adopt_compositions(self, document: Document) -> None:
        """Merge an imported document's animations into this one's library.

        Import adds to what is open rather than replacing it, so any incoming
        id this document may already have used is rewritten to a fresh one,
        and the strands arriving with it are repointed. Without that,
        importing two files that both start at id 1 would silently have the
        second file's strands play the first file's animations - and an
        imported animation could take the id of a deleted one that a strand
        here still points at.

        The ids the imported strands name count as incoming too, found or not:
        a reference its own file left dangling has to stay dangling here.
        """
        incoming = {c.id for c in document.compositions}
        incoming |= composition_ids_named(document.strands)
        if not incoming:
            return
        high = self._note_composition_ids()
        # Past every incoming id as well as every one used here, so a
        # rewritten id can never land on one another incoming animation keeps.
        next_id = max(high, *incoming) + 1
        remap: dict[int, int] = {}
        for old in sorted(i for i in incoming if i <= high):
            remap[old] = next_id
            next_id += 1

        for comp in document.compositions:
            comp.id = remap.get(comp.id, comp.id)
            self.compositions.append(comp)
        for cfg in document.strands:
            for a in cfg.animations():
                a.composition_id = remap.get(a.composition_id, a.composition_id)

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
            session.pause_music(not playing)
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
            session.pause_music(not playing)

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
