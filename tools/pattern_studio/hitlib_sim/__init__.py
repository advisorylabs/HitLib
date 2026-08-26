"""Python simulation of HitLib's LedStrand animation engine.

Ports src/led_strand.cpp / src/led_sequencer.cpp / include/hitlib/led_profile.hpp
method-for-method so GUI-authored patterns preview exactly like the real
firmware and can later be exported as equivalent C++.
"""

from .profile import Profile, ProfileMode
from .sequencer import Phase, Sequencer
from .strand import (
    MAX_LEDS,
    MOTOR_HEAT_STOPS,
    AnimMode,
    BitScrollSegment,
    GaugeBlend,
    GaugeStop,
    GaugeStyle,
    MusicTrack,
    SpliceMode,
    SpliceRegion,
    SpliceRegionAnimKind,
    Strand,
)

__all__ = [
    "Strand",
    "AnimMode",
    "BitScrollSegment",
    "MusicTrack",
    "SpliceMode",
    "SpliceRegion",
    "SpliceRegionAnimKind",
    "GaugeStop",
    "GaugeStyle",
    "GaugeBlend",
    "MOTOR_HEAT_STOPS",
    "MAX_LEDS",
    "Sequencer",
    "Phase",
    "Profile",
    "ProfileMode",
]
