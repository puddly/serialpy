"""Linux serial port tests."""

import sys

import pytest

if sys.platform != "linux":
    pytest.skip("Linux-only tests", allow_module_level=True)

import errno
import fcntl
from typing import Any
from unittest.mock import ANY, call, patch

from serialx.platforms.serial_posix import PosixSerial
from tests.common import create_socat_pair

TIOCSSERIAL = 0x0000541F
TIOCGSERIAL = 0x0000541E


@patch("serialx.platforms.serial_posix.TIOCGSERIAL", TIOCGSERIAL)
@patch("serialx.platforms.serial_posix.TIOCSSERIAL", TIOCSSERIAL)
def test_tiocgserial_ioctl_not_supported() -> None:
    """Test that TIOCGSERIAL ioctl not supported is handled gracefully."""
    ioctl_orig = fcntl.ioctl

    def ioctl(fd: int, request: int, arg: Any = 0, mutate_flag: bool = True) -> None:
        if request in (TIOCGSERIAL, TIOCSSERIAL):
            raise OSError(errno.EOPNOTSUPP, "Not supported")

        return ioctl_orig(fd, request, arg, mutate_flag)

    with patch(
        "serialx.platforms.serial_posix.fcntl.ioctl", side_effect=ioctl
    ) as mock_ioctl:
        with create_socat_pair() as (left, _right):
            with PosixSerial(left, baudrate=115200):
                # The serial port still opens
                pass

    assert call(ANY, TIOCGSERIAL, ANY) in mock_ioctl.mock_calls


@patch("serialx.platforms.serial_posix.TIOCGSERIAL", TIOCGSERIAL)
@patch("serialx.platforms.serial_posix.TIOCSSERIAL", TIOCSSERIAL)
def test_tiocgserial_ioctl_unexpected() -> None:
    """Test that TIOCGSERIAL ioctl not supported is handled gracefully."""
    ioctl_orig = fcntl.ioctl

    def ioctl(fd: int, request: int, arg: Any = 0, mutate_flag: bool = True) -> None:
        if request in (TIOCGSERIAL, TIOCSSERIAL):
            raise OSError(errno.EINVAL, "Invalid argument")

        return ioctl_orig(fd, request, arg, mutate_flag)

    with patch(
        "serialx.platforms.serial_posix.fcntl.ioctl", side_effect=ioctl
    ) as mock_ioctl:
        with create_socat_pair() as (left, _right):
            with pytest.raises(OSError, match="Invalid argument"):
                with PosixSerial(left, baudrate=115200):
                    # The serial port will fail to open
                    pass

    assert call(ANY, TIOCGSERIAL, ANY) in mock_ioctl.mock_calls
