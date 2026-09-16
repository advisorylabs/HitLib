"""Build the VEXcode distribution of HitLib: its sources, ready to copy into a
VEXcode project.

    python tools/package_vexcode.py [OUT_DIR]      (default: vexcode_pkg)

produces

    OUT_DIR/include/hitlib/...   every header, renamed from .hpp to .h
    OUT_DIR/src/hitlib/...       the library's .cpp files

VEXcode has no template system, so it gets source rather than a built archive.
The headers are renamed because VEXcode Pro V5 only recognises `.h` as a
header: an `.hpp` still compiles, but never appears in the project's file tree,
so a team could not open HitLib's documentation from their own editor. Every
reference to a HitLib header - an #include or a doc-comment example - is
rewritten to match. PROS keeps the `.hpp` names; this is a copy.

Pattern Studio's VEXcode exports include the `.h` names, so the two have to
agree; its tests build this package and compile against it.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

HEADER_ROOT = Path("include") / "hitlib"
SOURCE_DIR = Path("src")

#: The robot-program entry files in src/, which belong to this repo's example
#: project and not the library - the same list the Makefile's
#: EXCLUDE_SRC_FROM_LIB keeps out of the PROS archive.
ENTRY_POINT_STEMS = frozenset({"main", "autonomous", "opcontrol", "initialize"})


def header_stems(repo: Path = REPO_ROOT) -> list[str]:
    """The name, without extension, of every HitLib header."""
    return sorted({p.stem for p in (repo / HEADER_ROOT).rglob("*.hpp")})


def library_sources(repo: Path = REPO_ROOT) -> list[Path]:
    """The .cpp files that make up the library."""
    return sorted(p for p in (repo / SOURCE_DIR).glob("*.cpp") if p.stem not in ENTRY_POINT_STEMS)


def _reference_pattern(stems: list[str]) -> re.Pattern[str]:
    # A HitLib header's name followed by .hpp, alone or at the end of a path
    # ("hitlib/profiles/classic.hpp"), but not the tail of another name
    # ("my_platform.hpp") and not an .hpp header that isn't HitLib's, such as
    # "pros/rtos.hpp".
    names = "|".join(re.escape(s) for s in sorted(stems, key=len, reverse=True))
    return re.compile(rf"(?<![\w.])({names})\.hpp\b")


def rename_references(text: str, stems: list[str]) -> str:
    """@p text with every reference to a HitLib `.hpp` header made `.h`."""
    return _reference_pattern(stems).sub(r"\1.h", text)


def build(out_dir: Path, repo: Path = REPO_ROOT) -> Path:
    """Write the package into @p out_dir, replacing anything already there."""
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    stems = header_stems(repo)

    for header in sorted((repo / HEADER_ROOT).rglob("*.hpp")):
        target = out_dir / header.relative_to(repo).with_suffix(".h")
        target.parent.mkdir(parents=True, exist_ok=True)
        _write(target, rename_references(_read(header), stems))

    for source in library_sources(repo):
        target = out_dir / SOURCE_DIR / "hitlib" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        _write(target, rename_references(_read(source), stems))

    leftovers = [
        str(p.relative_to(out_dir))
        for p in out_dir.rglob("*")
        if p.is_file() and _reference_pattern(stems).search(_read(p))
    ]
    if leftovers:
        raise RuntimeError(f"HitLib .hpp references survived packaging in: {', '.join(leftovers)}")
    return out_dir


def _read(path: Path) -> str:
    # newline="" keeps each file's own line endings.
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


if __name__ == "__main__":
    destination = build(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("vexcode_pkg"))
    print(f"VEXcode package written to {destination}")
