"""Persists per-copy error details (which file, why) to a plain-text log
file. The completion dialog only has room for a count ("エラー: N件") -
that's not enough to actually go fix what failed, so this writes the full
list (and any fatal top-level error) somewhere the user can open afterward.
"""
from __future__ import annotations

import os
import time

LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "FastestCopy", "logs")


def write_error_log(errors: list[tuple[str, str]], fatal: str | None = None) -> str | None:
    """Writes a timestamped log listing each failed file and its reason,
    plus an optional fatal error (e.g. a full traceback). Returns the log
    file path, or None if there was nothing to log.
    """
    if not errors and not fatal:
        return None

    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, time.strftime("copy_errors_%Y%m%d_%H%M%S.log"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"FastestCopy エラーログ - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 60 + "\n\n")
        if fatal:
            f.write("致命的エラー:\n")
            f.write(fatal.rstrip() + "\n\n")
        if errors:
            f.write(f"ファイル単位のエラー ({len(errors)} 件):\n")
            for path_, reason in errors:
                f.write(f"  {path_}\n    -> {reason}\n")
    return path
