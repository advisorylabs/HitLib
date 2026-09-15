"""Compiling against the real VEX SDK, the way a VEXcode project does.

VEXcode builds C++11 with clang and the SDK's own headers, which is a much
older and stricter setup than PROS, so an export that compiles for PROS proves
nothing about VEXcode. These helpers use VEXcode Pro V5's toolchain and the
exact flags its makefile (vex/mkenv.mk) passes, and skip where it isn't
installed.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_PRO = (
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    / "VEX Robotics"
    / "VEXcode Pro V5"
)


@dataclass(frozen=True)
class Vexcode:
    clang: Path
    ld: Path
    sdk: Path

    def flags(self) -> list[str]:
        clang_headers = max((self.sdk / "clang").glob("*/include"))
        gcc = self.sdk / "gcc" / "include"
        return [
            # CFLAGS_CL, CFLAGS_V7 and CXX_FLAGS from vex/mkenv.mk
            "-target", "thumbv7-none-eabi", "-fshort-enums", "-Wno-unknown-attributes",
            "-U__INT32_TYPE__", "-U__UINT32_TYPE__",
            "-D__INT32_TYPE__=long", "-D__UINT32_TYPE__=unsigned long",
            "-march=armv7-a", "-mfpu=neon", "-mfloat-abi=softfp",
            "-Os", "-Wall", "-Werror=return-type",
            "-fno-rtti", "-fno-threadsafe-statics", "-fno-exceptions", "-std=gnu++11",
            "-ffunction-sections", "-fdata-sections", "-DVexV5",
            # TOOL_INC and the SDK include
            f"-I{clang_headers}", f"-I{gcc}", f"-I{gcc / 'c++' / '4.9.3'}",
            f"-I{gcc / 'c++' / '4.9.3' / 'arm-none-eabi' / 'armv7-ar' / 'thumb'}",
            f"-I{self.sdk / 'include'}",
        ]


def find_vexcode() -> Vexcode | None:
    found = Vexcode(
        clang=_PRO / "toolchain" / "vexv5" / "win32" / "clang" / "bin" / "clang.exe",
        ld=_PRO / "toolchain" / "vexv5" / "win32" / "gcc" / "bin" / "arm-none-eabi-ld.exe",
        sdk=_PRO / "sdk" / "vexv5",
    )
    if found.clang.is_file() and found.ld.is_file() and (found.sdk / "include" / "v5_vcs.h").is_file():
        return found
    return None


#: What a VEXcode main.cpp opens with: vex.h is project-local, and pulls in
#: these two SDK headers.
VEX_PROLOGUE = '#include "v5.h"\n#include "v5_vcs.h"\n'


def compile_or_fail(tmp_path: Path, source: str, name: str = "compile_check") -> Path:
    """Compile @p source as a VEXcode translation unit, with -Wall warnings
    treated as failures, and return the object file.

    HitLib's headers are found the way a VEXcode project finds them: through a
    relative `include`, from the root. For its bare-metal target, clang also
    adds a relative `include` as a *system* directory, so a project's headers
    are system headers and their warnings never reach the team; reaching
    HitLib by an absolute path instead would report warnings no VEXcode user
    sees. Files in @p tmp_path, the exports under test among them, get no such
    pass: an export is held to compiling warning-free on its own.
    """
    toolchain = find_vexcode()
    source_path = tmp_path / f"{name}.cpp"
    source_path.write_text(source, encoding="utf-8")
    obj = tmp_path / f"{name}.o"
    result = subprocess.run(
        [
            str(toolchain.clang), *toolchain.flags(),
            "-Iinclude", f"-I{tmp_path}",
            "-c", "-o", str(obj), str(source_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "warning:" not in output, output
    return obj


def partial_link(tmp_path: Path, objects: list[Path]) -> subprocess.CompletedProcess:
    """Link @p objects into one relocatable object - enough to surface a symbol
    defined in more than one of them, without a whole VEXcode program."""
    return subprocess.run(
        [str(find_vexcode().ld), "-r", *map(str, objects), "-o", str(tmp_path / "linked.o")],
        capture_output=True,
        text=True,
        timeout=60,
    )
