#pragma once
#include "pros/adi.hpp"
#include "pros/motors.hpp"
#include <cstdint>
#include <vector>

namespace hitlib {

// ---------------------------------------------------------------
// FillStyle
// Controls how the lit portion of a motorFollow strip looks.
// ---------------------------------------------------------------
enum class FillStyle : uint8_t {
    SOLID,      // flat color1
    FLOW,       // gradient color1 → color2, scrolling at speed
    PULSE,      // blob of color1 (runLength wide) scrolling on color2 bg
    FLASH,      // alternates color1 / color2 at speed
    RAINBOW     // full rainbow across lit portion, scrolling at speed
};

// ---------------------------------------------------------------
// LedStrand
// Represents a single physical ADI LED strip.
// Owns the buffer and all animation state for that strip.
// ---------------------------------------------------------------
class LedStrand {
public:
    // Standard ADI port
    LedStrand(uint8_t adiPort, uint8_t length);

    // Expander port (smart port + adi port)
    LedStrand(uint8_t smartPort, uint8_t adiPort, uint8_t length);

    // Must be called once before use — registers with PROS ADI
    void init();

    // ---- Animation setters ----
    void off();
    void setColor(uint32_t color);
    void pulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor);
    void flash(uint32_t color, uint8_t speed, uint32_t bgColor);
    void flow(uint32_t color1, uint32_t color2, uint8_t speed);
    void rainbow(uint8_t speed);

    // Motor-position-mapped fill animation.
    // startDeg is pre-captured by led_state at call time.
    // inverse = true means fully lit at start, emptying as motor moves.
    void motorFollow(
        int8_t    motorPort,
        float     startDeg,
        float     endDeg,
        FillStyle style,
        uint32_t  color1,
        uint32_t  color2    = 0x000000,
        uint8_t   speed     = 1,
        uint8_t   runLength = 3,
        uint32_t  bgColor   = 0x000000,
        bool      inverse   = false
    );

    // ---- Called by LedManager task each tick ----
    void dynamicUpdate();   // recomputes buffer from motor pos if DYNAMIC
    void bufferShift();     // rotates buffer if SHIFT
    void flush();           // writes buffer to hardware

    uint8_t getLength() const { return length; }

private:
    uint8_t adiPort;
    uint8_t smartPort  = 0;     // 0 = no expander
    uint8_t length;

    int     shiftValue = 0;
    std::vector<uint32_t> buffer;

    pros::adi::Led* led = nullptr;

    // ---- Animation mode ----
    enum class AnimMode : uint8_t { STATIC, SHIFT, DYNAMIC };
    AnimMode animMode = AnimMode::STATIC;

    // ---- motorFollow state ----
    struct MotorFollowState {
        int8_t   motorPort;
        float    startDeg;
        float    endDeg;
        uint32_t bgColor;
        bool     inverse;
    };
    MotorFollowState mfState {};

    std::vector<uint32_t> fillBuffer;   // animation buffer for lit portion
    uint8_t               fillShift = 0;

    // ---- Generators ----
    static std::vector<uint32_t> genGradient(uint32_t c1, uint32_t c2, uint8_t len);
    static std::vector<uint32_t> genRainbow(uint8_t len);

    static constexpr uint8_t MAX_LEDS = 63;
};

// ---------------------------------------------------------------
// LedManager
// Owns a collection of LedStrand pointers and fans out all
// animation commands to every registered strand.
// Spins the background update task.
// ---------------------------------------------------------------
class LedManager {
public:
    LedManager() = default;

    // Register a strand (call before initialize())
    void addStrand(LedStrand* strand);

    // Inits all strands + starts background task
    void initialize(uint32_t refreshRateMs = 20);

    // ---- Animation commands (fan out to all strands) ----
    void off();
    void setColor(uint32_t color);
    void pulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor);
    void flash(uint32_t color, uint8_t speed, uint32_t bgColor);
    void flow(uint32_t color1, uint32_t color2, uint8_t speed);
    void rainbow(uint8_t speed);
    void motorFollow(
        int8_t    motorPort,
        float     startDeg,
        float     endDeg,
        FillStyle style,
        uint32_t  color1,
        uint32_t  color2    = 0x000000,
        uint8_t   speed     = 1,
        uint8_t   runLength = 3,
        uint32_t  bgColor   = 0x000000,
        bool      inverse   = false
    );

private:
    std::vector<LedStrand*> strands;
    uint32_t refreshRateMs = 20;

    void updaterTask();
};

} // namespace ledlib