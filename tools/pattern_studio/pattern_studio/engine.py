"""Bridges StrandConfig (declarative) onto hitlib_sim.Strand (runtime engine)."""

from __future__ import annotations

from hitlib_sim import (
    BitScrollSegment,
    Phase,
    Profile,
    ProfileMode,
    Sequencer,
    SpliceRegion,
    SpliceRegionAnimKind,
    Strand,
)

from .models import (
    AnimationConfig,
    AnimationKind,
    ModeConfig,
    OverlayAnimationConfig,
    OverlayAnimationKind,
    PhaseConfig,
    SpliceMaskConfig,
    SpliceModeKind,
    SpliceRegionConfig,
    StrandConfig,
)

# OverlayAnimationConfig.kind (Pattern Studio's serializable model) -> the
# sim/C++ SpliceRegionAnimKind used to build a custom region's own buffer.
# Both enumerate the same overlay* vocabulary, just as separate types.
_REGION_ANIM_KIND = {
    OverlayAnimationKind.OFF: SpliceRegionAnimKind.OFF,
    OverlayAnimationKind.SOLID: SpliceRegionAnimKind.SOLID,
    OverlayAnimationKind.PULSE: SpliceRegionAnimKind.PULSE,
    OverlayAnimationKind.FLASH: SpliceRegionAnimKind.FLASH,
    OverlayAnimationKind.FLOW: SpliceRegionAnimKind.FLOW,
    OverlayAnimationKind.RAINBOW: SpliceRegionAnimKind.RAINBOW,
}


def make_strand(config: StrandConfig) -> Strand:
    strand = Strand(
        adi_port=config.adi_port,
        length=config.length,
        refresh_ms=config.refresh_ms,
        smart_port=config.smart_port,
    )
    apply_strand_config(strand, config)
    return strand


def apply_strand_config(strand: Strand, config: StrandConfig) -> None:
    """(Re-)apply a strand's full config: either a single free-standing
    animation, or an attached Profile with the configured modes activated.
    """
    strand.set_brightness(config.brightness)

    if config.use_profile:
        strand.attach_profile(build_profile(config))
        for idx in config.active_mode_indices:
            if 0 <= idx < len(config.profile_modes):
                strand.activate_mode(idx)
    else:
        strand.detach_profile()
        _apply_animation(strand, config.animation)
        _apply_splice(strand, config.splice)


def build_profile(config: StrandConfig) -> Profile:
    return Profile(name=config.name, modes=[_build_profile_mode(mc) for mc in config.profile_modes])


def _build_profile_mode(mc: ModeConfig) -> ProfileMode:
    if mc.phases:
        sequencer = Sequencer([Phase(p.duration_ms, _make_phase_start_fn(p)) for p in mc.phases])

        def on_activate(strand: Strand, seq: Sequencer = sequencer) -> None:
            seq.start(strand)

        def on_tick(strand: Strand, seq: Sequencer = sequencer) -> None:
            seq.update(strand)

        return ProfileMode(name=mc.name, priority=mc.priority, on_activate=on_activate, on_tick=on_tick)

    def on_activate(strand: Strand, mc: ModeConfig = mc) -> None:
        _apply_animation(strand, mc.animation)
        _apply_splice(strand, mc.splice)

    return ProfileMode(name=mc.name, priority=mc.priority, on_activate=on_activate, on_tick=None)


def _make_phase_start_fn(p: PhaseConfig):
    def start_fn(strand: Strand, p: PhaseConfig = p) -> None:
        _apply_animation(strand, p.animation)
        _apply_splice(strand, p.splice)

    return start_fn


def _apply_animation(strand: Strand, a: AnimationConfig) -> None:
    if a.kind == AnimationKind.OFF:
        strand.off()
    elif a.kind == AnimationKind.SOLID:
        strand.set_color(a.color)
    elif a.kind == AnimationKind.PULSE:
        strand.pulse(a.color, a.run_length, a.speed, a.bg_color, a.invert, a.bounce)
    elif a.kind == AnimationKind.FLASH:
        strand.flash(a.color, a.speed, a.bg_color)
    elif a.kind == AnimationKind.FLOW:
        strand.flow(a.color, a.color2, a.speed, a.invert)
    elif a.kind == AnimationKind.RAINBOW:
        strand.rainbow(a.speed)
    elif a.kind == AnimationKind.TWINKLE:
        strand.twinkle(a.palette, a.density_pct, a.fade_step, a.bg_color)
    elif a.kind == AnimationKind.BITSCROLL:
        segments = [BitScrollSegment(color=a.color, width=a.segment_width)]
        strand.bitscroll(segments, a.speed, a.invert, a.bg_color, a.bounce, a.spacing, a.repeating)


def _apply_overlay(strand: Strand, o: OverlayAnimationConfig) -> None:
    if o.kind == OverlayAnimationKind.OFF:
        strand.overlay_set_color(0)
    elif o.kind == OverlayAnimationKind.SOLID:
        strand.overlay_set_color(o.color)
    elif o.kind == OverlayAnimationKind.PULSE:
        strand.overlay_pulse(o.color, o.run_length, o.speed, o.bg_color)
    elif o.kind == OverlayAnimationKind.FLASH:
        strand.overlay_flash(o.color, o.speed, o.bg_color)
    elif o.kind == OverlayAnimationKind.FLOW:
        strand.overlay_flow(o.color, o.color2, o.speed)
    elif o.kind == OverlayAnimationKind.RAINBOW:
        strand.overlay_rainbow(o.speed)


def _make_sim_region(r: SpliceRegionConfig) -> SpliceRegion:
    a = r.animation
    return SpliceRegion(
        start=r.start,
        width=r.width,
        kind=_REGION_ANIM_KIND[a.kind],
        color=a.color,
        color2=a.color2,
        bg_color=a.bg_color,
        run_length=a.run_length,
        speed=a.speed,
    )


def _apply_splice(strand: Strand, s: SpliceMaskConfig) -> None:
    if not s.enabled:
        strand.clear_splice_mask()
        return

    if s.mode == SpliceModeKind.SPLIT:
        if s.needs_overlay():
            _apply_overlay(strand, s.overlay)
        strand.splice_mask(s.sections, s.invert, s.alternating, s.alt_period_ms, s.bg_color, s.use_overlay)
    else:
        strand.splice_mask_custom([_make_sim_region(r) for r in s.regions])
