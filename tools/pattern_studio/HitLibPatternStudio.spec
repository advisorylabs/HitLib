# -*- mode: python ; coding: utf-8 -*-

import re
import sys
from pathlib import Path

# One spec file, two platforms. Everything up to and including Analysis is
# shared - the same entry script, the same data files, the same hidden
# imports - and only the packaging on the far side of it differs, because a
# single Windows .exe and a macOS .app bundle have no common shape to express.
_IS_MACOS = sys.platform == "darwin"

_VERSION = re.search(
    r'__version__ = "([^"]+)"',
    Path("pattern_studio/__init__.py").read_text(encoding="utf-8"),
).group(1)

# VERSIONINFO's numeric version is four plain integers, so a PEP 440 suffix
# like "0.3.0a1" contributes its digits as the fourth component (0, 3, 0, 1).
# The human-readable strings below carry the exact version either way.
#
# macOS is stricter still: CFBundleShortVersionString has to be one to three
# dot-separated integers, and a bundle whose version string macOS cannot parse
# fails to launch rather than launching with an odd version in its About box.
# So the Mac side is built from the release segment alone, padded to three
# parts. It cannot reuse the digits above: for "1.1rc2" those are 1, 1, 2, and
# a release candidate would claim to be 1.1.2 and outrank the real 1.1.
_PARTS = [int(n) for n in re.findall(r"\d+", _VERSION)[:4]]
_FILEVERS = tuple(_PARTS + [0] * (4 - len(_PARTS)))
_RELEASE = [int(n) for n in re.match(r"\d+(?:\.\d+)*", _VERSION).group().split(".")]
_SHORT_VERSION = ".".join(str(n) for n in (_RELEASE + [0, 0])[:3])

# Windows reads an executable's VERSIONINFO resource to name it in Task
# Manager, the UAC prompt and the file properties dialog. Without one it falls
# back to the bare filename, which is why the app used to be hard to pick out
# of a process list. FileDescription is specifically the field Task Manager's
# Processes tab shows.
#
# The import sits inside the branch rather than at the top of the file because
# PyInstaller.utils.win32.versioninfo imports pefile and pywin32, and
# PyInstaller only declares those as dependencies on win32. At module scope it
# raises ModuleNotFoundError on a Mac before Analysis has even been reached,
# which turns "this spec has no Mac packaging yet" into "this spec cannot be
# read on a Mac at all".
version_info = None
if not _IS_MACOS:
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

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

if _IS_MACOS:
    # onedir, not onefile. A onefile executable unpacks everything it carries -
    # around 200MB of Qt - into a fresh temporary directory on every single
    # launch, and macOS wants to scan what lands there each time. The splash
    # screen cannot hide any of that: the splash is a Qt window, so it only
    # exists once the unpacking it would be covering has already finished.
    # PyInstaller deprecated the onefile-plus-windowed combination for exactly
    # this reason and will refuse it outright in 7.0.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='HitLibPatternStudio',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        # Off deliberately, even though PyInstaller only ever runs UPX on
        # Windows and this is a no-op today. UPX-compressed Mach-O files fail
        # codesign's validation, and an Apple silicon Mac refuses to run code
        # carrying no valid signature at all, so a future release that started
        # honouring the flag here would ship an app that cannot launch.
        upx=False,
        upx_exclude=[],
        console=False,
        disable_windowed_traceback=False,
        # Argv emulation exists to turn a double-clicked document into a
        # sys.argv entry. Nothing in this app reads a file path out of sys.argv,
        # so all it would buy is Apple Event handling during startup whose
        # result is then dropped on the floor. It goes on in the same change
        # that teaches the app to open a document, not before.
        argv_emulation=False,
        # None means "whatever architecture this runner is", which is what the
        # two-Mac CI matrix wants. universal2 is not an option here: the CI
        # Python is a thin build, and PyInstaller refuses it as not a fat
        # binary.
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
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
        # With the spaces, because on macOS this name *is* the name: Finder,
        # the Dock and Launchpad all show the bundle folder, and no Info.plist
        # key overrides that for an app sitting in /Applications.
        # "HitLibPatternStudio" run together between "Google Chrome" and
        # "Visual Studio Code" reads as a packaging mistake. The executable
        # inside stays HitLibPatternStudio with no space, so crash reports,
        # pgrep and the Windows build are all unchanged, and CI only has one
        # path to quote.
        name='HitLib Pattern Studio.app',
        icon='pattern_studio/resources/hitliblogo.icns',
        # Reverse-DNS and stable, and deliberately the same shape as the
        # AppUserModelID app.py claims on Windows. macOS keys off this the way
        # the Windows shell keys off that one, so changing it later makes the
        # system treat the app as a brand new one and drop any Dock pin.
        bundle_identifier='com.advisorylabs.hitlib.patternstudio',
        version=_SHORT_VERSION,
        info_plist={
            # CFBundleName is what macOS sets in bold at the left of the menu
            # bar, and inside "About ...", "Hide ..." and "Quit ...". Apple
            # asks for 16 characters or fewer there and means it, because the
            # File and Export menus sit immediately to its right. So the menu
            # bar gets the short form and everywhere with room gets the whole
            # name.
            'CFBundleName': 'Pattern Studio',
            'CFBundleDisplayName': 'HitLib Pattern Studio',

            # Two keys because macOS uses them for two things: the short string
            # is what a person reads in Get Info and the About box, and
            # CFBundleVersion is the build number the system compares when it
            # finds two copies of the same bundle identifier and has to pick
            # one. Both must parse as dot-separated integers, which is why they
            # come from _SHORT_VERSION rather than from _VERSION.
            'CFBundleShortVersionString': _SHORT_VERSION,
            'CFBundleVersion': _SHORT_VERSION,

            # PyInstaller already sets this for windowed builds, but it is the
            # one key whose absence is instantly visible: without it macOS runs
            # the whole app through its 2x upscaler and every pixel in the LED
            # preview goes soft. Too cheap to leave resting on a default.
            'NSHighResolutionCapable': True,

            # The floor is PySide6's, not a guess: its macOS wheels are tagged
            # macosx_13_0, so the Qt libraries inside this bundle will not load
            # on anything older than Ventura. Declaring it means Finder says so
            # in a sentence, instead of the app launching and dying on a
            # missing symbol with no window ever appearing.
            'LSMinimumSystemVersion': '13.0',

            # The same string as the Windows LegalCopyright field above, so the
            # two builds cannot drift about who wrote this and under what
            # licence.
            'NSHumanReadableCopyright': 'Advisory Labs. MPL-2.0 licensed.',

            # Inert outside the App Store and correct inside it: this app's job
            # is generating C++ headers for a robot, which is a developer tool.
            'LSApplicationCategoryType': 'public.app-category.developer-tools',

            # There is deliberately no CFBundleDocumentTypes entry for
            # .hlprofile. Registering the type is what makes double-clicking a
            # design launch this app, but nothing here handles QFileOpenEvent
            # and nothing reads a path out of sys.argv, so the app would come
            # up on an empty untitled document and silently drop the file that
            # was actually asked for. Finder admitting it does not know what
            # opens a .hlprofile is the kinder failure. The key goes in with
            # the handler, not before it.
            #
            # NSRequiresAquaSystemAppearance is likewise absent, and that is a
            # decision rather than an oversight. Setting it true would pin the
            # process to light appearance, which is backwards here: the window
            # content is already dark from our own QSS, and it is the native
            # title bar and menu bar that have to follow it. What does that is
            # QStyleHints.setColorScheme(), called in theme.apply_theme().
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
        icon='pattern_studio/resources/hitliblogo.ico',
        version=version_info,
    )
