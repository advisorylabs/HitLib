#include "hitlib/led_strand.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>

// Shared across every LedStrand instance (all groups). The V5 ADI/Smart Port
// link can't keep up with back-to-back or concurrent led->update() calls from
// multiple strands/tasks, serializing them here and pacing each one gives
// the ADI LED driver breathing room so updates don't queue up and lag behind.
static pros::Mutex s_adiMutex;

namespace hitlib {

namespace {

uint32_t lerpColor(uint32_t bg, uint32_t fg, uint8_t level) {
    uint8_t br = (bg >> 16) & 0xFF, bgc = (bg >> 8) & 0xFF, bb = bg & 0xFF;
    uint8_t fr = (fg >> 16) & 0xFF, fgc = (fg >> 8) & 0xFF, fb = fg & 0xFF;
    uint8_t r = (uint8_t)(br + ((int)(fr - br) * level) / 255);
    uint8_t g = (uint8_t)(bgc + ((int)(fgc - bgc) * level) / 255);
    uint8_t b = (uint8_t)(bb + ((int)(fb - bb) * level) / 255);
    return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

// Standard NeoPixel-style integer hue wheel (S=V=255).
uint32_t wheel(uint8_t pos) {
    pos = 255 - pos;
    if (pos < 85) return ((uint32_t)(255 - pos * 3) << 16) | (uint32_t)(pos * 3);
    if (pos < 170) {
        pos -= 85;
        return ((uint32_t)(pos * 3) << 8) | (uint32_t)(255 - pos * 3);
    }
    pos -= 170;
    return ((uint32_t)(pos * 3) << 16) | (uint32_t)(255 - pos * 3);
}

} // namespace

// ============================================================================
// Construction / init
// ============================================================================

LedStrand::LedStrand(uint8_t adiPort_, uint8_t length_, uint32_t refreshMs_)
    : adiPort(adiPort_), smartPort(0), length(std::max<uint8_t>(1, std::min<uint8_t>(length_, MAX_LEDS))),
      refreshMs(refreshMs_) {
    buffer.assign(length, 0);
    overlayBuffer.assign(length, 0);
    spliceShowAnim.assign(length, true);
    splicePixelBg.assign(length, 0);
    splicePixelUseOverlay.assign(length, false);
    splicePixelRegionIdx.assign(length, -1);
}

LedStrand::LedStrand(uint8_t smartPort_, uint8_t adiPort_, uint8_t length_, uint32_t refreshMs_)
    : adiPort(adiPort_), smartPort(smartPort_),
      length(std::max<uint8_t>(1, std::min<uint8_t>(length_, MAX_LEDS))), refreshMs(refreshMs_) {
    buffer.assign(length, 0);
    overlayBuffer.assign(length, 0);
    spliceShowAnim.assign(length, true);
    splicePixelBg.assign(length, 0);
    splicePixelUseOverlay.assign(length, false);
    splicePixelRegionIdx.assign(length, -1);
}

void LedStrand::init() {
    if (led != nullptr) return;
    if (smartPort == 0) led = new pros::adi::Led(adiPort, length);
    else                led = new pros::adi::Led(pros::adi::ext_adi_port_pair_t{smartPort, adiPort}, length);
}

// ============================================================================
// tick() - called once per refresh interval by LedGroup's task.
// ============================================================================

void LedStrand::tick() {
    if (!led) return;
    mutex.take();

    uint32_t now = pros::millis();
    pruneExpired(now);

    if (pulseRunLen > 0)                    advancePulseBounce();
    else if (!bitscrollMaster.empty())      advanceBitscrollBounce();
    else if (animMode == AnimMode::TWINKLE) advanceTwinkle();
    else if (animMode == AnimMode::FLASH)   advanceFlash();
    else if (animMode == AnimMode::LEVEL)   advanceLevel();
    else if (animMode == AnimMode::SHIFT)   shiftBuffer();

    if (overlayAnimMode == AnimMode::SHIFT)      shiftOverlayBuffer();
    else if (overlayAnimMode == AnimMode::FLASH) advanceOverlayFlash();

    advanceSpliceAlternating(now);
    advanceSpliceRegions();

    int16_t effectiveIdx = computeEffectiveMode();
    bool modeChanged = (effectiveIdx != lastModeIdx);
    lastModeIdx = effectiveIdx;

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

    flushBuffer();

    mutex.give();
}

// ============================================================================
// Base animations
// ============================================================================

void LedStrand::off() { mutex.take(); setColorNL(0); mutex.give(); }

void LedStrand::setColor(uint32_t color) { mutex.take(); setColorNL(color); mutex.give(); }

void LedStrand::setColorNL(uint32_t color) {
    pulseRunLen = 0;
    bitscrollMaster.clear();
    buffer.assign(length, color);
    shiftStep = 0;
    shiftVariant = 0;
    animMode = AnimMode::STATIC;
}

void LedStrand::pulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor, bool invert,
                       bool bounce) {
    mutex.take();
    pulseNL(color, runLength, speed, bgColor, invert, bounce);
    mutex.give();
}

void LedStrand::pulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg, bool invert, bool bounce) {
    bitscrollMaster.clear();
    if (!bounce) {
        pulseRunLen = 0;
        buffer.assign(length, bg);
        uint8_t rl = std::min<uint8_t>(runLen, length);
        std::fill_n(buffer.begin(), rl, color);
        shiftStep = 0;
        uint8_t sp = (uint8_t)(speed % length);
        shiftVariant = invert ? (uint8_t)((length - sp) % length) : sp;
        animMode = AnimMode::SHIFT;
    } else {
        if (buffer.size() != length) buffer.assign(length, bg);
        pulseColor = color;
        pulseBg = bg;
        pulseRunLen = std::max<uint8_t>(1, std::min<uint8_t>(runLen, length));
        pulseSpeed = std::max<uint8_t>(speed, 1);
        pulseOffset = invert ? (int16_t)(length - pulseRunLen) : 0;
        pulseDir = invert ? -1 : 1;
        animMode = AnimMode::STATIC; // content is generated per-tick by advancePulseBounce()
    }
}

void LedStrand::advancePulseBounce() {
    pulseOffset = (int16_t)(pulseOffset + pulseDir * pulseSpeed);
    int16_t maxOffset = (int16_t)length - (int16_t)pulseRunLen;
    if (maxOffset < 0) maxOffset = 0;
    if (pulseOffset >= maxOffset) { pulseOffset = maxOffset; pulseDir = -1; }
    if (pulseOffset <= 0)         { pulseOffset = 0;          pulseDir = 1; }

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

void LedStrand::flow(uint32_t color1, uint32_t color2, uint8_t speed, bool invert, bool seamless) {
    mutex.take();
    flowNL(color1, color2, speed, invert, seamless);
    mutex.give();
}

void LedStrand::flowNL(uint32_t c1, uint32_t c2, uint8_t speed, bool invert, bool seamless) {
    pulseRunLen = 0;
    bitscrollMaster.clear();
    buffer = genGradient(c1, c2, length, seamless);
    shiftStep = 0;
    uint8_t sp = (uint8_t)(speed % length);
    shiftVariant = invert ? (uint8_t)((length - sp) % length) : sp;
    animMode = AnimMode::SHIFT;
}

void LedStrand::rainbow(uint8_t speed) {
    mutex.take();
    rainbowNL(speed);
    mutex.give();
}

void LedStrand::rainbowNL(uint8_t speed) {
    pulseRunLen = 0;
    bitscrollMaster.clear();
    buffer = genRainbow(length);
    shiftStep = 0;
    shiftVariant = speed;
    animMode = AnimMode::SHIFT;
}

void LedStrand::twinkle(const std::vector<uint32_t>& colors, uint8_t densityPct, uint8_t fadeStep,
                         uint32_t bgColor) {
    mutex.take();
    twinkleNL(colors, densityPct, fadeStep, bgColor);
    mutex.give();
}

void LedStrand::twinkleNL(const std::vector<uint32_t>& colors, uint8_t densityPct, uint8_t fadeStep,
                           uint32_t bgColor) {
    pulseRunLen = 0;
    bitscrollMaster.clear();
    twinklePalette = colors;
    twinkleLevel.assign(length, 0);
    twinkleTarget.assign(length, 0);
    twinkleColorIdx.assign(length, 0);
    twinkleHoldTicks.assign(length, 0);
    twinkleDensityPct = std::min<uint8_t>(densityPct, 100);
    twinkleFadeStep = std::max<uint8_t>(fadeStep, 1);
    twinkleBgColor = bgColor;
    if (buffer.size() != length) buffer.assign(length, bgColor);
    animMode = AnimMode::TWINKLE;

    static bool seeded = false;
    if (!seeded) { std::srand(pros::millis()); seeded = true; }
}

void LedStrand::advanceTwinkle() {
    constexpr uint8_t HOLD_TICKS = 8;

    uint8_t activeCount = 0;
    for (uint8_t i = 0; i < length; ++i) {
        if (twinkleLevel[i] > 0 || twinkleHoldTicks[i] > 0) activeCount++;
    }
    uint8_t targetCount = (uint8_t)(((uint16_t)length * twinkleDensityPct + 50) / 100);

    if (activeCount < targetCount && !twinklePalette.empty()) {
        // Reservoir-sample one idle pixel to spawn, at most one per tick.
        int chosen = -1;
        int idleSeen = 0;
        for (uint8_t i = 0; i < length; ++i) {
            if (twinkleLevel[i] == 0 && twinkleHoldTicks[i] == 0) {
                idleSeen++;
                if ((std::rand() % idleSeen) == 0) chosen = i;
            }
        }
        if (chosen >= 0) {
            twinkleColorIdx[chosen] = (uint8_t)(std::rand() % twinklePalette.size());
            twinkleTarget[chosen] = 255;
        }
    }

    for (uint8_t i = 0; i < length; ++i) {
        if (twinkleHoldTicks[i] > 0) {
            if (--twinkleHoldTicks[i] == 0) twinkleTarget[i] = 0;
        } else if (twinkleLevel[i] < twinkleTarget[i]) {
            uint16_t nl = (uint16_t)twinkleLevel[i] + twinkleFadeStep;
            twinkleLevel[i] = (nl > 255) ? (uint8_t)255 : (uint8_t)nl;
            if (twinkleLevel[i] >= twinkleTarget[i]) twinkleHoldTicks[i] = HOLD_TICKS;
        } else if (twinkleLevel[i] > twinkleTarget[i]) {
            int16_t nl = (int16_t)twinkleLevel[i] - twinkleFadeStep;
            twinkleLevel[i] = (nl < 0) ? (uint8_t)0 : (uint8_t)nl;
        }

        uint32_t fg = twinklePalette.empty() ? 0 : twinklePalette[twinkleColorIdx[i]];
        buffer[i] = lerpColor(twinkleBgColor, fg, twinkleLevel[i]);
    }
}

void LedStrand::bitscroll(const std::vector<BitScrollSegment>& segments, uint8_t speed, bool invert,
                           uint32_t bgColor, bool bounce, uint8_t spacing, bool repeating) {
    mutex.take();
    bitscrollNL(segments, speed, invert, bgColor, bounce, spacing, repeating);
    mutex.give();
}

void LedStrand::bitscrollNL(const std::vector<BitScrollSegment>& segments, uint8_t speed, bool invert,
                             uint32_t bgColor, bool bounce, uint8_t spacing, bool repeating) {
    pulseRunLen = 0;

    // Build one tile of the pattern. Capped at MAX_LEDS: a pattern wider than the
    // whole strip can't usefully tile, and the cap keeps downstream size math
    // (shiftVariant, bitscrollMaster) safely inside uint8_t range.
    std::vector<uint32_t> unit;
    // Size of `unit` up to the last segment pixel, i.e. excluding the trailing
    // run of `spacing`, which only exists to separate one tile from the next.
    // A single non-tiled copy of the pattern shouldn't carry it.
    size_t contentLen = 0;
    for (const auto& seg : segments) {
        for (uint8_t k = 0; k < seg.width && unit.size() < MAX_LEDS; ++k) unit.push_back(seg.color);
        contentLen = unit.size();
        if (unit.size() >= MAX_LEDS) break;
        for (uint8_t k = 0; k < spacing && unit.size() < MAX_LEDS; ++k) unit.push_back(bgColor);
        if (unit.size() >= MAX_LEDS) break;
    }
    if (unit.empty()) {
        unit.push_back(bgColor);
        contentLen = unit.size();
    }

    if (!bounce) {
        bitscrollMaster.clear();
        if (repeating) {
            size_t reps = (size_t)(length / unit.size()) + 2;
            buffer.clear();
            buffer.reserve(unit.size() * reps);
            for (size_t r = 0; r < reps; ++r) buffer.insert(buffer.end(), unit.begin(), unit.end());
        } else {
            buffer.assign(length, bgColor);
            size_t n = std::min(contentLen, (size_t)length);
            std::copy(unit.begin(), unit.begin() + n, buffer.begin());
        }
        shiftStep = 0;
        size_t bufSize = buffer.size();
        uint8_t sp = (uint8_t)(speed % bufSize);
        shiftVariant = invert ? (uint8_t)((bufSize - sp) % bufSize) : sp;
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
        bounceSpeed = std::max<uint8_t>(speed, 1);
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
    int16_t maxPos = (int16_t)bitscrollMaster.size() - (int16_t)length;
    if (maxPos < 0) maxPos = 0;
    bounceScrollPos = (int16_t)(bounceScrollPos + bounceScrollDir * bounceSpeed);
    if (bounceScrollPos >= maxPos) { bounceScrollPos = maxPos; bounceScrollDir = -1; }
    if (bounceScrollPos <= 0)      { bounceScrollPos = 0;       bounceScrollDir = 1; }
    fillBitscrollFromMaster();
}

void LedStrand::shiftBuffer() {
    size_t bufSize = buffer.size();
    if (bufSize == 0) return;
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
    musicAnchorMs    = pros::millis();
    musicPausedAt    = 0;
    levelValue       = sampleMusicNL(0);
    mutex.give();
}

void LedStrand::musicSeek(uint32_t positionMs) {
    mutex.take();
    // Move the anchor rather than a position counter, so a seek while playing
    // resumes from the new spot at real-time speed with no further bookkeeping.
    musicAnchorMs = pros::millis() - positionMs;
    musicPausedAt = positionMs;
    levelValue    = sampleMusicNL(positionMs);
    mutex.give();
}

void LedStrand::musicPause(bool paused) {
    mutex.take();
    uint32_t now = pros::millis();
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
    return musicPaused ? musicPausedAt : (pros::millis() - musicAnchorMs);
}

void LedStrand::setSensitivity(uint8_t pct) {
    mutex.take();
    musicSensitivity = pct;
    mutex.give();
}

void LedStrand::advanceLevel() {
    if (musicTrack) {
        levelValue = sampleMusicNL(musicPaused ? musicPausedAt : (pros::millis() - musicAnchorMs));
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
    // An unplugged device reports PROS_ERR_F (infinity). Holding the fill says
    // "no reading" far better than slamming the bar to full would.
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
// the two frames it falls between. That interpolation is what lets the export
// use a coarse frame rate (fewer bytes of flash) without the fill visibly
// stepping at the strand's faster refresh rate.
uint8_t LedStrand::sampleMusicNL(uint32_t positionMs) const {
    if (!musicTrack || musicTrack->samples == nullptr) return 0;
    const MusicTrack& t = *musicTrack;
    if (t.frameCount == 0 || t.frameMs == 0) return 0;

    uint32_t totalMs = (uint32_t)t.frameCount * (uint32_t)t.frameMs;
    if (positionMs >= totalMs) {
        if (!musicLoop) return 0; // song over, let the meter drain
        positionMs %= totalMs;
    }

    uint32_t idx  = positionMs / t.frameMs;
    uint32_t frac = positionMs % t.frameMs;
    int a = t.samples[idx];
    int b;
    if (idx + 1 < t.frameCount) b = t.samples[idx + 1];
    else                        b = musicLoop ? t.samples[0] : a;

    int raw    = a + (b - a) * (int)frac / (int)t.frameMs;
    int scaled = raw * (int)musicSensitivity / 100;
    if (scaled > 255) scaled = 255;
    if (scaled < 0)   scaled = 0;
    return (uint8_t)scaled;
}

// The color one meter pixel shows, given the fill cutoff for this frame:
// @p full whole lit pixels, and @p frac of the way into the next one. The
// cutoff is computed once per flush rather than per pixel, and the partial
// pixel is what keeps a 30-pixel strand from showing only 30 distinguishable
// levels.
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
    spliceLastToggleMs = pros::millis();
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
                    state.shiftSpeed = r.speed;
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
                    state.shiftSpeed = r.speed;
                    break;
                case SpliceRegionAnimKind::RAINBOW:
                    state.buffer = genRainbow(regionWidth);
                    state.shiftSpeed = r.speed;
                    break;
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
                    // An empty scale becomes the two-color fallback, which is
                    // what guarantees the runtime always has something to
                    // bracket between.
                    std::vector<GaugeStop> scale = r.stops;
                    if (scale.empty()) scale = {{r.emptyAt, r.color}, {r.fullAt, r.color2}};
                    state.gaugeStops.reserve(scale.size());
                    for (const GaugeStop& gs : scale) {
                        // wrap is false here whatever the gauge itself does: a
                        // stop's place on the scale is a clamp, not a lap.
                        state.gaugeStops.emplace_back(
                            mapLevelTo(gs.at, r.emptyAt, r.fullAt, false), gs.color);
                    }
                    std::sort(state.gaugeStops.begin(), state.gaugeStops.end());
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
    // The V5 reports motor temperature in coarse steps rather than as a
    // continuous reading, so without smoothing a segment jumps from one stop
    // color straight to the next. This is what makes it creep instead.
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

void LedStrand::clearSpliceMask() {
    mutex.take();
    spliceActive = false;
    mutex.give();
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
                // the color says "no reading" better than slamming the segment
                // to the top of its scale would.
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
        if (state.shiftSpeed == 0) continue;
        state.shiftStep = (int)((state.shiftStep + state.shiftSpeed) % (int)bufSize);
    }
}

// The color a gauge's scale gives for a 0-255 level. Stops are already sorted
// and already on that axis, so this is a bracket and (at most) one lerp.
uint32_t LedStrand::gaugeColorAt(const SpliceRegionState& state, uint8_t level) {
    const std::vector<std::pair<uint8_t, uint32_t>>& stops = state.gaugeStops;
    if (stops.empty()) return state.gaugeBg;
    // Past either end the scale holds, so it never has to cover a range the
    // gauge does not care about.
    if (level <= stops.front().first) return stops.front().second;
    if (level >= stops.back().first)  return stops.back().second;

    size_t i = 1;
    while (i < stops.size() && stops[i].first < level) ++i;
    const std::pair<uint8_t, uint32_t>& lo = stops[i - 1];
    const std::pair<uint8_t, uint32_t>& hi = stops[i];
    // STEP holds the lower stop until the higher one is actually reached, which
    // is the honest reading when the stops are thresholds something crosses
    // rather than points on a ramp.
    if (state.gaugeBlend == GaugeBlend::STEP) return lo.second;

    uint8_t span = (uint8_t)(hi.first - lo.first);
    if (span == 0) return hi.second;
    uint8_t t = (uint8_t)(((uint32_t)(level - lo.first) * 255u) / span);
    return lerpColor(lo.second, hi.second, t);
}

// Repaint one gauge region from its current level. Called every tick rather
// than only on a change: it is at most 64 writes, and it keeps the region
// correct after a setRegionLevel() from another task with no dirty flag to
// get wrong.
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

void LedStrand::overlayPulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor) {
    mutex.take();
    overlayPulseNL(color, runLength, speed, bgColor);
    mutex.give();
}

void LedStrand::overlayFlash(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bgColor) {
    mutex.take();
    overlayFlashNL(color, onMs, offMs, bgColor);
    mutex.give();
}

void LedStrand::overlayFlow(uint32_t color1, uint32_t color2, uint8_t speed, bool seamless) {
    mutex.take();
    overlayFlowNL(color1, color2, speed, seamless);
    mutex.give();
}

void LedStrand::overlayRainbow(uint8_t speed) {
    mutex.take();
    overlayRainbowNL(speed);
    mutex.give();
}

void LedStrand::overlaySetColorNL(uint32_t color) {
    overlayBuffer.assign(length, color);
    overlayAnimMode = AnimMode::STATIC;
    overlayShiftStep = 0;
    overlayShiftSpeed = 0;
}

void LedStrand::overlayPulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg) {
    overlayBuffer.assign(length, bg);
    uint8_t rl = std::min<uint8_t>(runLen, length);
    std::fill_n(overlayBuffer.begin(), rl, color);
    overlayShiftStep = 0;
    overlayShiftSpeed = speed;
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

void LedStrand::overlayFlowNL(uint32_t c1, uint32_t c2, uint8_t speed, bool seamless) {
    overlayBuffer = genGradient(c1, c2, length, seamless);
    overlayShiftStep = 0;
    overlayShiftSpeed = speed;
    overlayAnimMode = AnimMode::SHIFT;
}

void LedStrand::overlayRainbowNL(uint8_t speed) {
    overlayBuffer = genRainbow(length);
    overlayShiftStep = 0;
    overlayShiftSpeed = speed;
    overlayAnimMode = AnimMode::SHIFT;
}

void LedStrand::shiftOverlayBuffer() {
    size_t bufSize = overlayBuffer.size();
    if (bufSize == 0) return;
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
    uint32_t ticks = (ms + refreshMs / 2) / refreshMs;
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
    mutex.take();
    uint32_t now = pros::millis();
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

void LedStrand::pruneExpired(uint32_t now) {
    if (modeStack.empty()) return;
    modeStack.erase(std::remove_if(modeStack.begin(), modeStack.end(),
                                    [now](const ModeEntry& e) { return !e.persistent && now >= e.endMs; }),
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

void LedStrand::flushBuffer() {
    size_t bufSize = buffer.size();
    size_t overlayBufSize = overlayBuffer.size();

    // Meter fill cutoff, in whole pixels plus a fraction of the next one.
    // Constant across the frame, so it is worked out here rather than inside
    // the per-pixel loop.
    uint32_t litQ8     = (uint32_t)levelValue * (uint32_t)length;
    uint32_t levelFull = litQ8 / 255;
    uint8_t  levelFrac = (uint8_t)(litQ8 % 255);

    s_adiMutex.take();
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

        if (spliceActive && !spliceShowAnim[i]) {
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

        led->set_pixel(applyBrightness(color), i);
    }
    led->update();
    // Give the ADI LED driver some breathing room between updates.
    pros::delay(12);
    s_adiMutex.give();
}

// ============================================================================
// Static generators
// ============================================================================

std::vector<uint32_t> LedStrand::genGradient(uint32_t c1, uint32_t c2, uint8_t len, bool seamless) {
    std::vector<uint32_t> out(len);
    int r1 = (c1 >> 16) & 0xFF, g1 = (c1 >> 8) & 0xFF, b1 = c1 & 0xFF;
    int r2 = (c2 >> 16) & 0xFF, g2 = (c2 >> 8) & 0xFF, b2 = c2 & 0xFF;
    for (uint8_t i = 0; i < len; ++i) {
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
        out[i] = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
    }
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
