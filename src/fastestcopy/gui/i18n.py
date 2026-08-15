"""Minimal i18n: a flat string-key -> {ja, en} lookup, with the current
choice persisted via QSettings (Windows registry under HKCU) so it
survives across launches.

Widgets are only translated at construction time - there's no live
retranslation of already-built widgets, so a language change takes
effect on the next launch rather than instantly. For a single-window
desktop app that's a reasonable trade against the complexity of tracking
every widget that would need its text rebuilt live.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings

_ORG = "FastestCopy"
_APP = "FastestCopy"

_current_lang: str | None = None

_STRINGS: dict[str, dict[str, str]] = {
    # -- window / menu --------------------------------------------------
    "admin_suffix": {"ja": " (管理者)", "en": " (Administrator)"},
    "menu_tools": {"ja": "ツール", "en": "Tools"},
    "menu_settings": {"ja": "設定...", "en": "Settings..."},
    "menu_elevate": {"ja": "管理者として再起動...", "en": "Restart as Administrator..."},
    "menu_language": {"ja": "言語", "en": "Language"},
    "menu_lang_ja": {"ja": "日本語", "en": "Japanese"},
    "menu_lang_en": {"ja": "English", "en": "English"},
    "menu_help": {"ja": "ヘルプ", "en": "Help"},
    "menu_help_show": {"ja": "ヘルプを表示", "en": "Show Help"},
    "restart_now_title": {"ja": "再起動の確認", "en": "Restart Required"},
    "restart_now_body": {
        "ja": "言語設定を反映するには、アプリを再起動する必要があります。今すぐ再起動しますか?",
        "en": "Restarting the app is required for the language change to take effect. "
        "Restart now?",
    },
    "restart_failed": {
        "ja": "再起動に失敗しました。手動でアプリを再起動してください。",
        "en": "Restart failed - please restart the app manually.",
    },
    # -- main window ------------------------------------------------------
    "pane_source_label": {"ja": "    コピー元", "en": "    Source"},
    "pane_target_label": {"ja": "    コピー先", "en": "    Target"},
    "policy_group_label": {"ja": "競合ポリシー:", "en": "Conflict policy:"},
    "policy_skip": {"ja": "スキップ (既存を保持)", "en": "Skip (keep existing)"},
    "policy_overwrite": {"ja": "上書き", "en": "Overwrite"},
    "policy_overwrite_if_newer": {"ja": "新しい方のみ上書き", "en": "Overwrite if newer"},
    "policy_ask": {"ja": "毎回確認", "en": "Ask every time"},
    "show_hidden_checkbox": {"ja": "隠しファイル/フォルダを表示", "en": "Show hidden files/folders"},
    "copy_button": {"ja": "コピー →", "en": "Copy →"},
    "scan_copy_button": {"ja": "スキャンしてコピー →", "en": "Scan then Copy →"},
    "scan_copy_button_tooltip": {
        "ja": "先に対象全体をスキャンしてコピー件数を確認し、確認後にコピーを開始します。",
        "en": "Scans everything first to show how many files will actually be copied, "
        "then starts the copy once confirmed.",
    },
    "privilege_admin": {
        "ja": "管理者権限: あり\n(巨大ファイル高速化 有効)",
        "en": "Admin privilege: yes\n(large-file speedup enabled)",
    },
    "privilege_not_admin": {
        "ja": "管理者権限: なし\n(ツールメニューから昇格可能)",
        "en": "Admin privilege: no\n(can elevate from the Tools menu)",
    },
    "status_ready": {"ja": "準備完了", "en": "Ready"},
    "status_done": {
        "ja": "完了: {files} ファイル, {mb} MB, {sec} 秒",
        "en": "Done: {files} files, {mb} MB, {sec} sec",
    },
    "error_title": {"ja": "エラー", "en": "Error"},
    "error_pick_dest_folder": {
        "ja": "コピー先フォルダを選択してください。",
        "en": "Please select a destination folder.",
    },
    "error_pick_source": {"ja": "コピー元を選択してください。", "en": "Please select a source."},
    "error_same_folder": {
        "ja": "コピー元とコピー先が同じフォルダです。",
        "en": "Source and destination are the same folder.",
    },
    "error_dst_inside_src": {
        "ja": "コピー先がコピー元の内部にあります。",
        "en": "Destination is inside the source.",
    },
    "elevate_confirm_title": {"ja": "管理者として再起動", "en": "Restart as Administrator"},
    "elevate_confirm_body": {
        "ja": "巨大ファイルの事前領域確保による高速化には管理者権限が必要です。\n"
        "アプリを管理者として再起動しますか?(UACの確認が表示されます)",
        "en": "Admin privilege is required for the large-file preallocation speedup.\n"
        "Restart the app as administrator? (a UAC prompt will appear)",
    },
    "elevate_failed": {
        "ja": "管理者としての再起動に失敗しました。",
        "en": "Failed to restart as administrator.",
    },
    "scan_error": {
        "ja": "スキャン中にエラーが発生しました:\n{error}",
        "en": "An error occurred while scanning:\n{error}",
    },
    "scan_done_title": {"ja": "スキャン完了", "en": "Scan Complete"},
    "scan_nothing_to_copy": {
        "ja": "合計 {total} 件を確認しましたが、すべて既に最新のためコピーは不要です。",
        "en": "Checked {total} items total - everything is already up to date, "
        "nothing to copy.",
    },
    "copy_confirm_title": {"ja": "コピーの確認", "en": "Confirm Copy"},
    "center_progress_line": {"ja": "{files} ファイル / {mb} MB", "en": "{files} files / {mb} MB"},
    "center_progress_pct": {"ja": "進捗: {pct}%", "en": "Progress: {pct}%"},
    "center_progress_eta": {"ja": "残り約 {eta}", "en": "About {eta} left"},
    "center_progress_scanning": {"ja": "スキャン中...", "en": "Scanning..."},
    # -- preview summary --------------------------------------------------
    "preview_total": {"ja": "合計 {total} 件を確認しました。", "en": "Checked {total} items total."},
    "preview_to_copy": {
        "ja": "コピー対象: {n} 件 ({mb} MB)",
        "en": "To copy: {n} items ({mb} MB)",
    },
    "preview_to_skip": {
        "ja": "スキップ (既に最新): {n} 件",
        "en": "To skip (already up to date): {n} items",
    },
    "preview_to_ask": {
        "ja": "要確認 (競合あり): {n} 件 - コピー中に都度確認します",
        "en": "Needs confirmation (conflicts): {n} items - asked live during the copy",
    },
    "preview_errors": {"ja": "スキャン中のエラー: {n} 件", "en": "Errors during scan: {n}"},
    "preview_space_warning": {
        "ja": "⚠ 空き容量が不足している可能性があります (必要: {required} MB / 空き: {free} MB)",
        "en": "⚠ May not have enough free space (needed: {required} MB / free: {free} MB)",
    },
    "preview_confirm_prompt": {
        "ja": "この内容でコピーを開始しますか?",
        "en": "Start the copy with this plan?",
    },
    "space_warning_none_title": {"ja": "空き容量がありません", "en": "No Free Space"},
    "space_warning_none_body": {
        "ja": "コピー先ドライブに空き容量がほとんどありません (空き: {free} MB)。\n"
        "コピー先を確認してください。",
        "en": "The destination drive has almost no free space left (free: {free} MB).\n"
        "Please check the destination.",
    },
    # -- FilePane -----------------------------------------------------
    "pc_button": {"ja": "PC", "en": "PC"},
    "pc_button_tooltip": {
        "ja": "コンピューター(ドライブ一覧)を表示",
        "en": "Show Computer (drive list)",
    },
    "up_button": {"ja": "上へ", "en": "Up"},
    "path_edit_placeholder": {
        "ja": r"パスを入力 (\\server\share もOK)",
        "en": r"Enter a path (\\server\share works too)",
    },
    "nav_network": {"ja": "ネットワーク", "en": "Network"},
    "nav_pc_alias": {"ja": "コンピューター", "en": "COMPUTER"},
    # -- copy dialogs -----------------------------------------------------
    "eta_hours_minutes": {"ja": "{h}時間{m}分", "en": "{h}h {m}m"},
    "eta_minutes_seconds": {"ja": "{m}分{s}秒", "en": "{m}m {s}s"},
    "eta_seconds": {"ja": "{s}秒", "en": "{s}s"},
    "conflict_title": {"ja": "ファイルが既に存在します", "en": "File Already Exists"},
    "conflict_body": {
        "ja": "コピー先に同名ファイルが存在します。上書きしますか?\n\n元: {src}\n先: {dst}",
        "en": "A file with the same name already exists at the destination. Overwrite?"
        "\n\nFrom: {src}\nTo: {dst}",
    },
    "copying_title": {"ja": "コピー中...", "en": "Copying..."},
    "scanning_title": {"ja": "スキャン中...", "en": "Scanning..."},
    "scanning_label": {"ja": "コピー対象を確認しています...", "en": "Checking what needs copying..."},
    "cancelling": {"ja": "キャンセル中...", "en": "Cancelling..."},
    "cancel_button": {"ja": "キャンセル", "en": "Cancel"},
    "close_button": {"ja": "閉じる", "en": "Close"},
    "open_log_button": {"ja": "エラーログを開く", "en": "Open Error Log"},
    "copy_progress_copied": {
        "ja": "{files} ファイル / {mb} MB コピー済み",
        "en": "{files} files / {mb} MB copied",
    },
    "copy_progress_speed": {"ja": "{mbps} MB/s, {fps} files/s", "en": "{mbps} MB/s, {fps} files/s"},
    "copy_progress_skip_err": {
        "ja": "スキップ: {skipped} 件, エラー: {errors} 件",
        "en": "Skipped: {skipped}, Errors: {errors}",
    },
    "copy_progress_pct": {"ja": "進捗: {pct}%", "en": "Progress: {pct}%"},
    "copy_progress_eta": {"ja": "残り時間(予測): {eta}", "en": "Time remaining (est.): {eta}"},
    "copy_done_summary": {
        "ja": "完了: {files} ファイル, {mb} MB, {sec} 秒",
        "en": "Done: {files} files, {mb} MB, {sec} sec",
    },
    "error_log_line": {"ja": "エラー詳細: {path}", "en": "Error details: {path}"},
    "copy_failed_summary": {
        "ja": "エラーが発生しました:\n{message}\n\n詳細: {path}",
        "en": "An error occurred:\n{message}\n\nDetails: {path}",
    },
    # -- settings dialog ---------------------------------------------------
    "settings_title": {"ja": "設定", "en": "Settings"},
    "settings_small_workers": {"ja": "小ファイル用スレッド数:", "en": "Small-file thread count:"},
    "settings_large_chunk": {
        "ja": "大ファイル用チャンク並列数:",
        "en": "Large-file chunk parallelism:",
    },
    "settings_buffer_size": {"ja": "転送バッファサイズ:", "en": "Transfer buffer size:"},
    "settings_preallocate": {
        "ja": "巨大ファイルの事前領域確保を試みる(管理者権限が必要)",
        "en": "Try to preallocate large files (requires admin privilege)",
    },
    # -- error log file -----------------------------------------------
    "log_header": {"ja": "FastestCopy エラーログ - {time}", "en": "FastestCopy Error Log - {time}"},
    "log_fatal_header": {"ja": "致命的エラー:", "en": "Fatal error:"},
    "log_file_errors_header": {
        "ja": "ファイル単位のエラー ({n} 件):",
        "en": "Per-file errors ({n}):",
    },
}


def get_language() -> str:
    global _current_lang
    if _current_lang is None:
        value = QSettings(_ORG, _APP).value("language", "ja")
        _current_lang = value if value in ("ja", "en") else "ja"
    return _current_lang


def set_language(lang: str) -> None:
    global _current_lang
    _current_lang = lang
    QSettings(_ORG, _APP).setValue("language", lang)


def tr(key: str) -> str:
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    lang = get_language()
    return entry.get(lang, entry.get("ja", key))
