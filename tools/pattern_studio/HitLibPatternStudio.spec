# -*- mode: python ; coding: utf-8 -*-

import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

# Windows reads an executable's VERSIONINFO resource to name it in Task
# Manager, the UAC prompt and the file properties dialog. Without one it falls
# back to the bare filename, which is why the app used to be hard to pick out
# of a process list. FileDescription is specifically the field Task Manager's
# Processes tab shows.
_VERSION = re.search(
    r'__version__ = "([^"]+)"',
    Path("pattern_studio/__init__.py").read_text(encoding="utf-8"),
).group(1)

# VERSIONINFO's numeric version is four plain integers, so a PEP 440 suffix
# like "0.3.0a1" contributes its digits as the fourth component (0, 3, 0, 1).
# The human-readable strings below carry the exact version either way.
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
    icon='pattern_studio/resources/hitliblogo.ico',
    version=version_info,
)
