from __future__ import annotations

import os

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from fastestcopy.engine.policy import ConflictPolicy

from .copy_dialog import ConflictAsker, CopyProgressDialog, CopyWorker, format_eta
from .elevate import is_admin, relaunch_as_admin
from .file_pane import FilePane
from .settings_dialog import CopySettings, SettingsDialog

_POLICY_LABELS = {
    "スキップ (既存を保持)": ConflictPolicy.SKIP,
    "上書き": ConflictPolicy.OVERWRITE,
    "新しい方のみ上書き": ConflictPolicy.OVERWRITE_IF_NEWER,
    "毎回確認": ConflictPolicy.ASK,
}


def _dest_name_for(src: str) -> str:
    """Name to paste `src` under at the destination. Usually just its own
    basename, but a bare drive or UNC share root (e.g. "C:\\" or
    "\\\\server\\share") has no basename via os.path.basename - that
    returns "" for both, which would silently collapse the paste into
    dst itself instead of a subfolder - so fall back to a name derived
    from the drive letter / host+share instead.
    """
    name = os.path.basename(src.rstrip("\\/"))
    if name:
        return name
    drive, _tail = os.path.splitdrive(src)
    drive = drive.strip("\\")
    if drive.endswith(":"):
        return drive.rstrip(":")  # "C:" -> "C"
    return drive.replace("\\", "_") or "root"  # "\\SERVER\Share" -> "SERVER_Share"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FastestCopy" + (" (管理者)" if is_admin() else ""))
        self.resize(1300, 750)

        self.settings = CopySettings()
        self.conflict_asker = ConflictAsker(self)
        self.worker: CopyWorker | None = None

        # Start on the drive-list ("This PC") view rather than drilling into
        # the user folder, so the nav tree opens uncommitted to any drive.
        self.source_pane = FilePane(None, "ソース")
        self.target_pane = FilePane(None, "ターゲット")

        self.policy_combo = QComboBox()
        for label in _POLICY_LABELS:
            self.policy_combo.addItem(label)

        self.show_hidden_checkbox = QCheckBox("隠しファイル/フォルダを表示")
        self.show_hidden_checkbox.setChecked(False)
        self.show_hidden_checkbox.toggled.connect(self._on_show_hidden_toggled)

        self.copy_button = QPushButton("コピー →")
        self.copy_button.setFixedHeight(40)
        self.copy_button.clicked.connect(self._on_copy_clicked)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)

        self.privilege_label = QLabel(
            "管理者権限: あり\n(巨大ファイル高速化 有効)"
            if is_admin()
            else "管理者権限: なし\n(ツールメニューから昇格可能)"
        )
        self.privilege_label.setWordWrap(True)

        center = QWidget()
        center.setFixedWidth(220)
        center_layout = QVBoxLayout(center)
        center_layout.addStretch()
        center_layout.addWidget(QLabel("競合ポリシー:"))
        center_layout.addWidget(self.policy_combo)
        center_layout.addWidget(self.show_hidden_checkbox)
        center_layout.addWidget(self.copy_button)
        center_layout.addWidget(self.progress_bar)
        center_layout.addWidget(self.progress_label)
        center_layout.addWidget(self.privilege_label)
        center_layout.addStretch()

        splitter = QSplitter()
        splitter.addWidget(self.source_pane)
        splitter.addWidget(center)
        splitter.addWidget(self.target_pane)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 4)

        self.setCentralWidget(splitter)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("準備完了")

        self._build_menu()

    def _build_menu(self) -> None:
        tools_menu = self.menuBar().addMenu("ツール")

        settings_action = QAction("設定...", self)
        settings_action.triggered.connect(self._open_settings)
        tools_menu.addAction(settings_action)

        if not is_admin():
            elevate_action = QAction("管理者として再起動...", self)
            elevate_action.triggered.connect(self._on_elevate)
            tools_menu.addAction(elevate_action)

    def _on_show_hidden_toggled(self, checked: bool) -> None:
        self.source_pane.set_show_hidden(checked)
        self.target_pane.set_show_hidden(checked)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            self.settings = dlg.result_settings()

    def _on_elevate(self) -> None:
        reply = QMessageBox.question(
            self,
            "管理者として再起動",
            "巨大ファイルの事前領域確保による高速化には管理者権限が必要です。\n"
            "アプリを管理者として再起動しますか?(UACの確認が表示されます)",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if relaunch_as_admin():
            self.close()
        else:
            QMessageBox.warning(self, "エラー", "管理者としての再起動に失敗しました。")

    def _validate_copy_pair(self, src: str, dst_abs: str) -> bool:
        """False (with an error dialog) if src can't be copied into the
        folder dst_abs resolves to: same folder, or dst nested inside src.
        """
        src_abs = os.path.normcase(os.path.abspath(src))
        if src_abs == dst_abs:
            QMessageBox.warning(self, "エラー", "コピー元とコピー先が同じフォルダです。")
            return False
        # A drive/UNC share root's abspath already ends in a separator
        # (e.g. "c:\\"), so appending another os.sep below would double it
        # up and silently defeat this check - strip any trailing separator
        # first so the prefix always ends in exactly one.
        src_prefix = src_abs.rstrip(os.sep) + os.sep
        if dst_abs.startswith(src_prefix):
            QMessageBox.warning(self, "エラー", "コピー先がコピー元の内部にあります。")
            return False
        return True

    def _on_copy_clicked(self) -> None:
        dst = self.target_pane.selected_path()
        if not dst or not os.path.isdir(dst):
            QMessageBox.warning(self, "エラー", "コピー先フォルダを選択してください。")
            return
        dst_abs = os.path.normcase(os.path.abspath(dst))

        rows = self.source_pane.selected_rows()
        # Nothing explicitly selected -> fall back to whatever folder the
        # pane is showing/has selected as a single-item source.
        sources = rows if rows else [self.source_pane.selected_path()]
        if not sources[0] or not os.path.exists(sources[0]):
            QMessageBox.warning(self, "エラー", "コピー元を選択してください。")
            return

        # Every item - file or folder - keeps its own name at dst, same as
        # an Explorer paste: a folder becomes a same-named subfolder there
        # rather than merging its contents directly into dst.
        items = []
        for src in sources:
            if not self._validate_copy_pair(src, dst_abs):
                return
            items.append((src, os.path.join(dst, _dest_name_for(src))))

        policy = _POLICY_LABELS[self.policy_combo.currentText()]
        ask_cb = self.conflict_asker.ask if policy is ConflictPolicy.ASK else None

        dialog = CopyProgressDialog(self)
        worker = CopyWorker(
            items,
            policy,
            ask_callback=ask_cb,
            small_workers=self.settings.small_workers,
            large_chunk_workers=self.settings.large_chunk_workers,
            buffer_mb=self.settings.buffer_mb,
            preallocate_large=self.settings.preallocate_large,
            parent=self,
        )
        worker.progress.connect(dialog.update_progress)
        worker.progress.connect(self._update_center_progress)
        worker.finished_ok.connect(lambda snap: self._on_copy_done(dialog, snap))
        worker.failed.connect(dialog.show_failed)
        worker.failed.connect(self._clear_center_progress)
        dialog.cancel_requested.connect(worker.cancel)

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.worker = worker
        worker.start()
        dialog.exec()
        self.target_pane.refresh()

    def _update_center_progress(self, snap: dict) -> None:
        lines = [f"{snap['files_copied']} ファイル / {snap['bytes_copied'] / (1024 * 1024):.1f} MB"]
        if snap["progress_pct"] is not None:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(int(snap["progress_pct"] * 10))
            lines.append(f"進捗: {snap['progress_pct']:.1f}%")
            if snap["eta_sec"] is not None:
                lines.append(f"残り約 {format_eta(snap['eta_sec'])}")
        else:
            self.progress_bar.setRange(0, 0)  # still scanning: total size not known yet
            lines.append("スキャン中...")
        self.progress_label.setText("\n".join(lines))

    def _clear_center_progress(self, *_args) -> None:
        self.progress_bar.setVisible(False)
        self.progress_label.setText("")

    def _on_copy_done(self, dialog: CopyProgressDialog, snap: dict) -> None:
        dialog.show_done(snap)
        self._clear_center_progress()
        self.statusBar().showMessage(
            f"完了: {snap['files_copied']} ファイル, "
            f"{snap['bytes_copied'] / (1024 * 1024):.1f} MB, "
            f"{snap['elapsed_sec']:.1f} 秒",
            10000,
        )
