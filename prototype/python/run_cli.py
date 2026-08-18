"""Nuitka entry point for a lightweight CLI-only build (no Qt/GUI deps) -
used to benchmark the actually-compiled artifact against robocopy, not just
the Python source running under the interpreter.
"""
from fastestcopy.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
