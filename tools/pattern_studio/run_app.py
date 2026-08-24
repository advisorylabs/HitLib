"""PyInstaller entry point.

pattern_studio/app.py uses relative imports (`from .main_window import ...`),
which only resolve when Python knows it's running as part of the
pattern_studio package.

This tiny wrapper lives outside the package and does a plain absolute
import instead, so it works regardless of how it's launched.
"""

from pattern_studio.app import main

if __name__ == "__main__":
    main()
