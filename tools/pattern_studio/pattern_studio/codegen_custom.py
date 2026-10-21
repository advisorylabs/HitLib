"""Exports the Animator's compositions as C++.

A composition becomes a block of namespace-scope constants under
`hitlib::profiles::anim::<name>`: the tables a Layer and a Driver point at,
then the `Layer[]`, the `Driver[]`, and the `Composition` itself. Robot code
plays one with `strand.compose(anim::heatWash::composition)`.

It sits outside any strand's namespace because a composition belongs to the
document, not to one strip - two strands can play the same one, and the
export should say so rather than emit it twice.

**Both dialects emit character-identical table bodies.** That is not a
coincidence, it is the reason LedStrand::Layer and LedStrand::Driver carry no
default member initializers: an aggregate with none is brace-initializable in
C++11 as well as C++17, which keeps the table `const` and therefore in flash
on both platforms. Only the *declaration* around it differs, and that is
_variable()'s existing job.
"""

from __future__ import annotations

from . import frame_codec
from .models import (
    CompositionConfig,
    DriverConfig,
    DriverKind,
    LayerConfig,
    LayerKind,
)
from .platforms import Platform

#: Values per line in a frame-data table, so a drawn animation stays readable.
_BYTES_PER_LINE = 16


def _hex(color: int) -> str:
    return f"0x{color & 0xFFFFFF:06X}"


def _bool(b: bool) -> str:
    return "true" if b else "false"


def _speed(pixels: int, step_ms: int) -> str:
    """A LedStrand::Speed, as codegen._speed() writes one: a bare number is
    pixels per refresh tick."""
    if not step_ms:
        return str(pixels)
    if pixels == 1:
        return f"LedStrand::Speed::everyMs({step_ms})"
    return f"LedStrand::Speed::everyMs({step_ms}, {pixels})"


def _double(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def _quoted(name: str) -> str:
    """A user-typed name as a C++ string literal, escaped the way codegen
    escapes the mode and strand names it quotes."""
    # Imported here rather than at the top: codegen imports this module.
    from .codegen import _escape
    return f'"{_escape(name)}"'


def _curve_name(layer_var: str, idx: int) -> str:
    return f"{layer_var}Curve{idx}"


# ============================================================================
# The tables a layer or driver points at
# ============================================================================


def _layer_tables(prefix: str, i: int, layer: LayerConfig, variable) -> tuple[list[str], dict]:
    """The arrays this layer needs, plus the names to reference them by."""
    lines: list[str] = []
    names = {"palette": "nullptr", "segments": "nullptr", "stops": "nullptr",
             "fdata": "nullptr", "findex": "nullptr", "fpalette": "nullptr",
             "fscroll": "nullptr"}
    var = f"{prefix}{i}"

    if layer.kind == LayerKind.TWINKLE and layer.palette:
        name = f"{var}Palette"
        body = ", ".join(_hex(c) for c in layer.palette)
        lines.append(variable("const uint32_t", name, f" = {{{body}}}", len(layer.palette)))
        names["palette"] = name

    if layer.kind == LayerKind.BITSCROLL and layer.segments:
        name = f"{var}Segments"
        body = ", ".join(f"{{{_hex(s.color)}, {s.width}}}" for s in layer.segments)
        lines.append(variable("const LedStrand::BitScrollSegment", name,
                              f" = {{{body}}}", len(layer.segments)))
        names["segments"] = name

    if layer.kind in (LayerKind.GAUGE, LayerKind.METER) and layer.stops:
        name = f"{var}Stops"
        body = ", ".join(f"{{{_double(s.at)}, {_hex(s.color)}}}" for s in layer.stops)
        lines.append(variable("const LedStrand::GaugeStop", name,
                              f" = {{{body}}}", len(layer.stops)))
        names["stops"] = name

    if layer.kind == LayerKind.FRAMES and layer.frames:
        data, index = frame_codec.encode_frames(layer.frames, layer.frame_width)
        name = f"{var}Frames"
        rows = []
        for at in range(0, len(data), _BYTES_PER_LINE):
            rows.append("    " + ", ".join(str(b) for b in data[at:at + _BYTES_PER_LINE]) + ",")
        lines.append("// Run-length coded: (run - 1) << 5 | palette index. Index 0 is")
        lines.append("// transparent, so the layers below show through around the drawing.")
        lines.append(variable("const uint8_t", name, " = {\n" + "\n".join(rows) + "\n}",
                              len(data)))
        names["fdata"] = name

        iname = f"{var}FrameIndex"
        lines.append(variable("const uint16_t", iname,
                              " = {" + ", ".join(str(v) for v in index) + "}", len(index)))
        names["findex"] = iname

        pname = f"{var}FramePalette"
        body = ", ".join(_hex(c) for c in layer.frame_palette)
        lines.append(variable("const uint32_t", pname, f" = {{{body}}}",
                              len(layer.frame_palette)))
        names["fpalette"] = pname

        # Only when some frame scrolls: nullptr already means "hold them all",
        # which is what most drawings want.
        flags = layer.scroll_flags()
        if any(flags):
            sname = f"{var}FrameScroll"
            body = ", ".join("1" if f else "0" for f in flags)
            lines.append(variable("const uint8_t", sname, f" = {{{body}}}", len(flags)))
            names["fscroll"] = sname

    for j, b in enumerate(layer.bindings[:4]):
        if b.curve:
            cname = _curve_name(var, j)
            body = ", ".join(str(v) for v in b.curve)
            lines.append(variable("const uint8_t", cname, f" = {{{body}}}", len(b.curve)))

    return lines, names


def _binding(var: str, j: int, b) -> str:
    curve = _curve_name(var, j) if b.curve else "nullptr"
    return (f"{{LedStrand::LayerParam::{b.param.name}, {b.driver}, "
            f"{b.at_zero}, {b.at_full}, {curve}}}")


def _layer_literal(prefix: str, i: int, layer: LayerConfig, names: dict) -> str:
    """One Layer, as the same characters on both platforms.

    Written group by group on its own lines rather than as one long literal:
    a layer has thirteen groups, and a reader who wants to know what colour
    it is should not have to count commas to find out.
    """
    var = f"{prefix}{i}"
    bindings = list(layer.bindings[:4])
    binds = ", ".join(_binding(var, j, b) for j, b in enumerate(bindings))
    binds = binds + (", " if binds else "") + ", ".join("{}" for _ in range(4 - len(bindings)))

    frame_count = len(layer.frames)
    return "\n".join([
        f"    {{{_quoted(layer.name)}, LedStrand::LayerKind::{layer.kind.name},",
        f"     /* span    */ {{{layer.start}, {layer.width}, {layer.stride}, "
        f"{_bool(layer.continuous)}}},",
        f"     /* mix     */ {{LedStrand::LayerBlend::{layer.blend.name}, {layer.opacity}}},",
        f"     /* binds   */ {{{binds}}},",
        f"     /* paint   */ {{{_hex(layer.color)}, {_hex(layer.color2)}, "
        f"{_hex(layer.bg_color)}}},",
        f"     /* motion  */ {{{layer.run_length}, {_speed(layer.speed, layer.step_ms)}, "
        f"{_bool(layer.invert)}, "
        f"{_bool(layer.bounce)}, {_bool(layer.seamless)}}},",
        f"     /* flash   */ {{{layer.on_ms}, {layer.off_ms}}},",
        f"     /* time    */ {{{layer.loop_ms}, {layer.start_ms}, {layer.end_ms}, "
        f"{layer.fade_in_ms}, {layer.fade_out_ms}, {_bool(layer.retrigger)}}},",
        f"     /* sparkle */ {{{names['palette']}, {len(layer.palette)}, "
        f"{layer.density_pct}, {layer.fade_step}}},",
        f"     /* pattern */ {{{names['segments']}, {len(layer.segments)}, "
        f"{layer.spacing}, {_bool(layer.repeating)}}},",
        f"     /* gauge   */ {{{layer.level}, {_bool(layer.gradient)}, "
        f"LedStrand::GaugeStyle::{layer.style.name}, "
        f"LedStrand::GaugeBlend::{layer.gauge_blend.name}, "
        f"{names['stops']}, {len(layer.stops)}, "
        f"{_double(layer.empty_at)}, {_double(layer.full_at)}}},",
        f"     /* frames  */ {{{names['fdata']}, {names['findex']}, {names['fpalette']}, "
        f"{len(layer.frame_palette)}, {layer.frame_width}, {frame_count}, "
        f"{layer.frame_ms}, {names['fscroll']}}}}},",
    ])


def _driver_literal(drv: DriverConfig, reader: str, track: str, loop: bool) -> str:
    # `loop` belongs to the song, not to any one driver, so it comes from
    # the document rather than from the driver's own fields - the same way
    # musicSync() takes it.
    return (
        f"    {{{_quoted(drv.name)}, LedStrand::DriverKind::{drv.kind.name}, {drv.value}, "
        f"{reader}, {_double(drv.source_empty)}, {_double(drv.source_full)}, "
        f"{_bool(drv.source_wrap)}, {drv.smoothing}, "
        f"{track}, {drv.sensitivity}, {_bool(loop)}, "
        f"LedStrand::LfoShape::{drv.shape.name}, {drv.period_ms}, {drv.phase_pct}}},"
    )


# ============================================================================
# The whole composition
# ============================================================================


def render_composition(cfg: CompositionConfig, var_name: str, platform: Platform,
                       variable, sources, music_ref, open_ns, close_ns) -> list[str]:
    """One composition as a `namespace <var_name>` block.

    `variable`, `open_ns` and `close_ns` are codegen's own helpers, passed in
    rather than imported: codegen imports this module, and importing it back
    would be a cycle. `sources` is a _FillSourceSet scoped to this
    composition, so its readers land in this composition's own `source::`
    rather than in some strand's.
    """
    body: list[str] = []
    table: list[str] = []

    for i, layer in enumerate(cfg.layers):
        if layer.muted:
            continue
        lines, names = _layer_tables("layer", i, layer, variable)
        body.extend(lines)
        table.append(_layer_literal("layer", i, layer, names))

    driver_lines: list[str] = []
    for drv in cfg.drivers:
        reader, track = "nullptr", "nullptr"
        if drv.kind == DriverKind.SENSOR:
            reader = sources.reader_for(drv, f"{cfg.name} {drv.name}") or "nullptr"
        elif drv.kind == DriverKind.MUSIC and music_ref is not None:
            expr = music_ref.expr_for(drv.band)
            if expr is not None:
                track = f"&{expr}"
        loop = bool(music_ref is not None and music_ref.loop
                    and drv.kind == DriverKind.MUSIC)
        driver_lines.append(_driver_literal(drv, reader, track, loop))

    out: list[str] = [open_ns(platform, var_name)]
    out.append("")

    if sources.lines:
        out.append(open_ns(platform, "source"))
        out.extend(sources.lines)
        out.append(close_ns(platform, "source"))
        out.append("")

    out.extend(body)
    if body:
        out.append("")

    if table:
        out.append(variable("const LedStrand::Layer", "layers",
                            " = {\n" + "\n".join(table) + "\n}", len(table)))
    else:
        # A composition with nothing in it still has to name an array: an
        # empty one is not a valid C++ aggregate, so it gets a single OFF
        # layer, which is exactly what it renders as anyway.
        out.append(variable("const LedStrand::Layer", "layers",
                            ' = {\n    {"", LedStrand::LayerKind::OFF, {0, 0, 0, false},'
                            " {LedStrand::LayerBlend::OVER, 255}, {{}, {}, {}, {}},"
                            " {0x000000, 0x000000, 0x000000}, {0, 0, false, false, false},"
                            " {0, 0}, {0, 0, 0, 0, 0, false}, {nullptr, 0, 0, 0},"
                            " {nullptr, 0, 0, false},"
                            " {0, false, LedStrand::GaugeStyle::HEAT,"
                            " LedStrand::GaugeBlend::LERP, nullptr, 0, 0.0, 100.0},"
                            " {nullptr, nullptr, nullptr, 0, 0, 0, 0}},\n}", 1))

    layer_count = max(len(table), 1)
    if driver_lines:
        out.append("")
        out.append(variable("const LedStrand::Driver", "drivers",
                            " = {\n" + "\n".join(driver_lines) + "\n}", len(driver_lines)))
        drivers_expr, driver_count = "drivers", len(driver_lines)
    else:
        drivers_expr, driver_count = "nullptr", 0

    out.append("")
    out.append("/// Play this with strand.compose(...).")
    out.append(variable(
        "const LedStrand::Composition", "composition",
        f" = {{{_quoted(cfg.name)}, layers, {layer_count}, {drivers_expr}, {driver_count}, "
        f"{cfg.loop_ms}u, {_hex(cfg.bg_color)}}}"))

    if any(d.kind == DriverKind.CONSTANT for d in cfg.drivers):
        out.append("")
        out.append("/// Indices for setDriverLevel(), so robot code names a driver")
        out.append("/// rather than counting them - and a reordered driver becomes a")
        out.append("/// compile error instead of a wrong animation.")
        out.append(open_ns(platform, "driver"))
        namer = set()
        for i, drv in enumerate(cfg.drivers):
            ident = _identifier(drv.name, f"driver{i}", namer)
            out.append(f"constexpr uint8_t {ident} = {i};")
        out.append(close_ns(platform, "driver"))

    out.append("")
    out.append(close_ns(platform, var_name))
    return out


def _identifier(name: str, fallback: str, seen: set) -> str:
    """A C++ identifier from a user-typed driver name, unique within its
    namespace."""
    import re
    from .codegen import _CPP_KEYWORDS
    words = re.findall(r"[A-Za-z0-9]+", name)
    if not words or words[0][0].isdigit():
        ident = fallback
    else:
        ident = words[0][0].lower() + words[0][1:] + "".join(w.capitalize() for w in words[1:])
    if ident in _CPP_KEYWORDS:
        # "Auto" is a natural name for a driver and not a usable identifier.
        ident += "Driver"
    base, n = ident, 2
    while ident in seen:
        ident = f"{base}{n}"
        n += 1
    seen.add(ident)
    return ident
