"""Background copy execution (QThread) plus the two dialogs it talks to:
a blocking "file exists, what do I do?" prompt and a live progress dialog.
"""
from __future__ import annotations

import os
import threading
import traceback
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from PySide6.QtCore import QObject

from fastestcopy.engine.copier import run_copy_jobs, run_copy_multi
from fastestcopy.engine.policy import ConflictAction, ConflictPolicy
from fastestcopy.engine.preview import PreviewResult, scan_preview
from fastestcopy.engine.scanner import CopyJob

from .copy_log import write_error_log
from .i18n import tr


def format_eta(seconds: float) -> str:
    """1時間2分 / 3分45秒 / 12秒 (or "1h 2m" / "3m 45s" / "12s" in English) -
    drops the leading unit(s) that are zero.
    """
    seconds = max(int(seconds), 0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return tr("eta_hours_minutes").format(h=hours, m=minutes)
    if minutes:
        return tr("eta_minutes_seconds").format(m=minutes, s=secs)
    return tr("eta_seconds").format(s=secs)


class ConflictAsker(QObject):
    """Lets a background copy thread block on a modal Qt dialog running on
    the GUI thread. Qt.BlockingQueuedConnection makes the emitting
    (worker) thread wait until the GUI-thread slot returns, which is
    exactly the semantics ConflictPolicy.ASK needs.

    The result is handed back via a plain attribute on this same QObject
    rather than a mutable argument on the signal: PySide6 marshals signal
    arguments across a blocking queued connection by value, so a list
    passed as an argument and mutated inside the slot does *not* reflect
    back to the emitting thread's copy (confirmed by manual GUI testing -
    every answer silently came back as SKIP). `self` itself is the same
    Python object in both threads, so plain attributes work.
    """

    ask_signal = Signal(str, str)

    def __init__(self, parent_widget):
        super().__init__()
        self._parent_widget = parent_widget
        self._lock = threading.Lock()
        self._result = ConflictAction.SKIP
        self.ask_signal.connect(self._handle_ask, Qt.BlockingQueuedConnection)

    def ask(self, src_path: str, dst_path: str) -> ConflictAction:
        # Serialize concurrent conflicts from different worker threads so
        # only one dialog is ever shown at a time.
        with self._lock:
            self.ask_signal.emit(src_path, dst_path)
            return self._result

    @Slot(str, str)
    def _handle_ask(self, src_path: str, dst_path: str) -> None:
        reply = QMessageBox.question(
            self._parent_widget,
            tr("conflict_title"),
            tr("conflict_body").format(src=src_path, dst=dst_path),
            QMessageBox.Yes | QMessageBox.No,
        )
        self._result = ConflictAction.COPY if reply == QMessageBox.Yes else ConflictAction.SKIP


class CopyWorker(QThread):
    progress = Signal(dict)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        items: Optional[list[tuple[str, str]]],
        policy: ConflictPolicy,
        ask_callback=None,
        small_workers: Optional[int] = None,
        large_chunk_workers: Optional[int] = None,
        buffer_mb: int = 4,
        preallocate_large: bool = True,
        jobs: Optional[list[CopyJob]] = None,
        parent=None,
    ):
        """Copies either `items` ([(src, dst), ...], each keeping its own
        name at dst - Explorer-style paste, walked and copied together)
        or a pre-scanned `jobs` list (from preview.scan_preview, already
        confirmed by the user) which skips scanning entirely and copies
        exactly those jobs. Pass exactly one of items/jobs.
        """
        super().__init__(parent)
        self.items = items
        self.jobs = jobs
        self.policy = policy
        self.ask_callback = ask_callback
        self.small_workers = small_workers
        self.large_chunk_workers = large_chunk_workers
        self.buffer_mb = buffer_mb
        self.preallocate_large = preallocate_large
        self.stop_event = threading.Event()

    def cancel(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            kwargs = dict(
                policy=self.policy,
                ask_callback=self.ask_callback,
                small_workers=self.small_workers,
                large_chunk_workers=self.large_chunk_workers,
                buffer_size=self.buffer_mb * 1024 * 1024,
                preallocate_large=self.preallocate_large,
                progress_cb=lambda snap: self.progress.emit(snap),
                stop_event=self.stop_event,
            )
            if self.jobs is not None:
                stats = run_copy_jobs(self.jobs, **kwargs)
            else:
                stats = run_copy_multi(self.items, **kwargs)
            self.finished_ok.emit(stats.snapshot())
        except Exception:  # noqa: BLE001 - full traceback, not just str(e), goes to the error log
            self.failed.emit(traceback.format_exc())


class CopyProgressDialog(QDialog):
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("copying_title"))
        self.setModal(True)
        self.resize(440, 150)

        self.info_label = QLabel(tr("scanning_title"))
        self.info_label.setWordWrap(True)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate: total size unknown until scan completes

        self.open_log_button = QPushButton(tr("open_log_button"))
        self.open_log_button.setVisible(False)
        self.open_log_button.clicked.connect(self._on_open_log)

        self.close_button = QPushButton(tr("cancel_button"))
        self.close_button.clicked.connect(self._on_button)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.open_log_button)
        btn_row.addStretch()
        btn_row.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.info_label)
        layout.addWidget(self.progress_bar)
        layout.addLayout(btn_row)

        self._done = False
        self.log_path: Optional[str] = None

    def _on_button(self) -> None:
        if self._done:
            self.accept()
        else:
            self.cancel_requested.emit()
            self.close_button.setEnabled(False)
            self.info_label.setText(tr("cancelling"))

    @Slot(dict)
    def update_progress(self, snap: dict) -> None:
        mb = f"{snap['bytes_copied'] / (1024 * 1024):.1f}"
        lines = [
            tr("copy_progress_copied").format(files=snap["files_copied"], mb=mb),
            tr("copy_progress_speed").format(
                mbps=f"{snap['mb_per_sec']:.1f}", fps=f"{snap['files_per_sec']:.0f}"
            ),
            tr("copy_progress_skip_err").format(
                skipped=snap["files_skipped"], errors=snap["error_count"]
            ),
        ]
        if snap["progress_pct"] is not None:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(int(snap["progress_pct"] * 10))
            lines.append(tr("copy_progress_pct").format(pct=f"{snap['progress_pct']:.1f}"))
            if snap["eta_sec"] is not None:
                lines.append(tr("copy_progress_eta").format(eta=format_eta(snap["eta_sec"])))
        else:
            self.progress_bar.setRange(0, 0)  # still scanning: total size not known yet
        self.info_label.setText("\n".join(lines))

    def show_done(self, snap: dict) -> None:
        self._done = True
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        mb = f"{snap['bytes_copied'] / (1024 * 1024):.1f}"
        sec = f"{snap['elapsed_sec']:.1f}"
        text = tr("copy_done_summary").format(files=snap["files_copied"], mb=mb, sec=sec)
        text += "\n" + tr("copy_progress_skip_err").format(
            skipped=snap["files_skipped"], errors=snap["error_count"]
        )
        if snap["error_count"] > 0:
            self.log_path = write_error_log(snap["errors"])
            self.open_log_button.setVisible(True)
            text += "\n" + tr("error_log_line").format(path=self.log_path)
        self.info_label.setText(text)
        self.close_button.setText(tr("close_button"))
        self.close_button.setEnabled(True)

    def show_failed(self, message: str) -> None:
        self._done = True
        self.log_path = write_error_log([], fatal=message)
        self.open_log_button.setVisible(True)
        first_line = message.strip().splitlines()[-1] if message.strip() else message
        self.info_label.setText(
            tr("copy_failed_summary").format(message=first_line, path=self.log_path)
        )
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.close_button.setText(tr("close_button"))
        self.close_button.setEnabled(True)

    def _on_open_log(self) -> None:
        if self.log_path:
            os.startfile(self.log_path)


class ScanWorker(QThread):
    """Runs preview.scan_preview off the GUI thread for "scan first, show
    what would happen, then confirm" copies - same shape as CopyWorker,
    but read-only and much lighter (no dialogs, one signal instead of
    three).
    """

    finished_ok = Signal(object)  # PreviewResult
    failed = Signal(str)

    def __init__(self, items: list[tuple[str, str]], policy: ConflictPolicy, parent=None):
        super().__init__(parent)
        self.items = items
        self.policy = policy
        self.stop_event = threading.Event()

    def cancel(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            result = scan_preview(self.items, self.policy, stop_event=self.stop_event)
            self.finished_ok.emit(result)
        except Exception:  # noqa: BLE001
            self.failed.emit(traceback.format_exc())


class ScanProgressDialog(QDialog):
    """Small modal shown while ScanWorker walks the source tree(s) - no
    live count (the walk is read-only and usually much faster than the
    copy itself), just an indeterminate spinner and a cancel option.
    """

    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("scanning_title"))
        self.setModal(True)
        self.resize(360, 120)

        self.info_label = QLabel(tr("scanning_label"))
        self.info_label.setWordWrap(True)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)

        self.close_button = QPushButton(tr("cancel_button"))
        self.close_button.clicked.connect(self._on_cancel)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.info_label)
        layout.addWidget(self.progress_bar)
        layout.addLayout(btn_row)

    def _on_cancel(self) -> None:
        self.cancel_requested.emit()
        self.close_button.setEnabled(False)
        self.info_label.setText(tr("cancelling"))


def format_preview_summary(result: PreviewResult, free_bytes: Optional[int] = None) -> str:
    """Confirmation text shown before an actually-scanned copy starts:
    how many of the total were already up to date vs. need copying, plus
    a free-space warning if `free_bytes` (the destination drive's current
    free space) can't cover what the scan found to copy.
    """
    mb = f"{result.copy_bytes / (1024 * 1024):.1f}"
    lines = [
        tr("preview_total").format(total=result.total_files),
        tr("preview_to_copy").format(n=len(result.to_copy), mb=mb),
        tr("preview_to_skip").format(n=len(result.to_skip)),
    ]
    if result.to_ask:
        lines.append(tr("preview_to_ask").format(n=len(result.to_ask)))
    if result.errors:
        lines.append(tr("preview_errors").format(n=len(result.errors)))
    if free_bytes is not None and free_bytes < result.copy_bytes:
        free_mb = f"{free_bytes / (1024 * 1024):.1f}"
        lines.append("")
        lines.append(tr("preview_space_warning").format(required=mb, free=free_mb))
    lines.append("")
    lines.append(tr("preview_confirm_prompt"))
    return "\n".join(lines)
