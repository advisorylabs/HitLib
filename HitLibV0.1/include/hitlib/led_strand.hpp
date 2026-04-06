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

    // Init hardware. Call from PROS initialize(). Safe to call twice (no-op if already inited).
    void init();

    // Called by LedGroup's update task each cycle. Not for direct use.
    void tick();

    // ----------------------------------------------------------------
    // Base animation API
    // Thread-safe. Takes effect on the next refresh tick.
    // ----------------------------------------------------------------
    void off();
    void setColor(uint32_t color);
    void pulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor = 0x000000);
    void flash(uint32_t color, uint8_t speed, uint32_t bgColor = 0x000000);
    void flow(uint32_t color1, uint32_t color2, uint8_t speed);
    void rainbow(uint8_t speed);

    // ----------------------------------------------------------------
    // Overlay animation API
    // Writes into a second buffer composited over the base by the spread mask.
    // ----------------------------------------------------------------
    void overlaySetColor(uint32_t color);
    void overlayPulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor = 0x000000);
    void overlayFlash(uint32_t color, uint8_t speed, uint32_t bgColor = 0x000000);
    void overlayFlow(uint32_t color1, uint32_t color2, uint8_t speed);
    void overlayRainbow(uint8_t speed);

    // ----------------------------------------------------------------
    // centerSpread
    //
    // The spread mask reveals the overlay buffer from the center outward.
    // Set up a base animation and an overlay animation first, then call
    // centerSpread() to begin masking.
    //
    // tickInterval: ticks between each pixel step (higher = slower).
    // At refreshMs=20, tickInterval=10 → 200ms/pixel, ~6s for 63 LEDs.
    //
    // centerSpreadStacked(): cycles through a list of AnimSetupFn callbacks.
    // Each fn is called with the strand when it becomes the next spreading layer.
    // ----------------------------------------------------------------
    using AnimSetupFn = void (*)(LedStrand&);

    void centerSpread(uint8_t tickInterval = 8);
    void centerSpreadStacked(const std::vector<AnimSetupFn>& layers, uint8_t tickInterval = 8);

    // ----------------------------------------------------------------
    // Brightness (0–100)
    // Applied at flush time; does not modify the buffer.
    // ----------------------------------------------------------------
    void    setBrightness(uint8_t pct);
    uint8_t getBrightness() const { return brightnessPct; }

    // ----------------------------------------------------------------
    // Profile following
    // ----------------------------------------------------------------
    void attachProfile(const Profile* profile);
    void detachProfile();
    void activateMode(uint8_t modeIdx);
    void activateModeTimed(uint8_t modeIdx, uint32_t durationMs);
    void deactivateMode(uint8_t modeIdx);

    uint8_t getLength() const { return length; }

private:
    // ---- Hardware ----
    uint8_t  adiPort;
    uint8_t  smartPort  = 0;
    uint8_t  length;
    uint32_t refreshMs;
    pros::adi::Led* led = nullptr;

    pros::Mutex mutex;

    // ----------------------------------------------------------------
    // Base buffer
    // ----------------------------------------------------------------
    enum class AnimMode : uint8_t { STATIC, SHIFT, CENTER_SPREAD };

    AnimMode              animMode  = AnimMode::STATIC;
    int                   shiftStep = 0;
    std::vector<uint32_t> buffer;

    // ----------------------------------------------------------------
    // Overlay buffer
    // ----------------------------------------------------------------
    AnimMode              overlayAnimMode  = AnimMode::STATIC;
    int                   overlayShiftStep = 0;
    std::vector<uint32_t> overlayBuffer;

    // ----------------------------------------------------------------
    // centerSpread state
    // ----------------------------------------------------------------
    std::vector<bool>        spreadMask;
    uint8_t                  spreadPos          = 0;
    uint8_t                  spreadTickInterval = 8;
    uint8_t                  spreadTickCounter  = 0;
    std::vector<AnimSetupFn> spreadLayers;
    uint8_t                  spreadLayerIdx     = 0;

    // ----------------------------------------------------------------
    // Brightness
    // ----------------------------------------------------------------
    uint8_t brightnessPct = 100;

    // ----------------------------------------------------------------
    // Profile state
    // ----------------------------------------------------------------
    struct ModeEntry {
        uint8_t  modeIdx;
        bool     persistent;
        uint32_t endMs;
    };

    const Profile*         activeProfile = nullptr;
    std::vector<ModeEntry> modeStack;
    int16_t                lastModeIdx   = -1;

    // ----------------------------------------------------------------
    // Internal implementations (no lock)
    // ----------------------------------------------------------------

    // Base
    void setColorNL(uint32_t color);
    void pulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg);
    void flashNL(uint32_t color, uint8_t speed, uint32_t bg);
    void flowNL(uint32_t c1, uint32_t c2, uint8_t speed);
    void rainbowNL(uint8_t speed);

    // Overlay
    void overlaySetColorNL(uint32_t color);
    void overlayPulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg);
    void overlayFlashNL(uint32_t color, uint8_t speed, uint32_t bg);
    void overlayFlowNL(uint32_t c1, uint32_t c2, uint8_t speed);
    void overlayRainbowNL(uint8_t speed);

    // centerSpread
    void advanceCenterSpread();

    // Buffer helpers
    void     shiftBuffer();
    void     shiftOverlayBuffer();
    void     flushBuffer();
    uint32_t composite(uint32_t base, uint32_t overlay, bool useOverlay) const;

    // Profile helpers
    int16_t computeEffectiveMode() const;
    void    pruneExpired(uint32_t now);

    // Brightness
    uint32_t applyBrightness(uint32_t color) const;

    // Generators
    static std::vector<uint32_t> genGradient(uint32_t c1, uint32_t c2, uint8_t len);
    static std::vector<uint32_t> genRainbow(uint8_t len);

    static constexpr uint8_t MAX_LEDS = 64;
};

} // namespace hitlib