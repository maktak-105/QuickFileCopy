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


def _scan_dir_tree(
    src_root: str,
    dst_root: str,
    job_queue: "queue.Queue[CopyJob]",
    stats: CopyStats,
    stop_event: threading.Event,
) -> None:
    """Walk src_root, mirroring its directory structure under dst_root
    (both already exist) and enqueuing a CopyJob per file. Shared by
    scan_and_enqueue (one src_root -> dst_root merge) and
    scan_items_and_enqueue (each selected item's own subtree).
    """
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

    _scan_dir_tree(src_root, dst_root, job_queue, stats, stop_event)
    scan_done.set()


def scan_items_and_enqueue(
    items: list[tuple[str, str]],
    job_queue: "queue.Queue[CopyJob]",
    stats: CopyStats,
    stop_event: threading.Event,
    scan_done: threading.Event,
) -> None:
    """Like scan_and_enqueue, but for several independently-named top-level
    items (a multi-selection) copied into one destination folder instead of
    one src_root's contents merging into dst_root: each (src, dst) pair
    keeps its own name at the destination - dst's parent directory already
    exists (it's the target pane's current folder), so unlike
    scan_and_enqueue there's no single root to os.makedirs upfront.
    """
    for src, dst in items:
        if stop_event.is_set():
            break
        try:
            if os.path.isdir(src):
                os.makedirs(dst, exist_ok=True)
                stats.add_dir()
                _scan_dir_tree(src, dst, job_queue, stats, stop_event)
            else:
                st = os.stat(src)
                job_queue.put(CopyJob(src, dst, st.st_size, st.st_mtime_ns))
        except OSError as e:
            stats.add_error(src, e)
    scan_done.set()
