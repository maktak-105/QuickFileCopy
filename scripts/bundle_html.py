from __future__ import annotations

from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "templates" / "index.html"
OUTPUT = ROOT / "core" / "native" / "resources" / "QuickFileCopy.html"
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SOURCE = REPO_ROOT / "src" / "ui" / "index.html"
OUTPUT = REPO_ROOT / "build" / "intermediate" / "QuickFileCopy.html"


def main() -> None:
def bundle(output_path: Path | None = None) -> Path:
    target = output_path or OUTPUT
    if not SOURCE.exists():
        raise SystemExit(f"UI template was not found: {SOURCE}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, OUTPUT)
    print(f"[ok] bundled UI: {OUTPUT.relative_to(ROOT)}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, target)
    print(f"[ok] bundled UI: {target.relative_to(REPO_ROOT)}")
    return target


def main() -> None:
    bundle()


if __name__ == "__main__":
    main()
