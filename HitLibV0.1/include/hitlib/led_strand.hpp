#pragma once
#include "led_profile.hpp"
#include "pros/adi.hpp"
#include "pros/rtos.hpp"
#include <cstdint>
#include <vector>

namespace hitlib {

class LedStrand {
public:
    struct BitScrollSegment {
        uint32_t color;
        uint8_t  width;
    };

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
    void pulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor = 0x000000,
               bool invert = false, bool bounce = false);
    void flash(uint32_t color, uint8_t speed, uint32_t bgColor = 0x000000);
    void flow(uint32_t color1, uint32_t color2, uint8_t speed, bool invert = false);
    void rainbow(uint8_t speed);
    void twinkle(const std::vector<uint32_t>& colors, uint8_t densityPct = 30,
                 uint8_t fadeStep = 16, uint32_t bgColor = 0x000000);
    void bitscroll(const std::vector<BitScrollSegment>& segments, uint8_t speed,
                   bool invert = false, uint32_t bgColor = 0x000000, bool bounce = false,
                   uint8_t spacing = 0, bool repeating = true);

    // Splice mask: splits the strip into (sections+1) equal bins; masked bins show bgColor,
    // others show the active animation. sections==0 disables.
    // invert: swap which bins are masked (e.g. first vs second half when sections==1).
    // alternating + altPeriodMs: toggles invert every altPeriodMs (e.g. 100 -> every 100ms).
    void spliceMask(uint8_t sections, bool invert = false, bool alternating = false,
                    uint32_t altPeriodMs = 100, uint32_t bgColor = 0x000000);
    void clearSpliceMask();

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
    // At refreshMs=20, tickInterval=10 -> 200ms/pixel, ~6s for 63 LEDs.
    //
    // invert: when true, reveals from the edges inward instead of center outward.
    //
    // centerSpreadStacked(): cycles through a list of AnimSetupFn callbacks.
    // Each fn is called with the strand when it becomes the next spreading layer.
    //
    // centerSpreadBounce(): expands the mask fully, then contracts it back before
    // swapping layers. Gives a "wipe in, wipe out, next" rhythm.
    // ----------------------------------------------------------------
    using AnimSetupFn = void (*)(LedStrand&);

    void centerSpread(uint8_t tickInterval = 8, bool invert = false);
    void centerSpreadStacked(const std::vector<AnimSetupFn>& layers,
                             uint8_t tickInterval = 8, bool invert = false);

    void centerSpreadBounce(uint8_t tickInterval = 8, bool invert = false);
    void centerSpreadBounceStacked(const std::vector<AnimSetupFn>& layers,
                                   uint8_t tickInterval = 8, bool invert = false);

    // ----------------------------------------------------------------
    // Brightness (0-100)
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
    enum class AnimMode : uint8_t { STATIC, SHIFT, CENTER_SPREAD, TWINKLE };

    AnimMode              animMode  = AnimMode::STATIC;
    int                   shiftStep = 0;
    // 0 = circular shift (flow/rainbow/non-bounce pulse/bitscroll); 1 = pulse bounce; 2 = bitscroll bounce
    uint8_t               shiftVariant = 0;
    std::vector<uint32_t> buffer;

    // pulse bounce
    uint8_t  pulseRunLen   = 0;
    uint32_t pulseColor    = 0;
    uint32_t pulseBg       = 0;
    uint8_t  pulseSpeed    = 1;
    int16_t  pulseOffset   = 0;
    int8_t   pulseDir      = 1;

    // bitscroll bounce (master pattern one strand long, shifted by bounceScrollPos)
    std::vector<uint32_t> bitscrollMaster;
    int16_t               bounceScrollPos = 0;
    int8_t                bounceScrollDir = 1;
    uint8_t               bounceSpeed     = 1;
    std::vector<uint8_t>  twinkleLevel;
    std::vector<uint8_t>  twinkleTarget;
    std::vector<uint8_t>  twinkleColorIdx;
    std::vector<uint8_t>  twinkleHoldTicks;
    std::vector<uint32_t> twinklePalette;
    uint8_t               twinkleDensityPct = 30;
    uint8_t               twinkleFadeStep   = 16;
    uint32_t              twinkleBgColor    = 0x000000;

    // splice mask (applied after centerSpread composite)
    bool                  spliceActive      = false;
    uint8_t               spliceSections    = 0;
    bool                  spliceInvert      = false;
    bool                  spliceAlternating = false;
    uint32_t              spliceAltMs       = 100;
    uint32_t              spliceBgColor       = 0x000000;
    bool                  spliceAltPhase      = false;
    uint32_t              spliceLastToggleMs  = 0;
    std::vector<bool>     spliceShowAnim;

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
    bool                     spreadInvert       = false;
    bool                     spreadBounce       = false;
    bool                     spreadReturning    = false;

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
    void pulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg, bool invert, bool bounce);
    void flashNL(uint32_t color, uint8_t speed, uint32_t bg);
    void flowNL(uint32_t c1, uint32_t c2, uint8_t speed, bool invert);
    void rainbowNL(uint8_t speed);
    void twinkleNL(const std::vector<uint32_t>& colors, uint8_t densityPct,
                   uint8_t fadeStep, uint32_t bgColor);
    void bitscrollNL(const std::vector<BitScrollSegment>& segments, uint8_t speed, bool invert, uint32_t bgColor,
                     bool bounce, uint8_t spacing, bool repeating);

    // Overlay
    void overlaySetColorNL(uint32_t color);
    void overlayPulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg);
    void overlayFlashNL(uint32_t color, uint8_t speed, uint32_t bg);
    void overlayFlowNL(uint32_t c1, uint32_t c2, uint8_t speed);
    void overlayRainbowNL(uint8_t speed);

    // centerSpread
    void advanceCenterSpread();
    void advanceTwinkle();
    void advancePulseBounce();
    void advanceBitscrollBounce();
    void fillBitscrollFromMaster();
    void advanceSpliceAlternating(uint32_t nowMs);
    void rebuildSpliceMask();
    void doLayerSwap();     // shared by spread and bounce on completion

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