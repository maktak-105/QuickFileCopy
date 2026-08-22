import filecmp
import hashlib
import os
import threading
import time

import pytest

from fastestcopy.engine.copier import run_copy, run_copy_jobs, run_copy_multi
from fastestcopy.engine.policy import ConflictPolicy
from fastestcopy.engine.preview import scan_preview


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


def test_copy_large_file_parallel_stops_mid_transfer(tmp_path):
    """A stop_event set partway through a chunked/sequential large-file
    copy must abort within that transfer, not only be checked between
    whole files - otherwise cancelling during one huge file waits for it
    to finish regardless.
    """
    from fastestcopy.engine import winio

    src = tmp_path / "big.bin"
    dst = tmp_path / "big_copy.bin"
    total_size = 5 * 1024 * 1024
    src.write_bytes(os.urandom(total_size))

    stop_event = threading.Event()
    seen = {"bytes": 0}

    def on_bytes(n):
        seen["bytes"] += n
        if seen["bytes"] >= 512 * 1024:  # cancel partway through, deterministically
            stop_event.set()

    with pytest.raises(winio.CopyCancelled):
        winio.copy_large_file_parallel(
            str(src),
            str(dst),
            total_size,
            buffer_size=64 * 1024,
            preallocate=False,
            on_bytes=on_bytes,
            stop_event=stop_event,
        )

    assert 0 < seen["bytes"] < total_size


def test_run_copy_cancel_mid_large_file_cleans_up_partial_file(tmp_path, monkeypatch):
    """End-to-end through run_copy: cancelling mid-copy of a large file
    must not be recorded as an error, and shouldn't leave a truncated
    partial file behind that could be mistaken for a complete one.

    Triggers the cancel from inside CopyStats.add_bytes (called once per
    buffer-sized chunk, same as the winio-level test above) rather than on
    a wall-clock delay, so this can't be flaky by the file finishing before
    a timer fires.
    """
    from fastestcopy.engine.stats import CopyStats

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "big.bin").write_bytes(os.urandom(5 * 1024 * 1024))

    stop_event = threading.Event()
    original_add_bytes = CopyStats.add_bytes

    def add_bytes_and_maybe_cancel(self, n):
        original_add_bytes(self, n)
        if self.bytes_copied >= 512 * 1024:
            stop_event.set()

    monkeypatch.setattr(CopyStats, "add_bytes", add_bytes_and_maybe_cancel)

    stats = run_copy(
        str(src),
        str(dst),
        policy=ConflictPolicy.SKIP,
        small_threshold=1,  # force this file onto the chunked/large path
        buffer_size=64 * 1024,
        preallocate_large=False,
        stop_event=stop_event,
    )
    snap = stats.snapshot()

    assert snap["error_count"] == 0
    assert not (dst / "big.bin").exists()


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


def test_scan_preview_classifies_new_changed_and_unchanged_files(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "unchanged.txt").write_bytes(b"same")
    (dst / "unchanged.txt").write_bytes(b"same")
    (src / "changed.txt").write_bytes(b"new version, longer")
    (dst / "changed.txt").write_bytes(b"old")
    (src / "brand_new.txt").write_bytes(b"never seen before")

    result = scan_preview([(str(src), str(dst))], ConflictPolicy.OVERWRITE_IF_NEWER)

    assert result.total_files == 3
    copied_names = {os.path.basename(j.src) for j in result.to_copy}
    skipped_names = {os.path.basename(j.src) for j in result.to_skip}
    assert copied_names == {"changed.txt", "brand_new.txt"}
    assert skipped_names == {"unchanged.txt"}
    assert result.to_ask == []


def test_scan_preview_ask_policy_defers_existing_conflicts(tmp_path):
    """ASK policy needs a live user decision, which a dry-run preview
    can't make - those go to to_ask instead of being pre-resolved.
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "new.txt").write_bytes(b"a")
    (src / "conflict.txt").write_bytes(b"b")
    (dst / "conflict.txt").write_bytes(b"existing")

    result = scan_preview([(str(src), str(dst))], ConflictPolicy.ASK)

    assert len(result.to_copy) == 1  # the genuinely new file
    assert result.to_copy[0].src.endswith("new.txt")
    assert len(result.to_ask) == 1  # the pre-existing one needs a live decision
    assert result.to_ask[0].src.endswith("conflict.txt")
    assert result.to_skip == []


def test_scan_preview_then_run_copy_jobs_copies_only_what_was_flagged(tmp_path):
    """The end-to-end "scan first, confirm, then copy" flow: preview a
    large batch where almost everything is already up to date, then copy
    only the flagged jobs - the untouched majority is never re-visited.
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    for i in range(200):
        content = f"content{i}".encode()
        (src / f"f{i}.txt").write_bytes(content)
        (dst / f"f{i}.txt").write_bytes(content)
    for i in range(5):
        (src / f"new{i}.txt").write_bytes(b"new")

    result = scan_preview([(str(src), str(dst))], ConflictPolicy.SKIP)
    assert len(result.to_copy) == 5
    assert len(result.to_skip) == 200

    stats = run_copy_jobs(result.to_copy + result.to_ask, policy=ConflictPolicy.SKIP)
    snap = stats.snapshot()

    assert snap["files_copied"] == 5
    assert snap["error_count"] == 0
    for i in range(5):
        assert (dst / f"new{i}.txt").read_bytes() == b"new"


def test_remove_partial_output_only_deletes_undersized_file(tmp_path):
    """Direct unit test of the cleanup helper's safety property: it must
    only remove a destination that's provably smaller than what the copy
    was supposed to produce, never a same-or-larger-sized file it might
    not have actually touched (e.g. the source never even got opened).
    """
    from fastestcopy.engine.copier import _remove_partial_output

    partial = tmp_path / "partial.bin"
    partial.write_bytes(b"ab")  # 2 bytes < expected 10 -> a failed copy's leftovers
    _remove_partial_output(str(partial), 10)
    assert not partial.exists()

    untouched = tmp_path / "untouched.bin"
    untouched.write_bytes(b"0123456789")  # already the expected size -> leave it alone
    _remove_partial_output(str(untouched), 10)
    assert untouched.exists()

    _remove_partial_output(str(tmp_path / "missing.bin"), 10)  # must not raise


def test_disk_full_during_large_copy_removes_partial_file(tmp_path, monkeypatch):
    """End-to-end: when the chunked large-file path fails partway (e.g.
    ERROR_DISK_FULL from WriteFile/SetEndOfFile), the truncated destination
    file it leaves behind must be cleaned up rather than left as an
    orphaned partial/zero-byte file, while the failure is still recorded.
    """
    from fastestcopy.engine import winio

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "big.bin").write_bytes(os.urandom(5 * 1024 * 1024))

    def fake_copy_large_file_parallel(src_path, dst_path, size, **kwargs):
        # CREATE_ALWAYS truncates the destination immediately on open, then
        # the disk fills up before any/all of the data is written.
        with open(dst_path, "wb") as f:
            f.write(b"partial")
        raise OSError("simulated ERROR_DISK_FULL")

    monkeypatch.setattr(winio, "copy_large_file_parallel", fake_copy_large_file_parallel)

    stats = run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP, small_threshold=1)
    snap = stats.snapshot()

    assert snap["error_count"] == 1
    assert not (dst / "big.bin").exists()


def test_disk_full_during_small_copy_removes_partial_file(tmp_path, monkeypatch):
    """Same guarantee as above, for the small-file (CopyFileExW) path."""
    from fastestcopy.engine import winio

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "a.txt").write_bytes(b"hello world")

    def fake_raw_copy_file(src_path, dst_path, buffer_size):
        open(dst_path, "wb").close()  # created, 0 bytes written before the failure
        raise OSError("simulated ERROR_DISK_FULL")

    monkeypatch.setattr(winio, "raw_copy_file", fake_raw_copy_file)

    stats = run_copy(str(src), str(dst), policy=ConflictPolicy.SKIP)
    snap = stats.snapshot()

    assert snap["error_count"] == 1
    assert not (dst / "a.txt").exists()


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
