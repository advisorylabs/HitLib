#include "hitlib/led_group.hpp"

#include <algorithm>

namespace hitlib {

// Lock order, everywhere: this group's mutex, then a strand's, then the
// shared ADI one inside LedStrand. Never the other way round.

void LedGroup::add(LedStrand* strand) {
    mutex.take();
    if (std::find(strands.begin(), strands.end(), strand) != strands.end()) {
        mutex.give();   // already here: a second copy would tick it twice a cycle
        return;
    }
    // Added after init(): bring it up the way init() would have, or it never
    // gets its hardware and stays dark.
    if (initialized) {
        strand->init();
        strand->setRefreshMs(refreshMs);
    }
    strands.push_back(strand);
    mutex.give();
}

void LedGroup::init(uint32_t refreshMsArg) {
    mutex.take();
    // One task ticks every strand at one rate, so that rate is the interval
    // each of them counts its durations in. 0 takes the first strand's own.
    if (refreshMsArg != 0) {
        refreshMs = refreshMsArg;
    } else if (!strands.empty() && strands[0]->refreshMs != 0) {
        refreshMs = strands[0]->refreshMs;
    }
    for (LedStrand* s : strands) {
        s->init();
        s->setRefreshMs(refreshMs);
    }
    initialized = true;
    mutex.give();
}

// Starts even with no strands yet, so ones add()ed later are ticked.
void LedGroup::start() {
    mutex.take();
    bool first = !started;
    started    = true;
    mutex.give();
    if (first) platform::startTask(&LedGroup::taskEntry, this, "LedGroup");
}

void LedGroup::taskEntry(void* group) {
    static_cast<LedGroup*>(group)->groupTask();
}

void LedGroup::groupTask() {
    std::vector<LedStrand*> ticking;   // this task's own copy of `strands`
    uint32_t prevTime = platform::millis();
    while (true) {
        // Copied under the lock and ticked without it, so an add() from
        // another task never has to wait out the strands' paced ADI writes,
        // and can't reallocate the list out from under this loop. The copy
        // reuses its storage once it is big enough.
        mutex.take();
        ticking         = strands;
        uint32_t period = refreshMs;
        mutex.give();

        for (LedStrand* s : ticking) s->tick();
        platform::delayUntil(prevTime, period);

        // A cycle that ran a whole period late - the strands' 12 ms ADI slots
        // add up, and a higher-priority task can hold this one off - leaves
        // prevTime behind, and delayUntil() would then return at once until
        // it caught up: a burst of ticks back to back that fast-forwards
        // every animation counted in ticks. Drop the missed time instead.
        uint32_t now = platform::millis();
        if ((int32_t)(now - prevTime) >= (int32_t)period) prevTime = now;
    }
}

LedStrand* LedGroup::operator[](size_t i) {
    mutex.take();
    LedStrand* s = strands[i];
    mutex.give();
    return s;
}

size_t LedGroup::size() const {
    mutex.take();
    size_t n = strands.size();
    mutex.give();
    return n;
}

// ============================================================================
// Fan-out wrappers, forward to every strand in the group.
//
// forEach() holds the group's mutex across the loop, so a strand add()ed
// meanwhile is either in or out, never half-way. That costs a broadcast
// nothing: a strand setter only waits for the strand's own lock, which tick()
// gives back before its frame goes out to the ADI.
// ============================================================================

void LedGroup::off() {
    forEach([&](LedStrand& s) { s.off(); });
}

void LedGroup::setColor(uint32_t color) {
    forEach([&](LedStrand& s) { s.setColor(color); });
}

void LedGroup::pulse(uint32_t color, uint8_t runLength, LedStrand::Speed speed,
                      uint32_t bgColor, bool invert, bool bounce) {
    forEach([&](LedStrand& s) { s.pulse(color, runLength, speed, bgColor, invert, bounce); });
}

void LedGroup::flash(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bgColor) {
    forEach([&](LedStrand& s) { s.flash(color, onMs, offMs, bgColor); });
}

void LedGroup::flow(uint32_t color1, uint32_t color2, LedStrand::Speed speed, bool invert, bool seamless) {
    forEach([&](LedStrand& s) { s.flow(color1, color2, speed, invert, seamless); });
}

void LedGroup::rainbow(LedStrand::Speed speed) {
    forEach([&](LedStrand& s) { s.rainbow(speed); });
}

void LedGroup::twinkle(const std::vector<uint32_t>& colors, uint8_t densityPct,
                        uint8_t fadeStep, uint32_t bgColor) {
    forEach([&](LedStrand& s) { s.twinkle(colors, densityPct, fadeStep, bgColor); });
}

void LedGroup::bitscroll(const std::vector<LedStrand::BitScrollSegment>& segments, LedStrand::Speed speed,
                          bool invert, uint32_t bgColor, bool bounce, uint8_t spacing, bool repeating) {
    forEach([&](LedStrand& s) {
        s.bitscroll(segments, speed, invert, bgColor, bounce, spacing, repeating);
    });
}

void LedGroup::levelFill(uint32_t color, uint32_t color2, bool gradient, uint32_t bgColor,
                         bool invert) {
    forEach([&](LedStrand& s) { s.levelFill(color, color2, gradient, bgColor, invert); });
}

void LedGroup::setLevel(uint8_t level) {
    forEach([&](LedStrand& s) { s.setLevel(level); });
}

void LedGroup::levelSource(LedStrand::LevelFn read, double emptyAt, double fullAt, bool wrap,
                           uint8_t smoothing) {
    forEach([&](LedStrand& s) { s.levelSource(read, emptyAt, fullAt, wrap, smoothing); });
}

void LedGroup::clearLevelSource() {
    forEach([&](LedStrand& s) { s.clearLevelSource(); });
}

void LedGroup::musicSync(const LedStrand::MusicTrack& track, uint32_t color, uint32_t color2,
                          bool gradient, uint32_t bgColor, bool invert, uint8_t sensitivity,
                          bool loop) {
    forEach([&](LedStrand& s) {
        s.musicSync(track, color, color2, gradient, bgColor, invert, sensitivity, loop);
    });
}

void LedGroup::musicSeek(uint32_t positionMs) {
    forEach([&](LedStrand& s) { s.musicSeek(positionMs); });
}

void LedGroup::musicPause(bool paused) {
    forEach([&](LedStrand& s) { s.musicPause(paused); });
}

void LedGroup::setSensitivity(uint8_t pct) {
    forEach([&](LedStrand& s) { s.setSensitivity(pct); });
}

void LedGroup::spliceMask(uint8_t sections, bool invert, bool alternating,
                           uint32_t altPeriodMs, uint32_t bgColor, bool useOverlay) {
    forEach([&](LedStrand& s) {
        s.spliceMask(sections, invert, alternating, altPeriodMs, bgColor, useOverlay);
    });
}

void LedGroup::spliceMaskCustom(const std::vector<LedStrand::SpliceRegion>& regions) {
    forEach([&](LedStrand& s) { s.spliceMaskCustom(regions); });
}

void LedGroup::clearSpliceMask() {
    forEach([&](LedStrand& s) { s.clearSpliceMask(); });
}

void LedGroup::setBrightness(uint8_t pct) {
    forEach([&](LedStrand& s) { s.setBrightness(pct); });
}

void LedGroup::overlaySetColor(uint32_t color) {
    forEach([&](LedStrand& s) { s.overlaySetColor(color); });
}

void LedGroup::overlayPulse(uint32_t color, uint8_t runLength, LedStrand::Speed speed, uint32_t bgColor) {
    forEach([&](LedStrand& s) { s.overlayPulse(color, runLength, speed, bgColor); });
}

void LedGroup::overlayFlash(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bgColor) {
    forEach([&](LedStrand& s) { s.overlayFlash(color, onMs, offMs, bgColor); });
}

void LedGroup::overlayFlow(uint32_t color1, uint32_t color2, LedStrand::Speed speed, bool seamless) {
    forEach([&](LedStrand& s) { s.overlayFlow(color1, color2, speed, seamless); });
}

void LedGroup::overlayRainbow(LedStrand::Speed speed) {
    forEach([&](LedStrand& s) { s.overlayRainbow(speed); });
}

void LedGroup::attachProfile(const Profile* profile) {
    forEach([&](LedStrand& s) { s.attachProfile(profile); });
}

void LedGroup::detachProfile() {
    forEach([&](LedStrand& s) { s.detachProfile(); });
}

void LedGroup::activateMode(uint8_t modeIdx) {
    forEach([&](LedStrand& s) { s.activateMode(modeIdx); });
}

void LedGroup::activateModeTimed(uint8_t modeIdx, uint32_t durationMs) {
    forEach([&](LedStrand& s) { s.activateModeTimed(modeIdx, durationMs); });
}

void LedGroup::deactivateMode(uint8_t modeIdx) {
    forEach([&](LedStrand& s) { s.deactivateMode(modeIdx); });
}

} // namespace hitlib
