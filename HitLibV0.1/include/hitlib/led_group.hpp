#pragma once
#include "led_strand.hpp"
#include "led_profile.hpp"
#include <vector>

namespace hitlib {

// Owns a set of LedStrand pointers and fans all commands out to each.
// Multiple independent groups are supported with no shared state.
class LedGroup {
public:
    // Add a strand. Must be called before init().
    void add(LedStrand* strand);

    // Calls init() on every strand then starts the group refresh task.
    void init(uint32_t refreshMs = 20);
    void start();

    // ----------------------------------------------------------------
    // Base animation fan-out
    // ----------------------------------------------------------------
    void off();
    void setColor(uint32_t color);
    void pulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor = 0x000000);
    void flash(uint32_t color, uint8_t speed, uint32_t bgColor = 0x000000);
    void flow(uint32_t color1, uint32_t color2, uint8_t speed);
    void rainbow(uint8_t speed);
    void setBrightness(uint8_t pct);

    // ----------------------------------------------------------------
    // Overlay animation fan-out
    // ----------------------------------------------------------------
    void overlaySetColor(uint32_t color);
    void overlayPulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor = 0x000000);
    void overlayFlash(uint32_t color, uint8_t speed, uint32_t bgColor = 0x000000);
    void overlayFlow(uint32_t color1, uint32_t color2, uint8_t speed);
    void overlayRainbow(uint8_t speed);

    // ----------------------------------------------------------------
    // centerSpread fan-out
    // ----------------------------------------------------------------
    void centerSpread(uint8_t tickInterval = 8);
    void centerSpreadStacked(const std::vector<LedStrand::AnimSetupFn>& layers, uint8_t tickInterval = 8);

    // ----------------------------------------------------------------
    // Profile fan-out
    // ----------------------------------------------------------------
    void attachProfile(const Profile* profile);
    void detachProfile();
    void activateMode(uint8_t modeIdx);
    void activateModeTimed(uint8_t modeIdx, uint32_t durationMs);
    void deactivateMode(uint8_t modeIdx);

    // Direct per-strand access for mixed control
    LedStrand* operator[](size_t i) { return strands[i]; }
    size_t     size()  const        { return strands.size(); }

private:
    std::vector<LedStrand*> strands;
    uint32_t refreshMs = 20;
    void groupTask();
};

} // namespace hitlib