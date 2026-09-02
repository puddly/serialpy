"""Tests for the Win32 serial port implementation."""

from __future__ import annotations

from collections.abc import Iterator
import errno
import sys
from unittest.mock import patch

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only tests", allow_module_level=True)

import pywintypes
import win32event
import win32file
from win32file import OVERLAPPED
from winerror import ERROR_INVALID_HANDLE

from serialx.platforms import _win32api, serial_win32
from serialx.platforms.serial_win32 import Win32Serial

INVALID_HANDLE = -1
"""A handle no Win32 call will accept, so a call through it fails for real."""

DEVICE_GONE = pywintypes.error(
    22, "EscapeCommFunction", "The device does not recognize the command."
)
"""What Win32 answers once the device behind an open handle has been removed."""

PYWIN32_MODULES = (win32event, win32file)
"""The modules `_win32api` stands in front of."""

NOT_A_WIN32_CALL = frozenset({"OVERLAPPED"})
"""Names pulled straight from pywin32 that reach no API and so cannot fail."""


@pytest.fixture
def port() -> Iterator[Win32Serial]:
    """Build a port object around a handle, without opening anything.

    The Win32 calls under test are patched out, so no real device is needed and
    these run on a machine with no serial ports at all. The handle is dropped
    again afterwards so that nothing tries to close it for real.
    """
    # Built the ordinary way and never opened, so it is a real object with a
    # stand-in handle rather than a half-initialised one.
    detached = Win32Serial()
    detached._handle = 1  # noqa: SLF001

    try:
        yield detached
    finally:
        detached._handle = None  # noqa: SLF001


def test_every_win32_entry_point_is_translated() -> None:
    """`serial_win32` reaches Win32 only through the translating wrappers.

    This is what makes the translation hold: no call site has to remember to
    catch `pywintypes.error`, because the name it calls cannot raise one. A
    Win32 call imported straight from pywin32 -- a new one, or one moved back
    off `_win32api` -- fails here rather than quietly reintroducing an
    exception that `except OSError` cannot catch.
    """
    direct = {
        name
        for name, value in vars(serial_win32).items()
        # Constants are compared out first: they cannot fail, and small ints
        # are identical to the pywin32 ones by interning alone.
        if callable(value)
        and name not in NOT_A_WIN32_CALL
        and any(value is getattr(mod, name, None) for mod in PYWIN32_MODULES)
    }

    assert not direct, (
        f"{sorted(direct)} are imported straight from pywin32 and still raise"
        f" pywintypes.error; add them to _win32api and import them from there"
    )


def test_translated_calls_raise_os_error() -> None:
    """A failing Win32 call raises `OSError`, not `pywintypes.error`.

    `pywintypes.error` does not derive from `OSError`, so letting it out means
    it passes through every `except OSError` a caller has written, including
    the one in `_close`.
    """

    def fail(_handle: int) -> None:
        raise DEVICE_GONE

    with pytest.raises(OSError) as raised:  # noqa: PT011
        _win32api._translated(fail)(INVALID_HANDLE)  # noqa: SLF001

    # The Win32 code stays in `winerror`, where Windows callers look for it,
    # rather than being passed off as an `errno`.
    assert raised.value.winerror == 22
    assert raised.value.strerror == "The device does not recognize the command."
    assert raised.value.__cause__ is DEVICE_GONE


@pytest.mark.parametrize("name", ["GetCommModemStatus", "ClearCommError"])
def test_real_win32_failure_raises_os_error(name: str) -> None:
    """The translation holds against a genuine pywin32 failure, not a stand-in.

    An invalid handle makes the call fail the way Windows really fails it, so
    this covers the actual `pywintypes.error` pywin32 raises.
    """
    call = getattr(_win32api, name)

    with pytest.raises(OSError) as raised:  # noqa: PT011
        call(INVALID_HANDLE)

    assert not isinstance(raised.value, pywintypes.error)
    assert raised.value.winerror == ERROR_INVALID_HANDLE
    assert raised.value.errno == errno.EBADF


def test_close_survives_a_device_that_has_been_removed(
    port: Win32Serial,
) -> None:
    """Closing a port whose device is gone does not raise.

    `_close` sets the modem pins on the way out, because Windows will not do it
    on close. A device that has been unplugged answers that call with "the
    device does not recognize the command", and `_close` is written to tolerate
    it -- but its `except OSError` never matched what was actually raised, so
    closing a removed device raised out of `close()`. Under asyncio that
    surfaces as a complaint from the executor thread rather than as something
    the caller can catch.
    """
    closed: list[int] = []

    def fail(*args: object, **kwargs: object) -> None:
        # What `_win32api` turns the unplugged device's answer into, which is
        # what `_close` actually has to cope with.
        raise OSError(None, DEVICE_GONE.strerror, None, DEVICE_GONE.winerror)

    with (
        patch.object(serial_win32, "EscapeCommFunction", fail),
        patch.object(serial_win32, "CancelIo", fail),
        patch.object(serial_win32, "CloseHandle", closed.append),
    ):
        port._close()  # noqa: SLF001

    assert closed == [1], "the handle must still be released"
    assert port._handle is None  # noqa: SLF001


def test_close_releases_the_overlapped_events(port: Win32Serial) -> None:
    """`_close` also releases the two overlapped-IO event handles."""
    port._overlapped_read = OVERLAPPED()  # noqa: SLF001
    port._overlapped_read.hEvent = 2  # noqa: SLF001
    port._overlapped_write = OVERLAPPED()  # noqa: SLF001
    port._overlapped_write.hEvent = 3  # noqa: SLF001

    closed: list[int] = []

    with (
        patch.object(serial_win32, "EscapeCommFunction", lambda *a, **kw: None),
        patch.object(serial_win32, "CancelIo", lambda *a, **kw: None),
        patch.object(serial_win32, "CloseHandle", closed.append),
    ):
        port._close()  # noqa: SLF001

    assert closed == [1, 2, 3]
    assert port._overlapped_read is None  # noqa: SLF001
    assert port._overlapped_write is None  # noqa: SLF001
