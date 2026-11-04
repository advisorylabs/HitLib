#include "hitlib/led_strand.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>

namespace hitlib {

// Shared across every LedStrand instance (all groups). The V5 ADI/Smart Port
// link can't keep up with back-to-back or concurrent led->write() calls from
// multiple strands/tasks, serializing them here and pacing each one gives
// the ADI LED driver breathing room so updates don't queue up and lag behind.
static platform::Mutex s_adiMutex;

// An unchanged frame isn't sent again, since the pixels latch the last one
// and every write holds the ADI for 12 ms. Still resend one this often, so a
// strip that was unplugged or browned out comes back on its own.
static constexpr uint32_t RESEND_MS = 1000;

// C++17 made static constexpr members implicitly inline. Before that - and
// VEXcode builds gnu++11 - one bound to a reference (std::min takes its
// arguments by const&) needs a definition of its own, or an unoptimized build
// fails to link.
#if __cplusplus < 201703L
constexpr uint8_t  LedStrand::MAX_LAYERS;
constexpr uint8_t  LedStrand::MAX_DRIVERS;
constexpr uint8_t  LedStrand::MAX_BINDINGS;
constexpr uint8_t  LedStrand::CURVE_POINTS;
constexpr uint8_t  LedStrand::MAX_LEDS;
constexpr uint8_t  LedStrand::MAX_SEGMENTS;
constexpr uint8_t  LedStrand::MAX_CHAIN_LEDS;
constexpr uint32_t LedStrand::COVER_OPAQUE;
#endif

// std::rand() is shared by every strand, so it is seeded once, the first time
// anything here draws from it.
static void seedRandOnce() {
    // Aligned to 4 for PROS's sake, not ours. A PROS cold image packs every
    // 1- and 2-byte-aligned .bss variable from every library together, in
    // link order, ahead of LVGL's display buffers, which declare no alignment
    // of their own. hitlib.a links before liblvgl.a, so a lone 1-byte bool
    // here pushed those buffers off 4-byte alignment: LVGL then asserts in
    // display_initialize() and spins forever, before the PROS scheduler
    // starts (HitLib 1.5.1). tools/check_cold_bss.py keeps it from coming back.
    alignas(4) static bool seeded = false;
    if (!seeded) { std::srand(platform::millis()); seeded = true; }
}

// lerpColor() and wheel() were file-local helpers here until 1.5. They are
// private statics of LedStrand now so led_custom.cpp can call the same bodies
// the presets do, rather than growing a second copy that could drift.
uint32_t LedStrand::lerpColor(uint32_t bg, uint32_t fg, uint8_t level) {
    uint8_t br = (bg >> 16) & 0xFF, bgc = (bg >> 8) & 0xFF, bb = bg & 0xFF;
    uint8_t fr = (fg >> 16) & 0xFF, fgc = (fg >> 8) & 0xFF, fb = fg & 0xFF;
    uint8_t r = (uint8_t)(br + ((int)(fr - br) * level) / 255);
    uint8_t g = (uint8_t)(bgc + ((int)(fgc - bgc) * level) / 255);
    uint8_t b = (uint8_t)(bb + ((int)(fb - bb) * level) / 255);
    return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

// Standard NeoPixel-style integer hue wheel (S=V=255).
uint32_t LedStrand::wheel(uint8_t pos) {
    pos = 255 - pos;
    if (pos < 85) return ((uint32_t)(255 - pos * 3) << 16) | (uint32_t)(pos * 3);
    if (pos < 170) {
        pos -= 85;
        return ((uint32_t)(pos * 3) << 8) | (uint32_t)(255 - pos * 3);
    }
    pos -= 170;
    return ((uint32_t)(pos * 3) << 16) | ((uint32_t)(255 - pos * 3) << 8);
}

// ============================================================================
// Construction / init
// ============================================================================

// Smart Port 0 means the brain's own ADI ports.
LedStrand::LedStrand(uint8_t adiPort_, uint8_t length_, uint32_t refreshMs_)
    : LedStrand(0, adiPort_, length_, refreshMs_) {}

LedStrand::LedStrand(ExpanderPort port, uint8_t length_, uint32_t refreshMs_)
    : LedStrand(port.smartPort, port.adiPort, length_, refreshMs_) {}

LedStrand::LedStrand(uint8_t smartPort_, uint8_t adiPort_, uint8_t length_, uint32_t refreshMs_)
    : LedStrand({Segment(ExpanderPort{smartPort_, adiPort_}, length_)}, refreshMs_) {}

// A strand of its own is a chain of one, so this is where every constructor
// ends up. Strips past MAX_SEGMENTS, and pixels past MAX_CHAIN_LEDS, are
// dropped rather than wrapped: the strand is only ever as long as what it
// can address.
LedStrand::LedStrand(std::initializer_list<Segment> chain, uint32_t refreshMs_)
    : length(0), refreshMs(refreshMs_) {
    segmentCount = 0;
    for (const Segment& seg : chain) {
        if (segmentCount >= MAX_SEGMENTS || length >= MAX_CHAIN_LEDS) break;
        uint8_t len = std::max<uint8_t>(1, std::min<uint8_t>(seg.length, MAX_LEDS));
        len = std::min<uint8_t>(len, (uint8_t)(MAX_CHAIN_LEDS - length));
        strips[segmentCount++] = {seg.smartPort, seg.adiPort, len, 0, nullptr};
        length = (uint8_t)(length + len);
    }
    // An empty chain drives nothing, but still has a pixel to animate, so
    // nothing downstream has to allow for a zero-length buffer.
    if (length == 0) length = 1;

    buffer.assign(length, 0);
    overlayBuffer.assign(length, 0);
    spliceShowAnim.assign(length, true);
    splicePixelBg.assign(length, 0);
    splicePixelUseOverlay.assign(length, false);
    splicePixelRegionIdx.assign(length, -1);
}

void LedStrand::init() {
    if (segmentCount == 0 || strips[0].led != nullptr) return;
    // Last strip first: tick() takes strips[0].led as the sign the strand is
    // ready, so it has to be the last one set. A group's task already
    // ticking - start() before init(), or a strand add()ed late - could
    // otherwise flush in between and write through a strip not built yet.
    for (uint8_t s = segmentCount; s-- > 0;) {
        strips[s].led = new platform::AdiLed(strips[s].smartPort, strips[s].adiPort,
                                             strips[s].length);
    }
    lastSentValid = false;  // a new strip has been sent nothing yet
}

// The interval comes from LedGroup, so the strand counts its durations at the
// rate it is actually ticked at. Anything already converted to ticks keeps
// the count it was given.
void LedStrand::setRefreshMs(uint32_t ms) {
    mutex.take();
    refreshMs = ms;
    mutex.give();
}

// ============================================================================
// tick() - called once per refresh interval by LedGroup's task.
// ============================================================================

void LedStrand::tick() {
    if (segmentCount == 0 || !strips[0].led) return;
    mutex.take();

    uint32_t now = platform::millis();
    pruneExpired(now);

    // A composition writes the finished frame straight into buffer[] and
    // leaves animMode == STATIC, so flushBuffer()'s existing pass-through
    // branch picks it up and nothing downstream - brightness, the overlay,
    // either splice mask - needs to know it is there.
    if (composition)                        advanceComposition(now);
    else if (pulseRunLen > 0)               advancePulseBounce();
    else if (!bitscrollMaster.empty())      advanceBitscrollBounce();
    else if (animMode == AnimMode::TWINKLE) advanceTwinkleInto(twinkleState, buffer);
    else if (animMode == AnimMode::FLASH)   advanceFlash();
    else if (animMode == AnimMode::LEVEL)   advanceLevel();
    else if (animMode == AnimMode::SHIFT)   shiftBuffer();

    if (overlayAnimMode == AnimMode::SHIFT)        shiftOverlayBuffer();
    else if (overlayAnimMode == AnimMode::FLASH)   advanceOverlayFlash();
    else if (overlayAnimMode == AnimMode::TWINKLE) advanceTwinkleInto(overlayTwinkleState, overlayBuffer);

    advanceSpliceAlternating(now);
    advanceSpliceRegions();
    advanceSpreadMask();
    if (spliceActive && spliceMode == SpliceMode::TWINKLE) stepTwinkle(maskTwinkle, length);

    int16_t effectiveIdx = computeEffectiveMode();
    bool modeChanged = (effectiveIdx != lastModeIdx);
    lastModeIdx = effectiveIdx;

    // A wipe already under way belongs to what was showing, so a new mode
    // cuts it short rather than coming in over its stale frame. One asked for
    // since the last tick - transition() just before activateMode() - is the
    // way into this mode, and stays.
    if (modeChanged && transKind != TransitionKind::CUT && transElapsed > 0)
        transKind = TransitionKind::CUT;

    const ProfileMode* mode =
        (effectiveIdx >= 0 && activeProfile) ? &activeProfile->modes[effectiveIdx] : nullptr;

    // Profile callbacks are user code and call the *public* (locking) API, so
    // the mutex must not be held while they run, release around each call.
    if (modeChanged && mode && mode->onActivate) {
        mutex.give();
        mode->onActivate(*this);
        mutex.take();
    }
    if (mode && mode->onTick) {
        mutex.give();
        mode->onTick(*this);
        mutex.take();
    }

    flushBuffer(now);   // gives the mutex back
}

// ============================================================================
// Base animations
// ============================================================================

void LedStrand::off() { mutex.take(); setColorNL(0); mutex.give(); }

void LedStrand::setColor(uint32_t color) { mutex.take(); setColorNL(color); mutex.give(); }

void LedStrand::setColorNL(uint32_t color) {
    dropCompositionNL();  // a preset takes the base buffer over
    pulseRunLen = 0;
    bitscrollMaster.clear();
    buffer.assign(length, color);
    shiftStep = 0;
    shiftVariant = 0;
    animMode = AnimMode::STATIC;
}

void LedStrand::pulse(uint32_t color, uint8_t runLength, Speed speed, uint32_t bgColor, bool invert,
                       bool bounce) {
    mutex.take();
    pulseNL(color, runLength, speed, bgColor, invert, bounce);
    mutex.give();
}

void LedStrand::pulseNL(uint32_t color, uint8_t runLen, Speed speed, uint32_t bg, bool invert, bool bounce) {
    dropCompositionNL();  // a preset takes the base buffer over
    bitscrollMaster.clear();
    shiftPacer.reset(stepTicks(speed));
    if (!bounce) {
        pulseRunLen = 0;
        buffer.assign(length, bg);
        uint8_t rl = std::min<uint8_t>(runLen, length);
        std::fill_n(buffer.begin(), rl, color);
        shiftStep = 0;
        uint8_t sp = (uint8_t)(speed.pixels % length);
        shiftVariant = invert ? (uint8_t)((length - sp) % length) : sp;
        animMode = AnimMode::SHIFT;
    } else {
        pulseColor = color;
        pulseBg = bg;
        pulseRunLen = std::max<uint8_t>(1, std::min<uint8_t>(runLen, length));
        pulseSpeed = std::max<uint8_t>(speed.pixels, 1);
        pulseOffset = invert ? (int16_t)(length - pulseRunLen) : 0;
        pulseDir = invert ? -1 : 1;
        animMode = AnimMode::STATIC; // content is generated per-tick by advancePulseBounce()
        // advancePulseBounce() repaints before the next flush, but a call from
        // a ProfileMode callback lands after this tick's advance, so paint the
        // starting frame here or that flush shows the previous animation.
        buffer.assign(length, bg);
        std::fill_n(buffer.begin() + pulseOffset, pulseRunLen, color);
    }
}

void LedStrand::advancePulseBounce() {
    // Paints on every tick even when it does not move, so the buffer can
    // never disagree with pulseOffset whatever else touched it.
    if (shiftPacer.due()) {
        pulseOffset = (int16_t)(pulseOffset + pulseDir * pulseSpeed);
        int16_t maxOffset = (int16_t)length - (int16_t)pulseRunLen;
        if (maxOffset < 0) maxOffset = 0;
        if (pulseOffset >= maxOffset) { pulseOffset = maxOffset; pulseDir = -1; }
        if (pulseOffset <= 0)         { pulseOffset = 0;          pulseDir = 1; }
    }

    std::fill(buffer.begin(), buffer.end(), pulseBg);
    int16_t end = pulseOffset + pulseRunLen;
    if (end > (int16_t)length) end = (int16_t)length;
    for (int16_t i = pulseOffset; i < end; ++i) buffer[i] = pulseColor;
}

void LedStrand::flash(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bgColor) {
    mutex.take();
    flashNL(color, onMs, offMs, bgColor);
    mutex.give();
}

void LedStrand::flashNL(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bg) {
    dropCompositionNL();  // a preset takes the base buffer over
    pulseRunLen = 0;
    bitscrollMaster.clear();
    flashColor    = color;
    flashBgColor  = bg;
    flashOnTicks  = msToTicks(onMs);
    flashOffTicks = msToTicks(offMs);
    flashCounter  = 0;
    flashLit      = true;
    // Start lit, so the first flash lands immediately rather than after a
    // blank interval.
    buffer.assign(length, color);
    shiftStep = 0;
    shiftVariant = 0;
    animMode = AnimMode::FLASH;
}

// flashCounter tracks frames already flushed in the current phase. tick() runs
// this before flushBuffer(), so the tick that flips the phase also renders the
// new colour. Count it as that phase's first frame, or every phase comes out
// one tick short.
void LedStrand::advanceFlash() {
    uint16_t hold = flashLit ? flashOnTicks : flashOffTicks;
    if (flashCounter >= hold) {
        flashCounter = 0;
        flashLit = !flashLit;
        if (buffer.size() != length) buffer.assign(length, flashBgColor);
        std::fill(buffer.begin(), buffer.end(), flashLit ? flashColor : flashBgColor);
    }
    ++flashCounter;
}

void LedStrand::flow(uint32_t color1, uint32_t color2, Speed speed, bool invert, bool seamless) {
    mutex.take();
    flowNL(color1, color2, speed, invert, seamless);
    mutex.give();
}

void LedStrand::flowNL(uint32_t c1, uint32_t c2, Speed speed, bool invert, bool seamless) {
    dropCompositionNL();  // a preset takes the base buffer over
    pulseRunLen = 0;
    bitscrollMaster.clear();
    buffer = genGradient(c1, c2, length, seamless);
    shiftStep = 0;
    shiftPacer.reset(stepTicks(speed));
    uint8_t sp = (uint8_t)(speed.pixels % length);
    shiftVariant = invert ? (uint8_t)((length - sp) % length) : sp;
    animMode = AnimMode::SHIFT;
}

void LedStrand::rainbow(Speed speed, bool invert) {
    mutex.take();
    rainbowNL(speed, invert);
    mutex.give();
}

void LedStrand::rainbowNL(Speed speed, bool invert) {
    dropCompositionNL();  // a preset takes the base buffer over
    pulseRunLen = 0;
    bitscrollMaster.clear();
    buffer = genRainbow(length);
    shiftStep = 0;
    shiftPacer.reset(stepTicks(speed));
    shiftVariant = shiftFor(speed.pixels, length, invert);
    animMode = AnimMode::SHIFT;
}

void LedStrand::twinkle(const std::vector<uint32_t>& colors, uint8_t densityPct, uint8_t fadeStep,
                         uint32_t bgColor, uint8_t bitWidth) {
    mutex.take();
    twinkleNL(colors, densityPct, fadeStep, bgColor, bitWidth);
    mutex.give();
}

void LedStrand::TwinkleState::reset(uint8_t width, const std::vector<uint32_t>& colors,
                                    uint8_t density, uint8_t fade, uint32_t bg, uint8_t bits) {
    palette = colors;
    bitWidth = std::max<uint8_t>(bits, 1);
    uint8_t count = bitsFor(width);
    level.assign(count, 0);
    target.assign(count, 0);
    colorIdx.assign(count, 0);
    holdTicks.assign(count, 0);
    densityPct = std::min<uint8_t>(density, 100);
    fadeStep = std::max<uint8_t>(fade, 1);
    bgColor = bg;
    seedRandOnce();
}

// Repaints `buf` from `t` every tick. The buffer is passed in rather than read
// off the strand so the base strand, the overlay, and each custom region can
// all sparkle at once, each over its own pixels.
void LedStrand::advanceTwinkleInto(TwinkleState& t, std::vector<uint32_t>& buf) {
    uint8_t width = (uint8_t)std::min(t.level.size() * t.bitWidth, buf.size());
    if (width == 0) return;
    stepTwinkle(t, width);
    paintTwinkleInto(t, buf, width);
}

void LedStrand::stepTwinkle(TwinkleState& t, uint8_t width) {
    constexpr uint8_t HOLD_TICKS = 8;

    // Everything below walks bits, not pixels: a wide bit spawns, holds and
    // fades as one.
    uint8_t bits = (uint8_t)std::min<size_t>(t.bitsFor(width), t.level.size());
    if (bits == 0) return;

    uint8_t activeCount = 0;
    for (uint8_t i = 0; i < bits; ++i) {
        if (t.level[i] > 0 || t.holdTicks[i] > 0) activeCount++;
    }
    uint8_t targetCount = (uint8_t)(((uint16_t)bits * t.densityPct + 50) / 100);

    if (activeCount < targetCount && !t.palette.empty()) {
        // Reservoir-sample one idle bit to spawn, at most one per tick.
        int chosen = -1;
        int idleSeen = 0;
        for (uint8_t i = 0; i < bits; ++i) {
            if (t.level[i] == 0 && t.holdTicks[i] == 0) {
                idleSeen++;
                if ((std::rand() % idleSeen) == 0) chosen = i;
            }
        }
        if (chosen >= 0) {
            t.colorIdx[chosen] = (uint8_t)(std::rand() % t.palette.size());
            t.target[chosen] = 255;
        }
    }

    for (uint8_t i = 0; i < bits; ++i) {
        if (t.holdTicks[i] > 0) {
            if (--t.holdTicks[i] == 0) t.target[i] = 0;
        } else if (t.level[i] < t.target[i]) {
            uint16_t nl = (uint16_t)t.level[i] + t.fadeStep;
            t.level[i] = (nl > 255) ? (uint8_t)255 : (uint8_t)nl;
            if (t.level[i] >= t.target[i]) t.holdTicks[i] = HOLD_TICKS;
        } else if (t.level[i] > t.target[i]) {
            int16_t nl = (int16_t)t.level[i] - t.fadeStep;
            t.level[i] = (nl < 0) ? (uint8_t)0 : (uint8_t)nl;
        }
    }
}

void LedStrand::paintTwinkleInto(const TwinkleState& t, std::vector<uint32_t>& buf,
                                 uint8_t width) {
    for (uint8_t i = 0; i < width; ++i) {
        size_t b = i / t.bitWidth;
        if (b >= t.level.size()) break;
        uint32_t fg = t.palette.empty() ? 0 : t.palette[t.colorIdx[b]];
        buf[i] = lerpColor(t.bgColor, fg, t.level[b]);
    }
}

void LedStrand::twinkleNL(const std::vector<uint32_t>& colors, uint8_t densityPct, uint8_t fadeStep,
                           uint32_t bgColor, uint8_t bitWidth) {
    dropCompositionNL();  // a preset takes the base buffer over
    pulseRunLen = 0;
    bitscrollMaster.clear();
    twinkleState.reset(length, colors, densityPct, fadeStep, bgColor, bitWidth);
    // Every spark starts at level 0, so this is exactly the first paint. A
    // call from a ProfileMode callback is flushed before the next advance, and
    // would otherwise show the previous animation for a frame.
    buffer.assign(length, bgColor);
    animMode = AnimMode::TWINKLE;
}

void LedStrand::bitscroll(const std::vector<BitScrollSegment>& segments, Speed speed, bool invert,
                           uint32_t bgColor, bool bounce, uint8_t spacing, bool repeating) {
    mutex.take();
    bitscrollNL(segments, speed, invert, bgColor, bounce, spacing, repeating);
    mutex.give();
}

// Capped at MAX_LEDS: a pattern wider than the whole strip can't usefully tile,
// and the cap keeps downstream size math (shiftVariant, bitscrollMaster) safely
// inside uint8_t range.
std::vector<uint32_t> LedStrand::buildBitscrollUnit(const BitScrollSegment* segments,
                                                    uint8_t count, uint32_t bgColor,
                                                    uint8_t spacing, size_t& contentLen,
                                                    std::vector<uint8_t>* coverage) {
    std::vector<uint32_t> unit;
    contentLen = 0;
    if (coverage != nullptr) coverage->clear();
    for (uint8_t s = 0; s < count; ++s) {
        const BitScrollSegment& seg = segments[s];
        for (uint8_t k = 0; k < seg.width && unit.size() < MAX_LEDS; ++k) {
            unit.push_back(seg.color);
            if (coverage != nullptr) coverage->push_back(255);
        }
        contentLen = unit.size();
        if (unit.size() >= MAX_LEDS) break;
        for (uint8_t k = 0; k < spacing && unit.size() < MAX_LEDS; ++k) {
            unit.push_back(bgColor);
            if (coverage != nullptr) coverage->push_back(0);
        }
        if (unit.size() >= MAX_LEDS) break;
    }
    if (unit.empty()) {
        unit.push_back(bgColor);
        if (coverage != nullptr) coverage->push_back(0);
        contentLen = unit.size();
    }
    return unit;
}

std::vector<uint32_t> LedStrand::buildBitscrollUnit(const std::vector<BitScrollSegment>& segments,
                                                    uint32_t bgColor, uint8_t spacing,
                                                    size_t& contentLen) {
    return buildBitscrollUnit(segments.empty() ? nullptr : &segments[0],
                              (uint8_t)segments.size(), bgColor, spacing, contentLen);
}

uint8_t LedStrand::fillBitscrollBuffer(std::vector<uint32_t>& buf,
                                       const std::vector<uint32_t>& unit, size_t contentLen,
                                       uint8_t width, uint32_t bgColor, uint8_t speed,
                                       bool invert, bool repeating) {
    if (repeating) {
        size_t reps = (size_t)(width / unit.size()) + 2;
        buf.clear();
        buf.reserve(unit.size() * reps);
        for (size_t r = 0; r < reps; ++r) buf.insert(buf.end(), unit.begin(), unit.end());
    } else {
        buf.assign(width, bgColor);
        size_t n = std::min(contentLen, (size_t)width);
        std::copy(unit.begin(), unit.begin() + n, buf.begin());
    }
    size_t bufSize = buf.size();
    // A zero-width region that doesn't repeat has nothing to scroll.
    if (bufSize == 0) return 0;
    uint8_t sp = (uint8_t)(speed % bufSize);
    return invert ? (uint8_t)((bufSize - sp) % bufSize) : sp;
}

void LedStrand::bitscrollNL(const std::vector<BitScrollSegment>& segments, Speed speed, bool invert,
                             uint32_t bgColor, bool bounce, uint8_t spacing, bool repeating) {
    dropCompositionNL();  // a preset takes the base buffer over
    pulseRunLen = 0;
    shiftPacer.reset(stepTicks(speed));

    size_t contentLen = 0;
    std::vector<uint32_t> unit = buildBitscrollUnit(segments, bgColor, spacing, contentLen);

    if (!bounce) {
        bitscrollMaster.clear();
        shiftStep = 0;
        shiftVariant = fillBitscrollBuffer(buffer, unit, contentLen, length, bgColor,
                                           speed.pixels, invert, repeating);
        animMode = AnimMode::SHIFT;
    } else {
        bitscrollMaster.clear();
        if (repeating) {
            size_t masterLen = std::max((size_t)length * 3, unit.size() * 3);
            bitscrollMaster.reserve(masterLen);
            while (bitscrollMaster.size() < masterLen)
                bitscrollMaster.insert(bitscrollMaster.end(), unit.begin(), unit.end());
        } else {
            // A single copy of the pattern, padded with background on both
            // sides so the visible window can carry it from the far end of the
            // strip to the near end and back.
            size_t n = std::min(contentLen, (size_t)length);
            size_t pad = (size_t)length - n;
            bitscrollMaster.assign(pad, bgColor);
            bitscrollMaster.insert(bitscrollMaster.end(), unit.begin(), unit.begin() + n);
            bitscrollMaster.insert(bitscrollMaster.end(), pad, bgColor);
        }

        if (buffer.size() != length) buffer.assign(length, bgColor);
        bounceScrollPos = 0;
        bounceScrollDir = 1;
        bounceSpeed = std::max<uint8_t>(speed.pixels, 1);
        animMode = AnimMode::STATIC;
        fillBitscrollFromMaster();
    }
}

void LedStrand::fillBitscrollFromMaster() {
    size_t maxStart = bitscrollMaster.size() > length ? bitscrollMaster.size() - length : 0;
    size_t start = (size_t)bounceScrollPos;
    if (start > maxStart) start = maxStart;
    for (uint8_t i = 0; i < length; ++i) buffer[i] = bitscrollMaster[start + i];
}

void LedStrand::advanceBitscrollBounce() {
    // bitscrollNL() already filled the window, so a tick that does not move
    // has nothing to repaint.
    if (!shiftPacer.due()) return;
    int16_t maxPos = (int16_t)bitscrollMaster.size() - (int16_t)length;
    if (maxPos < 0) maxPos = 0;
    bounceScrollPos = (int16_t)(bounceScrollPos + bounceScrollDir * bounceSpeed);
    if (bounceScrollPos >= maxPos) { bounceScrollPos = maxPos; bounceScrollDir = -1; }
    if (bounceScrollPos <= 0)      { bounceScrollPos = 0;       bounceScrollDir = 1; }
    fillBitscrollFromMaster();
}

void LedStrand::shiftBuffer() {
    size_t bufSize = buffer.size();
    if (bufSize == 0 || !shiftPacer.due()) return;
    shiftStep = (int)((shiftStep + shiftVariant) % (int)bufSize);
}

// ============================================================================
// Level meter / fill sources / music sync
// ============================================================================

void LedStrand::levelFill(uint32_t color, uint32_t color2, bool gradient, uint32_t bgColor,
                          bool invert) {
    mutex.take();
    levelFillNL(color, color2, gradient, bgColor, invert);
    // Manual control from here, see setLevel(). Either of the automatic
    // drivers would overwrite it on the next tick.
    musicTrack = nullptr;
    levelRead  = nullptr;
    mutex.give();
}

void LedStrand::levelFillNL(uint32_t color, uint32_t color2, bool gradient, uint32_t bg,
                            bool invert) {
    dropCompositionNL();  // a preset takes the base buffer over
    pulseRunLen = 0;
    bitscrollMaster.clear();
    // The meter's colors are laid across the whole strip once, here. All the
    // per-tick level does is decide how much of that is revealed, so driving
    // the meter costs no buffer work at all.
    if (gradient) buffer = genGradient(color, color2, length);
    else          buffer.assign(length, color);
    levelBg      = bg;
    levelInvert  = invert;
    shiftStep    = 0;
    shiftVariant = 0;
    animMode     = AnimMode::LEVEL;
}

void LedStrand::setLevel(uint8_t level) {
    mutex.take();
    levelValue = level;
    // A song or a live source would overwrite this on the next tick.
    musicTrack = nullptr;
    levelRead  = nullptr;
    mutex.give();
}

void LedStrand::levelSource(LevelFn read, double emptyAt, double fullAt, bool wrap,
                            uint8_t smoothing) {
    mutex.take();
    levelRead    = read;
    levelEmptyAt = emptyAt;
    levelFullAt  = fullAt;
    levelWrap    = wrap;
    levelSmooth  = smoothing > 99 ? 99 : smoothing; // 100 would never reach the target
    levelPrimed  = false;   // first sample lands outright, see advanceLevel()
    musicTrack   = nullptr; // a song would overwrite the reader on the next tick
    mutex.give();
}

void LedStrand::clearLevelSource() {
    mutex.take();
    levelRead = nullptr;
    mutex.give();
}

void LedStrand::musicSync(const MusicTrack& track, uint32_t color, uint32_t color2,
                          bool gradient, uint32_t bgColor, bool invert, uint8_t sensitivity,
                          bool loop) {
    mutex.take();
    levelFillNL(color, color2, gradient, bgColor, invert);
    musicTrack       = &track;
    levelRead        = nullptr; // the song drives the meter now
    musicSensitivity = sensitivity;
    musicLoop        = loop;
    musicPaused      = false;
    musicAnchorMs    = platform::millis();
    musicPausedAt    = 0;
    levelValue       = sampleMusicNL(0);
    mutex.give();
}

void LedStrand::musicSeek(uint32_t positionMs) {
    mutex.take();
    // Move the anchor rather than a position counter, so a seek while playing
    // resumes from the new spot at real-time speed with no further bookkeeping.
    musicAnchorMs = platform::millis() - positionMs;
    musicPausedAt = positionMs;
    levelValue    = sampleMusicNL(positionMs);
    mutex.give();
}

void LedStrand::musicPause(bool paused) {
    mutex.take();
    uint32_t now = platform::millis();
    if (paused && !musicPaused) {
        musicPausedAt = now - musicAnchorMs;
        musicPaused   = true;
    } else if (!paused && musicPaused) {
        musicAnchorMs = now - musicPausedAt;
        musicPaused   = false;
    }
    mutex.give();
}

uint32_t LedStrand::musicPositionMs() const {
    return musicPaused ? musicPausedAt : (platform::millis() - musicAnchorMs);
}

void LedStrand::setSensitivity(uint8_t pct) {
    mutex.take();
    musicSensitivity = pct;
    mutex.give();
}

void LedStrand::advanceLevel() {
    if (musicTrack) {
        levelValue = sampleMusicNL(musicPaused ? musicPausedAt : (platform::millis() - musicAnchorMs));
        return;
    }
    if (!levelRead) return;

    // The reader is user code and may well call back into this strand, so it
    // runs with the mutex released, the same way ProfileMode callbacks do.
    LevelFn read = levelRead;
    mutex.give();
    double raw = read();
    mutex.take();
    // Something may have re-pointed the meter while it was unlocked, in which
    // case this sample belongs to a source that no longer drives it.
    if (levelRead != read) return;
    // An unplugged device reports PROS_ERR_F (infinity). Holding the fill
    // avoids slamming the bar to full on a missing reading.
    if (!std::isfinite(raw)) return;

    uint8_t target = mapLevelNL(raw);
    levelValue  = levelPrimed ? smoothLevelNL(target) : target;
    levelPrimed = true;
}

// Where a raw reading sits between emptyAt and fullAt, as 0-255. A range given
// backwards (fullAt below emptyAt) falls out of the same arithmetic as a meter
// that drains while the value climbs.
uint8_t LedStrand::mapLevelTo(double raw, double emptyAt, double fullAt, bool wrap) {
    double span = fullAt - emptyAt;
    if (span == 0.0) return 0;

    double t = (raw - emptyAt) / span;
    if (wrap)           t -= std::floor(t);  // 1.25 -> 0.25, -0.25 -> 0.75
    else if (t < 0.0)   t = 0.0;
    else if (t > 1.0)   t = 1.0;

    int v = (int)(t * 255.0 + 0.5);
    if (v < 0)   v = 0;
    if (v > 255) v = 255;
    return (uint8_t)v;
}

uint8_t LedStrand::mapLevelNL(double raw) const {
    return mapLevelTo(raw, levelEmptyAt, levelFullAt, levelWrap);
}

// One tick of the smoothing filter: move part of the way to the target rather
// than snapping to it, so a jittery reading doesn't make the bar flicker.
uint8_t LedStrand::smoothLevelTo(uint8_t target, uint8_t current, uint8_t smoothing, bool wrap) {
    if (smoothing == 0) return target;

    int delta = (int)target - (int)current;
    if (delta == 0) return target;
    // A wrapping meter takes the short way round, otherwise a bar rolling over
    // from full to empty would sweep back down the whole strip to get there.
    if (wrap) {
        if      (delta >  128) delta -= 256;
        else if (delta < -128) delta += 256;
    }

    int step = delta * (100 - (int)smoothing) / 100;
    // Truncation would otherwise stall the bar a few pixels short of a target
    // it is already close to.
    if (step == 0) step = (delta > 0) ? 1 : -1;

    int next = (int)current + step;
    if (wrap)              next = ((next % 256) + 256) % 256;
    else if (next < 0)     next = 0;
    else if (next > 255)   next = 255;
    return (uint8_t)next;
}

uint8_t LedStrand::smoothLevelNL(uint8_t target) const {
    return smoothLevelTo(target, levelValue, levelSmooth, levelWrap);
}

// Read the envelope at an arbitrary millisecond offset, interpolating between
// the two frames it falls between. Interpolating lets the export store a coarse
// frame rate without the fill stepping at the strand's faster refresh rate.
uint8_t LedStrand::sampleTrackNL(const MusicTrack* track, uint32_t positionMs,
                                 uint8_t sensitivity, bool loop) {
    if (!track || track->samples == nullptr) return 0;
    const MusicTrack& t = *track;
    if (t.frameCount == 0 || t.frameMs == 0) return 0;

    uint32_t totalMs = (uint32_t)t.frameCount * (uint32_t)t.frameMs;
    if (positionMs >= totalMs) {
        if (!loop) return 0; // song over, let the meter drain
        positionMs %= totalMs;
    }

    uint32_t idx  = positionMs / t.frameMs;
    uint32_t frac = positionMs % t.frameMs;
    int a = t.samples[idx];
    int b;
    if (idx + 1 < t.frameCount) b = t.samples[idx + 1];
    else                        b = loop ? t.samples[0] : a;

    int raw    = a + (b - a) * (int)frac / (int)t.frameMs;
    int scaled = raw * (int)sensitivity / 100;
    if (scaled > 255) scaled = 255;
    if (scaled < 0)   scaled = 0;
    return (uint8_t)scaled;
}

uint8_t LedStrand::sampleMusicNL(uint32_t positionMs) const {
    return sampleTrackNL(musicTrack, positionMs, musicSensitivity, musicLoop);
}

// The color one meter pixel shows, given the fill cutoff for this frame:
// @p full whole lit pixels, and @p frac into the next one. The cutoff is
// computed once per flush, not per pixel. The partial pixel gives a 30-pixel
// strand more than 30 distinguishable levels.
uint32_t LedStrand::levelPixel(uint8_t i, uint32_t full, uint8_t frac) const {
    // Index along the direction of the fill, so the gradient always begins
    // where the fill begins.
    uint32_t f = levelInvert ? (uint32_t)(length - 1 - i) : (uint32_t)i;
    if (f < full)  return buffer[f];
    if (f == full) return lerpColor(levelBg, buffer[f], frac);
    return levelBg;
}

// ============================================================================
// Splice mask
// ============================================================================

void LedStrand::spliceMask(uint8_t sections, bool invert, bool alternating, uint32_t altPeriodMs,
                            uint32_t bgColor, bool useOverlay) {
    mutex.take();
    spliceMode = SpliceMode::SPLIT;
    spliceRegions.clear();
    ++spliceGeneration;
    spliceSections = sections;
    spliceInvert = invert;
    spliceAlternating = alternating;
    spliceAltMs = (altPeriodMs > 0) ? altPeriodMs : 1;
    spliceBgColor = bgColor;
    spliceUseOverlay = useOverlay;
    spliceActive = (sections != 0);
    spliceAltPhase = false;
    spliceLastToggleMs = platform::millis();
    rebuildSpliceMask();
    mutex.give();
}

void LedStrand::spliceMaskCustom(const std::vector<SpliceRegion>& regions) {
    mutex.take();
    spliceMode = SpliceMode::CUSTOM;
    spliceAlternating = false;
    std::fill(spliceShowAnim.begin(), spliceShowAnim.end(), true);
    std::fill(splicePixelRegionIdx.begin(), splicePixelRegionIdx.end(), (int16_t)-1);
    spliceRegions.clear();
    // Anything still holding a reference into spliceRegions - a gauge poll that
    // released the mutex to run user code - has to be able to notice this.
    ++spliceGeneration;

    for (size_t userIdx = 0; userIdx < regions.size(); ++userIdx) {
        const SpliceRegion& r = regions[userIdx];
        if (r.start >= length) continue;
        uint16_t end = (uint16_t)r.start + (uint16_t)r.width;
        if (end > length) end = length;
        uint8_t regionWidth = (uint8_t)(end - r.start);

        int16_t regionIdx = -1;
        uint32_t fallbackColor = 0x000000;

        if (r.kind == SpliceRegionAnimKind::SOLID) {
            fallbackColor = r.color;
        } else if (r.kind != SpliceRegionAnimKind::OFF) {
            SpliceRegionState state;
            state.start   = r.start;
            state.userIdx = (uint8_t)userIdx;
            switch (r.kind) {
                case SpliceRegionAnimKind::PULSE: {
                    state.buffer.assign(regionWidth, r.bgColor);
                    uint8_t rl = std::min<uint8_t>(r.runLength, regionWidth);
                    std::fill_n(state.buffer.begin(), rl, r.color);
                    state.shiftSpeed = shiftFor(r.speed.pixels, regionWidth, r.invert);
                    state.shiftPacer.reset(stepTicks(r.speed));
                    break;
                }
                case SpliceRegionAnimKind::FLASH: {
                    // Mirrors flashNL(), scaled to this region: the whole region
                    // blinks on a tick timer instead of shifting.
                    state.buffer.assign(regionWidth, r.color);
                    state.shiftSpeed    = 0;
                    state.flashing      = true;
                    state.flashColor    = r.color;
                    state.flashBgColor  = r.bgColor;
                    state.flashOnTicks  = msToTicks(r.onMs);
                    state.flashOffTicks = msToTicks(r.offMs);
                    state.flashCounter  = 0;
                    state.flashLit      = true;
                    break;
                }
                case SpliceRegionAnimKind::FLOW:
                    state.buffer = genGradient(r.color, r.color2, regionWidth, r.seamless);
                    state.shiftSpeed = shiftFor(r.speed.pixels, regionWidth, r.invert);
                    state.shiftPacer.reset(stepTicks(r.speed));
                    break;
                case SpliceRegionAnimKind::RAINBOW:
                    state.buffer = genRainbow(regionWidth);
                    state.shiftSpeed = shiftFor(r.speed.pixels, regionWidth, r.invert);
                    state.shiftPacer.reset(stepTicks(r.speed));
                    break;
                case SpliceRegionAnimKind::TWINKLE:
                    // Repaints its own pixels every tick, like the strand-wide
                    // twinkle, so it never scrolls.
                    state.buffer.assign(regionWidth, r.bgColor);
                    state.shiftSpeed = 0;
                    state.twinkling = true;
                    state.twinkle.reset(regionWidth, r.palette, r.densityPct, r.fadeStep, r.bgColor,
                                        r.bitWidth);
                    break;
                case SpliceRegionAnimKind::BITSCROLL: {
                    // No bounce: a region's buffer scrolls, and bouncing needs a
                    // wider master pattern to slide a window over.
                    size_t contentLen = 0;
                    std::vector<uint32_t> unit = buildBitscrollUnit(
                        {{r.color, r.segmentWidth}}, r.bgColor, r.spacing, contentLen);
                    state.shiftSpeed = fillBitscrollBuffer(state.buffer, unit, contentLen,
                                                           regionWidth, r.bgColor, r.speed.pixels,
                                                           r.invert, r.repeating);
                    state.shiftPacer.reset(stepTicks(r.speed));
                    break;
                }
                case SpliceRegionAnimKind::GAUGE: {
                    state.buffer.assign(regionWidth, r.bgColor);
                    state.shiftSpeed   = 0;   // a gauge repaints, it never scrolls
                    state.gauge        = true;
                    state.levelRead    = r.read;
                    state.levelEmptyAt = r.emptyAt;
                    state.levelFullAt  = r.fullAt;
                    state.levelWrap    = r.wrap;
                    state.levelSmooth  = std::min<uint8_t>(r.smoothing, 99); // 100 never arrives
                    state.levelPrimed  = false;
                    state.gaugeInvert  = r.invert;
                    state.gaugeStyle   = r.style;
                    state.gaugeBlend   = r.blend;
                    state.gaugeBg      = r.bgColor;

                    // Resolve the scale onto the same 0-255 axis levelValue
                    // lives on, once, so a tick only has to bracket a byte.
                    // An empty scale becomes the two-color fallback, so there
                    // is always something to bracket between.
                    std::vector<GaugeStop> scale = r.stops;
                    if (scale.empty()) scale = {{r.emptyAt, r.color}, {r.fullAt, r.color2}};
                    state.gaugeStops.reserve(scale.size());
                    for (const GaugeStop& gs : scale) {
                        // wrap is false here whatever the gauge itself does: a
                        // stop's place on the scale is a clamp, not a lap.
                        state.gaugeStops.emplace_back(
                            mapLevelTo(gs.at, r.emptyAt, r.fullAt, false), gs.color);
                    }
                    // By level only, and stable: two stops at one reading are a
                    // hard edge, and which side each color is on is the order
                    // they were given in, not which one is numerically larger.
                    std::stable_sort(state.gaugeStops.begin(), state.gaugeStops.end(),
                                     [](const std::pair<uint8_t, uint32_t>& a,
                                        const std::pair<uint8_t, uint32_t>& b) {
                                         return a.first < b.first;
                                     });
                    break;
                }
                default:
                    break;
            }
            spliceRegions.push_back(std::move(state));
            regionIdx = (int16_t)(spliceRegions.size() - 1);
            // A gauge shows the bottom of its scale rather than its background
            // on the very first frame, instead of waiting for the first poll.
            if (spliceRegions.back().gauge) paintGaugeRegion(spliceRegions.back());
        }

        for (uint16_t i = r.start; i < end; ++i) {
            spliceShowAnim[i] = false;
            splicePixelBg[i] = fallbackColor;
            splicePixelUseOverlay[i] = false;
            splicePixelRegionIdx[i] = regionIdx;
        }
    }
    spliceActive = !regions.empty();
    mutex.give();
}

void LedStrand::setRegionLevel(uint8_t regionIdx, uint8_t level) {
    mutex.take();
    for (SpliceRegionState& state : spliceRegions) {
        // A gauge with a reader of its own would overwrite this on the next
        // tick, so setting it would only ever look like a glitch.
        if (!state.gauge || state.userIdx != regionIdx || state.levelRead) continue;
        state.levelValue  = level;
        state.levelPrimed = true;
        paintGaugeRegion(state);
        break;
    }
    mutex.give();
}

LedStrand::SpliceRegion LedStrand::motorHeatGauge(uint8_t start, uint8_t width, LevelFn read) {
    SpliceRegion r;
    r.start   = start;
    r.width   = width;
    r.kind    = SpliceRegionAnimKind::GAUGE;
    r.read    = read;
    r.emptyAt = 20.0;
    r.fullAt  = 70.0;
    // The V5 reports motor temperature in coarse steps, so without smoothing
    // a segment jumps from one stop color straight to the next.
    r.smoothing = 80;
    r.stops = {
        {20.0, 0x00FF00}, // cold, full power
        {45.0, 0xFFFF00}, // warm - the first current cut is coming
        {55.0, 0xFF7000}, // current limited to 50%
        {60.0, 0xFF2000}, // current limited to 25%
        {65.0, 0xFF0000}, // current limited to 12.5%
        {70.0, 0xFF00FF}, // shut down. Deliberately not on the red ramp: this
                          // one has to not read as "slightly worse than before".
    };
    return r;
}

void LedStrand::spreadMask(Speed speed, SpreadOrigin origin, bool invert, bool bounce,
                           uint32_t bgColor, bool useOverlay) {
    mutex.take();
    spliceMode        = SpliceMode::SPREAD;
    spliceAlternating = false;
    spliceRegions.clear();
    ++spliceGeneration;
    spliceBgColor     = bgColor;
    spliceUseOverlay  = useOverlay;
    spreadOrigin      = origin;
    spreadInvert      = invert;
    spreadBounce      = bounce;
    spreadSpeed       = std::max<uint8_t>(speed.pixels, 1);
    spreadPacer.reset(stepTicks(speed));
    spreadPos         = 0;   // starts empty, and grows from there
    spreadDir         = 1;
    spliceActive      = true;
    mutex.give();
}

void LedStrand::twinkleMask(uint8_t densityPct, uint8_t fadeStep, uint8_t bitWidth, bool invert,
                            uint32_t bgColor, bool useOverlay) {
    mutex.take();
    spliceMode        = SpliceMode::TWINKLE;
    spliceAlternating = false;
    spliceRegions.clear();
    ++spliceGeneration;
    spliceInvert      = invert;
    spliceBgColor     = bgColor;
    spliceUseOverlay  = useOverlay;
    // Only the levels are ever read. The palette just has to be non-empty,
    // since an empty one is what tells a twinkle never to spawn.
    maskTwinkle.reset(length, std::vector<uint32_t>(1, 0xFFFFFF), densityPct, fadeStep, 0, bitWidth);
    spliceActive      = true;
    mutex.give();
}

void LedStrand::clearSpliceMask() {
    mutex.take();
    spliceActive = false;
    mutex.give();
}

uint8_t LedStrand::spreadRanks(uint8_t len, SpreadOrigin origin) {
    return origin == SpreadOrigin::CENTER ? (uint8_t)((len + 1) / 2) : len;
}

uint8_t LedStrand::spreadRank(uint8_t i, uint8_t len, SpreadOrigin origin, bool invert) {
    if (origin == SpreadOrigin::EDGE) return invert ? (uint8_t)(len - 1 - i) : i;
    // How far from the middle, a pair at a time: |2i - (len - 1)| / 2 puts the
    // middle pixel, or the middle two, at 0 and both end pixels at the last
    // rank, whichever way round len's parity falls.
    uint8_t fromMiddle = (uint8_t)(std::abs(2 * (int)i - ((int)len - 1)) / 2);
    return invert ? (uint8_t)(spreadRanks(len, origin) - 1 - fromMiddle) : fromMiddle;
}

// Moves the spread's edge on its own clock, the same way a bouncing pulse
// moves its run: a whole step at a time, held at each end for exactly one.
void LedStrand::advanceSpreadMask() {
    if (!spliceActive || spliceMode != SpliceMode::SPREAD || !spreadPacer.due()) return;
    int16_t ranks = spreadRanks(length, spreadOrigin);
    if (spreadBounce) {
        int16_t pos = (int16_t)(spreadPos + spreadDir * spreadSpeed);
        if (pos >= ranks) { pos = ranks; spreadDir = -1; }
        if (pos <= 0)     { pos = 0;     spreadDir = 1; }
        spreadPos = (uint8_t)pos;
    } else {
        // Full for one step, then empty again, rather than stepping past full:
        // a speed that does not divide the strip would otherwise never show
        // it covered.
        spreadPos = (spreadPos >= ranks)
                  ? (uint8_t)0
                  : (uint8_t)std::min<int16_t>((int16_t)(spreadPos + spreadSpeed), ranks);
    }
}

uint8_t LedStrand::maskCoverage(uint8_t i) const {
    if (spliceMode == SpliceMode::SPREAD)
        return spreadRank(i, length, spreadOrigin, spreadInvert) < spreadPos ? 255 : 0;
    size_t  b     = i / maskTwinkle.bitWidth;
    uint8_t level = b < maskTwinkle.level.size() ? maskTwinkle.level[b] : 0;
    return spliceInvert ? (uint8_t)(255 - level) : level;
}

// ============================================================================
// Transitions
// ============================================================================

void LedStrand::transition(const Transition& t) {
    mutex.take();
    if (t.kind == TransitionKind::CUT || t.durationMs == 0) {
        transKind = TransitionKind::CUT;
        mutex.give();
        return;
    }
    // What the strip is showing right now, wipe and all, so a transition that
    // lands on another still in progress carries on from where that one was.
    transFrom.assign(lastSent, lastSent + length);
    transKind     = t.kind;
    transOrigin   = t.origin;
    transInvert   = t.invert;
    transBitWidth = t.bitWidth ? t.bitWidth : 1;
    transFadeStep = t.fadeStep ? t.fadeStep : 255;
    transTicks    = msToTicks(t.durationMs);
    transElapsed  = 0;

    if (t.kind == TransitionKind::TWINKLE) {
        // Each bit takes fadeTicks to come in, so it has to start by
        // transTicks - fadeTicks to be fully in when the wipe ends. Inside
        // that window, when is down to chance.
        uint8_t  bits      = (uint8_t)(((uint16_t)length + transBitWidth - 1) / transBitWidth);
        uint16_t fadeTicks = (uint16_t)((255 + transFadeStep - 1) / transFadeStep);
        // A wipe too short for its fade fades faster, taking half of it at
        // most. Left as asked, every bit had to start at once and still never
        // reached full - a flat crossfade that jumped the last step.
        if (fadeTicks > transTicks / 2) {
            fadeTicks     = transTicks >= 2 ? (uint16_t)(transTicks / 2) : 1;
            transFadeStep = (uint8_t)((255 + fadeTicks - 1) / fadeTicks);
        }
        uint16_t window    = transTicks > fadeTicks ? (uint16_t)(transTicks - fadeTicks) : 0;
        seedRandOnce();
        transBitStart.assign(bits, 0);
        for (uint8_t b = 0; b < bits; ++b)
            transBitStart[b] = window ? (uint16_t)(std::rand() % (window + 1)) : 0;
    }
    mutex.give();
}

uint8_t LedStrand::transitionCoverage(uint8_t i, uint32_t full, uint8_t frac) const {
    if (transKind == TransitionKind::SPREAD) {
        uint8_t rank = spreadRank(i, length, transOrigin, transInvert);
        if (rank < full)  return 255;
        if (rank == full) return frac;
        return 0;
    }
    size_t b = i / transBitWidth;
    if (b >= transBitStart.size() || transElapsed <= transBitStart[b]) return 0;
    uint32_t level = (uint32_t)(transElapsed - transBitStart[b]) * transFadeStep;
    return level > 255 ? (uint8_t)255 : (uint8_t)level;
}

void LedStrand::rebuildSpliceMask() {
    uint16_t binCount = (uint16_t)spliceSections + 1;
    uint8_t base = length / binCount;
    uint8_t rem = length % binCount;
    uint8_t idx = 0;
    for (uint16_t b = 0; b < binCount && idx < length; ++b) {
        uint8_t binSize = base + (b < rem ? 1 : 0);
        bool showAnim = ((b % 2) == 1) != spliceInvert;
        for (uint8_t k = 0; k < binSize && idx < length; ++k) {
            spliceShowAnim[idx] = showAnim;
            splicePixelBg[idx] = spliceBgColor;
            splicePixelUseOverlay[idx] = spliceUseOverlay;
            splicePixelRegionIdx[idx] = -1;
            idx++;
        }
    }
}

void LedStrand::advanceSpliceAlternating(uint32_t nowMs) {
    if (!spliceActive || spliceMode != SpliceMode::SPLIT || !spliceAlternating) return;
    if (nowMs - spliceLastToggleMs >= spliceAltMs) {
        spliceInvert = !spliceInvert;
        spliceLastToggleMs = nowMs;
        rebuildSpliceMask();
    }
}

void LedStrand::advanceSpliceRegions() {
    if (!spliceActive || spliceMode != SpliceMode::CUSTOM) return;
    // Indexed rather than ranged: polling a gauge releases the mutex, and a
    // reference into the vector would not survive user code replacing the mask.
    for (size_t idx = 0; idx < spliceRegions.size(); ++idx) {
        if (spliceRegions[idx].gauge) {
            LevelFn read = spliceRegions[idx].levelRead;
            if (read) {
                // The reader is user code and may well call back into this
                // strand, so it runs unlocked, the same way advanceLevel() and
                // the ProfileMode callbacks do.
                uint32_t generation = spliceGeneration;
                mutex.give();
                double raw = read();
                mutex.take();
                // The mask may have been replaced while it was unlocked, in
                // which case every state in this vector belongs to someone else
                // now and the loop has nothing left to walk.
                if (generation != spliceGeneration) return;
                // An unplugged device reports PROS_ERR_F (infinity). Holding
                // the color avoids slamming the segment to the top of its
                // scale.
                if (std::isfinite(raw)) {
                    SpliceRegionState& g = spliceRegions[idx];
                    uint8_t target = mapLevelTo(raw, g.levelEmptyAt, g.levelFullAt, g.levelWrap);
                    g.levelValue = g.levelPrimed
                        ? smoothLevelTo(target, g.levelValue, g.levelSmooth, g.levelWrap)
                        : target;
                    g.levelPrimed = true;
                }
            }
            paintGaugeRegion(spliceRegions[idx]);
            continue;
        }

        SpliceRegionState& state = spliceRegions[idx];
        size_t bufSize = state.buffer.size();
        if (bufSize == 0) continue;
        if (state.flashing) {
            uint16_t hold = state.flashLit ? state.flashOnTicks : state.flashOffTicks;
            if (state.flashCounter >= hold) {
                state.flashCounter = 0;
                state.flashLit = !state.flashLit;
                std::fill(state.buffer.begin(), state.buffer.end(),
                          state.flashLit ? state.flashColor : state.flashBgColor);
            }
            ++state.flashCounter;
            continue;
        }
        if (state.twinkling) {
            advanceTwinkleInto(state.twinkle, state.buffer);
            continue;
        }
        if (state.shiftSpeed == 0 || !state.shiftPacer.due()) continue;
        state.shiftStep = (int)((state.shiftStep + state.shiftSpeed) % (int)bufSize);
    }
}

// The color a gauge's scale gives for a 0-255 level. Stops are already sorted
// and already on that axis, so this is a bracket and (at most) one lerp.
uint32_t LedStrand::scaleColorAt(const std::pair<uint8_t, uint32_t>* stops, uint8_t count,
                                 GaugeBlend blend, uint32_t bgColor, uint8_t level) {
    if (stops == nullptr || count == 0) return bgColor;
    // Past either end the scale holds, so it never has to cover a range the
    // gauge does not care about.
    if (level <= stops[0].first)         return stops[0].second;
    if (level >= stops[count - 1].first) return stops[count - 1].second;

    // STEP holds each stop until the next one is reached, for scales whose
    // stops are thresholds rather than points on a ramp. A reading exactly on
    // a stop has reached it: the last stop at or below the level wins.
    if (blend == GaugeBlend::STEP) {
        uint8_t j = (uint8_t)(count - 1);
        while (stops[j].first > level) --j;   // stops[0] is below, see above
        return stops[j].second;
    }

    uint8_t i = 1;
    while (i < count && stops[i].first < level) ++i;
    const std::pair<uint8_t, uint32_t>& lo = stops[i - 1];
    const std::pair<uint8_t, uint32_t>& hi = stops[i];

    uint8_t span = (uint8_t)(hi.first - lo.first);
    if (span == 0) return hi.second;
    uint8_t t = (uint8_t)(((uint32_t)(level - lo.first) * 255u) / span);
    return lerpColor(lo.second, hi.second, t);
}

uint32_t LedStrand::gaugeColorAt(const SpliceRegionState& state, uint8_t level) {
    const std::vector<std::pair<uint8_t, uint32_t>>& stops = state.gaugeStops;
    return scaleColorAt(stops.empty() ? nullptr : &stops[0], (uint8_t)stops.size(),
                        state.gaugeBlend, state.gaugeBg, level);
}

// Repaint one gauge region from its current level. Called every tick, not
// only on a change: at most 64 writes, and it stays correct after a
// setRegionLevel() from another task without needing a dirty flag.
void LedStrand::paintGaugeRegion(SpliceRegionState& state) {
    size_t width = state.buffer.size();
    if (width == 0) return;

    if (state.gaugeStyle == GaugeStyle::HEAT) {
        std::fill(state.buffer.begin(), state.buffer.end(),
                  gaugeColorAt(state, state.levelValue));
        return;
    }

    // BAR: flushBuffer()'s cutoff arithmetic for the strand-wide meter, scoped
    // to this region's own pixels - whole lit pixels plus a fraction of the
    // next one, so a 10-pixel segment still shows more than 10 levels.
    uint32_t litQ8 = (uint32_t)state.levelValue * (uint32_t)width;
    uint32_t full  = litQ8 / 255;
    uint8_t  frac  = (uint8_t)(litQ8 % 255);
    for (size_t k = 0; k < width; ++k) {
        // Index along the direction of the fill, so the scale always begins
        // where the fill begins - as levelPixel() does for the whole strip.
        size_t f = state.gaugeInvert ? (width - 1 - k) : k;
        uint8_t pos = (width <= 1) ? 255 : (uint8_t)((f * 255) / (width - 1));
        uint32_t scale = gaugeColorAt(state, pos);
        if      (f <  full) state.buffer[k] = scale;
        else if (f == full) state.buffer[k] = lerpColor(state.gaugeBg, scale, frac);
        else                state.buffer[k] = state.gaugeBg;
    }
}

// ============================================================================
// Overlay animations
// ============================================================================

void LedStrand::overlaySetColor(uint32_t color) { mutex.take(); overlaySetColorNL(color); mutex.give(); }

void LedStrand::overlayPulse(uint32_t color, uint8_t runLength, Speed speed, uint32_t bgColor,
                             bool invert) {
    mutex.take();
    overlayPulseNL(color, runLength, speed, bgColor, invert);
    mutex.give();
}

void LedStrand::overlayFlash(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bgColor) {
    mutex.take();
    overlayFlashNL(color, onMs, offMs, bgColor);
    mutex.give();
}

void LedStrand::overlayFlow(uint32_t color1, uint32_t color2, Speed speed, bool seamless,
                            bool invert) {
    mutex.take();
    overlayFlowNL(color1, color2, speed, seamless, invert);
    mutex.give();
}

void LedStrand::overlayTwinkle(const std::vector<uint32_t>& colors, uint8_t densityPct,
                               uint8_t fadeStep, uint32_t bgColor, uint8_t bitWidth) {
    mutex.take();
    overlayTwinkleNL(colors, densityPct, fadeStep, bgColor, bitWidth);
    mutex.give();
}

void LedStrand::overlayBitscroll(const std::vector<BitScrollSegment>& segments, Speed speed,
                                 bool invert, uint32_t bgColor, uint8_t spacing, bool repeating) {
    mutex.take();
    overlayBitscrollNL(segments, speed, invert, bgColor, spacing, repeating);
    mutex.give();
}

void LedStrand::overlayRainbow(Speed speed, bool invert) {
    mutex.take();
    overlayRainbowNL(speed, invert);
    mutex.give();
}

void LedStrand::overlaySetColorNL(uint32_t color) {
    overlayBuffer.assign(length, color);
    overlayAnimMode = AnimMode::STATIC;
    overlayShiftStep = 0;
    overlayShiftSpeed = 0;
}

void LedStrand::overlayPulseNL(uint32_t color, uint8_t runLen, Speed speed, uint32_t bg,
                               bool invert) {
    overlayBuffer.assign(length, bg);
    uint8_t rl = std::min<uint8_t>(runLen, length);
    std::fill_n(overlayBuffer.begin(), rl, color);
    overlayShiftStep = 0;
    overlayShiftSpeed = shiftFor(speed.pixels, length, invert);
    overlayShiftPacer.reset(stepTicks(speed));
    overlayAnimMode = AnimMode::SHIFT;
}

void LedStrand::overlayFlashNL(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bg) {
    // Mirrors flashNL(): tick-timed blink with independent on/off durations.
    overlayFlashColor    = color;
    overlayFlashBgColor  = bg;
    overlayFlashOnTicks  = msToTicks(onMs);
    overlayFlashOffTicks = msToTicks(offMs);
    overlayFlashCounter  = 0;
    overlayFlashLit      = true;
    overlayBuffer.assign(length, color);
    overlayShiftStep = 0;
    overlayShiftSpeed = 0;
    overlayAnimMode = AnimMode::FLASH;
}

void LedStrand::advanceOverlayFlash() {
    uint16_t hold = overlayFlashLit ? overlayFlashOnTicks : overlayFlashOffTicks;
    if (overlayFlashCounter >= hold) {
        overlayFlashCounter = 0;
        overlayFlashLit = !overlayFlashLit;
        if (overlayBuffer.size() != length) overlayBuffer.assign(length, overlayFlashBgColor);
        std::fill(overlayBuffer.begin(), overlayBuffer.end(),
                  overlayFlashLit ? overlayFlashColor : overlayFlashBgColor);
    }
    ++overlayFlashCounter;
}

void LedStrand::overlayFlowNL(uint32_t c1, uint32_t c2, Speed speed, bool seamless,
                              bool invert) {
    overlayBuffer = genGradient(c1, c2, length, seamless);
    overlayShiftStep = 0;
    overlayShiftSpeed = shiftFor(speed.pixels, length, invert);
    overlayShiftPacer.reset(stepTicks(speed));
    overlayAnimMode = AnimMode::SHIFT;
}

void LedStrand::overlayRainbowNL(Speed speed, bool invert) {
    overlayBuffer = genRainbow(length);
    overlayShiftStep = 0;
    overlayShiftSpeed = shiftFor(speed.pixels, length, invert);
    overlayShiftPacer.reset(stepTicks(speed));
    overlayAnimMode = AnimMode::SHIFT;
}

void LedStrand::overlayTwinkleNL(const std::vector<uint32_t>& colors, uint8_t densityPct,
                                 uint8_t fadeStep, uint32_t bgColor, uint8_t bitWidth) {
    overlayTwinkleState.reset(length, colors, densityPct, fadeStep, bgColor, bitWidth);
    overlayBuffer.assign(length, bgColor);
    overlayShiftStep = 0;
    overlayShiftSpeed = 0;
    overlayAnimMode = AnimMode::TWINKLE;
}

void LedStrand::overlayBitscrollNL(const std::vector<BitScrollSegment>& segments, Speed speed,
                                   bool invert, uint32_t bgColor, uint8_t spacing, bool repeating) {
    size_t contentLen = 0;
    std::vector<uint32_t> unit = buildBitscrollUnit(segments, bgColor, spacing, contentLen);
    overlayShiftStep = 0;
    overlayShiftSpeed = fillBitscrollBuffer(overlayBuffer, unit, contentLen, length, bgColor,
                                            speed.pixels, invert, repeating);
    overlayShiftPacer.reset(stepTicks(speed));
    overlayAnimMode = AnimMode::SHIFT;
}

void LedStrand::shiftOverlayBuffer() {
    size_t bufSize = overlayBuffer.size();
    if (bufSize == 0 || !overlayShiftPacer.due()) return;
    overlayShiftStep = (int)((overlayShiftStep + overlayShiftSpeed) % (int)bufSize);
}

// ============================================================================
// Brightness
// ============================================================================

void LedStrand::setBrightness(uint8_t pct) {
    mutex.take();
    brightnessPct = std::min<uint8_t>(pct, 100);
    mutex.give();
}

uint32_t LedStrand::applyBrightness(uint32_t color) const {
    uint32_t r = ((color >> 16) & 0xFF) * brightnessPct / 100;
    uint32_t g = ((color >> 8) & 0xFF) * brightnessPct / 100;
    uint32_t b = (color & 0xFF) * brightnessPct / 100;
    return (r << 16) | (g << 8) | b;
}

// Round a wall-clock duration to whole refresh ticks. Animations can only
// change state on a tick, so anything shorter than one interval is clamped up
// to a single tick rather than silently becoming zero (which would spin the
// phase every frame).
uint16_t LedStrand::msToTicks(uint32_t ms) const {
    if (refreshMs == 0) return 1;
    // In 64 bits, so a "forever" of UINT32_MAX can't wrap round to one tick.
    uint64_t ticks = ((uint64_t)ms + refreshMs / 2) / refreshMs;
    if (ticks < 1) ticks = 1;
    if (ticks > 0xFFFF) ticks = 0xFFFF;
    return (uint16_t)ticks;
}

// ============================================================================
// Profile system
// ============================================================================

void LedStrand::attachProfile(const Profile* profile) {
    mutex.take();
    activeProfile = profile;
    modeStack.clear();
    lastModeIdx = -1;
    mutex.give();
}

void LedStrand::detachProfile() {
    mutex.take();
    activeProfile = nullptr;
    modeStack.clear();
    lastModeIdx = -1;
    setColorNL(0);
    mutex.give();
}

void LedStrand::activateMode(uint8_t modeIdx) {
    mutex.take();
    for (auto& e : modeStack) {
        if (e.modeIdx == modeIdx) {
            e.persistent = true; // upgrade a timed entry to persistent rather than duplicate it
            mutex.give();
            return;
        }
    }
    modeStack.push_back({modeIdx, true, 0});
    mutex.give();
}

void LedStrand::activateModeTimed(uint8_t modeIdx, uint32_t durationMs) {
    // pruneExpired() compares deadlines wrap-safely, which only works for a
    // deadline less than half the clock's range away. That is still 24 days.
    if (durationMs > 0x7FFFFFFFu) durationMs = 0x7FFFFFFFu;
    mutex.take();
    uint32_t now = platform::millis();
    for (auto& e : modeStack) {
        if (e.modeIdx == modeIdx) {
            if (!e.persistent) e.endMs = now + durationMs; // extend deadline, don't downgrade persistent
            mutex.give();
            return;
        }
    }
    modeStack.push_back({modeIdx, false, now + durationMs});
    mutex.give();
}

void LedStrand::deactivateMode(uint8_t modeIdx) {
    mutex.take();
    modeStack.erase(std::remove_if(modeStack.begin(), modeStack.end(),
                                    [modeIdx](const ModeEntry& e) { return e.modeIdx == modeIdx; }),
                     modeStack.end());
    mutex.give();
}

// Wrap-safe, like every other timer here: now + durationMs can pass 2^32
// (49.7 days of uptime), and a plain `now >= endMs` then expires the mode on
// the very next tick.
void LedStrand::pruneExpired(uint32_t now) {
    if (modeStack.empty()) return;
    modeStack.erase(std::remove_if(modeStack.begin(), modeStack.end(),
                                    [now](const ModeEntry& e) {
                                        return !e.persistent && (int32_t)(now - e.endMs) >= 0;
                                    }),
                     modeStack.end());
}

int16_t LedStrand::computeEffectiveMode() const {
    if (!activeProfile || modeStack.empty()) return -1;
    int16_t winner = -1;
    int winnerPriority = -1;
    for (const auto& e : modeStack) {
        if (e.modeIdx >= activeProfile->modeCount) continue; // ignore out-of-range indices
        int p = activeProfile->modes[e.modeIdx].priority;
        if (p >= winnerPriority) {
            winnerPriority = p;
            winner = e.modeIdx;
        }
    }
    return winner;
}

// ============================================================================
// Compositing / flush
// ============================================================================

// Called with the mutex held, as the last thing tick() does, and returns with
// it given back: the frame is built under it, but sent without it.
void LedStrand::flushBuffer(uint32_t now) {
    size_t bufSize = buffer.size();
    size_t overlayBufSize = overlayBuffer.size();

    // Meter fill cutoff, in whole pixels plus a fraction of the next one.
    // Constant across the frame, so it is worked out here rather than inside
    // the per-pixel loop.
    uint32_t litQ8     = (uint32_t)levelValue * (uint32_t)length;
    uint32_t levelFull = litQ8 / 255;
    uint8_t  levelFrac = (uint8_t)(litQ8 % 255);

    bool wipeMask = spliceActive &&
                    (spliceMode == SpliceMode::SPREAD || spliceMode == SpliceMode::TWINKLE);

    uint32_t frame[MAX_CHAIN_LEDS];
    for (uint8_t i = 0; i < length; ++i) {
        uint32_t baseColor;
        if (animMode == AnimMode::LEVEL) {
            baseColor = levelPixel(i, levelFull, levelFrac);
        } else if (animMode == AnimMode::SHIFT && bufSize > 0) {
            baseColor = buffer[((size_t)i + (size_t)shiftStep) % bufSize];
        } else {
            baseColor = buffer[i];
        }

        // Mirrors baseColor above. Without this, overlayShiftStep (advanced
        // every tick by shiftOverlayBuffer()) is computed but never actually
        // read, so overlay animations render as a single frozen frame instead
        // of animating.
        uint32_t overlayColor;
        if (overlayAnimMode == AnimMode::SHIFT && overlayBufSize > 0) {
            overlayColor = overlayBuffer[((size_t)i + (size_t)overlayShiftStep) % overlayBufSize];
        } else {
            overlayColor = overlayBuffer[i];
        }

        uint32_t color = baseColor;

        if (wipeMask) {
            // A soft mask: a half-faded twinkle bit shows half the animation.
            uint32_t masked = spliceUseOverlay ? overlayColor : spliceBgColor;
            color = lerpColor(masked, baseColor, maskCoverage(i));
        } else if (spliceActive && !spliceShowAnim[i]) {
            int16_t regionIdx = splicePixelRegionIdx[i];
            if (regionIdx >= 0) {
                const SpliceRegionState& state = spliceRegions[regionIdx];
                size_t regionBufSize = state.buffer.size();
                if (regionBufSize > 0) {
                    uint8_t localOffset = (uint8_t)(i - state.start);
                    color = state.buffer[((size_t)localOffset + (size_t)state.shiftStep) % regionBufSize];
                } else {
                    color = splicePixelBg[i];
                }
            } else {
                color = splicePixelUseOverlay[i] ? overlayColor : splicePixelBg[i];
            }
        }

        frame[i] = applyBrightness(color);
    }

    // A transition wipes the finished frame in over the one it started from,
    // after brightness on both sides, since what it started from is what was
    // sent.
    if (transKind != TransitionKind::CUT) {
        ++transElapsed;
        // The SPREAD cutoff, as the meter's: whole ranks covered, and a
        // fraction of the next, so the edge glides rather than steps.
        uint32_t coverQ8 = (uint32_t)transElapsed * spreadRanks(length, transOrigin) * 255u /
                           transTicks;
        uint32_t full = coverQ8 / 255;
        uint8_t  frac = (uint8_t)(coverQ8 % 255);
        for (uint8_t i = 0; i < length && i < transFrom.size(); ++i)
            frame[i] = lerpColor(transFrom[i], frame[i], transitionCoverage(i, full, frac));
        if (transElapsed >= transTicks) transKind = TransitionKind::CUT;
    }

    // Compared after brightness, so a setBrightness() alone still goes out.
    // Strip by strip: a chain whose second strip is holding still does not
    // pay for its 12 ms because the first one moved.
    // One keep-alive resend a flush at most: strips sent together come due
    // together, and a chain of four holding still paid 48 ms in one tick
    // every second - a hitch on any strip of it still moving. Taken one a
    // tick, they spread themselves out and stay that way.
    bool    send[MAX_SEGMENTS] = {};
    bool    resent = false;
    uint8_t at = 0;
    for (uint8_t s = 0; s < segmentCount; ++s) {
        uint8_t n = strips[s].length;
        bool changed = !lastSentValid || !std::equal(frame + at, frame + at + n, lastSent + at);
        bool due     = now - strips[s].sentMs >= RESEND_MS;
        send[s] = changed || (due && !resent);
        if (send[s] && !changed) resent = true;
        if (send[s]) strips[s].sentMs = now;
        at = (uint8_t)(at + n);
    }
    // The strips not sent already hold exactly this, so the whole frame is
    // what the chain now shows.
    std::copy(frame, frame + length, lastSent);
    lastSentValid = true;

    // frame[] is a local copy and no strip's `led` changes after init(), so
    // the writes need nothing the lock protects. Holding it through the
    // 12 ms below would stall any setLevel() or setColor() from the control
    // loop that landed meanwhile.
    mutex.give();

    at = 0;
    for (uint8_t s = 0; s < segmentCount; ++s) {
        uint8_t n = strips[s].length;
        if (send[s] && strips[s].led) {
            s_adiMutex.take();
            strips[s].led->write(frame + at, n);
            // Give the ADI LED driver some breathing room between updates.
            platform::delay(12);
            s_adiMutex.give();
        }
        at = (uint8_t)(at + n);
    }
}

// ============================================================================
// Static generators
// ============================================================================

uint32_t LedStrand::gradientPixel(uint32_t c1, uint32_t c2, uint8_t i, uint8_t len,
                                  bool seamless) {
    int r1 = (c1 >> 16) & 0xFF, g1 = (c1 >> 8) & 0xFF, b1 = c1 & 0xFF;
    int r2 = (c2 >> 16) & 0xFF, g2 = (c2 >> 8) & 0xFF, b2 = c2 & 0xFF;
    int t;
    if (len <= 1) {
        t = 0;
    } else if (seamless) {
        // Triangle wave over the ring: c1 at i=0, c2 at the midpoint, and
        // back toward c1 by i=len-1, so a circular shift loops with no
        // hard cut where the buffer wraps.
        int x = (int)i * 2 * 255 / len;
        t = 255 - std::abs(x - 255);
    } else {
        t = (int)i * 255 / (len - 1);
    }
    uint8_t r = (uint8_t)(r1 + (r2 - r1) * t / 255);
    uint8_t g = (uint8_t)(g1 + (g2 - g1) * t / 255);
    uint8_t b = (uint8_t)(b1 + (b2 - b1) * t / 255);
    return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

std::vector<uint32_t> LedStrand::genGradient(uint32_t c1, uint32_t c2, uint8_t len, bool seamless) {
    std::vector<uint32_t> out(len);
    for (uint8_t i = 0; i < len; ++i) out[i] = gradientPixel(c1, c2, i, len, seamless);
    return out;
}

std::vector<uint32_t> LedStrand::genRainbow(uint8_t len) {
    std::vector<uint32_t> out(len);
    for (uint8_t i = 0; i < len; ++i) {
        uint8_t hue = (uint8_t)((int)i * 256 / std::max<int>(len, 1));
        out[i] = wheel(hue);
    }
    return out;
}

} // namespace hitlib
