# hitlib Docs Setup

## One-time submodule setup

```bash
git submodule add https://github.com/jothepro/doxygen-awesome-css docs/doxygen-awesome-css
git submodule update --init --recursive
```

## Build locally

```bash
# Install doxygen (macOS)
brew install doxygen

# Install doxygen (Ubuntu / WSL)
sudo apt install doxygen

# Generate
doxygen Doxyfile

# Open
open docs/generated/html/index.html       # macOS
xdg-open docs/generated/html/index.html  # Linux
```

## GitHub Pages deployment

1. Push the submodule and workflow to `main`.
2. Go to **Settings → Pages → Source** and set it to **GitHub Actions**.
3. The workflow in `.github/workflows/docs.yml` builds and deploys on every
   push to `main` that touches `include/`, `docs/`, or `Doxyfile`.

The live site will be at `https://YOUR_ORG.github.io/hitlib/`.

## .gitignore additions

Add to your `.gitignore`:
```
docs/generated/
```
