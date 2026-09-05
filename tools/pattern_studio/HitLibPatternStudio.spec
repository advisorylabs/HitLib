# -*- mode: python ; coding: utf-8 -*-

import re
import sys
from pathlib import Path

_VERSION = re.search(
    r'__version__ = "([^"]+)"',
    Path("pattern_studio/__init__.py").read_text(encoding="utf-8"),
).group(1)

_MACOS = sys.platform == "darwin"

# The two platforms name their icon containers differently and neither reads
# the other's: Windows wants the multi-frame .ico, macOS the .icns, whose
# frames run up to the 1024px the Dock asks for. Both are built from the same
# artwork by tools/make_icon.py and tools/make_icns.py.
_ICON = "pattern_studio/resources/hitliblogo." + ("icns" if _MACOS else "ico")

_BUNDLE_ID = "AdvisoryLabs.HitLib.PatternStudio"

# ----------------------------------------------------------------------
# Windows version resource
# ----------------------------------------------------------------------

# Windows reads an executable's VERSIONINFO resource to name it in Task
# Manager, the UAC prompt and the file properties dialog. Without one it falls
# back to the bare filename, which is why the app used to be hard to pick out
# of a process list. FileDescription is specifically the field Task Manager's
# Processes tab shows.
#
# Guarded because the module that builds one ships only in a Windows
# PyInstaller install: at top level this import is an ImportError on macOS
# before Analysis() is ever reached.
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    # VERSIONINFO's numeric version is four plain integers, so a PEP 440
    # suffix like "0.3.0a1" contributes its digits as the fourth component
    # (0, 3, 0, 1). The human-readable strings below carry the exact version
    # either way.
    _PARTS = [int(n) for n in re.findall(r"\d+", _VERSION)[:4]]
    _FILEVERS = tuple(_PARTS + [0] * (4 - len(_PARTS)))

    version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=_FILEVERS,
            prodvers=_FILEVERS,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,   # VOS_NT_WINDOWS32
            fileType=0x1,  # VFT_APP
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                StringTable(
                    "040904B0",  # US English, Unicode
                    [
                        StringStruct("CompanyName", "Advisory Labs"),
                        StringStruct("FileDescription", "HitLib Pattern Studio"),
                        StringStruct("FileVersion", _VERSION),
                        StringStruct("InternalName", "HitLibPatternStudio"),
                        StringStruct("LegalCopyright", "Advisory Labs. MPL-2.0 licensed."),
                        StringStruct("OriginalFilename", "HitLibPatternStudio.exe"),
                        StringStruct("ProductName", "HitLib Pattern Studio"),
                        StringStruct("ProductVersion", _VERSION),
                    ],
                ),
            ]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
else:
    version_info = None

# ----------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------

a = Analysis(
    ['run_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('pattern_studio/resources/hitliblogo.ico', 'pattern_studio/resources'),
        ('pattern_studio/resources/hitliblogo.png', 'pattern_studio/resources'),
        ('pattern_studio/resources/icons', 'pattern_studio/resources/icons'),
    ],
    # QtSvg: the theme's spin/chevron/check/transport icons are SVG, and Qt only
    # decodes SVG when the QtSvg module (and its imageformat plugin) is in the
    # bundle. Nothing imports it in Python, so PyInstaller can't infer it --
    # without this the frozen build silently loses every icon.
    #
    # QtMultimedia is imported by pattern_studio.audio, so PyInstaller finds the
    # module on its own -- but the ffmpeg media backend it needs is a Qt plugin
    # loaded at runtime, which nothing imports. Without the plugin the frozen
    # build cannot decode a single audio file, and the Song bar can only load
    # MIDI. The PySide6 hook collects it once QtMultimedia is known to be in.
    hiddenimports=['PySide6.QtSvg', 'PySide6.QtMultimedia'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Windows ships one self-extracting .exe; macOS ships a directory bundle.
#
# A one-file build inside a .app would work, but every launch would unpack the
# whole Qt framework into a temp directory before the splash appears, and the
# copy Gatekeeper checks would not be the one that runs. A .app is already a
# directory users treat as a single icon, so the layout it wants is the
# ordinary one: the binary and its libraries side by side under Contents/.
if _MACOS:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='HitLibPatternStudio',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        # UPX is left off here: a packed Mach-O binary fails signature
        # validation, so a compressed build is one Gatekeeper refuses to open.
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=_ICON,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='HitLibPatternStudio',
    )
    app = BUNDLE(
        coll,
        name='HitLibPatternStudio.app',
        icon=_ICON,
        bundle_identifier=_BUNDLE_ID,
        version=_VERSION,
        info_plist={
            'CFBundleShortVersionString': _VERSION,
            'CFBundleVersion': _VERSION,
            # Without this the window is scaled up from a 1x render and every
            # label on a Retina display comes out soft.
            'NSHighResolutionCapable': True,
            # The floor PySide6 6.6 itself supports.
            'LSMinimumSystemVersion': '11.0',
            'NSHumanReadableCopyright': 'Advisory Labs. MPL-2.0 licensed.',
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name='HitLibPatternStudio',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=_ICON,
        version=version_info,
    )
