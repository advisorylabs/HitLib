"""Pure-Python simulation of HitLib's LedStrand animation engine.

Ports src/led_strand.cpp / src/led_sequencer.cpp / include/hitlib/led_profile.hpp
method-for-method so GUI-authored patterns preview exactly like the real
firmware and can later be exported as equivalent C++.
"""

from .profile import Profile, ProfileMode
from .sequencer import Phase, Sequencer
from .strand import MAX_LEDS, AnimMode, BitScrollSegment, Strand

__all__ = [
    "Strand",
    "AnimMode",
    "BitScrollSegment",
    "MAX_LEDS",
    "Sequencer",
    "Phase",
    "Profile",
    "ProfileMode",
]
