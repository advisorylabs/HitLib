#include "hitlib/led_strand.hpp"

#include <algorithm>
#include <cmath>

/**
 * @file led_custom.cpp
 * @brief The Composition evaluator - Pattern Studio's custom animations.
 *
 * Split out of led_strand.cpp, which is long enough already. Everything here
 * is a member of LedStrand.
 *
 * Two rules hold throughout:
 *
 *  1. **Nothing reachable from tick() allocates.** Every buffer a layer needs
 *     is measured and reserved in compose(), in one grow-only arena.
 *  2. **A generator is never written twice.** Each case below calls the same
 *     body the matching preset calls - gradientPixel(), wheel(), stepTwinkle(),
 *     buildBitscrollUnit(), scaleColorAt(), mapLevelTo(), smoothLevelTo(),
 *     sampleTrackNL(). That is what makes "a custom layer reproduces its
 *     preset exactly" a structural property rather than a promise a test has
 *     to keep chasing.
 */

namespace hitlib {

namespace {

/// Quarter-wave sine, 64 entries, mirrored and offset into a full 0-255 cycle
/// by lfoValue(). A table rather than std::sin so the Python mirror can
/// reproduce it byte for byte with no floating-point rounding to argue about.
const uint8_t SINE_TABLE[64] = {
    128, 131, 134, 137, 140, 143, 146, 149, 152, 155, 158, 162, 165, 167, 170, 173,
    176, 179, 182, 185, 188, 190, 193, 196, 198, 201, 203, 206, 208, 211, 213, 215,
    218, 220, 222, 224, 226, 228, 230, 232, 234, 235, 237, 238, 240, 241, 243, 244,
    245, 246, 248, 249, 250, 250, 251, 252, 253, 253, 254, 254, 254, 255, 255, 255,
};

} // namespace

// ============================================================================
// Small integer helpers
// ============================================================================

uint32_t LedStrand::addSat(uint32_t a, uint32_t b) {
    uint32_t r = ((a >> 16) & 0xFF) + ((b >> 16) & 0xFF);
    uint32_t g = ((a >> 8) & 0xFF) + ((b >> 8) & 0xFF);
    uint32_t bl = (a & 0xFF) + (b & 0xFF);
    if (r > 255) r = 255;
    if (g > 255) g = 255;
    if (bl > 255) bl = 255;
    return (r << 16) | (g << 8) | bl;
}

uint32_t LedStrand::screenColor(uint32_t a, uint32_t b) {
    uint32_t out = 0;
    for (int shift = 16; shift >= 0; shift -= 8) {
        uint32_t x = (a >> shift) & 0xFF, y = (b >> shift) & 0xFF;
        uint32_t v = 255u - ((255u - x) * (255u - y)) / 255u;
        out |= v << shift;
    }
    return out;
}

uint32_t LedStrand::mulColor(uint32_t a, uint32_t b) {
    uint32_t out = 0;
    for (int shift = 16; shift >= 0; shift -= 8) {
        uint32_t x = (a >> shift) & 0xFF, y = (b >> shift) & 0xFF;
        out |= ((x * y) / 255u) << shift;
    }
    return out;
}

// Rec. 601 luma, integer. Used only by MASK, to turn a colored layer into a
// single "how much of what is underneath survives".
uint8_t LedStrand::luma(uint32_t c) {
    uint32_t r = (c >> 16) & 0xFF, g = (c >> 8) & 0xFF, b = c & 0xFF;
    return (uint8_t)((r * 77u + g * 150u + b * 29u) >> 8);
}

uint8_t LedStrand::applyCurve(const uint8_t* curve, uint8_t v) {
    if (!curve) return v;
    uint16_t scaled = (uint16_t)v * (uint16_t)(CURVE_POINTS - 1); // 0 .. 255*16
    uint8_t  i      = (uint8_t)(scaled / 255);                    // 0 .. 16
    if (i >= CURVE_POINTS - 1) return curve[CURVE_POINTS - 1];
    uint8_t frac = (uint8_t)(scaled % 255);
    return (uint8_t)(curve[i] + ((int)(curve[i + 1] - curve[i]) * frac) / 255);
}

// pulseNL()/flowNL()'s shiftVariant, exactly.
uint8_t LedStrand::shiftFor(uint8_t speed, uint8_t ring, bool invert) {
    if (ring == 0) return 0;
    uint8_t sp = (uint8_t)(speed % ring);
    return invert ? (uint8_t)((ring - sp) % ring) : sp;
}

// Wall-clock, like musicSync(), so a strand that misses a tick stays in phase
// rather than drifting behind.
uint8_t LedStrand::lfoValue(const Driver& d, uint32_t nowMs) {
    uint32_t period = d.periodMs ? d.periodMs : 1;
    uint32_t t = (nowMs + (uint32_t)d.phasePct * period / 100) % period;
    uint32_t x = t * 255u / period; // 0-254 across the period
    switch (d.shape) {
    case LfoShape::SAW:       return (uint8_t)x;
    case LfoShape::RAMP_DOWN: return (uint8_t)(255 - x);
    case LfoShape::SQUARE:    return x < 128 ? 255 : 0;
    case LfoShape::SINE: {
        // Quarter-wave lookup: fold 0-255 into the 64-entry table.
        uint8_t q = (uint8_t)(x & 0x3F);
        switch ((x >> 6) & 0x3) {
        case 0:  return SINE_TABLE[q];
        case 1:  return SINE_TABLE[63 - q];
        case 2:  return (uint8_t)(255 - SINE_TABLE[q]);
        default: return (uint8_t)(255 - SINE_TABLE[63 - q]);
        }
    }
    default: // TRIANGLE
        return (uint8_t)(255 - std::abs((int)(x * 2) - 255));
    }
}

/// Two layers with overlapping windows and matching fades *are* a crossfade.
uint8_t LedStrand::layerWindowAlpha(const Layer& l, uint32_t t, uint32_t period) {
    const LayerTime& w = l.time;
    if (w.startMs == 0 && w.endMs == 0) return 255;
    uint32_t begin = w.startMs;
    uint32_t end   = w.endMs ? w.endMs : (period ? period : 0xFFFFFFFFu);
    // The loop never reaches a window that starts at or past its end - which
    // a shortened loop leaves behind - and one that ends past it ends there.
    // Read as a wrap instead, the first would be lit for good.
    if (period && begin >= period) return 0;
    if (period && end > period) end = period;
    // A window whose end is at or before its start runs across the loop point,
    // which is how a layer stays lit through the wrap.
    bool wrapped = (end <= begin) && period;
    bool inside  = wrapped ? (t >= begin || t < end) : (t >= begin && t < end);
    if (!inside) return 0;

    uint32_t since = (wrapped && t < end)    ? (t + (period - begin)) : (t - begin);
    uint32_t until = (wrapped && t >= begin) ? (end + (period - t))   : (end - t);
    if (w.fadeInMs  && since < w.fadeInMs)  return (uint8_t)(since * 255u / w.fadeInMs);
    if (w.fadeOutMs && until < w.fadeOutMs) return (uint8_t)(until * 255u / w.fadeOutMs);
    return 255;
}

uint32_t LedStrand::blendPixel(LayerBlend m, uint32_t dst, uint32_t src, uint8_t a) {
    // MASK reads the layer, not the layer's coverage: it acts across the whole
    // span, dimming what is under it. That is what makes it a stencil. Alpha
    // then blends the masked result in like any other mode's, so a fading
    // mask eases in from untouched rather than from black.
    if (m == LayerBlend::MASK) return lerpColor(dst, lerpColor(0x000000u, dst, luma(src)), a);
    if (a == 0) return dst;
    uint32_t out;
    switch (m) {
    case LayerBlend::ADD:      out = addSat(dst, src);      break;
    case LayerBlend::SCREEN:   out = screenColor(dst, src); break;
    case LayerBlend::MULTIPLY: out = mulColor(dst, src);    break;
    default:                   out = src;                   break; // OVER, KEY
    }
    return (a == 255) ? out : lerpColor(dst, out, a);
}

// ============================================================================
// Install / tear down
// ============================================================================

void LedStrand::compose(const Composition& comp) {
    mutex.take();

    // Re-installing what is already playing is a no-op. Sequencer::update()
    // fires a phase's startFn on every transition, so a 200 ms phase would
    // otherwise rebuild every pattern five times a second.
    if (composition == &comp) {
        mutex.give();
        return;
    }

    composition = &comp;
    ++composeGeneration;
    composeAnchorMs      = platform::millis();
    composeMusicPausedAt = 0;
    composeMusicPaused   = false;

    // The composition's own drivers take these over.
    levelRead  = nullptr;
    musicTrack = nullptr;
    // And it takes the base buffer over from whatever preset had it.
    pulseRunLen = 0;
    bitscrollMaster.clear();
    animMode     = AnimMode::STATIC;
    shiftStep    = 0;
    shiftVariant = 0;

    uint8_t n = std::min<uint8_t>(comp.layerCount, MAX_LAYERS);
    layerStates.assign(n, LayerState());

    for (uint8_t d = 0; d < MAX_DRIVERS; ++d) {
        driverValue[d]  = (d < comp.driverCount && comp.drivers[d].kind == DriverKind::CONSTANT)
                        ? comp.drivers[d].value : 0;
        driverPrimed[d] = false;
    }

    // Measure every layer's scratch, then hand out offsets into one block.
    uint16_t arenaAt = 0;
    for (uint8_t li = 0; li < n; ++li) prepareLayerNL(comp.layers[li], layerStates[li], length, arenaAt);
    if (composeArena.size() < arenaAt) composeArena.resize(arenaAt);

    // Now that the arena exists, fill each layer's slice.
    for (uint8_t li = 0; li < n; ++li) {
        const Layer& l = comp.layers[li];
        buildLayerNL(l, layerStates[li]);
        resetLayerStateNL(l, layerStates[li], motionRing(l, nominalSpan(l, length)));
    }

    mutex.give();
}

// Every preset calls this rather than nulling `composition` itself: bumping
// the generation is what tells a tick paused in evalDriversNL()'s unlocked
// reader window that the composition it was evaluating has gone.
void LedStrand::dropCompositionNL() {
    composition = nullptr;
    ++composeGeneration;
}

void LedStrand::clearComposition() {
    mutex.take();
    dropCompositionNL();
    layerStates.clear();
    buffer.assign(length, 0);
    animMode     = AnimMode::STATIC;
    shiftStep    = 0;
    shiftVariant = 0;
    mutex.give();
}

void LedStrand::setDriverLevel(uint8_t driverIdx, uint8_t value) {
    mutex.take();
    if (composition && driverIdx < composition->driverCount && driverIdx < MAX_DRIVERS &&
        composition->drivers[driverIdx].kind == DriverKind::CONSTANT) {
        driverValue[driverIdx] = value;
    }
    mutex.give();
}

uint8_t LedStrand::getDriverLevel(uint8_t driverIdx) const {
    return driverIdx < MAX_DRIVERS ? driverValue[driverIdx] : 0;
}

void LedStrand::restartComposition() {
    mutex.take();
    if (composition) {
        composeAnchorMs      = platform::millis();
        composeMusicPausedAt = 0;
        composeMusicPaused   = false;
        uint8_t n = std::min<uint8_t>(composition->layerCount, MAX_LAYERS);
        for (uint8_t li = 0; li < n && li < layerStates.size(); ++li) {
            const Layer& l = composition->layers[li];
            resetLayerStateNL(l, layerStates[li], motionRing(l, nominalSpan(l, length)));
        }
    }
    mutex.give();
}

void LedStrand::composeMusicSeek(uint32_t positionMs) {
    mutex.take();
    if (composeMusicPaused) composeMusicPausedAt = positionMs;
    else                    composeAnchorMs = platform::millis() - positionMs;
    mutex.give();
}

void LedStrand::composeMusicPause(bool paused) {
    mutex.take();
    if (paused && !composeMusicPaused) {
        composeMusicPausedAt = platform::millis() - composeAnchorMs;
        composeMusicPaused   = true;
    } else if (!paused && composeMusicPaused) {
        composeAnchorMs    = platform::millis() - composeMusicPausedAt;
        composeMusicPaused = false;
    }
    mutex.give();
}

uint32_t LedStrand::composeMusicPositionMs() const {
    return composeClockNL(platform::millis());
}

/// Where the composition's timeline is at @p nowMs. MUSIC drivers and layer
/// windows both read this one clock, so pausing the music holds the windows
/// too: left running, they would come back out of step with the song, and
/// resuming - which moves the anchor back by the pause - would replay them.
uint32_t LedStrand::composeClockNL(uint32_t nowMs) const {
    if (composeMusicPaused) return composeMusicPausedAt;
    uint32_t elapsed = nowMs - composeAnchorMs;
    // restartComposition() or a seek from another task can land in
    // evalDriversNL()'s unlocked reader window and move the anchor past the
    // nowMs this tick already read. That is "just started", not 49 days in.
    return (int32_t)elapsed < 0 ? 0 : elapsed;
}

// ============================================================================
// One-time setup, in compose()
// ============================================================================

/// The width a layer was designed at, before any binding moves it: its span
/// clamped to a strand of @p len, and never 0. compose(), restartComposition()
/// and prepareLayerNL() all start a layer from this, so they cannot disagree
/// about where a span that overhangs the strand really ends.
uint8_t LedStrand::nominalSpan(const Layer& l, uint8_t len) {
    uint8_t start   = std::min(l.span.start, len);
    uint8_t nominal = l.span.width ? l.span.width : (uint8_t)(len - start);
    if ((uint16_t)start + nominal > len) nominal = (uint8_t)(len - start);
    return nominal ? nominal : 1;
}

/// The ring a layer's motion runs on, given its span: what it scrolls round,
/// bounces across and sparkles over. It has to be the ring layerPixel()
/// samples, which for a continuous stride is the whole strand - that is how
/// the windows show one scroll between them rather than one that snaps back
/// every @p span pixels.
uint8_t LedStrand::motionRing(const Layer& l, uint8_t span) const {
    return l.span.continuous ? length : span;
}

/// Measure this layer's scratch and claim a slice of the arena for it.
///
/// @p maxLen is the strand length: a span can never exceed it, so sizing the
/// span-independent scratch against it is always safe. The one exception is a
/// bouncing bitscroll, whose master *is* span-dependent; that one is built at
/// the layer's designed width - the whole strand, for a continuous stride,
/// see motionRing() - and @c maxSpan pins the span there, so a bound WIDTH
/// can narrow it but not overrun the master.
void LedStrand::prepareLayerNL(const Layer& l, LayerState& st, uint8_t maxLen,
                               uint16_t& arenaAt) {
    st.scratchAt  = arenaAt;
    st.scratchLen = 0;
    st.contentLen = 0;
    st.maxSpan    = 0;

    uint8_t nominal = motionRing(l, nominalSpan(l, maxLen));

    switch (l.kind) {
    case LayerKind::BITSCROLL: {
        size_t contentLen = 0;
        std::vector<uint32_t> unit = buildBitscrollUnit(l.pattern.segments, l.pattern.segmentCount,
                                                        l.paint.bgColor, l.pattern.spacing,
                                                        contentLen);
        if (l.motion.bounce) {
            // bitscrollNL()'s bounce master, built at the layer's designed
            // width because unlike every other scratch here it is span
            // dependent. maxSpan then pins the span to that width, so a bound
            // WIDTH can narrow the layer but never outrun its master.
            size_t masterLen;
            if (l.pattern.repeating) {
                masterLen = std::max((size_t)nominal * 3, unit.size() * 3);
                // bitscrollNL() tiles with `while (size < masterLen) insert(unit)`,
                // so the master overshoots to the next whole copy. The bounce
                // range is masterLen - span, so rounding differently here would
                // turn at a different pixel.
                if (unit.size())
                    masterLen = ((masterLen + unit.size() - 1) / unit.size()) * unit.size();
            } else {
                size_t nn  = std::min(contentLen, (size_t)nominal);
                size_t pad = (size_t)nominal - nn;
                masterLen  = pad + nn + pad;
            }
            st.scratchLen = (uint16_t)masterLen;
            st.maxSpan    = nominal;
        } else {
            st.scratchLen = (uint16_t)unit.size();
        }
        st.contentLen = (uint16_t)contentLen;
        break;
    }
    case LayerKind::FRAMES:
        st.scratchLen = l.frames.width ? l.frames.width : 1;
        break;
    case LayerKind::TWINKLE:
        // Sized for the whole strand, not the designed width, so a bound
        // WIDTH can grow the sparkle field as well as shrink it.
        break;
    default:
        break;
    }

    arenaAt = (uint16_t)(arenaAt + st.scratchLen);
}

/// Build what a layer draws from and never changes: its bitscroll pattern or
/// bounce master, its twinkle palette, its gauge scale. Only compose() calls
/// this, because all of it allocates or is costly to derive, and none of it
/// depends on anything a tick moves.
void LedStrand::buildLayerNL(const Layer& l, LayerState& st) {
    if (l.kind == LayerKind::TWINKLE) {
        if (l.sparkle.palette) st.twinkle.palette.assign(l.sparkle.palette,
                                                          l.sparkle.palette + l.sparkle.paletteCount);
        else                   st.twinkle.palette.clear();
    }

    if (l.kind == LayerKind::BITSCROLL && st.scratchLen) {
        size_t contentLen = 0;
        std::vector<uint8_t>  coverage;
        std::vector<uint32_t> unit = buildBitscrollUnit(l.pattern.segments, l.pattern.segmentCount,
                                                        l.paint.bgColor, l.pattern.spacing,
                                                        contentLen, &coverage);
        // The master was measured against the designed width, which a bounce
        // pins in maxSpan, so it has to be filled against that same width or
        // the padding would not line up.
        uint8_t designed = st.maxSpan ? st.maxSpan : 1;

        if (!l.motion.bounce) {
            for (size_t i = 0; i < unit.size() && i < st.scratchLen; ++i)
                composeArena[st.scratchAt + i] =
                    ((uint32_t)coverage[i] << 24) | (unit[i] & 0xFFFFFFu);
        } else if (l.pattern.repeating) {
            for (uint16_t i = 0; i < st.scratchLen; ++i) {
                size_t j = i % unit.size();
                composeArena[st.scratchAt + i] =
                    ((uint32_t)coverage[j] << 24) | (unit[j] & 0xFFFFFFu);
            }
        } else {
            // A single copy padded with background on both sides, so the
            // window can carry it from one end of the span to the other.
            size_t nn  = std::min(contentLen, (size_t)designed);
            size_t pad = (size_t)designed - nn;
            for (uint16_t i = 0; i < st.scratchLen; ++i) {
                if (i >= pad && i < pad + nn) {
                    size_t j = i - pad;
                    composeArena[st.scratchAt + i] =
                        ((uint32_t)coverage[j] << 24) | (unit[j] & 0xFFFFFFu);
                } else {
                    composeArena[st.scratchAt + i] = (uint32_t)(l.paint.bgColor & 0xFFFFFFu);
                }
            }
        }
    }

    // Resolve a GAUGE/METER scale onto the 0-255 axis once, the same
    // representation spliceMaskCustom() builds for a gauge region - including
    // its fallback, so a GAUGE given no scale ramps color at emptyAt to
    // color2 at fullAt rather than showing only its background.
    st.stops.clear();
    if (l.kind == LayerKind::GAUGE || l.kind == LayerKind::METER) {
        const GaugeStop fallback[2] = {{l.gauge.emptyAt, l.paint.color},
                                       {l.gauge.fullAt, l.paint.color2}};
        const GaugeStop* scale = l.gauge.stops;
        uint8_t          count = l.gauge.stops ? l.gauge.stopCount : 0;
        if (count == 0 && l.kind == LayerKind::GAUGE) { scale = fallback; count = 2; }

        double span2 = l.gauge.fullAt - l.gauge.emptyAt;
        for (uint8_t i = 0; i < count; ++i) {
            const GaugeStop& gs = scale[i];
            double frac = (span2 == 0.0) ? 0.0 : (gs.at - l.gauge.emptyAt) / span2;
            if (frac < 0.0) frac = 0.0;
            if (frac > 1.0) frac = 1.0;
            st.stops.push_back(std::make_pair((uint8_t)(frac * 255.0 + 0.5), gs.color));
        }
        // By level only, and stable: two stops at one reading are a hard
        // edge, and which side each color is on is the order they were given
        // in, not whatever order std::sort leaves them in.
        std::stable_sort(st.stops.begin(), st.stops.end(),
                         [](const std::pair<uint8_t, uint32_t>& a,
                            const std::pair<uint8_t, uint32_t>& b) { return a.first < b.first; });
    }
}

/// Put a layer's motion back to its first frame. Called once at compose(),
/// by restartComposition(), and whenever a retriggering layer's window
/// reopens - which is inside tick(), so this rewinds what moves and leaves
/// what buildLayerNL() built alone. It allocates nothing.
///
/// @p ring is the ring the motion runs on, see motionRing().
void LedStrand::resetLayerStateNL(const Layer& l, LayerState& st, uint8_t ring) {
    st.shiftAcc     = 0;
    st.flashCounter = 0;
    st.flashLit     = true;
    st.frameIdx     = 0;
    st.frameCounter = 0;
    st.step.reset(stepTicks(l.motion.speed));

    if (l.kind == LayerKind::PULSE && l.motion.bounce) {
        // pulseNL()'s bounce start: the far end and backwards when inverted.
        uint8_t run = std::max<uint8_t>(1, std::min<uint8_t>(l.motion.runLength, ring));
        st.bouncePos = l.motion.invert ? (int16_t)(ring - run) : 0;
        st.bounceDir = l.motion.invert ? -1 : 1;
    } else {
        // bitscrollNL()'s bounce branch starts at 0 going forward and ignores
        // `invert` entirely - the opposite of pulse. Reproduced, not tidied.
        st.bouncePos = 0;
        st.bounceDir = 1;
    }

    // Every spark back to dark. The palette is the one buildLayerNL() put
    // there, handed back to itself: a vector assigned to itself is left as it
    // is, and after compose() the per-pixel arrays are refilled at the size
    // they already have, so a retrigger never reaches the heap.
    if (l.kind == LayerKind::TWINKLE)
        st.twinkle.reset(length, st.twinkle.palette, l.sparkle.densityPct, l.sparkle.fadeStep,
                         l.paint.bgColor);

    if (l.kind == LayerKind::FRAMES) decodeFrameNL(l, st);
}

/// Decode frame @c st.frameIdx into this layer's arena slice.
///
/// One byte per run: the top three bits are the run length minus one, the
/// bottom five a palette index. Index 0 is transparent whatever color it
/// names. A run is clamped to the width left, so a hand-edited table cannot
/// overrun its slice.
void LedStrand::decodeFrameNL(const Layer& l, LayerState& st) {
    const LayerFrames& f = l.frames;
    uint8_t width = f.width ? f.width : 1;
    for (uint16_t i = 0; i < st.scratchLen; ++i) composeArena[st.scratchAt + i] = 0u;
    if (!f.data || !f.index || f.count == 0) return;

    uint16_t frame = (uint16_t)(st.frameIdx % f.count);
    uint32_t at    = f.index[frame];
    uint8_t  x     = 0;
    while (x < width && x < st.scratchLen) {
        uint8_t byte = f.data[at++];
        uint8_t run  = (uint8_t)((byte >> 5) + 1);
        uint8_t idx  = (uint8_t)(byte & 0x1F);
        if (run > width - x) run = (uint8_t)(width - x);
        uint32_t word = 0u; // index 0 is transparent
        if (idx != 0 && f.palette && idx < f.paletteCount)
            word = COVER_OPAQUE | (f.palette[idx] & 0xFFFFFFu);
        for (uint8_t k = 0; k < run; ++k) composeArena[st.scratchAt + x + k] = word;
        x = (uint8_t)(x + run);
    }
}

// ============================================================================
// Per-tick evaluation
// ============================================================================

bool LedStrand::evalDriversNL(const Composition& c, uint32_t nowMs) {
    uint8_t n = std::min<uint8_t>(c.driverCount, MAX_DRIVERS);

    double raw[MAX_DRIVERS];
    bool   got[MAX_DRIVERS];
    for (uint8_t d = 0; d < MAX_DRIVERS; ++d) got[d] = false;

    bool anyReader = false;
    for (uint8_t d = 0; d < n; ++d)
        if (c.drivers[d].kind == DriverKind::SENSOR && c.drivers[d].read) anyReader = true;

    // Readers are user code and may call back into this strand, so they run
    // with the mutex released - the same dance advanceLevel() and
    // advanceSpliceRegions() already do. Batched into one window rather than
    // one per driver, so the composition can never observe a half-updated
    // driver table, and re-checked afterwards so a swap during it is noticed
    // once instead of per driver.
    if (anyReader) {
        uint32_t gen = composeGeneration;
        mutex.give();
        for (uint8_t d = 0; d < n; ++d) {
            const Driver& dr = c.drivers[d];
            if (dr.kind == DriverKind::SENSOR && dr.read) { raw[d] = dr.read(); got[d] = true; }
        }
        mutex.take();
        // The composition was replaced while we were unlocked. Everything the
        // caller is holding - the Composition&, the LayerState indices - now
        // belongs to something else, so it has to unwind, not just skip a
        // driver.
        if (gen != composeGeneration) return false;
    }

    for (uint8_t d = 0; d < n; ++d) {
        const Driver& dr = c.drivers[d];
        switch (dr.kind) {
        case DriverKind::CONSTANT:
            break; // whatever setDriverLevel() left there
        case DriverKind::SENSOR: {
            // An unplugged device reports PROS_ERR_F (infinity). Holding the
            // last value avoids slamming the layer to the top of its range.
            if (!got[d] || !std::isfinite(raw[d])) break;
            uint8_t target = mapLevelTo(raw[d], dr.emptyAt, dr.fullAt, dr.wrap);
            driverValue[d] = driverPrimed[d]
                           ? smoothLevelTo(target, driverValue[d], dr.smoothing, dr.wrap)
                           : target; // the first sample lands outright
            driverPrimed[d] = true;
            break;
        }
        case DriverKind::MUSIC: {
            if (!dr.track) break;
            driverValue[d] = sampleTrackNL(dr.track, composeClockNL(nowMs), dr.sensitivity, dr.loop);
            break;
        }
        case DriverKind::LFO:
            driverValue[d] = lfoValue(dr, nowMs);
            break;
        }
    }
    return true;
}

void LedStrand::resolveLayerNL(const Composition& c, const Layer& l, Resolved& p) const {
    p.start      = l.span.start;
    p.width      = l.span.width;
    p.runLength  = l.motion.runLength;
    p.speed      = l.motion.speed.pixels;
    p.densityPct = l.sparkle.densityPct;
    p.opacity    = l.mix.opacity;
    p.level      = l.gauge.level;
    p.mix        = 0;

    for (uint8_t b = 0; b < MAX_BINDINGS; ++b) {
        const Binding& bd = l.bindings[b];
        if (bd.param == LayerParam::NONE || bd.driver >= c.driverCount ||
            bd.driver >= MAX_DRIVERS) continue;
        uint8_t v = applyCurve(bd.curve, driverValue[bd.driver]);
        int32_t x = (int32_t)bd.atZero + ((int32_t)(bd.atFull - bd.atZero) * v) / 255;
        uint8_t u = (uint8_t)(x < 0 ? 0 : (x > 255 ? 255 : x));
        switch (bd.param) {
        case LayerParam::OPACITY:    p.opacity    = u; break;
        case LayerParam::SPEED:      p.speed      = u; break;
        case LayerParam::RUN_LENGTH: p.runLength  = u; break;
        case LayerParam::LEVEL:      p.level      = u; break;
        case LayerParam::START:      p.start      = u; break;
        case LayerParam::WIDTH:      p.width      = u; break;
        case LayerParam::DENSITY:    p.densityPct = u; break;
        case LayerParam::MIX:        p.mix        = u; break;
        default: break;
        }
    }
}

uint8_t LedStrand::resolveSpan(const Resolved& p, const LayerState& st) const {
    if (p.start >= length) return 0;
    uint8_t span = p.width ? p.width : (uint8_t)(length - p.start);
    if ((uint16_t)p.start + span > length) span = (uint8_t)(length - p.start);
    if (st.maxSpan && span > st.maxSpan)   span = st.maxSpan;
    return span;
}

void LedStrand::advanceLayerNL(const Layer& l, LayerState& st, const Resolved& p, uint8_t ring) {
    // @p ring is the ring the motion runs on, see motionRing().
    //
    // The moving kinds move only on a tick their Speed says is a step, which
    // is every tick for a bare pixel count - so a layer reproduces its preset
    // exactly either way. Nothing is repainted here, so a tick that does not
    // move needs nothing else.
    switch (l.kind) {
    case LayerKind::PULSE:
        if (!st.step.due()) break;
        if (l.motion.bounce) {
            // advancePulseBounce(), with ring for length.
            st.bouncePos = (int16_t)(st.bouncePos + st.bounceDir * std::max<uint8_t>(p.speed, 1));
            int16_t maxOffset = (int16_t)ring - (int16_t)std::max<uint8_t>(p.runLength, 1);
            if (maxOffset < 0) maxOffset = 0;
            if (st.bouncePos >= maxOffset) { st.bouncePos = maxOffset; st.bounceDir = -1; }
            if (st.bouncePos <= 0)         { st.bouncePos = 0;          st.bounceDir = 1; }
        } else {
            st.shiftAcc = (uint16_t)((st.shiftAcc + shiftFor(p.speed, ring, l.motion.invert)) % ring);
        }
        break;

    case LayerKind::FLOW:
    case LayerKind::RAINBOW:
        if (!st.step.due()) break;
        st.shiftAcc = (uint16_t)((st.shiftAcc + shiftFor(p.speed, ring, l.motion.invert)) % ring);
        break;

    case LayerKind::BITSCROLL:
        if (!st.step.due()) break;
        if (l.motion.bounce) {
            // advanceBitscrollBounce(), with ring for length.
            int16_t maxPos = (int16_t)st.scratchLen - (int16_t)ring;
            if (maxPos < 0) maxPos = 0;
            st.bouncePos = (int16_t)(st.bouncePos + st.bounceDir * std::max<uint8_t>(p.speed, 1));
            if (st.bouncePos >= maxPos) { st.bouncePos = maxPos; st.bounceDir = -1; }
            if (st.bouncePos <= 0)      { st.bouncePos = 0;       st.bounceDir = 1; }
        } else {
            uint8_t wrapAt = l.pattern.repeating
                           ? (uint8_t)(st.scratchLen ? st.scratchLen : 1)
                           : ring;
            st.shiftAcc = (uint16_t)((st.shiftAcc + shiftFor(p.speed, wrapAt, l.motion.invert)) % wrapAt);
        }
        break;

    case LayerKind::FLASH: {
        // advanceFlash(): compare then increment, so the tick that flips the
        // phase is the one that renders it.
        uint16_t hold = st.flashLit ? msToTicks(l.flash.onMs) : msToTicks(l.flash.offMs);
        if (st.flashCounter >= hold) { st.flashCounter = 0; st.flashLit = !st.flashLit; }
        ++st.flashCounter;
        break;
    }

    case LayerKind::TWINKLE:
        st.twinkle.densityPct = std::min<uint8_t>(p.densityPct, 100);
        stepTwinkle(st.twinkle, ring);
        break;

    case LayerKind::FRAMES: {
        // advanceFlash()'s order: compare, then count, so the tick that
        // changes the frame is the one that shows it. Counting first cut the
        // first frame one tick short of the rest every time the layer started
        // or retriggered, which a painted loop shows as a hitch.
        uint16_t hold = msToTicks(l.frames.frameMs);
        if (st.frameCounter >= (hold ? hold : 1)) {
            st.frameCounter = 0;
            if (l.frames.count) {
                st.frameIdx = (uint16_t)((st.frameIdx + 1) % l.frames.count);
                decodeFrameNL(l, st);
            }
        }
        ++st.frameCounter;
        // A frame that holds shows the drawing where it was drawn, and a
        // scrolling frame after it starts from there rather than from wherever
        // the last scroll left off. A run of scrolling frames is one scroll.
        bool scrolls = l.frames.scroll && l.frames.count &&
                       l.frames.scroll[st.frameIdx % l.frames.count];
        if (!scrolls) {
            st.shiftAcc = 0;
            st.step.restart();
            break;
        }
        uint8_t frameRing = l.frames.width ? l.frames.width : 1;
        if (p.speed && st.step.due())
            st.shiftAcc = (uint16_t)((st.shiftAcc + shiftFor(p.speed, frameRing, l.motion.invert)) % frameRing);
        break;
    }

    default:
        break;
    }
}

uint32_t LedStrand::layerPixel(const Layer& l, const LayerState& st, const Resolved& p,
                               uint8_t k, uint8_t span) const {
    const uint32_t* sc = st.scratchLen ? &composeArena[st.scratchAt] : nullptr;
    // MIX slides the layer between its two colors with no rebuild.
    uint32_t fg = p.mix ? lerpColor(l.paint.color, l.paint.color2, p.mix) : l.paint.color;

    switch (l.kind) {
    case LayerKind::OFF:
        // Coverage 0, so a KEY layer contributes nothing and an OVER one
        // paints its own background - which is what "off" means in both.
        return (uint32_t)(l.paint.bgColor & 0xFFFFFFu);

    case LayerKind::SOLID:
        return COVER_OPAQUE | (fg & 0xFFFFFFu);

    case LayerKind::PULSE: {
        bool lit;
        if (l.motion.bounce) {
            int16_t end = (int16_t)(st.bouncePos + std::max<uint8_t>(p.runLength, 1));
            lit = ((int16_t)k >= st.bouncePos && (int16_t)k < end);
        } else {
            uint8_t j = (uint8_t)((k + st.shiftAcc) % (span ? span : 1));
            lit = (j < std::min<uint8_t>(p.runLength, span));
        }
        return lit ? (COVER_OPAQUE | (fg & 0xFFFFFFu)) : (uint32_t)(l.paint.bgColor & 0xFFFFFFu);
    }

    case LayerKind::FLASH:
        return st.flashLit ? (COVER_OPAQUE | (fg & 0xFFFFFFu))
                           : (uint32_t)(l.paint.bgColor & 0xFFFFFFu);

    case LayerKind::FLOW: {
        uint8_t j = (uint8_t)((k + st.shiftAcc) % (span ? span : 1));
        return COVER_OPAQUE | (gradientPixel(fg, l.paint.color2, j, span, l.motion.seamless) & 0xFFFFFFu);
    }

    case LayerKind::RAINBOW: {
        uint8_t j = (uint8_t)((k + st.shiftAcc) % (span ? span : 1));
        return COVER_OPAQUE | (wheel((uint8_t)((int)j * 256 / (span ? span : 1))) & 0xFFFFFFu);
    }

    case LayerKind::TWINKLE: {
        // stepTwinkle() has already moved level/target/hold this tick. The
        // spark's own brightness *is* its coverage, so a KEY twinkle layer
        // fades into what is under it rather than onto its own background.
        if (k >= st.twinkle.level.size()) return 0u;
        uint32_t fgc = st.twinkle.palette.empty() ? 0u
                     : st.twinkle.palette[st.twinkle.colorIdx[k]];
        return ((uint32_t)st.twinkle.level[k] << 24) | (fgc & 0xFFFFFFu);
    }

    case LayerKind::BITSCROLL: {
        if (!sc || st.scratchLen == 0) return (uint32_t)(l.paint.bgColor & 0xFFFFFFu);
        if (l.motion.bounce) {
            size_t idx = (size_t)st.bouncePos + k;
            return idx < st.scratchLen ? sc[idx] : 0u;
        }
        if (l.pattern.repeating) return sc[(k + st.shiftAcc) % st.scratchLen];
        size_t j = (size_t)((k + st.shiftAcc) % (span ? span : 1));
        return (j < st.contentLen && j < st.scratchLen) ? sc[j] : 0u;
    }

    case LayerKind::FRAMES: {
        if (!sc || st.scratchLen == 0) return 0u;
        uint8_t ring = l.frames.width ? l.frames.width : 1;
        return sc[(k + st.shiftAcc) % ring];
    }

    case LayerKind::GAUGE: {
        const std::pair<uint8_t, uint32_t>* stops = st.stops.empty() ? nullptr : &st.stops[0];
        uint8_t stopCount = (uint8_t)st.stops.size();
        if (l.gauge.style == GaugeStyle::HEAT)
            return COVER_OPAQUE |
                   (scaleColorAt(stops, stopCount, l.gauge.blend, l.paint.bgColor, p.level) & 0xFFFFFFu);
        // BAR: paintGaugeRegion()'s cutoff arithmetic, scoped to this span.
        uint32_t litQ8 = (uint32_t)p.level * (uint32_t)span;
        uint32_t full  = litQ8 / 255;
        uint8_t  frac  = (uint8_t)(litQ8 % 255);
        uint8_t  f     = l.motion.invert ? (uint8_t)(span - 1 - k) : k;
        uint8_t  pos   = (span <= 1) ? 255 : (uint8_t)(((uint32_t)f * 255) / (span - 1));
        uint32_t scale = scaleColorAt(stops, stopCount, l.gauge.blend, l.paint.bgColor, pos);
        if (f < full)  return COVER_OPAQUE | (scale & 0xFFFFFFu);
        if (f == full) return ((uint32_t)frac << 24) | (scale & 0xFFFFFFu);
        return (uint32_t)(l.paint.bgColor & 0xFFFFFFu);
    }

    case LayerKind::METER: {
        // levelPixel(), scoped to this span.
        uint32_t litQ8 = (uint32_t)p.level * (uint32_t)span;
        uint32_t full  = litQ8 / 255;
        uint8_t  frac  = (uint8_t)(litQ8 % 255);
        uint8_t  f     = l.motion.invert ? (uint8_t)(span - 1 - k) : k;
        // levelFillNL() calls the three-argument genGradient(), whose default
        // is an open ramp - so seamless is false here, not true.
        uint32_t scale = l.gauge.gradient
                       ? gradientPixel(fg, l.paint.color2, f, span, false) : fg;
        if (f < full)  return COVER_OPAQUE | (scale & 0xFFFFFFu);
        if (f == full) return ((uint32_t)frac << 24) | (scale & 0xFFFFFFu);
        return (uint32_t)(l.paint.bgColor & 0xFFFFFFu);
    }
    }
    return (uint32_t)(l.paint.bgColor & 0xFFFFFFu);
}

void LedStrand::compositeLayerNL(const Layer& l, const LayerState& st, const Resolved& p,
                                 uint8_t span, uint8_t alpha) {
    uint16_t step = l.span.stride ? l.span.stride : length;
    // A repeat is at most a stride wide, so no pixel is blended twice - which
    // ADD, SCREEN, MULTIPLY and MASK would stack. OVER already showed only the
    // last repeat to reach a pixel, which is exactly this, so it looks the same.
    uint8_t  width = (l.span.stride && l.span.stride < span) ? l.span.stride : span;
    uint8_t  ring  = motionRing(l, span);
    // Only the blends that mean "and let what is below show through" read
    // coverage. OVER deliberately does not, which is what makes a one-layer
    // composition bit-identical to the preset it reproduces: the preset
    // paints its own background too.
    bool keyed = (l.mix.blend == LayerBlend::KEY || l.mix.blend == LayerBlend::ADD ||
                  l.mix.blend == LayerBlend::SCREEN);

    for (uint16_t base = p.start; base < length; base = (uint16_t)(base + step)) {
        for (uint8_t k = 0; k < width && base + k < length; ++k) {
            // `continuous` samples at the strand-wide index, so a strided
            // layer is one animation cut into windows rather than N copies.
            uint8_t  local = l.span.continuous ? (uint8_t)(base + k - p.start) : k;
            uint32_t word  = layerPixel(l, st, p, local, ring);
            uint8_t  cov   = (uint8_t)(word >> 24);
            uint32_t rgb   = word & 0xFFFFFFu;
            uint8_t  a;
            if (keyed) {
                a = (uint8_t)(((uint16_t)cov * alpha) / 255);
            } else {
                // A blend that ignores coverage has to be handed a pixel that
                // already accounts for it, resolved against the layer's own
                // background - because that is precisely what the preset does.
                // levelPixel() returns lerpColor(levelBg, color, frac) for the
                // meter's partial edge pixel, and paintTwinkleInto() returns
                // lerpColor(bgColor, spark, level) for a half-faded spark.
                // Without this an OVER meter would paint a full pixel where
                // the preset paints a fraction of one.
                if (cov != 255) rgb = lerpColor(l.paint.bgColor, rgb, cov);
                a = alpha;
            }
            buffer[base + k] = blendPixel(l.mix.blend, buffer[base + k], rgb, a);
        }
        if (!l.span.stride) break;
    }
}

void LedStrand::advanceComposition(uint32_t nowMs) {
    const Composition* c = composition;
    if (!c) return;
    // Unwinds rather than skipping: after the unlocked reader window the
    // Composition& and every layerStates index could belong to another
    // animation entirely.
    if (!evalDriversNL(*c, nowMs)) return;
    // Every preset bumps the generation as it takes the buffer back, so the
    // check above has already caught one landing in the reader window. This
    // is the backstop, should anything ever null it without.
    c = composition;
    if (!c) return;

    uint32_t elapsed = composeClockNL(nowMs);
    for (uint8_t i = 0; i < length; ++i) buffer[i] = c->bgColor;

    uint8_t n = std::min<uint8_t>(c->layerCount, MAX_LAYERS);
    if (layerStates.size() < n) n = (uint8_t)layerStates.size();

    for (uint8_t li = 0; li < n; ++li) {
        const Layer& l  = c->layers[li];
        LayerState&  st = layerStates[li];

        uint32_t period = l.time.loopMs ? l.time.loopMs : c->loopMs;
        uint32_t t      = period ? (elapsed % period) : elapsed;

        uint8_t windowAlpha = layerWindowAlpha(l, t, period);
        if (windowAlpha == 0) { st.wasOpen = false; continue; }

        Resolved p;
        resolveLayerNL(*c, l, p);
        uint8_t span = resolveSpan(p, st);
        if (span == 0) { st.wasOpen = false; continue; }

        uint8_t ring = motionRing(l, span);

        // A layer coming back in starts from its first frame, unless it was
        // asked to carry on where it left off.
        if (!st.wasOpen && l.time.retrigger) resetLayerStateNL(l, st, ring);
        st.wasOpen = true;

        uint16_t alpha = ((uint16_t)windowAlpha * p.opacity) / 255;
        if (alpha == 0) continue;

        // A layer only advances while it is visible: an off-screen layer costs
        // nothing, and comes back at frame zero rather than mid-stride.
        advanceLayerNL(l, st, p, ring);
        compositeLayerNL(l, st, p, span, (uint8_t)alpha);
    }

    // flushBuffer() reads buffer[] straight through from here.
    animMode     = AnimMode::STATIC;
    shiftStep    = 0;
    shiftVariant = 0;
}

} // namespace hitlib
