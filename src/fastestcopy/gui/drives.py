"""Drive discovery/classification for the nav tree: which drive letters are
local ("PC") vs mapped network shares ("ネットワーク"), plus Explorer-style
display labels ("Windows-SSD (C:)").
"""
from __future__ import annotations

import ctypes
import string

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

_k32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
_k32.GetDriveTypeW.restype = ctypes.c_uint

_k32.GetVolumeInformationW.argtypes = [
    ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_wchar_p, ctypes.c_uint,
]
_k32.GetVolumeInformationW.restype = ctypes.c_int

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

_LOCAL_TYPES = (DRIVE_FIXED, DRIVE_REMOVABLE, DRIVE_CDROM, DRIVE_RAMDISK)


def list_drives() -> tuple[list[str], list[str]]:
    """Returns (local_drives, network_drives), each entry like 'C:\\'.

    GetDriveTypeW itself tells us whether a letter is even mapped
    (DRIVE_NO_ROOT_DIR for unused letters), so no separate existence check
    is needed.
    """
    local: list[str] = []
    network: list[str] = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        drive_type = _k32.GetDriveTypeW(drive)
        if drive_type == DRIVE_REMOTE:
            network.append(drive)
        elif drive_type in _LOCAL_TYPES:
            local.append(drive)
    return local, network


def drive_label(drive: str) -> str:
    """'Windows-SSD (C:)' style label, falling back to the bare drive
    letter if the volume name can't be read (e.g. an unreachable/offline
    network drive, or blank media in a removable drive).
    """
    letter = drive.rstrip("\\")
    name_buf = ctypes.create_unicode_buffer(261)
    ok = _k32.GetVolumeInformationW(drive, name_buf, 261, None, None, None, None, 0)
    if ok and name_buf.value:
        return f"{name_buf.value} ({letter})"
    return letter
