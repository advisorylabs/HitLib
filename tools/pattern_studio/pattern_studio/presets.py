"""Every preset animation, expressed as a custom animation.

This is the hinge the whole Animator turns on. The owner's bar for 1.5 was
"you should be able to create any of the preset animations inside the custom
one", and this module is where that claim is cashed: one function that takes
any AnimationConfig and hands back the layer (and any driver) that renders
identically.

Three things share it, which is the point:

  * the Design tab's "Open as Custom..." button, which turns the animation a
    team has been tuning into a layer stack they can add to;
  * the Animator's "From Preset..." menu and its template gallery;
  * tests/test_custom_reproduces_presets.py, which ticks both and diffs them.

So if the conversion ever stops being faithful, three things break at once
rather than a button quietly producing the wrong animation.
"""

from __future__ import annotations

from .models import (
    AnimationConfig,
    AnimationKind,
    BindingConfig,
    BitScrollSegmentConfig,
    CompositionConfig,
    DriverConfig,
    DriverKind,
    LayerBlend,
    LayerConfig,
    LayerKind,
    LayerParam,
)

#: AnimationKind -> LayerKind, for the ones that map straight across.
_DIRECT = {
    AnimationKind.OFF: LayerKind.OFF,
    AnimationKind.SOLID: LayerKind.SOLID,
    AnimationKind.PULSE: LayerKind.PULSE,
    AnimationKind.FLASH: LayerKind.FLASH,
    AnimationKind.FLOW: LayerKind.FLOW,
    AnimationKind.RAINBOW: LayerKind.RAINBOW,
    AnimationKind.TWINKLE: LayerKind.TWINKLE,
    AnimationKind.BITSCROLL: LayerKind.BITSCROLL,
    AnimationKind.FILL: LayerKind.METER,
    AnimationKind.MUSIC: LayerKind.METER,
}


def layer_for_animation(a: AnimationConfig,
                        name: str = "") -> tuple[LayerConfig, list[DriverConfig]]:
    """The layer that renders `a` exactly, plus any driver it needs.

    The layer blends OVER, not KEY, and that is deliberate: OVER ignores
    coverage, so the layer paints its own background exactly as the preset
    paints its own. KEY is for stacking a *second* layer, and is what the
    Animator defaults to for every layer after the first.

    A Fill or Music Sync animation is a meter, and a meter needs something to
    move it - so those two come back with one driver and a LEVEL binding
    already wired. Everything else returns no drivers at all.
    """
    if a.kind == AnimationKind.CUSTOM:
        raise ValueError("a custom animation is already a composition")

    kind = _DIRECT[a.kind]
    layer = LayerConfig(
        name=name or _default_name(a.kind),
        kind=kind,
        blend=LayerBlend.OVER,
        opacity=255,
        color=a.color,
        color2=a.color2,
        bg_color=a.bg_color,
        run_length=a.run_length,
        speed=a.speed,
        step_ms=a.step_ms,
        invert=a.invert,
        bounce=a.bounce,
        seamless=a.seamless,
        on_ms=a.on_ms,
        off_ms=a.off_ms,
        palette=list(a.palette),
        density_pct=a.density_pct,
        fade_step=a.fade_step,
        # The preset has only ever offered one segment, even though the C++
        # has always taken a vector. A layer can hold several, and this is
        # where a converted preset stops being limited to one.
        segments=[BitScrollSegmentConfig(color=a.color, width=a.segment_width)],
        spacing=a.spacing,
        repeating=a.repeating,
        gradient=a.gradient,
    )
    if a.kind == AnimationKind.OFF:
        # off() is black, whatever colors the config kept from the kind it was
        # switched from - and an OFF layer paints its background.
        layer.color = layer.bg_color = 0x000000

    drivers: list[DriverConfig] = []
    if a.kind == AnimationKind.FILL:
        drivers.append(DriverConfig(
            name="Level",
            kind=DriverKind.SENSOR,
            source=a.source,
            source_port=a.source_port,
            source_empty=a.source_empty,
            source_full=a.source_full,
            source_wrap=a.source_wrap,
            smoothing=a.smoothing,
            preview_level=a.preview_level,
            preview_sweep=a.preview_sweep,
        ))
        layer.bindings = [BindingConfig(param=LayerParam.LEVEL, driver=0,
                                        at_zero=0, at_full=255)]
    elif a.kind == AnimationKind.MUSIC:
        drivers.append(DriverConfig(
            name="Song",
            kind=DriverKind.MUSIC,
            band=a.band,
            sensitivity=a.sensitivity,
        ))
        layer.bindings = [BindingConfig(param=LayerParam.LEVEL, driver=0,
                                        at_zero=0, at_full=255)]

    return layer, drivers


def composition_for_animation(a: AnimationConfig, name: str = "",
                              comp_id: int = 1) -> CompositionConfig:
    """`a` as a one-layer composition, ready to open in the Animator."""
    layer, drivers = layer_for_animation(a)
    return CompositionConfig(
        id=comp_id,
        name=name or _default_name(a.kind),
        layers=[layer],
        drivers=drivers,
        # A converted preset has no timeline: it is whatever it is, always.
        loop_ms=0,
        bg_color=0x000000,
    )


def _default_name(kind: AnimationKind) -> str:
    from .models import ANIMATION_KIND_LABELS
    return ANIMATION_KIND_LABELS.get(kind, "Layer")
