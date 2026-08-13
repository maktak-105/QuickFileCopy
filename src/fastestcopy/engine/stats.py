"""Thread-safe progress/throughput aggregation for a copy run."""
from __future__ import annotations

import threading
import time


class CopyStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.bytes_copied = 0
        self.files_copied = 0
        self.files_skipped = 0
        self.dirs_created = 0
        self.errors: list[tuple[str, str]] = []
        self.start_time = time.monotonic()
        self.end_time: float | None = None

    def add_bytes(self, n: int) -> None:
        with self._lock:
            self.bytes_copied += n

    def add_file(self) -> None:
        with self._lock:
            self.files_copied += 1

    def add_skip(self) -> None:
        with self._lock:
            self.files_skipped += 1

    def add_dir(self) -> None:
        with self._lock:
            self.dirs_created += 1

    def add_error(self, path: str, exc: Exception | str) -> None:
        with self._lock:
            self.errors.append((path, str(exc)))

    def finish(self) -> None:
        with self._lock:
            self.end_time = time.monotonic()

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = max((self.end_time or time.monotonic()) - self.start_time, 1e-9)
            return {
                "bytes_copied": self.bytes_copied,
                "files_copied": self.files_copied,
                "files_skipped": self.files_skipped,
                "dirs_created": self.dirs_created,
                "elapsed_sec": elapsed,
                "mb_per_sec": self.bytes_copied / elapsed / (1024 * 1024),
                "files_per_sec": self.files_copied / elapsed,
                "error_count": len(self.errors),
                "done": self.end_time is not None,
            }
