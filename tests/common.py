"""Shared test utilities and fixtures."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
import contextlib
import dataclasses
import enum
import errno
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import IO, Any

import psutil
from typing_extensions import Self

import serialx
from serialx.common import BaseSerialTransport

SOCAT_BINARY = shutil.which("socat")
SER2NET_BINARY = shutil.which("ser2net")
HUB4COM_BINARY = shutil.which(
    "hub4com.exe",
    path=Path(__file__).resolve().parent / "data" / "windows" / "hub4com-2.1.0.0-386",
) or shutil.which("hub4com")
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


class SerialBackend(str, enum.Enum):
    """Known serial-pair backend families used by the test suite."""

    SOCAT = "socat"
    SOCKET = "socket"
    ESPHOME = "esphome"
    ESPHOME_HOST = "esphome_host"
    ADAPTER = "adapter"
    RFC2217 = "rfc2217"
    SER2NET = "ser2net"
    HUB4COM = "hub4com"


class SerialQuirk(str, enum.Enum):
    """Quirks carried by a serial transport."""

    NO_RTS_CTS = "no-rts-cts"
    NO_DTR_DSR = "no-dtr-dsr"
    NO_RTS_DTR_READBACK = "no-rts-dtr-readback"
    NO_NUM_UNREAD_BYTES = "no-num-unread-bytes"
    NO_NUM_UNWRITTEN_BYTES = "no-num-unwritten-bytes"
    NO_RESET_WRITE_BUFFER = "no-reset-write-buffer"
    NO_RESET_READ_BUFFER = "no-reset-read-buffer"
    NO_WRITE_TIMEOUT = "no-write-timeout"
    NO_WRITE_LIMITS = "no-write-limits"
    NO_BUFFER_CONTROL = "no-buffer-control"
    NO_PAUSE_WRITING_CALLBACKS = "no-pause-writing-callbacks"
    NO_EXCLUSIVITY = "no-exclusivity"
    NO_UNPLUG = "no-unplug"


SERIAL_PAIR_DEFAULT_QUIRKS: dict[SerialBackend, frozenset[SerialQuirk]] = {
    SerialBackend.SOCAT: frozenset(
        {
            SerialQuirk.NO_RTS_CTS,
            SerialQuirk.NO_DTR_DSR,
            SerialQuirk.NO_RESET_WRITE_BUFFER,
            SerialQuirk.NO_EXCLUSIVITY,
            SerialQuirk.NO_BUFFER_CONTROL,
        }
    ),
    SerialBackend.SOCKET: frozenset(
        {
            SerialQuirk.NO_RTS_CTS,
            SerialQuirk.NO_DTR_DSR,
            SerialQuirk.NO_RESET_WRITE_BUFFER,
            SerialQuirk.NO_WRITE_TIMEOUT,
            SerialQuirk.NO_NUM_UNREAD_BYTES,
            SerialQuirk.NO_PAUSE_WRITING_CALLBACKS,
            SerialQuirk.NO_EXCLUSIVITY,
            SerialQuirk.NO_UNPLUG,
        }
    ),
    SerialBackend.ESPHOME: frozenset(
        {
            SerialQuirk.NO_BUFFER_CONTROL,
            SerialQuirk.NO_RESET_WRITE_BUFFER,
            SerialQuirk.NO_WRITE_TIMEOUT,
            SerialQuirk.NO_EXCLUSIVITY,
        }
    ),
    SerialBackend.ESPHOME_HOST: frozenset(
        {
            SerialQuirk.NO_BUFFER_CONTROL,
            SerialQuirk.NO_RESET_WRITE_BUFFER,
            SerialQuirk.NO_WRITE_TIMEOUT,
            # Host binary does not support flow control
            SerialQuirk.NO_DTR_DSR,
            SerialQuirk.NO_RTS_CTS,
            SerialQuirk.NO_EXCLUSIVITY,
            SerialQuirk.NO_UNPLUG,
        }
    ),
    SerialBackend.RFC2217: frozenset(
        {
            SerialQuirk.NO_RTS_DTR_READBACK,
            SerialQuirk.NO_NUM_UNREAD_BYTES,
            SerialQuirk.NO_RESET_WRITE_BUFFER,
            SerialQuirk.NO_WRITE_TIMEOUT,
            SerialQuirk.NO_PAUSE_WRITING_CALLBACKS,
            SerialQuirk.NO_EXCLUSIVITY,
        }
    ),
    SerialBackend.SER2NET: frozenset({}),
    SerialBackend.HUB4COM: frozenset({}),
    SerialBackend.ADAPTER: frozenset(
        {
            SerialQuirk.NO_UNPLUG,
        }
    ),
}


@dataclasses.dataclass(frozen=True, kw_only=True)
class UnresolvedSerialPair:
    """Description of a test serial pair before fixture creation."""

    # The URIs to connect to either side, always set for emitted specs
    left: str | None
    right: str | None

    original_left: str | None
    original_right: str | None

    # Backends to chain
    backends: tuple[SerialBackend, ...]

    # Accumulated quirks
    quirks: frozenset[SerialQuirk]

    serial_class: str | None = None
    modem_line_propagation_delay: float = 0.05

    def chain(self, *backends: SerialBackend) -> Self:
        """Chain another backend layer on top of this one, accumulating quirks."""
        result = self

        for backend in backends:
            result = dataclasses.replace(
                result,
                original_left=result.original_left,
                original_right=result.original_right,
                backends=(backend,) + result.backends,
                quirks=frozenset(result.quirks) | SERIAL_PAIR_DEFAULT_QUIRKS[backend],
            )

        return result


@dataclasses.dataclass(frozen=True)
class SerialPair(UnresolvedSerialPair):
    """Description of a test serial pair after fixture creation."""

    left: str
    right: str

    original_left: str
    original_right: str

    serial_class: str


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
def create_adapter_pair(left: str, right: str) -> Iterator[tuple[str, str]]:
    """Fixture to clean up/set up physical adapters, which may have quirks."""
    if left.startswith("CNC") or right.startswith("CNC"):
        # com0com has baudrate emulation and requires buffers to be purged
        assert sys.platform == "win32"

        from win32file import (  # noqa: PLC0415
            PURGE_RXABORT,
            PURGE_RXCLEAR,
            PURGE_TXABORT,
            PURGE_TXCLEAR,
            PurgeComm,
        )

        for adapter in (left, right):
            if not adapter.startswith("CNC"):
                continue

            with serialx.Serial.from_url(adapter, baudrate=10_000_000) as serial:
                flags = PURGE_TXABORT | PURGE_RXABORT | PURGE_TXCLEAR | PURGE_RXCLEAR

                PurgeComm(serial._handle, flags)  # type: ignore[attr-defined]
                time.sleep(0.05)

        yield (left, right)
    elif left.startswith("/dev/tnt") or right.startswith("/dev/tnt"):
        try:
            yield (left, right)
        finally:
            for path in (left, right):
                try:
                    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                except OSError as exc:  # noqa: PERF203
                    if exc.errno != errno.EBUSY:
                        continue

                    logger = logging.getLogger(__name__)
                    logger.warning("tty0tty %s is EBUSY after teardown", path)

                    # Who has this device open?
                    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                        try:
                            for f in proc.open_files():
                                if f.path == path:
                                    logger.warning(
                                        "  PID %d (%s) fd=%d: %s",
                                        proc.pid,
                                        proc.info["name"],
                                        f.fd,
                                        proc.info["cmdline"],
                                    )
                        except (psutil.NoSuchProcess, psutil.AccessDenied):  # noqa: PERF203
                            pass

                    # Check our own process
                    for f in psutil.Process().open_files():
                        if "/dev/tnt" in f.path:
                            logger.warning("  SELF fd=%d: %s", f.fd, f.path)
                else:
                    os.close(fd)
    else:
        yield (left, right)


@contextlib.contextmanager
def create_esphome_pair(
    left_tty: str,
    right_tty: str,
    *,
    noise_psk: str = "",
) -> Iterator[tuple[str, str]]:
    """Create an esphome:// pair."""
    assert ESPHOME_HOST_BINARY is not None

    env = os.environ.copy()
    env["SERIALX_UART_LEFT"] = left_tty
    env["SERIALX_UART_RIGHT"] = right_tty
    env["SERIALX_API_PORT"] = "0"
    env["SERIALX_NOISE_PSK"] = noise_psk

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
            f"esphome://127.0.0.1:{api_port}?port_name=Serial+Proxy+Left",
            f"esphome://127.0.0.1:{api_port}?port_name=Serial+Proxy+Right",
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


@contextlib.contextmanager
def create_ser2net_pair(
    left_adapter: str, right_adapter: str
) -> Iterator[tuple[str, str]]:
    """Create a pair of independent RFC2217 sockets using ser2net."""

    # fmt: off
    proc = subprocess.Popen(
        [
            "ser2net",
            "-n",  # Don't detach from the controlling terminal
            "-r",  # Print "Ready" to stdout when listening
            "-u",  # Disable UUCP locking
            "-Y", json.dumps(
                {
                    "connections": {
                        "left_adapter": {
                            "accepter": "telnet(rfc2217),tcp,0",
                            "connector": f"serialdev(),{left_adapter},speed=115200n81",
                        },
                        "right_adapter": {
                            "accepter": "telnet(rfc2217),tcp,0",
                            "connector": f"serialdev(),{right_adapter},speed=115200n81",
                        },
                    }
                }
            )
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # fmt: on

    try:
        _wait_for_ready(
            proc,
            stream=proc.stdout,
            marker="Ready",
            name="ser2net",
        )

        left, right = _get_listening_ports(proc.pid)

        yield (
            f"rfc2217://127.0.0.1:{left}",
            f"rfc2217://127.0.0.1:{right}",
        )
    finally:
        if proc.returncode is None:
            proc.terminate()
            proc.wait()


@contextlib.contextmanager
def create_hub4com_pair(
    left_adapter: str, right_adapter: str, *, comport: str = "server"
) -> Iterator[tuple[str, str]]:
    """Create a pair of independent RFC2217 sockets using hub4com on Windows."""
    assert HUB4COM_BINARY is not None

    hub4com_args = [
        f"--create-filter=telnet,tcp,telnet:--comport={comport} --suppress-echo=yes",
        "--create-filter=lsrmap,tcp,lsrmap",
        "--create-filter=pinmap,tcp,pinmap:--cts=cts --dsr=dsr --dcd=dcd --ring=ring",
        "--create-filter=linectl,tcp,lc:--br=local --lc=local",
        "--create-filter=pinmap,com,pinmap:--rts=cts --dtr=dsr --break=break",
        "--create-filter=linectl,com,lc:--br=remote --lc=remote",
        "--create-filter=purge,com,purge",
        "--add-filters=0:com",
        "--add-filters=1:tcp",
        "--octs=off",
        "--write-limit=65536",
    ]

    procs = []

    try:
        for adapter in (left_adapter, right_adapter):
            proc = subprocess.Popen(
                [
                    HUB4COM_BINARY,
                    *hub4com_args,
                    f"\\\\.\\{adapter}",
                    "--use-driver=tcp",
                    "--interface=127.0.0.1",
                    "*0",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            procs.append(proc)

        for proc in procs:
            _wait_for_ready(
                proc,
                stream=proc.stdout,
                marker="Started TCP(",
                name="hub4com",
            )

        left, right = [_get_listening_ports(proc.pid)[0] for proc in procs]

        yield (
            f"rfc2217://127.0.0.1:{left}",
            f"rfc2217://127.0.0.1:{right}",
        )
    finally:
        for proc in procs:
            if proc.returncode is None:
                proc.terminate()
                proc.wait()


@contextlib.asynccontextmanager
async def async_create_reader_writer(
    *args: Any, **kwargs: Any
) -> AsyncIterator[
    tuple[asyncio.StreamReader, serialx.SerialStreamWriter[BaseSerialTransport]]
]:
    """Create a single reader/writer pair."""
    reader, writer = await serialx.open_serial_connection(*args, **kwargs)

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
