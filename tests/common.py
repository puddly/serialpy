"""Shared test utilities and fixtures."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
import contextlib
import enum
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any, NamedTuple

import pytest

import serialx
from serialx.common import BaseSerialTransport

SOCAT_BINARY = shutil.which("socat")
SER2NET_BINARY = shutil.which("ser2net")

_SERIALX_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ESPHOME_HOST_DAEMON_PROGRAM = (
    _SERIALX_ROOT
    / "tests"
    / "esphome"
    / ".esphome"
    / "build"
    / "serialx-host-daemon"
    / ".pioenvs"
    / "serialx-host-daemon"
    / "program"
)


class SerialPairBackend(str, enum.Enum):
    """Known serial-pair backend families used by the test suite."""

    SOCAT = "socat"
    SOCKET = "socket"
    ESPHOME = "esphome"
    ADAPTER = "adapter"
    COM0COM = "com0com"
    TTY0TTY = "tty0tty"
    RFC2217 = "rfc2217"


class SerialPairQuirk(str, enum.Enum):
    """Quirks carried by a generated serial test pair."""

    NO_PIN_READBACK = "no-pin-readback"
    NO_DTR_CTS = "no-dtr-cts"
    NO_FLOW_CONTROL = "no-flow-control"
    NO_NUM_UNREAD_BYTES = "no-num-unread-bytes"
    NO_RESET_READ_BUFFER = "no-reset-read-buffer"
    NO_NUM_UNWRITTEN_BYTES = "no-num-unwritten-bytes"
    NO_RESET_WRITE_BUFFER = "no-reset-write-buffer"
    NO_WRITE_TIMEOUT = "no-write-timeout"
    NO_PAUSE_READING = "no-pause-reading"
    NO_WRITE_LIMITS = "no-write-limits"
    NO_PAUSE_WRITING_CALLBACKS = "no-pause-writing-callbacks"


class SerialPair(NamedTuple):
    """A connected pair of serial port paths with backend metadata."""

    left: str
    right: str
    left_backend: SerialPairBackend
    right_backend: SerialPairBackend
    serial_class: str = serialx.Serial.__name__
    quirks: frozenset[SerialPairQuirk] = frozenset()
    spawned_ser2net: bool = False

    @property
    def backends(self) -> frozenset[SerialPairBackend]:
        """Return the distinct backend families used by this endpoint pair."""
        return frozenset({self.left_backend, self.right_backend})


class BridgedSocatPair(NamedTuple):
    """A bridged socat pair where killing one side propagates EOF to the other."""

    left: str
    right: str
    left_process: asyncio.subprocess.Process
    right_process: asyncio.subprocess.Process


def get_esphome_host_daemon_program() -> str | None:
    """Get the compiled ESPHome host daemon program path, if available."""
    if override := os.getenv("SERIALX_ESPHOME_DAEMON_PROGRAM"):
        program_path = Path(override).expanduser()
    else:
        program_path = _DEFAULT_ESPHOME_HOST_DAEMON_PROGRAM

    if not program_path.exists():
        return None
    if not os.access(program_path, os.X_OK):
        return None

    return str(program_path)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_esphome_listener(
    process: subprocess.Popen[Any], port: int, timeout: float = 5.0
) -> None:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"ESPHome host daemon exited before listening (code={process.returncode})"
            )

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return

        time.sleep(0.01)

    raise RuntimeError(
        f"ESPHome host daemon did not start listening on 127.0.0.1:{port}"
    )


@contextlib.contextmanager
def create_esphome_pair(program_path: str) -> Iterator[tuple[str, str]]:
    """Create an esphome:// pair backed by a socat PTY pair and host daemon."""
    with create_socat_pair() as (left_tty, right_tty):
        api_port = _pick_free_port()
        env = os.environ.copy()
        env["SERIALX_UART_LEFT"] = left_tty
        env["SERIALX_UART_RIGHT"] = right_tty
        env["SERIALX_API_PORT"] = str(api_port)

        process = subprocess.Popen(  # noqa: S603
            [program_path],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            _wait_for_esphome_listener(process, api_port)
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

        try:
            yield (in_tty, out_tty)
        finally:
            proc.terminate()
            proc.wait()


@contextlib.contextmanager
def create_ser2net_pair(
    left_adapter: str, right_adapter: str
) -> Iterator[tuple[str, str]]:
    """Create a pair of independent RFC2217 sockets using ser2net."""

    left_port = _pick_free_port()
    right_port = _pick_free_port()

    config = {
        "connections": {
            "left_adapter": {
                "accepter": f"telnet(rfc2217),tcp,{left_port}",
                "connector": f"serialdev(),{left_adapter},speed=115200n81",
            },
            "right_adapter": {
                "accepter": f"telnet(rfc2217),tcp,127.0.0.1,{right_port}",
                "connector": f"serialdev(),{right_adapter},speed=115200n81",
            },
        }
    }

    proc = subprocess.Popen(
        [
            "ser2net",
            "-n",
            "-Y",
            json.dumps(config),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    time.sleep(0.1)

    assert proc.returncode is None

    try:
        yield (
            f"rfc2217://127.0.0.1:{left_port}",
            f"rfc2217://127.0.0.1:{right_port}",
        )
    finally:
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
