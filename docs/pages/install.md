# Installation {#install_page}

## Requirements

- [PROS](https://pros.cs.purdue.edu/) kernel 4.2.0 or later, **or** VEXcode
  (VEXcode V5, VEXcode Pro V5, or the VEX VS Code extension). Projects on
  kernel 4.1.x, which includes EZ-Template 3.x projects, use the
  [softfp download](#install_softfp) instead.
- VEX V5 brain
- WS2812B-compatible LED strip wired to a VEX ADI port

---

## Install via PROS CLI

Download `hitlib@1.6.0.zip` from the
[1.6.0 release](https://github.com/advisorylabs/hitlib/releases/tag/1.6.0)
into your Downloads folder, then, from your project folder:

```bash
pros c fetch $HOME/Downloads/hitlib@1.6.0.zip
pros c apply HitLib
```

Those run as they are in PowerShell, the VS Code terminal's default on
Windows, and in a macOS or Linux terminal. If you saved the zip somewhere
else, give `pros c fetch` that path instead. In Command Prompt, the path is
`%USERPROFILE%\Downloads\hitlib@1.6.0.zip`. Safari unzips downloads by
itself, into a `hitlib@1.6.0` folder, and `pros c fetch` takes that folder
too: leave off the `.zip`.

The CLI fetches a template from a file, not a URL, and template names are
case-sensitive: it's `HitLib`. Or paste the zip's URL,
`https://github.com/advisorylabs/hitlib/releases/download/1.6.0/hitlib@1.6.0.zip`,
into the **Install Template** dialog in the PROS VS Code extension.

Check the [Releases page](https://github.com/advisorylabs/hitlib/releases) for the
latest version and swap the version number above if a newer one is available.

### Projects on kernel 4.1.x (EZ-Template 3.x) {#install_softfp}

PROS kernel 4.2.0 changed how projects compile floating-point code, from
`softfp` to `hard`, and the linker won't mix the two. A project on an older
kernel can't use the download above: `pros c apply` reports that HitLib does
not support the project's kernel version, or the build fails with
`hitlib.a(...) uses VFP register arguments, ... does not`. Kernel 4.1.x
projects, which includes every EZ-Template 3.x project, use the softfp build,
`hitlib@1.6.0-softfp.zip` from the same release:

```bash
pros c fetch $HOME/Downloads/hitlib@1.6.0-softfp.zip
pros c apply HitLib
```

`pros c info-project` shows a project's kernel version. Both downloads install
as `HitLib`, and `pros c apply HitLib` takes whichever fits the project's
kernel, so once a project moves to kernel 4.2 or later, fetching the regular
download and running `pros c apply HitLib` again swaps the softfp build out.
Kernels before 4.1.0 aren't supported.

### "HitLib does not support kernel version ..." {#install_kernel_error}

`pros c apply` prints this whenever none of the templates you have fetched
matches the name you gave it for the project's kernel. A typo gets the same
message as a real kernel mismatch. On kernel 4.2.0 or later, check:

- **The capitalization.** The CLI matches template names exactly, and the
  download's file name is lowercase, so `pros c apply hitlib` fails with
  "hitlib does not support kernel version 4.2.2". It's `pros c apply HitLib`.
- **Which build you fetched.** `pros c ls-templates HitLib` lists every HitLib
  you have fetched. If the only one there ends in `-softfp`, that's the kernel
  4.1.x build: fetch `hitlib@1.6.0.zip` as well.
- **That the fetch worked.** `pros c apply` doesn't download HitLib by
  itself. If `pros c fetch` printed `Template not found: path/to/hitlib`, or
  any other path, it found no zip there and fetched nothing. Pasting both
  lines at once buries that error under the one from `pros c apply`.

On kernel 4.1.x, the message means the project needs the
[softfp build](#install_softfp).

### Black screen at startup after installing 1.5.1 {#install_cold_hang}

With HitLib 1.5.1 installed, a PROS project can stop at a black screen as soon
as it starts: `initialize()` never runs, nothing drives, and the terminal
doesn't even show the PROS banner. It happens whether or not the program uses
any LEDs. One single-byte variable in 1.5.1's `hitlib.a` moves LVGL's display
buffers off 4-byte alignment, and LVGL stops dead on that before the program
starts. 1.5.2 fixes it: fetch it and run `pros c apply HitLib` again.

To keep 1.5.1 instead, add this line to the project's `Makefile`, then run
`make clean` and upload again:

```make
EXCLUDE_COLD_LIBRARIES:= $(FWDIR)/hitlib.a
```

That links HitLib into the hot image, where it can't move LVGL's buffers. The
line can come out once the project is on 1.5.2 or later.

### Black screen at startup with Arm GNU 15.2 {#install_toolchain_hang}

The same black screen can show up with any HitLib version, or with no HitLib
at all, when the project is built with Arm GNU Toolchain 15.2 instead of the
toolchain the PROS VS Code extension installs. Other 15.x releases may do the
same. That toolchain's C++ library pulls in a 1-byte C library variable that
lands right in front of LVGL's display buffers and moves them off 4-byte
alignment. Nothing in HitLib can prevent that. The fix belongs in PROS's LVGL
template, in [liblvgl#54](https://github.com/purduesigbots/liblvgl/pull/54).

To check a build, run this from the project folder:

```bash
arm-none-eabi-nm -n bin/cold.package.elf | grep display_buffer
```

On Windows, use `findstr` in place of `grep`. Both addresses have to end in
`0`, `4`, `8` or `c`. If either doesn't, run
`arm-none-eabi-gcc --version` in the terminal you build from. If that reports
15.x, put the PROS extension's toolchain ahead of it on `PATH`, or remove it,
then run `make clean` and upload again.

---

## Install into a VEXcode project

VEXcode has no template system, so HitLib ships as source for it. Download
`hitlib-vexcode@1.6.0.zip` from the [Releases page](https://github.com/advisorylabs/hitlib/releases)
and copy its two folders into your project:

```
your-project/
├─ include/hitlib/     ← from the zip
└─ src/hitlib/         ← from the zip
```

The VEXcode makefile already builds `src/*/*.cpp` and searches `include/`, so
there is nothing to configure. HitLib notices it is building against the VEX
SDK and uses it in place of PROS.

In this download every header ends in `.h` rather than `.hpp`, because VEXcode
Pro V5 only shows `.h` files in its file tree. Wherever these docs include
`hitlib/hitapi.hpp` or `hitlib/profiles/classic.hpp`, a VEXcode project includes
`hitlib/hitapi.h` and `hitlib/profiles/classic.h`. Then, in `main.cpp`:

```cpp
#include "vex.h"
#include "hitlib/hitapi.h"

hitlib::LedStrand strand(1, 63);   // 3-wire port A, 63 LEDs
hitlib::LedGroup  group;

int main() {
    vexcodeInit();
    group.add(&strand);
    group.init();
    group.start();

    group.rainbow(1);

    while (true) vex::this_thread::sleep_for(100);   // keep main() from exiting
}
```

Ports are numbered 1-8 for A-H. Don't also configure that port as a device in
VEXcode's device menu; HitLib drives it directly.

---

## Include the library

Add one include at the top of your `main.h` or any file that uses hitlib:

```cpp
#include "hitlib/hitapi.hpp"      // hitlib/hitapi.h in VEXcode
```

To use the built-in profiles, also include:

```cpp
#include "hitlib/profiles/classic.hpp"      // hitlib/profiles/classic.h in VEXcode
```

---

### ADI expander

If your strip is on an ADI expander connected to a smart port:

```cpp
// Smart port 2, ADI port A (1), 63 LEDs
hitlib::LedStrand strand({2, 1}, 63);
```

The braces matter: three bare numbers always mean `(adiPort, length, refreshMs)`
on the brain's own ports.

### Chaining strips into one strand

Several strips can act as one long strand: every animation, mask and profile
then runs across all of them, so a pulse leaves the end of one strip and
carries on at the start of the next. List the strips in pixel order:

```cpp
// Four 20-LED strips around the chassis, as one 80-LED ring.
hitlib::LedStrand ring({{1, 20}, {2, 20}, {3, 20}, {4, 20}});

// Brain ports and expander ports mix: ADI A, then port A of the expander on Smart Port 5.
hitlib::LedStrand sides({{1, 30}, {{5, 1}, 30}});
```

A chain is up to 8 strips and 128 LEDs. Each strip still costs its own 12 ms ADI
write when its part of the frame changes. In Pattern Studio, select strands that
sit next to each other in the list and press **Chain**. To run an animation the
other way along the chain, use its **Invert** option.

---

## Minimal working example

```cpp
#include "main.h"
#include "hitlib/hitapi.hpp"

hitlib::LedStrand strand(6, 63);
hitlib::LedGroup  group;

void initialize() {
    group.add(&strand);
    group.init();
    group.start();
}

void opcontrol() {
    group.rainbow(1);
}
```

---

## Pattern Studio

[Pattern Studio](https://github.com/advisorylabs/hitlib/tree/main/tools/pattern_studio)
is a desktop GUI for designing and live-previewing LED profiles without flashing a
brain, then exporting them as ready-to-include HitLib C++.

Download a prebuilt build from the
[Releases page](https://github.com/advisorylabs/hitlib/releases) -
`HitLibPatternStudio-*-windows.zip` for Windows, `-macos-apple-silicon.zip`
or `-macos-intel.zip` for macOS 13 or later - or run it from source on any
platform:

```bash
git clone https://github.com/advisorylabs/hitlib.git
cd hitlib/tools/pattern_studio
pip install -e .
pattern-studio
```

The Mac build is not signed with an Apple developer certificate, so macOS
blocks the first launch. Drag the app into Applications, double-click it and
dismiss the warning, then open **System Settings > Privacy & Security**,
scroll to Security and click **Open Anyway**. Confirm when macOS asks again and
enter your Mac password. It opens normally after that.

### Getting a design onto the robot

1. Point Pattern Studio at your project - drag the project folder onto the
   window, or **Export > Choose Robot Project...**. It recognises a PROS
   `project.pros`, a VEXcode `.v5code` file, and a VS Code VEX extension
   project's `.vscode/vex_project_settings.json`, and remembers the project
   between runs.
2. **Export > Deploy**. The header lands in the project's `include/` as
   `hitlib_studio.hpp`, or `hitlib_studio.h` in a VEXcode project - VEXcode Pro
   V5 lists only `.h` headers in its file tree, so Deploy also adds it to the
   project's `.v5code` file list.
3. Paste the two lines it shows you into `main.cpp`, once:

```cpp
#include "hitlib_studio.hpp"   // hitlib_studio.h in VEXcode

void initialize() { hitlib::studio::begin(); }   // pre_auton() in VEXcode
```

Deploying again overwrites that header and `main.cpp` does not change:
`begin()` builds every strand from the design's port, length, refresh interval
and brightness, attaches each profile and activates its first mode.

To own the wiring yourself, `hitlib::studio::begin(yourGroup)` puts the
design's strands into your group,
and `#define HITLIB_STUDIO_NO_AUTOWIRE` before the include leaves only the
profile and the `constexpr` constants to build your own `LedStrand` from.
**Export > Export Current Strand as C++...** (or **Export All Strands as
C++...**) still saves through a normal file dialog for a project the app cannot
see. See [Profiles & Modes](#profiles_page) for the full shape of the generated
file.

---

## Building a PROS template from source

If you want to build hitlib yourself rather than using a release zip: this
repo isn't set up as a `pros make`-managed project, so it's a plain library
build followed by packaging with Python 3.

```bash
# Clone the repo
git clone https://github.com/advisorylabs/hitlib.git
cd hitlib

# Build bin/hitlib.a requires an arm-none-eabi-gcc toolchain on PATH
# (installed alongside PROS, or via your package manager)
make

# Package it as an installable PROS template
python tools/package_template.py
```

This produces `hitlib@1.6.0.zip` in the project root (matching the `version`
field in `template.pros`), which you can then fetch with the CLI as shown above.
The archive goes in `firmware/` because that is the only place a PROS project
links archives from, and only `include/hitlib/` is packaged: the rest of
`include/` is the PROS kernel's, which every project already has.

The [softfp build](#install_softfp) for kernel 4.1.x is compiled against that
kernel's own headers. Unzip
[kernel@4.1.2.zip](https://pros.cs.purdue.edu/v5/_static/releases/kernel@4.1.2.zip)
somewhere, then:

```bash
make softfp SOFTFP_KERNEL=path/to/unzipped/kernel
python tools/package_template.py --softfp
```

That builds `bin/softfp/hitlib.a` and packages it as `hitlib@1.6.0-softfp.zip`.
The packager checks each archive's float ABI and refuses one built for the
other template.

## Building the VEXcode download from source

The VEXcode download is the library's sources, with the headers renamed to
`.h`. No toolchain is needed, only Python 3:

```bash
python tools/package_vexcode.py vexcode_pkg
```

`vexcode_pkg/` then holds the `include/hitlib/` and `src/hitlib/` folders to
copy into a VEXcode project, exactly as the release zip does. Give it your
project's folder instead to install or update HitLib there directly: it
replaces those two folders and leaves everything else in the project alone.
