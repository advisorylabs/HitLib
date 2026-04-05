#include "main.h"
#include "hitlib/hitapi.hpp"

/**
 * Runs initialization code. This occurs as soon as the program is started.
 *
 * All other competition modes are blocked by initialize; it is recommended
 * to keep execution time for this mode under a few seconds.
 */

hitlib::LedStrand strand1(5, 63);
hitlib::LedStrand strand2(6, 63);
hitlib::LedManager ledManager;


leds::MatchProfile myProfiles[] {
	leds::profiles::classic,
	leds::profiles::modern,
	leds::profiles::showy
};


void initialize() {
	pros::lcd::initialize();
	pros::lcd::set_text(1, "Hello PROS User!");

	ledManager.addStrand(&strand1);
	ledManager.addStrand(&strand2);
	ledManager.initialize(10);
	leds::init(ledManager, myProfiles, 3);
	leds::setAlliance(leds::Alliance::BLUE);
	leds::setMatchProfile(1);
	pros::Task ledTask([]() {
		while (true) {
			leds::periodic();
			pros::delay(20);
		}
	});
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
	leds::setAlliance(leds::Alliance::BLUE);
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
void opcontrol() {
	pros::Controller master(pros::E_CONTROLLER_MASTER);
	pros::MotorGroup left_mg({1, -2, 3});    // Creates a motor group with forwards ports 1 & 3 and reversed port 2
	pros::MotorGroup right_mg({-4, 5, -6});  // Creates a motor group with forwards port 5 and reversed ports 4 & 6


	while (true) {

		// Arcade control scheme
		int dir = master.get_analog(ANALOG_LEFT_Y);    // Gets amount forward/backward from left joystick
		int turn = master.get_analog(ANALOG_RIGHT_X);  // Gets the turn left/right from right joystick
		left_mg.move(dir - turn);                      // Sets left motor voltage
		right_mg.move(dir + turn);                     // Sets right motor voltage

		// Scraper button — holds override on while pressed
        if (master.get_digital(pros::E_CONTROLLER_DIGITAL_L1)) {
            leds::setScraperOverride(true);
        } else {
            leds::setScraperOverride(false);
        }

        // Scoring button — 1.5 second flash on press
        if (master.get_digital_new_press(pros::E_CONTROLLER_DIGITAL_R1)) {
            leds::playScoringFx();
        }

        // Endgame button — triggers endgame sequence
        if (master.get_digital_new_press(pros::E_CONTROLLER_DIGITAL_UP)) {
            leds::startEndgame();
        } else if (master.get_digital_new_press(pros::E_CONTROLLER_DIGITAL_DOWN)) {
			leds::stopEndgame();
		}



		pros::delay(20);                               // Run for 20 ms then update
	}
}