import shutil
import subprocess
from pathlib import Path

import pytest

from pattern_studio import fill_sources
from pattern_studio.codegen import (
    generate_cpp,
    generate_document_cpp,
    suggested_header_name,
    validate_document_for_export,
    validate_for_export,
)
from pattern_studio.models import (
    AnimationConfig,
    AnimationKind,
    GaugeBlendKind,
    GaugeStopConfig,
    GaugeStyleKind,
    ModeConfig,
    MusicConfig,
    OverlayAnimationConfig,
    OverlayAnimationKind,
    PhaseConfig,
    SpliceMaskConfig,
    SpliceModeKind,
    SpliceRegionConfig,
    StrandConfig,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _elaborate_config() -> StrandConfig:
    cfg = StrandConfig(name="Classic Demo", length=30, use_profile=True)

    idle = ModeConfig(name="Idle", priority=10)
    idle.animation.kind = AnimationKind.FLOW
    idle.animation.color = 0xFF00DD

    red = ModeConfig(name="Red", priority=20)
    red.animation.kind = AnimationKind.PULSE
    red.animation.color = 0xFF0000
    red.animation.run_length = 5

    sparkle = ModeConfig(name="Sparkle", priority=15)
    sparkle.animation.kind = AnimationKind.TWINKLE
    sparkle.animation.palette = [0xFF0000, 0x00FF00, 0x0000FF]
    sparkle.splice.enabled = True
    sparkle.splice.sections = 2

    bits = ModeConfig(name="Bits", priority=12)
    bits.animation.kind = AnimationKind.BITSCROLL
    bits.animation.color = 0x00FFAA

    endgame = ModeConfig(name="Endgame", priority=100)
    p1 = PhaseConfig(name="Warn", duration_ms=1500)
    p1.animation.kind = AnimationKind.FLASH
    p1.animation.color = 0xFFFF00
    p2 = PhaseConfig(name="White", duration_ms=8500)
    p2.animation.kind = AnimationKind.SOLID
    p2.animation.color = 0xFFFFFF
    endgame.phases = [p1, p2]

    cfg.profile_modes = [idle, red, sparkle, bits, endgame]
    cfg.active_mode_indices = [0]
    return cfg


# ============================================================================
# Validation
# ============================================================================


def test_valid_config_has_no_errors():
    assert validate_for_export(_elaborate_config()) == []


def test_out_of_range_length_and_brightness_are_caught():
    cfg = StrandConfig(length=200, brightness=150)
    errors = validate_for_export(cfg)
    assert any("length" in e.lower() for e in errors)
    assert any("brightness" in e.lower() for e in errors)


def test_duplicate_mode_names_are_caught():
    cfg = StrandConfig(use_profile=True)
    cfg.profile_modes = [ModeConfig(name="Idle"), ModeConfig(name="Idle")]
    errors = validate_for_export(cfg)
    assert any("duplicate" in e.lower() for e in errors)


def test_priority_out_of_range_is_caught():
    cfg = StrandConfig(use_profile=True)
    cfg.profile_modes = [ModeConfig(name="Idle", priority=999)]
    errors = validate_for_export(cfg)
    assert any("priority" in e.lower() for e in errors)


def test_twinkle_without_palette_is_caught():
    cfg = StrandConfig(use_profile=True)
    mode = ModeConfig(name="Sparkle")
    mode.animation.kind = AnimationKind.TWINKLE
    mode.animation.palette = []
    cfg.profile_modes = [mode]
    errors = validate_for_export(cfg)
    assert any("twinkle" in e.lower() for e in errors)


def test_single_animation_without_profile_validates_via_synthetic_mode():
    cfg = StrandConfig(use_profile=False)
    cfg.animation.kind = AnimationKind.RAINBOW
    assert validate_for_export(cfg) == []


def test_custom_splice_with_no_regions_is_caught():
    cfg = StrandConfig(use_profile=False)
    cfg.splice.enabled = True
    cfg.splice.mode = SpliceModeKind.CUSTOM
    errors = validate_for_export(cfg)
    assert any("region" in e.lower() for e in errors)


def test_custom_splice_region_zero_width_is_caught():
    cfg = StrandConfig(use_profile=False)
    cfg.splice.enabled = True
    cfg.splice.mode = SpliceModeKind.CUSTOM
    cfg.splice.regions = [SpliceRegionConfig(start=0, width=0)]
    errors = validate_for_export(cfg)
    assert any("width" in e.lower() for e in errors)


# ============================================================================
# Codegen shape / identifier handling
# ============================================================================


def test_generates_expected_structure():
    out = generate_cpp(_elaborate_config())
    assert "namespace hitlib::profiles {" in out
    assert "namespace classicDemo {" in out
    assert "inline const ProfileMode modeTable[] = {" in out
    assert 'inline const Profile profile = {"Classic Demo", modeTable, 5};' in out
    assert "s.flow(0xFF00DD, 0x0000FF, 1, false);" in out  # color2 left at its AnimationConfig default
    assert "s.twinkle({0xFF0000, 0x00FF00, 0x0000FF}, 30, 16, 0x000000);" in out
    assert "s.spliceMask(2, false, false, 400, 0x000000, false);" in out
    assert "LedStrand::BitScrollSegment{0x00FFAA, 3}" in out
    assert "endgameSeq.start(s);" in out
    assert "endgameSeq.update(s);" in out
    assert '{"Endgame", 100, detail::endgameActivate, detail::endgameTick}' in out


def test_hardware_settings_are_emitted_as_constants():
    # The whole point: the LedStrand on the robot is built from the same
    # numbers the preview ran against, rather than retyped from the GUI.
    cfg = StrandConfig(name="Wired", adi_port=6, length=63, refresh_ms=25, brightness=40)
    out = generate_cpp(cfg)
    assert "constexpr uint8_t  adiPort    = 6;" in out
    assert "constexpr uint8_t  length     = 63;" in out
    assert "constexpr uint32_t refreshMs  = 25;" in out
    assert "constexpr uint8_t  brightness = 40;" in out
    assert "smartPort" not in out  # not on an expander


def test_expander_strand_emits_smart_port():
    cfg = StrandConfig(name="Expanded", smart_port=2, adi_port=1)
    out = generate_cpp(cfg)
    assert "constexpr uint8_t  smartPort  = 2;" in out
    # The usage banner has to pick the 4-argument constructor to match.
    assert "hitlib::LedStrand expandedStrand(expanded::smartPort, expanded::adiPort," in out


def test_mode_index_constants_are_named_and_numbered():
    out = generate_cpp(_elaborate_config())
    assert "namespace mode {" in out
    assert 'constexpr uint8_t idle    = 0;  // "Idle", priority 10' in out
    assert 'constexpr uint8_t endgame = 4;  // "Endgame", priority 100' in out


def test_mode_constants_cannot_shadow_the_namespace_members():
    # A mode named "Length" would otherwise emit `constexpr uint8_t length`
    # into the same namespace as the strand's own `length`.
    cfg = StrandConfig(use_profile=True)
    mode = ModeConfig(name="Length")
    mode.animation.kind = AnimationKind.SOLID
    cfg.profile_modes = [mode]
    out = generate_cpp(cfg)
    mode_block = out[out.index("namespace mode {") : out.index("}  // namespace mode")]
    assert "constexpr uint8_t length =" not in mode_block
    assert "length2" in mode_block


def test_usage_banner_names_the_header_it_was_saved_as():
    out = generate_cpp(StrandConfig(name="My Robot"), "my_robot.hpp")
    assert '#include "my_robot.hpp"' in out
    assert "namespace myRobot = hitlib::profiles::myRobot;" in out
    # The declared variable must not shadow the alias declared right above it.
    assert "hitlib::LedStrand myRobotStrand(myRobot::adiPort" in out


def test_suggested_header_name_is_snake_case():
    assert suggested_header_name("My Robot") == "my_robot.hpp"
    assert suggested_header_name("") == "profile.hpp"


def test_apply_helper_sets_brightness_and_attaches():
    out = generate_cpp(StrandConfig(name="Solo", brightness=55))
    assert "inline void apply(LedStrand& s) { s.setBrightness(brightness); s.attachProfile(&profile); }" in out
    assert "inline void apply(LedGroup& g) { g.setBrightness(brightness); g.attachProfile(&profile); }" in out


# ============================================================================
# Whole-document export
# ============================================================================


def _two_strands_sharing_a_mode_name() -> list[StrandConfig]:
    configs = []
    for name, port in (("Left", 6), ("Right", 7)):
        cfg = StrandConfig(name=name, adi_port=port, use_profile=True)
        mode = ModeConfig(name="Idle", priority=10)
        mode.animation.kind = AnimationKind.RAINBOW
        cfg.profile_modes = [mode]
        configs.append(cfg)
    return configs


def test_document_export_gives_each_strand_its_own_namespace():
    out = generate_document_cpp(_two_strands_sharing_a_mode_name())
    assert "namespace left {" in out
    assert "namespace right {" in out
    # One shared ProfileMode/Profile name per namespace is fine precisely
    # because the namespaces keep them apart.
    assert out.count("inline const ProfileMode modeTable[] = {") == 2
    assert out.count("constexpr uint8_t idle = 0;") == 2


def test_document_export_rejects_duplicate_strand_names():
    configs = _two_strands_sharing_a_mode_name()
    configs[1].name = "Left"
    errors = validate_document_for_export(configs)
    assert any("unique name" in e for e in errors)


def test_document_export_rejects_empty_document():
    assert validate_document_for_export([]) != []


def test_document_validation_labels_errors_by_strand():
    configs = _two_strands_sharing_a_mode_name()
    configs[1].length = 200
    errors = validate_document_for_export(configs)
    assert any(e.startswith("[Right]") and "length" in e.lower() for e in errors)


def test_duplicate_mode_display_names_get_unique_identifiers():
    cfg = StrandConfig(use_profile=True)
    a = ModeConfig(name="Go!")
    a.animation.kind = AnimationKind.SOLID
    b = ModeConfig(name="Go?")  # sanitizes to the same base identifier as "Go!"
    b.animation.kind = AnimationKind.SOLID
    cfg.profile_modes = [a, b]
    cfg.active_mode_indices = []

    out = generate_cpp(cfg)
    assert "inline void go(LedStrand& s)" in out
    assert "inline void go2(LedStrand& s)" in out


def test_single_animation_export_wraps_as_default_mode():
    cfg = StrandConfig(name="Solo", use_profile=False)
    cfg.animation.kind = AnimationKind.RAINBOW
    cfg.animation.speed = 2
    out = generate_cpp(cfg)
    assert "s.rainbow(2);" in out
    assert '{"Default", 100,' in out
    # The synthetic mode's *display* name is "Default", but the generated
    # function identifier must not be the bare word "default". That's a
    # reserved C++ keyword and won't compile.
    assert "void default(" not in out
    assert "void defaultMode(" in out


def test_mode_named_with_a_cpp_keyword_gets_a_safe_identifier():
    cfg = StrandConfig(use_profile=True)
    mode = ModeConfig(name="switch")
    mode.animation.kind = AnimationKind.SOLID
    cfg.profile_modes = [mode]
    out = generate_cpp(cfg)
    assert "void switch(" not in out
    assert "void switchMode(" in out


def test_split_splice_with_overlay_emits_overlay_setup_before_splice_call():
    cfg = StrandConfig(name="Overlay Split", use_profile=False)
    cfg.animation.kind = AnimationKind.FLOW
    cfg.splice.enabled = True
    cfg.splice.mode = SpliceModeKind.SPLIT
    cfg.splice.sections = 1
    cfg.splice.use_overlay = True
    cfg.splice.overlay.kind = OverlayAnimationKind.RAINBOW
    cfg.splice.overlay.speed = 3
    out = generate_cpp(cfg)
    assert "s.overlayRainbow(3);" in out
    assert "s.spliceMask(1, false, false, 400, 0x000000, true);" in out
    # Overlay must be primed before the splice call that reveals it.
    assert out.index("s.overlayRainbow(3);") < out.index("s.spliceMask(1,")


def test_custom_splice_emits_independent_region_literals_and_never_shared_overlay():
    cfg = StrandConfig(name="Custom Splice", use_profile=False)
    cfg.animation.kind = AnimationKind.RAINBOW
    cfg.splice.enabled = True
    cfg.splice.mode = SpliceModeKind.CUSTOM

    region1 = SpliceRegionConfig(start=0, width=5)
    region1.animation.kind = OverlayAnimationKind.SOLID
    region1.animation.color = 0xFF0000
    region2 = SpliceRegionConfig(start=20, width=8)
    region2.animation.kind = OverlayAnimationKind.RAINBOW
    region2.animation.speed = 2
    cfg.splice.regions = [region1, region2]

    # Split mode's shared overlay config is unrelated to Custom mode, so
    # setting it must not leak an overlaySetColor()/overlayRainbow() call,
    # since each region below carries its own independent animation instead.
    cfg.splice.overlay.kind = OverlayAnimationKind.SOLID
    cfg.splice.overlay.color = 0x00FF00

    out = generate_cpp(cfg)
    assert "overlaySetColor" not in out
    assert "overlayRainbow" not in out
    # One region per line: a row of gauges with a color scale each is far too
    # long to read on one, so every custom mask is emitted the same way.
    assert (
        "    s.spliceMaskCustom({\n"
        "        {.start = 0, .width = 5, .kind = LedStrand::SpliceRegionAnimKind::SOLID, "
        ".color = 0xFF0000, .color2 = 0x0000FF, .bgColor = 0x000000, .runLength = 5, .speed = 1, "
        ".onMs = 250, .offMs = 250},\n"
        "        {.start = 20, .width = 8, .kind = LedStrand::SpliceRegionAnimKind::RAINBOW, "
        ".color = 0xFFFFFF, .color2 = 0x0000FF, .bgColor = 0x000000, .runLength = 5, .speed = 2, "
        ".onMs = 250, .offMs = 250},\n"
        "    });"
    ) in out


def _find_toolchain_compiler() -> str | None:
    found = shutil.which("arm-none-eabi-g++")
    if found:
        return found
    # PROS installs its bundled toolchain outside PATH on Windows.
    candidates = list(
        Path.home().glob(
            "AppData/Roaming/Code/User/globalStorage/sigbots.pros/install/"
            "pros-toolchain-windows/usr/bin/arm-none-eabi-g++.exe"
        )
    )
    return str(candidates[0]) if candidates else None


def _compile_or_fail(tmp_path: Path, header: str, main: str) -> None:
    """Compile `main` against `header` with the real PROS ARM toolchain and the
    real hitlib headers. The only check that proves an export is valid C++."""
    (tmp_path / "generated_profile.hpp").write_text(header, encoding="utf-8")
    source_path = tmp_path / "compile_check.cpp"
    source_path.write_text(
        '#include "hitlib/hitapi.hpp"\n#include "generated_profile.hpp"\n\n' + main,
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            _find_toolchain_compiler(), "-c",
            "-mcpu=cortex-a9", "-mfpu=neon-fp16", "-mfloat-abi=hard", "-Os", "-g", "-mthumb",
            "-D_POSIX_THREADS", "-D_UNIX98_THREAD_MUTEX_ATTRIBUTES",
            "-D_POSIX_TIMERS", "-D_POSIX_MONOTONIC_CLOCK",
            "-D_PROS_INCLUDE_LIBLVGL_LLEMU_H", "-D_PROS_INCLUDE_LIBLVGL_LLEMU_HPP",
            "-Wno-psabi", "-ffunction-sections", "-fdata-sections", "-funwind-tables",
            "--std=gnu++20",
            "-iquote", str(REPO_ROOT / "include"),
            "-iquote", str(tmp_path),
            "-o", str(tmp_path / "compile_check.o"),
            str(source_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert (tmp_path / "compile_check.o").exists()


@pytest.mark.skipif(_find_toolchain_compiler() is None, reason="PROS ARM toolchain not installed")
def test_generated_profile_compiles_against_real_hitlib_headers(tmp_path):
    _compile_or_fail(
        tmp_path,
        generate_cpp(_elaborate_config()),
        "namespace classicDemo = hitlib::profiles::classicDemo;\n\n"
        "hitlib::LedStrand testStrand(classicDemo::adiPort, classicDemo::length,\n"
        "                            classicDemo::refreshMs);\n\n"
        "void useProfile() {\n"
        "    classicDemo::apply(testStrand);\n"
        "    testStrand.activateMode(classicDemo::mode::idle);\n"
        "    testStrand.activateModeTimed(classicDemo::mode::endgame, 1500);\n"
        "}\n",
    )


@pytest.mark.skipif(_find_toolchain_compiler() is None, reason="PROS ARM toolchain not installed")
def test_usage_banner_from_the_export_actually_compiles(tmp_path):
    """The banner is copy-paste instructions, so compile the paste."""
    cfg = _elaborate_config()
    header = generate_cpp(cfg, "generated_profile.hpp")

    banner = [
        line[len("//        ") :]
        for line in header.splitlines()
        if line.startswith("//        ")
    ]
    # Drop the two #include lines: _compile_or_fail() emits them itself.
    body = "\n".join(line for line in banner if not line.startswith("#include"))
    assert "void initialize() {" in body

    _compile_or_fail(tmp_path, header, body + "\n")


@pytest.mark.skipif(_find_toolchain_compiler() is None, reason="PROS ARM toolchain not installed")
def test_two_strands_sharing_mode_names_compile_in_one_document_export(tmp_path):
    # Regression test: exported per-strand, two "Idle" modes both produced
    # `inline void idle(LedStrand&)` in namespace hitlib::profiles and could
    # not be included in the same translation unit.
    _compile_or_fail(
        tmp_path,
        generate_document_cpp(_two_strands_sharing_a_mode_name()),
        "namespace left = hitlib::profiles::left;\n"
        "namespace right = hitlib::profiles::right;\n\n"
        "hitlib::LedStrand leftStrand(left::adiPort, left::length, left::refreshMs);\n"
        "hitlib::LedStrand rightStrand(right::adiPort, right::length, right::refreshMs);\n\n"
        "void useProfiles() {\n"
        "    left::apply(leftStrand);\n"
        "    right::apply(rightStrand);\n"
        "    leftStrand.activateMode(left::mode::idle);\n"
        "    rightStrand.activateMode(right::mode::idle);\n"
        "}\n",
    )


@pytest.mark.skipif(_find_toolchain_compiler() is None, reason="PROS ARM toolchain not installed")
def test_non_profile_export_compiles_despite_default_mode_name(tmp_path):
    # Regression test: _effective_modes() names the synthetic single-animation
    # mode "Default", which used to sanitize to the bare identifier `default`,
    # a reserved C++ keyword, and failed to compile.
    cfg = StrandConfig(name="Solo", use_profile=False)
    cfg.animation.kind = AnimationKind.RAINBOW

    _compile_or_fail(
        tmp_path,
        generate_cpp(cfg),
        "namespace solo = hitlib::profiles::solo;\n\n"
        "hitlib::LedStrand testStrand(solo::adiPort, solo::length, solo::refreshMs);\n\n"
        "void useProfile() {\n"
        "    solo::apply(testStrand);\n"
        "    testStrand.activateMode(solo::mode::defaultMode);\n"
        "}\n",
    )


# ============================================================================
# Music sync
# ============================================================================


_DEFAULT_BANDS = {
    "bass": [0, 40, 255, 128, 0],
    "mid": [4, 4, 4, 4, 4],
    "treble": [9, 200, 9, 0, 9],
    "full": [7, 7, 7, 7, 7],
}


def _music(**kwargs) -> MusicConfig:
    kwargs.setdefault("bands", dict(_DEFAULT_BANDS))
    return MusicConfig(name="Anthem", **kwargs)


def _music_strand(**animation) -> StrandConfig:
    cfg = StrandConfig(name="Meter", length=24)
    cfg.animation.kind = AnimationKind.MUSIC
    cfg.animation.color = 0x00FF88
    cfg.animation.color2 = 0xFF0044
    cfg.animation.gradient = True
    cfg.animation.sensitivity = 140
    for key, value in animation.items():
        setattr(cfg.animation, key, value)
    return cfg


def test_music_sync_emits_the_sample_table_and_the_call():
    out = generate_cpp(_music_strand(), "meter.hpp", _music())

    assert "namespace music {" in out
    assert "inline const uint8_t anthemBassSamples[] = {" in out
    assert "inline const LedStrand::MusicTrack anthemBass = {anthemBassSamples, 5, 25};" in out
    # Only the band this design uses: three more tables would be dead weight.
    assert "anthemTrebleSamples" not in out
    assert (
        "s.musicSync(music::anthemBass, 0x00FF88, 0xFF0044, true, 0x000000, false, 140, false);"
    ) in out


def test_loop_comes_from_the_song():
    music = _music()
    music.loop = True
    assert "140, true);" in generate_cpp(_music_strand(), "meter.hpp", music)


def test_the_sample_table_is_only_emitted_when_something_uses_it():
    plain = StrandConfig(name="Plain")
    plain.animation.kind = AnimationKind.RAINBOW
    out = generate_cpp(plain, "plain.hpp", _music())

    assert "namespace music {" not in out
    assert "anthemBassSamples" not in out


def test_strands_on_one_band_share_its_table():
    left = _music_strand()
    left.name = "Left"
    right = _music_strand(invert=True, gradient=False)
    right.name = "Right"
    out = generate_document_cpp([left, right], "led_profiles.hpp", _music())

    assert out.count("inline const uint8_t anthemBassSamples[] = {") == 1
    assert out.count("s.musicSync(music::anthemBass,") == 2


def test_strands_on_different_bands_each_get_their_own_table():
    low = _music_strand()
    low.name = "Low"
    high = _music_strand(band="treble")
    high.name = "High"
    out = generate_document_cpp([low, high], "led_profiles.hpp", _music())

    assert "anthemBassSamples" in out
    assert "anthemTrebleSamples" in out
    assert "anthemMidSamples" not in out
    assert "s.musicSync(music::anthemBass," in out
    assert "s.musicSync(music::anthemTreble," in out


def test_a_band_the_song_has_nothing_in_is_an_export_error():
    silent = _music(bands={"bass": [0, 40, 255], "treble": []})
    assert any("treble" in e for e in validate_for_export(_music_strand(band="treble"), silent))


def test_music_sync_without_a_song_is_an_export_error():
    errors = validate_for_export(_music_strand())
    assert any("Music Sync needs a song" in e for e in errors)
    assert validate_for_export(_music_strand(), _music()) == []


def test_music_sync_inside_a_sequenced_phase_reaches_the_table():
    cfg = StrandConfig(name="Show", use_profile=True)
    mode = ModeConfig(name="Endgame", priority=90)
    phase = PhaseConfig(name="Drop", duration_ms=4000)
    phase.animation.kind = AnimationKind.MUSIC
    mode.phases = [phase]
    cfg.profile_modes = [mode]

    assert validate_for_export(cfg) != []
    out = generate_cpp(cfg, "show.hpp", _music())
    assert "s.musicSync(music::anthemBass," in out


@pytest.mark.skipif(_find_toolchain_compiler() is None, reason="PROS ARM toolchain not installed")
def test_a_music_sync_export_compiles_against_real_hitlib_headers(tmp_path):
    _compile_or_fail(
        tmp_path,
        generate_cpp(_music_strand(), "meter.hpp", _music()),
        "namespace meter = hitlib::profiles::meter;\n\n"
        "hitlib::LedStrand testStrand(meter::adiPort, meter::length, meter::refreshMs);\n\n"
        "void useProfile() {\n"
        "    meter::apply(testStrand);\n"
        "    testStrand.activateMode(meter::mode::defaultMode);\n"
        "    testStrand.setSensitivity(180);\n"
        "    testStrand.musicSeek(0);\n"
        "}\n",
    )


# ============================================================================
# Fill meters
# ============================================================================


def _fill_strand(name: str = "Gauge", **animation) -> StrandConfig:
    cfg = StrandConfig(name=name, length=24)
    cfg.animation.kind = AnimationKind.FILL
    cfg.animation.color = 0x00FF00
    cfg.animation.color2 = 0xFF0000
    cfg.animation.gradient = True
    cfg.animation.source = "motor_temp"
    cfg.animation.source_port = 11
    cfg.animation.source_empty = 20
    cfg.animation.source_full = 70
    for key, value in animation.items():
        setattr(cfg.animation, key, value)
    return cfg


def test_a_device_source_emits_a_reader_and_wires_it_to_the_meter():
    out = generate_cpp(_fill_strand(smoothing=25), "gauge.hpp")

    assert "namespace source {" in out
    assert (
        "inline double motorTemperature11() { static pros::Motor device(11); "
        "return device.get_temperature(); }"
    ) in out
    assert "s.levelFill(0x00FF00, 0xFF0000, true, 0x000000, false);" in out
    assert "s.levelSource(source::motorTemperature11, 20.0, 70.0, false, 25);" in out


def test_a_device_source_pulls_in_the_header_it_needs():
    out = generate_cpp(_fill_strand(), "gauge.hpp")
    assert '#include "pros/motors.hpp"' in out
    # And only the ones actually read - a design with no fill carries none.
    plain = StrandConfig(name="Plain")
    plain.animation.kind = AnimationKind.RAINBOW
    assert "pros/motors.hpp" not in generate_cpp(plain, "plain.hpp")


def test_a_portless_source_needs_no_port_in_its_reader():
    out = generate_cpp(_fill_strand(source="battery", source_empty=0, source_full=100), "batt.hpp")
    assert "inline double batteryCapacity() { return pros::battery::get_capacity(); }" in out
    assert "s.levelSource(source::batteryCapacity, 0.0, 100.0, false, 0);" in out
    assert '#include "pros/misc.hpp"' in out


def test_two_meters_on_the_same_device_share_one_reader():
    cfg = StrandConfig(name="Twin", use_profile=True)
    low = ModeConfig(name="Low", priority=10)
    low.animation = _fill_strand().animation
    high = ModeConfig(name="High", priority=20)
    high.animation = _fill_strand(invert=True).animation
    cfg.profile_modes = [low, high]

    out = generate_cpp(cfg, "twin.hpp")
    assert out.count("inline double motorTemperature11()") == 1
    assert out.count("s.levelSource(source::motorTemperature11,") == 2


def test_the_same_device_on_a_different_port_is_a_different_reader():
    cfg = StrandConfig(name="Pair", use_profile=True)
    left = ModeConfig(name="Left", priority=10)
    left.animation = _fill_strand(source_port=11).animation
    right = ModeConfig(name="Right", priority=20)
    right.animation = _fill_strand(source_port=12).animation
    cfg.profile_modes = [left, right]

    out = generate_cpp(cfg, "pair.hpp")
    assert "inline double motorTemperature11()" in out
    assert "inline double motorTemperature12()" in out


def test_a_custom_source_leaves_a_hook_and_says_how_to_assign_it():
    # The hook is named after the mode it feeds, since that is what the user
    # has to line it up with when assigning it.
    cfg = StrandConfig(name="Arm", use_profile=True)
    mode = ModeConfig(name="Overheat", priority=90)
    mode.animation = _fill_strand(source="custom").animation
    cfg.profile_modes = [mode]
    out = generate_cpp(cfg, "custom.hpp")

    assert "inline LedStrand::LevelFn overheat = nullptr;" in out
    assert "s.levelSource(source::overheat, 20.0, 70.0, false, 0);" in out
    # The banner is the copy-paste instructions, so the assignment belongs there.
    assert "//        arm::source::overheat = [] { return someValue(); };" in out


def test_a_manual_meter_is_a_levelfill_with_nothing_attached():
    out = generate_cpp(_fill_strand(source="manual"), "manual.hpp")

    assert "s.levelFill(0x00FF00, 0xFF0000, true, 0x000000, false);" in out
    assert "s.levelSource(" not in out
    assert "namespace source {" not in out
    assert ".setLevel(128);" in out  # the banner says how to move it


def test_each_strand_gets_its_own_source_namespace():
    left = _fill_strand(name="Left")
    right = _fill_strand(name="Right", source="battery", source_empty=0, source_full=100)
    out = generate_document_cpp([left, right], "led_profiles.hpp")

    assert out.count("namespace source {") == 2
    assert "inline double motorTemperature11()" in out
    assert "inline double batteryCapacity() { return pros::battery::get_capacity(); }" in out


def test_a_fill_inside_a_sequenced_phase_gets_its_own_reader():
    cfg = StrandConfig(name="Show", use_profile=True)
    mode = ModeConfig(name="Endgame", priority=90)
    phase = PhaseConfig(name="Heat", duration_ms=4000)
    phase.animation = _fill_strand(source="custom").animation
    mode.phases = [phase]
    cfg.profile_modes = [mode]

    out = generate_cpp(cfg, "show.hpp")
    assert "inline LedStrand::LevelFn endgameHeat = nullptr;" in out
    assert "s.levelSource(source::endgameHeat," in out


def test_a_port_outside_its_devices_range_is_an_export_error():
    errors = validate_for_export(_fill_strand(source_port=44))
    assert any("port between 1 and 21" in e for e in errors)
    assert validate_for_export(_fill_strand(source_port=21)) == []


def test_an_adi_source_is_checked_against_the_adi_range():
    assert any("port between 1 and 8" in e for e in validate_for_export(
        _fill_strand(source="potentiometer", source_port=9, source_empty=0, source_full=250)))


def test_a_meter_with_no_range_to_fill_across_is_an_export_error():
    errors = validate_for_export(_fill_strand(source_empty=50, source_full=50))
    assert any("no range to fill across" in e for e in errors)
    # Manual meters have no range to speak of, so they are not held to it.
    assert validate_for_export(_fill_strand(source="manual", source_empty=0, source_full=0)) == []


def test_out_of_range_smoothing_is_an_export_error():
    assert any("smoothing" in e for e in validate_for_export(_fill_strand(smoothing=100)))


@pytest.mark.skipif(_find_toolchain_compiler() is None, reason="PROS ARM toolchain not installed")
def test_a_fill_export_compiles_against_real_hitlib_headers(tmp_path):
    """Every kind of source at once: a device reader, a custom hook to assign,
    and a hand-driven meter."""
    cfg = StrandConfig(name="Gauges", length=24, use_profile=True)
    heat = ModeConfig(name="Heat", priority=20)
    heat.animation = _fill_strand().animation
    spin = ModeConfig(name="Spin", priority=30)
    spin.animation = _fill_strand(source="rotation", source_empty=0, source_full=36000,
                                  source_wrap=True).animation
    custom = ModeConfig(name="Pressure", priority=40)
    custom.animation = _fill_strand(source="custom", source_empty=0, source_full=100).animation
    hand = ModeConfig(name="Hand", priority=10)
    hand.animation = _fill_strand(source="manual").animation
    cfg.profile_modes = [heat, spin, custom, hand]

    _compile_or_fail(
        tmp_path,
        generate_cpp(cfg, "gauges.hpp"),
        "namespace gauges = hitlib::profiles::gauges;\n"
        "\n"
        "hitlib::LedStrand testStrand(gauges::adiPort, gauges::length, gauges::refreshMs);\n"
        "\n"
        "void useProfile() {\n"
        "    gauges::apply(testStrand);\n"
        "    gauges::source::pressure = [] { return 42.0; };\n"
        "    testStrand.activateMode(gauges::mode::heat);\n"
        "    testStrand.activateMode(gauges::mode::hand);\n"
        "    testStrand.setLevel(128);\n"
        "}\n",
    )


@pytest.mark.skipif(_find_toolchain_compiler() is None, reason="PROS ARM toolchain not installed")
def test_every_fill_source_in_the_catalog_compiles(tmp_path):
    """One mode per source, compiled for real.

    The catalog names PROS types and calls as strings, which nothing else would
    catch a typo in until someone picked that source and tried to build.
    """
    cfg = StrandConfig(name="Every Source", length=24, use_profile=True)
    for source_id in fill_sources.ORDER:
        source = fill_sources.get(source_id)
        mode = ModeConfig(name=source.label.replace("(", "").replace(")", ""), priority=10)
        mode.animation.kind = AnimationKind.FILL
        mode.animation.source = source_id
        mode.animation.source_port = 1 if source.port_kind == fill_sources.PORT_ADI else 11
        mode.animation.source_empty = source.empty_default
        mode.animation.source_full = source.full_default
        mode.animation.source_wrap = source.wrap_default
        cfg.profile_modes.append(mode)

    assert validate_for_export(cfg) == []
    _compile_or_fail(
        tmp_path,
        generate_cpp(cfg, "every_source.hpp"),
        "namespace es = hitlib::profiles::everySource;\n"
        "\n"
        "hitlib::LedStrand testStrand(es::adiPort, es::length, es::refreshMs);\n"
        "\n"
        "void useProfile() {\n"
        "    es::apply(testStrand);\n"
        "    es::source::customAssignInCode = [] { return 1.0; };\n"
        "}\n",
    )


# ============================================================================
# Gauge regions - a strip split into one meter per motor
# ============================================================================


def _gauge_region(start: int, port: int, *, source: str = "motor_temp",
                  width: int = 9) -> SpliceRegionConfig:
    animation = OverlayAnimationConfig(
        kind=OverlayAnimationKind.GAUGE,
        source=source,
        source_port=port,
        source_empty=20,
        source_full=70,
        smoothing=80,
        stops=[GaugeStopConfig(at=at, color=color)
               for at, color in fill_sources.default_stops("motor_temp")],
    )
    return SpliceRegionConfig(start=start, width=width, animation=animation)


def _drive_heat_config(ports=(1, 2, 3, 11, 12, 13)) -> StrandConfig:
    """The six-motor drivebase design, as Pattern Studio would hand it over."""
    return StrandConfig(
        name="Drive Heat",
        adi_port=6,
        length=60,
        refresh_ms=25,
        brightness=40,
        animation=AnimationConfig(kind=AnimationKind.OFF),
        splice=SpliceMaskConfig(
            enabled=True,
            mode=SpliceModeKind.CUSTOM,
            regions=[_gauge_region(i * 10, port) for i, port in enumerate(ports)],
        ),
    )


def test_a_gauge_region_exports_its_reader_its_range_and_its_scale():
    out = generate_cpp(_gauge_strand())

    assert "static pros::Motor device(7); return device.get_temperature();" in out
    assert "LedStrand::SpliceRegionAnimKind::GAUGE" in out
    assert ".emptyAt = 20.0, .fullAt = 70.0" in out
    assert ".style = LedStrand::GaugeStyle::HEAT" in out
    assert ".blend = LedStrand::GaugeBlend::LERP" in out
    # The scale goes out in the reading's own units, not pre-mapped to 0-255,
    # so the generated file still says what the numbers mean.
    assert ".stops = {{20.0, 0x00FF00}, {45.0, 0xFFFF00}, {55.0, 0xFF7000}, " \
           "{60.0, 0xFF2000}, {65.0, 0xFF0000}, {70.0, 0xFF00FF}}" in out


def _gauge_strand() -> StrandConfig:
    return StrandConfig(
        name="One Gauge",
        length=20,
        animation=AnimationConfig(kind=AnimationKind.OFF),
        splice=SpliceMaskConfig(
            enabled=True, mode=SpliceModeKind.CUSTOM, regions=[_gauge_region(0, 7)]
        ),
    )


def test_six_gauges_export_six_readers_and_one_include():
    out = generate_cpp(_drive_heat_config())

    for port in (1, 2, 3, 11, 12, 13):
        assert f"static pros::Motor device({port});" in out
    assert out.count('#include "pros/motors.hpp"') == 1
    assert out.count("LedStrand::SpliceRegionAnimKind::GAUGE") == 6


def test_two_gauges_on_the_same_motor_share_one_reader():
    """Two segments watching the same motor are the same reading, and one
    function holding one device says that better than two copies would."""
    cfg = _drive_heat_config(ports=(1, 1))
    out = generate_cpp(cfg)

    assert out.count("static pros::Motor device(1);") == 1
    assert out.count("source::motorTemperature1") == 2


def test_a_manual_gauge_exports_no_reader():
    cfg = _gauge_strand()
    cfg.splice.regions[0].animation.source = fill_sources.MANUAL
    out = generate_cpp(cfg)

    assert ".read =" not in out
    assert "LedStrand::SpliceRegionAnimKind::GAUGE" in out
    # Still a gauge with a scale - the robot's code drives it with
    # setRegionLevel() instead of a reader doing it.
    assert ".stops = {" in out


def test_a_custom_gauge_leaves_a_hook_named_after_its_segment():
    """Six identical "assign this" hooks would be useless; each says which
    segment it feeds."""
    cfg = _drive_heat_config(ports=(1, 2))
    for region in cfg.splice.regions:
        region.animation.source = fill_sources.CUSTOM
    out = generate_cpp(cfg)

    assert "segment 1" in out.lower() or "segment1" in out.lower()
    assert out.count("inline LedStrand::LevelFn") == 2


def test_a_gauge_on_a_bad_port_is_caught_before_export():
    cfg = _gauge_strand()
    cfg.splice.regions[0].animation.source_port = 40
    errors = validate_for_export(cfg)
    assert any("port between 1 and 21" in e for e in errors)


def test_a_gauge_with_no_range_is_caught_before_export():
    """Empty At == Full At leaves the color stops nowhere to spread across."""
    cfg = _gauge_strand()
    cfg.splice.regions[0].animation.source_full = 20
    errors = validate_for_export(cfg)
    assert any("no range" in e for e in errors)


def test_a_valid_drivebase_design_exports_clean():
    assert validate_for_export(_drive_heat_config()) == []


def test_bar_style_and_step_blend_reach_the_export():
    cfg = _gauge_strand()
    cfg.splice.regions[0].animation.style = GaugeStyleKind.BAR
    cfg.splice.regions[0].animation.blend = GaugeBlendKind.STEP
    cfg.splice.regions[0].animation.invert = True
    out = generate_cpp(cfg)

    assert ".style = LedStrand::GaugeStyle::BAR" in out
    assert ".blend = LedStrand::GaugeBlend::STEP" in out
    assert ".invert = true" in out


@pytest.mark.skipif(_find_toolchain_compiler() is None, reason="PROS ARM toolchain not installed")
def test_the_drivebase_heat_export_compiles_against_real_hitlib_headers(tmp_path):
    """The one check that proves a six-gauge export is valid C++."""
    _compile_or_fail(
        tmp_path,
        generate_cpp(_drive_heat_config()),
        "namespace driveHeat = hitlib::profiles::driveHeat;\n\n"
        "hitlib::LedStrand underStrand(driveHeat::adiPort, driveHeat::length,\n"
        "                             driveHeat::refreshMs);\n"
        "hitlib::LedGroup group;\n\n"
        "void initialize() {\n"
        "    group.add(&underStrand);\n"
        "    group.init();\n"
        "    group.start();\n"
        "    driveHeat::apply(group);\n"
        "    group.activateMode(driveHeat::mode::defaultMode);\n"
        "}\n",
    )
