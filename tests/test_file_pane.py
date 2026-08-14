import ctypes
import os
import stat

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication

from fastestcopy.gui.file_pane import FilePane

FILE_ATTRIBUTE_HIDDEN = 0x2


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _set_hidden(path: str) -> None:
    ok = ctypes.windll.kernel32.SetFileAttributesW(path, FILE_ATTRIBUTE_HIDDEN)
    assert ok, ctypes.get_last_error()


def test_content_pane_allows_shift_ctrl_multiselect(qapp, tmp_path):
    pane = FilePane(str(tmp_path), "Test")
    assert pane.content_tree.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert pane.content_tree.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows


def test_selected_rows_reflects_multiple_selected_items(qapp, tmp_path):
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    (tmp_path / "c_folder").mkdir()

    pane = FilePane(str(tmp_path), "Test")
    assert pane.selected_rows() == []  # nothing explicitly selected yet

    model = pane.model
    root = pane.content_tree.rootIndex()
    # QFileSystemModel populates asynchronously (a background scan thread),
    # so rowCount() can still be 0 immediately after set_path(); pump the
    # event loop until it's done rather than racing it.
    for _ in range(200):
        if model.rowCount(root) >= 3:
            break
        qapp.processEvents()
    else:
        pytest.fail("QFileSystemModel never finished loading tmp_path")

    selection = pane.content_tree.selectionModel()
    picked = []
    for row in range(model.rowCount(root)):
        idx = model.index(row, 0, root)
        name = model.filePath(idx)
        if os.path.basename(name) in ("a.txt", "c_folder"):
            selection.select(
                idx, selection.SelectionFlag.Select | selection.SelectionFlag.Rows
            )
            picked.append(name)

    got = {os.path.normcase(p) for p in pane.selected_rows()}
    want = {os.path.normcase(p) for p in picked}
    assert got == want
    assert len(got) == 2


def test_hidden_files_excluded_from_nav_tree_by_default(qapp, tmp_path):
    (tmp_path / "visible_dir").mkdir()
    hidden_dir = tmp_path / "hidden_dir"
    hidden_dir.mkdir()
    _set_hidden(str(hidden_dir))

    pane = FilePane(str(tmp_path), "Test")
    assert pane.show_hidden is False

    # set_path() (called by FilePane's constructor) already reveals tmp_path
    # in the nav tree, so its item is already the current one.
    pane._reveal_in_nav_tree(str(tmp_path))
    current = pane.nav_tree.currentItem()
    assert current is not None
    pane.nav_tree.expandItem(current)
    names = {current.child(i).text(0) for i in range(current.childCount())}
    assert "visible_dir" in names
    assert "hidden_dir" not in names

    pane.set_show_hidden(True)
    pane._reveal_in_nav_tree(str(tmp_path))
    current = pane.nav_tree.currentItem()
    pane.nav_tree.expandItem(current)
    names = {current.child(i).text(0) for i in range(current.childCount())}
    assert "visible_dir" in names
    assert "hidden_dir" in names


def test_is_hidden_matches_windows_hidden_attribute(qapp, tmp_path):
    normal = tmp_path / "normal_dir"
    normal.mkdir()
    hidden = tmp_path / "hidden_dir2"
    hidden.mkdir()
    _set_hidden(str(hidden))

    entries = {e.name: e for e in os.scandir(tmp_path)}
    assert FilePane._is_hidden(entries["normal_dir"]) is False
    assert FilePane._is_hidden(entries["hidden_dir2"]) is True
