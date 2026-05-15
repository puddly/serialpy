"""Windows kernel-handle enumeration for the test fd-leak detector.

Pure ctypes wrapper around `NtQuerySystemInformation` +
`NtQueryObject` so the test suite can identify leaked `File`-typed handles
(what `CreateFile`/COM-port opens produce). Keeping this isolated from the
library code: it's only useful for tests.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
_kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]

_SYSTEM_EXTENDED_HANDLE_INFORMATION = 64
_OBJECT_TYPE_INFORMATION = 2
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004


class _SystemHandleEntryEx(ctypes.Structure):
    _fields_ = (
        ("Object", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_void_p),
        ("HandleValue", ctypes.c_void_p),
        ("GrantedAccess", ctypes.c_ulong),
        ("CreatorBackTraceIndex", ctypes.c_ushort),
        ("ObjectTypeIndex", ctypes.c_ushort),
        ("HandleAttributes", ctypes.c_ulong),
        ("Reserved", ctypes.c_ulong),
    )


class _UnicodeString(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    )


def _query_object_type(handle: int) -> str:
    # `ObjectTypeInformation` is safe; `ObjectNameInformation` deadlocks the
    # caller on certain synchronous handle classes (e.g. anonymous pipes).
    buf = (ctypes.c_byte * 0x400)()
    ret_len = ctypes.c_ulong(0)
    status = _ntdll.NtQueryObject(
        wintypes.HANDLE(handle),
        _OBJECT_TYPE_INFORMATION,
        buf,
        len(buf),
        ctypes.byref(ret_len),
    )
    if status != 0:
        return ""
    us = ctypes.cast(buf, ctypes.POINTER(_UnicodeString))[0]
    if us.Length == 0 or not us.Buffer:
        return ""
    return ctypes.wstring_at(us.Buffer, us.Length // 2)


def snapshot_file_handles() -> set[int]:
    """Return the set of `File`-typed kernel handle values open in this process."""
    pid = _kernel32.GetCurrentProcessId()
    buf_size = 0x10000
    while True:
        buf = (ctypes.c_byte * buf_size)()
        ret_len = ctypes.c_ulong(0)
        status = _ntdll.NtQuerySystemInformation(
            _SYSTEM_EXTENDED_HANDLE_INFORMATION,
            buf,
            buf_size,
            ctypes.byref(ret_len),
        )
        if status == 0:
            break
        if status & 0xFFFFFFFF == _STATUS_INFO_LENGTH_MISMATCH:
            buf_size *= 2
            continue
        raise OSError(f"NtQuerySystemInformation failed: 0x{status & 0xFFFFFFFF:08x}")

    n = ctypes.cast(buf, ctypes.POINTER(ctypes.c_size_t))[0]
    entries = ctypes.cast(
        ctypes.byref(buf, ctypes.sizeof(ctypes.c_size_t) * 2),
        ctypes.POINTER(_SystemHandleEntryEx * n),
    )[0]

    out: set[int] = set()
    for i in range(n):
        e = entries[i]
        if e.UniqueProcessId != pid:
            continue
        h = int(e.HandleValue)
        if _query_object_type(h) == "File":
            out.add(h)
    return out
