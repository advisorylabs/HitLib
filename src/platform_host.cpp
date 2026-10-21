#include "hitlib/platform.hpp"

#if HITLIB_PLATFORM_HOST

#include <cstring>

// The desktop test backend. See the host namespace in platform.hpp for why it
// exists and why the clock is driven rather than running on its own.
//
// Nothing here is thread-safe, and deliberately so: the harness drives tick()
// from one thread, and a mutex that really locked would only hide a mistake in
// the test rather than in the library. A Mutex only keeps count, so a test can
// ask what would have blocked on the robot.

namespace hitlib {
namespace platform {

namespace {

uint32_t s_nowMs = 0;

uint32_t s_frame[64];
uint8_t  s_frameCount = 0;
bool     s_wrote      = false;
uint32_t s_writeCount = 0;

void (*s_onWrite)()         = nullptr;
void (*s_onDelay)(uint32_t) = nullptr;

uint32_t s_heldMutexes   = 0;
uint32_t s_contendedTake = 0;

// Tasks startTask() was handed. Only a harness ever runs one, see runTask().
struct Task {
    void (*entry)(void*);
    void* arg;
};
constexpr uint32_t MAX_TASKS = 16;
Task     s_tasks[MAX_TASKS];
uint32_t s_taskCount = 0;

// runTask()'s budget: delayUntil() throws this once the task has gone round
// its loop as many times as it was allowed to, unwinding it back out.
struct TaskStopped {};
bool     s_inTask     = false;
uint32_t s_cyclesLeft = 0;

} // namespace

uint32_t millis() { return s_nowMs; }

// Not a sleep, and not a clock advance unless a test's onDelay() hook makes it
// one. LedStrand paces the real ADI driver with delay(12); off-hardware there
// is nothing to pace, and advancing the clock here by default would run every
// frame ahead of the simulator's.
void delay(uint32_t ms) {
    if (s_onDelay) s_onDelay(ms);
}

// The honest desktop reading of "sleep until prevMs + ms": the loop's period
// elapses, so the clock lands there. A body that overran still lands on the
// deadline rather than going backwards.
void delayUntil(uint32_t& prevMs, uint32_t ms) {
    prevMs += ms;
    if ((int32_t)(prevMs - s_nowMs) > 0) s_nowMs = prevMs;
    if (s_inTask && --s_cyclesLeft == 0) throw TaskStopped();
}

// Started tasks never run on their own. The parity harness calls
// LedStrand::tick() itself, one tick at a time, so a real thread would race the
// clock it is trying to step deterministically. runTask() runs one on demand.
void startTask(void (*entry)(void*), void* arg, const char*) {
    if (s_taskCount < MAX_TASKS) s_tasks[s_taskCount] = {entry, arg};
    ++s_taskCount;
}

// The handle is a take count rather than a flag: a take while held is the one
// that would have waited, and the give that follows it is its own, so the
// holder still holds the lock afterwards.
Mutex::Mutex() : handle(new uint32_t(0)) {}
Mutex::~Mutex() { delete static_cast<uint32_t*>(handle); }

void Mutex::take() {
    uint32_t& depth = *static_cast<uint32_t*>(handle);
    if (depth++ > 0) ++s_contendedTake;
    else             ++s_heldMutexes;
}

void Mutex::give() {
    uint32_t& depth = *static_cast<uint32_t*>(handle);
    if (depth == 0) return;
    if (--depth == 0) --s_heldMutexes;
}

AdiLed::AdiLed(uint8_t, uint8_t, uint8_t) : impl(nullptr) {}
AdiLed::~AdiLed() {}

void AdiLed::write(const uint32_t* pixels, uint8_t count) {
    if (count > 64) count = 64;
    if (pixels != nullptr && count > 0) {
        std::memcpy(s_frame, pixels, (size_t)count * sizeof(uint32_t));
    }
    s_frameCount = count;
    s_wrote      = true;
    ++s_writeCount;
    if (s_onWrite) s_onWrite();
}

namespace host {

void setMillis(uint32_t ms) { s_nowMs = ms; }

void advanceMillis(uint32_t ms) { s_nowMs += ms; }

void reset() {
    s_nowMs          = 0;
    s_frameCount     = 0;
    s_wrote          = false;
    s_writeCount     = 0;
    s_onWrite        = nullptr;
    s_onDelay        = nullptr;
    s_contendedTake  = 0;
    s_taskCount      = 0;
}

const uint32_t* lastFrame() { return s_wrote ? s_frame : nullptr; }

uint8_t lastFrameCount() { return s_frameCount; }

uint32_t writeCount() { return s_writeCount; }

void onWrite(void (*hook)()) { s_onWrite = hook; }

void onDelay(void (*hook)(uint32_t)) { s_onDelay = hook; }

uint32_t heldMutexes() { return s_heldMutexes; }

uint32_t contendedTakes() { return s_contendedTake; }

uint32_t taskCount() { return s_taskCount; }

void runTask(uint32_t index, uint32_t cycles) {
    if (index >= s_taskCount || index >= MAX_TASKS || cycles == 0) return;
    s_inTask     = true;
    s_cyclesLeft = cycles;
    try {
        s_tasks[index].entry(s_tasks[index].arg);
    } catch (const TaskStopped&) {
    }
    s_inTask = false;
}

} // namespace host

} // namespace platform
} // namespace hitlib

#endif // HITLIB_PLATFORM_HOST
