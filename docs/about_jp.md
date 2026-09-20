# QuickFileCopy バージョン情報

[English about.md](about.md)

## バージョン

Ver. v1.1.0

## 対応OS

Windows 10 / 11 (64-bit)

## 表示言語

- GUI: 日本語 / English（画面右上のQDB形式トグルで即時切替）
- リポジトリ文書・配布説明書: 日本語 / English
- 選択言語はWebView2のローカル設定に保存し、再起動後も維持

## 技術スタック

- C++20 / MinGW-w64 / WinLibs MCF UCRT
- Win32 API
- Microsoft Edge WebView2
- HTML / CSS / バニラJavaScript
- Python標準ライブラリ（ビルドスクリプトのみ）

主なWindows API: `CopyFileExW`, `MoveFileExW`, `SetFileValidData`, `ReadEncryptedFileRaw`, `WriteEncryptedFileRaw`, `DeviceIoControl`, BCrypt SHA-256, Windows Security API。

実行時にPythonやサードパーティC++ランタイムを別途導入する必要はありません。WebView2 Runtimeは必要です。

## 制作者

GitHub: maktak-105
