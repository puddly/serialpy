"""Shared test utilities and fixtures."""

import asyncio
from collections.abc import AsyncIterator, Iterator
import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

import pytest

import serialx
from serialx.common import BaseSerialTransport

SOCAT_BINARY = shutil.which("socat")

if sys.platform == "win32":
    COM0COM_SETUPC = shutil.which("setupc") or (
        "C:\\Program Files (x86)\\com0com\\setupc.exe"
        if os.path.exists("C:\\Program Files (x86)\\com0com\\setupc.exe")
        else None
    )
else:
    COM0COM_SETUPC = None


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


def _parse_com0com_install_output(output: str) -> tuple[int, str, str]:
    """Parse setupc.exe install output to extract pair index and device names.

    Example output lines:
        CNCA1 PortName=-,EmuBR=yes,cts=rdtr
        CNCB1 PortName=-,EmuBR=yes,cts=rdtr
    """
    port_pattern = re.compile(r"(CNC[AB])(\d+)\s+PortName=")
    matches = port_pattern.findall(output)

    assert len(matches) == 2, f"Expected 2 port matches, got {len(matches)}: {output!r}"

    prefix_a, index_a = matches[0]
    prefix_b, index_b = matches[1]
    assert index_a == index_b

    return int(index_a), f"{prefix_a}{index_a}", f"{prefix_b}{index_b}"


@contextlib.contextmanager
def create_com0com_pair() -> Iterator[tuple[str, str]]:
    """Create a pair of virtual COM ports using com0com (synchronous)."""
    assert COM0COM_SETUPC is not None
    setupc_dir = os.path.dirname(COM0COM_SETUPC)

    result = subprocess.run(
        [
            COM0COM_SETUPC,
            "install",
            "PortName=-,EmuBR=yes,cts=rdtr",
            "PortName=-,EmuBR=yes,cts=rdtr",
        ],
        check=True,
        cwd=setupc_dir,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"setupc install failed: {result.stdout}\n{result.stderr}"
    )

    pair_index, left_port, right_port = _parse_com0com_install_output(result.stdout)

    try:
        yield (left_port, right_port)
    finally:
        subprocess.run(
            [COM0COM_SETUPC, "remove", str(pair_index)],
            check=True,
            cwd=setupc_dir,
            capture_output=True,
        )


@contextlib.asynccontextmanager
async def async_create_com0com_pair() -> AsyncIterator[tuple[str, str]]:
    """Create a pair of virtual COM ports using com0com (asynchronous)."""
    assert COM0COM_SETUPC is not None
    setupc_dir = os.path.dirname(COM0COM_SETUPC)

    proc = await asyncio.create_subprocess_exec(
        COM0COM_SETUPC,
        "install",
        "PortName=-,EmuBR=yes,cts=rdtr",
        "PortName=-,EmuBR=yes,cts=rdtr",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=setupc_dir,
    )

    stdout, stderr = await proc.communicate()

    assert proc.returncode == 0, (
        f"setupc install failed: {stdout.decode()}\n{stderr.decode()}"
    )

    pair_index, left_port, right_port = _parse_com0com_install_output(stdout.decode())

    try:
        yield (left_port, right_port)
    finally:
        remove_proc = await asyncio.create_subprocess_exec(
            COM0COM_SETUPC,
            "remove",
            str(pair_index),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=setupc_dir,
        )
        await remove_proc.communicate()


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


@contextlib.asynccontextmanager
async def async_create_dual_loopback(
    left_port: str,
    right_port: str,
    **kwargs: Any,
) -> AsyncIterator[
    tuple[
        asyncio.StreamReader,
        serialx.SerialStreamWriter[BaseSerialTransport],
        asyncio.StreamReader,
        serialx.SerialStreamWriter[BaseSerialTransport],
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
