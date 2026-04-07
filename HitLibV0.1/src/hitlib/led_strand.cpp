#include "hitlib/led_strand.hpp"
#include "pros/rtos.hpp"
#include <algorithm>
#include <cstdlib>

static pros::Mutex s_adiMutex;

namespace hitlib {

// ================================================================
// Construction / init
// ================================================================

LedStrand::LedStrand(uint8_t adiPort, uint8_t length, uint32_t refreshMs)
    : adiPort(adiPort), length(std::min(length, MAX_LEDS)), refreshMs(refreshMs) {}

LedStrand::LedStrand(uint8_t smartPort, uint8_t adiPort, uint8_t length, uint32_t refreshMs)
    : adiPort(adiPort), smartPort(smartPort),
      length(std::min(length, MAX_LEDS)), refreshMs(refreshMs) {}

void LedStrand::init() {
    if (led) return;
    s_adiMutex.take(TIMEOUT_MAX);
    if (smartPort != 0) {
        pros::adi::ext_adi_port_pair_t pair = {smartPort, adiPort};
        led = new pros::adi::Led(pair, length);
    } else {
        led = new pros::adi::Led(adiPort, length);
    }
    buffer.assign(length, 0x000000);
    overlayBuffer.assign(length, 0x000000);
    pros::delay(50);
    s_adiMutex.give();
}

// ================================================================
// Per-strand refresh task
// ================================================================

void LedStrand::tick() {
    uint32_t now = pros::millis();

    if (activeProfile) {
        mutex.take(TIMEOUT_MAX);
        pruneExpired(now);
        int16_t effective = computeEffectiveMode();
        mutex.give();

        if (effective != lastModeIdx) {
            lastModeIdx = effective;
            if (effective >= 0)
                activeProfile->modes[effective].onActivate(*this);
        }

        if (lastModeIdx >= 0 && activeProfile->modes[lastModeIdx].onTick)
            activeProfile->modes[lastModeIdx].onTick(*this);
    }

    mutex.take(TIMEOUT_MAX);

    // Advance base animation.
    // When CENTER_SPREAD is active we also shift the base if it has a shift
    // animation — otherwise a shifting base (e.g. rainbow) would freeze once
    // it becomes the current layer.
    if (animMode == AnimMode::CENTER_SPREAD) {
        advanceCenterSpread();
        if (shiftStep != 0)
            shiftBuffer();
    } else if (animMode == AnimMode::TWINKLE) {
        advanceTwinkle();
    } else if (animMode == AnimMode::SHIFT && shiftStep != 0) {
        shiftBuffer();
    }

    // Advance overlay animation independently
    if (overlayAnimMode == AnimMode::SHIFT && overlayShiftStep != 0)
        shiftOverlayBuffer();

    flushBuffer();
    mutex.give();
}

// ================================================================
// Base animation API (public, locked)
// ================================================================

void LedStrand::off()                                                    { mutex.take(TIMEOUT_MAX); setColorNL(0x000000);         mutex.give(); }
void LedStrand::setColor(uint32_t c)                                     { mutex.take(TIMEOUT_MAX); setColorNL(c);               mutex.give(); }
void LedStrand::pulse(uint32_t c, uint8_t r, uint8_t sp, uint32_t bg, bool invert) { mutex.take(TIMEOUT_MAX); pulseNL(c, r, sp, bg, invert); mutex.give(); }
void LedStrand::flash(uint32_t c, uint8_t sp, uint32_t bg)              { mutex.take(TIMEOUT_MAX); flashNL(c, sp, bg);          mutex.give(); }
void LedStrand::flow(uint32_t c1, uint32_t c2, uint8_t sp, bool invert) { mutex.take(TIMEOUT_MAX); flowNL(c1, c2, sp, invert);  mutex.give(); }
void LedStrand::rainbow(uint8_t sp)                                      { mutex.take(TIMEOUT_MAX); rainbowNL(sp);               mutex.give(); }
void LedStrand::twinkle(const std::vector<uint32_t>& colors, uint8_t densityPct,
                        uint8_t fadeStep, uint32_t bgColor) {
    mutex.take(TIMEOUT_MAX);
    twinkleNL(colors, densityPct, fadeStep, bgColor);
    mutex.give();
}
void LedStrand::bitscroll(const std::vector<BitScrollSegment>& segments, uint8_t speed,
                          bool invert, uint32_t bgColor) {
    mutex.take(TIMEOUT_MAX);
    bitscrollNL(segments, speed, invert, bgColor);
    mutex.give();
}
void LedStrand::setBrightness(uint8_t pct)                               { brightnessPct = (pct > 100) ? 100 : pct; }

// ================================================================
// Base animation implementations (no lock)
// ================================================================

void LedStrand::setColorNL(uint32_t color) {
    animMode  = AnimMode::STATIC;
    shiftStep = 0;
    buffer.assign(length, color);
}

void LedStrand::pulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg, bool invert) {
    animMode  = AnimMode::SHIFT;
    shiftStep = invert ? -(int)speed : (int)speed;
    buffer.assign(length, bg);
    uint8_t safe = std::min(runLen, length);
    for (uint8_t i = 0; i < safe; i++) buffer[i] = color;
}

void LedStrand::flashNL(uint32_t color, uint8_t speed, uint32_t bg) {
    animMode  = AnimMode::SHIFT;
    shiftStep = length;
    buffer.clear();
    buffer.insert(buffer.end(), length, color);
    buffer.insert(buffer.end(), (size_t)length * speed, bg);
}

void LedStrand::flowNL(uint32_t c1, uint32_t c2, uint8_t speed, bool invert) {
    animMode  = AnimMode::SHIFT;
    shiftStep = invert ? -(int)speed : (int)speed;
    buffer    = genGradient(c1, c2, length);
}

void LedStrand::rainbowNL(uint8_t speed) {
    animMode  = AnimMode::SHIFT;
    shiftStep = speed;
    buffer    = genRainbow(length);
}

void LedStrand::twinkleNL(const std::vector<uint32_t>& colors, uint8_t densityPct,
                          uint8_t fadeStep, uint32_t bgColor) {
    animMode = AnimMode::TWINKLE;
    shiftStep = 0;
    twinklePalette = colors;
    if (twinklePalette.empty())
        twinklePalette.push_back(0xFFFFFF);

    twinkleDensityPct = (densityPct > 100) ? 100 : densityPct;
    twinkleFadeStep   = (fadeStep == 0) ? 1 : fadeStep;
    twinkleBgColor    = bgColor;

    buffer.assign(length, twinkleBgColor);
    twinkleLevel.assign(length, 0);
    twinkleTarget.assign(length, 0);
    twinkleColorIdx.assign(length, 0);
    twinkleHoldTicks.assign(length, 0);
}

void LedStrand::bitscrollNL(const std::vector<BitScrollSegment>& segments, uint8_t speed,
                            bool invert, uint32_t bgColor) {
    animMode = AnimMode::SHIFT;
    shiftStep = invert ? -(int)speed : (int)speed;
    buffer.clear();

    if (segments.empty()) {
        buffer.assign(length, bgColor);
        return;
    }

    // Expand the segments repeatedly until the strand is fully covered.
    // This keeps the scrolling pattern continuous instead of "one pass then bg".
    size_t segIdx = 0;
    uint8_t remainingInSeg = (segments[0].width == 0) ? 1 : segments[0].width;

    buffer.reserve(length);
    for (uint8_t i = 0; i < length; i++) {
        const auto& seg = segments[segIdx];
        buffer.push_back(seg.color);

        if (--remainingInSeg == 0) {
            segIdx = (segIdx + 1) % segments.size();
            remainingInSeg = (segments[segIdx].width == 0) ? 1 : segments[segIdx].width;
        }
    }
}

// ================================================================
// Overlay animation API (public, locked)
// ================================================================

void LedStrand::overlaySetColor(uint32_t c)                                   { mutex.take(TIMEOUT_MAX); overlaySetColorNL(c);         mutex.give(); }
void LedStrand::overlayPulse(uint32_t c, uint8_t r, uint8_t sp, uint32_t bg) { mutex.take(TIMEOUT_MAX); overlayPulseNL(c, r, sp, bg); mutex.give(); }
void LedStrand::overlayFlash(uint32_t c, uint8_t sp, uint32_t bg)            { mutex.take(TIMEOUT_MAX); overlayFlashNL(c, sp, bg);    mutex.give(); }
void LedStrand::overlayFlow(uint32_t c1, uint32_t c2, uint8_t sp)            { mutex.take(TIMEOUT_MAX); overlayFlowNL(c1, c2, sp);    mutex.give(); }
void LedStrand::overlayRainbow(uint8_t sp)                                    { mutex.take(TIMEOUT_MAX); overlayRainbowNL(sp);         mutex.give(); }

// ================================================================
// Overlay animation implementations (no lock)
// ================================================================

void LedStrand::overlaySetColorNL(uint32_t color) {
    overlayAnimMode  = AnimMode::STATIC;
    overlayShiftStep = 0;
    overlayBuffer.assign(length, color);
}

void LedStrand::overlayPulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg) {
    overlayAnimMode  = AnimMode::SHIFT;
    overlayShiftStep = speed;
    overlayBuffer.assign(length, bg);
    uint8_t safe = std::min(runLen, length);
    for (uint8_t i = 0; i < safe; i++) overlayBuffer[i] = color;
}

void LedStrand::overlayFlashNL(uint32_t color, uint8_t speed, uint32_t bg) {
    overlayAnimMode  = AnimMode::SHIFT;
    overlayShiftStep = length;
    overlayBuffer.clear();
    overlayBuffer.insert(overlayBuffer.end(), length, color);
    overlayBuffer.insert(overlayBuffer.end(), (size_t)length * speed, bg);
}

void LedStrand::overlayFlowNL(uint32_t c1, uint32_t c2, uint8_t speed) {
    overlayAnimMode  = AnimMode::SHIFT;
    overlayShiftStep = speed;
    overlayBuffer    = genGradient(c1, c2, length);
}

void LedStrand::overlayRainbowNL(uint8_t speed) {
    overlayAnimMode  = AnimMode::SHIFT;
    overlayShiftStep = speed;
    overlayBuffer    = genRainbow(length);
}

void LedStrand::shiftOverlayBuffer() {
    if (overlayBuffer.empty() || overlayShiftStep == 0) return;
    size_t step = (size_t)std::abs(overlayShiftStep) % overlayBuffer.size();
    if (!step) return;
    if (overlayShiftStep > 0)
        std::rotate(overlayBuffer.rbegin(), overlayBuffer.rbegin() + (ptrdiff_t)step, overlayBuffer.rend());
    else
        std::rotate(overlayBuffer.begin(), overlayBuffer.begin() + (ptrdiff_t)step, overlayBuffer.end());
}

// ================================================================
// centerSpread
// ================================================================

void LedStrand::centerSpread(uint8_t tickInterval, bool invert) {
    mutex.take(TIMEOUT_MAX);
    animMode           = AnimMode::CENTER_SPREAD;
    spreadPos          = 0;
    spreadTickCounter  = 0;
    spreadTickInterval = (tickInterval < 1) ? 1 : tickInterval;
    spreadLayers.clear();
    spreadLayerIdx     = 0;
    spreadMask.assign(length, false);
    spreadInvert       = invert;
    spreadBounce       = false;
    spreadReturning    = false;
    mutex.give();
}

void LedStrand::centerSpreadStacked(const std::vector<AnimSetupFn>& layers,
                                    uint8_t tickInterval, bool invert) {
    if (layers.size() < 2) return;

    // Call layer fns outside the lock — they take it themselves
    layers[0](*this);

    mutex.take(TIMEOUT_MAX);
    std::vector<uint32_t> savedBuffer = buffer;
    AnimMode              savedMode   = animMode;
    int                   savedStep   = shiftStep;
    mutex.give();

    layers[1](*this);

    mutex.take(TIMEOUT_MAX);
    overlayBuffer    = buffer;
    overlayAnimMode  = animMode;
    overlayShiftStep = shiftStep;

    buffer    = savedBuffer;
    animMode  = AnimMode::CENTER_SPREAD;
    shiftStep = savedStep;

    spreadLayers       = layers;
    spreadLayerIdx     = 0;
    spreadPos          = 0;
    spreadTickCounter  = 0;
    spreadTickInterval = (tickInterval < 1) ? 1 : tickInterval;
    spreadMask.assign(length, false);
    spreadInvert       = invert;
    spreadBounce       = false;
    spreadReturning    = false;
    mutex.give();
}

void LedStrand::centerSpreadBounce(uint8_t tickInterval, bool invert) {
    mutex.take(TIMEOUT_MAX);
    animMode           = AnimMode::CENTER_SPREAD;
    spreadPos          = 0;
    spreadTickCounter  = 0;
    spreadTickInterval = (tickInterval < 1) ? 1 : tickInterval;
    spreadLayers.clear();
    spreadLayerIdx     = 0;
    spreadMask.assign(length, false);
    spreadInvert       = invert;
    spreadBounce       = true;
    spreadReturning    = false;
    mutex.give();
}

void LedStrand::centerSpreadBounceStacked(const std::vector<AnimSetupFn>& layers,
                                          uint8_t tickInterval, bool invert) {
    if (layers.size() < 2) return;

    // Call layer fns outside the lock — they take it themselves
    layers[0](*this);

    mutex.take(TIMEOUT_MAX);
    std::vector<uint32_t> savedBuffer = buffer;
    AnimMode              savedMode   = animMode;
    int                   savedStep   = shiftStep;
    mutex.give();

    layers[1](*this);

    mutex.take(TIMEOUT_MAX);
    overlayBuffer    = buffer;
    overlayAnimMode  = animMode;
    overlayShiftStep = shiftStep;

    buffer    = savedBuffer;
    animMode  = AnimMode::CENTER_SPREAD;
    shiftStep = savedStep;

    spreadLayers       = layers;
    spreadLayerIdx     = 0;
    spreadPos          = 0;
    spreadTickCounter  = 0;
    spreadTickInterval = (tickInterval < 1) ? 1 : tickInterval;
    spreadMask.assign(length, false);
    spreadInvert       = invert;
    spreadBounce       = true;
    spreadReturning    = false;
    mutex.give();
}

// ================================================================
// doLayerSwap — shared by spread and bounce on completion (no lock)
// ================================================================

void LedStrand::doLayerSwap() {
    // Promote overlay → base, then set up the next overlay layer
    std::swap(buffer, overlayBuffer);
    std::swap(animMode, overlayAnimMode);
    std::swap(shiftStep, overlayShiftStep);
    animMode = AnimMode::CENTER_SPREAD;

    if (!spreadLayers.empty()) {
        spreadLayerIdx = (spreadLayerIdx + 1) % (uint8_t)spreadLayers.size();
        uint8_t nextIdx = (spreadLayerIdx + 1) % (uint8_t)spreadLayers.size();

        std::vector<uint32_t> savedBase     = buffer;
        AnimMode              savedAnimMode  = animMode;
        int                   savedShiftStep = shiftStep;

        // Release lock — setup fn takes it itself
        mutex.give();
        spreadLayers[nextIdx](*this);
        mutex.take(TIMEOUT_MAX);

        // Capture what the fn wrote as the new overlay
        overlayBuffer    = buffer;
        overlayAnimMode  = animMode;
        overlayShiftStep = shiftStep;

        // Restore the base
        buffer    = savedBase;
        animMode  = AnimMode::CENTER_SPREAD;
        shiftStep = savedShiftStep;
    }
}

// ================================================================
// advanceCenterSpread (no lock — called from tick() under mutex)
// ================================================================

void LedStrand::advanceCenterSpread() {
    if (++spreadTickCounter < spreadTickInterval) return;
    spreadTickCounter = 0;

    int center    = length / 2;
    int maxSpread = std::max(center, (int)length - 1 - center);

    // ---- Advance position ----
    if (spreadBounce && spreadReturning) {
        if (spreadPos > 0) --spreadPos;
        if (spreadPos == 0) {
            // Fully contracted — commit the layer swap and restart
            spreadReturning = false;
            spreadMask.assign(length, false);
            doLayerSwap();
            return;
        }
    } else {
        ++spreadPos;
    }

    // ---- Compute mask ----
    for (int i = 0; i < (int)length; i++) {
        if (!spreadInvert)
            // Normal: center → edges — reveal where distance ≤ spreadPos
            spreadMask[i] = (std::abs(i - center) <= (int)spreadPos);
        else
            // Inverted: edges → center — reveal where distance ≥ (maxSpread − spreadPos)
            spreadMask[i] = (std::abs(i - center) >= (maxSpread - (int)spreadPos));
    }

    // ---- Phase transition ----
    if (!spreadBounce) {
        // Standard: swap as soon as fully expanded
        if ((int)spreadPos > maxSpread) {
            spreadPos = 0;
            spreadMask.assign(length, false);
            doLayerSwap();
        }
    } else if (!spreadReturning && (int)spreadPos >= maxSpread) {
        // Bounce: begin contracting instead of swapping immediately
        spreadReturning = true;
    }
}

void LedStrand::advanceTwinkle() {
    if (twinkleLevel.size() != length || twinkleTarget.size() != length ||
        twinkleColorIdx.size() != length || twinkleHoldTicks.size() != length ||
        twinklePalette.empty()) {
        return;
    }

    // Compute current lit cap and count.
    uint8_t maxLit = (uint8_t)((uint16_t)length * twinkleDensityPct / 100);
    uint8_t currentLit = 0;
    for (uint8_t i = 0; i < length; i++) {
        if (twinkleLevel[i] > 0 || twinkleTarget[i] > 0)
            ++currentLit;
    }

    // Step 1: advance existing sparkles (fade in/out, and hold at peak)
    for (uint8_t i = 0; i < length; i++) {
        uint8_t lvl  = twinkleLevel[i];
        uint8_t tgt  = twinkleTarget[i];
        uint8_t step = twinkleFadeStep;

        if (lvl < tgt) {
            int next = (int)lvl + (int)step;
            lvl = (uint8_t)((next > (int)tgt) ? tgt : next);
        } else if (lvl > tgt) {
            int next = (int)lvl - (int)step;
            lvl = (uint8_t)((next < (int)tgt) ? tgt : next);
        } else if (lvl == 255) {
            // Hold at peak for a few ticks to break synchronization.
            if (twinkleHoldTicks[i] > 0) {
                --twinkleHoldTicks[i];
            } else {
                twinkleTarget[i] = 0; // begin fade-out
            }
        }

        twinkleLevel[i] = lvl;

        if (lvl == 0) {
            buffer[i] = twinkleBgColor;
        } else {
            uint32_t baseColor = twinklePalette[twinkleColorIdx[i] % twinklePalette.size()];
            uint32_t r = (((baseColor >> 16) & 0xFF) * lvl) / 255;
            uint32_t g = (((baseColor >> 8) & 0xFF) * lvl) / 255;
            uint32_t b = (((baseColor >> 0) & 0xFF) * lvl) / 255;
            buffer[i] = (r << 16) | (g << 8) | b;
        }
    }

    // Step 2: spawn a few new sparkles at random dark pixels (stagger starts).
    if (maxLit == 0 || currentLit >= maxLit) return;

    // Strongly stagger new sparkles: at most one spawn per tick.
    uint8_t spawnLimit = 1;
    uint8_t spawned = 0;

    // Try a bounded number of random picks to find dark pixels.
    uint16_t tries = (uint16_t)length * 2;
    while (spawned < spawnLimit && currentLit < maxLit && tries--) {
        uint8_t idx = (uint8_t)(std::rand() % length);
        if (twinkleLevel[idx] != 0 || twinkleTarget[idx] != 0) continue;

        // Use densityPct as "sparkle activity" probability; cap still enforces darkness.
        if ((std::rand() % 100) >= twinkleDensityPct) continue;

        twinkleColorIdx[idx]  = (uint8_t)(std::rand() % twinklePalette.size());
        twinkleHoldTicks[idx] = (uint8_t)(std::rand() % 6); // 0-5 ticks hold

        // Fully randomize starting brightness to maximize phase offset.
        uint8_t startLvl = (uint8_t)(std::rand() % 256);
        twinkleLevel[idx]  = startLvl;
        twinkleTarget[idx] = 255;

        ++spawned;
        ++currentLit;
    }
}

// ================================================================
// Composite
// ================================================================

uint32_t LedStrand::composite(uint32_t base, uint32_t overlay, bool useOverlay) const {
    return useOverlay ? overlay : base;
}

// ================================================================
// Profile following
// ================================================================

void LedStrand::attachProfile(const Profile* profile) {
    mutex.take(TIMEOUT_MAX);
    activeProfile = profile;
    modeStack.clear();
    lastModeIdx = -1;
    mutex.give();
}

void LedStrand::detachProfile() {
    mutex.take(TIMEOUT_MAX);
    activeProfile = nullptr;
    modeStack.clear();
    lastModeIdx = -1;
    mutex.give();
    off();
}

void LedStrand::activateMode(uint8_t modeIdx) {
    mutex.take(TIMEOUT_MAX);
    for (auto& e : modeStack) {
        if (e.modeIdx == modeIdx) { e.persistent = true; e.endMs = 0; mutex.give(); return; }
    }
    modeStack.push_back({modeIdx, true, 0});
    mutex.give();
}

void LedStrand::activateModeTimed(uint8_t modeIdx, uint32_t durationMs) {
    uint32_t endMs = pros::millis() + durationMs;
    mutex.take(TIMEOUT_MAX);
    for (auto& e : modeStack) {
        if (e.modeIdx == modeIdx && !e.persistent) {
            if (endMs > e.endMs) e.endMs = endMs;
            mutex.give(); return;
        }
    }
    modeStack.push_back({modeIdx, false, endMs});
    mutex.give();
}

void LedStrand::deactivateMode(uint8_t modeIdx) {
    mutex.take(TIMEOUT_MAX);
    modeStack.erase(
        std::remove_if(modeStack.begin(), modeStack.end(),
            [modeIdx](const ModeEntry& e) { return e.modeIdx == modeIdx; }),
        modeStack.end());
    mutex.give();
}

// ================================================================
// Internal profile helpers (no lock)
// ================================================================

int16_t LedStrand::computeEffectiveMode() const {
    int16_t bestIdx  = -1;
    uint8_t bestPrio = 0;
    for (const auto& e : modeStack) {
        if (e.modeIdx >= activeProfile->modeCount) continue;
        uint8_t prio = activeProfile->modes[e.modeIdx].priority;
        if (bestIdx < 0 || prio > bestPrio) { bestPrio = prio; bestIdx = (int16_t)e.modeIdx; }
    }
    return bestIdx;
}

void LedStrand::pruneExpired(uint32_t now) {
    modeStack.erase(
        std::remove_if(modeStack.begin(), modeStack.end(),
            [now](const ModeEntry& e) { return !e.persistent && now >= e.endMs; }),
        modeStack.end());
}

// ================================================================
// Buffer helpers (no lock)
// ================================================================

void LedStrand::shiftBuffer() {
    if (buffer.empty() || shiftStep == 0) return;
    size_t step = (size_t)std::abs(shiftStep) % buffer.size();
    if (!step) return;
    if (shiftStep > 0)
        std::rotate(buffer.rbegin(), buffer.rbegin() + (ptrdiff_t)step, buffer.rend());
    else
        std::rotate(buffer.begin(), buffer.begin() + (ptrdiff_t)step, buffer.end());
}

void LedStrand::flushBuffer() {
    if (!led || buffer.empty()) return;
    bool   hasMask   = (animMode == AnimMode::CENTER_SPREAD && !spreadMask.empty());
    size_t baseSz    = buffer.size();
    size_t overlaySz = overlayBuffer.size();

    s_adiMutex.take(TIMEOUT_MAX);
    for (uint8_t i = 0; i < length; i++) {
        uint32_t px;
        if (hasMask && overlaySz > 0)
            px = composite(buffer[i % baseSz], overlayBuffer[i % overlaySz], spreadMask[i]);
        else
            px = buffer[i % baseSz];
        led->set_pixel(applyBrightness(px), i);
    }
    led->update();
    // Give the ADI LED driver some breathing room between updates.
    pros::delay(12);
    s_adiMutex.give();
}

uint32_t LedStrand::applyBrightness(uint32_t c) const {
    if (brightnessPct == 100) return c;
    uint32_t r = ((c >> 16) & 0xFF) * brightnessPct / 100;
    uint32_t g = ((c >>  8) & 0xFF) * brightnessPct / 100;
    uint32_t b = ((c >>  0) & 0xFF) * brightnessPct / 100;
    return (r << 16) | (g << 8) | b;
}

// ================================================================
// Generators
// ================================================================

std::vector<uint32_t> LedStrand::genGradient(uint32_t c1, uint32_t c2, uint8_t len) {
    std::vector<uint32_t> out;
    out.reserve(len);
    int r1 = (c1 >> 16) & 0xFF, g1 = (c1 >> 8) & 0xFF, b1 = c1 & 0xFF;
    int r2 = (c2 >> 16) & 0xFF, g2 = (c2 >> 8) & 0xFF, b2 = c2 & 0xFF;
    for (uint8_t i = 0; i < len; i++) {
        float t = (len > 1) ? (float)i / (float)(len - 1) : 0.0f;
        out.push_back(
            ((uint32_t)((int)(r1 + (float)(r2 - r1) * t)) << 16) |
            ((uint32_t)((int)(g1 + (float)(g2 - g1) * t)) <<  8) |
            ((uint32_t)((int)(b1 + (float)(b2 - b1) * t)))
        );
    }
    return out;
}

std::vector<uint32_t> LedStrand::genRainbow(uint8_t len) {
    std::vector<uint32_t> out;
    out.reserve(len);
    for (uint8_t i = 0; i < len; i++) {
        float hue  = (float)i / (float)len * 360.0f;
        int   sect = (int)(hue / 60.0f);
        float frac = hue / 60.0f - (float)sect;
        float r, g, b;
        switch (sect) {
            case 0:  r = 1.0f;       g = frac;       b = 0.0f;       break;
            case 1:  r = 1.0f-frac;  g = 1.0f;       b = 0.0f;       break;
            case 2:  r = 0.0f;       g = 1.0f;       b = frac;       break;
            case 3:  r = 0.0f;       g = 1.0f-frac;  b = 1.0f;       break;
            case 4:  r = frac;       g = 0.0f;       b = 1.0f;       break;
            default: r = 1.0f;       g = 0.0f;       b = 1.0f-frac;  break;
        }
        out.push_back(((uint32_t)(r*255) << 16) | ((uint32_t)(g*255) << 8) | (uint32_t)(b*255));
    }
    return out;
}

} // namespace hitlib