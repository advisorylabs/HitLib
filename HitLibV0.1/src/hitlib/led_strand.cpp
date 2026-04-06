#include "hitlib/led_strand.hpp"
#include "pros/rtos.hpp"
#include <algorithm>

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

    // Advance base animation
    if (animMode == AnimMode::SHIFT && shiftStep != 0)
        shiftBuffer();
    else if (animMode == AnimMode::CENTER_SPREAD)
        advanceCenterSpread();

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
void LedStrand::pulse(uint32_t c, uint8_t r, uint8_t sp, uint32_t bg)   { mutex.take(TIMEOUT_MAX); pulseNL(c, r, sp, bg);       mutex.give(); }
void LedStrand::flash(uint32_t c, uint8_t sp, uint32_t bg)              { mutex.take(TIMEOUT_MAX); flashNL(c, sp, bg);          mutex.give(); }
void LedStrand::flow(uint32_t c1, uint32_t c2, uint8_t sp)              { mutex.take(TIMEOUT_MAX); flowNL(c1, c2, sp);          mutex.give(); }
void LedStrand::rainbow(uint8_t sp)                                      { mutex.take(TIMEOUT_MAX); rainbowNL(sp);               mutex.give(); }
void LedStrand::setBrightness(uint8_t pct)                               { brightnessPct = (pct > 100) ? 100 : pct; }

// ================================================================
// Base animation implementations (no lock)
// ================================================================

void LedStrand::setColorNL(uint32_t color) {
    animMode  = AnimMode::STATIC;
    shiftStep = 0;
    buffer.assign(length, color);
}

void LedStrand::pulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg) {
    animMode  = AnimMode::SHIFT;
    shiftStep = speed;
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

void LedStrand::flowNL(uint32_t c1, uint32_t c2, uint8_t speed) {
    animMode  = AnimMode::SHIFT;
    shiftStep = speed;
    buffer    = genGradient(c1, c2, length);
}

void LedStrand::rainbowNL(uint8_t speed) {
    animMode  = AnimMode::SHIFT;
    shiftStep = speed;
    buffer    = genRainbow(length);
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
    if (overlayBuffer.empty() || overlayShiftStep <= 0) return;
    size_t step = (size_t)overlayShiftStep % overlayBuffer.size();
    if (step) std::rotate(overlayBuffer.rbegin(), overlayBuffer.rbegin() + (ptrdiff_t)step, overlayBuffer.rend());
}

// ================================================================
// centerSpread
// ================================================================

void LedStrand::centerSpread(uint8_t tickInterval) {
    mutex.take(TIMEOUT_MAX);
    animMode           = AnimMode::CENTER_SPREAD;
    spreadPos          = 0;
    spreadTickCounter  = 0;
    spreadTickInterval = (tickInterval < 1) ? 1 : tickInterval;
    spreadLayers.clear();
    spreadLayerIdx     = 0;
    spreadMask.assign(length, false);
    mutex.give();
}

void LedStrand::centerSpreadStacked(const std::vector<AnimSetupFn>& layers, uint8_t tickInterval) {
    if (layers.size() < 2) return;
    mutex.take(TIMEOUT_MAX);
    animMode           = AnimMode::CENTER_SPREAD;
    spreadLayers       = layers;
    spreadLayerIdx     = 0;
    spreadPos          = 0;
    spreadTickCounter  = 0;
    spreadTickInterval = (tickInterval < 1) ? 1 : tickInterval;
    spreadMask.assign(length, false);

    // Base buffer = layer[0], overlay buffer = layer[1]
    spreadLayers[0](*this);
    overlayBuffer = buffer;              // capture what layer[0] wrote
    spreadLayers[1](*this);              // layer[1] writes into buffer
    std::swap(buffer, overlayBuffer);    // buffer = layer[0] (base), overlayBuffer = layer[1] (spreading)

    mutex.give();
}

void LedStrand::advanceCenterSpread() {
    // Throttle: only step every spreadTickInterval ticks
    if (++spreadTickCounter < spreadTickInterval) return;
    spreadTickCounter = 0;

    int center    = length / 2;
    int maxSpread = std::max(center, (int)length - 1 - center);

    // Update mask: pixels within spreadPos of center show overlay
    for (int i = 0; i < (int)length; i++)
        spreadMask[i] = (std::abs(i - center) <= (int)spreadPos);

    spreadPos++;

    // Spread complete — overlay now fully covers the strip
    if ((int)spreadPos > maxSpread) {
        spreadPos = 0;
        spreadMask.assign(length, false);

        // Promote overlay to base
        std::swap(buffer, overlayBuffer);
        overlayAnimMode  = overlayAnimMode; // overlay keeps its own animation state
        overlayShiftStep = overlayShiftStep;

        // If stacked, set up the next layer into overlayBuffer
        if (!spreadLayers.empty()) {
            spreadLayerIdx = (spreadLayerIdx + 1) % (uint8_t)spreadLayers.size();
            uint8_t nextIdx = (spreadLayerIdx + 1) % (uint8_t)spreadLayers.size();

            // Save base buffer, run next setup fn, capture result into overlayBuffer, restore base
            std::vector<uint32_t> savedBase = buffer;
            AnimMode              savedMode = animMode;
            int                   savedStep = shiftStep;

            spreadLayers[nextIdx](*this);
            overlayBuffer    = buffer;
            overlayAnimMode  = animMode;
            overlayShiftStep = shiftStep;

            buffer    = savedBase;
            animMode  = savedMode;
            shiftStep = savedStep;
        }
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
    if (buffer.empty() || shiftStep <= 0) return;
    size_t step = (size_t)shiftStep % buffer.size();
    if (step) std::rotate(buffer.rbegin(), buffer.rbegin() + (ptrdiff_t)step, buffer.rend());
}

void LedStrand::flushBuffer() {
    if (!led || buffer.empty()) return;
    bool hasMask = (animMode == AnimMode::CENTER_SPREAD && !spreadMask.empty());
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
    pros::delay(10);
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