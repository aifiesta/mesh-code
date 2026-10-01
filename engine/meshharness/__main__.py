"""Entry point for `python -m meshharness` and for the frozen binary.

The import is ABSOLUTE on purpose. PyInstaller runs this file as a
top-level script with no parent package, so `from .server import main`
raises "attempted relative import with no known parent package" — the
frozen app dies before it prints its handshake, and the desktop shell just
reports that the engine never became ready. The absolute form works under
both `-m` and the freeze.
"""
from meshharness.server import main

if __name__ == "__main__":
    main()
