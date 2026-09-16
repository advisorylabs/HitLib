"""HitLib's own sources, as each platform receives them, compiled under that
platform's compiler.

The exports are tested elsewhere; this pins down the library API they and
robot code are written against, and the VEXcode download that renames its
headers, where a C++11, overload-resolution or packaging problem would
otherwise only surface in someone's project.
"""

import re
import subprocess

import pytest

from test_codegen import _find_toolchain_compiler
from vexcode_toolchain import REPO_ROOT, VEX_PROLOGUE, compile_or_fail, find_vexcode, packager, vexcode_package

needs_vexcode = pytest.mark.skipif(find_vexcode() is None, reason="VEXcode Pro V5 toolchain not installed")

#: Every documented way to construct a strand, with `{ext}` for the platform's
#: header extension. Three bare numbers used to be ambiguous between the
#: brain-port and expander constructors, so none of `(6, 63, 20)`,
#: `(2, 1, 63)` or typed constants compiled.
_CONSTRUCTORS = """\
#include "hitlib/hitapi.{ext}"
#include "hitlib/profiles/classic.{ext}"

constexpr uint8_t  port = 6, len = 63, smart = 2;
constexpr uint32_t refresh = 20;

hitlib::LedStrand brain(6, 63);
hitlib::LedStrand brainWithRefresh(6, 63, 20);
hitlib::LedStrand fromConstants{{port, len, refresh}};
hitlib::LedStrand expander({{2, 1}}, 63);
hitlib::LedStrand expanderWithRefresh({{2, 1}}, 63, 20);
hitlib::LedStrand expanderFourNumbers(2, 1, 63, 20);
hitlib::LedStrand expanderFromConstants{{smart, port, len, refresh}};

void useThem() {{
    hitlib::LedGroup group;
    group.add(&brain);
    group.attachProfile(&hitlib::profiles::classic);
    expander.levelSource([] {{ return 42.0; }}, 0.0, 100.0);
}}
"""


@needs_vexcode
def test_every_constructor_form_compiles_for_vexcode(tmp_path):
    compile_or_fail(tmp_path, VEX_PROLOGUE + _CONSTRUCTORS.format(ext="h"))


@pytest.mark.skipif(_find_toolchain_compiler() is None, reason="PROS ARM toolchain not installed")
def test_every_constructor_form_compiles_for_pros(tmp_path):
    source = tmp_path / "constructors.cpp"
    source.write_text(_CONSTRUCTORS.format(ext="hpp"), encoding="utf-8")
    result = subprocess.run(
        [
            _find_toolchain_compiler(), "-c",
            "-mcpu=cortex-a9", "-mfpu=neon-fp16", "-mfloat-abi=hard", "-Os", "-mthumb",
            "-D_POSIX_THREADS", "-D_UNIX98_THREAD_MUTEX_ATTRIBUTES",
            "-D_POSIX_TIMERS", "-D_POSIX_MONOTONIC_CLOCK",
            "-Wall", "--std=gnu++20",
            "-iquote", str(REPO_ROOT / "include"),
            "-o", str(tmp_path / "constructors.o"), str(source),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


# ============================================================================
# The VEXcode download (tools/package_vexcode.py)
# ============================================================================


def test_the_vexcode_download_has_every_header_renamed_to_dot_h():
    package = vexcode_package()
    repo_headers = sorted(
        str(p.relative_to(REPO_ROOT / "include").with_suffix(".h")).replace("\\", "/")
        for p in (REPO_ROOT / "include" / "hitlib").rglob("*.hpp")
    )
    packaged = sorted(
        str(p.relative_to(package / "include")).replace("\\", "/")
        for p in (package / "include").rglob("*")
        if p.is_file()
    )
    assert packaged == repo_headers


def test_the_vexcode_download_has_the_library_sources_and_not_the_example_program():
    names = sorted(p.name for p in (vexcode_package() / "src" / "hitlib").iterdir())
    assert names == sorted(p.name for p in packager.library_sources())
    assert "main.cpp" not in names
    assert "led_strand.cpp" in names and "platform_vexcode.cpp" in names


def test_no_file_in_the_vexcode_download_still_names_a_hitlib_hpp_header():
    stems = packager.header_stems()
    pattern = re.compile(rf"\b({'|'.join(stems)})\.hpp\b")
    for path in vexcode_package().rglob("*"):
        if path.is_file():
            assert not pattern.search(path.read_text(encoding="utf-8")), path


def test_only_hitlib_headers_are_renamed():
    stems = packager.header_stems()
    text = (
        '#include "hitlib/led_strand.hpp"\n'
        '#include "led_profile.hpp"\n'
        '#include "hitlib/profiles/classic.hpp"\n'
        '#include "pros/rtos.hpp"\n'
        '#include "my_platform.hpp"\n'
        " * @file platform.hpp\n"
    )
    assert packager.rename_references(text, stems) == (
        '#include "hitlib/led_strand.h"\n'
        '#include "led_profile.h"\n'
        '#include "hitlib/profiles/classic.h"\n'
        '#include "pros/rtos.hpp"\n'
        '#include "my_platform.hpp"\n'
        " * @file platform.h\n"
    )


@needs_vexcode
@pytest.mark.parametrize("source", [p.name for p in packager.library_sources()])
def test_each_library_source_in_the_vexcode_download_compiles(tmp_path, source):
    # Built as the VEXcode makefile builds src/*/*.cpp. These are the files a
    # team's own build compiles, so a warning here is one they would see.
    code = (vexcode_package() / "src" / "hitlib" / source).read_text(encoding="utf-8")
    compile_or_fail(tmp_path, code, name=source.removesuffix(".cpp"))
