"""Declarative config for a strand's current animation.

This is the layer that sits between the GUI widgets and hitlib_sim.Strand: a
plain, serializable description of "what animation with what params" rather
than engine runtime state. Kept deliberately separate from hitlib_sim so the
same shape can later be (de)serialized for save/load (Phase 4) and mapped
straight onto LedStrand method calls for C++ export (Phase 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AnimationKind(str, Enum):
    OFF = "off"
    SOLID = "solid"
    PULSE = "pulse"
    FLASH = "flash"
    FLOW = "flow"
    RAINBOW = "rainbow"
    TWINKLE = "twinkle"
    BITSCROLL = "bitscroll"


# Human-readable labels for the animation-kind dropdown, in display order.
ANIMATION_KIND_LABELS: dict[AnimationKind, str] = {
    AnimationKind.OFF: "Off",
    AnimationKind.SOLID: "Solid Color",
    AnimationKind.PULSE: "Pulse",
    AnimationKind.FLASH: "Flash",
    AnimationKind.FLOW: "Flow (Gradient)",
    AnimationKind.RAINBOW: "Rainbow",
    AnimationKind.TWINKLE: "Twinkle",
    AnimationKind.BITSCROLL: "Bitscroll",
}


@dataclass
class AnimationConfig:
    kind: AnimationKind = AnimationKind.RAINBOW
    color: int = 0xFF0000
    color2: int = 0x0000FF
    bg_color: int = 0x000000
    run_length: int = 5
    speed: int = 1
    invert: bool = False
    bounce: bool = False
    density_pct: int = 30
    fade_step: int = 16
    palette: list[int] = field(default_factory=lambda: [0xFF0000, 0x00FF00, 0x0000FF])
    segment_width: int = 3
    spacing: int = 1
    repeating: bool = True


@dataclass
class SpliceMaskConfig:
    enabled: bool = False
    sections: int = 1
    invert: bool = False
    alternating: bool = False
    alt_period_ms: int = 400
    bg_color: int = 0x000000


@dataclass
class PhaseConfig:
    """One timed step of a sequenced mode -- mirrors hitlib_sim.sequencer.Phase."""

    name: str = "Phase"
    duration_ms: int = 1000
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    splice: SpliceMaskConfig = field(default_factory=SpliceMaskConfig)


@dataclass
class ModeConfig:
    """One named entry in a strand's profile -- mirrors hitlib_sim.profile.ProfileMode.

    If `phases` is empty the mode is a steady-state animation (`animation`/
    `splice`, re-issued once on activation). If non-empty, it's a timed
    sequence: `animation`/`splice` are unused and the mode cycles through
    `phases` via a Sequencer instead.
    """

    name: str = "Mode"
    priority: int = 10
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    splice: SpliceMaskConfig = field(default_factory=SpliceMaskConfig)
    phases: list[PhaseConfig] = field(default_factory=list)


@dataclass
class StrandConfig:
    name: str = "Strand"
    adi_port: int = 1
    smart_port: int = 0
    length: int = 30
    refresh_ms: int = 20
    brightness: int = 100
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    splice: SpliceMaskConfig = field(default_factory=SpliceMaskConfig)

    # Profile mode: when use_profile is True, `animation`/`splice` above are
    # ignored in favor of `profile_modes`, with `active_mode_indices` driving
    # the same activate_mode()/priority-stack behavior LedStrand uses on
    # real hardware -- lets the preview exercise mode switching, not just a
    # single animation in isolation.
    use_profile: bool = False
    profile_modes: list[ModeConfig] = field(default_factory=list)
    active_mode_indices: list[int] = field(default_factory=list)
