"""Top-level window: strand list (left) + live preview (center) + inspector (right)."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt
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

from . import __version__, theme
from .canvas import StripCanvas
from .codegen import (
    generate_cpp,
    generate_document_cpp,
    suggested_header_name,
    validate_document_for_export,
    validate_for_export,
)
from .group_edit import apply_changes, diff_config
from .inspector import InspectorPanel
from .models import StrandConfig
from .serialization import load_document, save_document
from .session import StrandSession
from .strand_list import StrandListPanel
from .widgets import BrandRule
from . import window_chrome

_FILE_FILTER = "HitLib Pattern Studio Profile (*.hlprofile);;JSON (*.json);;All Files (*)"
_DEFAULT_SUFFIX = ".hlprofile"
_CPP_FILE_FILTER = "C++ Header (*.hpp);;All Files (*)"


def _transport_button(label: str, icon_name: str, tooltip: str) -> QPushButton:
    button = QPushButton(theme.icon(icon_name), f" {label}")
    button.setProperty("role", "transport")
    button.setIconSize(QSize(13, 13))
    button.setToolTip(tooltip)
    # Cyan rather than the violet accent: transport acts on what's *running*,
    # and cyan is already the app's "this is live" color (selected rows, the
    # group outline on the canvas).
    theme.HoverBloom(button, theme.FOCUS, radius=14, alpha=105)
    return button


def _scope_label(text: str) -> QLabel:
    """The "ALL" / "SELECTED" heading over a transport triad."""
    label = QLabel(text)
    label.setProperty("role", "sectionHeader")
    return label


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
        # Frameless, with the logo/menus/title/caption buttons folded into one
        # row. See window_chrome for what that trades away and how it's
        # paid back. Built before _update_title(), which writes into it.
        self.title_bar = window_chrome.install(self)
        self._chrome_hooked = False
        self._update_title()

        self.canvas = StripCanvas()
        self.strand_list = StrandListPanel()
        self.inspector = InspectorPanel()
        self.inspector.setEnabled(False)

        # Same six actions, same order as before, but the scope now lives in
        # a heading over each triad instead of inside every label. "Play Sel"
        # read to newcomers as a fourth verb rather than as a scope, and the
        # abbreviation only existed to keep the row narrow.
        self.play_all_btn = _transport_button("Play", "play", "Play every strand")
        self.pause_all_btn = _transport_button("Pause", "pause", "Pause every strand")
        self.reset_all_btn = _transport_button("Reset", "reset", "Restart every strand's animation")
        self.play_selected_btn = _transport_button("Play", "play", "Play the selected strands")
        self.pause_selected_btn = _transport_button("Pause", "pause", "Pause the selected strands")
        self.reset_selected_btn = _transport_button(
            "Reset", "reset", "Restart the selected strands' animations"
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

        # Transport row + strand-settings strip both have a real minimum
        # width (6 buttons; 6 fields) that can exceed a narrowed center
        # column once the splitter is dragged, or even at default size on
        # a modest window. Wrapping both together in one slim scroll area
        # means THIS strip gets a small horizontal scrollbar in that squeeze
        # instead of either clipping its contents or forcing the whole
        # center column to refuse to shrink. They scroll together as one
        # unit since they're logically one "controls header" block.
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addLayout(toolbar_row)
        controls_layout.addWidget(self.inspector.strand_panel)

        controls_scroll = QScrollArea()
        controls_scroll.setWidget(controls)
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Reserve room for the horizontal scrollbar even when it isn't shown.
        # The height is fixed, so a bar appearing on a narrow window would
        # otherwise eat it out of the strip's own height and clip the fields.
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

        right = QScrollArea()
        right.setWidget(self.inspector)
        right.setWidgetResizable(True)
        right.setMinimumWidth(260)

        # QSplitter lets every column be resized by dragging its edge,
        # replaces the old fixed-width side panels, which could force a
        # horizontal scrollbar in the right column with no way to widen it.
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([210, 880, 400])

        # The wordmark gradient as a hairline under the menu bar, the same
        # accent the docs site runs across its header, and the one piece of
        # brand color that's always on screen. It drifts and casts a short
        # falloff onto the window; see widgets.BrandRule.
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
        self.play_all_btn.clicked.connect(self._play_all)
        self.pause_all_btn.clicked.connect(self._pause_all)
        self.reset_all_btn.clicked.connect(self._reset_all)
        self.play_selected_btn.clicked.connect(self._play_selected)
        self.pause_selected_btn.clicked.connect(self._pause_selected)
        self.reset_selected_btn.clicked.connect(self._reset_selected)

        self._build_menu()
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

    def _file_new(self) -> None:
        self._clear_sessions()
        self._current_file_path = None
        self._update_title()
        self.add_strand()

    def _file_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Profile", "", _FILE_FILTER)
        if not path:
            return
        try:
            configs = load_document(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Open Failed", f"Couldn't load {path}:\n{exc}")
            return
        self._clear_sessions()
        for cfg in configs:
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
            configs = load_document(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Import Failed", f"Couldn't load {path}:\n{exc}")
            return
        for cfg in configs:
            self._add_session(cfg)

    def _write_to(self, path: Path) -> None:
        try:
            save_document(path, [s.config for s in self.sessions])
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
        errors = validate_for_export(session.config)
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
        errors = validate_document_for_export(configs)
        if errors:
            self._show_export_errors(errors)
            return None
        return configs

    def _generate_export_or_warn(self, header_name: str | None = None) -> str | None:
        config = self._current_config_or_warn()
        if config is None:
            return None
        return generate_cpp(config, header_name)

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
            lambda name: generate_cpp(config, name),
            "Export C++ Profile",
            suggested_header_name(config.name),
        )

    def _export_all_save(self) -> None:
        configs = self._all_configs_or_warn()
        if configs is None:
            return
        self._write_export(
            lambda name: generate_document_cpp(configs, name),
            "Export All Strands as C++",
            "led_profiles.hpp",
        )

    def _export_clipboard(self) -> None:
        code = self._generate_export_or_warn()
        if code is None:
            return
        QGuiApplication.clipboard().setText(code)

    # ------------------------------------------------------------------
    # Strand list management
    # ------------------------------------------------------------------

    def _add_session(self, cfg: StrandConfig) -> StrandSession:
        session = StrandSession(cfg)
        session.ticked.connect(self.canvas.update)
        self.sessions.append(session)
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
        if rebuild:
            self._refresh_list()

    def _on_strand_settings_changed(self) -> None:
        self._apply_group_edit(rebuild=True)

    def _on_animation_changed(self) -> None:
        self._apply_group_edit(rebuild=False)

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
        for session in self.sessions:
            session.rebuild()

    def _play_selected(self) -> None:
        for session in self._group_sessions():
            session.start()

    def _pause_selected(self) -> None:
        for session in self._group_sessions():
            session.stop()

    def _reset_selected(self) -> None:
        for session in self._group_sessions():
            session.rebuild()
