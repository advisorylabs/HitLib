"""The robot programming environments an export can be written for.

HitLib itself builds unchanged under both, but the header Pattern Studio writes
cannot be shared between them: its Fill readers call PROS or VEX device APIs,
and VEXcode compiles C++11 where PROS compiles C++20.
"""

from __future__ import annotations

from enum import Enum


class Platform(str, Enum):
    PROS = "pros"
    #: VEXcode V5, VEXcode Pro V5 and the VEX VS Code extension, which share one
    #: SDK and one compiler setup.
    VEXCODE = "vexcode"

    @property
    def label(self) -> str:
        return "PROS" if self is Platform.PROS else "VEXcode"

    @classmethod
    def parse(cls, value: str) -> Platform:
        """The platform `value` names, falling back to PROS for anything
        unrecognised - a stale or hand-edited setting should not stop the app."""
        try:
            return cls(value)
        except ValueError:
            return cls.PROS
