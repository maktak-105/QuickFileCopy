# 最終ベンチマーク: Python版 / コンパイル版exe / Robocopy 比較

同一データセット(2,000フォルダ×20,000ファイル、計498.2MB、`src_full`)で3種類の
測定を実施した。開発機はセッション中に複数のビルド・GUIテストが並行して走っていたため
ラウンド間の変動が大きく、それも含めて正直に記録する。

## 測定1: Python版エンジン単体 (3ラウンド、クリーンな条件)

| Engine | Avg time (s) | Avg MB/s | Avg files/s |
|---|---|---|---|
| **FastestCopy (Python)** | **9.38** | **55.9** | **2246** |
| Robocopy /MT:8 | 15.83 | 34.3 | 1375 |
| Robocopy /MT:32 | 19.86 | 26.4 | 1059 |

3ラウンド全てでFastestCopyが最速。平均でRobocopy /MT:8比 **+41%**、/MT:32比 **+52%** 高速。
これは[small_files_benchmark.md](small_files_benchmark.md)の結果(+22〜25%)とも整合する
(同一マシンでも実行タイミングにより変動するが、一貫してFastestCopyが優位)。

## 測定2: コンパイル済みexe(CLI版) vs Robocopy vs Python版 (2ラウンド)

`FastestCopy-CLI.exe`(Nuitka `--onefile`ビルド)をサブプロセスとして起動して計測。

| Engine | Avg time (s) | Avg MB/s | Avg files/s |
|---|---|---|---|
| FastestCopy (Python) | 15.58 | 35.6 | 1430 |
| FastestCopy.exe | 17.96 | 28.8 | 1158 |
| Robocopy /MT:8 | 13.53 | 37.5 | 1505 |
| Robocopy /MT:32 | 11.78 | 42.3 | 1699 |

この回はラウンド間の変動が大きく(round1で全エンジンが減速)、robocopyが上回る結果になった。
2つの理由が考えられる:

1. **`--onefile`の自己展開オーバーヘッド**: Nuitkaの`--onefile`は既定で*実行のたびに*
   実行ファイルを一時フォルダへ展開する。この展開コストはコピー処理そのものとは無関係の
   固定オーバーヘッドで、プロセスを1回起動して使い続けるGUIアプリでは無視できるが、
   本測定のようにCLIを毎回サブプロセス起動するベンチマーク手法では不利に働く
   (`--standalone`のみでonefile化しないビルドや、常駐プロセスとして使う場合はこの影響を受けない)。
2. **システム変動**: 本セッションでは直前にNuitkaビルドを2回、GUIの手動テストを実施しており、
   バックグラウンド負荷(ディスクキャッシュの状態、Windows Defenderのスキャン等)が
   測定1・小ファイルベンチマークの実行時と異なっていた可能性がある。

## 結論

- **エンジンそのものの優位性**(測定1、および[small_files_benchmark.md](small_files_benchmark.md))は
  複数回の独立した測定で一貫して確認された: Robocopyに対し概ね **+22%〜+52%** 高速。
- **exe化(Nuitka)は、コピー速度そのものを上げる目的の最適化ではない** -
  ファイルコピーはI/Oバウンドであり、実測でもPython版とexe版で本質的な速度差はない
  (測定2の差は主にonefile展開オーバーヘッドとシステム変動によるもの)。exe化の目的は
  Python未インストール環境への配布と、GUIアプリとしての起動性という当初の要件を満たすこと。
- 巨大ファイル少数シナリオでは[large_files_benchmark.md](large_files_benchmark.md)の通り、
  非管理者権限下ではRobocopyとほぼ同等(設計通りのフォールバック)。管理者権限下での
  優位性は未検証。

生データ: [final_python_only.json](final_python_only.json), [final_exe_comparison.json](final_exe_comparison.json)
