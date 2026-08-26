"""(De)serializes StrandConfig trees to/from JSON.

This is both the GUI's native save format and the thing teammates share -
one format, not two. Every *_from_dict() reads via dict.get() with a default
pulled from a fresh dataclass instance, so older or hand-edited files with
missing fields load with sane defaults instead of raising, and adding a new
field later doesn't break existing saved files.
"""

from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path

from .envelope import EnvelopeMode, EnvelopeSettings, TrackAnalysis
from .models import (
    AnimationConfig,
    AnimationKind,
    Document,
    GaugeBlendKind,
    GaugeStopConfig,
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
SCHEMA_VERSION = 5


def _animation_to_dict(a: AnimationConfig) -> dict:
    return {
        "kind": a.kind.value,
        "color": a.color,
        "color2": a.color2,
        "bg_color": a.bg_color,
        "run_length": a.run_length,
        "speed": a.speed,
        "on_ms": a.on_ms,
        "off_ms": a.off_ms,
        "invert": a.invert,
        "bounce": a.bounce,
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
    }


def _animation_from_dict(d: dict) -> AnimationConfig:
    default = AnimationConfig()
    return AnimationConfig(
        kind=AnimationKind(d.get("kind", default.kind.value)),
        color=d.get("color", default.color),
        color2=d.get("color2", default.color2),
        bg_color=d.get("bg_color", default.bg_color),
        run_length=d.get("run_length", default.run_length),
        speed=d.get("speed", default.speed),
        on_ms=d.get("on_ms", default.on_ms),
        off_ms=d.get("off_ms", default.off_ms),
        invert=d.get("invert", default.invert),
        bounce=d.get("bounce", default.bounce),
        density_pct=d.get("density_pct", default.density_pct),
        fade_step=d.get("fade_step", default.fade_step),
        palette=list(d.get("palette", default.palette)),
        segment_width=d.get("segment_width", default.segment_width),
        spacing=d.get("spacing", default.spacing),
        repeating=d.get("repeating", default.repeating),
        source=d.get("source", default.source),
        source_port=d.get("source_port", default.source_port),
        source_empty=d.get("source_empty", default.source_empty),
        source_full=d.get("source_full", default.source_full),
        source_wrap=d.get("source_wrap", default.source_wrap),
        smoothing=d.get("smoothing", default.smoothing),
        preview_level=d.get("preview_level", default.preview_level),
        preview_sweep=d.get("preview_sweep", default.preview_sweep),
        gradient=d.get("gradient", default.gradient),
        sensitivity=d.get("sensitivity", default.sensitivity),
        band=d.get("band", default.band),
    )


def _stop_to_dict(stop: GaugeStopConfig) -> dict:
    return {"at": stop.at, "color": stop.color}


def _stop_from_dict(d: dict) -> GaugeStopConfig:
    default = GaugeStopConfig()
    return GaugeStopConfig(at=d.get("at", default.at), color=d.get("color", default.color))


def _overlay_to_dict(o: OverlayAnimationConfig) -> dict:
    return {
        "kind": o.kind.value,
        "color": o.color,
        "color2": o.color2,
        "bg_color": o.bg_color,
        "run_length": o.run_length,
        "speed": o.speed,
        "on_ms": o.on_ms,
        "off_ms": o.off_ms,
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
        kind=OverlayAnimationKind(d.get("kind", default.kind.value)),
        color=d.get("color", default.color),
        color2=d.get("color2", default.color2),
        bg_color=d.get("bg_color", default.bg_color),
        run_length=d.get("run_length", default.run_length),
        speed=d.get("speed", default.speed),
        on_ms=d.get("on_ms", default.on_ms),
        off_ms=d.get("off_ms", default.off_ms),
        source=d.get("source", default.source),
        source_port=d.get("source_port", default.source_port),
        source_empty=d.get("source_empty", default.source_empty),
        source_full=d.get("source_full", default.source_full),
        source_wrap=d.get("source_wrap", default.source_wrap),
        smoothing=d.get("smoothing", default.smoothing),
        invert=d.get("invert", default.invert),
        style=GaugeStyleKind(d.get("style", default.style.value)),
        blend=GaugeBlendKind(d.get("blend", default.blend.value)),
        stops=[_stop_from_dict(stop) for stop in d.get("stops", [])],
        preview_level=d.get("preview_level", default.preview_level),
        preview_sweep=d.get("preview_sweep", default.preview_sweep),
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
        start=d.get("start", default.start),
        width=d.get("width", default.width),
        animation=_overlay_from_dict(d.get("animation", {})),
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
        enabled=d.get("enabled", default.enabled),
        mode=SpliceModeKind(d.get("mode", default.mode.value)),
        sections=d.get("sections", default.sections),
        invert=d.get("invert", default.invert),
        alternating=d.get("alternating", default.alternating),
        alt_period_ms=d.get("alt_period_ms", default.alt_period_ms),
        bg_color=d.get("bg_color", default.bg_color),
        use_overlay=d.get("use_overlay", default.use_overlay),
        regions=[_region_from_dict(r) for r in d.get("regions", [])],
        overlay=_overlay_from_dict(d.get("overlay", {})),
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
        name=d.get("name", default.name),
        duration_ms=d.get("duration_ms", default.duration_ms),
        animation=_animation_from_dict(d.get("animation", {})),
        splice=_splice_from_dict(d.get("splice", {})),
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
        name=d.get("name", default.name),
        priority=d.get("priority", default.priority),
        animation=_animation_from_dict(d.get("animation", {})),
        splice=_splice_from_dict(d.get("splice", {})),
        phases=[_phase_from_dict(p) for p in d.get("phases", [])],
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
        name=d.get("name", default.name),
        adi_port=d.get("adi_port", default.adi_port),
        smart_port=d.get("smart_port", default.smart_port),
        length=d.get("length", default.length),
        refresh_ms=d.get("refresh_ms", default.refresh_ms),
        brightness=d.get("brightness", default.brightness),
        animation=_animation_from_dict(d.get("animation", {})),
        splice=_splice_from_dict(d.get("splice", {})),
        use_profile=d.get("use_profile", default.use_profile),
        profile_modes=[_mode_from_dict(m) for m in d.get("profile_modes", [])],
        active_mode_indices=list(d.get("active_mode_indices", [])),
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
        return [max(0, min(255, int(v))) for v in raw]
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
        mode=EnvelopeMode(d.get("mode", default.mode.value)),
        smooth_ms=d.get("smooth_ms", default.smooth_ms),
        attack_ms=d.get("attack_ms", default.attack_ms),
        release_ms=d.get("release_ms", default.release_ms),
        auto_gain=d.get("auto_gain", default.auto_gain),
        contrast=d.get("contrast", default.contrast),
        range_db=d.get("range_db", default.range_db),
        frame_ms=d.get("frame_ms", default.frame_ms),
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
    bands = {name: _unpack(raw) for name, raw in d.get("bands", {}).items()}
    return TrackAnalysis(
        bands={name: values for name, values in bands.items() if values},
        frame_ms=d.get("frame_ms", default.frame_ms),
        name=d.get("name", default.name),
        source_path=d.get("source_path", default.source_path),
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
        name=d.get("name", default.name),
        source_path=d.get("source_path", default.source_path),
        source_kind=d.get("source_kind", default.source_kind),
        tracks=list(d.get("tracks", [])),
        loop=d.get("loop", default.loop),
        settings=_settings_from_dict(d.get("settings", {})),
        analysis=_analysis_from_dict(d.get("analysis", {})),
    )


def document_to_dict(doc: Document) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "strands": [strand_to_dict(c) for c in doc.strands],
        "music": _music_to_dict(doc.music),
    }


def document_from_dict(d: dict) -> Document:
    return Document(
        strands=[strand_from_dict(s) for s in d.get("strands", [])],
        music=_music_from_dict(d.get("music", {})),
    )


def save_document(path: str | Path, doc: Document) -> None:
    Path(path).write_text(json.dumps(document_to_dict(doc), indent=2), encoding="utf-8")


def load_document(path: str | Path) -> Document:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return document_from_dict(data)
