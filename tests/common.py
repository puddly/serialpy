"""Shared test utilities and fixtures."""

import asyncio
from collections.abc import AsyncIterator, Iterator
import contextlib
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any

import pytest

import serialx

SOCAT_BINARY = shutil.which("socat")


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
async def async_create_reader_writer(
    port: str | None,
    **kwargs: Any,
) -> AsyncIterator[
    tuple[asyncio.StreamReader, serialx.SerialStreamWriter[serialx.SerialTransport]]
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
        serialx.SerialStreamWriter[serialx.SerialTransport],
        asyncio.StreamReader,
        serialx.SerialStreamWriter[serialx.SerialTransport],
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


@contextlib.asynccontextmanager
async def async_create_dual_loopback(
    left_port: str,
    right_port: str,
    **kwargs: Any,
) -> AsyncIterator[
    tuple[
        asyncio.StreamReader,
        serialx.SerialStreamWriter[serialx.SerialTransport],
        asyncio.StreamReader,
        serialx.SerialStreamWriter[serialx.SerialTransport],
    ]
]:
    """Create reader/writer pairs for dual loopback configuration.

    Returns (reader_left, writer_left, reader_right, writer_right).
    """
    reader_left, writer_left = await serialx.open_serial_connection(left_port, **kwargs)
    reader_right, writer_right = await serialx.open_serial_connection(
        right_port, **kwargs
    )

    try:
        yield (reader_left, writer_left, reader_right, writer_right)
    finally:
        writer_left.close()
        writer_right.close()
        await writer_left.wait_closed()
        await writer_right.wait_closed()


@contextlib.contextmanager
def create_connected_pair(
    **kwargs: Any,
) -> Iterator[tuple[serialx.Serial, serialx.Serial]]:
    """Create a connected pair of serial ports with socat."""
    left_kwargs = {}
    right_kwargs = {}
    shared_kwargs = {}

    for key, value in kwargs.items():
        if key.startswith("left_"):
            left_kwargs[key[5:]] = value
        elif key.startswith("right_"):
            right_kwargs[key[6:]] = value
        else:
            shared_kwargs[key] = value

    with create_socat_pair() as (in_tty, out_tty):
        with (
            serialx.Serial(in_tty, **left_kwargs, **shared_kwargs) as left_serial,
            serialx.Serial(out_tty, **right_kwargs, **shared_kwargs) as right_serial,
        ):
            yield (left_serial, right_serial)


@contextlib.contextmanager
def create_dual_loopback(
    left_port: str,
    right_port: str,
    **kwargs: Any,
) -> Iterator[tuple[serialx.Serial, serialx.Serial]]:
    """Create a connected pair of serial ports with dual loopback hardware.

    Returns (serial_left, serial_right).
    """
    with (
        serialx.Serial(left_port, **kwargs) as serial_left,
        serialx.Serial(right_port, **kwargs) as serial_right,
    ):
        yield (serial_left, serial_right)
