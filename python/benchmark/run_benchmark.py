"""Benchmarks the FastestCopy engine against robocopy on the same source tree.

Methodology notes:
- Every engine copies the exact same source tree to its own fresh
  destination folder (deleted+recreated before each timed run).
- An untimed warmup copy runs first so the OS file cache is warm for all
  timed engines equally (otherwise whichever engine runs first would be
  unfairly slowed down by cold-cache disk reads).
- robocopy is invoked with /NFL /NDL /NJH /NJS /NP /NC to suppress
  per-file console logging, since that logging overhead can dominate
  wall-clock time and would not reflect real-world usage.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastestcopy.engine.copier import run_copy  # noqa: E402
from fastestcopy.engine.policy import ConflictPolicy  # noqa: E402


def _dir_stats(root: str) -> tuple[int, int]:
    total_files = 0
    total_bytes = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            total_files += 1
            total_bytes += os.path.getsize(os.path.join(dirpath, name))
    return total_files, total_bytes


def _clean(path: str) -> None:
    if os.path.exists(path):
        shutil.rmtree(path)


def run_fastestcopy(src: str, dst: str, **kwargs) -> dict:
    _clean(dst)
    t0 = time.perf_counter()
    stats = run_copy(src, dst, policy=ConflictPolicy.OVERWRITE, **kwargs)
    elapsed = time.perf_counter() - t0
    snap = stats.snapshot()
    return {
        "engine": "FastestCopy",
        "elapsed_sec": elapsed,
        "files": snap["files_copied"],
        "bytes": snap["bytes_copied"],
        "errors": snap["error_count"],
    }


def run_robocopy(src: str, dst: str, mt: int, label: str) -> dict:
    _clean(dst)
    t0 = time.perf_counter()
    proc = subprocess.run(
        [
            "robocopy", src, dst, "/E", f"/MT:{mt}",
            "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/NC", "/R:0", "/W:0",
        ],
        capture_output=True, text=True,
    )
    elapsed = time.perf_counter() - t0
    files, total_bytes = _dir_stats(dst)
    # robocopy exit codes 0-7 all indicate success (bitmask of what happened);
    # 8+ indicates at least one failure.
    return {
        "engine": label,
        "elapsed_sec": elapsed,
        "files": files,
        "bytes": total_bytes,
        "errors": 0 if proc.returncode < 8 else proc.returncode,
    }


def run_fastcopy(fcp_path: str, src: str, dst: str) -> dict:
    """Benchmark FastCopy via its dedicated CLI tool (fcp.exe, not the GUI
    FastCopy.exe) in silent mode. `src\\*` (not bare `src`) is required so
    FastCopy copies the tree's *contents* into dst, matching how robocopy
    /E and our own engine are benchmarked here - passing `src` alone would
    instead nest it as dst\\<src folder name>\\..., an unfair file layout
    to compare against.
    """
    _clean(dst)
    src_glob = os.path.join(src, "*")
    t0 = time.perf_counter()
    proc = subprocess.run(
        [fcp_path, "/cmd=force_copy", "/no_ui", "/auto_close", src_glob, f"/to={dst}\\"],
        capture_output=True, text=True,
    )
    elapsed = time.perf_counter() - t0
    files, total_bytes = _dir_stats(dst)
    return {
        "engine": "FastCopy",
        "elapsed_sec": elapsed,
        "files": files,
        "bytes": total_bytes,
        "errors": 0 if proc.returncode == 0 else proc.returncode,
    }


def run_exe(exe_path: str, src: str, dst: str) -> dict:
    """Benchmark the actually-compiled Nuitka artifact (not the Python
    source under the interpreter) by shelling out to it, same as robocopy.
    """
    _clean(dst)
    t0 = time.perf_counter()
    proc = subprocess.run(
        [exe_path, "copy", src, dst, "--policy", "overwrite", "--quiet"],
        capture_output=True, text=True,
    )
    elapsed = time.perf_counter() - t0
    files, total_bytes = _dir_stats(dst)
    return {
        "engine": "FastestCopy.exe",
        "elapsed_sec": elapsed,
        "files": files,
        "bytes": total_bytes,
        "errors": 0 if proc.returncode == 0 else proc.returncode,
    }


def _print_summary(results: list[dict]) -> None:
    by_engine = defaultdict(list)
    for r in results:
        by_engine[r["engine"]].append(r)

    print("\n| Engine | Avg time (s) | Avg MB/s | Avg files/s | Errors |")
    print("|---|---|---|---|---|")
    for engine, rs in by_engine.items():
        avg_t = sum(r["elapsed_sec"] for r in rs) / len(rs)
        avg_mb = sum(r["mb_per_sec"] for r in rs) / len(rs)
        avg_fps = sum(r["files_per_sec"] for r in rs) / len(rs)
        errs = sum(r["errors"] for r in rs)
        print(f"| {engine} | {avg_t:.2f} | {avg_mb:.1f} | {avg_fps:.0f} | {errs} |")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--src", required=True)
    p.add_argument("--work", required=True, help="parent dir for benchmark destination folders")
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument("--out", default=None, help="path to write raw JSON results")
    p.add_argument("--skip-robocopy", action="store_true")
    p.add_argument("--exe", default=None, help="path to compiled FastestCopy CLI exe to benchmark")
    p.add_argument("--fastcopy", default=None, help="path to fcp.exe (FastCopy's CLI tool) to benchmark")
    args = p.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)

    src_files, src_bytes = _dir_stats(args.src)
    print(f"Source: {src_files} files, {src_bytes / (1024 * 1024):.1f} MB")

    warmup_dst = os.path.join(args.work, "_warmup")
    print("Warming OS file cache (untimed warmup copy)...")
    run_fastestcopy(args.src, warmup_dst)
    _clean(warmup_dst)

    engines = [
        ("fastestcopy", lambda: run_fastestcopy(args.src, os.path.join(args.work, "dst_fastestcopy"))),
    ]
    if args.exe:
        engines.append((
            "fastestcopy_exe",
            lambda: run_exe(args.exe, args.src, os.path.join(args.work, "dst_fastestcopy_exe")),
        ))
    if args.fastcopy:
        engines.append((
            "fastcopy",
            lambda: run_fastcopy(args.fastcopy, args.src, os.path.join(args.work, "dst_fastcopy")),
        ))
    if not args.skip_robocopy:
        engines.append((
            "robocopy_mt8",
            lambda: run_robocopy(args.src, os.path.join(args.work, "dst_robocopy_mt8"), 8, "Robocopy /MT:8"),
        ))
        engines.append((
            "robocopy_mt32",
            lambda: run_robocopy(args.src, os.path.join(args.work, "dst_robocopy_mt32"), 32, "Robocopy /MT:32"),
        ))

    results = []
    for round_idx in range(args.rounds):
        for _key, fn in engines:
            r = fn()
            r["round"] = round_idx
            r["mb_per_sec"] = r["bytes"] / r["elapsed_sec"] / (1024 * 1024)
            r["files_per_sec"] = r["files"] / r["elapsed_sec"]
            print(
                f"[round {round_idx}] {r['engine']}: {r['elapsed_sec']:.2f}s, "
                f"{r['mb_per_sec']:.1f} MB/s, {r['files_per_sec']:.0f} files/s, errors={r['errors']}"
            )
            results.append(r)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"source": {"files": src_files, "bytes": src_bytes}, "results": results}, f, indent=2)
        print(f"Saved raw results to {args.out}")

    _print_summary(results)


if __name__ == "__main__":
    main()
