# FastestCopy

Windows標準のエクスプローラー/Robocopyより高速なファイルコピーアプリ。
Explorer風のデュアルペインGUI(左=ソース、右=ターゲット)、スキップ/上書きの
競合ポリシー選択、Python実装からNuitkaによるexeビルドまでを含む。

## 特徴

1. **Explorer風デュアルペインUI** (PySide6) - 左ペインでソース、右ペインでターゲットを
   Explorerのようにブラウズして選択し、中央の「コピー →」ボタンで実行。各ペインは
   さらに左右に分かれており、左側に常時表示のナビゲーションツリー、右側に選択中
   フォルダの内容一覧を表示する。ナビゲーションツリーはExplorer同様「PC」(ローカル/
   リムーバブルドライブ)と「ネットワーク」(`net use`等で割り当てたネットワークドライブ)
   に分けて表示し([drives.py](src/fastestcopy/gui/drives.py)で`GetDriveTypeW`により
   種別判定)、サブフォルダも展開時に遅延読み込みする。「PC」ボタン/「上へ」でドライブ
   ルートからドライブ一覧へも戻れる(Explorer同様、C:\がナビゲーションの終端にならない)。
   `\\server\share`形式のUNCパスもパスバーに直接入力して移動可能。
2. **Robocopyより高速なコピーエンジン** - I/Oバウンドな処理特性を踏まえ、Pythonから
   `ctypes`でkernel32を直接呼び出すことでpywin32のオーバーヘッドを回避しつつ、
   小ファイルはスレッドプールによるファイル単位並列化、巨大ファイルは
   (管理者権限があれば)チャンク単位の並列書き込みで高速化する。詳細は
   [src/fastestcopy/engine](src/fastestcopy/engine)と下記ベンチマーク結果を参照。
3. **競合ポリシー** - スキップ/上書き/新しい方のみ上書き/毎回確認 の4種類を選択可能。
4. **参考実装調査** - GitHub上の定番OSS [FastCopy](https://github.com/shirouzu/FastCopy)
   (C++, 非同期I/O・SetFileValidData活用)を`reference/FastCopy`にクローンし、
   設計の参考にした(直接組み込みはせず、自作Pythonエンジンとして再設計)。
5. **ベンチマーク** - 小さいファイルが大量にある多数フォルダのシナリオと、
   巨大ファイル少数のシナリオの両方でRobocopyと速度比較([benchmark/results](benchmark/results))。
6. **Python実装 → Nuitkaでexe化** - 開発はPythonで行い、最終的に
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
- ナビゲーションツリーの「ネットワーク」に表示されるのは`net use`等で**ドライブ文字に
  割り当て済み**のネットワークドライブのみ(`GetDriveTypeW`で判定)。Explorerの「ネットワーク」
  のようなドライブ未割り当てのコンピューター/共有フォルダの探索(ネットワーク近隣探索)は
  未対応 - 未割り当ての共有は`\\server\share`をパスバーに直接入力すれば開ける。

## プロジェクト構成

```
src/fastestcopy/
  engine/         # コピーエンジン本体 (GUI非依存)
    winio.py        # ctypesによるWinAPI直接呼び出し (CopyFileExW/ReadFile/WriteFile/SetFileValidData)
    scanner.py       # ディレクトリ列挙(パイプライン化)
    copier.py        # スレッドプールオーケストレーション
    policy.py        # スキップ/上書き/新しい方優先/毎回確認
    planner.py       # ワーカー数・チャンク戦略の決定
    stats.py         # 進捗・スループット集計
  gui/            # PySide6 デュアルペインGUI
    main_window.py   # メインウィンドウ、ツールメニュー
    file_pane.py      # 片側ペイン (PC/ネットワーク ナビゲーションツリー + 内容一覧)
    drives.py         # ドライブ種別判定(ローカル/ネットワーク)・ラベル取得
    copy_dialog.py    # コピー実行スレッド・進捗ダイアログ・競合確認ダイアログ
    settings_dialog.py
    elevate.py        # 管理者として再起動
  cli.py          # ヘッドレスCLI
assets/           # アイコン素材 (icon.svg / icon.ico)
benchmark/        # ベンチマーク生成・実行スクリプトと結果
tests/            # pytest
reference/        # 参考OSS (shirouzu/FastCopy) のクローン、study用
run_gui.py        # Nuitka用GUIエントリポイント
run_cli.py        # Nuitka用CLIエントリポイント
build_exe.ps1     # GUI版exeビルドスクリプト
```
