# QuickFileCopy
[English README.md](README.md)

QuickFileCopyはWindows 10/11向けのネイティブ高速ファイルコピーアプリです。簡潔な操作、容量とファイル件数の分かる進捗、安全な置換、Windows固有メタデータの保持を重視し、実行時にPythonを必要としません。

バージョン: **v1.1.0**

GUIと文書は日本語／Englishに対応し、QDB形式の言語ボタンで切り替えた設定は再起動後も維持されます。

<p align="center">
  <img src="assets/QuickFileCopy-gui-ja.png" alt="QuickFileCopy 日本語GUI" width="720">
</p>

## 主な機能

- WebView2 GUIとCLIで共有するC++20ネイティブコピーエンジン
- 設定を保存する日本語／English即時切替UI
- ディレクトリ走査とコピーの同時実行、有界キューによる並列処理
- スキップ、上書き、新しい方のみ上書きの競合ポリシー
- バックアップ先へコピー元の中身を直接更新、またはコピー元フォルダーごと配置
- 一時ファイルをフラッシュした後のSHA-256検証
- コピー先と同じディレクトリの一時ファイルから安全に置換
- ACL/所有者/グループ/SACL、ADS、EA、EFS、リパースポイント、ハードリンク、スパース、NTFS圧縮の保持
- 中断後に完了済みファイルを再利用する再開ジャーナル
- SMB圧縮要求、保存先に応じた並列数、大容量ローカルコピーの非バッファ化
- 完全保持モードにおける512 MiB以上の対象ローカルファイルの特権事前確保

## 配布版を使う

コード署名: [SignPath.io](https://signpath.io) による無償のコード署名を利用し、証明書は [SignPath Foundation](https://signpath.org) が発行します。現在申請中で、承認後に公開するリリースから署名されます。詳細は [コード署名ポリシー](docs/code_signing_policy.md)。

実行だけなら GitHub Releases の ZIP を使います。`v*` タグで GitHub Actions が `QuickFileCopy-binary.zip` を作ります。ZIP はリポジトリには置きません。

- [最新版の配布ページ](https://github.com/maktak-105/QuickFileCopy/releases)
- [v1.1.0](https://github.com/maktak-105/QuickFileCopy/releases/tag/v1.1.0)
- [QuickFileCopy-binary.zipを直接ダウンロード](https://github.com/maktak-105/QuickFileCopy/releases/download/v1.1.0/QuickFileCopy-binary.zip)

すべてのファイルを同じフォルダへ展開して `QuickFileCopy.exe` を実行します。

- `QuickFileCopy.exe` - WebView2 GUI版
- `QuickFileCopy_cli.exe` - CLI版
- `WebView2Loader.dll` - WebView2ローダー
- `readme.txt` / `readme_jp.txt` - 使用説明書
- `history.txt` / `history_jp.txt` - 更新履歴
- `LICENSE.txt` / `LICENSE_jp.txt` - ライセンス

### 完全性検証 (SHA-256)

配布用 ZIP および各バイナリの公式 SHA-256 ハッシュ値は、CI (GitHub Actions) ビルド時に自動計算され、各リリースページに `SHA256SUMS.txt` として添付・公開されています。PowerShell でダウンロードファイルの完全性を確認できます:

```powershell
Get-FileHash .\QuickFileCopy-binary.zip -Algorithm SHA256
```

GUIのHTMLは`QuickFileCopy.exe`へ埋め込まれるため、外部の`index.html`は不要です。

## GUIの使い方

1. コピー元フォルダーを1つ以上選択します。
2. コピー先フォルダーを選択します。
3. コピー先を直接更新する場合は「中身をコピー」、配下にコピー元フォルダーを作る場合は「フォルダごと」を選びます。
4. 競合ポリシーとコピーモードを選択します。
5. コピーを開始し、容量進捗とファイル進捗を確認します。

完全保持モードではUACを通じて別の管理者ワーカーを起動します。メインGUI自体は通常権限のままです。

## CLIの使い方

```powershell
.\dist\QuickFileCopy_cli.exe --help
.\dist\QuickFileCopy_cli.exe copy C:\Source D:\Backup --contents --policy newer
.\dist\QuickFileCopy_cli.exe copy C:\Source D:\Backup --folder --policy overwrite --verify
```

## ビルド

必要なもの:

- Windows 10/11 64-bit
- ビルドスクリプト用Python 3.11以降
- MinGW-w64（WinLibs MCF/UCRTで確認）
- Microsoft WebView2 SDKヘッダーと`WebView2Loader.dll`

```powershell
winget install --id BrechtSanders.WinLibs.MCF.UCRT --exact --source winget
scripts\build.bat
```

WebView2 SDKの既定ルートは`C:\tools\webview2\build\native`です。別の場所を使う場合は`WEBVIEW2_ROOT`、`WEBVIEW2_INCLUDE`、`WEBVIEW2_LOADER`で指定できます。

成果物は`dist/`へ出力されます。ビルド後のスモークテスト:

```powershell
python proto\tests\native_smoke.py
```

詳しい仕様とビルド情報は[`docs/`](docs/)にあります。

## フォルダ構成

```text
QuickFileCopy/
├── src/
│   ├── app/              GUIホスト & Windowsリソース（main_gui.cpp, .rc, .ico, resource.h）
│   ├── cli/              CLIエントリーポイント（main_cli.cpp）
│   ├── engine/           コピーエンジンおよびqfc/ヘッダー（copy_engine.cpp, qfc/copy_engine.h）
│   └── ui/               UIソース（index.html, css/, js/, img/）
├── proto/
│   ├── prototype/        旧Python/PySide6プロトタイプ
│   ├── tests/            ネイティブCLIのスモークとベンチマーク
│   └── benchmark/        旧Python試作の計測記録
├── scripts/              build.py, build.bat, bundle_html.py
├── docs/                 仕様、開発環境、バージョン情報
│   └── distribution/     配布用ユーザー文書
├── dist/                 生成バイナリ（ソース管理対象外、.gitkeepのみ保持）
└── .github/workflows/    CI / Release ワークフロー
```

## ライセンスと免責

MIT Licenseです。[`LICENSE`](LICENSE)および[`docs/distribution/LICENSE_jp.txt`](docs/distribution/LICENSE_jp.txt)を参照してください。

本ソフトウェアは現状有姿で提供されます。データ消失、システム障害、ハードウェア故障、その他の損害について作者は責任を負いません。重要なデータをバックアップし、最初は重要でないファイルで動作確認してください。

## 設計思想

- 通常操作をコピー元、コピー先、モード、配置、競合ポリシーに絞る。
- すべてを単純なバイト列とみなさず、Windows固有のファイル情報を保持する。
- ベンチマーク値より先に、キャンセルと置換の安全性を確保する。
- ストレージとファイル種別が有効な場合だけ専用経路を使い、それ以外はWindows標準APIへ戻す。
