"""Declarative config for a strand's current animation.

This is the layer that sits between the GUI widgets and hitlib_sim.Strand: a
plain, serializable description of "what animation with what params" rather
than engine runtime state. Kept deliberately separate from hitlib_sim so the
same shape can later be (de)serialized for save/load and mapped
straight onto LedStrand method calls for C++ export.
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


class OverlayAnimationKind(str, Enum):
    OFF = "off"
    SOLID = "solid"
    PULSE = "pulse"
    FLASH = "flash"
    FLOW = "flow"
    RAINBOW = "rainbow"


# Human-readable labels for the overlay-kind dropdown, in display order.
OVERLAY_ANIMATION_KIND_LABELS: dict[OverlayAnimationKind, str] = {
    OverlayAnimationKind.OFF: "Off",
    OverlayAnimationKind.SOLID: "Solid Color",
    OverlayAnimationKind.PULSE: "Pulse",
    OverlayAnimationKind.FLASH: "Flash",
    OverlayAnimationKind.FLOW: "Flow (Gradient)",
    OverlayAnimationKind.RAINBOW: "Rainbow",
}


@dataclass
class OverlayAnimationConfig:
    """Mirrors LedStrand's overlay* methods -- an animation buffer that can be
    shown instead of a solid color. Used two ways: as the single shared
    overlay a Split-mode splice mask's masked bins can reveal (one buffer for
    the whole strand), and as each Custom-mode region's own independent
    animation (one buffer per region -- see SpliceRegionConfig).
    """

    kind: OverlayAnimationKind = OverlayAnimationKind.SOLID
    color: int = 0xFFFFFF
    color2: int = 0x0000FF
    bg_color: int = 0x000000
    run_length: int = 5
    speed: int = 1


class SpliceModeKind(str, Enum):
    SPLIT = "split"
    CUSTOM = "custom"


@dataclass
class SpliceRegionConfig:
    """One independently placed override region for a Custom-mode splice
    mask. Each region owns its own OverlayAnimationConfig -- unlike Split
    mode's single shared overlay, every region can animate independently and
    simultaneously.
    """

    start: int = 0
    width: int = 1
    animation: OverlayAnimationConfig = field(default_factory=OverlayAnimationConfig)


@dataclass
class SpliceMaskConfig:
    enabled: bool = False
    mode: SpliceModeKind = SpliceModeKind.SPLIT

    # Split mode: sections + 1 equal bins, optionally alternating.
    sections: int = 1
    invert: bool = False
    alternating: bool = False
    alt_period_ms: int = 400
    bg_color: int = 0x000000
    use_overlay: bool = False

    # Custom mode: arbitrarily placed/sized regions, never alternating.
    regions: list[SpliceRegionConfig] = field(default_factory=list)

    # Split mode's single shared overlay animation (see use_overlay above).
    # Custom mode regions each carry their own animation instead -- see
    # SpliceRegionConfig.animation.
    overlay: OverlayAnimationConfig = field(default_factory=OverlayAnimationConfig)

    def needs_overlay(self) -> bool:
        """Whether Split mode's shared overlay animation actually needs to be
        set up -- i.e. its masked bins are configured to show it. Not
        meaningful for Custom mode, where each region owns its animation."""
        return self.mode == SpliceModeKind.SPLIT and self.use_overlay


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
    refresh_ms: int = 25
    brightness: int = 100
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    splice: SpliceMaskConfig = field(default_factory=SpliceMaskConfig)

    # Profile mode: when use_profile is True, `animation`/`splice` above are
    # ignored in favor of `profile_modes`, with `active_mode_indices` driving
    # the same activate_mode()/priority-stack behavior LedStrand uses on
    # real hardware -- lets the preview exercise mode switching.
    use_profile: bool = False
    profile_modes: list[ModeConfig] = field(default_factory=list)
    active_mode_indices: list[int] = field(default_factory=list)
