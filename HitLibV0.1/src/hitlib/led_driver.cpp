#include "hitlib/led_driver.hpp"
#include "pros/adi.hpp"
#include "pros/motors.hpp"
#include "pros/rtos.hpp"
#include <algorithm>
#include <cmath>

namespace hitlib {

// ================================================================
//  LedStrand
// ================================================================

LedStrand::LedStrand(uint8_t adiPort, uint8_t length)
    : adiPort(adiPort), length(std::min(length, MAX_LEDS)) {}

LedStrand::LedStrand(uint8_t smartPort, uint8_t adiPort, uint8_t length)
    : adiPort(adiPort), smartPort(smartPort), length(std::min(length, MAX_LEDS)) {}

void LedStrand::init() {
    if (smartPort != 0) {
        pros::adi::ext_adi_port_pair_t pair = {smartPort, adiPort};
        led = new pros::adi::Led(pair, length);
    } else {
        led = new pros::adi::Led(adiPort, length);
    }
    buffer.assign(length, 0x000000);
}

// ================================================================
//  Animation setters
// ================================================================

void LedStrand::off() {
    animMode   = AnimMode::STATIC;
    shiftValue = 0;
    buffer.assign(length, 0x000000);
}

void LedStrand::setColor(uint32_t color) {
    animMode   = AnimMode::STATIC;
    shiftValue = 0;
    buffer.assign(length, color);
}

void LedStrand::pulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor) {
    animMode = AnimMode::SHIFT;
    buffer.assign(length, bgColor);
    uint8_t safeRun = std::min(runLength, length);
    for (uint8_t i = 0; i < safeRun; i++) buffer[i] = color;
    shiftValue = speed;
}

void LedStrand::flash(uint32_t color, uint8_t speed, uint32_t bgColor) {
    animMode = AnimMode::SHIFT;
    buffer.clear();
    buffer.resize(length, color);
    buffer.resize(length * speed * 2, bgColor);
    shiftValue = length;
}

void LedStrand::flow(uint32_t color1, uint32_t color2, uint8_t speed) {
    animMode   = AnimMode::SHIFT;
    buffer     = genGradient(color1, color2, length);
    shiftValue = speed;
}

void LedStrand::rainbow(uint8_t speed) {
    animMode   = AnimMode::SHIFT;
    buffer     = genRainbow(length);
    shiftValue = speed;
}

// ================================================================
//  motorFollow
// ================================================================

void LedStrand::motorFollow(
    int8_t    motorPort,
    float     startDeg,
    float     endDeg,
    FillStyle style,
    uint32_t  color1,
    uint32_t  color2,
    uint8_t   speed,
    uint8_t   runLength,
    uint32_t  bgColor,
    bool      inverse)
{
    animMode   = AnimMode::DYNAMIC;
    shiftValue = 0;

    mfState = {motorPort, startDeg, endDeg, bgColor, inverse};

    fillBuffer.clear();
    fillShift = 0;

    switch (style) {
        case FillStyle::SOLID:
            fillBuffer.assign(length, color1);
            fillShift = 0;
            break;

        case FillStyle::FLOW:
            fillBuffer = genGradient(color1, color2, length);
            fillShift  = speed;
            break;

        case FillStyle::PULSE:
            fillBuffer.assign(length, color2);
            for (uint8_t i = 0; i < std::min(runLength, length); i++)
                fillBuffer[i] = color1;
            fillShift = speed;
            break;

        case FillStyle::FLASH:
            fillBuffer.resize(length, color1);
            fillBuffer.resize(length * speed * 2, color2);
            fillShift = length;
            break;

        case FillStyle::RAINBOW:
            fillBuffer = genRainbow(length);
            fillShift  = speed;
            break;
    }

    buffer.assign(length, bgColor);
}

// ================================================================
//  Task-facing methods
// ================================================================

void LedStrand::dynamicUpdate() {
    if (animMode != AnimMode::DYNAMIC) return;

    // 1. Read motor and compute fill fraction
    float pos   = (float)pros::Motor(mfState.motorPort).get_position();
    float range = mfState.endDeg - mfState.startDeg;
    float t     = (range != 0.0f) ? (pos - mfState.startDeg) / range : 0.0f;
    t = (t < 0.0f) ? 0.0f : (t > 1.0f) ? 1.0f : t;

    if (mfState.inverse) t = 1.0f - t;

    uint8_t fillCount = (uint8_t)(t * length);

    // 2. Advance fill animation
    if (fillShift > 0 && fillBuffer.size() >= (size_t)fillShift) {
        std::rotate(
            fillBuffer.rbegin(),
            fillBuffer.rbegin() + fillShift,
            fillBuffer.rend()
        );
    }

    // 3. Map fill buffer onto lit LEDs, background onto unlit LEDs
    for (uint8_t i = 0; i < length; i++) {
        buffer[i] = (i < fillCount)
            ? fillBuffer[i % fillBuffer.size()]
            : mfState.bgColor;
    }
}

void LedStrand::bufferShift() {
    if (animMode != AnimMode::SHIFT) return;
    if (shiftValue == 0 || buffer.size() < (size_t)shiftValue) return;
    std::rotate(buffer.rbegin(), buffer.rbegin() + shiftValue, buffer.rend());
}

void LedStrand::flush() {
    if (!led) return;
    for (uint8_t i = 0; i < length; i++) {
        led->set_pixel(buffer[i % buffer.size()], i);
    }
    led->update();
}

// ================================================================
//  Generators
// ================================================================

std::vector<uint32_t> LedStrand::genGradient(uint32_t c1, uint32_t c2, uint8_t len) {
    std::vector<uint32_t> out;
    out.reserve(len);

    uint8_t r1 = (c1 >> 16) & 0xFF, g1 = (c1 >> 8) & 0xFF, b1 = c1 & 0xFF;
    uint8_t r2 = (c2 >> 16) & 0xFF, g2 = (c2 >> 8) & 0xFF, b2 = c2 & 0xFF;

    for (uint8_t i = 0; i < len; i++) {
        float t = (len > 1) ? (float)i / (len - 1) : 0.0f;
        uint8_t r = (uint8_t)(r1 + (r2 - r1) * t);
        uint8_t g = (uint8_t)(g1 + (g2 - g1) * t);
        uint8_t b = (uint8_t)(b1 + (b2 - b1) * t);
        out.push_back((r << 16) | (g << 8) | b);
    }
    return out;
}

std::vector<uint32_t> LedStrand::genRainbow(uint8_t len) {
    std::vector<uint32_t> out;
    out.reserve(len);

    for (uint8_t i = 0; i < len; i++) {
        float hue = (float)i / len * 360.0f;
        float r, g, b;

        if      (hue < 60)  { r = hue / 60;        g = 1;               b = 0; }
        else if (hue < 120) { r = 1;                g = 1-(hue-60)/60;  b = 0; }
        else if (hue < 180) { r = 0;                g = 1;               b = (hue-120)/60; }
        else if (hue < 240) { r = 0;                g = 1-(hue-180)/60; b = 1; }
        else if (hue < 300) { r = (hue-240)/60;    g = 0;               b = 1; }
        else                { r = 1;                g = 0;               b = 1-(hue-300)/60; }

        out.push_back(
            ((uint32_t)(r * 255) << 16) |
            ((uint32_t)(g * 255) << 8)  |
            ((uint32_t)(b * 255))
        );
    }
    return out;
}

// ================================================================
//  LedManager
// ================================================================

void LedManager::addStrand(LedStrand* strand) {
    strands.push_back(strand);
}

void LedManager::initialize(uint32_t refreshRateMs) {
    this->refreshRateMs = refreshRateMs;
    for (auto* s : strands) s->init();
    pros::Task([this]() { updaterTask(); });
}

void LedManager::updaterTask() {
    while (true) {
        for (auto* s : strands) {
            s->dynamicUpdate();
            s->bufferShift();
            s->flush();
            pros::delay(refreshRateMs);
        }
    }
}

// ================================================================
//  Fan-out commands
// ================================================================

void LedManager::off()
    { for (auto* s : strands) s->off(); }

void LedManager::setColor(uint32_t color)
    { for (auto* s : strands) s->setColor(color); }

void LedManager::pulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor)
    { for (auto* s : strands) s->pulse(color, runLength, speed, bgColor); }

void LedManager::flash(uint32_t color, uint8_t speed, uint32_t bgColor)
    { for (auto* s : strands) s->flash(color, speed, bgColor); }

void LedManager::flow(uint32_t color1, uint32_t color2, uint8_t speed)
    { for (auto* s : strands) s->flow(color1, color2, speed); }

void LedManager::rainbow(uint8_t speed)
    { for (auto* s : strands) s->rainbow(speed); }

void LedManager::motorFollow(
    int8_t    motorPort,
    float     startDeg,
    float     endDeg,
    FillStyle style,
    uint32_t  color1,
    uint32_t  color2,
    uint8_t   speed,
    uint8_t   runLength,
    uint32_t  bgColor,
    bool      inverse)
{
    for (auto* s : strands)
        s->motorFollow(motorPort, startDeg, endDeg, style, color1, color2, speed, runLength, bgColor, inverse);
}

} // namespace ledlib