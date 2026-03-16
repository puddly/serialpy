"""Shared test utilities and fixtures."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import IO, Any, NamedTuple

import psutil
import pytest

import serialx
from serialx.common import BaseSerialTransport

SOCAT_BINARY = shutil.which("socat")
ESPHOME_HOST_BINARY = shutil.which(
    "program",
    path=(
        Path(__file__).resolve().parent
        / "esphome"
        / ".esphome"
        / "build"
        / "serialx-host-daemon"
        / ".pioenvs"
        / "serialx-host-daemon"
    ),
)


class SerialPair(NamedTuple):
    """A connected pair of serial port paths with backend metadata."""

    left: str
    right: str
    backend: str  # "socat", "socket", "esphome", "adapter", or "com0com"
    serial_class: str = serialx.Serial.__name__


class BridgedSocatPair(NamedTuple):
    """A bridged socat pair where killing one side propagates EOF to the other."""

    left: str
    right: str
    left_process: asyncio.subprocess.Process
    right_process: asyncio.subprocess.Process


def _get_listening_ports(pid: int) -> list[int]:
    """Get the TCP ports a process is listening on, via psutil."""
    return sorted(
        c.laddr.port
        for c in psutil.Process(pid).net_connections(kind="tcp")
        if c.status == psutil.CONN_LISTEN
    )


def _wait_for_ready(
    process: subprocess.Popen[Any],
    stream: IO[bytes] | None,
    marker: str,
    name: str,
) -> None:
    """Wait for a process to print a ready marker to stdout or stderr."""
    assert stream is not None

    marker_bytes = marker.encode()
    output = bytearray()

    while True:
        line = stream.readline()

        if not line:
            raise RuntimeError(
                f"{name} exited before ready (code={process.returncode})"
                f"\n{stream}: {output.decode(errors='replace')}"
            )

        output.extend(line)

        if marker_bytes in line:
            return


@contextlib.contextmanager
def create_esphome_pair() -> Iterator[tuple[str, str]]:
    """Create an esphome:// pair."""
    assert ESPHOME_HOST_BINARY is not None

    with create_socat_pair() as (left_tty, right_tty):
        env = os.environ.copy()
        env["SERIALX_UART_LEFT"] = left_tty
        env["SERIALX_UART_RIGHT"] = right_tty
        env["SERIALX_API_PORT"] = "0"

        process = subprocess.Popen(  # noqa: S603
            [ESPHOME_HOST_BINARY],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        try:
            _wait_for_ready(
                process,
                stream=process.stderr,
                marker="Ready",
                name="ESPHome host daemon",
            )

            api_port = _get_listening_ports(process.pid)[0]

            yield (
                f"esphome://127.0.0.1:{api_port}/0",
                f"esphome://127.0.0.1:{api_port}/1",
            )
        finally:
            if process.poll() is None:
                process.terminate()

                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


@contextlib.contextmanager
def create_socat_pair() -> Iterator[tuple[str, str]]:
    """Create a bridged pair of virtual PTYs using two socat processes.

    Each PTY is managed by its own socat process, linked via a UNIX socket.
    Killing one socat process closes its PTY and propagates EOF to the other.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        left_tty = os.path.join(tmpdir, "ttyLeft")
        right_tty = os.path.join(tmpdir, "ttyRight")
        bridge = os.path.join(tmpdir, "bridge.sock")

        # Start the right side first (UNIX-LISTEN), then the left (UNIX-CONNECT)
        right_proc = subprocess.Popen(
            [
                "socat",
                "-d",
                "-d",
                f"PTY,link={right_tty},raw,echo=0",
                f"UNIX-LISTEN:{bridge}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        _wait_for_ready(
            right_proc,
            marker="listening on",
            stream=right_proc.stderr,
            name="socat(right)",
        )

        left_proc = subprocess.Popen(
            [
                "socat",
                "-d",
                "-d",
                f"PTY,link={left_tty},raw,echo=0",
                f"UNIX-CONNECT:{bridge}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        _wait_for_ready(
            left_proc,
            marker="starting data transfer loop",
            stream=left_proc.stderr,
            name="socat(left)",
        )

        try:
            yield (left_tty, right_tty)
        finally:
            for proc in (left_proc, right_proc):
                if proc.returncode is None:
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
