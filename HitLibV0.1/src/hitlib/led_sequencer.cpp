#include "hitlib/led_sequencer.hpp"
#include "pros/rtos.hpp"

namespace leds {

Sequencer::Sequencer(const Phase* p, uint8_t count)
    : phases(p), phaseCount(count) {}

void Sequencer::start() {
    running = true;
    currentPhase = 0;
    phaseStartTime = pros::millis();
    phases[0].startFn();
}

void Sequencer::stop() {
    running = false;
}

bool Sequencer::isRunning() const {
    return running;
}

void Sequencer::update() {
    if (!running) return;

    uint32_t now = pros::millis();
    if (now - phaseStartTime >= phases[currentPhase].durationMs) {
        currentPhase = (currentPhase + 1) % phaseCount;
        phaseStartTime = now;
        phases[currentPhase].startFn();
    }
}

}
