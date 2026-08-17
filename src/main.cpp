#include "main.h"
#include "hitlib/hitapi.hpp"

/**
 * ============================================================================
 * HitLib Demo
 * ============================================================================
 *
 * This program shows off the four things you need to know to get started
 * with HitLib:
 *
 *   1. Boot self-test  - prove the LEDs are physically working before you
 *                         trust any animation logic.
 *   2. Independent control - two strands, each animated separately.
 *   3. Masking          - overlaying a splice mask to blank out sections of
 *                         a running animation.
 *   4. Sequencer        - a timed, looping sequence of animation phases
 *                         driven by repeated update() calls.
 *
 * Wiring: two WS2812B strands on ADI ports 6 and 7 (lol).
 */

hitlib::LedStrand strandLeft(6, 63);
hitlib::LedStrand strandRight(7, 63);

// LedGroup is just a convenience for setup/teardown that touches every
// strand at once (init, start, boot test). Once the demo starts, strandLeft
// and strandRight are driven independently below.
hitlib::LedGroup group;

// --- Sequencer demo ---------------------------------------------------------
// A Sequencer walks through a fixed list of timed phases, calling each
// phase's function once when it begins. Here it cycles strandRight through
// red -> green -> blue, looping forever. It must be advanced every tick by
// calling update(), which we do from the opcontrol loop below.

void seqRed(hitlib::LedStrand& s)   { s.setColor(0xFF0000); }
void seqGreen(hitlib::LedStrand& s) { s.setColor(0x00FF00); }
void seqBlue(hitlib::LedStrand& s)  { s.setColor(0x0000FF); }

static const hitlib::Sequencer::Phase colorCyclePhases[] = {
	{1000, seqRed},
	{1000, seqGreen},
	{1000, seqBlue},
};
static hitlib::Sequencer colorCycleSeq(colorCyclePhases, 3);

/**
 * Runs initialization code. This occurs as soon as the program is started.
 *
 * All other competition modes are blocked by initialize; it is recommended
 * to keep execution time for this mode under a few seconds.
 */
void initialize() {
	pros::lcd::initialize();
	pros::lcd::set_text(1, "HitLib Demo");

	group.add(&strandLeft);
	group.add(&strandRight);
	group.init();
	group.start();

	// --- 1. Boot self-test ---
	// Solid white on both strands for a few seconds. If any LEDs are dark,
	// dim, or the wrong color right now, it's a wiring/hardware problem, not
	// an animation bug - check this before debugging anything else below.
	pros::lcd::set_text(2, "Boot test: solid white");
	group.setColor(0xFFFFFF);
	pros::delay(3000);
	group.off();
}

/**
 * Runs while the robot is in the disabled state of Field Management System or
 * the VEX Competition Switch, following either autonomous or opcontrol. When
 * the robot is enabled, this task will exit.
 */
void disabled() {}

/**
 * Runs after initialize(), and before autonomous when connected to the Field
 * Management System or the VEX Competition Switch. This is intended for
 * competition-specific initialization routines, such as an autonomous selector
 * on the LCD.
 *
 * This task will exit when the robot is enabled and autonomous or opcontrol
 * starts.
 */
void competition_initialize() {}

/**
 * Runs the user autonomous code. This function will be started in its own task
 * with the default priority and stack size whenever the robot is enabled via
 * the Field Management System or the VEX Competition Switch in the autonomous
 * mode. Alternatively, this function may be called in initialize or opcontrol
 * for non-competition testing purposes.
 *
 * If the robot is disabled or communications is lost, the autonomous task
 * will be stopped. Re-enabling the robot will restart the task, not re-start it
 * from where it left off.
 */
void autonomous() {}

/**
 * Runs the operator control code. This function will be started in its own task
 * with the default priority and stack size whenever the robot is enabled via
 * the Field Management System or the VEX Competition Switch in the operator
 * control mode.
 *
 * If no competition control is connected, this function will run immediately
 * following initialize().
 *
 * If the robot is disabled or communications is lost, the
 * operator control task will be stopped. Re-enabling the robot will restart the
 * task, not resume it from where it left off.
 */
void opcontrol() {
	pros::lcd::set_text(2, "HitLib Demo running");

	// --- 2. Independent control ---
	// strandLeft and strandRight are commanded separately, each runs its own
	// animation without affecting the other.
	strandLeft.rainbow(1);

	// --- 3. Masking ---
	// spliceMask divides strandLeft into alternating bins and overrides them
	// with bgColor, blanking out chunks of the rainbow instead of letting it
	// show across the whole strip. `alternating` makes it swap which bins
	// are blanked every altPeriodMs, so the mask itself animates.
	strandLeft.spliceMask(3, false, true, 400);

	// --- 4. Sequencer ---
	// strandRight ignores the rainbow style entirely and instead hands its
	// color over to the Sequencer defined above.
	colorCycleSeq.start(strandRight);

	while (true) {
		// Sequencers don't advance on their own - update() must be called
		// regularly so it can check whether the current phase has expired.
		colorCycleSeq.update(strandRight);
		pros::delay(20);
	}
}
