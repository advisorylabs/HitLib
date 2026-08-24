#include "hitlib/led_group.hpp"
#include "pros/rtos.hpp"

namespace hitlib {

void LedGroup::add(LedStrand* strand) {
    strands.push_back(strand);
}

void LedGroup::init(uint32_t refreshMsArg) {
    if (refreshMsArg != 0) refreshMs = refreshMsArg;
    for (LedStrand* s : strands) s->init();
}

void LedGroup::start() {
    if (strands.empty()) return;
    pros::Task([this] { groupTask(); }, TASK_PRIORITY_DEFAULT, TASK_STACK_DEPTH_DEFAULT, "LedGroup");
}

void LedGroup::groupTask() {
    uint32_t prevTime = pros::millis();
    while (true) {
        for (LedStrand* s : strands) s->tick();
        pros::Task::delay_until(&prevTime, refreshMs);
    }
}

// ============================================================================
// Fan-out wrappers -- forward to every strand in the group.
// ============================================================================

void LedGroup::off() {
    for (LedStrand* s : strands) s->off();
}

void LedGroup::setColor(uint32_t color) {
    for (LedStrand* s : strands) s->setColor(color);
}

void LedGroup::pulse(uint32_t color, uint8_t runLength, uint8_t speed,
                      uint32_t bgColor, bool invert, bool bounce) {
    for (LedStrand* s : strands) s->pulse(color, runLength, speed, bgColor, invert, bounce);
}

void LedGroup::flash(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bgColor) {
    for (LedStrand* s : strands) s->flash(color, onMs, offMs, bgColor);
}

void LedGroup::flow(uint32_t color1, uint32_t color2, uint8_t speed, bool invert) {
    for (LedStrand* s : strands) s->flow(color1, color2, speed, invert);
}

void LedGroup::rainbow(uint8_t speed) {
    for (LedStrand* s : strands) s->rainbow(speed);
}

void LedGroup::twinkle(const std::vector<uint32_t>& colors, uint8_t densityPct,
                        uint8_t fadeStep, uint32_t bgColor) {
    for (LedStrand* s : strands) s->twinkle(colors, densityPct, fadeStep, bgColor);
}

void LedGroup::bitscroll(const std::vector<LedStrand::BitScrollSegment>& segments, uint8_t speed,
                          bool invert, uint32_t bgColor, bool bounce, uint8_t spacing, bool repeating) {
    for (LedStrand* s : strands) s->bitscroll(segments, speed, invert, bgColor, bounce, spacing, repeating);
}

void LedGroup::spliceMask(uint8_t sections, bool invert, bool alternating,
                           uint32_t altPeriodMs, uint32_t bgColor, bool useOverlay) {
    for (LedStrand* s : strands) s->spliceMask(sections, invert, alternating, altPeriodMs, bgColor, useOverlay);
}

void LedGroup::spliceMaskCustom(const std::vector<LedStrand::SpliceRegion>& regions) {
    for (LedStrand* s : strands) s->spliceMaskCustom(regions);
}

void LedGroup::clearSpliceMask() {
    for (LedStrand* s : strands) s->clearSpliceMask();
}

void LedGroup::setBrightness(uint8_t pct) {
    for (LedStrand* s : strands) s->setBrightness(pct);
}

void LedGroup::overlaySetColor(uint32_t color) {
    for (LedStrand* s : strands) s->overlaySetColor(color);
}

void LedGroup::overlayPulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor) {
    for (LedStrand* s : strands) s->overlayPulse(color, runLength, speed, bgColor);
}

void LedGroup::overlayFlash(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bgColor) {
    for (LedStrand* s : strands) s->overlayFlash(color, onMs, offMs, bgColor);
}

void LedGroup::overlayFlow(uint32_t color1, uint32_t color2, uint8_t speed) {
    for (LedStrand* s : strands) s->overlayFlow(color1, color2, speed);
}

void LedGroup::overlayRainbow(uint8_t speed) {
    for (LedStrand* s : strands) s->overlayRainbow(speed);
}

void LedGroup::centerSpread(uint8_t tickInterval, bool invert) {
    for (LedStrand* s : strands) s->centerSpread(tickInterval, invert);
}

void LedGroup::centerSpreadStacked(const std::vector<LedStrand::AnimSetupFn>& layers,
                                    uint8_t tickInterval, bool invert) {
    for (LedStrand* s : strands) s->centerSpreadStacked(layers, tickInterval, invert);
}

void LedGroup::centerSpreadBounce(uint8_t tickInterval, bool invert) {
    for (LedStrand* s : strands) s->centerSpreadBounce(tickInterval, invert);
}

void LedGroup::centerSpreadBounceStacked(const std::vector<LedStrand::AnimSetupFn>& layers,
                                          uint8_t tickInterval, bool invert) {
    for (LedStrand* s : strands) s->centerSpreadBounceStacked(layers, tickInterval, invert);
}

void LedGroup::attachProfile(const Profile* profile) {
    for (LedStrand* s : strands) s->attachProfile(profile);
}

void LedGroup::detachProfile() {
    for (LedStrand* s : strands) s->detachProfile();
}

void LedGroup::activateMode(uint8_t modeIdx) {
    for (LedStrand* s : strands) s->activateMode(modeIdx);
}

void LedGroup::activateModeTimed(uint8_t modeIdx, uint32_t durationMs) {
    for (LedStrand* s : strands) s->activateModeTimed(modeIdx, durationMs);
}

void LedGroup::deactivateMode(uint8_t modeIdx) {
    for (LedStrand* s : strands) s->deactivateMode(modeIdx);
}

} // namespace hitlib
