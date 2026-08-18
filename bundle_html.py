from __future__ import annotations

from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "templates" / "index.html"
OUTPUT = ROOT / "core" / "native" / "resources" / "QuickFileCopy.html"


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"UI template was not found: {SOURCE}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, OUTPUT)
    print(f"[ok] bundled UI: {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
