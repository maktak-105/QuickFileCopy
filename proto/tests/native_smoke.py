from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
QFC = ROOT / "dist" / "QuickFileCopy_cli.exe"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def make_junction(link: Path, target: Path) -> None:
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise AssertionError(completed.stdout + completed.stderr)


class NativeCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        if not QFC.exists():
            self.fail(f"Build first: {QFC}")
        self.temp = tempfile.TemporaryDirectory(prefix="qfc-native-test-", dir=ROOT / "dist")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_copy(self, source: Path, destination: Path, *options: str, expected: int = 0):
        completed = subprocess.run(
            [str(QFC), "copy", str(source), str(destination), "--quiet", *options],
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, expected, completed.stdout + completed.stderr)
        return completed

    def test_contents_overwrite_and_strong_verify(self) -> None:
        source = self.root / "source"
        destination = self.root / "destination"
        (source / "nested").mkdir(parents=True)
        destination.mkdir()
        payload = os.urandom(17 * 1024 * 1024 + 123)
        (source / "nested" / "payload.bin").write_bytes(payload)
        (destination / "nested").mkdir()
        (destination / "nested" / "payload.bin").write_bytes(b"old")

        self.run_copy(
            source, destination, "--policy", "overwrite", "--verify",
        )
        copied = destination / "nested" / "payload.bin"
        self.assertEqual(digest(copied), hashlib.sha256(payload).hexdigest())

    def test_existing_directory_junction_is_replaced(self) -> None:
        source = self.root / "source"
        destination = self.root / "destination"
        target_new = self.root / "target-new"
        target_old = self.root / "target-old"
        source.mkdir()
        destination.mkdir()
        target_new.mkdir()
        target_old.mkdir()
        (target_new / "new.txt").write_text("new", encoding="utf-8")
        (target_old / "old.txt").write_text("old", encoding="utf-8")
        make_junction(source / "linked", target_new)
        make_junction(destination / "linked", target_old)

        self.run_copy(source, destination, "--policy", "overwrite")
        self.assertTrue((destination / "linked" / "new.txt").is_file())
        self.assertFalse((destination / "linked" / "old.txt").exists())
        self.assertTrue((target_old / "old.txt").is_file())

    def test_real_destination_directory_is_never_replaced_by_reparse_point(self) -> None:
        source = self.root / "source"
        destination = self.root / "destination"
        target = self.root / "target"
        source.mkdir()
        (destination / "linked").mkdir(parents=True)
        target.mkdir()
        (destination / "linked" / "guard.txt").write_text("keep", encoding="utf-8")
        make_junction(source / "linked", target)

        self.run_copy(source, destination, "--policy", "overwrite", expected=1)
        self.assertEqual(
            (destination / "linked" / "guard.txt").read_text(encoding="utf-8"), "keep"
        )

    def test_multiple_sources_are_merged(self) -> None:
        source_a = self.root / "source-a"
        source_b = self.root / "source-b"
        destination = self.root / "destination"
        source_a.mkdir()
        source_b.mkdir()
        destination.mkdir()
        (source_a / "a.txt").write_text("A", encoding="utf-8")
        (source_b / "b.txt").write_text("B", encoding="utf-8")

        self.run_copy(source_a, destination, "--add-source", str(source_b))
        self.assertEqual((destination / "a.txt").read_text(encoding="utf-8"), "A")
        self.assertEqual((destination / "b.txt").read_text(encoding="utf-8"), "B")


if __name__ == "__main__":
    unittest.main(verbosity=2)
