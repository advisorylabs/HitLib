"""Top-level window: strand list (left) + live preview (center) + inspector (right)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .canvas import StripCanvas
from .codegen import generate_cpp, validate_for_export
from .inspector import InspectorPanel
from .models import StrandConfig
from .serialization import load_document, save_document
from .session import StrandSession
from .strand_list import StrandListPanel

_FILE_FILTER = "HitLib Pattern Studio Profile (*.hlprofile);;JSON (*.json);;All Files (*)"
_DEFAULT_SUFFIX = ".hlprofile"
_CPP_FILE_FILTER = "C++ Header (*.hpp);;All Files (*)"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HitLib Pattern Studio")

        self.sessions: list[StrandSession] = []
        self._current_index = -1
        self._running = True
        self._current_file_path: Path | None = None

        self.canvas = StripCanvas()
        self.strand_list = StrandListPanel()
        self.inspector = InspectorPanel()
        self.inspector.setEnabled(False)

        self.play_all_btn = QPushButton("Play All")
        self.pause_all_btn = QPushButton("Pause All")
        self.reset_all_btn = QPushButton("Reset All")
        self.play_selected_btn = QPushButton("Play Sel")
        self.pause_selected_btn = QPushButton("Pause Sel")
        self.reset_selected_btn = QPushButton("Reset Sel")
        self.play_selected_btn.setEnabled(False)
        self.pause_selected_btn.setEnabled(False)
        self.reset_selected_btn.setEnabled(False)

        left = QWidget()
        left.setMinimumWidth(160)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.strand_list)

        toolbar_row = QHBoxLayout()
        toolbar_row.addWidget(self.play_all_btn)
        toolbar_row.addWidget(self.pause_all_btn)
        toolbar_row.addWidget(self.reset_all_btn)

        transport_separator = QFrame()
        transport_separator.setFrameShape(QFrame.VLine)
        transport_separator.setFrameShadow(QFrame.Sunken)
        toolbar_row.addWidget(transport_separator)

        toolbar_row.addWidget(self.play_selected_btn)
        toolbar_row.addWidget(self.pause_selected_btn)
        toolbar_row.addWidget(self.reset_selected_btn)
        toolbar_row.addStretch(1)

        # Transport row + strand-settings strip both have a real minimum
        # width (6 buttons; 6 fields) that can exceed a narrowed center
        # column once the splitter is dragged -- or even at default size on
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
        controls_scroll.setFixedHeight(controls.sizeHint().height() + 4)
        controls_scroll.setFrameShape(QScrollArea.NoFrame)

        center = QWidget()
        center.setMinimumWidth(280)
        center_layout = QVBoxLayout(center)
        center_layout.addWidget(controls_scroll)
        center_layout.addWidget(self.canvas, 1)

        right = QScrollArea()
        right.setWidget(self.inspector)
        right.setWidgetResizable(True)
        right.setMinimumWidth(260)

        # QSplitter lets every column be resized by dragging its edge --
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
        splitter.setSizes([220, 820, 410])
        self.setCentralWidget(splitter)

        self.canvas.set_sessions(self.sessions)

        self.strand_list.add_requested.connect(self.add_strand)
        self.strand_list.remove_requested.connect(self.remove_strand)
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
        # Qt's own layout-driven size once the window is actually shown --
        # confirmed by measuring the real window rect at runtime, not assumed.
        # Wide enough that the strand-settings strip (6 fields, ~750px
        # minimum with real spinbox arrows) fits in the center column without
        # needing the controls_scroll fallback below.
        self.resize(1450, 750)

    # ------------------------------------------------------------------
    # File menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&File")
        menu.addAction("&New", self._file_new)
        menu.addAction("&Open...", self._file_open)
        menu.addSeparator()
        menu.addAction("&Save", self._file_save)
        menu.addAction("Save &As...", self._file_save_as)
        menu.addSeparator()
        menu.addAction("&Import...", self._file_import)

        export_menu = self.menuBar().addMenu("&Export")
        export_menu.addAction("Export Current Strand as C++...", self._export_save)
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
        suffix = f" -- {self._current_file_path.name}" if self._current_file_path else ""
        self.setWindowTitle(f"HitLib Pattern Studio{suffix}")

    # ------------------------------------------------------------------
    # C++ export
    # ------------------------------------------------------------------

    def _generate_export_or_warn(self) -> str | None:
        session = self._current_session()
        if session is None:
            QMessageBox.warning(self, "No Strand Selected", "Select a strand to export first.")
            return None
        self.inspector.save(session.config)  # make sure in-progress edits are flushed
        errors = validate_for_export(session.config)
        if errors:
            QMessageBox.critical(
                self, "Can't Export", "Fix these before exporting:\n\n" + "\n".join(f"- {e}" for e in errors)
            )
            return None
        return generate_cpp(session.config)

    def _export_save(self) -> None:
        code = self._generate_export_or_warn()
        if code is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export C++ Profile", "", _CPP_FILE_FILTER)
        if not path:
            return
        p = Path(path)
        if not p.suffix:
            p = p.with_suffix(".hpp")
        try:
            p.write_text(code, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Export Failed", f"Couldn't write {p}:\n{exc}")

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
        self._refresh_list()
        self.strand_list.select(len(self.sessions) - 1)
        return session

    def _clear_sessions(self) -> None:
        for session in self.sessions:
            session.stop()
            session.deleteLater()
        self.sessions.clear()
        self._current_index = -1
        self._refresh_list()
        self.canvas.update()

    def add_strand(self) -> None:
        n = len(self.sessions) + 1
        cfg = StrandConfig(name=f"Strand {n}", adi_port=min(n, 8))
        self._add_session(cfg)

    def remove_strand(self, row: int) -> None:
        if not (0 <= row < len(self.sessions)):
            return
        session = self.sessions.pop(row)
        session.stop()
        session.deleteLater()
        self._refresh_list()
        new_row = min(row, len(self.sessions) - 1)
        self.strand_list.select(new_row)
        # setCurrentRow(-1) is a no-op (and won't fire selection_changed) when
        # the row was already -1, which happens when the last strand is
        # removed -- call the handler directly so inspector/transport-button
        # enabled state doesn't go stale in that case.
        self._on_selection_changed(new_row)
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

    def _on_selection_changed(self, row: int) -> None:
        self._current_index = row
        session = self._current_session()
        has_selection = session is not None
        self.inspector.setEnabled(has_selection)
        self.play_selected_btn.setEnabled(has_selection)
        self.pause_selected_btn.setEnabled(has_selection)
        self.reset_selected_btn.setEnabled(has_selection)
        if session is not None:
            self.inspector.load(session.config)

    def _on_strand_settings_changed(self) -> None:
        session = self._current_session()
        if session is None:
            return
        self.inspector.save(session.config)
        session.rebuild()
        self._refresh_list()

    def _on_animation_changed(self) -> None:
        session = self._current_session()
        if session is None:
            return
        self.inspector.save(session.config)
        session.reapply_animation()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _play_all(self) -> None:
        # Also updates the default new-strand play state (see _add_session) --
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
        session = self._current_session()
        if session is not None:
            session.start()

    def _pause_selected(self) -> None:
        session = self._current_session()
        if session is not None:
            session.stop()

    def _reset_selected(self) -> None:
        session = self._current_session()
        if session is not None:
            session.rebuild()
