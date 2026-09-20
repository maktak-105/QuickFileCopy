# QuickFileCopy 仕様書

[English spec.md](spec.md)

## 1. アプリ概要

- 名称: QuickFileCopy（高速ファイルコピー）
- 目的: Windows上のファイル・フォルダーを少ない設定で高速かつ安全にコピーする
- 対象OS: Windows 10 / 11 (64-bit)
- 実装: C++20 (MinGW-w64) + Win32 + WebView2 + HTML/CSS/バニラJavaScript
- バージョン: v1.1.0
- 配布形態: フラット構成のZIP

## 2. アーキテクチャ

```text
[src/ui/index.html]
        ↓ bundle_html.py
[EXE埋め込みHTML] ←WebMessage(JSON)→ [webview_main.cpp]
                                           ↓
                                    [copy_engine.cpp]
                                           ↑
                                      [main_cli.cpp]
```

- `copy_engine.cpp`: GUI非依存の走査、判定、コピー、検証、メタデータ保持
- `webview_main.cpp`: Win32ウィンドウ、WebView2、フォルダー選択、UACワーカー、JSON変換
- `main_cli.cpp`: 同じコピーエンジンを使用するCLI
- `src/ui/index.html`: フレームワーク非依存の自己完結UI。ビルド時にEXEリソースへ格納

## 3. 画面構成

| 領域 | 内容 |
| --- | --- |
| ヘッダー | アプリ名、モード、配置、競合ポリシー、日英言語切替 |
| コピー設定 | 複数コピー元、コピー先、履歴、開始・キャンセル |
| 転送サマリー | 検出、コピー済み、再開、スキップ、エラー件数 |
| 進捗 | 容量%、ファイル件数%、速度、経過時間、処理中パス |

## 4. コピーモード

| モード | 動作 |
| --- | --- |
| 高速 | 標準コピー。EAは要求せず、必要に応じ大容量ローカルファイルを非バッファ化 |
| 検証 | 標準コピー後にコピー元と一時コピーのSHA-256を比較 |
| 完全保持 | 管理者ワーカーでACL/SACL、EA、ハードリンクなどを保持 |
| 完全保持＋検証 | 完全保持にSHA-256検証を追加 |

## 5. 配置と競合

- `contents`: コピー元フォルダーの中身をコピー先へ直接マージする。
- `folder`: コピー先の下にコピー元フォルダー名を作る。
- `skip`: 同名のコピー先が存在すれば処理しない。
- `overwrite`: 同名のコピー先を安全な一時ファイルから置換する。
- `newer`: コピー元の更新時刻が新しい場合だけ置換する。

## 6. WebMessageプロトコル

すべてのメッセージは`version: 1`を持つJSONオブジェクトとする。

### JavaScript → native

| command | 主な値 | 説明 |
| --- | --- | --- |
| `pickSource` | なし | 複数選択可能なコピー元ダイアログ |
| `pickDestination` | なし | コピー先ダイアログ |
| `setSource` | `path` | 履歴からコピー元を設定 |
| `setDestination` | `path` | 履歴からコピー先を設定 |
| `startCopy` | `policy`, `mode`, `layout` | コピー開始 |
| `cancel` | なし | 実行中セッションまたは特権ワーカーをキャンセル |
| `getState` | なし | 現在の選択状態を要求 |

### native → JavaScript

| event | 説明 |
| --- | --- |
| `selection` | コピー元とコピー先の現在値 |
| `started` | コピー開始 |
| `progress` | 容量、件数、速度、経過時間、現在パス |
| `completed` | 完了、キャンセル、エラー詳細 |
| `error` | コピー開始前または特権ワーカー起動の失敗 |

## 7. 安全性

- 通常ファイルはコピー先と同じディレクトリの`.qfc-<pid>-<id>.part`へ書く。
- コピー、任意の検証、メタデータ保持が完了してから`MoveFileExW`で置換する。
- キャンセル・失敗時は未完成の一時ファイルを削除する。
- 再開ジャーナルは確定済みファイルだけをサイズと更新時刻で再検証して再利用する。
- 特権事前確保した一時ファイルは、完成まで実行ユーザーとSYSTEMだけがアクセスできるDACLにする。

## 8. メタデータ保持

- ADS: `CopyFileExW`
- EA: 完全保持モードで`NtQueryEaFile` / `NtSetEaFile`を使用。コピー元のファイルシステムがEA非対応なら「EAなし」としてコピーを継続する。
- EFS: `ReadEncryptedFileRaw` / `WriteEncryptedFileRaw`
- ACL/所有者/グループ/SACL: Windows Security API
- リパースポイント: `FSCTL_GET_REPARSE_POINT` / `FSCTL_SET_REPARSE_POINT`
- スパース: 割当範囲を問い合わせて未割当領域を再作成
- 圧縮: `FSCTL_SET_COMPRESSION`
- ハードリンク: ファイルIDを基にコピー先のリンク関係を再現

## 9. 性能方針

- 走査とコピーを有界キューで並行実行する。
- 自動並列数はローカル最大16、クラウド属性/リパースマウント12、UNC/SMB 8。
- 512 MiB以上のローカル通常ファイルは非バッファコピーを要求する。
- 完全保持モードで特権を取得できる場合だけ、対象ローカル通常ファイルを事前確保する。
- スパース、圧縮、EFS、ADS、リパース、クラウド、ネットワークは事前確保の対象外とする。

## 10. 表示言語

- 日本語とEnglishを画面右上の`🌐 English` / `🌐 日本語`ボタンで即時切替する。
- 静的ラベル、進捗、完了、キャンセル、アプリ側エラーを同じ言語へ統一する。
- 選択言語を`localStorage`の`qfc.preferences`オブジェクトへ保存する。
- Windows API由来の詳細エラー本文は、診断情報を失わないためOSが返した原文を表示する。

## 11. 既知の制限

- コード署名は未実施。
- 特権事前確保の効果は保存先が高速なローカルストレージの場合に限られる。
- 外部の`index.html`は配布しないため、UI差し替えには再ビルドが必要。
