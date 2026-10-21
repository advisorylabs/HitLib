"""Port of include/hitlib/led_sequencer.hpp + src/led_sequencer.cpp.

A Sequencer walks a looping list of timed Phases, calling each phase's
start_fn once when it begins. It must be advanced by calling update() once
per strand tick (typically from a ProfileMode's on_tick callback), it does
not advance on its own.

One Sequencer can run on several strands at once, each at its own phase: a
profile attached to a whole group shares its Sequencer between the group's
strands. It keeps one run per strand, up to MAX_STRANDS of them, exactly as
the firmware does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional, Sequence

if TYPE_CHECKING:
    from .strand import Strand

StartFn = Callable[["Strand"], None]


@dataclass(frozen=True)
class Phase:
    duration_ms: int
    start_fn: StartFn


@dataclass
class _Run:
    """Where one strand is in the sequence. Mirrors Sequencer::Run."""

    strand: "Strand"
    phase: int
    phase_start_ms: int
    last_touch: int


class Sequencer:
    #: Strands one Sequencer can run on at a time. Mirrors
    #: Sequencer::MAX_STRANDS.
    MAX_STRANDS = 8

    def __init__(self, phases: Sequence[Phase]):
        self.phases = list(phases)
        self._runs: list[_Run] = []
        # Bumped on every start() and update(), so the run touched longest ago
        # is the one with the smallest last_touch.
        self._touches = 0

    @property
    def is_running(self) -> bool:
        """Whether the sequence is running on any strand."""
        return bool(self._runs)

    def is_running_on(self, strand: "Strand") -> bool:
        return self._find(strand) is not None

    def current_phase_on(self, strand: "Strand") -> Optional[int]:
        """The phase @p strand is in, or None where it isn't running."""
        run = self._find(strand)
        return run.phase if run is not None else None

    def _find(self, strand: "Strand") -> Optional[_Run]:
        for run in self._runs:
            if run.strand is strand:
                return run
        return None

    def _touch(self) -> int:
        self._touches += 1
        return self._touches

    def start(self, strand: "Strand") -> None:
        run = self._find(strand)
        if run is None:
            run = _Run(strand, 0, 0, 0)
            if len(self._runs) < self.MAX_STRANDS:
                self._runs.append(run)
            else:
                # Full: take over the run whose strand has gone longest
                # without an update(), which is what the firmware does.
                stale = min(range(len(self._runs)), key=lambda i: self._runs[i].last_touch)
                self._runs[stale] = run
        run.phase = 0
        run.phase_start_ms = strand.now_ms
        run.last_touch = self._touch()
        if self.phases:
            self.phases[0].start_fn(strand)

    def stop(self, strand: Optional["Strand"] = None) -> None:
        """Stop on @p strand, or on every strand when none is given. The
        animation showing is left as it is."""
        if strand is None:
            self._runs.clear()
        else:
            self._runs = [run for run in self._runs if run.strand is not strand]

    def update(self, strand: "Strand") -> None:
        if not self.phases:
            return
        run = self._find(strand)
        if run is None:
            return
        run.last_touch = self._touch()
        now = strand.now_ms
        if now - run.phase_start_ms >= self.phases[run.phase].duration_ms:
            run.phase = (run.phase + 1) % len(self.phases)
            run.phase_start_ms = now
            self.phases[run.phase].start_fn(strand)
