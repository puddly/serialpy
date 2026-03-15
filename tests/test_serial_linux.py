"""Linux serial port tests."""

import sys

import pytest

if sys.platform != "linux":
    pytest.skip("Linux-only tests", allow_module_level=True)

import asyncio
import contextlib
import errno
import fcntl
import os
import threading
from typing import Any
from unittest.mock import ANY, call, patch

from serialx.platforms.serial_linux import LinuxSerial, LinuxSerialTransport
from tests.common import SerialBackend, SerialPair

TIOCSSERIAL = 0x0000541F
TIOCGSERIAL = 0x0000541E


@patch("serialx.platforms.serial_linux.TIOCGSERIAL", TIOCGSERIAL)
@patch("serialx.platforms.serial_linux.TIOCSSERIAL", TIOCSSERIAL)
def test_tiocgserial_ioctl_not_supported(serial_pair: SerialPair) -> None:
    """Test that TIOCGSERIAL ioctl not supported is handled gracefully."""
    if serial_pair.backends and serial_pair.backends[-1] != SerialBackend.ADAPTER:
        pytest.skip("This test is only relevant for the adapter backend")

    ioctl_orig = fcntl.ioctl

    def ioctl(fd: int, request: int, arg: Any = 0, mutate_flag: bool = True) -> None:
        if request in (TIOCGSERIAL, TIOCSSERIAL):
            raise OSError(errno.EOPNOTSUPP, "Not supported")

        return ioctl_orig(fd, request, arg, mutate_flag)

    with patch(
        "serialx.platforms.serial_linux.fcntl.ioctl", side_effect=ioctl
    ) as mock_ioctl:
        with LinuxSerial(serial_pair.left, baudrate=115200):
            # The serial port still opens
            pass

    assert call(ANY, TIOCGSERIAL, ANY) in mock_ioctl.mock_calls


@patch("serialx.platforms.serial_linux.TIOCGSERIAL", TIOCGSERIAL)
@patch("serialx.platforms.serial_linux.TIOCSSERIAL", TIOCSSERIAL)
def test_tiocgserial_ioctl_unexpected(serial_pair: SerialPair) -> None:
    """Test that TIOCGSERIAL ioctl not supported is handled gracefully."""
    if serial_pair.backends and serial_pair.backends[-1] != SerialBackend.ADAPTER:
        pytest.skip("This test is only relevant for the adapter backend")

    ioctl_orig = fcntl.ioctl

    def ioctl(fd: int, request: int, arg: Any = 0, mutate_flag: bool = True) -> None:
        if request in (TIOCGSERIAL, TIOCSSERIAL):
            raise OSError(errno.EINVAL, "Invalid argument")

        return ioctl_orig(fd, request, arg, mutate_flag)

    with patch(
        "serialx.platforms.serial_linux.fcntl.ioctl", side_effect=ioctl
    ) as mock_ioctl:
        with pytest.raises(OSError, match="Invalid argument"):
            with LinuxSerial(serial_pair.left, baudrate=115200):
                # The serial port will fail to open
                pass

    assert call(ANY, TIOCGSERIAL, ANY) in mock_ioctl.mock_calls


async def test_async_linux_race_condition_connect_close(
    serial_pair: SerialPair,
) -> None:
    """Test that calling `close()` during connection halts a `connection_made` call."""
    if serial_pair.backends and serial_pair.backends[-1] != SerialBackend.ADAPTER:
        pytest.skip("This test is only relevant for the adapter backend")

    started_configuring = threading.Event()
    resume_configuring = threading.Event()

    class SlowConfigureSerial(LinuxSerial):
        def _configure_port(self) -> None:
            started_configuring.set()
            if not resume_configuring.wait(timeout=5.0):
                raise RuntimeError("Timeout waiting for resume signal")

            super()._configure_port()

    class TestTransport(LinuxSerialTransport):
        _serial_cls = SlowConfigureSerial

    class ProbeProtocol(asyncio.Protocol):
        def __init__(self) -> None:
            self.connection_made_calls = 0

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            assert isinstance(transport, LinuxSerialTransport)
            self.connection_made_calls += 1

    loop = asyncio.get_running_loop()
    protocol = ProbeProtocol()
    transport = TestTransport(loop, protocol)

    # Start connection
    connect_task = asyncio.create_task(
        transport.connect(path=serial_pair.left, baudrate=115200)
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


async def test_async_linux_wait_closed_when_close_task_cancelled(
    serial_pair: SerialPair,
) -> None:
    """wait_closed should resolve even if close task is cancelled before start."""
    if serial_pair.backends and serial_pair.backends[-1] != SerialBackend.ADAPTER:
        pytest.skip("This test is only relevant for the adapter backend")

    loop = asyncio.get_running_loop()
    transport = LinuxSerialTransport(loop, asyncio.Protocol())

    await transport.connect(path=serial_pair.left, baudrate=115200)
    transport.close()

    close_task = transport._close_task
    assert close_task is not None
    close_task.cancel()
    await asyncio.sleep(0)

    await transport.wait_closed()

    # Clean up if the fd wasn't closed
    if transport._fileno is not None:
        os.close(transport._fileno)


async def test_async_linux_wait_closed_when_connection_lost_raises(
    serial_pair: SerialPair,
) -> None:
    """wait_closed should resolve even if protocol.connection_lost raises."""
    if serial_pair.backends and serial_pair.backends[-1] != SerialBackend.ADAPTER:
        pytest.skip("This test is only relevant for the adapter backend")

    class RaisingProtocol(asyncio.Protocol):
        def connection_lost(self, exc: Exception | None) -> None:
            raise RuntimeError("boom")

    loop = asyncio.get_running_loop()
    transport = LinuxSerialTransport(loop, RaisingProtocol())

    await transport.connect(path=serial_pair.left, baudrate=115200)
    transport.close()

    await transport.wait_closed()
    assert transport._fileno is None


async def test_async_linux_close_clears_fileno_when_fd_already_closed(
    serial_pair: SerialPair,
) -> None:
    """Close should clear fileno even if fd was externally closed."""
    if serial_pair.backends and serial_pair.backends[-1] != SerialBackend.ADAPTER:
        pytest.skip("This test is only relevant for the adapter backend")

    loop = asyncio.get_running_loop()
    transport = LinuxSerialTransport(loop, asyncio.Protocol())

    await transport.connect(path=serial_pair.left, baudrate=115200)

    assert transport._fileno is not None
    os.close(transport._fileno)

    transport.close()
    await transport.wait_closed()

    assert transport._fileno is None
