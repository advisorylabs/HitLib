#include "hitlib/led_group.hpp"

namespace hitlib {

void LedGroup::add(LedStrand* s) { strands.push_back(s); }

void LedGroup::init(uint32_t refreshMs) {
    this->refreshMs = refreshMs;
    for (auto* s : strands) s->init();
}

void LedGroup::groupTask() {
    while (true) {
        for (auto* s : strands) s->tick();
        pros::delay(refreshMs);
    }
}

void LedGroup::start() {
    pros::Task([this]() { groupTask(); });
}

// ----------------------------------------------------------------
// Base animation fan-out
// ----------------------------------------------------------------

void LedGroup::off()                                                   { for (auto* s : strands) s->off(); }
void LedGroup::setColor(uint32_t c)                                    { for (auto* s : strands) s->setColor(c); }
void LedGroup::pulse(uint32_t c, uint8_t r, uint8_t sp, uint32_t bg, bool invert, bool bounce) {
    for (auto* s : strands) s->pulse(c, r, sp, bg, invert, bounce);
}
void LedGroup::flash(uint32_t c, uint8_t sp, uint32_t bg)             { for (auto* s : strands) s->flash(c, sp, bg); }
void LedGroup::flow(uint32_t c1, uint32_t c2, uint8_t sp, bool invert){ for (auto* s : strands) s->flow(c1, c2, sp, invert); }
void LedGroup::rainbow(uint8_t sp)                                     { for (auto* s : strands) s->rainbow(sp); }
void LedGroup::twinkle(const std::vector<uint32_t>& colors, uint8_t densityPct,
                       uint8_t fadeStep, uint32_t bgColor) {
    for (auto* s : strands) s->twinkle(colors, densityPct, fadeStep, bgColor);
}
void LedGroup::bitscroll(const std::vector<LedStrand::BitScrollSegment>& segments, uint8_t speed,
                         bool invert, uint32_t bgColor, bool bounce, uint8_t spacing, bool repeating) {
    for (auto* s : strands) s->bitscroll(segments, speed, invert, bgColor, bounce, spacing, repeating);
}

void LedGroup::spliceMask(uint8_t sections, bool invert, bool alternating, uint32_t altPeriodMs, uint32_t bgColor) {
    for (auto* s : strands) s->spliceMask(sections, invert, alternating, altPeriodMs, bgColor);
}

void LedGroup::clearSpliceMask() {
    for (auto* s : strands) s->clearSpliceMask();
}
void LedGroup::setBrightness(uint8_t p)                                { for (auto* s : strands) s->setBrightness(p); }

// ----------------------------------------------------------------
// Overlay animation fan-out
// ----------------------------------------------------------------

void LedGroup::overlaySetColor(uint32_t c)                                   { for (auto* s : strands) s->overlaySetColor(c); }
void LedGroup::overlayPulse(uint32_t c, uint8_t r, uint8_t sp, uint32_t bg) { for (auto* s : strands) s->overlayPulse(c, r, sp, bg); }
void LedGroup::overlayFlash(uint32_t c, uint8_t sp, uint32_t bg)            { for (auto* s : strands) s->overlayFlash(c, sp, bg); }
void LedGroup::overlayFlow(uint32_t c1, uint32_t c2, uint8_t sp)            { for (auto* s : strands) s->overlayFlow(c1, c2, sp); }
void LedGroup::overlayRainbow(uint8_t sp)                                    { for (auto* s : strands) s->overlayRainbow(sp); }

// ----------------------------------------------------------------
// centerSpread fan-out
// ----------------------------------------------------------------

void LedGroup::centerSpread(uint8_t tickInterval, bool invert) {
    for (auto* s : strands) s->centerSpread(tickInterval, invert);
}

void LedGroup::centerSpreadStacked(const std::vector<LedStrand::AnimSetupFn>& layers,
                                   uint8_t tickInterval, bool invert) {
    for (auto* s : strands) s->centerSpreadStacked(layers, tickInterval, invert);
}

void LedGroup::centerSpreadBounce(uint8_t tickInterval, bool invert) {
    for (auto* s : strands) s->centerSpreadBounce(tickInterval, invert);
}

void LedGroup::centerSpreadBounceStacked(const std::vector<LedStrand::AnimSetupFn>& layers,
                                         uint8_t tickInterval, bool invert) {
    for (auto* s : strands) s->centerSpreadBounceStacked(layers, tickInterval, invert);
}

// ----------------------------------------------------------------
// Profile fan-out
// ----------------------------------------------------------------

void LedGroup::attachProfile(const Profile* p)              { for (auto* s : strands) s->attachProfile(p); }
void LedGroup::detachProfile()                              { for (auto* s : strands) s->detachProfile(); }
void LedGroup::activateMode(uint8_t i)                      { for (auto* s : strands) s->activateMode(i); }
void LedGroup::activateModeTimed(uint8_t i, uint32_t ms)    { for (auto* s : strands) s->activateModeTimed(i, ms); }
void LedGroup::deactivateMode(uint8_t i)                    { for (auto* s : strands) s->deactivateMode(i); }

} // namespace hitlib