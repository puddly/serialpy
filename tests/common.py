"""Shared test utilities and fixtures."""

import asyncio
from collections.abc import AsyncIterator, Iterator
import contextlib
import os
import subprocess
import tempfile
import time
from typing import Any

import pytest

import serialpy

LOOPBACK_ADAPTER = os.environ.get("SERIALPY_LOOPBACK_PORT")
DUAL_LOOPBACK_LEFT = os.environ.get("SERIALPY_DUAL_LOOPBACK_LEFT")
DUAL_LOOPBACK_RIGHT = os.environ.get("SERIALPY_DUAL_LOOPBACK_RIGHT")


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
) -> AsyncIterator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    """Create a single reader/writer pair."""

    if port is None:
        pytest.skip("No loopback adapter configured")

    reader, writer = await serialpy.open_serial_connection(port, **kwargs)

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
        asyncio.StreamWriter,
        asyncio.StreamReader,
        asyncio.StreamWriter,
    ]
]:
    """Create reader/writer pairs for both sides of a socat connection.

    Returns (reader_left, writer_left, reader_right, writer_right).
    """
    reader_left, writer_left = await serialpy.open_serial_connection(left, **kwargs)
    reader_right, writer_right = await serialpy.open_serial_connection(right, **kwargs)

    try:
        yield (reader_left, writer_left, reader_right, writer_right)
    finally:
        writer_left.close()
        writer_right.close()
        await writer_left.wait_closed()
        await writer_right.wait_closed()


@contextlib.asynccontextmanager
async def async_create_dual_loopback(
    **kwargs: Any,
) -> AsyncIterator[
    tuple[
        asyncio.StreamReader,
        asyncio.StreamWriter,
        asyncio.StreamReader,
        asyncio.StreamWriter,
    ]
]:
    """Create reader/writer pairs for dual loopback configuration.

    Uses DUAL_LOOPBACK_LEFT and DUAL_LOOPBACK_RIGHT environment variables.
    Returns (reader_left, writer_left, reader_right, writer_right).
    """
    if DUAL_LOOPBACK_LEFT is None or DUAL_LOOPBACK_RIGHT is None:
        pytest.skip("Dual loopback ports not configured")

    reader_left, writer_left = await serialpy.open_serial_connection(
        DUAL_LOOPBACK_LEFT, **kwargs
    )
    reader_right, writer_right = await serialpy.open_serial_connection(
        DUAL_LOOPBACK_RIGHT, **kwargs
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
) -> Iterator[tuple[serialpy.Serial, serialpy.Serial]]:
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
            serialpy.Serial(in_tty, **left_kwargs, **shared_kwargs) as left_serial,
            serialpy.Serial(out_tty, **right_kwargs, **shared_kwargs) as right_serial,
        ):
            yield (left_serial, right_serial)
