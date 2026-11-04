"""Fail if a HitLib archive would knock LVGL's display buffers out of alignment.

    python tools/check_cold_bss.py bin/hitlib.a [bin/softfp/hitlib.a ...]

A PROS project with hot/cold linking puts every archive in firmware/ into the
cold image, linked with --sort-section=alignment. Within .bss that orders the
input sections by alignment, largest first, and keeps sections of equal
alignment in link order. LVGL's display buffers, display_buffer_1 and
display_buffer_2 in liblvgl's display.c, are lv_color_t arrays that declare
no alignment of their own, so they open the 1-byte-aligned group. They start
where the 4-aligned group ends, plus every 2-aligned section, plus every
1-aligned section from an archive that links ahead of liblvgl.a. hitlib.a
sorts ahead of liblvgl.a in firmware/, so each byte HitLib puts in that group
moves both buffers by a byte.

LVGL 9 asserts in lv_display_set_buffers() that the buffers are 4-byte
aligned, and the assert spins forever. display_initialize() makes that call
from pros_init(), before the scheduler starts, so the brain sits on a black
screen: no PROS banner, no initialize(), nothing to say why. HitLib 1.5.1
shipped exactly that, from a single function-local `static bool`.

So every non-empty .bss section in the archive has to be aligned to at least
4, which keeps HitLib out of the 1- and 2-aligned groups. Fix a failure with
alignas(4) on the variable it names. That leaves 4-aligned sections smaller
than 4 bytes, such as a 4-aligned bool. Alignment pads before a section, not
after it, so one of those leaves the next section off 4-byte alignment
unless that section is aligned too. That only matters for the last section in the
4-aligned group, and HitLib's never are: liblvgl.a and libpros.a link after
hitlib.a and bring 4-aligned .bss of their own.

This can't catch the same hang from outside HitLib. With Arm GNU 15.2, the
cold image's libstdc++ (format.o) pulls in newlib's lmonetary.o, whose
1-byte, 4-aligned `empty` ends the 4-aligned group and misaligns the buffers
in a project with no HitLib at all. The PROS-bundled 14.3 toolchain doesn't
pull it in. Only aligning the buffers in liblvgl fixes that:
https://github.com/purduesigbots/liblvgl/pull/54

Needs arm-none-eabi-objdump and arm-none-eabi-c++filt on PATH.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MIN_ALIGN = 4

MEMBER_RE = re.compile(r"^(\S+):\s+file format")
# objdump -h: Idx Name Size VMA LMA File-off Algn
SECTION_RE = re.compile(
    r"^\s*\d+\s+(\S+)\s+([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+[0-9a-f]+\s+2\*\*(\d+)")


def demangle(name: str) -> str:
    out = subprocess.run(["arm-none-eabi-c++filt", name], capture_output=True, text=True)
    return out.stdout.strip() or name


def misaligned_bss(archive: Path) -> list[tuple[str, str, int, int]]:
    """Every non-empty .bss section in @p archive aligned to less than MIN_ALIGN,
    as (member, variable, size, alignment)."""
    out = subprocess.run(["arm-none-eabi-objdump", "-h", str(archive)],
                         capture_output=True, text=True, check=True).stdout
    found = []
    member = "?"
    for line in out.splitlines():
        m = MEMBER_RE.match(line)
        if m:
            member = m.group(1)
            continue
        m = SECTION_RE.match(line)
        if not m:
            continue
        name, size, align = m.group(1), int(m.group(2), 16), 1 << int(m.group(3))
        if name != ".bss" and not name.startswith(".bss."):
            continue
        if size == 0 or align >= MIN_ALIGN:
            continue
        variable = demangle(name[len(".bss."):]) if name.startswith(".bss.") else "(.bss)"
        found.append((member, variable, size, align))
    return found


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    failed = False
    for arg in argv:
        archive = Path(arg)
        bad = misaligned_bss(archive)
        if not bad:
            print(f"{archive}: every .bss variable is aligned to {MIN_ALIGN} or more")
            continue
        failed = True
        for member, variable, size, align in bad:
            print(f"::error::{archive} ({member}): {variable} is {size} byte(s) of .bss aligned "
                  f"to {align}. In a PROS cold image it moves LVGL's display buffers off "
                  f"{MIN_ALIGN}-byte alignment and the brain hangs on a black screen. Give "
                  f"it alignas({MIN_ALIGN}); see tools/check_cold_bss.py.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
