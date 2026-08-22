"""Conflict resolution policy: what to do when the destination file already exists."""
from __future__ import annotations

import os
from enum import Enum
from typing import Callable, Optional

# Timestamps compared with this tolerance to account for FAT's 2s resolution
# (same tolerance robocopy uses), avoiding spurious re-copies across filesystems.
_MTIME_TOLERANCE_NS = 2_000_000_000


class ConflictPolicy(Enum):
    SKIP = "skip"                      # keep existing destination file untouched
    OVERWRITE = "overwrite"            # always replace destination
    OVERWRITE_IF_NEWER = "overwrite_if_newer"  # replace only if source is newer/different size
    ASK = "ask"                        # defer to ask_callback


class ConflictAction(Enum):
    COPY = "copy"
    SKIP = "skip"


AskCallback = Callable[[str, str], ConflictAction]


def resolve_conflict(
    src_path: str,
    dst_path: str,
    policy: ConflictPolicy,
    ask_callback: Optional[AskCallback] = None,
) -> ConflictAction:
    if not os.path.exists(dst_path):
        return ConflictAction.COPY

    if policy is ConflictPolicy.SKIP:
        return ConflictAction.SKIP
    if policy is ConflictPolicy.OVERWRITE:
        return ConflictAction.COPY
    if policy is ConflictPolicy.OVERWRITE_IF_NEWER:
        try:
            src_stat = os.stat(src_path)
            dst_stat = os.stat(dst_path)
        except OSError:
            return ConflictAction.COPY
        if (
            src_stat.st_size != dst_stat.st_size
            or src_stat.st_mtime_ns > dst_stat.st_mtime_ns + _MTIME_TOLERANCE_NS
        ):
            return ConflictAction.COPY
        return ConflictAction.SKIP
    if policy is ConflictPolicy.ASK:
        if ask_callback is None:
            return ConflictAction.SKIP
        return ask_callback(src_path, dst_path)
    return ConflictAction.SKIP
