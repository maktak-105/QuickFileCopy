from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
QFC = ROOT / "dist" / "binary" / "QuickFileCopy_cli.exe"
ELAPSED = re.compile(r"elapsed:\s+([0-9.]+)\s+s")


def write_repeated(path: Path, size: int, seed: int) -> None:
    block = bytearray(1024 * 1024)
    value = seed & 0xFFFFFFFF
    for offset in range(0, len(block), 4):
        value = (1664525 * value + 1013904223) & 0xFFFFFFFF
        block[offset : offset + 4] = value.to_bytes(4, "little")
    with path.open("wb", buffering=0) as stream:
        remaining = size
        while remaining:
            chunk = min(remaining, len(block))
            stream.write(memoryview(block)[:chunk])
            remaining -= chunk


def create_corpus(root: Path, profile: str, file_count: int | None) -> tuple[int, int]:
    root.mkdir(parents=True)
    files = 0
    total = 0
    if profile in {"small", "mixed"}:
        count, size = (1000, 64 * 1024) if profile == "small" else (500, 16 * 1024)
        if file_count is not None:
            count = file_count
        for index in range(count):
            directory = root / f"d{index % 20:02d}"
            directory.mkdir(exist_ok=True)
            payload = (f"QuickFileCopy benchmark {index:08d}\n".encode() * 2048)[:size]
            (directory / f"f{index:05d}.bin").write_bytes(payload)
            files += 1
            total += len(payload)
    if profile in {"large", "mixed"}:
        count, size = (1, 512 * 1024 * 1024) if profile == "large" else (4, 64 * 1024 * 1024)
        for index in range(count):
            write_repeated(root / f"large-{index}.bin", size, 0x51464300 + index)
            files += 1
            total += size
    return files, total


def safe_remove(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    resolved_parent = parent.resolve()
    if resolved.parent != resolved_parent or not resolved.name.startswith("qfc-benchmark-"):
        raise RuntimeError(f"Refusing to remove unexpected path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def run_once(source: Path, destination: Path, workers: int, verify: bool) -> dict[str, object]:
    command = [
        str(QFC), "copy", str(source), str(destination),
        "--policy", "overwrite", "--workers", str(workers), "--quiet",
    ]
    if verify:
        command.append("--verify")
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True)
    wall = time.perf_counter() - started
    output = completed.stdout + completed.stderr
    match = ELAPSED.search(output)
    return {
        "workers": workers,
        "verify": verify,
        "exit_code": completed.returncode,
        "engine_seconds": float(match.group(1)) if match else None,
        "wall_seconds": round(wall, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("small", "large", "mixed"), required=True)
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--workers", default="1,4,8")
    parser.add_argument("--file-count", type=int)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if not QFC.exists():
        raise SystemExit(f"Build first: {QFC}")

    corpus_parent = ROOT / "dist" / "benchmark-corpus"
    corpus = corpus_parent / args.profile
    if corpus.exists():
        shutil.rmtree(corpus)
    files, total = create_corpus(corpus, args.profile, args.file_count)

    destination_root = args.destination_root.resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / f"qfc-benchmark-{uuid.uuid4().hex}"
    results: list[dict[str, object]] = []
    try:
        for workers in (int(value) for value in args.workers.split(",")):
            destination.mkdir()
            result = run_once(corpus, destination, workers, args.verify)
            result.update({"profile": args.profile, "files": files, "bytes": total})
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            safe_remove(destination, destination_root)
    finally:
        safe_remove(destination, destination_root)

    print(json.dumps({"complete": True, "results": results}, ensure_ascii=False), flush=True)
    return 0 if all(item["exit_code"] == 0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
