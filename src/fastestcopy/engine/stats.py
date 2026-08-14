"""Thread-safe progress/throughput aggregation for a copy run."""
from __future__ import annotations

import threading
import time


class CopyStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.bytes_copied = 0
        self.bytes_skipped = 0
        self.files_copied = 0
        self.files_skipped = 0
        self.dirs_created = 0
        self.errors: list[tuple[str, str]] = []
        self.start_time = time.monotonic()
        self.end_time: float | None = None
        # Running total of what the scanner has discovered so far (bytes/
        # files), independent of how much has actually been copied - lets
        # snapshot() report a real percentage/ETA once scanning is done,
        # instead of only "N copied so far" with no known target.
        self.total_bytes_found = 0
        self.total_files_found = 0
        self.scan_done = False

    def add_bytes(self, n: int) -> None:
        with self._lock:
            self.bytes_copied += n

    def add_file(self) -> None:
        with self._lock:
            self.files_copied += 1

    def add_skip(self, size: int = 0) -> None:
        with self._lock:
            self.files_skipped += 1
            self.bytes_skipped += size

    def add_dir(self) -> None:
        with self._lock:
            self.dirs_created += 1

    def add_found(self, size: int) -> None:
        with self._lock:
            self.total_bytes_found += size
            self.total_files_found += 1

    def mark_scan_done(self) -> None:
        with self._lock:
            self.scan_done = True

    def add_error(self, path: str, exc: Exception | str) -> None:
        with self._lock:
            self.errors.append((path, str(exc)))

    def finish(self) -> None:
        with self._lock:
            self.end_time = time.monotonic()

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = max((self.end_time or time.monotonic()) - self.start_time, 1e-9)
            mb_per_sec = self.bytes_copied / elapsed / (1024 * 1024)

            # bytes_processed (copied + skipped) - not bytes_copied alone -
            # is the right basis for progress%/ETA, so a run with lots of
            # skips still reaches 100% instead of stalling below it; MB/s
            # above stays based on bytes actually written, unaffected.
            progress_pct = None
            eta_sec = None
            if self.scan_done and self.total_bytes_found > 0:
                bytes_processed = self.bytes_copied + self.bytes_skipped
                progress_pct = min(bytes_processed / self.total_bytes_found * 100, 100.0)
                if mb_per_sec > 0:
                    remaining = max(self.total_bytes_found - bytes_processed, 0)
                    eta_sec = remaining / (mb_per_sec * 1024 * 1024)

            return {
                "bytes_copied": self.bytes_copied,
                "files_copied": self.files_copied,
                "files_skipped": self.files_skipped,
                "dirs_created": self.dirs_created,
                "elapsed_sec": elapsed,
                "mb_per_sec": mb_per_sec,
                "files_per_sec": self.files_copied / elapsed,
                "error_count": len(self.errors),
                "errors": list(self.errors),
                "done": self.end_time is not None,
                "total_bytes_found": self.total_bytes_found,
                "total_files_found": self.total_files_found,
                "scan_done": self.scan_done,
                "progress_pct": progress_pct,
                "eta_sec": eta_sec,
            }
