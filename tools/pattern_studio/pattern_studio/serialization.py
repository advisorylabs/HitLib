"""(De)serializes StrandConfig trees to/from JSON.

This is both the GUI's native save format and the thing teammates share -
one format, not two. Every *_from_dict() reads via dict.get() with a default
pulled from a fresh dataclass instance, so older or hand-edited files with
missing fields load with sane defaults instead of raising, and adding a new
field later doesn't break existing saved files.
"""

from __future__ import annotations

import json
from pathlib import Path

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

SCHEMA_VERSION = 1


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
    )


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


def document_to_dict(strand_configs: list[StrandConfig]) -> dict:
    return {"schema_version": SCHEMA_VERSION, "strands": [strand_to_dict(c) for c in strand_configs]}


def document_from_dict(d: dict) -> list[StrandConfig]:
    return [strand_from_dict(s) for s in d.get("strands", [])]


def save_document(path: str | Path, strand_configs: list[StrandConfig]) -> None:
    Path(path).write_text(json.dumps(document_to_dict(strand_configs), indent=2), encoding="utf-8")


def load_document(path: str | Path) -> list[StrandConfig]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return document_from_dict(data)
