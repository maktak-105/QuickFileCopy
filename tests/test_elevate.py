import os
import sys
from unittest.mock import patch

from fastestcopy.gui.elevate import (
    ERROR_CANCELLED,
    SE_ERR_ACCESSDENIED,
    _get_relaunch_target,
    is_admin,
    relaunch_as_admin,
    relaunch_normal,
)


def test_is_admin():
    with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=1):
        assert is_admin() is True
    with patch("ctypes.windll.shell32.IsUserAnAdmin", return_value=0):
        assert is_admin() is False


def test_get_relaunch_target_nuitka_onefile(tmp_path):
    fake_binary = str(tmp_path / "FastestCopy.exe")
    with open(fake_binary, "w") as f:
        f.write("fake")
    with patch.dict(os.environ, {"NUITKA_ONEFILE_BINARY": fake_binary}):
        exe, params = _get_relaunch_target()
        assert exe == os.path.abspath(fake_binary)


def test_get_relaunch_target_exe_argv(tmp_path):
    fake_exe = str(tmp_path / "FastestCopy.exe")
    with open(fake_exe, "w") as f:
        f.write("fake")
    with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", [fake_exe, "--foo"]):
        exe, params = _get_relaunch_target()
        assert exe == os.path.abspath(fake_exe)
        assert params == '"--foo"'


def test_relaunch_as_admin_success():
    with patch("fastestcopy.gui.elevate._shell_execute_w", return_value=42) as mock_shell:
        res = relaunch_as_admin(hwnd=12345)
        assert res is True
        mock_shell.assert_called_once()
        args = mock_shell.call_args[0]
        assert args[0] == 12345
        assert args[1] == "runas"
        assert args[4] == os.getcwd()


def test_relaunch_as_admin_user_cancelled():
    with patch("fastestcopy.gui.elevate._shell_execute_w", return_value=SE_ERR_ACCESSDENIED):
        assert relaunch_as_admin(hwnd=12345) is None
    with patch("fastestcopy.gui.elevate._shell_execute_w", return_value=0), \
         patch("ctypes.get_last_error", return_value=ERROR_CANCELLED):
        assert relaunch_as_admin(hwnd=12345) is None


def test_relaunch_as_admin_failure():
    with patch("fastestcopy.gui.elevate._shell_execute_w", return_value=2):
        assert relaunch_as_admin(hwnd=12345) is False


def test_relaunch_normal_success():
    with patch("fastestcopy.gui.elevate._shell_execute_w", return_value=42) as mock_shell:
        assert relaunch_normal(hwnd=12345) is True
        args = mock_shell.call_args[0]
        assert args[0] == 0
        assert args[1] == "open"
        assert args[4] == os.getcwd()
