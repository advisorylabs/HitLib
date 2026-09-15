"""HitLib's own public headers, compiled under both platforms' compilers.

The exports are tested elsewhere; this pins down the library API they and
robot code are written against, where a C++11 or overload-resolution problem
would otherwise only surface in someone's project.
"""

import subprocess

import pytest

from test_codegen import _find_toolchain_compiler
from vexcode_toolchain import REPO_ROOT, VEX_PROLOGUE, compile_or_fail, find_vexcode

#: Every documented way to construct a strand. Three bare numbers used to be
#: ambiguous between the brain-port and expander constructors, so none of
#: `(6, 63, 20)`, `(2, 1, 63)` or typed constants compiled.
_CONSTRUCTORS = """\
#include "hitlib/hitapi.hpp"
#include "hitlib/profiles/classic.hpp"

constexpr uint8_t  port = 6, len = 63, smart = 2;
constexpr uint32_t refresh = 20;

hitlib::LedStrand brain(6, 63);
hitlib::LedStrand brainWithRefresh(6, 63, 20);
hitlib::LedStrand fromConstants{port, len, refresh};
hitlib::LedStrand expander({2, 1}, 63);
hitlib::LedStrand expanderWithRefresh({2, 1}, 63, 20);
hitlib::LedStrand expanderFourNumbers(2, 1, 63, 20);
hitlib::LedStrand expanderFromConstants{smart, port, len, refresh};

void useThem() {
    hitlib::LedGroup group;
    group.add(&brain);
    group.attachProfile(&hitlib::profiles::classic);
    expander.levelSource([] { return 42.0; }, 0.0, 100.0);
}
"""


@pytest.mark.skipif(find_vexcode() is None, reason="VEXcode Pro V5 toolchain not installed")
def test_every_constructor_form_compiles_for_vexcode(tmp_path):
    compile_or_fail(tmp_path, VEX_PROLOGUE + _CONSTRUCTORS)


@pytest.mark.skipif(_find_toolchain_compiler() is None, reason="PROS ARM toolchain not installed")
def test_every_constructor_form_compiles_for_pros(tmp_path):
    source = tmp_path / "constructors.cpp"
    source.write_text(_CONSTRUCTORS, encoding="utf-8")
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
