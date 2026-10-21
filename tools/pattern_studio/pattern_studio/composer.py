"""Maps a CompositionConfig (declarative) onto hitlib_sim's Composition.

The same job engine.py does for AnimationConfig, kept separate because it is
the one piece both the preview and the reproduction tests need and neither
wants the rest of engine.py's strand plumbing.

A SENSOR driver is handed the same stand-in reader a Fill meter gets - see
engine._preview_reader. Only the number is invented; the range, wrap,
smoothing and the whole downstream path are the real code.
"""

from __future__ import annotations

from hitlib_sim import (
    Binding,
    BitScrollSegment,
    Composition,
    Driver,
    DriverKind,
    GaugeBlend,
    GaugeStop,
    GaugeStyle,
    Layer,
    LayerBlend,
    LayerKind,
    LayerParam,
    LfoShape,
    Speed,
)

from . import frame_codec
from .models import (
    CompositionConfig,
    DriverConfig,
    GaugeBlendKind,
    GaugeStyleKind,
    LayerConfig,
)
from .models import DriverKind as CfgDriverKind
from .models import LayerBlend as CfgLayerBlend
from .models import LayerKind as CfgLayerKind
from .models import LayerParam as CfgLayerParam
from .models import LfoShape as CfgLfoShape

_KIND = {k: LayerKind[k.name] for k in CfgLayerKind}
_BLEND = {b: LayerBlend[b.name] for b in CfgLayerBlend}
_PARAM = {p: LayerParam[p.name] for p in CfgLayerParam}
_DRIVER_KIND = {d: DriverKind[d.name] for d in CfgDriverKind}
_LFO = {s: LfoShape[s.name] for s in CfgLfoShape}
_GAUGE_STYLE = {GaugeStyleKind.HEAT: GaugeStyle.HEAT, GaugeStyleKind.BAR: GaugeStyle.BAR}
_GAUGE_BLEND = {GaugeBlendKind.LERP: GaugeBlend.LERP, GaugeBlendKind.STEP: GaugeBlend.STEP}


def _layer(cfg: LayerConfig) -> Layer:
    data, index = (frame_codec.encode_frames(cfg.frames, cfg.frame_width)
                   if cfg.frames else (b"", ()))
    return Layer(
        kind=_KIND[cfg.kind],
        name=cfg.name,
        start=cfg.start,
        width=cfg.width,
        stride=cfg.stride,
        continuous=cfg.continuous,
        blend=_BLEND[cfg.blend],
        opacity=cfg.opacity,
        bindings=tuple(
            Binding(param=_PARAM[b.param], driver=b.driver, at_zero=b.at_zero,
                    at_full=b.at_full, curve=tuple(b.curve) if b.curve else None)
            for b in cfg.bindings
        ),
        color=cfg.color,
        color2=cfg.color2,
        bg_color=cfg.bg_color,
        run_length=cfg.run_length,
        speed=Speed(cfg.speed, cfg.step_ms),
        invert=cfg.invert,
        bounce=cfg.bounce,
        seamless=cfg.seamless,
        on_ms=cfg.on_ms,
        off_ms=cfg.off_ms,
        loop_ms=cfg.loop_ms,
        start_ms=cfg.start_ms,
        end_ms=cfg.end_ms,
        fade_in_ms=cfg.fade_in_ms,
        fade_out_ms=cfg.fade_out_ms,
        retrigger=cfg.retrigger,
        palette=tuple(cfg.palette),
        density_pct=cfg.density_pct,
        fade_step=cfg.fade_step,
        segments=tuple(BitScrollSegment(color=s.color, width=s.width) for s in cfg.segments),
        spacing=cfg.spacing,
        repeating=cfg.repeating,
        level=cfg.level,
        gradient=cfg.gradient,
        style=_GAUGE_STYLE[cfg.style],
        gauge_blend=_GAUGE_BLEND[cfg.gauge_blend],
        stops=tuple(GaugeStop(at=s.at, color=s.color) for s in cfg.stops),
        empty_at=cfg.empty_at,
        full_at=cfg.full_at,
        frame_data=data,
        frame_index=index,
        frame_palette=tuple(cfg.frame_palette),
        frame_width=cfg.frame_width,
        frame_count=len(cfg.frames),
        frame_ms=cfg.frame_ms,
        frame_scroll=tuple(int(f) for f in cfg.scroll_flags()),
    )


def _driver(cfg: DriverConfig, reader=None, track=None) -> Driver:
    return Driver(
        kind=_DRIVER_KIND[cfg.kind],
        name=cfg.name,
        value=cfg.value,
        read=reader,
        empty_at=float(cfg.source_empty),
        full_at=float(cfg.source_full),
        wrap=cfg.source_wrap,
        smoothing=cfg.smoothing,
        track=track,
        sensitivity=cfg.sensitivity,
        loop=bool(track is not None and getattr(track, "loop", False)),
        shape=_LFO[cfg.shape],
        period_ms=cfg.period_ms,
        phase_pct=cfg.phase_pct,
    )


def flatten(cfg: CompositionConfig, readers=None, tracks=None, loop: bool = False) -> Composition:
    """The runtime Composition for `cfg`.

    `readers` maps a driver index to the callable a SENSOR driver polls, and
    `tracks` a driver index to the MusicTrack a MUSIC driver plays. Both are
    supplied by the caller because neither exists in the saved model - one is
    a stand-in the preview invents, the other is baked from the document's
    song.

    A muted layer is dropped here rather than flagged downstream, so the
    preview and the export agree about what "muted" means: not present.
    """
    readers = readers or {}
    tracks = tracks or {}
    layers = tuple(_layer(layer) for layer in cfg.layers if not layer.muted)
    drivers = tuple(
        _driver(d, readers.get(i), tracks.get(i)) for i, d in enumerate(cfg.drivers)
    )
    # `loop` belongs to the song, not to any one driver, so it is applied here
    # rather than read off a track.
    if loop:
        drivers = tuple(
            Driver(**{**d.__dict__, "loop": True}) if d.kind == DriverKind.MUSIC else d
            for d in drivers
        )
    return Composition(
        name=cfg.name,
        layers=layers,
        drivers=drivers,
        loop_ms=cfg.loop_ms,
        bg_color=cfg.bg_color,
    )
