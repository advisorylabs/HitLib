"""(De)serializes StrandConfig trees to/from JSON.

This is both the GUI's native save format and the thing teammates share -
one format, not two. Every *_from_dict() reads through a typed getter with a
default pulled from a fresh dataclass instance, so older or hand-edited files
with missing fields - or a null, or a string where a number goes - load with
sane defaults instead of raising, and adding a new field later doesn't break
existing saved files.

What still raises is a ValueError, which is what the File menu reports: a file
that is not a profile at all, a name no version knows (an unknown animation
kind), or a file from a newer Pattern Studio than this one.
"""

from __future__ import annotations

import base64
import contextlib
import json
import math
import os
import zlib
from pathlib import Path

from . import frame_codec
from .envelope import EnvelopeSettings, TrackAnalysis
from .models import (
    AnimationConfig,
    BindingConfig,
    BitScrollSegmentConfig,
    CompositionConfig,
    Document,
    DriverConfig,
    GaugeStopConfig,
    LayerConfig,
    ModeConfig,
    MusicConfig,
    OverlayAnimationConfig,
    PhaseConfig,
    SpliceMaskConfig,
    SpliceRegionConfig,
    StrandConfig,
)

# 2 added the document-level `music` block. 3 replaced its single baked table
# with the per-band loudness analysis it is baked from, so the shaping controls
# keep working after the source file is gone. A schema-2 file still loads: its
# song is dropped (there is nothing to re-analyse from) and its strands come
# through untouched.
#
# 4 added the Fill animation and its source fields. Older files load unchanged
# (the fields default), but the bump is not cosmetic in the other direction: a
# schema-3 reader hits an unknown animation kind on a file with a Fill in it,
# so the version is what tells it why.
#
# 5 added the Gauge splice region - a Fill meter scoped to a few pixels, with a
# color scale of its own. Same story as 4 in both directions: older files load
# with the gauge fields defaulted, and a schema-4 reader hits an unknown
# overlay kind on a design that uses one.
#
# 6 added Twinkle and Bitscroll as overlay/region animations, so a masked bin
# or a custom region can run either. Same story again: older files default the
# new fields, and an older reader hits an unknown overlay kind on a design
# that picks one of them.
# 7 added the Animator: a document-level `compositions` library of named layer
# stacks, and the `custom` animation kind that plays one by id. Same story as
# 4, 5 and 6 in both directions - an older file loads with an empty library and
# no strand referring to one, and a schema-6 reader hits an unknown animation
# kind on any design that actually uses one, which is what the version is there
# to explain. A drawn-frame layer's pixels are packed with frame_codec and
# base64'd, so the file holds exactly the bytes the export and the preview
# decode. 7 also gave every moving animation a `step_ms` beside its `speed`
# (LedStrand::Speed), and each drawn frame a scroll flag; a file without them
# means a step every refresh tick, exactly as `speed` always did.
#
# A file newer than SCHEMA_VERSION is refused rather than read. Its additions
# may be nothing but new keys, which would load without complaint - and the
# next Save would write it back as this version with them gone.
SCHEMA_VERSION = 7


# ============================================================================
# Reading a value as the type its field is
#
# A hand-edited file can hold null, a string where a number goes, or an object
# where a list goes. Each of those loads as the field's default, the same as a
# missing key. Taken as-is, it only moved the failure somewhere worse: into
# the engine the first time the strand ticked.
# ============================================================================


def _obj(d: dict, key: str) -> dict:
    """A nested block, or {} - every field defaulted - if it is not one."""
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def _objs(d: dict, key: str) -> list[dict]:
    """A list of nested blocks. An entry that is not one loads as all defaults."""
    v = d.get(key)
    if not isinstance(v, list):
        return []
    return [x if isinstance(x, dict) else {} for x in v]


def _as_int(v) -> int | None:
    # bool is an int to Python, but `true` is not a port number.
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    # JSON has one number type, and some tools write every number as 2.0.
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _int(d: dict, key: str, default: int) -> int:
    v = _as_int(d.get(key))
    return default if v is None else v


def _ints(d: dict, key: str, default: list[int]) -> list[int]:
    """A list of ints - colors, indices - or the default if any entry is not one."""
    v = d.get(key)
    if isinstance(v, list):
        values = [_as_int(x) for x in v]
        if None not in values:
            return values
    return list(default)


def _num(d: dict, key: str, default: float) -> float:
    """A field that takes fractions: an int or a float, whichever was written."""
    v = d.get(key)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return v
    return default


def _bool(d: dict, key: str, default: bool) -> bool:
    v = d.get(key)
    # 0 and 1 are what a hand-written flag most often looks like.
    return bool(v) if isinstance(v, int) else default


def _str(d: dict, key: str, default: str) -> str:
    v = d.get(key)
    return v if isinstance(v, str) else default


def _enum(d: dict, key: str, default):
    """A member of `default`'s enum. Only a value of the wrong type falls back:
    a name no member has still raises ValueError, because that is a newer file
    or a typo, and guessing would open a different design."""
    v = d.get(key)
    return type(default)(v) if isinstance(v, str) else default


def _animation_to_dict(a: AnimationConfig) -> dict:
    return {
        "kind": a.kind.value,
        "color": a.color,
        "color2": a.color2,
        "bg_color": a.bg_color,
        "run_length": a.run_length,
        "speed": a.speed,
        "step_ms": a.step_ms,
        "on_ms": a.on_ms,
        "off_ms": a.off_ms,
        "invert": a.invert,
        "bounce": a.bounce,
        "seamless": a.seamless,
        "density_pct": a.density_pct,
        "fade_step": a.fade_step,
        "palette": list(a.palette),
        "segment_width": a.segment_width,
        "spacing": a.spacing,
        "repeating": a.repeating,
        "source": a.source,
        "source_port": a.source_port,
        "source_empty": a.source_empty,
        "source_full": a.source_full,
        "source_wrap": a.source_wrap,
        "smoothing": a.smoothing,
        "preview_level": a.preview_level,
        "preview_sweep": a.preview_sweep,
        "gradient": a.gradient,
        "sensitivity": a.sensitivity,
        "band": a.band,
        "composition_id": a.composition_id,
    }


def _animation_from_dict(d: dict) -> AnimationConfig:
    default = AnimationConfig()
    return AnimationConfig(
        kind=_enum(d, "kind", default.kind),
        color=_int(d, "color", default.color),
        color2=_int(d, "color2", default.color2),
        bg_color=_int(d, "bg_color", default.bg_color),
        run_length=_int(d, "run_length", default.run_length),
        speed=_int(d, "speed", default.speed),
        step_ms=_int(d, "step_ms", default.step_ms),
        on_ms=_int(d, "on_ms", default.on_ms),
        off_ms=_int(d, "off_ms", default.off_ms),
        invert=_bool(d, "invert", default.invert),
        bounce=_bool(d, "bounce", default.bounce),
        seamless=_bool(d, "seamless", default.seamless),
        density_pct=_int(d, "density_pct", default.density_pct),
        fade_step=_int(d, "fade_step", default.fade_step),
        palette=_ints(d, "palette", default.palette),
        segment_width=_int(d, "segment_width", default.segment_width),
        spacing=_int(d, "spacing", default.spacing),
        repeating=_bool(d, "repeating", default.repeating),
        source=_str(d, "source", default.source),
        source_port=_int(d, "source_port", default.source_port),
        source_empty=_int(d, "source_empty", default.source_empty),
        source_full=_int(d, "source_full", default.source_full),
        source_wrap=_bool(d, "source_wrap", default.source_wrap),
        smoothing=_int(d, "smoothing", default.smoothing),
        preview_level=_int(d, "preview_level", default.preview_level),
        preview_sweep=_bool(d, "preview_sweep", default.preview_sweep),
        gradient=_bool(d, "gradient", default.gradient),
        sensitivity=_int(d, "sensitivity", default.sensitivity),
        band=_str(d, "band", default.band),
        composition_id=_int(d, "composition_id", default.composition_id),
    )


def _stop_to_dict(stop: GaugeStopConfig) -> dict:
    return {"at": stop.at, "color": stop.color}


def _stop_from_dict(d: dict) -> GaugeStopConfig:
    default = GaugeStopConfig()
    return GaugeStopConfig(at=_num(d, "at", default.at), color=_int(d, "color", default.color))


def _overlay_to_dict(o: OverlayAnimationConfig) -> dict:
    return {
        "kind": o.kind.value,
        "color": o.color,
        "color2": o.color2,
        "bg_color": o.bg_color,
        "run_length": o.run_length,
        "speed": o.speed,
        "step_ms": o.step_ms,
        "on_ms": o.on_ms,
        "off_ms": o.off_ms,
        "seamless": o.seamless,
        "density_pct": o.density_pct,
        "fade_step": o.fade_step,
        "palette": list(o.palette),
        "segment_width": o.segment_width,
        "spacing": o.spacing,
        "repeating": o.repeating,
        "source": o.source,
        "source_port": o.source_port,
        "source_empty": o.source_empty,
        "source_full": o.source_full,
        "source_wrap": o.source_wrap,
        "smoothing": o.smoothing,
        "invert": o.invert,
        "style": o.style.value,
        "blend": o.blend.value,
        "stops": [_stop_to_dict(stop) for stop in o.stops],
        "preview_level": o.preview_level,
        "preview_sweep": o.preview_sweep,
    }


def _overlay_from_dict(d: dict) -> OverlayAnimationConfig:
    default = OverlayAnimationConfig()
    return OverlayAnimationConfig(
        kind=_enum(d, "kind", default.kind),
        color=_int(d, "color", default.color),
        color2=_int(d, "color2", default.color2),
        bg_color=_int(d, "bg_color", default.bg_color),
        run_length=_int(d, "run_length", default.run_length),
        speed=_int(d, "speed", default.speed),
        step_ms=_int(d, "step_ms", default.step_ms),
        on_ms=_int(d, "on_ms", default.on_ms),
        off_ms=_int(d, "off_ms", default.off_ms),
        seamless=_bool(d, "seamless", default.seamless),
        density_pct=_int(d, "density_pct", default.density_pct),
        fade_step=_int(d, "fade_step", default.fade_step),
        palette=_ints(d, "palette", default.palette),
        segment_width=_int(d, "segment_width", default.segment_width),
        spacing=_int(d, "spacing", default.spacing),
        repeating=_bool(d, "repeating", default.repeating),
        source=_str(d, "source", default.source),
        source_port=_int(d, "source_port", default.source_port),
        source_empty=_int(d, "source_empty", default.source_empty),
        source_full=_int(d, "source_full", default.source_full),
        source_wrap=_bool(d, "source_wrap", default.source_wrap),
        smoothing=_int(d, "smoothing", default.smoothing),
        invert=_bool(d, "invert", default.invert),
        style=_enum(d, "style", default.style),
        blend=_enum(d, "blend", default.blend),
        stops=[_stop_from_dict(stop) for stop in _objs(d, "stops")],
        preview_level=_int(d, "preview_level", default.preview_level),
        preview_sweep=_bool(d, "preview_sweep", default.preview_sweep),
    )


def _region_to_dict(r: SpliceRegionConfig) -> dict:
    return {
        "start": r.start,
        "width": r.width,
        "animation": _overlay_to_dict(r.animation),
    }


def _region_from_dict(d: dict) -> SpliceRegionConfig:
    default = SpliceRegionConfig()
    return SpliceRegionConfig(
        start=_int(d, "start", default.start),
        width=_int(d, "width", default.width),
        animation=_overlay_from_dict(_obj(d, "animation")),
    )


def _splice_to_dict(s: SpliceMaskConfig) -> dict:
    return {
        "enabled": s.enabled,
        "mode": s.mode.value,
        "sections": s.sections,
        "invert": s.invert,
        "alternating": s.alternating,
        "alt_period_ms": s.alt_period_ms,
        "bg_color": s.bg_color,
        "use_overlay": s.use_overlay,
        "regions": [_region_to_dict(r) for r in s.regions],
        "overlay": _overlay_to_dict(s.overlay),
    }


def _splice_from_dict(d: dict) -> SpliceMaskConfig:
    default = SpliceMaskConfig()
    return SpliceMaskConfig(
        enabled=_bool(d, "enabled", default.enabled),
        mode=_enum(d, "mode", default.mode),
        sections=_int(d, "sections", default.sections),
        invert=_bool(d, "invert", default.invert),
        alternating=_bool(d, "alternating", default.alternating),
        alt_period_ms=_int(d, "alt_period_ms", default.alt_period_ms),
        bg_color=_int(d, "bg_color", default.bg_color),
        use_overlay=_bool(d, "use_overlay", default.use_overlay),
        regions=[_region_from_dict(r) for r in _objs(d, "regions")],
        overlay=_overlay_from_dict(_obj(d, "overlay")),
    )


def _phase_to_dict(p: PhaseConfig) -> dict:
    return {
        "name": p.name,
        "duration_ms": p.duration_ms,
        "animation": _animation_to_dict(p.animation),
        "splice": _splice_to_dict(p.splice),
    }


def _phase_from_dict(d: dict) -> PhaseConfig:
    default = PhaseConfig()
    return PhaseConfig(
        name=_str(d, "name", default.name),
        duration_ms=_int(d, "duration_ms", default.duration_ms),
        animation=_animation_from_dict(_obj(d, "animation")),
        splice=_splice_from_dict(_obj(d, "splice")),
    )


def _mode_to_dict(m: ModeConfig) -> dict:
    return {
        "name": m.name,
        "priority": m.priority,
        "animation": _animation_to_dict(m.animation),
        "splice": _splice_to_dict(m.splice),
        "phases": [_phase_to_dict(p) for p in m.phases],
    }


def _mode_from_dict(d: dict) -> ModeConfig:
    default = ModeConfig()
    return ModeConfig(
        name=_str(d, "name", default.name),
        priority=_int(d, "priority", default.priority),
        animation=_animation_from_dict(_obj(d, "animation")),
        splice=_splice_from_dict(_obj(d, "splice")),
        phases=[_phase_from_dict(p) for p in _objs(d, "phases")],
    )


def strand_to_dict(cfg: StrandConfig) -> dict:
    return {
        "name": cfg.name,
        "adi_port": cfg.adi_port,
        "smart_port": cfg.smart_port,
        "length": cfg.length,
        "refresh_ms": cfg.refresh_ms,
        "brightness": cfg.brightness,
        "animation": _animation_to_dict(cfg.animation),
        "splice": _splice_to_dict(cfg.splice),
        "use_profile": cfg.use_profile,
        "profile_modes": [_mode_to_dict(m) for m in cfg.profile_modes],
        "active_mode_indices": list(cfg.active_mode_indices),
    }


def strand_from_dict(d: dict) -> StrandConfig:
    default = StrandConfig()
    return StrandConfig(
        name=_str(d, "name", default.name),
        adi_port=_int(d, "adi_port", default.adi_port),
        smart_port=_int(d, "smart_port", default.smart_port),
        length=_int(d, "length", default.length),
        refresh_ms=_int(d, "refresh_ms", default.refresh_ms),
        brightness=_int(d, "brightness", default.brightness),
        animation=_animation_from_dict(_obj(d, "animation")),
        splice=_splice_from_dict(_obj(d, "splice")),
        use_profile=_bool(d, "use_profile", default.use_profile),
        profile_modes=[_mode_from_dict(m) for m in _objs(d, "profile_modes")],
        active_mode_indices=_ints(d, "active_mode_indices", []),
    )


def _pack(values: list[int]) -> str:
    """A byte-per-frame table as compressed base64.

    A four-minute analysis is a hundred thousand values; as a JSON array of ints
    it would dwarf the design it belongs to and make the file unreadable. dB
    curves compress well, so deflate plus base64 gets it down to a few tens of
    kilobytes on one line.
    """
    return base64.b64encode(zlib.compress(bytes(values), 6)).decode("ascii")


def _unpack(raw) -> list[int]:
    # A hand-edited file may carry a plain list instead of the packed string, so
    # accept either rather than failing the whole load.
    if isinstance(raw, list):
        try:
            return [max(0, min(255, int(v))) for v in raw]
        except (TypeError, ValueError, OverflowError):
            return []
    if not isinstance(raw, str) or not raw:
        return []
    try:
        return list(zlib.decompress(base64.b64decode(raw)))
    except (ValueError, TypeError, zlib.error):
        return []


def _settings_to_dict(s: EnvelopeSettings) -> dict:
    return {
        "mode": s.mode.value,
        "smooth_ms": s.smooth_ms,
        "attack_ms": s.attack_ms,
        "release_ms": s.release_ms,
        "auto_gain": s.auto_gain,
        "contrast": s.contrast,
        "range_db": s.range_db,
        "frame_ms": s.frame_ms,
    }


def _settings_from_dict(d: dict) -> EnvelopeSettings:
    default = EnvelopeSettings()
    return EnvelopeSettings(
        mode=_enum(d, "mode", default.mode),
        smooth_ms=_int(d, "smooth_ms", default.smooth_ms),
        attack_ms=_int(d, "attack_ms", default.attack_ms),
        release_ms=_int(d, "release_ms", default.release_ms),
        auto_gain=_int(d, "auto_gain", default.auto_gain),
        contrast=_int(d, "contrast", default.contrast),
        range_db=_int(d, "range_db", default.range_db),
        frame_ms=_int(d, "frame_ms", default.frame_ms),
    )


def _analysis_to_dict(a: TrackAnalysis) -> dict:
    return {
        "frame_ms": a.frame_ms,
        "name": a.name,
        "source_path": a.source_path,
        "bands": {name: _pack(values) for name, values in a.bands.items()},
    }


def _analysis_from_dict(d: dict) -> TrackAnalysis:
    default = TrackAnalysis()
    bands = {name: _unpack(raw) for name, raw in _obj(d, "bands").items()}
    return TrackAnalysis(
        bands={name: values for name, values in bands.items() if values},
        frame_ms=_int(d, "frame_ms", default.frame_ms),
        name=_str(d, "name", default.name),
        source_path=_str(d, "source_path", default.source_path),
    )


def _music_to_dict(m: MusicConfig) -> dict:
    # `bands` is deliberately absent: it is re-baked from `analysis` on load.
    return {
        "name": m.name,
        "source_path": m.source_path,
        "source_kind": m.source_kind,
        "tracks": list(m.tracks),
        "loop": m.loop,
        "settings": _settings_to_dict(m.settings),
        "analysis": _analysis_to_dict(m.analysis),
    }


def _music_from_dict(d: dict) -> MusicConfig:
    default = MusicConfig()
    return MusicConfig(
        name=_str(d, "name", default.name),
        source_path=_str(d, "source_path", default.source_path),
        source_kind=_str(d, "source_kind", default.source_kind),
        tracks=_ints(d, "tracks", []),
        loop=_bool(d, "loop", default.loop),
        settings=_settings_from_dict(_obj(d, "settings")),
        analysis=_analysis_from_dict(_obj(d, "analysis")),
    )


# ============================================================================
# Custom animations (schema 7)
# ============================================================================


def _binding_to_dict(b: BindingConfig) -> dict:
    d = {
        "param": b.param.value,
        "driver": b.driver,
        "at_zero": b.at_zero,
        "at_full": b.at_full,
    }
    # Omitted entirely when it is the straight line, which is the common case
    # and would otherwise put seventeen zeros in every binding.
    if b.curve:
        d["curve"] = list(b.curve)
    return d


def _binding_from_dict(d: dict) -> BindingConfig:
    ref = BindingConfig()
    return BindingConfig(
        param=_enum(d, "param", ref.param),
        driver=_int(d, "driver", ref.driver),
        at_zero=_int(d, "at_zero", ref.at_zero),
        at_full=_int(d, "at_full", ref.at_full),
        curve=_ints(d, "curve", []),
    )


def _segment_to_dict(s: BitScrollSegmentConfig) -> dict:
    return {"color": s.color, "width": s.width}


def _segment_from_dict(d: dict) -> BitScrollSegmentConfig:
    ref = BitScrollSegmentConfig()
    return BitScrollSegmentConfig(
        color=_int(d, "color", ref.color),
        width=_int(d, "width", ref.width),
    )


def _layer_to_dict(layer: LayerConfig) -> dict:
    d = {
        "name": layer.name,
        "kind": layer.kind.value,
        "muted": layer.muted,
        "start": layer.start,
        "width": layer.width,
        "stride": layer.stride,
        "continuous": layer.continuous,
        "blend": layer.blend.value,
        "opacity": layer.opacity,
        "bindings": [_binding_to_dict(b) for b in layer.bindings],
        "color": layer.color,
        "color2": layer.color2,
        "bg_color": layer.bg_color,
        "run_length": layer.run_length,
        "speed": layer.speed,
        "step_ms": layer.step_ms,
        "invert": layer.invert,
        "bounce": layer.bounce,
        "seamless": layer.seamless,
        "on_ms": layer.on_ms,
        "off_ms": layer.off_ms,
        "loop_ms": layer.loop_ms,
        "start_ms": layer.start_ms,
        "end_ms": layer.end_ms,
        "fade_in_ms": layer.fade_in_ms,
        "fade_out_ms": layer.fade_out_ms,
        "retrigger": layer.retrigger,
        "palette": list(layer.palette),
        "density_pct": layer.density_pct,
        "fade_step": layer.fade_step,
        "segments": [_segment_to_dict(s) for s in layer.segments],
        "spacing": layer.spacing,
        "repeating": layer.repeating,
        "level": layer.level,
        "gradient": layer.gradient,
        "style": layer.style.value,
        "gauge_blend": layer.gauge_blend.value,
        "stops": [_stop_to_dict(s) for s in layer.stops],
        "empty_at": layer.empty_at,
        "full_at": layer.full_at,
        "frame_palette": list(layer.frame_palette),
        "frame_width": layer.frame_width,
        "frame_ms": layer.frame_ms,
    }
    # Drawn frames go through the same RLE the export and the preview use, so
    # a file cannot hold a frame that decodes differently from the one drawn.
    # Base64 because JSON has no bytes, and skipped entirely when nothing is
    # drawn, which keeps every other layer's block small.
    if layer.frames:
        data, index = frame_codec.encode_frames(layer.frames, layer.frame_width)
        d["frames"] = base64.b64encode(data).decode("ascii")
        d["frame_index"] = list(index)
        d["frame_count"] = len(layer.frames)
        d["frame_scroll"] = [bool(f) for f in layer.frame_scroll]
    return d


def _layer_from_dict(d: dict) -> LayerConfig:
    ref = LayerConfig()
    speed = _int(d, "speed", ref.speed)
    frame_width = _int(d, "frame_width", ref.frame_width)
    frames: list[list[int]] = []
    raw = d.get("frames")
    if isinstance(raw, str) and raw:
        index = _ints(d, "frame_index", [])
        frames = frame_codec.decode_frames(base64.b64decode(raw), index, frame_width)
    # Before frames could each choose, a drawn layer scrolled every frame
    # whenever it had a speed at all, so that is what a missing flag list means.
    scroll = d.get("frame_scroll")
    if not isinstance(scroll, list):
        scroll = [speed > 0] * len(frames)
    return LayerConfig(
        name=_str(d, "name", ref.name),
        kind=_enum(d, "kind", ref.kind),
        muted=_bool(d, "muted", ref.muted),
        start=_int(d, "start", ref.start),
        width=_int(d, "width", ref.width),
        stride=_int(d, "stride", ref.stride),
        continuous=_bool(d, "continuous", ref.continuous),
        blend=_enum(d, "blend", ref.blend),
        opacity=_int(d, "opacity", ref.opacity),
        bindings=[_binding_from_dict(b) for b in _objs(d, "bindings")],
        color=_int(d, "color", ref.color),
        color2=_int(d, "color2", ref.color2),
        bg_color=_int(d, "bg_color", ref.bg_color),
        run_length=_int(d, "run_length", ref.run_length),
        speed=speed,
        step_ms=_int(d, "step_ms", ref.step_ms),
        invert=_bool(d, "invert", ref.invert),
        bounce=_bool(d, "bounce", ref.bounce),
        seamless=_bool(d, "seamless", ref.seamless),
        on_ms=_int(d, "on_ms", ref.on_ms),
        off_ms=_int(d, "off_ms", ref.off_ms),
        loop_ms=_int(d, "loop_ms", ref.loop_ms),
        start_ms=_int(d, "start_ms", ref.start_ms),
        end_ms=_int(d, "end_ms", ref.end_ms),
        fade_in_ms=_int(d, "fade_in_ms", ref.fade_in_ms),
        fade_out_ms=_int(d, "fade_out_ms", ref.fade_out_ms),
        retrigger=_bool(d, "retrigger", ref.retrigger),
        palette=_ints(d, "palette", ref.palette),
        density_pct=_int(d, "density_pct", ref.density_pct),
        fade_step=_int(d, "fade_step", ref.fade_step),
        segments=[_segment_from_dict(s) for s in _objs(d, "segments")],
        spacing=_int(d, "spacing", ref.spacing),
        repeating=_bool(d, "repeating", ref.repeating),
        level=_int(d, "level", ref.level),
        gradient=_bool(d, "gradient", ref.gradient),
        style=_enum(d, "style", ref.style),
        gauge_blend=_enum(d, "gauge_blend", ref.gauge_blend),
        stops=[_stop_from_dict(s) for s in _objs(d, "stops")],
        empty_at=_num(d, "empty_at", ref.empty_at),
        full_at=_num(d, "full_at", ref.full_at),
        frames=frames,
        frame_palette=_ints(d, "frame_palette", ref.frame_palette),
        frame_width=frame_width,
        frame_ms=_int(d, "frame_ms", ref.frame_ms),
        frame_scroll=[bool(v) for v in scroll],
    )


def _driver_to_dict(drv: DriverConfig) -> dict:
    return {
        "name": drv.name,
        "kind": drv.kind.value,
        "value": drv.value,
        "source": drv.source,
        "source_port": drv.source_port,
        "source_empty": drv.source_empty,
        "source_full": drv.source_full,
        "source_wrap": drv.source_wrap,
        "smoothing": drv.smoothing,
        "band": drv.band,
        "sensitivity": drv.sensitivity,
        "shape": drv.shape.value,
        "period_ms": drv.period_ms,
        "phase_pct": drv.phase_pct,
        "preview_level": drv.preview_level,
        "preview_sweep": drv.preview_sweep,
    }


def _driver_from_dict(d: dict) -> DriverConfig:
    ref = DriverConfig()
    return DriverConfig(
        name=_str(d, "name", ref.name),
        kind=_enum(d, "kind", ref.kind),
        value=_int(d, "value", ref.value),
        source=_str(d, "source", ref.source),
        source_port=_int(d, "source_port", ref.source_port),
        source_empty=_int(d, "source_empty", ref.source_empty),
        source_full=_int(d, "source_full", ref.source_full),
        source_wrap=_bool(d, "source_wrap", ref.source_wrap),
        smoothing=_int(d, "smoothing", ref.smoothing),
        band=_str(d, "band", ref.band),
        sensitivity=_int(d, "sensitivity", ref.sensitivity),
        shape=_enum(d, "shape", ref.shape),
        period_ms=_int(d, "period_ms", ref.period_ms),
        phase_pct=_int(d, "phase_pct", ref.phase_pct),
        preview_level=_int(d, "preview_level", ref.preview_level),
        preview_sweep=_bool(d, "preview_sweep", ref.preview_sweep),
    )


def composition_to_dict(comp: CompositionConfig) -> dict:
    return {
        "id": comp.id,
        "name": comp.name,
        "layers": [_layer_to_dict(layer) for layer in comp.layers],
        "drivers": [_driver_to_dict(d) for d in comp.drivers],
        "loop_ms": comp.loop_ms,
        "bg_color": comp.bg_color,
    }


def composition_from_dict(d: dict) -> CompositionConfig:
    ref = CompositionConfig()
    return CompositionConfig(
        id=_int(d, "id", ref.id),
        name=_str(d, "name", ref.name),
        layers=[_layer_from_dict(layer) for layer in _objs(d, "layers")],
        drivers=[_driver_from_dict(x) for x in _objs(d, "drivers")],
        loop_ms=_int(d, "loop_ms", ref.loop_ms),
        bg_color=_int(d, "bg_color", ref.bg_color),
    )


def document_to_dict(doc: Document) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "strands": [strand_to_dict(c) for c in doc.strands],
        "music": _music_to_dict(doc.music),
        "compositions": [composition_to_dict(c) for c in doc.compositions],
    }


def document_from_dict(d: dict) -> Document:
    """The Document `d` describes. Anything wrong with it that defaults cannot
    cover is a ValueError, the one error the File menu reports."""
    if not isinstance(d, dict):
        raise ValueError("This is not a Pattern Studio profile.")
    version = _as_int(d.get("schema_version"))
    if version is not None and version > SCHEMA_VERSION:
        raise ValueError(
            f"This file was saved by a newer Pattern Studio (schema {version}); this "
            f"version reads up to schema {SCHEMA_VERSION}. Update Pattern Studio to open it."
        )
    try:
        return Document(
            strands=[strand_from_dict(s) for s in _objs(d, "strands")],
            music=_music_from_dict(_obj(d, "music")),
            compositions=[composition_from_dict(c) for c in _objs(d, "compositions")],
        )
    except (TypeError, AttributeError, LookupError) as exc:
        # The getters above cover what a hand edit usually gets wrong. This is
        # whatever they don't, such as a drawn frame's index pointing outside
        # its own data.
        raise ValueError(f"This profile is damaged and can't be read ({exc}).") from exc


def save_document(path: str | Path, doc: Document) -> None:
    """Write `doc` to `path`, all or nothing.

    Written beside the target and then moved over it in one step. Writing in
    place truncated the old save first, so a write that failed partway - a
    full disk, a pulled USB stick - left neither the old design nor the new.
    """
    path = Path(path)
    text = json.dumps(document_to_dict(doc), indent=2)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def load_document(path: str | Path) -> Document:
    # utf-8-sig, not utf-8: it also reads a file some Windows editor saved with
    # a byte-order mark, which plain utf-8 hands to json as a stray character.
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return document_from_dict(data)
