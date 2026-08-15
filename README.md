# FastestCopy

Windows標準のエクスプローラー/Robocopyより高速なファイルコピーアプリ。
Explorer風のデュアルペインGUI(上=コピー元、下=コピー先)、スキップ/上書きの
競合ポリシー選択、日本語/英語UI、Python実装からNuitkaによるexeビルドまでを含む。

## 特徴

1. **Explorer風デュアルペインUI** (PySide6) - 上側でコピー元、下側でコピー先を
   Explorerのようにブラウズして選択し(間の▼が上→下のコピー方向を示す)、右側の
   「コピー →」ボタンで実行。各ペインはさらに左右に分かれており、左側に常時表示の
   ナビゲーションツリー、右側に選択中フォルダの内容一覧を表示する。
   - **PC**: ローカル/リムーバブルドライブ、`net use`等で割り当て済みのネットワーク
     ドライブ、および「ネットワークの場所」ショートカット(ドライブレターなし)を一覧表示
     ([drives.py](src/fastestcopy/gui/drives.py)で`GetDriveTypeW`により種別判定)。
   - **ネットワーク**: シェルの名前空間を辿ってネットワーク上のコンピューターを検出・
     表示し(Explorerと同じ発見方法、SSDPノイズは除外)、展開すると各コンピューターの
     共有フォルダが見える([netbrowse.py](src/fastestcopy/gui/netbrowse.py))。
   - すべての項目(ドライブ・コンピューター・共有・フォルダ)にOSネイティブのアイコンを
     表示(`QFileIconProvider`)。
   - `\\server\share`形式のUNCパスもパスバーに直接入力して移動可能。
   - コンテンツペインは**Shift+クリックで範囲選択、Ctrl+クリックで個別選択**に対応
     (Explorer同様)。フォルダをコピーすると同名のサブフォルダがコピー先に作られる
     (中身を直接マージする一昔前の挙動は廃止)。
   - 「隠しファイル/フォルダを表示」チェックボックス(デフォルトOFF)でWindowsの
     「隠し」属性付きアイテムの表示切替。
2. **Robocopyより高速なコピーエンジン** - I/Oバウンドな処理特性を踏まえ、Pythonから
   `ctypes`でkernel32を直接呼び出すことでpywin32のオーバーヘッドを回避しつつ、
   小ファイルはスレッドプールによるファイル単位並列化、巨大ファイルは
   (管理者権限があれば)チャンク単位の並列書き込みで高速化する。詳細は
   [src/fastestcopy/engine](src/fastestcopy/engine)と下記ベンチマーク結果を参照。
3. **競合ポリシー** - スキップ/上書き/新しい方のみ上書き/毎回確認 の4種類を選択可能。
4. **スキャンしてコピー** - 「スキャンしてコピー →」ボタンで、実際にコピーする前に
   対象全体を読み取り専用でスキャンし、「コピー対象は何件、スキップは何件」を
   確認してから実行できる([preview.py](src/fastestcopy/engine/preview.py))。
   確認後は判定済みのジョブリストをそのままコピーに渡すため、二重にツリーを
   走査しない。スキャン結果からコピーに必要な合計バイト数が分かるため、コピー先の
   空き容量と比較し、不足していそうなら確認ダイアログに警告を表示する
   ([diskspace.py](src/fastestcopy/engine/diskspace.py))。「コピー →」(直接コピー)側は
   事前スキャンをしない設計上、必要量までは分からないが、コピー先が実質空き0の
   場合はその場で警告して開始をブロックする。
5. **進捗・キャンセル・エラーログ**
   - コピー中は進捗率(%)・推定残り時間・MB/s・files/sを進捗ダイアログと
     メイン画面中央の両方に表示。
   - キャンセルは大容量ファイルのコピー中でもほぼ即座に反映され(4MBバッファ単位で
     チェック)、書きかけの不完全なファイルは自動削除される。容量不足など通常の
     書き込みエラーで失敗した場合も同様に、残った不完全な出力ファイル(0バイト/
     部分書き込み)を自動削除する([copier.py](src/fastestcopy/engine/copier.py)の
     `_remove_partial_output`) - 元々あった同名ファイルを誤って消さないよう、
     期待サイズより小さいものだけを対象にしている。
   - コピーエラーが発生した場合、ファイル名と原因を記録したログを
     `%LOCALAPPDATA%\FastestCopy\logs\` に自動保存し、完了ダイアログから直接開ける。
6. **日本語/英語UI + ヘルプ** - 「ツール>言語」でUI言語を切替可能(再起動で反映、
   設定は保持される)。「ヘルプ」メニューから使い方ガイドを日本語/英語で表示。
7. **参考実装調査** - GitHub上の定番OSS [FastCopy](https://github.com/shirouzu/FastCopy)
   (C++, 非同期I/O・SetFileValidData活用)を`reference/FastCopy`にクローンし、
   設計の参考にした(直接組み込みはせず、自作Pythonエンジンとして再設計)。
   実際にインストールされたFastCopyとの直接比較ベンチマークも実施
   ([fastcopy_comparison.md](benchmark/results/fastcopy_comparison.md))。
8. **ベンチマーク** - 小さいファイルが大量にある多数フォルダのシナリオと、
   巨大ファイル少数のシナリオの両方でRobocopy/FastCopyと速度比較
   ([benchmark/results](benchmark/results))。
9. **Python実装 → Nuitkaでexe化** - 開発はPythonで行い、最終的に
   [Nuitka](https://nuitka.net/)で`FastestCopy.exe`としてコンパイルする
   (`build_exe.ps1`)。コピー速度自体はI/Oバウンドなためコンパイルでは変わらないが、
   配布のしやすさと起動速度のために採用。

## なぜ速いか

ファイルコピーはCPUバウンドではなくI/Oバウンドな処理であり、実際のディスクI/Oは
呼び出し言語に関わらずOSカーネルが行う。したがって速度の本質は「どう呼び出すか」にある:

- **パイプライン化**: ディレクトリ列挙(`os.scandir`)とコピーを同時進行させ、列挙完了を待たずコピーを開始する。
- **適応的並列度**: 小ファイル大量ならファイル単位でスレッドプール並列化(既定 `cpu_count*2`、上限32)。
- **ctypesによる直接WinAPI呼び出し**: `CopyFileExW`/`ReadFile`/`WriteFile`をpywin32経由ではなく
  `ctypes`で直接呼ぶことで、Python側のマーシャリングオーバーヘッドを削減
  (開発中の計測で逐次コピー速度が174 files/s→1394 files/sに改善)。
- **巨大ファイルのチャンク並列書き込み**: 管理者権限があれば`SetFileValidData`で
  NTFSのゼロフィルをスキップした上で複数スレッドが異なるオフセットへ並列書き込み。
  管理者権限がない場合は(ゼロフィルの罠を避けるため)単一ストリームの逐次コピーに
  安全にフォールバックする。

詳しい設計判断と、開発中に見つかったバグ・教訓は
[benchmark/results/small_files_benchmark.md](benchmark/results/small_files_benchmark.md)と
[benchmark/results/large_files_benchmark.md](benchmark/results/large_files_benchmark.md)を参照。

## ベンチマーク結果サマリー

複数回の独立した測定結果は[benchmark/results](benchmark/results)に生データとともに記録している。
詳細と分析は[final_comparison.md](benchmark/results/final_comparison.md)を参照。

### 小ファイル大量シナリオ (2,000フォルダ×20,000ファイル、計498MB)

| 測定 | FastestCopy | Robocopy /MT:8 | Robocopy /MT:32 |
|---|---|---|---|
| 初回 (3ラウンド平均) | 14.71s | 19.54s (**+25%**) | 18.93s (**+22%**) |
| 再測定 (3ラウンド平均, 全ラウンド勝利) | 9.38s | 15.83s (**+41%**) | 19.86s (**+52%**) |

複数回の独立した測定でFastestCopyが一貫してRobocopyを上回った(概ね+22%〜+52%)。
コンパイル済みexeをサブプロセスとして毎回起動するベンチマーク手法では、Nuitka
`--onefile`の自己展開オーバーヘッドとシステム変動により差が縮まる回もあった
([final_comparison.md](benchmark/results/final_comparison.md)で詳述) - GUIアプリとして
1度起動して使う通常の利用形態ではこのオーバーヘッドは発生しない。

### 巨大ファイル少数シナリオ (4ファイル×800MB、非管理者権限、2ラウンド平均)

| Engine | Avg time (s) | Avg MB/s |
|---|---|---|
| FastestCopy | 50.64 | 63.9 |
| Robocopy /MT:8 | 49.29 | 65.0 |

非管理者環境ではRobocopyとほぼ同等(両者とも単一ストリーム逐次コピーになるため、設計上の想定通り)。
管理者権限で実行した場合の高速化は未検証 -
`python benchmark\run_benchmark.py --src <大ファイルフォルダ> --work <作業フォルダ>` を
管理者PowerShellで再実行することで確認できる。

### FastCopyとの直接比較

実際にインストールされた[FastCopy](https://fastcopy.jp/)(H.Shirouzu氏、CLI版`fcp.exe`)とも
同一データセットで比較した([fastcopy_comparison.md](benchmark/results/fastcopy_comparison.md))。
小ファイル大量シナリオではFastestCopyがFastCopy(既定設定)比で約3.3倍高速、
大容量ファイル少数シナリオでは3エンジンともディスク帯域で頭打ちになり実質横並びだった。

## セットアップ

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .
```

## 使い方

### GUI

```powershell
.\.venv\Scripts\pythonw.exe -m fastestcopy.gui.app
```

### CLI (ヘッドレス)

```powershell
.\.venv\Scripts\python.exe -m fastestcopy.cli copy <src> <dst> --policy skip
```

`--policy` は `skip` / `overwrite` / `overwrite-if-newer` から選択。

### テスト

```powershell
.\.venv\Scripts\python.exe -m pytest tests\
```

### ベンチマーク

```powershell
# テストデータ生成 (多数の小フォルダ・小ファイル)
.\.venv\Scripts\python.exe benchmark\generate_dataset.py <出力先> --num-dirs 2000 --files-per-dir 10

# FastestCopy vs Robocopy 比較
.\.venv\Scripts\python.exe benchmark\run_benchmark.py --src <出力先> --work <作業フォルダ> --rounds 3
```

### exeビルド (Nuitka)

GUI版(`FastestCopy.exe`、アイコン付き):

```powershell
.\.venv\Scripts\python.exe -m nuitka --standalone --onefile --enable-plugin=pyside6 `
    --windows-console-mode=disable --windows-icon-from-ico=assets\icon.ico `
    --output-dir=dist --output-filename=FastestCopy.exe run_gui.py
```

(`build_exe.ps1` に同内容のスクリプトあり。PowerShellの実行ポリシーでブロックされる場合は
上記コマンドを直接実行するか、`Set-ExecutionPolicy -Scope Process Bypass` を検討。)

CLI版(`FastestCopy-CLI.exe`、Qt非依存で軽量・ビルドも高速。ベンチマークで実際のコンパイル
成果物を計測する用途などに):

```powershell
.\.venv\Scripts\python.exe -m nuitka --standalone --onefile `
    --output-dir=dist --output-filename=FastestCopy-CLI.exe run_cli.py
```

## アイコン

`assets/icon.svg` から生成した `assets/icon.ico`(複数解像度)をexeに埋め込んでいる。
再生成する場合:

```powershell
.\.venv\Scripts\python.exe -c "from PySide6.QtWidgets import QApplication; from PySide6.QtGui import QPixmap, QPainter; from PySide6.QtSvg import QSvgRenderer; app=QApplication.instance() or QApplication([]); r=QSvgRenderer('assets/icon.svg'); p=QPixmap(512,512); p.fill(0); pt=QPainter(p); r.render(pt); pt.end(); p.save('assets/icon_512.png')"
.\.venv\Scripts\python.exe -c "from PIL import Image; Image.open('assets/icon_512.png').convert('RGBA').save('assets/icon.ico', format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
```

## 既知の制限

- シンボリックリンク/ジャンクションの特別扱いは未対応(通常ファイルとして扱われる)。
- 巨大ファイルの真の並列チャンクコピーには管理者権限が必要(`SetFileValidData`のため)。
  非管理者時は安全な逐次コピーにフォールバックする。
- ストレージ種別(SSD/HDD)の自動判定は未実装。ワーカー数は`ツール>設定`から手動調整可能。
- 言語切替(ツール>言語)は再起動後に反映される方式(設定はWindowsレジストリ`QSettings`に自動保存・保持されます)。

## 最近の修正・改善内容

- **メニューからのアプリ再起動・管理者昇格の安定化 ([elevate.py](src/fastestcopy/gui/elevate.py), [main_window.py](src/fastestcopy/gui/main_window.py))**:
  - `ShellExecuteW` 呼び出し時の作業ディレクトリ (`cwd`) 保持および親ウィンドウ (`hwnd`) の伝達を適用。
  - Nuitka `--onefile` 単一 executable 実行環境において、一時展開用バイナリではなくオリジナルの `.exe` パス (`NUITKA_ONEFILE_BINARY` / `sys.argv[0]`) を追跡・指定して再起動するよう改修。
  - 通常権限での再起動時のプロセス切り離し (`hwnd=0`) と `QTimer.singleShot` による安全なウィンドウクローズを適用し、旧ウィンドウ終了に伴う道連れ終了を防止。
  - ユーザーが UAC 確認で「いいえ」を選択した場合のキャンセルハンドリングおよび `%LOCALAPPDATA%\FastestCopy\logs\relaunch.log` への自動ログ記録を追加。
- **UIおよびヘルプ表記の改善 ([i18n.py](src/fastestcopy/gui/i18n.py), [help_content.py](src/fastestcopy/gui/help_content.py))**:
  - メイン画面の「コピー元」「コピー先」ラベルの先頭にインデント（半角4文字分）を付与。
  - ヘルプダイアログの「ナビゲーションツリー (左側)」->「ネットワーク」の説明に LocalPC1 表記および探索時の注意事項（「環境によっては少し時間がかかります、そのままお待ちください」）を追加。

## プロジェクト構成

```
src/fastestcopy/
  engine/         # コピーエンジン本体 (GUI非依存)
    winio.py        # ctypesによるWinAPI直接呼び出し (CopyFileExW/ReadFile/WriteFile/SetFileValidData)
    scanner.py       # ディレクトリ列挙(パイプライン化)
    preview.py       # 読み取り専用の事前スキャン(スキャンしてコピー用の判定)
    copier.py        # スレッドプールオーケストレーション
    policy.py        # スキップ/上書き/新しい方優先/毎回確認
    planner.py       # ワーカー数・チャンク戦略の決定
    stats.py         # 進捗・スループット・ETA集計
  gui/            # PySide6 デュアルペインGUI
    main_window.py   # メインウィンドウ、メニュー、コピー元/コピー先ペインの配置
    file_pane.py      # 片側ペイン (PC/ネットワーク ナビゲーションツリー + 内容一覧)
    drives.py         # ローカル/ネットワークドライブの種別判定・ラベル取得
    netbrowse.py      # シェル名前空間経由のネットワークコンピューター/共有の探索
    copy_dialog.py    # コピー/スキャン実行スレッド・進捗ダイアログ・競合確認ダイアログ
    copy_log.py       # コピーエラーのログファイル出力
    settings_dialog.py
    elevate.py        # 管理者/通常権限での再起動
    i18n.py           # UI文字列の日英切替
    help_content.py   # ヘルプ本文(日英、埋め込み)
    help_dialog.py    # ヘルプダイアログ
  cli.py          # ヘッドレスCLI
assets/           # アイコン素材 (icon.svg / icon.ico)
benchmark/        # ベンチマーク生成・実行スクリプトと結果
tests/            # pytest
reference/        # 参考OSS (shirouzu/FastCopy) のクローン、study用
run_gui.py        # Nuitka用GUIエントリポイント
run_cli.py        # Nuitka用CLIエントリポイント
build_exe.ps1     # GUI版exeビルドスクリプト
```
