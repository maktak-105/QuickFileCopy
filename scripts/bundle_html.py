from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "ui" / "index.html"
DEFAULT_OUTPUT = ROOT / "build" / "intermediate" / "QuickFileCopy.html"


def bundle(output_path: Path | None = None) -> Path:
    target = output_path or DEFAULT_OUTPUT
    if not SOURCE.is_file():
        raise SystemExit(f"UI template was not found: {SOURCE}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, target)
    print(f"[ok] bundled UI: {target.relative_to(ROOT)}")
    return target


if __name__ == "__main__":
    bundle()
