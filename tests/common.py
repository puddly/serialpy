"""Shared test utilities and fixtures."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
import contextlib
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any, NamedTuple

import pytest

import serialx
from serialx.common import BaseSerialTransport

SOCAT_BINARY = shutil.which("socat")


class SerialPair(NamedTuple):
    """A connected pair of serial port paths with backend metadata."""

    left: str
    right: str
    backend: str  # "socat", "socket", or "adapter"


class BridgedSocatPair(NamedTuple):
    """A bridged socat pair where killing one side propagates EOF to the other."""

    left: str
    right: str
    left_process: asyncio.subprocess.Process
    right_process: asyncio.subprocess.Process


@contextlib.contextmanager
def create_socat_pair() -> Iterator[tuple[str, str]]:
    """Create a pair of virtual PTYs using socat (synchronous)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        in_tty = os.path.join(tmpdir, "ttyTestIn")
        out_tty = os.path.join(tmpdir, "ttyTestOut")

        proc = subprocess.Popen(
            [
                "socat",
                f"PTY,link={in_tty},raw,echo=0",
                f"PTY,link={out_tty},raw,echo=0",
            ],
            stderr=subprocess.DEVNULL,
        )

        # Give socat time to set up the PTYs
        for _attempt in range(100):
            if os.path.exists(in_tty) and os.path.exists(out_tty):
                break

            time.sleep(0.01)
        else:
            raise RuntimeError("socat PTYs were not created in time")

        assert proc.returncode is None

        yield (in_tty, out_tty)

        proc.terminate()
        proc.wait()


@contextlib.asynccontextmanager
async def async_create_socat_pair() -> AsyncIterator[tuple[str, str]]:
    """Create a pair of virtual PTYs using socat (asynchronous)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        in_tty = os.path.join(tmpdir, "ttyTestIn")
        out_tty = os.path.join(tmpdir, "ttyTestOut")

        proc = await asyncio.create_subprocess_exec(
            "socat",
            f"PTY,link={in_tty},raw,echo=0",
            f"PTY,link={out_tty},raw,echo=0",
            stderr=asyncio.subprocess.DEVNULL,
        )

        # Give socat time to set up the PTYs
        await asyncio.sleep(0.5)

        assert proc.returncode is None

        yield (in_tty, out_tty)

        proc.terminate()
        await proc.wait()


@contextlib.asynccontextmanager
async def async_create_bridged_socat_pair() -> AsyncIterator[BridgedSocatPair]:
    """Create a pair of PTYs bridged via two socat processes over a Unix socket.

    Unlike `async_create_socat_pair`, killing one socat process propagates
    through the bridge and tears down the other side, triggering a real EOF.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        left_tty = os.path.join(tmpdir, "ttyLeft")
        right_tty = os.path.join(tmpdir, "ttyRight")
        sock_path = os.path.join(tmpdir, "bridge.sock")

        listener = await asyncio.create_subprocess_exec(
            "socat",
            f"PTY,link={left_tty},raw,echo=0",
            f"UNIX-LISTEN:{sock_path}",
            stderr=asyncio.subprocess.DEVNULL,
        )

        # Wait for the socket to appear
        for _ in range(100):
            if os.path.exists(sock_path):
                break
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("socat listener socket was not created in time")

        connector = await asyncio.create_subprocess_exec(
            "socat",
            f"PTY,link={right_tty},raw,echo=0",
            f"UNIX-CONNECT:{sock_path}",
            stderr=asyncio.subprocess.DEVNULL,
        )

        # Wait for both PTYs to appear
        for _ in range(100):
            if os.path.exists(left_tty) and os.path.exists(right_tty):
                break
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("socat PTYs were not created in time")

        assert listener.returncode is None
        assert connector.returncode is None

        yield BridgedSocatPair(
            left=left_tty,
            right=right_tty,
            left_process=listener,
            right_process=connector,
        )

        if connector.returncode is None:
            connector.terminate()
            await connector.wait()
        if listener.returncode is None:
            listener.terminate()
            await listener.wait()


@contextlib.asynccontextmanager
async def async_create_reader_writer(
    port: str | None,
    **kwargs: Any,
) -> AsyncIterator[
    tuple[asyncio.StreamReader, serialx.SerialStreamWriter[BaseSerialTransport]]
]:
    """Create a single reader/writer pair."""

    if port is None:
        pytest.skip("No loopback adapter configured")

    reader, writer = await serialx.open_serial_connection(port, **kwargs)

    try:
        yield (reader, writer)
    finally:
        writer.close()
        await writer.wait_closed()


@contextlib.asynccontextmanager
async def async_create_reader_writer_pair(
    left: str,
    right: str,
    **kwargs: Any,
) -> AsyncIterator[
    tuple[
        asyncio.StreamReader,
        serialx.SerialStreamWriter[BaseSerialTransport],
        asyncio.StreamReader,
        serialx.SerialStreamWriter[BaseSerialTransport],
    ]
]:
    """Create reader/writer pairs for both sides of a socat connection.

    Returns (reader_left, writer_left, reader_right, writer_right).
    """
    reader_left, writer_left = await serialx.open_serial_connection(left, **kwargs)
    reader_right, writer_right = await serialx.open_serial_connection(right, **kwargs)

    try:
        yield (reader_left, writer_left, reader_right, writer_right)
    finally:
        writer_left.close()
        writer_right.close()
        await writer_left.wait_closed()
        await writer_right.wait_closed()


@contextlib.contextmanager
def measure_time() -> Iterator[Callable[[], float]]:
    """Measure elapsed time in a context."""
    start = time.monotonic()
    end = None

    def get_result() -> float:
        if end is None:
            raise RuntimeError("Context has not exited yet")

        return end - start

    try:
        yield get_result
    finally:
        end = time.monotonic()
