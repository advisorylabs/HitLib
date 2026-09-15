"""Writes an export into a robot project's include/ directory.

Three kinds of project are recognised by the file at their root:

  * PROS - `project.pros`.
  * VEXcode Pro V5 (and VEXcode V5) - a `<name>.v5code` file.
  * The VEX VS Code extension - `.vscode/vex_project_settings.json`.

All three keep headers in `include/`, which is already on the compiler's search
path, so a header dropped there needs no build changes. Which kind it is
decides the dialect the export is generated in, and its file extension.

VEXcode Pro V5 builds whatever is in the folder, but its file tree shows only
the files its `.v5code` lists, plus any new file it notices appearing while
the project is open, so a header deployed while VEXcode was closed would be
invisible. Deploy therefore adds the header to that list the first time. If
VEXcode has the project open at that moment it may reload it once.

Nothing here edits existing code: main.cpp varies too much between projects to
rewrite safely, so the lines that use the export are left for the user.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .platforms import Platform

#: What marks a directory as the root of a PROS project.
MANIFEST = "project.pros"

#: The VS Code extension's project file, relative to the project root.
VSCODE_SETTINGS = Path(".vscode") / "vex_project_settings.json"

#: VEXcode's project file extension for a V5 C++ project. Python projects use
#: another (.v5python), so a match here is already the right language.
V5CODE_SUFFIX = ".v5code"

#: Where a project's headers live, relative to its root.
INCLUDE_DIR = "include"

#: The header that is present exactly when HitLib is installed into a project,
#: less its extension: .hpp from the PROS template, .h from the VEXcode download.
_HITLIB_MARKER_STEM = "hitlib/hitapi"

#: What Deploy writes, before the platform's extension. Fixed, not derived from
#: the design name: renaming a strand would otherwise deploy under a new name
#: and leave the previous header in place, still included by main.cpp. The
#: file also defines hitlib::studio, so a project can only carry one.
STUDIO_HEADER_STEM = "hitlib_studio"

#: Shown when it isn't, per platform. Separate lines so no entry has to carry
#: an escape. Names no version: a pinned URL goes stale at the next release.
_INSTALL_HINTS = {
    Platform.PROS: (
        "Download the template from",
        "    https://github.com/advisorylabs/HitLib/releases",
        "then, in the project:",
        "    pros c fetch <the downloaded .zip>",
        "    pros c apply hitlib",
    ),
    Platform.VEXCODE: (
        "Download hitlib-vexcode from",
        "    https://github.com/advisorylabs/HitLib/releases",
        "and copy its include/hitlib/ and src/hitlib/ folders",
        "into the project.",
    ),
}

#: How far up from a dropped file to look for the manifest. Bounded so an
#: unrelated file does not walk up to the drive root and match something.
_MAX_DEPTH = 6


def install_hint_lines(platform: Platform) -> tuple[str, ...]:
    """How to install HitLib into a project on @p platform."""
    return _INSTALL_HINTS[platform]


def _vscode_project_is_v5_cpp(settings: Path) -> bool:
    """Whether a VS Code extension project is one HitLib can build in.

    The same settings file marks IQ, EXP and Python projects. An unreadable
    file gets the benefit of the doubt: the folder is still plainly a VEX
    project, and refusing it would give no reason the user could act on.
    """
    try:
        project = json.loads(settings.read_text(encoding="utf-8")).get("project", {})
    except (OSError, ValueError, AttributeError):
        return True
    platform = str(project.get("platform", "V5")).upper()
    language = str(project.get("language", "cpp")).lower()
    return platform == "V5" and language == "cpp"


def platform_at(directory: Path) -> Platform | None:
    """The kind of project rooted at @p directory, or None if it isn't one."""
    if (directory / MANIFEST).is_file():
        return Platform.PROS
    settings = directory / VSCODE_SETTINGS
    if settings.is_file():
        return Platform.VEXCODE if _vscode_project_is_v5_cpp(settings) else None
    if any(p.is_file() for p in directory.glob(f"*{V5CODE_SUFFIX}")):
        return Platform.VEXCODE
    return None


def find_project_root(path: Path) -> Path | None:
    """The robot project @p path belongs to, or None.

    Accepts the project folder, its project file, or any file inside it.
    """
    # A relative path has no usable .parents to walk, so resolve first.
    path = Path(path).resolve()
    start = path if path.is_dir() else path.parent
    for candidate in [start, *start.parents][:_MAX_DEPTH]:
        if platform_at(candidate) is not None:
            return candidate
    return None


@dataclass(frozen=True)
class Project:
    """A robot project the app can deploy into, and whether it is ready to."""

    root: Path
    platform: Platform = Platform.PROS

    @property
    def include_dir(self) -> Path:
        return self.root / INCLUDE_DIR

    @property
    def has_hitlib(self) -> bool:
        """Whether HitLib is installed here.

        Checked before writing: without the library the export produces only
        missing-include errors, which say nothing about the actual fix. A
        VEXcode project needs the VEXcode download's `.h` headers, the names
        its export includes; `.hpp` sources copied from the repo don't count.
        """
        return (self.include_dir / (_HITLIB_MARKER_STEM + self.platform.header_suffix)).is_file()

    @property
    def install_hint_lines(self) -> tuple[str, ...]:
        return install_hint_lines(self.platform)

    @property
    def studio_header_name(self) -> str:
        """The one header Deploy writes into this project."""
        return STUDIO_HEADER_STEM + self.platform.header_suffix

    def header_path(self, header_name: str) -> Path:
        return self.include_dir / header_name

    def deploy(self, header_name: str, code: str) -> Path:
        """Write @p code as @p header_name in the project's include/ directory.

        Overwrites in place so a re-export lands on the file the project
        already includes, rather than beside it as a second copy. In a VEXcode
        Pro V5 project the header is also listed in the `.v5code`, after it is
        written, so a VEXcode that reloads on seeing the list change finds it.
        """
        self.include_dir.mkdir(parents=True, exist_ok=True)
        destination = self.header_path(header_name)
        destination.write_text(code, encoding="utf-8")
        if self.platform is Platform.VEXCODE:
            _list_in_v5code(self.root, f"{INCLUDE_DIR}/{header_name}")
        return destination


def _list_in_v5code(root: Path, name: str) -> None:
    """Add file @p name (relative, forward slashes) to the project's `.v5code`
    file list, if the project has one and it isn't already there.

    Written the way VEXcode writes the file - compact JSON, files before
    directories - so VEXcode reads it back unchanged. A list that can't be read
    is left alone: the header still builds, and rewriting a file VEXcode needs
    to open the project is not a risk worth taking to make it visible.
    """
    solution = next((p for p in root.glob(f"*{V5CODE_SUFFIX}") if p.is_file()), None)
    if solution is None:
        return
    try:
        content = json.loads(solution.read_text(encoding="utf-8"))
        files = content["files"]
        if not isinstance(files, list):
            return
    except (OSError, ValueError, KeyError, TypeError):
        return
    if any(isinstance(entry, dict) and entry.get("name") == name for entry in files):
        return

    entry = {"name": name, "type": "File", "specialType": ""}
    first_dir = next(
        (i for i, e in enumerate(files) if isinstance(e, dict) and e.get("type") == "Directory"),
        len(files),
    )
    files.insert(first_dir, entry)
    folder = name.rsplit("/", 1)[0] if "/" in name else ""
    if folder and not any(isinstance(e, dict) and e.get("name") == folder for e in files):
        files.append({"name": folder, "type": "Directory"})
    solution.write_text(json.dumps(content, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def open_project(path: Path) -> Project | None:
    """A Project for whatever was dropped or picked, or None if it isn't one."""
    root = find_project_root(Path(path))
    if root is None:
        return None
    return Project(root, platform_at(root))
