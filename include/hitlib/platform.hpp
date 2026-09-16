#pragma once
#include <cstdint>

/**
 * @file platform.hpp
 * @brief The handful of OS and hardware calls HitLib needs, kept apart from
 *        any one SDK so the library builds under PROS and VEXcode alike.
 *
 * Nothing here includes a PROS or VEX header: each backend lives in its own
 * source file (`platform_pros.cpp`, `platform_vexcode.cpp`) and only the one
 * matching the project compiles to anything.  The platform is detected from
 * the headers on the include path; to force it, define one of these before
 * building the library:
 *
 * | Macro                        | Platform                               |
 * |------------------------------|----------------------------------------|
 * | `HITLIB_PLATFORM_PROS=1`     | PROS 4.x                               |
 * | `HITLIB_PLATFORM_VEXCODE=1`  | VEXcode V5, VEXcode Pro V5, VS Code VEX extension |
 *
 * User code never needs to touch this header.
 */

#if !defined(HITLIB_PLATFORM_PROS) && !defined(HITLIB_PLATFORM_VEXCODE)
#  if defined(__has_include)
#    if __has_include("pros/rtos.hpp")
#      define HITLIB_PLATFORM_PROS 1
#    elif __has_include("v5_vcs.h")
#      define HITLIB_PLATFORM_VEXCODE 1
#    endif
#  endif
#endif

#ifndef HITLIB_PLATFORM_PROS
#  define HITLIB_PLATFORM_PROS 0
#endif
#ifndef HITLIB_PLATFORM_VEXCODE
#  define HITLIB_PLATFORM_VEXCODE 0
#endif

#if HITLIB_PLATFORM_PROS && HITLIB_PLATFORM_VEXCODE
#  error "HitLib: both HITLIB_PLATFORM_PROS and HITLIB_PLATFORM_VEXCODE are set; pick one."
#elif !HITLIB_PLATFORM_PROS && !HITLIB_PLATFORM_VEXCODE
#  error "HitLib: couldn't find PROS (pros/rtos.hpp) or the VEX SDK (v5_vcs.h) on the include path. Define HITLIB_PLATFORM_PROS=1 or HITLIB_PLATFORM_VEXCODE=1."
#endif

namespace hitlib {
namespace platform {

/** @brief Milliseconds since the program started. */
uint32_t millis();

/** @brief Sleep the calling task for @p ms milliseconds. */
void delay(uint32_t ms);

/**
 * @brief Sleep until @p prevMs + @p ms, then advance @p prevMs by @p ms.
 *
 * Keeps a loop on a fixed period no matter how long its body took.  A body
 * that overran the period returns straight away (after yielding) rather than
 * sleeping.
 */
void delayUntil(uint32_t& prevMs, uint32_t ms);

/**
 * @brief Start a background task that runs @p entry(@p arg).
 *
 * The task is expected never to return.
 */
void startTask(void (*entry)(void*), void* arg, const char* name);

/** @brief Non-recursive mutex, blocking take. */
class Mutex {
public:
    Mutex();
    ~Mutex();

    Mutex(const Mutex&)            = delete;
    Mutex& operator=(const Mutex&) = delete;

    void take();
    void give();

private:
    void* handle;
};

/**
 * @brief A WS2812B strip on one ADI port, either the brain's own or an
 *        ADI expander's.
 */
class AdiLed {
public:
    /**
     * @param smartPort  Smart Port of the ADI expander (1-21), or 0 for the
     *                   brain's built-in ADI ports.
     * @param adiPort    ADI port (1-8).
     * @param length     Number of LEDs.
     */
    AdiLed(uint8_t smartPort, uint8_t adiPort, uint8_t length);
    ~AdiLed();

    AdiLed(const AdiLed&)            = delete;
    AdiLed& operator=(const AdiLed&) = delete;

    /** @brief Send @p count 0xRRGGBB colors to the strip, starting at LED 0. */
    void write(const uint32_t* pixels, uint8_t count);

private:
    void* impl;
};

} // namespace platform
} // namespace hitlib
