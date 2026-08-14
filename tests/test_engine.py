import filecmp
import hashlib
import os
import time

import pytest

from fastestcopy.engine.copier import run_copy, run_copy_multi
from fastestcopy.engine.policy import ConflictPolicy


def _make_tree(root, spec):
    """spec: dict of relative path -> bytes content (dirs auto-created)."""
    for rel, content in spec.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def test_basic_copy_matches_content(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    _make_tree(
        src,
        {
            "a.txt": b"hello",
            "sub1/b.txt": b"world" * 100,
            "sub1/sub2/c.bin": os.urandom(50_000),
        },
    )

    stats = run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP)
    snap = stats.snapshot()

    assert snap["files_copied"] == 3
    assert snap["error_count"] == 0
    cmp = filecmp.dircmp(str(src), str(dst))
    assert not cmp.diff_files
    assert not cmp.left_only
    for rel in ("a.txt", "sub1/b.txt", "sub1/sub2/c.bin"):
        assert _sha256(str(src / rel)) == _sha256(str(dst / rel))


def test_skip_policy_does_not_recopy(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    _make_tree(src, {"a.txt": b"v1", "b.txt": b"v2"})

    run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP)

    # mutate source, re-run with SKIP: destination must stay at v1/v2
    _make_tree(src, {"a.txt": b"CHANGED"})
    stats = run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP)
    snap = stats.snapshot()

    assert snap["files_copied"] == 0
    assert snap["files_skipped"] == 2
    assert (dst / "a.txt").read_bytes() == b"v1"


def test_overwrite_policy_replaces_existing(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    _make_tree(src, {"a.txt": b"v1"})
    run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP)

    _make_tree(src, {"a.txt": b"v2-longer-content"})
    stats = run_copy(str(src), str(dst), policy=ConflictPolicy.OVERWRITE)
    snap = stats.snapshot()

    assert snap["files_copied"] == 1
    assert (dst / "a.txt").read_bytes() == b"v2-longer-content"


def test_overwrite_if_newer_only_recopies_changed(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    _make_tree(src, {"a.txt": b"v1", "b.txt": b"unchanged"})
    run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP)

    # policy tolerates <=2s mtime differences (FAT-resolution tolerance, like
    # robocopy), so push the changed file's mtime well past that threshold
    # instead of relying on real elapsed time between the two run_copy calls.
    _make_tree(src, {"a.txt": b"v1-new-and-longer"})
    future = time.time() + 10
    os.utime(src / "a.txt", (future, future))

    stats = run_copy(str(src), str(dst), policy=ConflictPolicy.OVERWRITE_IF_NEWER)
    snap = stats.snapshot()

    assert snap["files_copied"] == 1
    assert snap["files_skipped"] == 1
    assert (dst / "a.txt").read_bytes() == b"v1-new-and-longer"
    assert (dst / "b.txt").read_bytes() == b"unchanged"


def test_ask_policy_uses_callback(tmp_path):
    from fastestcopy.engine.policy import ConflictAction

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    _make_tree(src, {"a.txt": b"v1", "b.txt": b"v1"})
    run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP)

    _make_tree(src, {"a.txt": b"v2", "b.txt": b"v2"})

    decisions = {"a.txt": ConflictAction.COPY, "b.txt": ConflictAction.SKIP}

    def ask(src_path, dst_path):
        return decisions[os.path.basename(src_path)]

    stats = run_copy(str(src), str(dst), policy=ConflictPolicy.ASK, ask_callback=ask)
    snap = stats.snapshot()

    assert snap["files_copied"] == 1
    assert snap["files_skipped"] == 1
    assert (dst / "a.txt").read_bytes() == b"v2"
    assert (dst / "b.txt").read_bytes() == b"v1"


def test_large_file_chunked_path_matches_content(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    data = os.urandom(5 * 1024 * 1024)  # 5MB, made non-uniform to catch chunk-boundary bugs
    (src / "big.bin").write_bytes(data)

    # lower the small/large threshold so this 5MB file takes the chunked path
    stats = run_copy(
        str(src),
        str(dst),
        policy=ConflictPolicy.SKIP,
        small_threshold=1024 * 1024,
        large_chunk_workers=4,
        preallocate_large=False,
    )
    snap = stats.snapshot()

    assert snap["error_count"] == 0
    assert snap["files_copied"] == 1
    assert (dst / "big.bin").read_bytes() == data


def test_large_file_chunked_path_forced_matches_content(tmp_path, monkeypatch):
    """Force the true parallel-chunk branch (normally gated behind admin's
    SeManageVolumePrivilege) to make sure it still produces correct output
    even when SetFileValidData silently fails - only the zero-fill speedup
    is lost, not correctness.
    """
    from fastestcopy.engine import winio

    monkeypatch.setattr(winio, "has_manage_volume_privilege", lambda: True)

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    data = os.urandom(5 * 1024 * 1024)
    (src / "big.bin").write_bytes(data)

    stats = run_copy(
        str(src),
        str(dst),
        policy=ConflictPolicy.SKIP,
        small_threshold=1024 * 1024,
        large_chunk_workers=4,
        preallocate_large=True,
    )
    snap = stats.snapshot()

    assert snap["error_count"] == 0
    assert snap["files_copied"] == 1
    assert (dst / "big.bin").read_bytes() == data


def test_timestamps_preserved(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    _make_tree(src, {"a.txt": b"hi"})
    old_time = time.time() - 100_000
    os.utime(src / "a.txt", (old_time, old_time))

    run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP)

    src_mtime = os.stat(src / "a.txt").st_mtime
    dst_mtime = os.stat(dst / "a.txt").st_mtime
    assert abs(src_mtime - dst_mtime) < 1.0


def test_progress_reaches_100_percent_even_with_all_skipped(tmp_path):
    """progress_pct/eta_sec are only meaningful once scanning is done, and
    must reach 100% regardless of how many files end up skipped - skipped
    bytes count as "processed" for this purpose even though they're not
    reflected in bytes_copied (which stays throughput-only).
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    _make_tree(src, {"a.txt": b"x" * 1000, "b.txt": b"y" * 2000})

    stats = run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP)
    snap = stats.snapshot()
    assert snap["scan_done"] is True
    assert snap["total_bytes_found"] == 3000
    assert snap["progress_pct"] == pytest.approx(100.0)

    # re-run: both files now exist unchanged, so everything gets skipped -
    # bytes_copied stays 0, but progress must still reach 100%, not stall.
    stats2 = run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP)
    snap2 = stats2.snapshot()
    assert snap2["files_skipped"] == 2
    assert snap2["bytes_copied"] == 0
    assert snap2["progress_pct"] == pytest.approx(100.0)


def test_run_copy_multi_keeps_each_item_own_name(tmp_path):
    """Multi-select paste: unlike run_copy (which merges src's contents
    into dst), each selected item - file or folder - keeps its own name
    at the destination.
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    _make_tree(
        src,
        {
            "lonefile.txt": b"solo",
            "folder/a.txt": b"hello",
            "folder/sub/b.txt": b"world",
        },
    )

    items = [
        (str(src / "lonefile.txt"), str(dst / "lonefile.txt")),
        (str(src / "folder"), str(dst / "folder")),
    ]
    stats = run_copy_multi(items, policy=ConflictPolicy.SKIP)
    snap = stats.snapshot()

    assert snap["error_count"] == 0
    assert snap["files_copied"] == 3
    assert (dst / "lonefile.txt").read_bytes() == b"solo"
    assert (dst / "folder" / "a.txt").read_bytes() == b"hello"
    assert (dst / "folder" / "sub" / "b.txt").read_bytes() == b"world"


def test_run_copy_multi_respects_conflict_policy(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.txt").write_bytes(b"new")
    (dst / "a.txt").write_bytes(b"existing")

    stats = run_copy_multi(
        [(str(src / "a.txt"), str(dst / "a.txt"))], policy=ConflictPolicy.SKIP
    )
    snap = stats.snapshot()

    assert snap["files_copied"] == 0
    assert snap["files_skipped"] == 1
    assert (dst / "a.txt").read_bytes() == b"existing"


def test_empty_source_tree_produces_no_errors(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "emptydir").mkdir()

    stats = run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP)
    snap = stats.snapshot()

    assert snap["error_count"] == 0
    assert snap["files_copied"] == 0
    assert (dst / "emptydir").is_dir()
