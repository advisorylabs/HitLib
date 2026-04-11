# Installation {#install_page}

## Requirements

- [PROS](https://pros.cs.purdue.edu/) 3.x or later
- VEX V5 brain
- WS2812B-compatible LED strip wired to a VEX ADI port

---

## Install via PROS CLI

```bash
pros c fetch https://github.com/advisorylabs/hitlib/releases/download/v0.6.0/hitlib@0.6.0.zip
pros c apply hitlib
```

Or paste the URL directly into the **Install Template** dialog in the PROS VS Code extension.

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

## Building a PROS template from source

If you want to build hitlib yourself rather than using a release zip:

```bash
# Clone the repo
git clone https://github.com/advisorylabs/hitlib.git
cd hitlib

git submodule update --init --recursive

# Build and package
pros make
pros make-template
```

This produces `hitlib@0.6.0.zip` in the project root, which you can then
fetch with the CLI as shown above.
