"""Relaunch this application elevated (UAC) - used for the optional
large-file preallocation speedup, which requires SeManageVolumePrivilege.
"""
from __future__ import annotations

import ctypes
import os
import sys

SE_ERR_ACCESSDENIED = 5
ERROR_CANCELLED = 1223


def _shell_execute_w(hwnd: int, op: str, file: str, params: str, dir_path: str | None, show: int) -> int:
    return ctypes.windll.shell32.ShellExecuteW(hwnd, op, file, params, dir_path, show)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _get_cwd() -> str | None:
    try:
        cwd = os.getcwd()
        return cwd if os.path.exists(cwd) else None
    except Exception:
        return None


def _log_relaunch(msg: str) -> None:
    try:
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "FastestCopy", "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "relaunch.log"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _get_relaunch_target() -> tuple[str, str]:
    # 1. Check if running under Nuitka --onefile (NUITKA_ONEFILE_BINARY)
    onefile_binary = os.environ.get("NUITKA_ONEFILE_BINARY")
    if onefile_binary and os.path.exists(onefile_binary):
        exe = os.path.abspath(onefile_binary)
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
        return exe, params

    # 2. Check if sys.argv[0] is an existing .exe (Nuitka / PyInstaller executable)
    if sys.argv and sys.argv[0].endswith(".exe") and os.path.exists(sys.argv[0]):
        exe = os.path.abspath(sys.argv[0])
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
        return exe, params

    # 3. Check if running as a compiled frozen application
    is_compiled = getattr(sys, "frozen", False) or "__compiled__" in globals() or "nuitka" in sys.modules
    if is_compiled:
        exe = sys.executable
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
        return exe, params

    # 4. Running as a normal Python script under interpreter
    exe = sys.executable
    if sys.argv:
        first_arg = os.path.abspath(sys.argv[0])
        if os.path.exists(first_arg) and first_arg.endswith(".py"):
            params = f'"{first_arg}" ' + " ".join(f'"{a}"' for a in sys.argv[1:])
            return exe, params

    params = "-m fastestcopy.gui.app " + " ".join(f'"{a}"' for a in sys.argv[1:])
    return exe, params


def relaunch_as_admin(hwnd: int = 0) -> bool | None:
    """Ask Windows to relaunch this process elevated via the UAC prompt.
    Returns:
        True: Relaunch process started (caller should close current window)
        None: User cancelled the UAC prompt (caller should take no action)
        False: Couldn't even attempt relaunch (caller should show error)
    """
    try:
        cwd = _get_cwd()
        exe, params = _get_relaunch_target()
        _log_relaunch(f"[relaunch_as_admin] hwnd={hwnd}, exe={exe}, params={params}, cwd={cwd}")

        rc = _shell_execute_w(hwnd, "runas", exe, params, cwd, 1)
        _log_relaunch(f"[relaunch_as_admin] ShellExecuteW returned rc={rc}")

        if rc > 32:
            return True
        err = ctypes.get_last_error()
        _log_relaunch(f"[relaunch_as_admin] Failed or cancelled. rc={rc}, get_last_error={err}")
        if rc == SE_ERR_ACCESSDENIED or err in (SE_ERR_ACCESSDENIED, ERROR_CANCELLED):
            return None  # User cancelled UAC prompt
        return False
    except Exception as e:
        _log_relaunch(f"[relaunch_as_admin] Exception: {e}")
        return False


def relaunch_normal(hwnd: int = 0) -> bool:
    """Launch a fresh, non-elevated copy of this application (no UAC
    prompt) - used after a settings change (e.g. UI language) that only
    takes effect on next launch. Caller should close the current window
    regardless of the return value's outer process having started.
    """
    try:
        cwd = _get_cwd()
        exe, params = _get_relaunch_target()
        _log_relaunch(f"[relaunch_normal] hwnd={hwnd}, exe={exe}, params={params}, cwd={cwd}")

        # Pass 0 for hwnd to ensure the newly spawned process is decoupled
        # from the parent window that will be closed immediately after.
        rc = _shell_execute_w(0, "open", exe, params, cwd, 1)
        _log_relaunch(f"[relaunch_normal] ShellExecuteW returned rc={rc}")
        if rc <= 32:
            err = ctypes.get_last_error()
            _log_relaunch(f"[relaunch_normal] Failed. rc={rc}, get_last_error={err}")
        return rc > 32
    except Exception as e:
        _log_relaunch(f"[relaunch_normal] Exception: {e}")
        return False

