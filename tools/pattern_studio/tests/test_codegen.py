import shutil
import subprocess
from pathlib import Path

import pytest

from pattern_studio.codegen import (
    generate_cpp,
    generate_document_cpp,
    suggested_header_name,
    validate_document_for_export,
    validate_for_export,
)
from pattern_studio.models import (
    AnimationKind,
    ModeConfig,
    OverlayAnimationKind,
    PhaseConfig,
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
    assert (
        "s.spliceMaskCustom({"
        "{.start = 0, .width = 5, .kind = LedStrand::SpliceRegionAnimKind::SOLID, "
        ".color = 0xFF0000, .color2 = 0x0000FF, .bgColor = 0x000000, .runLength = 5, .speed = 1, "
        ".onMs = 250, .offMs = 250}, "
        "{.start = 20, .width = 8, .kind = LedStrand::SpliceRegionAnimKind::RAINBOW, "
        ".color = 0xFFFFFF, .color2 = 0x0000FF, .bgColor = 0x000000, .runLength = 5, .speed = 2, "
        ".onMs = 250, .offMs = 250}"
        "});"
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
