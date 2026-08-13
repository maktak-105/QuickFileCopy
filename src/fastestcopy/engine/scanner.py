"""Producer side of the copy pipeline: walk the source tree, create matching
destination directories immediately, and enqueue file jobs so the consumer
worker pools can start copying before enumeration finishes.
"""
from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass

from .stats import CopyStats


@dataclass
class CopyJob:
    src: str
    dst: str
    size: int
    mtime_ns: int


def scan_and_enqueue(
    src_root: str,
    dst_root: str,
    job_queue: "queue.Queue[CopyJob]",
    stats: CopyStats,
    stop_event: threading.Event,
    scan_done: threading.Event,
) -> None:
    try:
        os.makedirs(dst_root, exist_ok=True)
        stats.add_dir()
    except OSError as e:
        stats.add_error(dst_root, e)
        scan_done.set()
        return

    stack = [(src_root, dst_root)]
    while stack and not stop_event.is_set():
        cur_src, cur_dst = stack.pop()
        try:
            entries = list(os.scandir(cur_src))
        except OSError as e:
            stats.add_error(cur_src, e)
            continue
        for entry in entries:
            if stop_event.is_set():
                break
            dst_path = os.path.join(cur_dst, entry.name)
            try:
                if entry.is_dir(follow_symlinks=False):
                    os.makedirs(dst_path, exist_ok=True)
                    stats.add_dir()
                    stack.append((entry.path, dst_path))
                else:
                    st = entry.stat(follow_symlinks=False)
                    job_queue.put(CopyJob(entry.path, dst_path, st.st_size, st.st_mtime_ns))
            except OSError as e:
                stats.add_error(entry.path, e)
    scan_done.set()
