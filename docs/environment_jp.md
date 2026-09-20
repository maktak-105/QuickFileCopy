# 開発環境

[English environment.md](environment.md)

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

`scripts/build.py`はPATHに加えて、WinGetの標準パッケージ位置も検索します。

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
scripts\build.bat
```

内部処理:

1. `scripts/bundle_html.py`が`src/ui/index.html`を`build/intermediate/QuickFileCopy.html`へコピー
2. `windres`がアイコン、HTML、VERSIONINFOをリソースオブジェクト化
3. CLI版をコンパイル
4. GUI版をコンパイル
5. `WebView2Loader.dll`を成果物へコピー
6. 中間リソースオブジェクトを削除

## 成果物

| ファイル | 説明 |
| --- | --- |
| `dist/QuickFileCopy.exe` | GUI版。HTMLを内蔵 |
| `dist/QuickFileCopy_cli.exe` | CLI版 |
| `dist/WebView2Loader.dll` | WebView2ローダー |

外部の`index.html`およびエンジンDLLは必要ありません。

## テスト

```powershell
python proto\tests\native_smoke.py
```

性能測定:

```powershell
python proto\tests\benchmark_native.py --profile small --destination-root D:\qfc-bench --workers 1,4,8,16
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
src/app/                  GUIホスト、RCリソース、アイコン
src/cli/                  CLIエントリーポイント
src/engine/               共有コピーエンジンと公開ヘッダー
src/ui/                   WebView2 UIソース
proto/prototype/           保存済みPythonプロトタイプ
proto/tests/               ネイティブスモークとベンチマーク
proto/benchmark/           過去のPython計測データ
scripts/                   ビルドとUIバンドル
docs/                      開発者向け文書
docs/distribution/         リリース同梱のユーザー文書
build/intermediate/        生成UIとリソースオブジェクト
dist/                      生成リリースバイナリ
```
