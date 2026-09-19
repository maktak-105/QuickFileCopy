"""Decides worker/chunk counts. File copying is I/O-bound, so the right
concurrency depends far more on the storage device's queue depth than on
CPU core count - but CPU count is a reasonable, dependency-free default that
scales sensibly, and every value here is overridable by the caller (CLI
flag / GUI setting) once a real benchmark run shows a better number for a
given machine/drive.
"""
from __future__ import annotations

import os

SMALL_FILE_THRESHOLD = 64 * 1024 * 1024  # files >= this are copied via chunked parallel path


def suggest_small_workers(cpu_count: int | None = None) -> int:
    cpu_count = cpu_count or os.cpu_count() or 4
    # Small-file copying is dominated by open/close/metadata syscall latency,
    # not throughput per stream, so oversubscribing past core count helps
    # hide that latency - but only up to a point. Benchmarked on a 12-core
    # machine: 8->2700 files/s, 20->3600 files/s (peak), 64->800 files/s
    # (thrashing). ~2x core count landed at/near the peak, so that's the
    # default; large deployments should re-tune via --small-workers once
    # they've run the benchmark harness on their own hardware.
    return max(8, min(32, cpu_count * 2))


def suggest_large_file_concurrency() -> int:
    # How many *different* large files may be chunk-copied at once.
    return 2


def suggest_large_chunk_workers(cpu_count: int | None = None) -> int:
    cpu_count = cpu_count or os.cpu_count() or 4
    return max(2, min(16, cpu_count))
