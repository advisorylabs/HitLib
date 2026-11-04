"""Owns one strand's engine instance and its own refresh-rate QTimer.

Each strand gets its own QTimer at its configured refresh_ms, so preview speed
matches hardware. A shared timer would tick strands with different refresh_ms
values at the wrong relative speed.

The timer is a precise one. Every tick adds exactly refresh_ms to the sim
clock, so the preview is only as fast as the timer is punctual - and Windows
serves a coarse timer above 20 ms from its 15.6 ms system tick, which fired a
25 ms strand every ~31 ms and played it at 79 % of the robot's speed.

A chain is one engine strand across several strands' strips, as it is on the
robot. Its head's session owns that strand and ticks it; every other session
in the chain follows the head - it has no engine strand ticking of its own,
draws its slice of the head's frame, and hands play, pause and reset to it.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from hitlib_sim import DriverKind

from .engine import (
    MusicBinding,
    apply_phase,
    apply_strand_config,
    make_strand,
    make_transition,
)
from .models import CompositionConfig, StrandConfig, chain_length

#: How long Preview Transition shows the phase it wipes away from first, so
#: there is a frame worth wiping - a pulse or a twinkle starts out dark.
PREVIEW_LEAD_MS = 1000

#: How long it holds the incoming phase once the wipe is done.
PREVIEW_HOLD_MS = 1000


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
        #: The strands chained on after this one, when it heads a chain.
        self.chained: list[StrandConfig] = []
        #: The session this one follows, when it is chained onto another, and
        #: where its strip starts in that session's frame.
        self.head: StrandSession | None = None
        self.offset = 0
        self.strand = make_strand(config, music, self.compositions)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(max(1, config.refresh_ms))
        self.timer.timeout.connect(self._on_timeout)
        # A Preview Transition under way: what is left of it, as [ticks to
        # go, what to do then] stages, and whether the strand was playing
        # before it - a preview plays a paused strand, and pauses it again.
        self._preview: list[list] | None = None
        self._preview_resume = False

    def _on_timeout(self) -> None:
        self.strand.tick()
        if self._preview:
            stage = self._preview[0]
            stage[0] -= 1
            if stage[0] <= 0:
                self._preview.pop(0)
                stage[1]()
        self.ticked.emit()

    def start(self) -> None:
        if self.head is not None:
            self.head.start()
            return
        if self._preview is not None:
            self._preview_resume = True
        self.timer.start()

    def stop(self) -> None:
        if self.head is not None:
            self.head.stop()
            return
        if self._preview is not None:
            # Paused mid-preview, the strip goes back to what it plays rather
            # than freezing on half a wipe it is not part of.
            self._preview = None
            apply_strand_config(self.strand, self.config, self.music, self.compositions)
            self.ticked.emit()
        self.timer.stop()

    @property
    def running(self) -> bool:
        if self.head is not None:
            return self.head.running
        if self._preview is not None:
            return self._preview_resume
        return self.timer.isActive()

    @property
    def previewing(self) -> bool:
        return self._preview is not None

    # ------------------------------------------------------------------
    # Preview Transition
    # ------------------------------------------------------------------

    def preview_transition(self, mode_idx: int, phase_idx: int) -> bool:
        """Play phase `phase_idx` of mode `mode_idx` wiping in over the phase
        before it - now, rather than whenever the loop next gets there - and
        then go back to what the strand plays.

        The phase before is the one above it in the list, or the last for
        the first, as the loop goes round. False, doing nothing, when there
        is no wipe to see: a cut, a mode of one phase (which never wipes into
        itself), or a strand that plays its head's animation.
        """
        if self.head is not None or not self.config.use_profile:
            return False
        modes = self.config.profile_modes
        if not 0 <= mode_idx < len(modes):
            return False
        phases = modes[mode_idx].phases
        if len(phases) < 2 or not 0 <= phase_idx < len(phases):
            return False
        into, before = phases[phase_idx], phases[phase_idx - 1]
        transition = make_transition(into.transition)
        if transition is None:
            return False
        resume = self.running
        strand = self.strand
        strand.detach_profile()
        # Cut short whatever wipe the strand was in, so the phase before
        # starts out whole.
        strand.transition(type(transition)())
        apply_phase(strand, before, self.music, self.compositions)

        def wipe() -> None:
            self.strand.transition(transition)
            apply_phase(self.strand, into, self.music, self.compositions)

        self._preview = [
            [self._ticks(PREVIEW_LEAD_MS), wipe],
            [self._ticks(into.transition.duration_ms + PREVIEW_HOLD_MS), self._end_preview],
        ]
        self._preview_resume = resume
        self.timer.start()
        self.ticked.emit()
        return True

    def _ticks(self, ms: int) -> int:
        return max(1, -(-ms // max(1, self.config.refresh_ms)))

    def _end_preview(self) -> None:
        resume = self._preview_resume
        self._preview = None
        apply_strand_config(self.strand, self.config, self.music, self.compositions)
        if not resume:
            self.timer.stop()

    def _drop_preview(self) -> bool:
        """Forget a preview the caller is about to replace the strand's state
        under. Whether the strand was playing before it - or is now, without
        one."""
        if self._preview is None:
            return self.timer.isActive()
        self._preview = None
        if not self._preview_resume:
            self.timer.stop()
        return self._preview_resume

    @property
    def length(self) -> int:
        """How many LEDs this row of the preview draws: its own strip's."""
        return self.config.length

    @property
    def chain_length(self) -> int:
        """How many pixels the animation this strand shows plays across: the
        whole chain's, which is what a mask's regions are placed along.
        Worked out from the configs rather than read off the engine strand,
        which a Length edit has not rebuilt yet when this is asked."""
        lead = self.head or self
        return chain_length([lead.config, *lead.chained])

    def pixels(self) -> list[int]:
        """What this strand's own strip shows, LED 0 first: the whole frame
        for a strand on its own, and its slice of the chain's frame
        otherwise."""
        if self.head is not None:
            frame = self.head.strand.pixels[self.offset:self.offset + self.config.length]
        else:
            frame = self.strand.pixels[:self.config.length]
        # A chain past MAX_CHAIN_LEDS drops its last pixels, as the robot does;
        # those LEDs draw dark rather than the strip drawing short.
        return frame + [0] * (self.config.length - len(frame))

    # ------------------------------------------------------------------
    # Chains
    # ------------------------------------------------------------------

    def _chain_shape(self) -> tuple:
        """What the engine strand is built from, so a relink that changes
        nothing about it costs nothing."""
        return tuple((c.adi_port, c.smart_port, c.length, c.reversed)
                     for c in [self.config, *self.chained])

    def lead(self, chained: list[StrandConfig]) -> bool:
        """Head a chain of `chained` - or, given none, play on its own -
        rebuilding the engine strand only if its strips changed. Whether it
        did: a new engine strand starts its song from the top, so the caller
        has to put it back where the Song bar is."""
        was_following = self.head is not None
        head = self.head
        self.head = None
        self.offset = 0
        before = self._chain_shape()
        self.chained = list(chained)
        if was_following:
            # It has been dormant while it followed. Start it afresh, playing
            # or not as the chain it left was.
            self.strand = make_strand(self.config, self.music, self.compositions,
                                      self.chained)
            # Its own rate again: a group edit can have moved refresh_ms while
            # it ran at its head's.
            self.timer.setInterval(max(1, self.config.refresh_ms))
            if head is not None and head.running:
                self.timer.start()
            self.ticked.emit()
            return True
        if self._chain_shape() != before:
            self.rebuild()
            return True
        return False

    def dispose(self) -> None:
        """Stop for good, as the strand is removed. Only this session's own
        timer: stop() on a chained strand stops its head, and what is left of
        the chain plays on without the strip taken out of it."""
        self._preview = None
        self.timer.stop()
        self.head = None

    def follow(self, head: "StrandSession", offset: int) -> None:
        """Become part of `head`'s chain, drawing from `offset` in its frame."""
        self._preview = None
        self.timer.stop()
        self.chained = []
        self.head = head
        self.offset = offset

    def rebuild(self) -> None:
        """Strand-level settings (length/port/refresh_ms) changed - recreate the engine strand."""
        if self.head is not None:
            self.head.rebuild()
            return
        was_running = self._drop_preview()
        self.timer.stop()
        self.timer.setInterval(max(1, self.config.refresh_ms))
        self.strand = make_strand(self.config, self.music, self.compositions, self.chained)
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
        if self.head is not None:
            self.head.reset()
            return
        old = self.strand
        if old.music_track is not None:
            position_ms, paused = old.music_position_ms(), old.music_paused
        else:
            position_ms, paused = old.compose_music_position_ms(), old.compose_music_paused
        self._preview = None
        self.timer.stop()
        self.strand = make_strand(self.config, self.music, self.compositions, self.chained)
        self.pause_music(paused)
        self._seek_song(position_ms)
        self.ticked.emit()

    def reapply_animation(self) -> None:
        """Only animation/splice params changed - no need to recreate the Strand."""
        if self.head is not None:
            return  # what a chained strand plays is its head's, not its own
        # An edit lands on what the strand plays, so a preview gives way to it.
        self._drop_preview()
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
        if self.head is not None:
            # Its song is its head's: the strand it has of its own is idle.
            self.head.seek_music(position_ms)
            return
        if not self._seek_song(position_ms):
            return
        if not self.timer.isActive():
            self.strand.render()
            self.ticked.emit()

    def pause_music(self, paused: bool) -> None:
        """Hold or release this strand's song position, if it plays the song."""
        if self.head is not None:
            self.head.pause_music(paused)
            return
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
