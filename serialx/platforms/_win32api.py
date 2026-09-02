"""Win32 API calls that fail with `OSError` instead of `pywintypes.error`.

`pywintypes.error` derives from `Exception`, not from `OSError`, so it slips
through any `except OSError` a caller has written -- including the ones this
package writes around its own teardown paths. Translating it at each call site
means every new call has to remember to do it; translating it here means no
call site can forget.

`serial_win32` imports its Win32 entry points from this module rather than from
`win32event`/`win32file` directly. Signatures are preserved, so the typeshed
stubs still apply at the call site.
"""

from __future__ import annotations

from collections.abc import Callable
import functools
from typing import TypeVar

import pywintypes
from typing_extensions import ParamSpec
import win32event
import win32file

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _translated(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    """Wrap a pywin32 call so that it raises `OSError`."""

    # Only the identifying attributes are copied. The default set includes
    # `__type_params__`, which is not a tuple on the stand-ins Sphinx installs
    # for pywin32 when it builds the docs off-Windows, and copying it there
    # fails the import.
    @functools.wraps(fn, assigned=("__module__", "__name__", "__qualname__", "__doc__"))
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return fn(*args, **kwargs)
        except pywintypes.error as e:
            # Letting `OSError` map the Win32 code itself keeps `winerror`
            # intact and picks the matching `errno`, rather than passing the
            # Win32 code off as one.
            raise OSError(None, e.strerror, None, e.winerror) from e

    return wrapper


CreateEvent = _translated(win32event.CreateEvent)
ResetEvent = _translated(win32event.ResetEvent)
WaitForSingleObject = _translated(win32event.WaitForSingleObject)

CancelIo = _translated(win32file.CancelIo)
ClearCommError = _translated(win32file.ClearCommError)
CloseHandle = _translated(win32file.CloseHandle)
CreateFile = _translated(win32file.CreateFile)
EscapeCommFunction = _translated(win32file.EscapeCommFunction)
FlushFileBuffers = _translated(win32file.FlushFileBuffers)
GetCommModemStatus = _translated(win32file.GetCommModemStatus)
GetCommState = _translated(win32file.GetCommState)
GetOverlappedResult = _translated(win32file.GetOverlappedResult)
PurgeComm = _translated(win32file.PurgeComm)
ReadFile = _translated(win32file.ReadFile)
SetCommState = _translated(win32file.SetCommState)
SetCommTimeouts = _translated(win32file.SetCommTimeouts)
SetupComm = _translated(win32file.SetupComm)
WriteFile = _translated(win32file.WriteFile)
