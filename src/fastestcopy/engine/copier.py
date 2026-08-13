"""Top-level orchestration: scan pipeline + two worker pools (small-file
whole-copy pool, large-file chunked-parallel pool) running concurrently.
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from . import planner, winio
from .policy import AskCallback, ConflictAction, ConflictPolicy, resolve_conflict
from .scanner import CopyJob, scan_and_enqueue
from .stats import CopyStats

ProgressCallback = Callable[[dict], None]


def _copy_small_job(
    job: CopyJob,
    policy: ConflictPolicy,
    ask_callback: Optional[AskCallback],
    stats: CopyStats,
    buffer_size: int,
    stop_event: threading.Event,
) -> None:
    if stop_event.is_set():
        return
    try:
        action = resolve_conflict(job.src, job.dst, policy, ask_callback)
        if action is ConflictAction.SKIP:
            stats.add_skip()
            return
        winio.raw_copy_file(job.src, job.dst, buffer_size)
        # CopyFileExW already preserves last-write-time and attributes.
        stats.add_bytes(job.size)
        stats.add_file()
    except Exception as e:  # noqa: BLE001 - surfaced via stats, not fatal to the run
        stats.add_error(job.src, e)


def _copy_large_job(
    job: CopyJob,
    policy: ConflictPolicy,
    ask_callback: Optional[AskCallback],
    stats: CopyStats,
    chunk_workers: int,
    buffer_size: int,
    preallocate: bool,
    stop_event: threading.Event,
) -> None:
    if stop_event.is_set():
        return
    try:
        action = resolve_conflict(job.src, job.dst, policy, ask_callback)
        if action is ConflictAction.SKIP:
            stats.add_skip()
            return
        winio.copy_large_file_parallel(
            job.src,
            job.dst,
            job.size,
            chunk_workers=chunk_workers,
            buffer_size=buffer_size,
            preallocate=preallocate,
            on_bytes=stats.add_bytes,
        )
        winio.copy_file_times_and_attrs(job.src, job.dst)
        stats.add_file()
    except Exception as e:  # noqa: BLE001
        stats.add_error(job.src, e)


def run_copy(
    src_root: str,
    dst_root: str,
    *,
    policy: ConflictPolicy = ConflictPolicy.SKIP,
    ask_callback: Optional[AskCallback] = None,
    small_threshold: int = planner.SMALL_FILE_THRESHOLD,
    small_workers: Optional[int] = None,
    large_file_concurrency: Optional[int] = None,
    large_chunk_workers: Optional[int] = None,
    buffer_size: int = winio.DEFAULT_BUFFER_SIZE,
    preallocate_large: bool = True,
    progress_cb: Optional[ProgressCallback] = None,
    progress_interval: float = 0.2,
    stop_event: Optional[threading.Event] = None,
) -> CopyStats:
    stats = CopyStats()
    stop_event = stop_event or threading.Event()
    small_workers = small_workers or planner.suggest_small_workers()
    large_file_concurrency = large_file_concurrency or planner.suggest_large_file_concurrency()
    large_chunk_workers = large_chunk_workers or planner.suggest_large_chunk_workers()

    job_queue: "queue.Queue[CopyJob]" = queue.Queue(maxsize=20000)
    scan_done = threading.Event()

    scanner_thread = threading.Thread(
        target=scan_and_enqueue,
        args=(src_root, dst_root, job_queue, stats, stop_event, scan_done),
        daemon=True,
        name="fc-scanner",
    )
    scanner_thread.start()

    reporter_stop = threading.Event()
    reporter_thread = None
    if progress_cb is not None:
        def _report_loop() -> None:
            while not reporter_stop.is_set():
                progress_cb(stats.snapshot())
                reporter_stop.wait(progress_interval)

        reporter_thread = threading.Thread(target=_report_loop, daemon=True, name="fc-reporter")
        reporter_thread.start()

    with ThreadPoolExecutor(max_workers=small_workers, thread_name_prefix="fc-small") as small_pool, \
         ThreadPoolExecutor(max_workers=large_file_concurrency, thread_name_prefix="fc-largejob") as large_pool:
        futures = []
        while True:
            if stop_event.is_set():
                break
            try:
                job = job_queue.get(timeout=0.2)
            except queue.Empty:
                if scan_done.is_set() and job_queue.empty():
                    break
                continue

            if job.size >= small_threshold:
                futures.append(
                    large_pool.submit(
                        _copy_large_job,
                        job,
                        policy,
                        ask_callback,
                        stats,
                        large_chunk_workers,
                        buffer_size,
                        preallocate_large,
                        stop_event,
                    )
                )
            else:
                futures.append(
                    small_pool.submit(
                        _copy_small_job, job, policy, ask_callback, stats, buffer_size, stop_event
                    )
                )

        for f in futures:
            f.result()

    scanner_thread.join()

    if reporter_thread is not None:
        reporter_stop.set()
        reporter_thread.join()
        progress_cb(stats.snapshot())  # final update reflecting done=False until finish() below

    stats.finish()
    if progress_cb is not None:
        progress_cb(stats.snapshot())
    return stats
