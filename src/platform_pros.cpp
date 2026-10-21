#include "hitlib/platform.hpp"

#if HITLIB_PLATFORM_PROS

#include "pros/adi.hpp"
#include "pros/rtos.h"

namespace hitlib {
namespace platform {

uint32_t millis() { return pros::c::millis(); }

void delay(uint32_t ms) { pros::c::delay(ms); }

void delayUntil(uint32_t& prevMs, uint32_t ms) { pros::c::task_delay_until(&prevMs, ms); }

void startTask(void (*entry)(void*), void* arg, const char* name) {
    pros::c::task_create(entry, arg, TASK_PRIORITY_DEFAULT, TASK_STACK_DEPTH_DEFAULT, name);
}

Mutex::Mutex() : handle(pros::c::mutex_create()) {}
Mutex::~Mutex() { pros::c::mutex_delete(static_cast<pros::mutex_t>(handle)); }
void Mutex::take() { pros::c::mutex_take(static_cast<pros::mutex_t>(handle), TIMEOUT_MAX); }
void Mutex::give() { pros::c::mutex_give(static_cast<pros::mutex_t>(handle)); }

AdiLed::AdiLed(uint8_t smartPort, uint8_t adiPort, uint8_t length) {
    if (smartPort == 0) impl = new pros::adi::Led(adiPort, length);
    else                impl = new pros::adi::Led(pros::adi::ext_adi_port_pair_t{smartPort, adiPort}, length);
}

AdiLed::~AdiLed() { delete static_cast<pros::adi::Led*>(impl); }

// Fills the Led's buffer through operator[], which only touches memory, and
// sends it once with update(). set_pixel() would hand the whole strip to
// VEXos on every call - 21 transfers for a 20-LED frame - and rejects every
// pixel of a 64-LED strip outright, leaving update() to send it black.
void AdiLed::write(const uint32_t* pixels, uint8_t count) {
    pros::adi::Led* led = static_cast<pros::adi::Led*>(impl);
    // operator[] doesn't check its index the way set_pixel() did.
    if (count > led->length()) count = (uint8_t)led->length();
    for (uint8_t i = 0; i < count; ++i) (*led)[i] = pixels[i];
    led->update();
}

} // namespace platform
} // namespace hitlib

#endif // HITLIB_PLATFORM_PROS
