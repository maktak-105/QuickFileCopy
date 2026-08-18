"""Headless entry point: used directly by users on the command line, and by
the benchmark harness / tests so the copy engine can be exercised without
booting the GUI.
"""
from __future__ import annotations

import argparse
import sys
import time

from fastestcopy.engine.copier import run_copy
from fastestcopy.engine.policy import ConflictPolicy

_POLICY_MAP = {
    "skip": ConflictPolicy.SKIP,
    "overwrite": ConflictPolicy.OVERWRITE,
    "overwrite-if-newer": ConflictPolicy.OVERWRITE_IF_NEWER,
}


def _print_progress(snapshot: dict) -> None:
    sys.stdout.write(
        f"\r{snapshot['files_copied']} files, "
        f"{snapshot['bytes_copied'] / (1024 * 1024):.1f} MB, "
        f"{snapshot['mb_per_sec']:.1f} MB/s, "
        f"{snapshot['files_per_sec']:.0f} files/s, "
        f"skipped={snapshot['files_skipped']}, errors={snapshot['error_count']}   "
    )
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fastestcopy", description="Fast parallel file copy")
    sub = parser.add_subparsers(dest="command", required=True)

    copy_p = sub.add_parser("copy", help="Copy src directory tree to dst")
    copy_p.add_argument("src")
    copy_p.add_argument("dst")
    copy_p.add_argument("--policy", choices=list(_POLICY_MAP), default="skip")
    copy_p.add_argument("--small-workers", type=int, default=None)
    copy_p.add_argument("--large-chunk-workers", type=int, default=None)
    copy_p.add_argument("--buffer-mb", type=int, default=8)
    copy_p.add_argument("--no-preallocate", action="store_true")
    copy_p.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "copy":
        start = time.monotonic()
        stats = run_copy(
            args.src,
            args.dst,
            policy=_POLICY_MAP[args.policy],
            small_workers=args.small_workers,
            large_chunk_workers=args.large_chunk_workers,
            buffer_size=args.buffer_mb * 1024 * 1024,
            preallocate_large=not args.no_preallocate,
            progress_cb=None if args.quiet else _print_progress,
        )
        elapsed = time.monotonic() - start
        snap = stats.snapshot()
        if not args.quiet:
            print()
        print(
            f"Done in {elapsed:.2f}s: {snap['files_copied']} files copied, "
            f"{snap['files_skipped']} skipped, {snap['error_count']} errors, "
            f"{snap['bytes_copied'] / (1024 * 1024):.1f} MB "
            f"({snap['bytes_copied'] / elapsed / (1024 * 1024):.1f} MB/s)"
        )
        if stats.errors:
            for path, err in stats.errors[:20]:
                print(f"  ERROR {path}: {err}", file=sys.stderr)
            return 1
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
