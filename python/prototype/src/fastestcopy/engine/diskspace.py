"""Best-effort free-space checks for the destination drive.

Not a hard guarantee - another process can consume the space between this
check and the actual write, and destination-side effects (overwriting
existing files, NTFS compression, sparse files) can make the real
requirement differ from a byte-for-byte sum of source sizes. This is a
sanity check to catch the common case (obviously not enough room) before
starting a copy, not a substitute for handling write failures.
"""
from __future__ import annotations

import shutil


def free_bytes(dst_root: str) -> int:
    return shutil.disk_usage(dst_root).free


def has_enough_space(dst_root: str, required_bytes: int) -> tuple[bool, int]:
    """Returns (enough, free_bytes_available)."""
    free = free_bytes(dst_root)
    return free >= required_bytes, free
