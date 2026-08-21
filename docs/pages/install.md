# Installation {#install_page}

## Requirements

- [PROS](https://pros.cs.purdue.edu/) 4.x or later
- VEX V5 brain
- WS2812B-compatible LED strip wired to a VEX ADI port

---

## Install via PROS CLI

```bash
pros c fetch https://github.com/advisorylabs/hitlib/releases/download/1.1.0/hitlib@1.1.0.zip
pros c apply hitlib
```

Or paste the URL directly into the **Install Template** dialog in the PROS VS Code extension.

Check the [Releases page](https://github.com/advisorylabs/hitlib/releases) for the
latest version and swap the version number above if a newer one is available.

---

## Include the library

Add one include at the top of your `main.h` or any file that uses hitlib:

```cpp
#include "hitlib/hitapi.hpp"
```

To use the built-in profiles, also include:

```cpp
#include "hitlib/profiles/classic.hpp"
```

---

### ADI expander

If your strip is on an ADI expander connected to a smart port:

```cpp
// Smart port 2, ADI port A (1), 63 LEDs
hitlib::LedStrand strand(2, 1, 63);
```

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

Download the prebuilt Windows build from the
[Releases page](https://github.com/advisorylabs/hitlib/releases)
(`HitLibPatternStudio-*-windows.zip`), or run it from source on any platform:

```bash
git clone https://github.com/advisorylabs/hitlib.git
cd hitlib/tools/pattern_studio
pip install -e .
pattern-studio
```

---

## Building a PROS template from source

If you want to build hitlib yourself rather than using a release zip: this
repo isn't set up as a `pros make`-managed project, so it's a plain library
build followed by manual packaging -- the same steps CI runs for every release.

```bash
# Clone the repo
git clone https://github.com/advisorylabs/hitlib.git
cd hitlib

# Build bin/hitlib.a -- requires an arm-none-eabi-gcc toolchain on PATH
# (installed alongside PROS, or via your package manager)
make

# Package it as an installable PROS template
mkdir -p template_pkg/include template_pkg/lib
cp -r include/ template_pkg/
cp bin/hitlib.a template_pkg/lib/
cp template.pros template_pkg/
cd template_pkg && zip -r ../hitlib@1.2.0.zip .
```

This produces `hitlib@1.2.0.zip` in the project root (matching the `version`
field in `template.pros`), which you can then fetch with the CLI as shown above.
