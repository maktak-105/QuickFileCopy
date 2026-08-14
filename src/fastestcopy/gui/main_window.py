from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
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

from fastestcopy.engine import diskspace
from fastestcopy.engine.policy import ConflictPolicy

from .copy_dialog import (
    ConflictAsker,
    CopyProgressDialog,
    CopyWorker,
    ScanProgressDialog,
    ScanWorker,
    format_eta,
    format_preview_summary,
)
from .elevate import is_admin, relaunch_as_admin, relaunch_normal
from .file_pane import FilePane
from .help_dialog import HelpDialog
from .i18n import get_language, set_language, tr
from .settings_dialog import CopySettings, SettingsDialog

_POLICY_ORDER = [
    ConflictPolicy.SKIP,
    ConflictPolicy.OVERWRITE,
    ConflictPolicy.OVERWRITE_IF_NEWER,
    ConflictPolicy.ASK,
]
_POLICY_KEYS = {
    ConflictPolicy.SKIP: "policy_skip",
    ConflictPolicy.OVERWRITE: "policy_overwrite",
    ConflictPolicy.OVERWRITE_IF_NEWER: "policy_overwrite_if_newer",
    ConflictPolicy.ASK: "policy_ask",
}

# Below this, starting a copy is pointless regardless of how much the
# source actually needs - used as a cheap upfront guard on the direct-copy
# path, which (unlike scan-then-copy) has no pre-scanned total to compare
# against.
_MIN_FREE_BYTES = 1024 * 1024


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
        self.setWindowTitle("FastestCopy" + (tr("admin_suffix") if is_admin() else ""))
        self.resize(950, 900)

        self.settings = CopySettings()
        self.conflict_asker = ConflictAsker(self)
        self.worker: CopyWorker | None = None

        # Start on the drive-list ("This PC") view rather than drilling into
        # the user folder, so the nav tree opens uncommitted to any drive.
        self.source_pane = FilePane(None, "source")
        self.target_pane = FilePane(None, "target")

        self.policy_combo = QComboBox()
        for policy in _POLICY_ORDER:
            self.policy_combo.addItem(tr(_POLICY_KEYS[policy]), policy)

        self.show_hidden_checkbox = QCheckBox(tr("show_hidden_checkbox"))
        self.show_hidden_checkbox.setChecked(False)
        self.show_hidden_checkbox.toggled.connect(self._on_show_hidden_toggled)

        self.copy_button = QPushButton(tr("copy_button"))
        self.copy_button.setFixedHeight(40)
        self.copy_button.clicked.connect(self._on_copy_clicked)

        self.scan_copy_button = QPushButton(tr("scan_copy_button"))
        self.scan_copy_button.setToolTip(tr("scan_copy_button_tooltip"))
        self.scan_copy_button.clicked.connect(self._on_scan_copy_clicked)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)

        self.privilege_label = QLabel(tr("privilege_admin") if is_admin() else tr("privilege_not_admin"))
        self.privilege_label.setWordWrap(True)

        center = QWidget()
        center.setFixedWidth(220)
        center_layout = QVBoxLayout(center)
        center_layout.addStretch()
        center_layout.addWidget(QLabel(tr("policy_group_label")))
        center_layout.addWidget(self.policy_combo)
        center_layout.addWidget(self.show_hidden_checkbox)
        center_layout.addWidget(self.copy_button)
        center_layout.addWidget(self.scan_copy_button)
        center_layout.addWidget(self.progress_bar)
        center_layout.addWidget(self.progress_label)
        center_layout.addWidget(self.privilege_label)
        center_layout.addStretch()

        # Source/target stacked vertically (each still gets its own PC /
        # ネットワーク nav tree + content pane side-by-side internally) so
        # the window stays roughly square instead of sprawling wide with
        # three side-by-side columns. Labeled and separated by a down
        # arrow so the top-to-bottom copy direction reads at a glance.
        source_label = QLabel(tr("pane_source_label"))
        source_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        target_label = QLabel(tr("pane_target_label"))
        target_label.setStyleSheet("font-weight: bold; font-size: 13px;")

        arrow_label = QLabel("▼")
        arrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow_label.setStyleSheet("font-size: 22px; color: #555;")

        panes_container = QWidget()
        panes_layout = QVBoxLayout(panes_container)
        panes_layout.setContentsMargins(0, 0, 0, 0)
        panes_layout.setSpacing(2)
        panes_layout.addWidget(source_label)
        panes_layout.addWidget(self.source_pane, 1)
        panes_layout.addWidget(arrow_label)
        panes_layout.addWidget(target_label)
        panes_layout.addWidget(self.target_pane, 1)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(panes_container)
        main_splitter.addWidget(center)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 0)

        self.setCentralWidget(main_splitter)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(tr("status_ready"))

        self._build_menu()

    def _build_menu(self) -> None:
        tools_menu = self.menuBar().addMenu(tr("menu_tools"))

        settings_action = QAction(tr("menu_settings"), self)
        settings_action.triggered.connect(self._open_settings)
        tools_menu.addAction(settings_action)

        if not is_admin():
            elevate_action = QAction(tr("menu_elevate"), self)
            elevate_action.triggered.connect(self._on_elevate)
            tools_menu.addAction(elevate_action)

        language_menu = tools_menu.addMenu(tr("menu_language"))
        lang_group = QActionGroup(self)
        lang_group.setExclusive(True)
        current = get_language()
        for lang_code, key in (("ja", "menu_lang_ja"), ("en", "menu_lang_en")):
            action = QAction(tr(key), self, checkable=True)
            action.setChecked(lang_code == current)
            action.triggered.connect(lambda _checked, code=lang_code: self._on_language_selected(code))
            lang_group.addAction(action)
            language_menu.addAction(action)

        help_menu = self.menuBar().addMenu(tr("menu_help"))
        help_action = QAction(tr("menu_help_show"), self)
        help_action.triggered.connect(self._on_show_help)
        help_menu.addAction(help_action)

    def _on_show_help(self) -> None:
        HelpDialog(self).exec()

    def _on_language_selected(self, lang: str) -> None:
        if lang == get_language():
            return
        set_language(lang)
        reply = QMessageBox.question(
            self,
            tr("restart_now_title"),
            tr("restart_now_body"),
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if relaunch_normal():
            self.close()
        else:
            QMessageBox.warning(self, tr("error_title"), tr("restart_failed"))

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
            tr("elevate_confirm_title"),
            tr("elevate_confirm_body"),
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if relaunch_as_admin():
            self.close()
        else:
            QMessageBox.warning(self, tr("error_title"), tr("elevate_failed"))

    def _validate_copy_pair(self, src: str, dst_abs: str) -> bool:
        """False (with an error dialog) if src can't be copied into the
        folder dst_abs resolves to: same folder, or dst nested inside src.
        """
        src_abs = os.path.normcase(os.path.abspath(src))
        if src_abs == dst_abs:
            QMessageBox.warning(self, tr("error_title"), tr("error_same_folder"))
            return False
        # A drive/UNC share root's abspath already ends in a separator
        # (e.g. "c:\\"), so appending another os.sep below would double it
        # up and silently defeat this check - strip any trailing separator
        # first so the prefix always ends in exactly one.
        src_prefix = src_abs.rstrip(os.sep) + os.sep
        if dst_abs.startswith(src_prefix):
            QMessageBox.warning(self, tr("error_title"), tr("error_dst_inside_src"))
            return False
        return True

    def _build_copy_items(self) -> Optional[list[tuple[str, str]]]:
        """Resolves the current source/target pane selections into
        [(src, dst), ...] pairs, showing an error dialog and returning
        None if anything's invalid. Shared by the normal copy button and
        the scan-first-then-copy button below.
        """
        dst = self.target_pane.selected_path()
        if not dst or not os.path.isdir(dst):
            QMessageBox.warning(self, tr("error_title"), tr("error_pick_dest_folder"))
            return None
        dst_abs = os.path.normcase(os.path.abspath(dst))

        rows = self.source_pane.selected_rows()
        # Nothing explicitly selected -> fall back to whatever folder the
        # pane is showing/has selected as a single-item source.
        sources = rows if rows else [self.source_pane.selected_path()]
        if not sources[0] or not os.path.exists(sources[0]):
            QMessageBox.warning(self, tr("error_title"), tr("error_pick_source"))
            return None

        # Every item - file or folder - keeps its own name at dst, same as
        # an Explorer paste: a folder becomes a same-named subfolder there
        # rather than merging its contents directly into dst.
        items = []
        for src in sources:
            if not self._validate_copy_pair(src, dst_abs):
                return None
            items.append((src, os.path.join(dst, _dest_name_for(src))))
        return items

    def _current_policy(self) -> ConflictPolicy:
        return self.policy_combo.currentData()

    def _on_copy_clicked(self) -> None:
        items = self._build_copy_items()
        if items is None:
            return

        dst = self.target_pane.selected_path()
        free = diskspace.free_bytes(dst)
        if free < _MIN_FREE_BYTES:
            QMessageBox.warning(
                self,
                tr("space_warning_none_title"),
                tr("space_warning_none_body").format(free=f"{free / (1024 * 1024):.1f}"),
            )
            return

        policy = self._current_policy()
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
        self._run_copy_worker(worker, dialog)

    def _on_scan_copy_clicked(self) -> None:
        items = self._build_copy_items()
        if items is None:
            return
        policy = self._current_policy()

        scan_dialog = ScanProgressDialog(self)
        scan_worker = ScanWorker(items, policy, parent=self)
        scan_dialog.cancel_requested.connect(scan_worker.cancel)

        outcome: dict = {}

        def on_scan_done(result):
            outcome["result"] = result
            scan_dialog.accept()

        def on_scan_failed(msg):
            outcome["error"] = msg
            scan_dialog.accept()

        scan_worker.finished_ok.connect(on_scan_done)
        scan_worker.failed.connect(on_scan_failed)

        scan_worker.start()
        scan_dialog.exec()
        scan_worker.wait()

        if "error" in outcome:
            QMessageBox.warning(self, tr("error_title"), tr("scan_error").format(error=outcome["error"]))
            return

        result = outcome.get("result")
        if result is None or result.cancelled:
            return

        jobs = result.to_copy + result.to_ask
        if not jobs:
            QMessageBox.information(
                self,
                tr("scan_done_title"),
                tr("scan_nothing_to_copy").format(total=result.total_files),
            )
            return

        dst = self.target_pane.selected_path()
        free = diskspace.free_bytes(dst)
        reply = QMessageBox.question(
            self,
            tr("copy_confirm_title"),
            format_preview_summary(result, free_bytes=free),
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        ask_cb = self.conflict_asker.ask if policy is ConflictPolicy.ASK else None
        dialog = CopyProgressDialog(self)
        worker = CopyWorker(
            None,
            policy,
            ask_callback=ask_cb,
            small_workers=self.settings.small_workers,
            large_chunk_workers=self.settings.large_chunk_workers,
            buffer_mb=self.settings.buffer_mb,
            preallocate_large=self.settings.preallocate_large,
            jobs=jobs,
            parent=self,
        )
        self._run_copy_worker(worker, dialog)

    def _run_copy_worker(self, worker: CopyWorker, dialog: CopyProgressDialog) -> None:
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
        mb = f"{snap['bytes_copied'] / (1024 * 1024):.1f}"
        lines = [tr("center_progress_line").format(files=snap["files_copied"], mb=mb)]
        if snap["progress_pct"] is not None:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(int(snap["progress_pct"] * 10))
            lines.append(tr("center_progress_pct").format(pct=f"{snap['progress_pct']:.1f}"))
            if snap["eta_sec"] is not None:
                lines.append(tr("center_progress_eta").format(eta=format_eta(snap["eta_sec"])))
        else:
            self.progress_bar.setRange(0, 0)  # still scanning: total size not known yet
            lines.append(tr("center_progress_scanning"))
        self.progress_label.setText("\n".join(lines))

    def _clear_center_progress(self, *_args) -> None:
        self.progress_bar.setVisible(False)
        self.progress_label.setText("")

    def _on_copy_done(self, dialog: CopyProgressDialog, snap: dict) -> None:
        dialog.show_done(snap)
        self._clear_center_progress()
        mb = f"{snap['bytes_copied'] / (1024 * 1024):.1f}"
        sec = f"{snap['elapsed_sec']:.1f}"
        self.statusBar().showMessage(
            tr("status_done").format(files=snap["files_copied"], mb=mb, sec=sec), 10000
        )
