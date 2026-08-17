"""Port of include/hitlib/led_sequencer.hpp + src/led_sequencer.cpp.

A Sequencer walks a looping list of timed Phases, calling each phase's
start_fn once when it begins. It must be advanced by calling update() once
per strand tick (typically from a ProfileMode's on_tick callback) -- it does
not advance on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Sequence

if TYPE_CHECKING:
    from .strand import Strand

StartFn = Callable[["Strand"], None]


@dataclass(frozen=True)
class Phase:
    duration_ms: int
    start_fn: StartFn


class Sequencer:
    def __init__(self, phases: Sequence[Phase]):
        self.phases = list(phases)
        self._current_phase = 0
        self._phase_start_ms = 0
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_phase_index(self) -> int:
        return self._current_phase

    def start(self, strand: "Strand") -> None:
        self._current_phase = 0
        self._phase_start_ms = strand.now_ms
        self._running = True
        if self.phases:
            self.phases[0].start_fn(strand)

    def stop(self) -> None:
        self._running = False

    def update(self, strand: "Strand") -> None:
        if not self._running or not self.phases:
            return
        now = strand.now_ms
        current = self.phases[self._current_phase]
        if now - self._phase_start_ms >= current.duration_ms:
            self._current_phase = (self._current_phase + 1) % len(self.phases)
            self._phase_start_ms = now
            self.phases[self._current_phase].start_fn(strand)
