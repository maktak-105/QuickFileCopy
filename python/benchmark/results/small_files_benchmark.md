# ベンチマーク結果: 小ファイル大量シナリオ

## データセット
- `benchmark/generate_dataset.py` で生成
- 2,000フォルダ × 10ファイル = 20,000ファイル、合計 498.2 MB
- ファイルサイズ: 1KB〜50KB (ランダム)
- ツリー深さ: breadth=10 (約4階層)

## 方法
- 同一ソースをFastestCopyとrobocopy(/MT:8, /MT:32)それぞれ専用の空フォルダへコピー
- 事前に1回ウォームアップコピー(未計測)を行いOSファイルキャッシュを温めてから計測(全エンジン公平化)
- robocopyは `/NFL /NDL /NJH /NJS /NP /NC` でコンソールログ出力を抑制(ログ出力オーバーヘッドが結果を歪めるため)
- 3ラウンド実行し平均を採用

## 結果 (3ラウンド平均)

| Engine | Avg time (s) | Avg MB/s | Avg files/s |
|---|---|---|---|
| **FastestCopy** | **14.71** | **33.9** | **1362** |
| Robocopy /MT:8 | 19.54 | 25.7 | 1032 |
| Robocopy /MT:32 | 18.93 | 26.5 | 1064 |

FastestCopyはRobocopy /MT:8比で約 **+25%**、/MT:32比で約 **+22%** 高速。

## 開発中に見つかった重要な知見

初期実装では pywin32 (`win32file.ReadFile`/`WriteFile`) をそのまま使ったところ、
1000ファイルの逐次コピーで **174 files/s** しか出ず、`shutil.copy2`(901 files/s)にも
劣っていた。プロファイリングの結果、pywin32のPython側マーシャリングオーバーヘッドが
原因と判明。`ctypes` で kernel32 (`CopyFileExW` / `ReadFile` / `WriteFile`) を直接呼ぶ
実装に切り替えたところ、逐次で **1394 files/s**、20並列スレッドで **3600 files/s** まで
改善した。ファイルコピーはI/Oバウンドでも、API呼び出しのPython側オーバーヘッドが
無視できないボトルネックになり得るという教訓([winio.py](../../src/fastestcopy/engine/winio.py)参照)。

小ファイル用ワーカー数は `cpu_count * 2`(上限32、下限8)をデフォルトとした
(`planner.py`)。12コア環境でのベンチマークでは8〜64の間でおよそ20並列がピークだった。
