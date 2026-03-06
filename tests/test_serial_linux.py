"""Linux serial port tests."""

import sys

import pytest

if sys.platform != "linux":
    pytest.skip("Linux-only tests", allow_module_level=True)

import asyncio
import contextlib
import errno
import fcntl
import threading
from typing import Any
from unittest.mock import ANY, call, patch

from serialx.platforms.serial_posix import PosixSerial, PosixSerialTransport
from tests.common import async_create_socat_pair, create_socat_pair

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


async def test_async_linux_race_condition_connect_close() -> None:
    """Test that calling `close()` during connection halts a `connection_made` call."""
    started_configuring = threading.Event()
    resume_configuring = threading.Event()

    class SlowConfigureSerial(PosixSerial):
        def _configure_port(self) -> None:
            started_configuring.set()
            if not resume_configuring.wait(timeout=5.0):
                raise RuntimeError("Timeout waiting for resume signal")

            super()._configure_port()

    class TestTransport(PosixSerialTransport):
        _serial_cls = SlowConfigureSerial

    class ProbeProtocol(asyncio.Protocol):
        def __init__(self) -> None:
            self.connection_made_calls = 0

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            assert isinstance(transport, PosixSerialTransport)
            self.connection_made_calls += 1

    loop = asyncio.get_running_loop()
    protocol = ProbeProtocol()
    transport = TestTransport(loop, protocol)

    async with async_create_socat_pair() as (left_path, _right_path):
        # Start connection
        connect_task = asyncio.create_task(
            transport.connect(path=left_path, baudrate=115200)
        )

        await loop.run_in_executor(None, started_configuring.wait, 5.0)
        if not started_configuring.is_set():
            pytest.fail("configure_port was not called in time")

        assert protocol.connection_made_calls == 0

        # Close the transport while it is connecting
        transport.close()

        # Signal the thread to finish configure_port
        resume_configuring.set()

        # Wait for connect_task to finish
        with contextlib.suppress(Exception):
            await connect_task

        # Wait for the transport to fully close before the socat pair is torn
        # down, preventing fd reuse races with the socat pidfd.
        await transport.wait_closed()

        # connection_made was never called
        assert protocol.connection_made_calls == 0
        assert transport.is_closing()
