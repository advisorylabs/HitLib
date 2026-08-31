"""Bridges StrandConfig (declarative) onto hitlib_sim.Strand (runtime engine)."""

from __future__ import annotations

from dataclasses import dataclass, field

from hitlib_sim import (
    BitScrollSegment,
    GaugeBlend,
    GaugeStop,
    GaugeStyle,
    MusicTrack,
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
    GaugeBlendKind,
    GaugeStyleKind,
    ModeConfig,
    MusicConfig,
    OverlayAnimationConfig,
    OverlayAnimationKind,
    PhaseConfig,
    SpliceMaskConfig,
    SpliceModeKind,
    SpliceRegionConfig,
    StrandConfig,
)

# OverlayAnimationConfig.kind (the serializable model) -> the sim/C++
# SpliceRegionAnimKind that builds a custom region's buffer. Both enumerate the
# same overlay* vocabulary as separate types.
_REGION_ANIM_KIND = {
    OverlayAnimationKind.OFF: SpliceRegionAnimKind.OFF,
    OverlayAnimationKind.SOLID: SpliceRegionAnimKind.SOLID,
    OverlayAnimationKind.PULSE: SpliceRegionAnimKind.PULSE,
    OverlayAnimationKind.FLASH: SpliceRegionAnimKind.FLASH,
    OverlayAnimationKind.FLOW: SpliceRegionAnimKind.FLOW,
    OverlayAnimationKind.RAINBOW: SpliceRegionAnimKind.RAINBOW,
    OverlayAnimationKind.TWINKLE: SpliceRegionAnimKind.TWINKLE,
    OverlayAnimationKind.BITSCROLL: SpliceRegionAnimKind.BITSCROLL,
    OverlayAnimationKind.GAUGE: SpliceRegionAnimKind.GAUGE,
}

_GAUGE_STYLE = {
    GaugeStyleKind.HEAT: GaugeStyle.HEAT,
    GaugeStyleKind.BAR: GaugeStyle.BAR,
}

_GAUGE_BLEND = {
    GaugeBlendKind.LERP: GaugeBlend.LERP,
    GaugeBlendKind.STEP: GaugeBlend.STEP,
}


@dataclass(frozen=True)
class MusicBinding:
    """What a MUSIC animation needs when it is applied: one playable track per
    band, plus the playback options that belong to the song rather than to any
    table. Every strand shares this and picks its own band out of it."""

    tracks: dict[str, MusicTrack] = field(default_factory=dict)
    loop: bool = False

    def track_for(self, band: str) -> MusicTrack | None:
        """The track for `band`, or any other band that has one.

        Falling back rather than returning nothing keeps a strand lit when its
        band came out empty - a MIDI with no percussion has no treble to speak
        of, and a dark strip is a worse answer than the wrong band.
        """
        track = self.tracks.get(band)
        if track is not None and track.frame_count:
            return track
        for candidate in self.tracks.values():
            if candidate.frame_count:
                return candidate
        return None


def make_music_binding(music: MusicConfig | None) -> MusicBinding | None:
    """The binding a MUSIC animation plays, or None when the design has no song
    loaded - in which case a MUSIC animation previews as an empty meter rather
    than failing."""
    if music is None or not music.loaded:
        return None
    return MusicBinding(
        tracks={
            band: MusicTrack(samples=tuple(table), frame_ms=music.frame_ms)
            for band, table in music.bands.items()
            if table
        },
        loop=music.loop,
    )


def make_strand(config: StrandConfig, music: MusicBinding | None = None) -> Strand:
    strand = Strand(
        adi_port=config.adi_port,
        length=config.length,
        refresh_ms=config.refresh_ms,
        smart_port=config.smart_port,
    )
    apply_strand_config(strand, config, music)
    return strand


def apply_strand_config(strand: Strand, config: StrandConfig,
                        music: MusicBinding | None = None) -> None:
    """(Re-)apply a strand's full config: either a single free-standing
    animation, or an attached Profile with the configured modes activated.
    """
    strand.set_brightness(config.brightness)

    if config.use_profile:
        strand.attach_profile(build_profile(config, music))
        for idx in config.active_mode_indices:
            if 0 <= idx < len(config.profile_modes):
                strand.activate_mode(idx)
    else:
        strand.detach_profile()
        _apply_animation(strand, config.animation, music)
        _apply_splice(strand, config.splice)


def build_profile(config: StrandConfig, music: MusicBinding | None = None) -> Profile:
    return Profile(
        name=config.name, modes=[_build_profile_mode(mc, music) for mc in config.profile_modes]
    )


def _build_profile_mode(mc: ModeConfig, music: MusicBinding | None = None) -> ProfileMode:
    if mc.phases:
        sequencer = Sequencer([Phase(p.duration_ms, _make_phase_start_fn(p, music)) for p in mc.phases])

        def on_activate(strand: Strand, seq: Sequencer = sequencer) -> None:
            seq.start(strand)

        def on_tick(strand: Strand, seq: Sequencer = sequencer) -> None:
            seq.update(strand)

        return ProfileMode(name=mc.name, priority=mc.priority, on_activate=on_activate, on_tick=on_tick)

    def on_activate(strand: Strand, mc: ModeConfig = mc) -> None:
        _apply_animation(strand, mc.animation, music)
        _apply_splice(strand, mc.splice)

    return ProfileMode(name=mc.name, priority=mc.priority, on_activate=on_activate, on_tick=None)


def _make_phase_start_fn(p: PhaseConfig, music: MusicBinding | None = None):
    def start_fn(strand: Strand, p: PhaseConfig = p) -> None:
        _apply_animation(strand, p.animation, music)
        _apply_splice(strand, p.splice)

    return start_fn


def _apply_animation(strand: Strand, a: AnimationConfig, music: MusicBinding | None = None) -> None:
    if a.kind == AnimationKind.OFF:
        strand.off()
    elif a.kind == AnimationKind.SOLID:
        strand.set_color(a.color)
    elif a.kind == AnimationKind.PULSE:
        strand.pulse(a.color, a.run_length, a.speed, a.bg_color, a.invert, a.bounce)
    elif a.kind == AnimationKind.FLASH:
        strand.flash(a.color, a.on_ms, a.off_ms, a.bg_color)
    elif a.kind == AnimationKind.FLOW:
        strand.flow(a.color, a.color2, a.speed, a.invert, a.seamless)
    elif a.kind == AnimationKind.RAINBOW:
        strand.rainbow(a.speed)
    elif a.kind == AnimationKind.TWINKLE:
        strand.twinkle(a.palette, a.density_pct, a.fade_step, a.bg_color)
    elif a.kind == AnimationKind.BITSCROLL:
        segments = [BitScrollSegment(color=a.color, width=a.segment_width)]
        strand.bitscroll(segments, a.speed, a.invert, a.bg_color, a.bounce, a.spacing, a.repeating)
    elif a.kind == AnimationKind.FILL:
        strand.level_fill(a.color, a.color2, a.gradient, a.bg_color, a.invert)
        # Every source previews the same way, Manual included: with no device
        # to read, a stand-in value drives the meter through the firmware's own
        # mapping. The source only changes what the export polls - see codegen.
        strand.level_source(_preview_reader(strand, a), a.source_empty, a.source_full,
                            a.source_wrap, a.smoothing)
    elif a.kind == AnimationKind.MUSIC:
        track = music.track_for(a.band) if music is not None else None
        if track is None:
            # No song loaded: still set the meter up so its colors and
            # direction preview, it just sits empty until there is one.
            strand.level_fill(a.color, a.color2, a.gradient, a.bg_color, a.invert)
        else:
            strand.music_sync(track, a.color, a.color2, a.gradient, a.bg_color, a.invert,
                              a.sensitivity, music.loop)


#: How long one preview sweep takes to cross the range, in milliseconds. Slow
#: enough to watch the fill move, quick enough to see the whole range without
#: waiting on it.
_SWEEP_MS = 4000

#: Sweeps a wrapping meter through this many ranges before starting over, so
#: the preview actually shows it wrapping rather than a single pass.
_SWEEP_WRAPS = 3


def _preview_reader(strand: Strand, a, phase_offset: float = 0.0):
    """A stand-in for the value a Fill meter or Gauge region follows on the
    robot.

    With no device to read, the preview feeds the meter an invented reading in
    the animation's own units. Everything downstream - range, wrap, smoothing,
    the color scale, the partial edge pixel - is the real code path; only the
    number is invented.

    Wrap shows as a sawtooth sweep; clamping shows as a triangle.

    `a` is an AnimationConfig or an OverlayAnimationConfig: a gauge region
    carries the same source fields under the same names.

    `phase_offset` shifts the sweep, in whole sweeps, so a row of gauges does
    not move in lockstep.
    """

    def read() -> float:
        span = a.source_full - a.source_empty
        if not a.preview_sweep:
            return a.source_empty + span * a.preview_level / 100
        now = strand.now_ms + phase_offset * _SWEEP_MS
        if a.source_wrap:
            phase = (now % (_SWEEP_MS * _SWEEP_WRAPS)) / _SWEEP_MS
        else:
            cycle = (now % (_SWEEP_MS * 2)) / _SWEEP_MS
            phase = cycle if cycle <= 1 else 2 - cycle
        return a.source_empty + span * phase

    return read


def _apply_overlay(strand: Strand, o: OverlayAnimationConfig) -> None:
    if o.kind == OverlayAnimationKind.OFF:
        strand.overlay_set_color(0)
    elif o.kind == OverlayAnimationKind.SOLID:
        strand.overlay_set_color(o.color)
    elif o.kind == OverlayAnimationKind.PULSE:
        strand.overlay_pulse(o.color, o.run_length, o.speed, o.bg_color)
    elif o.kind == OverlayAnimationKind.FLASH:
        strand.overlay_flash(o.color, o.on_ms, o.off_ms, o.bg_color)
    elif o.kind == OverlayAnimationKind.FLOW:
        strand.overlay_flow(o.color, o.color2, o.speed, o.seamless)
    elif o.kind == OverlayAnimationKind.RAINBOW:
        strand.overlay_rainbow(o.speed)
    elif o.kind == OverlayAnimationKind.TWINKLE:
        strand.overlay_twinkle(o.palette, o.density_pct, o.fade_step, o.bg_color)
    elif o.kind == OverlayAnimationKind.BITSCROLL:
        segments = [BitScrollSegment(color=o.color, width=o.segment_width)]
        strand.overlay_bitscroll(segments, o.speed, o.invert, o.bg_color, o.spacing, o.repeating)


def _make_sim_region(strand: Strand, r: SpliceRegionConfig,
                     phase_offset: float = 0.0) -> SpliceRegion:
    a = r.animation
    if a.kind == OverlayAnimationKind.GAUGE:
        return SpliceRegion(
            start=r.start,
            width=r.width,
            kind=SpliceRegionAnimKind.GAUGE,
            # Only used as the fallback scale, when the region has no stops.
            color=a.color,
            color2=a.color2,
            bg_color=a.bg_color,
            # As in _apply_fill above: the source only changes what the export
            # polls, not how the preview drives the gauge.
            read=_preview_reader(strand, a, phase_offset),
            empty_at=a.source_empty,
            full_at=a.source_full,
            wrap=a.source_wrap,
            smoothing=a.smoothing,
            invert=a.invert,
            style=_GAUGE_STYLE[a.style],
            blend=_GAUGE_BLEND[a.blend],
            stops=tuple(GaugeStop(at=stop.at, color=stop.color) for stop in a.stops),
        )
    return SpliceRegion(
        start=r.start,
        width=r.width,
        kind=_REGION_ANIM_KIND[a.kind],
        color=a.color,
        color2=a.color2,
        bg_color=a.bg_color,
        run_length=a.run_length,
        speed=a.speed,
        on_ms=a.on_ms,
        off_ms=a.off_ms,
        seamless=a.seamless,
        invert=a.invert,
        palette=tuple(a.palette),
        density_pct=a.density_pct,
        fade_step=a.fade_step,
        segment_width=a.segment_width,
        spacing=a.spacing,
        repeating=a.repeating,
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
        # Spread the preview sweeps across the triangle so a row of gauges
        # shows as several independent meters, not one wide one.
        count = max(len(s.regions), 1)
        strand.splice_mask_custom([
            _make_sim_region(strand, r, 2.0 * i / count) for i, r in enumerate(s.regions)
        ])
