"""Nuitka entry point (avoids relative imports so Nuitka can treat this as
the top-level script while still finding the `fastestcopy` package).
"""
from fastestcopy.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
