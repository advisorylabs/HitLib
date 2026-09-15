"""The VEXcode export: the same design, generated as C++11 against the VEX SDK.

test_codegen.py covers what an export says. This covers how it has to be said
for VEXcode, and compiles the result with VEXcode's own toolchain - the only
check that proves a C++11 export is valid, since PROS's compiler accepts far
more.
"""

import re

import pytest

from pattern_studio import fill_sources
from pattern_studio.codegen import generate_cpp, generate_document_cpp, paste_block, validate_for_export
from pattern_studio.models import AnimationKind, ModeConfig, StrandConfig
from pattern_studio.platforms import Platform
from test_codegen import (
    _drive_heat_config,
    _elaborate_config,
    _music,
    _music_strand,
    _twinkle_and_bitscroll_overlays,
    _two_strands_sharing_a_mode_name,
)
from vexcode_toolchain import VEX_PROLOGUE, compile_or_fail, find_vexcode, partial_link

VEX = Platform.VEXCODE

needs_vexcode = pytest.mark.skipif(find_vexcode() is None, reason="VEXcode Pro V5 toolchain not installed")


def _every_source_config() -> StrandConfig:
    """One Fill mode per catalog source, each on a port its source accepts."""
    cfg = StrandConfig(name="Every Source", length=24, use_profile=True)
    for source_id in fill_sources.ORDER:
        source = fill_sources.get(source_id)
        mode = ModeConfig(name=source.label.replace("(", "").replace(")", ""), priority=10)
        mode.animation.kind = AnimationKind.FILL
        mode.animation.source = source_id
        mode.animation.source_port = 3 if source.port_kind == fill_sources.PORT_ADI else 11
        mode.animation.source_empty = source.empty_default
        mode.animation.source_full = source.full_default
        mode.animation.source_wrap = source.wrap_default
        cfg.profile_modes.append(mode)
    assert validate_for_export(cfg) == []
    return cfg


def _translation_unit(header: str, main: str, defines: tuple[str, ...] = ()) -> str:
    return (
        VEX_PROLOGUE
        + '#include "hitlib/hitapi.hpp"\n'
        + "".join(f"#define {d}\n" for d in defines)
        + f'#include "{header}"\n\n'
        + main
    )


# ============================================================================
# What changes for C++11
# ============================================================================


def test_pros_is_still_what_an_export_is_generated_for_by_default():
    cfg = _elaborate_config()
    assert generate_cpp(cfg) == generate_cpp(cfg, platform=Platform.PROS)
    assert generate_cpp(cfg) != generate_cpp(cfg, platform=VEX)


def test_the_export_uses_nothing_newer_than_cxx11():
    out = generate_document_cpp(
        [_elaborate_config(), _drive_heat_config(), _twinkle_and_bitscroll_overlays()],
        platform=VEX,
    )
    # Inline functions are C++98; an inline variable is C++17.
    assert not re.search(r"^inline (?!void |double )", out, re.MULTILINE)
    # Nested namespace names are C++17, designated initializers C++20.
    assert not re.search(r"namespace \w+::\w+ \{", out)
    assert "{.start" not in out


def test_a_shared_object_is_a_template_member_behind_a_reference_of_its_name():
    out = generate_cpp(StrandConfig(name="My Robot", adi_port=6, length=63), platform=VEX)

    assert (
        "template <typename = void> struct strand_ { static LedStrand value; };\n"
        "template <typename T> LedStrand strand_<T>::value{adiPort, length, refreshMs};\n"
        "static LedStrand& strand = strand_<>::value;"
    ) in out
    # So robot code reads exactly as it does against a PROS export.
    assert "myRobot::strand.activateMode(myRobot::mode::defaultMode);" in paste_block(out)


def test_arrays_carry_their_bound_so_a_reference_can_name_them():
    out = generate_cpp(_elaborate_config(), platform=VEX)
    assert "static const ProfileMode (&modeTable)[5] = modeTable_<>::value;" in out
    assert "static LedGroup (&groups)[1] = groups_<>::value;" in out


def test_splice_regions_are_filled_in_field_by_field():
    # SpliceRegion has default member values, so C++11 has no brace form that
    # builds one.
    out = generate_cpp(_twinkle_and_bitscroll_overlays(), platform=VEX)
    assert (
        "    std::vector<LedStrand::SpliceRegion> regions(2);\n"
        "    regions[0].start = 0;\n"
        "    regions[0].width = 10;\n"
        "    regions[0].kind = LedStrand::SpliceRegionAnimKind::TWINKLE;\n"
        "    regions[0].bgColor = 0x000011;\n"
        "    regions[0].palette = {0xFFFFFF};\n"
    ) in out
    assert "    s.spliceMaskCustom(regions);" in out


def test_the_banner_uses_the_competition_templates_entry_points():
    out = generate_cpp(_elaborate_config(), "leds.hpp", platform=VEX)
    paste = paste_block(out)
    assert "your VEXcode project's include/" in out
    assert "void pre_auton() {" in paste
    assert "void usercontrol() {" in paste
    assert "initialize" not in paste and "opcontrol" not in paste


def test_fill_readers_call_the_vex_sdk_not_pros():
    out = generate_cpp(_every_source_config(), platform=VEX)
    assert "pros" not in out.lower()
    assert '#include "v5.h"' in out and '#include "v5_vcs.h"' in out
    assert "static vex::motor device(vex::PORT11); return device.temperature(vex::temperatureUnits::celsius);" in out
    assert "return vexBatteryCapacityGet();" in out
    # The catalog's rotation range is centidegrees on both platforms.
    assert "device.angle(vex::rotationUnits::deg) * 100.0" in out
    # ADI port C is index 2 on the brain's internal triport.
    assert "static vex::triport ports(vex::PORT22); static vex::pot device(ports.Port[2]);" in out


def test_every_device_source_has_a_vex_reader():
    for source_id in fill_sources.ORDER:
        source = fill_sources.get(source_id)
        if fill_sources.polls_a_device(source_id) and source_id != fill_sources.CUSTOM:
            assert source.vex_read, f"{source_id} has no VEXcode reader"


# ============================================================================
# Compiled with VEXcode's toolchain
# ============================================================================


@needs_vexcode
@pytest.mark.parametrize(
    "config",
    [_elaborate_config, _drive_heat_config, _twinkle_and_bitscroll_overlays, _every_source_config],
    ids=["elaborate", "drive-heat", "sparkle", "every-source"],
)
def test_the_usage_banner_compiles_as_pasted(tmp_path, config):
    header = generate_cpp(config(), "generated_profile.hpp", platform=VEX)
    (tmp_path / "generated_profile.hpp").write_text(header, encoding="utf-8")
    body = "\n".join(line for line in paste_block(header).splitlines() if not line.startswith("#include"))

    compile_or_fail(tmp_path, _translation_unit("generated_profile.hpp", body + "\n"))


@needs_vexcode
def test_a_music_sync_export_compiles(tmp_path):
    (tmp_path / "meter.hpp").write_text(
        generate_cpp(_music_strand(), "meter.hpp", _music(), platform=VEX), encoding="utf-8"
    )
    compile_or_fail(
        tmp_path,
        _translation_unit(
            "meter.hpp",
            "void usercontrol() {\n"
            "    hitlib::studio::begin();\n"
            "    hitlib::profiles::meter::strand.setSensitivity(80);\n"
            "}\n",
        ),
    )


@needs_vexcode
def test_a_multi_strand_document_compiles(tmp_path):
    (tmp_path / "leds.hpp").write_text(
        generate_document_cpp(_two_strands_sharing_a_mode_name(), "leds.hpp", platform=VEX),
        encoding="utf-8",
    )
    compile_or_fail(
        tmp_path,
        _translation_unit(
            "leds.hpp",
            "void usercontrol() {\n"
            "    hitlib::studio::begin();\n"
            "    hitlib::profiles::left::strand.activateMode(hitlib::profiles::left::mode::idle);\n"
            "    hitlib::profiles::right::strand.activateMode(hitlib::profiles::right::mode::idle);\n"
            "}\n",
        ),
    )


@needs_vexcode
def test_the_escape_hatches_compile(tmp_path):
    (tmp_path / "leds.hpp").write_text(generate_cpp(_elaborate_config(), platform=VEX), encoding="utf-8")
    compile_or_fail(
        tmp_path,
        _translation_unit(
            "leds.hpp",
            "hitlib::LedStrand ourOwnStrand(3, 20);\n"
            "hitlib::LedGroup  ourGroup;\n\n"
            "void pre_auton() {\n"
            "    ourGroup.add(&ourOwnStrand);\n"
            "    hitlib::studio::begin(ourGroup);\n"
            "    ourGroup.init(20);\n"
            "    ourGroup.start();\n"
            "}\n",
        ),
        name="into_our_group",
    )
    compile_or_fail(
        tmp_path,
        _translation_unit(
            "leds.hpp",
            "namespace classicDemo = hitlib::profiles::classicDemo;\n\n"
            "hitlib::LedStrand testStrand(classicDemo::adiPort, classicDemo::length,\n"
            "                             classicDemo::refreshMs);\n\n"
            "void pre_auton() {\n"
            "    classicDemo::apply(testStrand);\n"
            "    testStrand.activateMode(classicDemo::mode::idle);\n"
            "}\n",
            defines=("HITLIB_STUDIO_NO_AUTOWIRE",),
        ),
        name="no_autowire",
    )


@needs_vexcode
def test_two_files_including_the_export_share_one_of_everything(tmp_path):
    """What the template-member form is for: main.cpp and an autonomous file
    both including the header must link to one strand, one group and one
    hook, not a copy each."""
    (tmp_path / "leds.hpp").write_text(
        generate_cpp(_every_source_config(), "leds.hpp", platform=VEX), encoding="utf-8"
    )
    main = compile_or_fail(
        tmp_path,
        _translation_unit("leds.hpp", "void pre_auton() { hitlib::studio::begin(); }\n"),
        name="main",
    )
    auton = compile_or_fail(
        tmp_path,
        _translation_unit(
            "leds.hpp",
            "namespace es = hitlib::profiles::everySource;\n"
            "void autonomous() {\n"
            "    es::source::customAssignInCode = [] { return 1.0; };\n"
            "    es::strand.activateMode(es::mode::batteryCapacity);\n"
            "    hitlib::studio::group.off();\n"
            "}\n",
        ),
        name="auton",
    )

    linked = partial_link(tmp_path, [main, auton])
    assert linked.returncode == 0, linked.stderr


@needs_vexcode
def test_the_link_check_does_catch_a_definition_in_two_files(tmp_path):
    # Without this, the test above could pass because the check can't fail.
    (tmp_path / "naive.hpp").write_text(
        '#include "hitlib/hitapi.hpp"\nhitlib::LedStrand naive(1, 10);\n', encoding="utf-8"
    )
    one = compile_or_fail(tmp_path, VEX_PROLOGUE + '#include "naive.hpp"\n', name="one")
    two = compile_or_fail(tmp_path, VEX_PROLOGUE + '#include "naive.hpp"\n', name="two")

    linked = partial_link(tmp_path, [one, two])
    assert linked.returncode != 0
    assert "multiple definition" in linked.stderr
