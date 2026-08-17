"""Port of include/hitlib/led_profile.hpp."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .strand import Strand

ModeFn = Callable[["Strand"], None]


@dataclass(frozen=True)
class ProfileMode:
    name: str
    priority: int
    on_activate: Optional[ModeFn] = None
    on_tick: Optional[ModeFn] = None


@dataclass(frozen=True)
class Profile:
    name: str
    modes: list[ProfileMode] = field(default_factory=list)

    @property
    def mode_count(self) -> int:
        return len(self.modes)
