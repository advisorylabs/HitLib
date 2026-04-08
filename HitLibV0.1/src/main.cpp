#include "main.h"
#include "hitlib/hitapi.hpp"
#include "hitlib/profiles/classic.hpp"

/**
 * Runs initialization code. This occurs as soon as the program is started.
 *
 * All other competition modes are blocked by initialize; it is recommended
 * to keep execution time for this mode under a few seconds.
 */

hitlib::LedStrand strand1(6, 63);
hitlib::LedStrand strand2(7, 63);
hitlib::LedStrand strand3(8, 63);

hitlib::LedGroup group1;
hitlib::LedGroup group2;

void initialize() {
	pros::lcd::initialize();
	pros::lcd::set_text(1, "Hello PROS User!");

	group1.add(&strand1);
	group1.add(&strand3);
	// group1.add(&strand3);
	group2.add(&strand2);
	group1.init(0);
	group2.init(0);

	//group1.attachProfile(&hitlib::profiles::classic);
	//group2.attachProfile(&hitlib::profiles::modern);
	//group1.activateMode(1);
	//group2.activateMode(1);

	group1.start();
	group2.start();

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
void competition_initialize() {
	//group1.activateMode(3);
	//group2.activateMode(3);
}

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

static void layerBlue    (hitlib::LedStrand& s) { s.setColor(0xFFFFFF); }
static void layerRainbow (hitlib::LedStrand& s) { s.rainbow(1); }
static void layerGreen    (hitlib::LedStrand& s) { s.setColor(0x00FF00); }



void opcontrol() {
	pros::Controller master(pros::E_CONTROLLER_MASTER);

	group1.bitscroll({{0xFF0000, 4},
							    {0x0000FF, 4},
							    {0x00FF00, 4}}, 2, true, 0x000000, false, 5, false);
	group2.bitscroll({{0x0000FF, 4},
							    {0x00FF00, 4},
							    {0xFF0000, 4}}, 2, false, 0x000000, false, 5, false);
}