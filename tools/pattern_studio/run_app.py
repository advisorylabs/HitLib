"""PyInstaller entry point.

pattern_studio/app.py uses relative imports (`from .main_window import ...`),
which only resolve when Python knows it's running as part of the
pattern_studio package. Pointing PyInstaller's Analysis directly at
pattern_studio/app.py runs it as a bare top-level script with no package
context -- the same failure as `python pattern_studio/app.py` instead of
`python -m pattern_studio` -- and raises "attempted relative import with no
known parent package" at startup.

This tiny wrapper lives outside the package and does a plain absolute
import instead, so it works regardless of how it's launched. `python -m
pattern_studio` and the installed `pattern-studio` console script are
unaffected -- both already resolve pattern_studio as a real package.
"""

from pattern_studio.app import main

if __name__ == "__main__":
    main()
