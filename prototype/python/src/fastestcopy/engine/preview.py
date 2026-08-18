"""Dry-run scan: walks the source tree(s) and classifies each file as
"will copy" / "will skip" / "needs a live decision" (ASK policy) without
touching the destination filesystem at all - unlike scanner.py's
scan_and_enqueue/scan_items_and_enqueue, which create destination
directories as they walk (fine for the normal overlapped scan+copy flow,
wrong for a preview the user hasn't confirmed yet).

Feeds a "scan first, show what would happen, then confirm" flow: the
resulting to_copy/to_ask job lists can be handed directly to
copier.run_copy_jobs to actually copy, without re-walking the tree.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Optional

from .policy import ConflictAction, ConflictPolicy, resolve_conflict
from .scanner import CopyJob


@dataclass
class PreviewResult:
    to_copy: list[CopyJob] = field(default_factory=list)
    to_skip: list[CopyJob] = field(default_factory=list)
    to_ask: list[CopyJob] = field(default_factory=list)
    dirs_found: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def total_files(self) -> int:
        return len(self.to_copy) + len(self.to_skip) + len(self.to_ask)

    @property
    def copy_bytes(self) -> int:
        return sum(job.size for job in self.to_copy) + sum(job.size for job in self.to_ask)


def _classify(job: CopyJob, policy: ConflictPolicy, result: PreviewResult) -> None:
    if not os.path.exists(job.dst):
        result.to_copy.append(job)
        return
    if policy is ConflictPolicy.ASK:
        # Real per-file decision needs the user - resolved live during the
        # actual copy, not here.
        result.to_ask.append(job)
        return
    # SKIP/OVERWRITE/OVERWRITE_IF_NEWER are deterministic from file state
    # alone, so no ask_callback is needed to resolve them ahead of time.
    action = resolve_conflict(job.src, job.dst, policy, ask_callback=None)
    (result.to_copy if action is ConflictAction.COPY else result.to_skip).append(job)


def _preview_dir_tree(
    src_root: str,
    dst_root: str,
    policy: ConflictPolicy,
    result: PreviewResult,
    stop_event: Optional[threading.Event],
) -> None:
    stack = [(src_root, dst_root)]
    while stack:
        if stop_event is not None and stop_event.is_set():
            result.cancelled = True
            return
        cur_src, cur_dst = stack.pop()
        try:
            entries = list(os.scandir(cur_src))
        except OSError as e:
            result.errors.append((cur_src, str(e)))
            continue
        for entry in entries:
            dst_path = os.path.join(cur_dst, entry.name)
            try:
                if entry.is_dir(follow_symlinks=False):
                    result.dirs_found += 1
                    stack.append((entry.path, dst_path))
                else:
                    st = entry.stat(follow_symlinks=False)
                    _classify(CopyJob(entry.path, dst_path, st.st_size, st.st_mtime_ns), policy, result)
            except OSError as e:
                result.errors.append((entry.path, str(e)))


def scan_preview(
    items: list[tuple[str, str]],
    policy: ConflictPolicy,
    stop_event: Optional[threading.Event] = None,
) -> PreviewResult:
    """items: [(src, dst), ...], each a file or a whole directory tree -
    same shape as copier.run_copy_multi's items. Read-only: never touches
    the destination filesystem, safe to run before the user has confirmed
    anything.
    """
    result = PreviewResult()
    for src, dst in items:
        if stop_event is not None and stop_event.is_set():
            result.cancelled = True
            break
        try:
            if os.path.isdir(src):
                result.dirs_found += 1
                _preview_dir_tree(src, dst, policy, result, stop_event)
                if result.cancelled:
                    break
            else:
                st = os.stat(src)
                _classify(CopyJob(src, dst, st.st_size, st.st_mtime_ns), policy, result)
        except OSError as e:
            result.errors.append((src, str(e)))
    return result
