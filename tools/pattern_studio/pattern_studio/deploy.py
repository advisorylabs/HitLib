"""Writes an export into a PROS project's include/ directory.

A project is identified by `project.pros` at its root. `include/` is already on
the compiler's search path, so a header dropped there needs no build changes.

Nothing here edits existing code: main.cpp varies too much between projects to
rewrite safely, so the lines that use the export are left for the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: What marks a directory as the root of a PROS project.
MANIFEST = "project.pros"

#: Where a PROS project's headers live, relative to that root.
INCLUDE_DIR = "include"

#: The header that is present exactly when HitLib is installed into a project.
_HITLIB_MARKER = "hitlib/hitapi.hpp"

#: Shown when it isn't. Separate lines so no entry has to carry an escape.
#: Names no version: a pinned URL goes stale at the next release.
INSTALL_HINT_LINES = (
    "Download the template from",
    "    https://github.com/advisorylabs/HitLib/releases",
    "then, in the project:",
    "    pros c fetch <the downloaded .zip>",
    "    pros c apply hitlib",
)

#: How far up from a dropped file to look for the manifest. Bounded so an
#: unrelated file does not walk up to the drive root and match something.
_MAX_DEPTH = 6


def find_project_root(path: Path) -> Path | None:
    """The PROS project @p path belongs to, or None.

    Accepts the project folder, its `project.pros`, or any file inside it.
    """
    # A relative path has no usable .parents to walk, so resolve first.
    path = Path(path).resolve()
    start = path if path.is_dir() else path.parent
    for candidate in [start, *start.parents][:_MAX_DEPTH]:
        if (candidate / MANIFEST).is_file():
            return candidate
    return None


@dataclass(frozen=True)
class Project:
    """A PROS project the app can deploy into, and whether it is ready to."""

    root: Path

    @property
    def include_dir(self) -> Path:
        return self.root / INCLUDE_DIR

    @property
    def has_hitlib(self) -> bool:
        """Whether HitLib is installed here.

        Checked before writing: without the library the export produces only
        missing-include errors, which say nothing about the actual fix.
        """
        return (self.include_dir / _HITLIB_MARKER).is_file()

    def header_path(self, header_name: str) -> Path:
        return self.include_dir / header_name

    def deploy(self, header_name: str, code: str) -> Path:
        """Write @p code as @p header_name in the project's include/ directory.

        Overwrites in place so a re-export lands on the file the project
        already includes, rather than beside it as a second copy.
        """
        self.include_dir.mkdir(parents=True, exist_ok=True)
        destination = self.header_path(header_name)
        destination.write_text(code, encoding="utf-8")
        return destination


def open_project(path: Path) -> Project | None:
    """A Project for whatever was dropped or picked, or None if it isn't one."""
    root = find_project_root(Path(path))
    return Project(root) if root is not None else None
