QuickFileCopy - Windows向けネイティブ高速ファイルコピー
配布パッケージ  v1.1.0

GitHub
------
https://github.com/maktak-105/QuickFileCopy

動作環境
--------
- Windows 10 / 11 (64-bit)
- Microsoft Edge WebView2 Runtime
- 完全保持モードではUACによる管理者承認

導入方法
--------
QuickFileCopy-binary.zipの全ファイルを同じフォルダーへ展開してください。
QuickFileCopy.exeを実行します。GUIはEXEに埋め込まれているためindex.htmlは不要です。
WebView2Loader.dllはQuickFileCopy.exeと同じ場所に置いてください。

GUIの使い方
-----------
1. コピー元フォルダーを1つ以上選択します。
2. コピー先フォルダーを選択します。
3. コピー元の中身を直接マージするか、コピー元フォルダーごと配置するかを選択します。
4. スキップ、上書き、新しい方の競合ポリシーとコピーモードを選択します。
5. コピーを開始し、容量進捗とファイル進捗を確認します。

画面右上のQDB形式言語ボタンで日本語／Englishを切り替えられます。
選択した言語は保存され、次回起動時にも復元されます。

CLIの使い方
-----------
QuickFileCopy_cli.exe copy C:\Source D:\Backup --contents --policy newer
QuickFileCopy_cli.exe copy C:\Source D:\Backup --folder --policy overwrite --verify
QuickFileCopy_cli.exe --help

配布ファイル
------------
- QuickFileCopy.exe - HTML内蔵WebView2 GUI版
- QuickFileCopy_cli.exe - CLI版
- WebView2Loader.dll - WebView2ローダー（Runtime本体ではありません）
- readme.txt / readme_jp.txt - 使用説明書
- history.txt / history_jp.txt - 更新履歴
- LICENSE.txt / LICENSE_jp.txt - MIT License

SHA-256
-------
DD5CB93CF84CD6FE5D0DF78DC1A640B3CF97EF4E153989CC8F39A7EF681651E7  QuickFileCopy.exe
5CAE1046B51B5D60EB929561294E3E2F526C62CAA97A828BA9B326F693E08843  QuickFileCopy_cli.exe
完全性検証（SHA-256）
---------------------
配布用ZIPおよびバイナリの公式SHA-256ハッシュ値はCIビルド時に自動計算され、
GitHub Releasesの各リリースに SHA256SUMS.txt として添付・公開されています。
PowerShellでダウンロードファイルの整合性を確認できます:
  Get-FileHash .\QuickFileCopy-binary.zip -Algorithm SHA256

ライセンス
----------
MIT Licenseです。LICENSE.txtが英語原文です。LICENSE_jp.txtは日本語参考訳で、内容に相違が
ある場合は英語原文が優先します。

免責事項
--------
本ソフトウェアは現状有姿で提供されます。本ソフトウェアの使用、動作結果、データ消失、
システム障害、ハードウェア故障、その他の損害について作者は責任を負いません。
重要なデータをバックアップし、最初は重要でないファイルで動作確認してください。

QuickFileCopyは独立したアプリであり、Microsoft、FastCopy、Robocopy、Box、その他の
第三者ベンダーとの提携または承認関係はありません。
