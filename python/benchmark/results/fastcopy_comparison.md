# FastCopy比較ベンチマーク

FastestCopyを作る際に比較対象にしていたRobocopyに加え、実際に有名な高速コピーツール
「[FastCopy](https://fastcopy.jp/)」(H.Shirouzu氏)がインストールされたので、
そのCLI版 `fcp.exe` を使って同一データセットで直接比較した。

`fcp.exe` はFastCopyのGUI用設定ファイル(`FastCopy2.ini`)の値を引き継ぐため、両方の
計測ともユーザー環境のFastCopyインストール後の**素の設定**(`bufsize=512`、
`speed_level=11`)で実行している - チューニングして有利にした数値ではない。

## 小ファイル大量 (2,000フォルダ×20,000ファイル、498.2MB、`src_full`、3ラウンド)

| Engine | Avg time (s) | Avg MB/s | Avg files/s | Errors |
|---|---|---|---|---|
| **FastestCopy** | **12.74** | **41.2** | **1655** | 0 |
| Robocopy /MT:32 | 11.85 | 44.1 | 1769 | 0 |
| Robocopy /MT:8 | 12.93 | 39.6 | 1591 | 0 |
| FastCopy (`fcp.exe`, 既定設定) | 42.14 | 12.5 | 501 | 0 |

このワークロードではFastCopyが明確に遅く、FastestCopy比で**約3.3倍**の時間がかかった。
「本当にFastCopyの実力を引き出せているか」を疑い、`/speed=full`・バッファサイズ変更を
手動で試した:

- `/speed=full` 明示指定: 変化なし(43.3秒、既定とほぼ同じ) - 速度制限が原因ではない
- `/bufsize=64`(fcp.exeが受け付ける最小値。既定の512より小さい)+`/speed=full`:
  28.9秒まで改善したが、それでもFastestCopy/Robocopyより**2倍以上遅い**

チューニングでも埋まらない差なので、設定ミスではなく「大量の小ファイルを1ファイルずつ
逐次処理する」FastCopyのアーキテクチャ的な弱点だと考えられる(FastCopyはRobocopyの
`/MT`やFastestCopyの並列ワーカープールに相当する同時ファイル数の指定オプションが
`fcp.exe /?` のヘルプ上に見当たらない)。

## 大容量ファイル少数 (3.2GB、4ファイル、`src_large`、2ラウンド、非管理者権限)

| Engine | Avg time (s) | Avg MB/s | Errors |
|---|---|---|---|
| **FastestCopy** | **47.52** | **67.4** | 0 |
| Robocopy /MT:8 | 48.69 | 65.8 | 0 |
| FastCopy | 49.86 | 64.2 | 0 |
| Robocopy /MT:32 | 53.59 | 59.9 | 0 |

こちらは4エンジンとも59〜68 MB/sの範囲に収まり、実質的に横並び(差は誤差レベル)。
非管理者権限のためFastestCopy側の巨大ファイル高速化(`SetFileValidData`によるゼロ埋め
省略)が効いておらず、単一ディスクの転送帯域で頭打ちになっていると見られる - [large_files_benchmark.md](large_files_benchmark.md)
と同じ結論。

## 結論

- **大量の小ファイル**: FastestCopyがFastCopy(既定設定)より約3倍、チューニング後でも
  2倍以上速かった。この差はFastCopy側のファイル単位の逐次処理アーキテクチャに起因すると
  見られ、設定変更では埋まらない。
- **少数の大容量ファイル**: 3エンジンともディスク帯域で頭打ちになり、実質的に同等
  (管理者権限下でのFastestCopyの優位性は未検証、[final_comparison.md](final_comparison.md)と同じ注記)。
- FastCopyは実運用で高く評価されているツールだが、本機・本データセットでの実測では
  小ファイル大量シナリオにおいてFastestCopy/Robocopyに劣る結果だった。GUI版とCLI版
  (`fcp.exe`)で挙動が異なる可能性や、本セッションの環境固有の要因も否定はできない。

生データ: [fastcopy_comparison.json](fastcopy_comparison.json), [fastcopy_comparison_large.json](fastcopy_comparison_large.json)
