"""Owns one strand's engine instance and its own refresh-rate QTimer.

Each strand gets its own QTimer at its configured refresh_ms, so preview speed
matches hardware. A shared timer would tick strands with different refresh_ms
values at the wrong relative speed.

The timer is a precise one. Every tick adds exactly refresh_ms to the sim
clock, so the preview is only as fast as the timer is punctual - and Windows
serves a coarse timer above 20 ms from its 15.6 ms system tick, which fired a
25 ms strand every ~31 ms and played it at 79 % of the robot's speed.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from hitlib_sim import DriverKind

from .engine import MusicBinding, apply_strand_config, make_strand
from .models import CompositionConfig, StrandConfig


class StrandSession(QObject):
    ticked = Signal()

    def __init__(self, config: StrandConfig, parent=None, music: MusicBinding | None = None,
                 compositions: dict[int, CompositionConfig] | None = None):
        super().__init__(parent)
        self.config = config
        # The document's baked song, shared by every session. A MUSIC
        # animation needs it at the moment its animation call is issued, so it
        # is held here rather than passed in at each rebuild.
        self.music = music
        # The document's composition library, shared by every session for the
        # same reason the song is: a CUSTOM animation needs it at the moment
        # its animation call is issued.
        self.compositions = compositions or {}
        self.strand = make_strand(config, music, self.compositions)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
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
        self.strand = make_strand(self.config, self.music, self.compositions)
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
        old = self.strand
        if old.music_track is not None:
            position_ms, paused = old.music_position_ms(), old.music_paused
        else:
            position_ms, paused = old.compose_music_position_ms(), old.compose_music_paused
        self.timer.stop()
        self.strand = make_strand(self.config, self.music, self.compositions)
        self.pause_music(paused)
        self._seek_song(position_ms)
        self.ticked.emit()

    def reapply_animation(self) -> None:
        """Only animation/splice params changed - no need to recreate the Strand."""
        apply_strand_config(self.strand, self.config, self.music, self.compositions)
        self.ticked.emit()

    def set_compositions(self, compositions: dict[int, CompositionConfig],
                         reapply: bool = True) -> None:
        """Point this session at the document's library and re-issue the
        animation, which hands the new composition to the engine strand.

        `reapply=False` only repoints it, for a strand that plays no
        composition: re-issuing restarts the animation, and it would pick
        nothing up for it.
        """
        self.compositions = compositions
        if reapply:
            self.reapply_animation()

    def set_music(self, music: MusicBinding | None) -> None:
        """Point this session at a (re)baked song and re-issue the animation,
        which hands the new envelope to the engine strand."""
        self.music = music
        self.reapply_animation()

    def seek_music(self, position_ms: int) -> None:
        """Move this strand's playback to `position_ms`.

        A paused session is re-rendered on the spot rather than left showing a
        stale frame, so scrubbing a stopped preview still moves the LEDs. (A
        composition's frame is only worked out on a tick, so it shows the new
        position once it plays again.)

        Only a strand that plays the song is moved. The Song bar pushes its
        position into every session, and seeking one with no track samples
        nothing - it zeroed a Fill meter's level on every push.
        """
        if not self._seek_song(position_ms):
            return
        if not self.timer.isActive():
            self.strand.render()
            self.ticked.emit()

    def pause_music(self, paused: bool) -> None:
        """Hold or release this strand's song position, if it plays the song."""
        strand = self.strand
        if strand.music_track is not None:
            strand.music_pause(paused)
        elif self._composes_music():
            strand.compose_music_pause(paused)

    def _seek_song(self, position_ms: int) -> bool:
        """seek_music() without the repaint. False if nothing plays the song."""
        strand = self.strand
        if strand.music_track is not None:
            strand.music_seek(position_ms)
        elif self._composes_music():
            strand.compose_music_seek(position_ms)
        else:
            return False
        return True

    def _composes_music(self) -> bool:
        """Whether the strand plays a composition with a song-driven driver.

        A composition keeps its song position on its own clock, apart from
        Music Sync's. It is left alone when no driver plays the song: seeking
        that clock moves the composition's whole timeline with it.
        """
        comp = self.strand.composition
        return comp is not None and any(
            d.kind == DriverKind.MUSIC and d.track is not None for d in comp.drivers)
