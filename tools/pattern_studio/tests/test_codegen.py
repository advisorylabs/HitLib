import shutil
import subprocess
from pathlib import Path

import pytest

from pattern_studio.codegen import generate_cpp, validate_for_export
from pattern_studio.models import AnimationKind, ModeConfig, PhaseConfig, StrandConfig

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


# ============================================================================
# Codegen shape / identifier handling
# ============================================================================


def test_generates_expected_structure():
    out = generate_cpp(_elaborate_config())
    assert "namespace hitlib::profiles {" in out
    assert "inline const ProfileMode classicDemoModes[] = {" in out
    assert 'inline const Profile classicDemo = {"Classic Demo", classicDemoModes, 5};' in out
    assert "s.flow(0xFF00DD, 0x0000FF, 1, false);" in out  # color2 left at its AnimationConfig default
    assert "s.twinkle({0xFF0000, 0x00FF00, 0x0000FF}, 30, 16, 0x000000);" in out
    assert "s.spliceMask(2, false, false, 400, 0x000000);" in out
    assert "LedStrand::BitScrollSegment{0x00FFAA, 3}" in out
    assert "endgameSeq.start(s);" in out
    assert "endgameSeq.update(s);" in out
    assert '{"Endgame", 100, endgameActivate, endgameTick}' in out


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


# ============================================================================
# Real compile check -- the actual correctness bar per the project plan.
# Skips gracefully if the PROS ARM toolchain isn't installed on this machine.
# ============================================================================


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


@pytest.mark.skipif(_find_toolchain_compiler() is None, reason="PROS ARM toolchain not installed")
def test_generated_profile_compiles_against_real_hitlib_headers(tmp_path):
    compiler = _find_toolchain_compiler()
    header_path = tmp_path / "generated_profile.hpp"
    header_path.write_text(generate_cpp(_elaborate_config()), encoding="utf-8")

    source_path = tmp_path / "compile_check.cpp"
    source_path.write_text(
        '#include "hitlib/hitapi.hpp"\n'
        '#include "generated_profile.hpp"\n\n'
        "hitlib::LedStrand testStrand(1, 30);\n\n"
        "void useProfile() {\n"
        "    testStrand.attachProfile(&hitlib::profiles::classicDemo);\n"
        "    testStrand.activateMode(0);\n"
        "}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            compiler, "-c",
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
