"""Relaunch this application elevated (UAC) - used for the optional
large-file preallocation speedup, which requires SeManageVolumePrivilege.
"""
from __future__ import annotations

import ctypes
import sys


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Ask Windows to relaunch this process elevated via the UAC prompt.
    Returns True if the relaunch request was issued (caller should close
    the current, non-elevated window regardless of whether the user
    approves the UAC prompt) - False if it couldn't even be attempted.
    """
    try:
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = " ".join(f'"{a}"' for a in sys.argv[1:])
        else:
            exe = sys.executable
            params = "-m fastestcopy.gui.app " + " ".join(f'"{a}"' for a in sys.argv[1:])
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return rc > 32  # ShellExecuteW: return values > 32 indicate success
    except Exception:
        return False


def relaunch_normal() -> bool:
    """Launch a fresh, non-elevated copy of this application (no UAC
    prompt) - used after a settings change (e.g. UI language) that only
    takes effect on next launch. Caller should close the current window
    regardless of the return value's outer process having started.
    """
    try:
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = " ".join(f'"{a}"' for a in sys.argv[1:])
        else:
            exe = sys.executable
            params = "-m fastestcopy.gui.app " + " ".join(f'"{a}"' for a in sys.argv[1:])
        rc = ctypes.windll.shell32.ShellExecuteW(None, "open", exe, params, None, 1)
        return rc > 32
    except Exception:
        return False
