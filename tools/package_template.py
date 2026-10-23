"""Package a built HitLib archive as an installable PROS template.

    python tools/package_template.py [--softfp] [OUT_DIR]      (default: .)

produces OUT_DIR/hitlib@VERSION.zip from bin/hitlib.a, or with --softfp,
OUT_DIR/hitlib@VERSION-softfp.zip from bin/softfp/hitlib.a. Build the archive
first: `make` for the first, `make softfp` for the second (see the Makefile).

Each zip holds what `pros c apply` copies into a project:

    template.pros
    include/hitlib/...     every header
    firmware/hitlib.a      a PROS project only links the archives in firmware/

Only include/hitlib/ ships: the rest of include/ is the PROS kernel's, which
the project already has.

PROS kernels from 4.2.0 on compile with -mfloat-abi=hard and older ones with
softfp, and the linker won't mix the two, so HitLib ships two templates with
the same name. template.pros describes the hard-float one. The softfp one gets
"-softfp" on its version and the 4.1.x kernels as its supported_kernels.
`pros c apply` skips a template whose supported_kernels leave out the
project's kernel, so with both fetched it picks the right one by itself. And
since VERSION-softfp sorts below VERSION, a project that moves from kernel 4.1
to 4.2 is offered the hard-float one as an upgrade.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

HEADER_ROOT = Path("include") / "hitlib"

#: The kernels the softfp template is for. 4.2.0 is where PROS switched to
#: hard float. The archive is compiled against kernel 4.1 headers, and 4.1.0
#: changed the virtual functions of pros::adi::Port, which pros::adi::Led
#: inherits, so the archive isn't offered to 4.0.x.
SOFTFP_KERNELS = ">=4.1.0,<4.2.0"

READELF = "arm-none-eabi-readelf"


def template_metadata(softfp: bool, repo: Path = REPO_ROOT) -> str:
    """template.pros as it ships in the zip: the repo's own, or for softfp,
    that with the softfp version and kernels."""
    text = (repo / "template.pros").read_text(encoding="utf-8")
    if not softfp:
        return text
    metadata = json.loads(text)
    metadata["version"] += "-softfp"
    metadata["supported_kernels"] = SOFTFP_KERNELS
    metadata["description"] += " (softfp build for PROS kernel 4.1.x)"
    return json.dumps(metadata, indent=2) + "\n"


def archive_path(softfp: bool, repo: Path = REPO_ROOT) -> Path:
    return repo / "bin" / ("softfp" if softfp else "") / "hitlib.a"


def check_float_abi(archive: Path, softfp: bool) -> None:
    """Refuse an archive built for the other float ABI - the mix-up the two
    templates exist to prevent."""
    if shutil.which(READELF) is None:
        raise RuntimeError(f"{READELF} isn't on PATH; it comes with the toolchain that builds the archive")
    attributes = subprocess.run(
        [READELF, "-A", str(archive)], capture_output=True, text=True, check=True
    ).stdout
    # readelf heads each member's attributes with "File: hitlib.a(member.o)".
    members = re.split(r"^File: ", attributes, flags=re.MULTILINE)[1:]
    if not members:
        raise RuntimeError(f"{archive} has no object files")
    wrong = [
        m.splitlines()[0]
        for m in members
        if ("Tag_ABI_VFP_args: VFP registers" in m) == softfp
    ]
    if wrong:
        built_for = "hard" if softfp else "softfp"
        raise RuntimeError(f"built for {built_for} float, not what this template needs: {', '.join(wrong)}")


def build(out_dir: Path, softfp: bool = False, repo: Path = REPO_ROOT) -> Path:
    """Write the template zip into @p out_dir and return its path."""
    archive = archive_path(softfp, repo)
    if not archive.is_file():
        raise RuntimeError(f"{archive} doesn't exist; run `make{' softfp' if softfp else ''}` first")
    check_float_abi(archive, softfp)

    metadata = template_metadata(softfp, repo)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"hitlib@{json.loads(metadata)['version']}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("template.pros", metadata)
        for header in sorted((repo / HEADER_ROOT).rglob("*")):
            if header.is_file():
                z.write(header, header.relative_to(repo).as_posix())
        z.write(archive, "firmware/hitlib.a")
    return zip_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Package HitLib as a PROS template zip.")
    parser.add_argument("out_dir", nargs="?", default=".", type=Path)
    parser.add_argument("--softfp", action="store_true", help="package bin/softfp/hitlib.a for kernel 4.1.x")
    args = parser.parse_args()
    try:
        print(f"PROS template written to {build(args.out_dir, args.softfp)}")
    except RuntimeError as e:
        sys.exit(f"error: {e}")
