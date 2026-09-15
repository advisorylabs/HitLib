#include "hitlib/platform.hpp"

#if HITLIB_PLATFORM_VEXCODE

#include "v5.h"
#include "v5_vcs.h"

// Present in the V5 runtime (libv5rt.a) but left out of the SDK headers, and
// the same call PROS's adi::Led makes underneath.
extern "C" void vexDeviceAdiAddrLedSet(V5_DeviceT device, uint32_t port, uint32_t* pData,
                                       uint32_t nOffset, uint32_t nLength, uint32_t options);

namespace hitlib {
namespace platform {

uint32_t millis() { return vex::timer::system(); }

void delay(uint32_t ms) { vex::this_thread::sleep_for(ms); }

void delayUntil(uint32_t& prevMs, uint32_t ms) {
    uint32_t wake = prevMs + ms;
    int32_t  left = (int32_t)(wake - vex::timer::system());
    // Still yield on an overrun, so a loop that is always late can't starve
    // the rest of the program.
    if (left > 0) vex::this_thread::sleep_for((uint32_t)left);
    else          vex::this_thread::yield();
    prevMs = wake;
}

void startTask(void (*entry)(void*), void* arg, const char*) {
    // Never destroyed: the task runs for the life of the program, and the SDK
    // doesn't promise what ~thread() does to a running thread.
    new vex::thread(entry, arg);
}

Mutex::Mutex() : handle(new vex::mutex()) {}
Mutex::~Mutex() { delete static_cast<vex::mutex*>(handle); }
void Mutex::take() { static_cast<vex::mutex*>(handle)->lock(); }
void Mutex::give() { static_cast<vex::mutex*>(handle)->unlock(); }

namespace {

struct LedPort {
    V5_DeviceT device;
    uint32_t   port;
    uint32_t*  pixels;
    uint8_t    length;
};

} // namespace

AdiLed::AdiLed(uint8_t smartPort, uint8_t adiPort, uint8_t length) {
    // The brain's built-in ADI ports are the internal device on PORT22.
    int32_t index = (smartPort == 0) ? vex::PORT22 : vex::PORT1 + (smartPort - 1);

    LedPort* p = new LedPort;
    p->device  = vexDeviceGetByIndex((uint32_t)index);
    p->port    = (uint32_t)(adiPort - 1);
    p->pixels  = new uint32_t[length]();
    p->length  = length;
    vexDeviceAdiPortConfigSet(p->device, p->port, kAdiPortTypeDigitalOut);
    impl = p;
}

AdiLed::~AdiLed() {
    LedPort* p = static_cast<LedPort*>(impl);
    delete[] p->pixels;
    delete p;
}

void AdiLed::write(const uint32_t* pixels, uint8_t count) {
    LedPort* p = static_cast<LedPort*>(impl);
    if (count > p->length) count = p->length;
    for (uint8_t i = 0; i < count; ++i) p->pixels[i] = pixels[i];
    vexDeviceAdiAddrLedSet(p->device, p->port, p->pixels, 0, count, 0);
}

} // namespace platform
} // namespace hitlib

#endif // HITLIB_PLATFORM_VEXCODE
