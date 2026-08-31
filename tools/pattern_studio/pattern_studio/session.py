"""Owns one strand's engine instance and its own refresh-rate QTimer.

Each strand gets its own QTimer at its configured refresh_ms, so preview speed
matches hardware. A shared timer would tick strands with different refresh_ms
values at the wrong relative speed.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from .engine import MusicBinding, apply_strand_config, make_strand
from .models import StrandConfig


class StrandSession(QObject):
    ticked = Signal()

    def __init__(self, config: StrandConfig, parent=None, music: MusicBinding | None = None):
        super().__init__(parent)
        self.config = config
        # The document's baked song, shared by every session. A MUSIC
        # animation needs it at the moment its animation call is issued, so it
        # is held here rather than passed in at each rebuild.
        self.music = music
        self.strand = make_strand(config, music)
        self.timer = QTimer(self)
        self.timer.setInterval(max(1, config.refresh_ms))
        self.timer.timeout.connect(self._on_timeout)

    def _on_timeout(self) -> None:
        self.strand.tick()
        self.ticked.emit()

    def start(self) -> None:
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    @property
    def running(self) -> bool:
        return self.timer.isActive()

    def rebuild(self) -> None:
        """Strand-level settings (length/port/refresh_ms) changed - recreate the engine strand."""
        was_running = self.timer.isActive()
        self.timer.stop()
        self.timer.setInterval(max(1, self.config.refresh_ms))
        self.strand = make_strand(self.config, self.music)
        if was_running:
            self.timer.start()
        self.ticked.emit()

    def reset(self) -> None:
        """Stop and go dark.

        The engine strand is rebuilt so the next play starts from the
        animation's first frame, but nothing is composited until then: Reset
        means an unlit strip, not a jump to frame zero. Song position carries
        over rather than rewinding - that belongs to the Song bar's transport.
        """
        position_ms = self.strand.music_position_ms()
        paused = self.strand.music_paused
        self.timer.stop()
        self.strand = make_strand(self.config, self.music)
        self.strand.music_pause(paused)
        self.strand.music_seek(position_ms)
        self.ticked.emit()

    def reapply_animation(self) -> None:
        """Only animation/splice params changed - no need to recreate the Strand."""
        apply_strand_config(self.strand, self.config, self.music)
        self.ticked.emit()

    def set_music(self, music: MusicBinding | None) -> None:
        """Point this session at a (re)baked song and re-issue the animation,
        which hands the new envelope to the engine strand."""
        self.music = music
        self.reapply_animation()

    def seek_music(self, position_ms: int) -> None:
        """Move this strand's playback to `position_ms`.

        A paused session is re-rendered on the spot rather than left showing a
        stale frame, so scrubbing a stopped preview still moves the LEDs.
        """
        self.strand.music_seek(position_ms)
        if not self.timer.isActive():
            self.strand.render()
            self.ticked.emit()
