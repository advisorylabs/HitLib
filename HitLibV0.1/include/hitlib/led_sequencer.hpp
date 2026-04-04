#pragma once
#include <cstdint>

namespace leds {

class Sequencer {
public:
    struct Phase {
        uint32_t durationMs;
        void (*startFn)();
    };

    Sequencer(const Phase* phases, uint8_t count);

    void start();
    void stop();
    void update();
    bool isRunning() const;

private:
    const Phase* phases;
    uint8_t phaseCount;

    uint8_t currentPhase = 0;
    uint32_t phaseStartTime = 0;
    bool running = false;
};

}
