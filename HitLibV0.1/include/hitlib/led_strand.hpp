#pragma once
#include "led_profile.hpp"
#include "pros/adi.hpp"
#include "pros/rtos.hpp"
#include <cstdint>
#include <vector>

namespace hitlib {

class LedStrand {
public:
    // Standard ADI port
    LedStrand(uint8_t adiPort, uint8_t length, uint32_t refreshMs = 20);
    // ADI expander (smart port + adi port)
    LedStrand(uint8_t smartPort, uint8_t adiPort, uint8_t length, uint32_t refreshMs = 20);

    // Init hardware + start the per-strand refresh task.
    // Call from PROS initialize(). Safe to call twice (no-op if already inited).
    void init();

    // Called by LedGroup's update task each cycle. Not for direct use.
    void tick();

    // ---- Raw animation API ----
    // Thread-safe. Takes effect on the next refresh tick.
    void off();
    void setColor(uint32_t color);
    void pulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor = 0x000000);
    void flash(uint32_t color, uint8_t speed, uint32_t bgColor = 0x000000);
    void flow(uint32_t color1, uint32_t color2, uint8_t speed);
    void rainbow(uint8_t speed);

    // ---- Brightness (0–100) ----
    // Applied at flush time, does not modify the buffer.
    void    setBrightness(uint8_t pct);
    uint8_t getBrightness() const { return brightnessPct; }

    // ---- Profile following ----
    // Attach a profile; strand enters profile-driven mode.
    void attachProfile(const Profile* profile);

    // Return to raw-command mode, clear all active modes.
    void detachProfile();

    // Activate a mode persistently (stays until deactivateMode is called).
    void activateMode(uint8_t modeIdx);

    // Activate a mode for durationMs then automatically expire.
    // If the same mode is already timed, extends to whichever end time is later.
    void activateModeTimed(uint8_t modeIdx, uint32_t durationMs);

    // Remove a mode (persistent or timed).
    void deactivateMode(uint8_t modeIdx);

    uint8_t getLength() const { return length; }

private:
    // ---- Hardware ----
    uint8_t  adiPort;
    uint8_t  smartPort  = 0;
    uint8_t  length;
    uint32_t refreshMs;
    pros::adi::Led* led = nullptr;

    // ---- Buffer (mutex-protected) ----
    pros::Mutex mutex;

    enum class AnimMode : uint8_t { STATIC, SHIFT };
    AnimMode              animMode  = AnimMode::STATIC;
    int                   shiftStep = 0;
    std::vector<uint32_t> buffer;

    uint8_t brightnessPct = 100;

    // ---- Profile state (mutex-protected) ----
    struct ModeEntry {
        uint8_t  modeIdx;
        bool     persistent;
        uint32_t endMs;
    };

    const Profile*         activeProfile = nullptr;
    std::vector<ModeEntry> modeStack;
    int16_t                lastModeIdx   = -1;

    // ---- Internal (no lock) ----
    void setColorNL(uint32_t color);
    void pulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg);
    void flashNL(uint32_t color, uint8_t speed, uint32_t bg);
    void flowNL(uint32_t c1, uint32_t c2, uint8_t speed);
    void rainbowNL(uint8_t speed);

    void    shiftBuffer();
    void    flushBuffer();
    int16_t computeEffectiveMode() const;
    void    pruneExpired(uint32_t now);

    uint32_t applyBrightness(uint32_t color) const;


    static std::vector<uint32_t> genGradient(uint32_t c1, uint32_t c2, uint8_t len);
    static std::vector<uint32_t> genRainbow(uint8_t len);

    static constexpr uint8_t MAX_LEDS = 64;
};

} // namespace hitlib