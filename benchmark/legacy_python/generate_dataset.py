"""Generates a synthetic "many small folders, many small files" test tree,
used to stress the scenario Windows Explorer/robocopy handle worst:
metadata/open-close overhead dominating over raw throughput.
"""
from __future__ import annotations

import argparse
import os
import random
import shutil
import time


def _random_pool(size: int = 8 * 1024 * 1024) -> bytes:
    return os.urandom(size)


def generate(
    root: str,
    *,
    num_dirs: int,
    files_per_dir: int,
    min_size: int,
    max_size: int,
    breadth: int,
    seed: int = 1234,
) -> dict:
    rng = random.Random(seed)
    pool = _random_pool()
    pool_len = len(pool)

    if os.path.exists(root):
        shutil.rmtree(root)
    os.makedirs(root, exist_ok=True)

    dirs = [root]
    frontier = [root]
    idx = 0
    while len(dirs) - 1 < num_dirs and frontier:
        parent = frontier.pop(0)
        for _ in range(breadth):
            if len(dirs) - 1 >= num_dirs:
                break
            d = os.path.join(parent, f"d{idx:06d}")
            idx += 1
            os.makedirs(d, exist_ok=True)
            dirs.append(d)
            frontier.append(d)

    total_bytes = 0
    total_files = 0
    for d in dirs[1:]:  # skip root itself, only populate leaf/branch dirs created
        for i in range(files_per_dir):
            size = rng.randint(min_size, max_size)
            if size <= pool_len:
                start = rng.randint(0, pool_len - size)
                data = pool[start : start + size]
            else:
                reps = size // pool_len + 1
                data = (pool * reps)[:size]
            with open(os.path.join(d, f"f{i:04d}.dat"), "wb") as f:
                f.write(data)
            total_bytes += size
            total_files += 1

    return {"dirs": len(dirs) - 1, "files": total_files, "bytes": total_bytes}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root")
    p.add_argument("--num-dirs", type=int, default=2000)
    p.add_argument("--files-per-dir", type=int, default=10)
    p.add_argument("--min-size", type=int, default=1024)
    p.add_argument("--max-size", type=int, default=51200)
    p.add_argument("--breadth", type=int, default=10, help="child dirs per parent (controls tree depth)")
    args = p.parse_args(argv)

    t0 = time.monotonic()
    info = generate(
        args.root,
        num_dirs=args.num_dirs,
        files_per_dir=args.files_per_dir,
        min_size=args.min_size,
        max_size=args.max_size,
        breadth=args.breadth,
    )
    elapsed = time.monotonic() - t0
    print(
        f"Generated {info['dirs']} dirs, {info['files']} files, "
        f"{info['bytes'] / (1024 * 1024):.1f} MB in {elapsed:.1f}s -> {args.root}"
    )


if __name__ == "__main__":
    main()
