# 開発環境

## 実行環境

- Windows 10 / 11 (64-bit)
- Microsoft Edge WebView2 Runtime

## ビルド環境

- Python 3.11以降（ビルドスクリプトのみ）
- MinGW-w64 C++20、WinLibs MCF/UCRTで確認
- Microsoft WebView2 SDK

### MinGW-w64

```powershell
winget install --id BrechtSanders.WinLibs.MCF.UCRT --exact --source winget
```

`build_native.py`はPATHに加えて、WinGetの標準パッケージ位置も検索します。

### WebView2 SDK

既定値:

```text
C:\tools\webview2\build\native\include\WebView2.h
C:\tools\webview2\build\native\x64\WebView2Loader.dll
```

必要に応じて次を設定します。

- `WEBVIEW2_ROOT`
- `WEBVIEW2_INCLUDE`
- `WEBVIEW2_LOADER`

## ビルド

```powershell
cd C:\path\to\QuickFileCopy
build.bat
```

内部処理:

1. `bundle_html.py`が`templates/index.html`を生成リソースへコピー
2. `windres`がアイコン、HTML、VERSIONINFOをリソースオブジェクト化
3. CLI版をコンパイル
4. GUI版をコンパイル
5. `WebView2Loader.dll`を成果物へコピー
6. 中間リソースオブジェクトを削除

## 成果物

| ファイル | 説明 |
| --- | --- |
| `dist/binary/QuickFileCopy.exe` | GUI版。HTMLを内蔵 |
| `dist/binary/QuickFileCopy_cli.exe` | CLI版 |
| `dist/binary/WebView2Loader.dll` | WebView2ローダー |

外部の`index.html`およびエンジンDLLは必要ありません。

## テスト

```powershell
python core\native\tests\native_smoke.py
```

性能測定:

```powershell
python core\native\tests\benchmark_native.py --profile small --destination-root D:\qfc-bench --workers 1,4,8,16
```

## トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| `g++/windres was not found` | WinLibsを導入するかMinGWのbinをPATHへ追加 |
| `WebView2.h was not found` | SDK配置または`WEBVIEW2_INCLUDE`を確認 |
| `WebView2Loader.dll was not found` | `WEBVIEW2_LOADER`を実ファイルへ向ける |
| 起動時にRuntimeエラー | WebView2 Runtime Evergreenを導入 |
| 完全保持でUACが出ない | 管理者起動がポリシーで禁止されていないか確認 |
| ネットワークドライブが見えない | UACの昇格コンテキストではドライブ割当が分離される場合があるためUNCを使用 |

## フォルダ構成

```text
core/native/include/qfc/  公開エンジンヘッダー
core/native/src/          エンジン、WebView2ホスト、CLI
core/native/resources/    RC、アイコン/HTMLのリソース定義
core/native/tests/        スモークテストとベンチマーク
templates/                開発用WebView2 UI
static/                   将来のCSS/JS/画像分離先
assets/                   アイコン原本・公開用画像
document/                 開発者向け文書
plans/                    計画・実施結果
prototype/python/         旧Pythonプロトタイプ
dist/binary/              生成バイナリ
dist/documents/           配布同梱文書
```
