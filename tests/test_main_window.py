import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from fastestcopy.gui.main_window import MainWindow, _dest_name_for


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def main_window(qapp, monkeypatch):
    # _validate_copy_pair pops a real modal QMessageBox on failure - stub
    # it out so tests don't block waiting for a user to click it.
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    return MainWindow()


def test_dest_name_for_normal_path_uses_basename():
    assert _dest_name_for("C:\\Users\\makta\\myfolder") == "myfolder"
    assert _dest_name_for("C:\\Users\\makta\\myfile.txt") == "myfile.txt"


def test_dest_name_for_drive_root_falls_back_to_letter():
    assert _dest_name_for("C:\\") == "C"
    assert _dest_name_for("D:\\") == "D"


def test_dest_name_for_unc_share_root_falls_back_to_host_share():
    assert _dest_name_for("\\\\BEELINK\\Users") == "BEELINK_Users"


def test_validate_copy_pair_allows_unrelated_folders(main_window):
    dst_abs = os.path.normcase(os.path.abspath("D:\\backup"))
    assert main_window._validate_copy_pair("C:\\Users\\makta\\src", dst_abs) is True


def test_validate_copy_pair_blocks_same_folder(main_window):
    dst_abs = os.path.normcase(os.path.abspath("C:\\Users\\makta\\same"))
    assert main_window._validate_copy_pair("C:\\Users\\makta\\same", dst_abs) is False


def test_validate_copy_pair_blocks_dst_nested_in_normal_src(main_window):
    dst_abs = os.path.normcase(os.path.abspath("C:\\Users\\makta\\src\\sub"))
    assert main_window._validate_copy_pair("C:\\Users\\makta\\src", dst_abs) is False


def test_validate_copy_pair_blocks_drive_root_source_into_own_subfolder(main_window):
    """Regression: a drive root's abspath already ends in a separator, so
    the naive `src_abs + os.sep` prefix check used to double it up and
    silently fail to detect that the destination is inside the drive
    being copied - which would try to copy C:\\ into a folder that's
    itself part of C:\\, recursing forever.
    """
    dst_abs = os.path.normcase(os.path.abspath("C:\\Users\\makta\\some_backup"))
    assert main_window._validate_copy_pair("C:\\", dst_abs) is False
