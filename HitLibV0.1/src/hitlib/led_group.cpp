#include "hitlib/led_group.hpp"

namespace hitlib {

void LedGroup::add(LedStrand* s) { strands.push_back(s); }

void LedGroup::init(uint32_t refreshMs) {
    this->refreshMs = refreshMs;
    for (auto* s : strands) s->init(); // s_adiMutex serializes + settles each one
}

void LedGroup::groupTask() {
    while (true) {
        for (auto* s : strands) {
            s->tick();
        }
        pros::delay(refreshMs);
    }
}

void LedGroup::start() {
    pros::Task([this]() { groupTask(); });
}

void LedGroup::off()                                                   { for (auto* s : strands) s->off(); }
void LedGroup::setColor(uint32_t c)                                    { for (auto* s : strands) s->setColor(c); }
void LedGroup::pulse(uint32_t c, uint8_t r, uint8_t sp, uint32_t bg)  { for (auto* s : strands) s->pulse(c, r, sp, bg); }
void LedGroup::flash(uint32_t c, uint8_t sp, uint32_t bg)             { for (auto* s : strands) s->flash(c, sp, bg); }
void LedGroup::flow(uint32_t c1, uint32_t c2, uint8_t sp)             { for (auto* s : strands) s->flow(c1, c2, sp); }
void LedGroup::rainbow(uint8_t sp)                                     { for (auto* s : strands) s->rainbow(sp); }
void LedGroup::setBrightness(uint8_t p)                                { for (auto* s : strands) s->setBrightness(p); }

void LedGroup::attachProfile(const Profile* p)              { for (auto* s : strands) s->attachProfile(p); }
void LedGroup::detachProfile()                              { for (auto* s : strands) s->detachProfile(); }
void LedGroup::activateMode(uint8_t i)                      { for (auto* s : strands) s->activateMode(i); }
void LedGroup::activateModeTimed(uint8_t i, uint32_t ms)    { for (auto* s : strands) s->activateModeTimed(i, ms); }
void LedGroup::deactivateMode(uint8_t i)                    { for (auto* s : strands) s->deactivateMode(i); }

} // namespace hitlib