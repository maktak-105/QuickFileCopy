"""Background copy execution (QThread) plus the two dialogs it talks to:
a blocking "file exists, what do I do?" prompt and a live progress dialog.
"""
from __future__ import annotations

import threading
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

from fastestcopy.engine.copier import run_copy, run_copy_multi
from fastestcopy.engine.policy import ConflictAction, ConflictPolicy


def format_eta(seconds: float) -> str:
    """1時間2分 / 3分45秒 / 12秒 - drops the leading unit(s) that are zero."""
    seconds = max(int(seconds), 0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}時間{minutes}分"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


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
            "ファイルが既に存在します",
            f"コピー先に同名ファイルが存在します。上書きしますか?\n\n"
            f"元: {src_path}\n先: {dst_path}",
            QMessageBox.Yes | QMessageBox.No,
        )
        self._result = ConflictAction.COPY if reply == QMessageBox.Yes else ConflictAction.SKIP


class CopyWorker(QThread):
    progress = Signal(dict)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        items: list[tuple[str, str]],
        merge: bool,
        policy: ConflictPolicy,
        ask_callback=None,
        small_workers: Optional[int] = None,
        large_chunk_workers: Optional[int] = None,
        buffer_mb: int = 4,
        preallocate_large: bool = True,
        parent=None,
    ):
        """items: [(src, dst), ...] to copy. When merge is True, items must
        be a single pair and src's *contents* merge into dst (the classic
        one-folder-into-another mode); otherwise each item is copied
        keeping its own name at its own dst (a multi-selection paste).
        """
        super().__init__(parent)
        self.items = items
        self.merge = merge
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
            if self.merge:
                src, dst = self.items[0]
                stats = run_copy(src, dst, **kwargs)
            else:
                stats = run_copy_multi(self.items, **kwargs)
            self.finished_ok.emit(stats.snapshot())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class CopyProgressDialog(QDialog):
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("コピー中...")
        self.setModal(True)
        self.resize(440, 150)

        self.info_label = QLabel("スキャン中...")
        self.info_label.setWordWrap(True)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate: total size unknown until scan completes

        self.close_button = QPushButton("キャンセル")
        self.close_button.clicked.connect(self._on_button)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.info_label)
        layout.addWidget(self.progress_bar)
        layout.addLayout(btn_row)

        self._done = False

    def _on_button(self) -> None:
        if self._done:
            self.accept()
        else:
            self.cancel_requested.emit()
            self.close_button.setEnabled(False)
            self.info_label.setText("キャンセル中...")

    @Slot(dict)
    def update_progress(self, snap: dict) -> None:
        lines = [
            f"{snap['files_copied']} ファイル / {snap['bytes_copied'] / (1024 * 1024):.1f} MB コピー済み",
            f"{snap['mb_per_sec']:.1f} MB/s, {snap['files_per_sec']:.0f} files/s",
            f"スキップ: {snap['files_skipped']} 件, エラー: {snap['error_count']} 件",
        ]
        if snap["progress_pct"] is not None:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(int(snap["progress_pct"] * 10))
            lines.append(f"進捗: {snap['progress_pct']:.1f}%")
            if snap["eta_sec"] is not None:
                lines.append(f"残り時間(予測): {format_eta(snap['eta_sec'])}")
        else:
            self.progress_bar.setRange(0, 0)  # still scanning: total size not known yet
        self.info_label.setText("\n".join(lines))

    def show_done(self, snap: dict) -> None:
        self._done = True
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.info_label.setText(
            f"完了: {snap['files_copied']} ファイル, "
            f"{snap['bytes_copied'] / (1024 * 1024):.1f} MB, "
            f"{snap['elapsed_sec']:.1f} 秒\n"
            f"スキップ: {snap['files_skipped']} 件, エラー: {snap['error_count']} 件"
        )
        self.close_button.setText("閉じる")
        self.close_button.setEnabled(True)

    def show_failed(self, message: str) -> None:
        self._done = True
        self.info_label.setText(f"エラーが発生しました:\n{message}")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.close_button.setText("閉じる")
        self.close_button.setEnabled(True)
