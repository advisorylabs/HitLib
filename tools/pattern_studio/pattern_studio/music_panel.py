"""The Song bar: load a track, hear it, scrub it, and shape how it drives the strip.

The song is document-level (see models.MusicConfig), so this panel sits under
the preview canvas rather than in the per-strand inspector: one song, one
transport, and every strand whose animation is Music Sync fills to it - each
picking its own band, so one strip can pump on the kick while another sparkles
on the hats.

Audio files play through Qt Multimedia while the preview runs, and when they do
the media player's own clock drives the transport. Watching the lights while
hearing the track is the only way to judge whether the settings are right, and
a separate timer would drift away from the audio within a few bars.

Three signals go out. `song_changed` means the tables were replaced (a new
file, or a shaping control moved) and the engine strands need re-issuing;
`position_changed` is the transport moving, which only needs a seek; and
`playing_changed` tells the strands whether to advance the song on their own
between those seeks, which is what keeps a paused transport actually paused.

Collapsed to a single row until a file is loaded, so an unused Song bar costs
the preview one row of height rather than a panel's worth.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .audio import AUDIO_SUFFIXES, AudioError, analyse_audio, is_audio_file
from .envelope import (
    BAND_BASS,
    ENVELOPE_MODE_HELP,
    ENVELOPE_MODE_LABELS,
    EnvelopeMode,
    bake,
)
from .midi import MidiError, MidiSong, analyse_midi, read_midi
from .models import MusicConfig
from .widgets import enum_data

_AUDIO_PATTERN = " ".join(f"*{s}" for s in AUDIO_SUFFIXES)
_FILE_FILTER = (
    f"Audio and MIDI ({_AUDIO_PATTERN} *.mid *.midi);;"
    f"Audio ({_AUDIO_PATTERN});;"
    "MIDI (*.mid *.midi);;"
    "All Files (*)"
)

SOURCE_AUDIO = "audio"
SOURCE_MIDI = "midi"


def format_time(ms: float, *, tenths: bool = False) -> str:
    """m:ss, or m:ss.t for the moving readout - a whole-second playhead on a
    song that fills the strip in fractions of a beat reads as stuck."""
    total = max(0.0, ms) / 1000.0
    minutes, seconds = divmod(total, 60)
    if tenths:
        return f"{int(minutes)}:{seconds:04.1f}"
    return f"{int(minutes)}:{int(seconds):02d}"


def _flexible(label: QLabel) -> QLabel:
    """Let a label be squeezed below its text width.

    A QLabel's minimum size is its text, so the song title in the header row
    would otherwise be the thing setting the panel's minimum width - and
    through it, how narrow the window can be dragged. A clipped filename is the
    better trade.
    """
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return label


def _ms_spin(low: int, high: int, step: int) -> QSpinBox:
    """A millisecond field sized to its own digits. Styled spin boxes take their
    width from the stylesheet rather than their contents, so left to themselves
    a row of them overflows the column."""
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setSingleStep(step)
    spin.setSuffix(" ms")
    spin.setFixedWidth(92)
    return spin


def _pct_spin(low: int, high: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setSingleStep(5)
    spin.setSuffix(" %")
    spin.setFixedWidth(78)
    return spin


class EnvelopeScrubber(QWidget):
    """The baked envelope drawn as a waveform, with a draggable playhead.

    Showing the shape (rather than a bare slider) is most of the point: it is
    how you find the drop you want the strip to hit, and it makes the effect of
    the shaping controls visible while you turn them.
    """

    seek_requested = Signal(int)

    HEIGHT = 58
    PAD = 8
    #: Additive strokes along the played envelope's silhouette, widest first:
    #: (pen width, alpha). Same trick the canvas uses for lit rows - the fill
    #: stays flat and the light comes from passes around it.
    GLOW = ((7.0, 22), (3.0, 40))
    #: Additive passes behind the playhead, widest first.
    HEAD_GLOW = ((9.0, 30), (4.0, 55))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.HEIGHT)
        self.setMaximumHeight(self.HEIGHT)
        self.setCursor(Qt.SizeHorCursor)
        self._samples: list[int] = []
        self._frame_ms = 25
        self._position_ms = 0
        #: One 0..1 peak per pixel column, rebuilt only when the samples or the
        #: width change - not on every repaint, which happens 30 times a second.
        self._peaks: list[float] = []
        self._peaks_width = -1

    def set_envelope(self, samples: list[int], frame_ms: int) -> None:
        self._samples = samples
        self._frame_ms = max(1, frame_ms)
        self._peaks_width = -1
        self.update()

    def set_position(self, ms: int) -> None:
        self._position_ms = ms
        self.update()

    @property
    def duration_ms(self) -> int:
        return len(self._samples) * self._frame_ms

    def _plot_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)

    def _ms_at(self, x: float) -> int:
        plot = self._plot_rect()
        if plot.width() <= 0 or not self._samples:
            return 0
        fraction = (x - plot.left()) / plot.width()
        return int(max(0.0, min(1.0, fraction)) * self.duration_ms)

    def _build_peaks(self, columns: int) -> None:
        """Collapse the samples onto one value per pixel column.

        The column's *maximum*, not its mean: a single-frame hit in an otherwise
        quiet stretch is exactly what you are looking for when scrubbing, and
        averaging would erase it.
        """
        n = len(self._samples)
        peaks = []
        for c in range(columns):
            lo = c * n // columns
            hi = max(lo + 1, (c + 1) * n // columns)
            peaks.append(max(self._samples[lo:hi]) / 255.0)
        self._peaks = peaks
        self._peaks_width = columns

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._samples:
            self.seek_requested.emit(self._ms_at(event.position().x()))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._samples and event.buttons() & Qt.LeftButton:
            self.seek_requested.emit(self._ms_at(event.position().x()))

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.setBrush(QBrush(QColor(theme.CANVAS_BG)))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)

        plot = self._plot_rect()
        if not self._samples or plot.width() < 2:
            painter.setPen(QColor(theme.CANVAS_EMPTY_TEXT))
            painter.drawText(self.rect(), Qt.AlignCenter, "No song loaded")
            return

        columns = int(plot.width())
        if columns != self._peaks_width:
            self._build_peaks(columns)

        area, ridge = self._envelope_shapes(plot)
        played_x = plot.left() + plot.width() * (
            min(self._position_ms, self.duration_ms) / max(1, self.duration_ms)
        )

        # The whole song first, dimmed: what is still ahead. Then the part
        # already played, at full strength and lit, so the playhead reads as a
        # boundary between two states instead of a line lying on top of one.
        upcoming = QColor(theme.FOCUS)
        upcoming.setAlpha(70)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(upcoming))
        painter.drawPolygon(area)

        painter.save()
        painter.setClipRect(QRectF(plot.left(), 0, played_x - plot.left(), self.height()))
        painter.setBrush(QBrush(QColor(theme.FOCUS)))
        painter.drawPolygon(area)
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        painter.setBrush(Qt.NoBrush)
        for width, alpha in self.GLOW:
            tint = QColor(theme.FOCUS)
            tint.setAlpha(alpha)
            painter.setPen(QPen(tint, width))
            painter.drawPath(ridge)
        painter.restore()

        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        for width, alpha in self.HEAD_GLOW:
            tint = QColor(theme.ACCENT)
            tint.setAlpha(alpha)
            painter.setPen(QPen(tint, width))
            painter.drawLine(int(played_x), int(plot.top()), int(played_x), int(plot.bottom()))
        painter.restore()
        painter.setPen(QPen(QColor(theme.ACCENT_HI), 1.4))
        painter.drawLine(int(played_x), int(plot.top()), int(played_x), int(plot.bottom()))

    def _envelope_shapes(self, plot: QRectF) -> tuple[QPolygonF, QPainterPath]:
        """The envelope as a filled area and as its top edge alone.

        Two shapes from one pass of the peaks: the polygon is the body, the path
        is what the glow strokes, so the light follows the waveform's silhouette
        rather than boxing it in.
        """
        base = plot.bottom()
        points = [
            QPointF(plot.left() + i, base - peak * plot.height())
            for i, peak in enumerate(self._peaks)
        ]
        area = QPolygonF([QPointF(plot.left(), base), *points, QPointF(points[-1].x(), base)])
        ridge = QPainterPath()
        ridge.moveTo(points[0])
        for point in points[1:]:
            ridge.lineTo(point)
        return area, ridge


class MusicPanel(QWidget):
    song_changed = Signal()
    position_changed = Signal(int)
    playing_changed = Signal(bool)

    #: Transport repaint/advance rate. Faster than any strand's refresh, so the
    #: position a strand samples is never more than one of its own ticks stale.
    FRAME_MS = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("musicPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._config = MusicConfig()
        #: The parsed MIDI, when the source is one. Audio needs no equivalent:
        #: its analysis is the only thing re-baking depends on.
        self._song: MidiSong | None = None
        self._position_ms = 0
        self._loading = False
        #: Which band the scrubber draws. Follows the selected strand, so the
        #: waveform under the preview is the one that strand is actually filling
        #: to rather than an arbitrary band.
        self._preview_band = BAND_BASS

        # Fallback clock, used when there is no audio to play (a MIDI source).
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._advance)

        self._audio_out = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.mediaStatusChanged.connect(self._on_media_status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        layout.addLayout(self._build_header())

        self.scrubber = EnvelopeScrubber()
        self.scrubber.seek_requested.connect(self._on_seek)
        layout.addWidget(self.scrubber)

        self.settings = self._build_settings()
        layout.addWidget(self.settings)

        self._update_enabled()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        heading = QLabel("SONG")
        heading.setProperty("role", "sectionHeader")
        row.addWidget(heading)

        self.load_btn = QPushButton(" Load Song...")
        self.load_btn.setToolTip(
            "Analyse an audio file (MP3, M4A, FLAC, OGG, WAV) or a MIDI, and\n"
            "bake it into envelopes the strip can fill to"
        )
        self.load_btn.clicked.connect(self._open_file)
        row.addWidget(self.load_btn)

        self.play_btn = QPushButton(theme.icon("play"), "")
        self.play_btn.setProperty("role", "icon")
        self.play_btn.setIconSize(QSize(13, 13))
        self.play_btn.setToolTip("Play / pause")
        self.play_btn.clicked.connect(self._toggle_play)
        theme.HoverBloom(self.play_btn, theme.FOCUS, radius=14, alpha=105)
        row.addWidget(self.play_btn)

        self.rewind_btn = QPushButton(theme.icon("reset"), "")
        self.rewind_btn.setProperty("role", "icon")
        self.rewind_btn.setIconSize(QSize(13, 13))
        self.rewind_btn.setToolTip("Back to the start")
        self.rewind_btn.clicked.connect(lambda: self._on_seek(0))
        theme.HoverBloom(self.rewind_btn, theme.FOCUS, radius=14, alpha=105)
        row.addWidget(self.rewind_btn)

        self.mute_check = QCheckBox("Mute")
        self.mute_check.setToolTip("Watch the strip without hearing the track")
        self.mute_check.toggled.connect(self._audio_out.setMuted)
        row.addWidget(self.mute_check)

        self.title_label = _flexible(QLabel("No song loaded"))
        self.title_label.setProperty("role", "hint")
        row.addWidget(self.title_label, 1)

        self.time_label = QLabel("0:00.0 / 0:00")
        self.time_label.setProperty("role", "hint")
        row.addWidget(self.time_label)

        self.settings_btn = QPushButton(theme.icon("chevron-down"), " Settings")
        self.settings_btn.setCheckable(True)
        self.settings_btn.setToolTip("How the song is turned into a fill")
        self.settings_btn.toggled.connect(self._on_settings_toggled)
        row.addWidget(self.settings_btn)
        return row

    def _build_settings(self) -> QWidget:
        box = QWidget()
        box.setVisible(False)
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.mode_combo = QComboBox()
        for mode in EnvelopeMode:
            self.mode_combo.addItem(ENVELOPE_MODE_LABELS[mode], mode)
        self.mode_combo.setToolTip("\n".join(ENVELOPE_MODE_HELP.values()))
        self.mode_combo.setFixedWidth(88)
        self.attack_spin = _ms_spin(0, 2000, 5)
        self.attack_spin.setToolTip("How long the fill takes to climb from empty to full")
        self.release_spin = _ms_spin(0, 5000, 10)
        self.release_spin.setToolTip("How long the fill takes to fall from full to empty")
        self.contrast_spin = _pct_spin(50, 200)
        self.contrast_spin.setToolTip(
            "Bends the auto-fitted curve. Above 100 darkens the quiet frames for a\n"
            "punchier look; below 100 lifts them so the strip is busier."
        )
        self.gain_spin = _pct_spin(0, 100)
        self.gain_spin.setToolTip(
            "How far to normalise against the last few seconds instead of the whole\n"
            "track, so a quiet verse still uses the strip and a loud chorus still\n"
            "has somewhere left to go."
        )
        self.frame_spin = _ms_spin(5, 200, 5)
        self.frame_spin.setToolTip(
            "Time between exported samples. Coarser means a smaller table in the\n"
            "generated header; the firmware interpolates between frames either way."
        )
        self.loop_check = QCheckBox("Loop")
        self.loop_check.setToolTip("Repeat the song instead of going dark at the end")
        self.size_label = QLabel()
        self.size_label.setProperty("role", "hint")

        # One row of labelled fields rather than a form, with the MIDI track
        # list under it: the Song bar borrows height from the preview while it
        # is open, and a form of nine rows took a third of the canvas.
        fields = QHBoxLayout()
        fields.setSpacing(6)
        for label, widget in (
            ("Follow", self.mode_combo),
            ("Attack", self.attack_spin),
            ("Release", self.release_spin),
            ("Contrast", self.contrast_spin),
            ("Auto-gain", self.gain_spin),
            ("Frame", self.frame_spin),
        ):
            fields.addWidget(QLabel(label))
            fields.addWidget(widget)
        fields.addWidget(self.loop_check)
        fields.addStretch(1)
        fields.addWidget(self.size_label)

        fields_widget = QWidget()
        fields_widget.setLayout(fields)
        fields_scroll = QScrollArea()
        fields_scroll.setWidget(fields_widget)
        fields_scroll.setWidgetResizable(True)
        fields_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        fields_scroll.setFrameShape(QScrollArea.NoFrame)
        # Same trade the controls strip above the canvas makes: reserve the
        # scrollbar's height up front, so a bar appearing on a narrow window
        # eats reserved space instead of clipping the fields themselves.
        fields_scroll.setFixedHeight(
            fields_widget.sizeHint().height()
            + fields_scroll.horizontalScrollBar().sizeHint().height()
        )
        outer.addWidget(fields_scroll)

        # MIDI only: audio has no parts to pick from.
        self.tracks_widget = QWidget()
        tracks_layout = QVBoxLayout(self.tracks_widget)
        tracks_layout.setContentsMargins(0, 0, 0, 0)
        tracks_layout.setSpacing(4)
        tracks_label = QLabel("MIDI TRACKS")
        tracks_label.setProperty("role", "sectionHeader")
        tracks_layout.addWidget(tracks_label)
        self.track_list = QListWidget()
        self.track_list.setMaximumHeight(76)
        self.track_list.itemChanged.connect(self._on_tracks_changed)
        tracks_layout.addWidget(self.track_list)
        outer.addWidget(self.tracks_widget)

        for widget, signal_name in (
            (self.mode_combo, "currentIndexChanged"),
            (self.attack_spin, "valueChanged"),
            (self.release_spin, "valueChanged"),
            (self.contrast_spin, "valueChanged"),
            (self.gain_spin, "valueChanged"),
            (self.frame_spin, "valueChanged"),
        ):
            getattr(widget, signal_name).connect(self._on_shaping_changed)
        self.loop_check.toggled.connect(self._on_loop_changed)
        return box

    # ------------------------------------------------------------------
    # Binding
    # ------------------------------------------------------------------

    def load(self, music: MusicConfig) -> None:
        """Bind a document's MusicConfig. Edits mutate it in place, the way the
        other panels treat their nested configs."""
        self._loading = True
        self._config = music
        self._song = None
        self._position_ms = 0
        self._stop()

        # A design saved with a MIDI remembers where it came from; re-reading it
        # puts the track list back in play. Audio needs nothing: its saved
        # analysis is all re-baking depends on.
        if music.source_kind == SOURCE_MIDI and music.source_path:
            if Path(music.source_path).is_file():
                try:
                    self._song = read_midi(music.source_path)
                except MidiError:
                    self._song = None

        settings = music.settings
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(settings.mode)))
        self.attack_spin.setValue(settings.attack_ms)
        self.release_spin.setValue(settings.release_ms)
        self.contrast_spin.setValue(settings.contrast)
        self.gain_spin.setValue(settings.auto_gain)
        self.frame_spin.setValue(settings.frame_ms)
        self.loop_check.setChecked(music.loop)
        self._refresh_track_list()
        self._attach_audio()
        self._loading = False
        # Tables are never saved, so a freshly opened design has an analysis and
        # no envelopes until this runs.
        if music.analysis.loaded and not music.bands:
            self.rebake(announce=False)
        self._refresh_view()

    def set_preview_band(self, band: str) -> None:
        """Draw `band` in the scrubber - the band the selected strand fills to."""
        if band and band != self._preview_band:
            self._preview_band = band
            self._refresh_view()

    @property
    def position_ms(self) -> int:
        return self._position_ms

    @property
    def playing(self) -> bool:
        return self._timer.isActive()

    # ------------------------------------------------------------------
    # Loading and baking
    # ------------------------------------------------------------------

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load Song", "", _FILE_FILTER)
        if not path:
            return
        self._stop()
        if is_audio_file(path):
            self._load_audio(Path(path))
        else:
            self._load_midi(Path(path))

    def _load_audio(self, path: Path) -> None:
        progress = QProgressDialog(f"Analysing {path.name}...", "Cancel", 0, 100, self)
        progress.setWindowTitle("Loading Song")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def tick(fraction: float) -> None:
            progress.setValue(int(fraction * 100))
            # The analysis runs on this thread, so without pumping the loop the
            # dialog would be a frozen rectangle for the whole of it.
            QApplication.processEvents()

        try:
            analysis = analyse_audio(path, on_progress=tick)
        except AudioError as exc:
            progress.close()
            QMessageBox.critical(self, "Couldn't Read Audio", str(exc))
            return
        finally:
            progress.close()

        if not analysis.loaded:
            QMessageBox.warning(self, "Silent Track", f"{path.name} decoded, but it is silent.")
            return

        self._song = None
        self._config.source_kind = SOURCE_AUDIO
        self._adopt(analysis, path)

    def _load_midi(self, path: Path) -> None:
        try:
            song = read_midi(path)
        except MidiError as exc:
            QMessageBox.critical(self, "Couldn't Read MIDI", str(exc))
            return
        if not song.playable_tracks:
            QMessageBox.warning(
                self, "No Notes", f"{path.name} parsed, but none of its tracks contain notes."
            )
            return

        self._song = song
        self._config.source_kind = SOURCE_MIDI
        # Everything with notes to start with: a first look at the whole song is
        # more useful than an empty strip you have to go tick boxes to fill.
        self._config.tracks = list(song.playable_tracks)
        self._loading = True
        self._refresh_track_list()
        self._loading = False
        self._adopt(analyse_midi(song, tracks=self._config.tracks), path)

    def _adopt(self, analysis, path: Path) -> None:
        self._config.analysis = analysis
        self._config.source_path = str(path)
        self._config.name = analysis.name or path.stem
        self._position_ms = 0
        self._attach_audio()
        self.rebake()

    def rebake(self, *, announce: bool = True) -> None:
        """Re-render every band's table from the analysis and current settings.

        Runs on every control change, which is why the expensive part is the
        analysis and not this: it only touches the frame arrays.
        """
        analysis = self._config.analysis
        self._config.bands = {
            band: bake(analysis, band, self._config.settings) for band in analysis.bands
        }
        self._position_ms = min(self._position_ms, self._config.duration_ms)
        self._refresh_view()
        if announce:
            self.song_changed.emit()

    def _attach_audio(self) -> None:
        """Point the media player at the source, if it is something playable."""
        path = self._config.source_path
        playable = (
            self._config.source_kind == SOURCE_AUDIO and path and Path(path).is_file()
        )
        self._player.setSource(QUrl.fromLocalFile(path) if playable else QUrl())

    @property
    def _has_audio(self) -> bool:
        return not self._player.source().isEmpty()

    def _refresh_track_list(self) -> None:
        self.track_list.blockSignals(True)
        self.track_list.clear()
        if self._song is not None:
            for index in self._song.playable_tracks:
                info = self._song.tracks[index]
                item = QListWidgetItem(info.label)
                item.setData(Qt.UserRole, index)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if index in self._config.tracks else Qt.Unchecked)
                self.track_list.addItem(item)
        self.track_list.blockSignals(False)

    def _on_tracks_changed(self, _item: QListWidgetItem) -> None:
        if self._loading or self._song is None:
            return
        chosen = []
        for row in range(self.track_list.count()):
            item = self.track_list.item(row)
            if item.checkState() == Qt.Checked:
                chosen.append(item.data(Qt.UserRole))
        self._config.tracks = chosen
        # Track choice changes the loudness itself, so this one re-analyses.
        # Cheap for MIDI - there is no audio to decode.
        self._config.analysis = analyse_midi(self._song, tracks=chosen)
        self.rebake()

    def _on_shaping_changed(self, *_args) -> None:
        if self._loading:
            return
        settings = self._config.settings
        settings.mode = enum_data(self.mode_combo, EnvelopeMode)
        settings.attack_ms = self.attack_spin.value()
        settings.release_ms = self.release_spin.value()
        settings.contrast = self.contrast_spin.value()
        settings.auto_gain = self.gain_spin.value()
        settings.frame_ms = self.frame_spin.value()
        self.rebake()

    def _on_loop_changed(self, checked: bool) -> None:
        # Not a shaping control: looping changes the musicSync() call, not the
        # tables, so there is nothing to re-render.
        if self._loading:
            return
        self._config.loop = checked
        self.song_changed.emit()

    def _on_settings_toggled(self, checked: bool) -> None:
        self.settings.setVisible(checked)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _toggle_play(self) -> None:
        if self._timer.isActive():
            self._stop()
        elif self._config.loaded:
            if self._position_ms >= self._config.duration_ms:
                self._position_ms = 0
            self._clock.restart()
            if self._has_audio:
                self._player.setPosition(self._position_ms)
                self._player.play()
            self._timer.start()
            self.play_btn.setIcon(theme.icon("pause"))
            self.playing_changed.emit(True)
            self.position_changed.emit(self._position_ms)

    def _stop(self) -> None:
        self._timer.stop()
        self._player.pause()
        self.play_btn.setIcon(theme.icon("play"))
        self.playing_changed.emit(False)

    def _advance(self) -> None:
        # With audio loaded the media player's clock is authoritative: the
        # lights have to stay with what is being heard, and a separate timer
        # would drift away from it within a few bars. Without audio, wall-clock
        # elapsed time - counting timer firings would play the song slow
        # whenever a repaint runs long.
        if self._has_audio and self._player.playbackState() == QMediaPlayer.PlayingState:
            self._position_ms = self._player.position()
        else:
            self._position_ms += self._clock.restart()

        duration = self._config.duration_ms
        if self._position_ms >= duration:
            if self._config.loop and duration > 0:
                self._position_ms %= duration
                if self._has_audio:
                    self._player.setPosition(self._position_ms)
                    self._player.play()
            else:
                self._position_ms = duration
                self._stop()
        self._refresh_position()

    def _on_media_status(self, status) -> None:
        # The audio can end before the envelope does (the tables run a release
        # tail past the last sound). Looping is handled in _advance against the
        # envelope's length, so all this has to do is not leave the player
        # stopped at the end of a loop.
        if status == QMediaPlayer.EndOfMedia and self._config.loop and self._timer.isActive():
            self._player.setPosition(0)
            self._player.play()

    def _on_seek(self, ms: int) -> None:
        self._position_ms = max(0, min(ms, self._config.duration_ms))
        self._clock.restart()
        if self._has_audio:
            self._player.setPosition(self._position_ms)
        self._refresh_position()

    def _refresh_position(self) -> None:
        self.scrubber.set_position(self._position_ms)
        self.time_label.setText(
            f"{format_time(self._position_ms, tenths=True)} / "
            f"{format_time(self._config.duration_ms)}"
        )
        self.position_changed.emit(self._position_ms)

    # ------------------------------------------------------------------
    # View state
    # ------------------------------------------------------------------

    def _refresh_view(self) -> None:
        self.scrubber.set_envelope(self._config.table(self._preview_band), self._config.frame_ms)

        if self._config.loaded:
            self.title_label.setText(self._config.name or "Untitled song")
            self.title_label.setProperty("role", None)
        else:
            self.title_label.setText("No song loaded")
            self.title_label.setProperty("role", "hint")
        # A property change only takes effect once the widget is restyled.
        self.title_label.style().unpolish(self.title_label)
        self.title_label.style().polish(self.title_label)

        frames = sum(len(t) for t in self._config.bands.values() if t)
        self.size_label.setText(
            f"{len(self._config.bands)} bands, {frames / 1024:.1f} KB if every band is used."
        )
        self._update_enabled()
        self._refresh_position()

    def _update_enabled(self) -> None:
        loaded = self._config.loaded
        can_bake = self._config.analysis.loaded
        # A parsed MIDI counts too: unchecking its last track empties the
        # analysis, and closing the pane on that would leave no way back.
        can_edit = can_bake or self._song is not None

        self.scrubber.setVisible(loaded)
        self.play_btn.setEnabled(loaded)
        self.rewind_btn.setEnabled(loaded)
        self.mute_check.setVisible(self._has_audio)
        self.settings_btn.setEnabled(can_edit)
        if not can_edit:
            self.settings_btn.setChecked(False)

        for widget in (
            self.mode_combo, self.attack_spin, self.release_spin,
            self.contrast_spin, self.gain_spin, self.frame_spin,
        ):
            widget.setEnabled(can_bake)
        # Only a MIDI has parts to choose between, and only while we still have
        # the file to re-read them from.
        self.tracks_widget.setVisible(self._song is not None)
