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

Download `hitlib@1.5.1.zip` from the
[1.5.1 release](https://github.com/advisorylabs/hitlib/releases/tag/1.5.1),
then, from your project folder:

```bash
pros c fetch path/to/hitlib@1.5.1.zip
pros c apply HitLib
```

The CLI fetches a template from a file, not a URL, and template names are
case-sensitive: it's `HitLib`. Or paste the zip's URL,
`https://github.com/advisorylabs/hitlib/releases/download/1.5.1/hitlib@1.5.1.zip`,
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
`hitlib@1.5.1-softfp.zip` from the same release:

```bash
pros c fetch path/to/hitlib@1.5.1-softfp.zip
pros c apply HitLib
```

`pros c info-project` shows a project's kernel version. Both downloads install
as `HitLib`, and `pros c apply HitLib` takes whichever fits the project's
kernel, so once a project moves to kernel 4.2 or later, fetching the regular
download and running `pros c apply HitLib` again swaps the softfp build out.
Kernels before 4.1.0 aren't supported.

---

## Install into a VEXcode project

VEXcode has no template system, so HitLib ships as source for it. Download
`hitlib-vexcode@1.5.1.zip` from the [Releases page](https://github.com/advisorylabs/hitlib/releases)
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

This produces `hitlib@1.5.1.zip` in the project root (matching the `version`
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

That builds `bin/softfp/hitlib.a` and packages it as `hitlib@1.5.1-softfp.zip`.
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
