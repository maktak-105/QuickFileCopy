"""Thin wrappers around the Windows APIs used for high-throughput file transfer.

Design note: file copying is I/O-bound, not CPU-bound. The kernel does the
actual disk work regardless of the calling language, so the speed gains here
come from *how* we call the API (large buffers, sequential-scan hints,
per-chunk parallelism for large files, skipping NTFS zero-fill via
SetFileValidData when privileged) rather than from avoiding Python.

Implementation note: this module calls kernel32 directly via `ctypes`
instead of using `pywin32`'s win32file wrappers. Benchmarking during
development showed pywin32's per-call marshalling overhead makes
ReadFile/WriteFile roughly 5x slower than plain ctypes for small files
(174 files/s vs ~1400 files/s sequentially on the same dataset) - hard
to overcome with more threads since it's pure Python/API-boundary
overhead, not I/O wait. ctypes calls release the GIL for the duration of
the foreign call, so threads copying different files still run their
kernel32 calls concurrently.
"""
from __future__ import annotations

import ctypes
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from typing import Callable, Optional

import win32api
import win32security

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

_k32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_k32.CreateFileW.restype = wintypes.HANDLE

_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL

_k32.ReadFile.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]
_k32.ReadFile.restype = wintypes.BOOL

_k32.WriteFile.argtypes = [
    wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]
_k32.WriteFile.restype = wintypes.BOOL

_k32.SetFilePointerEx.argtypes = [
    wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD,
]
_k32.SetFilePointerEx.restype = wintypes.BOOL

_k32.SetEndOfFile.argtypes = [wintypes.HANDLE]
_k32.SetEndOfFile.restype = wintypes.BOOL

_k32.CopyFileExW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID, wintypes.LPVOID,
    ctypes.POINTER(wintypes.BOOL), wintypes.DWORD,
]
_k32.CopyFileExW.restype = wintypes.BOOL

_k32.SetFileValidData.argtypes = [wintypes.HANDLE, ctypes.c_longlong]
_k32.SetFileValidData.restype = wintypes.BOOL

INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
CREATE_ALWAYS = 2
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
FILE_BEGIN = 0

DEFAULT_BUFFER_SIZE = 4 * 1024 * 1024  # 4MB transfer buffer for the chunked large-file path
DEFAULT_MIN_CHUNK_SIZE = 32 * 1024 * 1024  # don't split a large file into pieces smaller than this

_privilege_cache: dict[str, bool] = {}


class CopyCancelled(Exception):
    """Raised mid-transfer when stop_event is set, so a cancel during a
    huge file takes effect within one buffer_size read/write instead of
    only being noticed between whole-file jobs.
    """


class WinIOError(OSError):
    def __init__(self, path: str, operation: str):
        code = ctypes.get_last_error()
        msg = ctypes.FormatError(code)
        super().__init__(f"{operation} failed for {path!r}: [WinError {code}] {msg}")


def _create_file(path: str, access: int, share: int, disposition: int, flags: int) -> int:
    handle = _k32.CreateFileW(path, access, share, None, disposition, flags, None)
    if handle == INVALID_HANDLE_VALUE or not handle:
        raise WinIOError(path, "CreateFile")
    return handle


def _close(handle: int) -> None:
    _k32.CloseHandle(handle)


def enable_privilege(name: str) -> bool:
    """Try to enable a privilege (e.g. SeManageVolumePrivilege) for this process.

    Returns True only if the privilege was actually granted (the process
    token must already hold it, which normally requires an elevated/admin
    session). Never raises - callers should treat False as "fall back".
    Uses pywin32 here (not the hot path, called once and cached).
    """
    try:
        htoken = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32security.TOKEN_ADJUST_PRIVILEGES | win32security.TOKEN_QUERY,
        )
        try:
            luid = win32security.LookupPrivilegeValue(None, name)
            win32security.AdjustTokenPrivileges(
                htoken, False, [(luid, win32security.SE_PRIVILEGE_ENABLED)]
            )
            # AdjustTokenPrivileges silently drops privileges the token
            # doesn't hold instead of raising; ERROR_NOT_ALL_ASSIGNED (1300)
            # is how we detect that it didn't really take effect.
            return win32api.GetLastError() != 1300
        finally:
            htoken.Close()
    except Exception:
        return False


def has_manage_volume_privilege() -> bool:
    if "SeManageVolumePrivilege" not in _privilege_cache:
        _privilege_cache["SeManageVolumePrivilege"] = enable_privilege("SeManageVolumePrivilege")
    return _privilege_cache["SeManageVolumePrivilege"]


def set_file_valid_data(handle: int, length: int) -> bool:
    """Mark the file's data as valid up to `length`, skipping NTFS zero-fill.

    Requires SeManageVolumePrivilege (admin). handle must be opened with
    GENERIC_WRITE. Safe to call speculatively - failures are swallowed by
    the caller.
    """
    return bool(_k32.SetFileValidData(handle, ctypes.c_longlong(length)))


def copy_file_times_and_attrs(src: str, dst: str) -> None:
    """Preserve mtime/atime and basic attributes after a raw (non-CopyFileExW) copy."""
    st = os.stat(src)
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    try:
        os.chmod(dst, st.st_mode)
    except OSError:
        pass


def raw_copy_file(src: str, dst: str, buffer_size: int = DEFAULT_BUFFER_SIZE) -> None:
    """Copy one small/medium file. Backed by CopyFileExW, which is a single
    kernel32 call that already preserves attributes and last-write-time -
    no separate metadata pass needed. `buffer_size` is unused here (kept
    for API symmetry with the chunked path) since CopyFileExW manages its
    own internal buffering.
    """
    ok = _k32.CopyFileExW(src, dst, None, None, None, 0)
    if not ok:
        raise WinIOError(src, "CopyFileEx")


def _copy_sequential(
    src: str,
    dst: str,
    size: int,
    buffer_size: int,
    on_bytes: Optional[Callable[[int], None]],
    stop_event: Optional[threading.Event] = None,
) -> None:
    """Single-stream read/write loop. Writes always land at the current
    end-of-file, so the destination grows naturally with no gap for NTFS to
    zero-fill - this is the safe fallback when we can't preallocate.
    """
    s_h = _create_file(
        src, GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN,
    )
    d_h = _create_file(dst, GENERIC_WRITE, FILE_SHARE_READ, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL)
    try:
        buf = ctypes.create_string_buffer(buffer_size)
        bytes_read = wintypes.DWORD(0)
        bytes_written = wintypes.DWORD(0)
        remaining = size
        while remaining > 0:
            if stop_event is not None and stop_event.is_set():
                raise CopyCancelled(dst)
            to_read = min(buffer_size, remaining)
            if not _k32.ReadFile(s_h, buf, to_read, ctypes.byref(bytes_read), None):
                raise WinIOError(src, "ReadFile")
            n = bytes_read.value
            if n == 0:
                break
            if not _k32.WriteFile(d_h, buf, n, ctypes.byref(bytes_written), None):
                raise WinIOError(dst, "WriteFile")
            remaining -= n
            if on_bytes:
                on_bytes(n)
    finally:
        _close(s_h)
        _close(d_h)


def copy_large_file_parallel(
    src: str,
    dst: str,
    size: int,
    *,
    chunk_workers: int = 8,
    buffer_size: int = DEFAULT_BUFFER_SIZE,
    preallocate: bool = True,
    min_chunk_size: int = DEFAULT_MIN_CHUNK_SIZE,
    on_bytes: Optional[Callable[[int], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> None:
    """Copy a large file by splitting it into byte-range chunks copied by
    separate threads in parallel, each with its own file handles - but only
    when SeManageVolumePrivilege (admin) is available.

    Without that privilege, SetFileValidData can't be called, so extending
    the destination with SetEndOfFile and then writing chunks out of order
    forces NTFS to synchronously zero-fill the skipped region on the first
    write past the old valid-data length - measured in development to be
    dramatically *slower* than a plain sequential copy (worse than robocopy)
    because most of the "parallel" writes end up serialized behind that
    zero-fill. So without the privilege we fall back to a single sequential
    stream instead of chunking - the same shape of copy robocopy itself
    does per file (its /MT only parallelizes *across* files).
    """
    if size == 0:
        h = _create_file(dst, GENERIC_WRITE, FILE_SHARE_READ, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL)
        _close(h)
        return

    can_prealloc = preallocate and has_manage_volume_privilege()
    if not can_prealloc:
        _copy_sequential(src, dst, size, buffer_size, on_bytes, stop_event)
        return

    dst_h = _create_file(
        dst, GENERIC_WRITE, FILE_SHARE_READ, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL
    )
    try:
        if not _k32.SetFilePointerEx(dst_h, ctypes.c_longlong(size), None, FILE_BEGIN):
            raise WinIOError(dst, "SetFilePointerEx")
        if not _k32.SetEndOfFile(dst_h):
            raise WinIOError(dst, "SetEndOfFile")
        try:
            set_file_valid_data(dst_h, size)
        except Exception:
            pass
    finally:
        _close(dst_h)

    n_chunks = max(1, min(chunk_workers, math.ceil(size / min_chunk_size)))
    chunk_size = math.ceil(size / n_chunks)
    ranges = []
    pos = 0
    while pos < size:
        end = min(size, pos + chunk_size)
        ranges.append((pos, end))
        pos = end

    def copy_range(start: int, end: int) -> None:
        s_h = _create_file(
            src, GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN,
        )
        d_h = _create_file(
            dst, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
        )
        try:
            if not _k32.SetFilePointerEx(s_h, ctypes.c_longlong(start), None, FILE_BEGIN):
                raise WinIOError(src, "SetFilePointerEx")
            if not _k32.SetFilePointerEx(d_h, ctypes.c_longlong(start), None, FILE_BEGIN):
                raise WinIOError(dst, "SetFilePointerEx")

            buf = ctypes.create_string_buffer(buffer_size)
            bytes_read = wintypes.DWORD(0)
            bytes_written = wintypes.DWORD(0)
            remaining = end - start
            while remaining > 0:
                if stop_event is not None and stop_event.is_set():
                    raise CopyCancelled(dst)
                to_read = min(buffer_size, remaining)
                if not _k32.ReadFile(s_h, buf, to_read, ctypes.byref(bytes_read), None):
                    raise WinIOError(src, "ReadFile")
                n = bytes_read.value
                if n == 0:
                    break
                if not _k32.WriteFile(d_h, buf, n, ctypes.byref(bytes_written), None):
                    raise WinIOError(dst, "WriteFile")
                remaining -= n
                if on_bytes:
                    on_bytes(n)
        finally:
            _close(s_h)
            _close(d_h)

    with ThreadPoolExecutor(max_workers=len(ranges), thread_name_prefix="fc-chunk") as ex:
        futures = [ex.submit(copy_range, s, e) for s, e in ranges]
        for f in futures:
            f.result()
