"""Transport compliance tests for both sync and async implementations.

These tests are parameterized to run against multiple transport implementations
(currently 'socat' for PTY-based serial and 'socket' for network-based serial)
to ensure consistent behavior across different backends.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
import logging
import os
import sys

if sys.version_info >= (3, 11):
    from asyncio import timeout as asyncio_timeout
else:
    from async_timeout import timeout as asyncio_timeout

import pytest

from serialx import (
    ModemPins,
    Parity,
    PinState,
    Serial,
    SerialStreamWriter,
    SerialTransport,
    StopBits,
    create_serial_connection,
)
from serialx.common import BaseSerialTransport
from serialx.platforms.serial_socket import SocketSerial
from tests.common import (
    SOCAT_BINARY,
    async_create_reader_writer,
    async_create_reader_writer_pair,
    async_create_socat_pair,
    create_socat_pair,
)
from tests.test_socket_async import async_create_socket_pair
from tests.test_socket_sync import create_socket_pair

LOGGER = logging.getLogger(__name__)


# --- Sync Fixtures ---


@pytest.fixture(params=["socat", "socket"])
def sync_transport_pair(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[Serial, Serial]]:
    """Yield a connected pair of sync Serial objects."""
    backend = request.param

    if backend == "socat":
        if not SOCAT_BINARY:
            pytest.skip("socat binary is missing")
        with create_socat_pair() as (left, right):
            yield (left, right)
    elif backend == "socket":
        with create_socket_pair() as (left, right):
            yield (left, right)
    else:
        raise ValueError(f"Unknown backend: {backend}")


@pytest.fixture
def sync_serial_pair(
    sync_transport_pair: tuple[str, str],
) -> Iterator[tuple[Serial, Serial]]:
    """Yield a connected pair of opened Serial objects with default settings."""
    left_path, right_path = sync_transport_pair
    # Detect which class to use based on the path/url
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    right_cls = SocketSerial if right_path.startswith("socket://") else Serial

    with (
        left_cls(left_path, baudrate=115200) as left_serial,
        right_cls(right_path, baudrate=115200) as right_serial,
    ):
        yield left_serial, right_serial


# --- Async Fixtures ---


@pytest.fixture(params=["socat", "socket"])
async def async_transport_pair(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[str, str]]:
    """Yield a connected pair of async transport paths/URLs."""
    backend = request.param

    if backend == "socat":
        if not SOCAT_BINARY:
            pytest.skip("socat binary is missing")
        async with async_create_socat_pair() as (left, right):
            yield (left, right)
    elif backend == "socket":
        async with async_create_socket_pair() as (left, right):
            yield (left, right)
    else:
        raise ValueError(f"Unknown backend: {backend}")


@pytest.fixture
async def async_serial_pair(
    async_transport_pair: tuple[str, str],
) -> AsyncIterator[
    tuple[
        asyncio.StreamReader,
        SerialStreamWriter[BaseSerialTransport],
        asyncio.StreamReader,
        SerialStreamWriter[BaseSerialTransport],
    ]
]:
    """Yield a connected pair of reader/writers with default settings."""
    left_path, right_path = async_transport_pair
    async with async_create_reader_writer_pair(
        left_path, right_path, baudrate=115200
    ) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        yield reader_left, writer_left, reader_right, writer_right


# --- Sync Compliance Tests ---


def test_sync_all_bytes(sync_serial_pair: tuple[Serial, Serial]) -> None:
    """Test that all bytes 0-255 can be transmitted."""
    left, right = sync_serial_pair
    data = bytes(range(256))
    left.write(data)
    result = right.readexactly(len(data))
    assert result == data


def test_sync_segmented_binary_data(sync_serial_pair: tuple[Serial, Serial]) -> None:
    """Test binary data sent in segments."""
    left, right = sync_serial_pair
    segment_size = 16
    data = bytes(range(256))

    for i in range(0, 256, segment_size):
        segment = data[i : i + segment_size]
        left.write(segment)
        result = right.readexactly(len(segment))
        assert result == segment


@pytest.mark.parametrize("size", [1, 16, 64, 256, 512, 1024])
def test_sync_binary_payload_sizes(
    sync_serial_pair: tuple[Serial, Serial], size: int
) -> None:
    """Test various binary payload sizes."""
    left, right = sync_serial_pair
    data = bytes([i % 256 for i in range(size)])
    left.write(data)
    result = right.readexactly(len(data))
    assert result == data


def test_sync_null_bytes(sync_serial_pair: tuple[Serial, Serial]) -> None:
    """Test that null bytes (0x00) can be transmitted."""
    left, right = sync_serial_pair
    null_data = b"\x00" * 64
    left.write(null_data)
    result = right.readexactly(len(null_data))
    assert result == null_data


def test_sync_overlapping_read_write(sync_serial_pair: tuple[Serial, Serial]) -> None:
    """Test that read and write can overlap, data is buffered."""
    left, right = sync_serial_pair
    data = bytes(range(256))
    read = b""

    left.write(data[:100])
    read += right.readexactly(10)
    left.write(data[100:150])
    read += right.readexactly(10)
    left.write(data[150:])
    read += right.readexactly(10)
    read += right.readexactly(256 - 30)

    assert read == data


@pytest.mark.parametrize(
    "baudrate,chunk_size",
    [
        (9600, 1),
        (9600, 16),
        (115200, 1),
        (115200, 16),
        (115200, 64),
        (921600, 1),
        (921600, 16),
        (921600, 256),
        (921600, 1024),
    ],
)
def test_sync_random_large(
    sync_transport_pair: tuple[str, str], baudrate: int, chunk_size: int
) -> None:
    """Test random read/write at various speeds (requires custom setup per test)."""
    left_path, right_path = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    right_cls = SocketSerial if right_path.startswith("socket://") else Serial

    with (
        left_cls(left_path, baudrate=baudrate) as left,
        right_cls(right_path, baudrate=baudrate) as right,
    ):
        data = os.urandom(chunk_size)
        left.write(data)
        read_data = right.readexactly(chunk_size)
        assert read_data == data


@pytest.mark.parametrize("iterations", [16, 32, 64])
def test_sync_repeated_write_read_cycles(
    sync_serial_pair: tuple[Serial, Serial], iterations: int
) -> None:
    """Test repeated write/read cycles."""
    left, right = sync_serial_pair
    data = bytes(range(256))

    for _ in range(iterations):
        left.write(data)
        result = right.readexactly(len(data))
        assert result == data


def test_sync_buffered_writes_then_read(
    sync_serial_pair: tuple[Serial, Serial],
) -> None:
    """Test multiple writes followed by a single read."""
    left, right = sync_serial_pair
    chunk = bytes(range(256))
    iterations = 4

    for _ in range(iterations):
        left.write(chunk)

    total_size = len(chunk) * iterations
    result = right.readexactly(total_size)
    expected = chunk * iterations
    assert result == expected


@pytest.mark.parametrize("payload_size", [1024, 2048])
def test_sync_large_payload(
    sync_transport_pair: tuple[str, str], payload_size: int
) -> None:
    """Test large payload transmission."""
    left_path, right_path = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    right_cls = SocketSerial if right_path.startswith("socket://") else Serial

    with (
        left_cls(left_path, baudrate=921600) as left,
        right_cls(right_path, baudrate=921600) as right,
    ):
        data = bytes([i % 256 for i in range(payload_size)])
        left.write(data)
        result = right.readexactly(len(data))
        assert result == data


def test_sync_rapid_small_writes(sync_serial_pair: tuple[Serial, Serial]) -> None:
    """Test rapid succession of small writes."""
    left, right = sync_serial_pair
    iterations = 256
    received = bytearray()

    for i in range(iterations):
        data = bytes([i % 256])
        left.write(data)
        result = right.readexactly(1)
        received.extend(result)

    expected = bytes([i % 256 for i in range(iterations)])
    assert bytes(received) == expected


@pytest.mark.parametrize(
    "baudrate,iterations", [(9600, 8), (115200, 64), (921600, 512)]
)
def test_sync_sustained_throughput(
    sync_transport_pair: tuple[str, str], baudrate: int, iterations: int
) -> None:
    """Test sustained data throughput at various baudrates."""
    left_path, right_path = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    right_cls = SocketSerial if right_path.startswith("socket://") else Serial

    with (
        left_cls(left_path, baudrate=baudrate) as left,
        right_cls(right_path, baudrate=baudrate) as right,
    ):
        chunk = os.urandom(1024)
        for _ in range(iterations):
            left.write(chunk)
            result = right.readexactly(len(chunk))
            assert result == chunk


@pytest.mark.parametrize(
    "baudrate", [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
)
def test_sync_valid_baudrates(
    sync_transport_pair: tuple[str, str], baudrate: int
) -> None:
    """Test that valid baudrates are accepted."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    with left_cls(left_path, baudrate=baudrate) as serial:
        assert serial.baudrate == baudrate
        serial.write(b"test")


@pytest.mark.parametrize(
    "parity", [Parity.NONE, Parity.ODD, Parity.EVEN, Parity.MARK, Parity.SPACE]
)
def test_sync_valid_parity(
    sync_transport_pair: tuple[str, str], parity: Parity
) -> None:
    """Test that valid parity settings are accepted."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    with left_cls(left_path, baudrate=115200, parity=parity) as serial:
        assert serial.parity == parity
        serial.write(b"test")


@pytest.mark.parametrize(
    "stopbits,expected",
    [
        (StopBits.ONE, StopBits.ONE),
        (StopBits.ONE_POINT_FIVE, StopBits.ONE_POINT_FIVE),
        (StopBits.TWO, StopBits.TWO),
        (1, StopBits.ONE),
        (1.5, StopBits.ONE_POINT_FIVE),
        (2, StopBits.TWO),
    ],
)
def test_sync_valid_stopbits(
    sync_transport_pair: tuple[str, str],
    stopbits: StopBits | int | float,
    expected: StopBits,
) -> None:
    """Test that valid stopbits settings are accepted."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    with left_cls(left_path, baudrate=115200, stopbits=stopbits) as serial:
        assert serial.stopbits == expected
        serial.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
def test_sync_valid_byte_size(
    sync_transport_pair: tuple[str, str], byte_size: int
) -> None:
    """Test that valid byte sizes are accepted."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    with left_cls(left_path, baudrate=115200, byte_size=byte_size) as serial:
        serial.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
def test_sync_xonxoff_setting(
    sync_transport_pair: tuple[str, str], xonxoff: bool
) -> None:
    """Test that xonxoff setting is accepted."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    with left_cls(left_path, baudrate=115200, xonxoff=xonxoff) as serial:
        serial.write(b"test")


@pytest.mark.parametrize("rtscts", [True, False])
def test_sync_rtscts_setting(
    sync_transport_pair: tuple[str, str], rtscts: bool
) -> None:
    """Test that rtscts setting is accepted."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    with left_cls(left_path, baudrate=115200, rtscts=rtscts) as serial:
        serial.write(b"test")


def test_sync_exclusive(sync_transport_pair: tuple[str, str]) -> None:
    """Test that exclusive setting is respected."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial

    with left_cls(left_path, baudrate=115200, exclusive=True) as serial:
        assert serial.exclusive is True

        # Behavior depends on backend
        if left_path.startswith("socket://"):
            # Socket endpoints are not lockable tty devices
            with left_cls(left_path, baudrate=115200, exclusive=True) as serial2:
                assert serial2.exclusive is True
        else:
            # Socat PTYs should lock
            with pytest.raises(OSError):
                with left_cls(left_path, baudrate=115200, exclusive=True):
                    pass


def test_sync_exclusive_disabled(sync_transport_pair: tuple[str, str]) -> None:
    """Test that exclusive setting is respected."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial

    with left_cls(left_path, baudrate=115200, exclusive=False) as serial1:
        assert serial1.exclusive is False
        with left_cls(left_path, baudrate=115200, exclusive=False) as serial2:
            assert serial2.exclusive is False
            serial2.write(b"test")


def test_sync_context_manager_multiple_times(
    sync_transport_pair: tuple[str, str],
) -> None:
    """Test that context manager can be used multiple times."""
    left_path, right_path = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    right_cls = SocketSerial if right_path.startswith("socket://") else Serial

    serial_left = left_cls(left_path, baudrate=115200)
    serial_right = right_cls(right_path, baudrate=115200)

    # First context
    with serial_left, serial_right:
        serial_left.write(b"test1")
        result = serial_right.readexactly(5)
        assert result == b"test1"

    # Second context
    with serial_left, serial_right:
        serial_left.write(b"test2")
        result = serial_right.readexactly(5)
        assert result == b"test2"


def test_sync_open_close_cycles(sync_transport_pair: tuple[str, str]) -> None:
    """Test multiple open/close cycles."""
    left_path, right_path = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial
    right_cls = SocketSerial if right_path.startswith("socket://") else Serial

    serial_left = left_cls(left_path, baudrate=115200)
    serial_right = right_cls(right_path, baudrate=115200)

    for i in range(1, 4):
        serial_left.open()
        serial_left.configure_port()
        serial_right.open()
        serial_right.configure_port()

        chunk = str(i).encode("ascii")
        serial_left.write(chunk)
        assert serial_right.readexactly(1) == chunk

        serial_left.close()
        serial_right.close()


def test_sync_flush_after_write(sync_serial_pair: tuple[Serial, Serial]) -> None:
    """Test flushing after write operation."""
    left, right = sync_serial_pair
    left.flush()
    data = b"Test flush operation"
    left.write(data)
    left.flush()
    result = right.readexactly(len(data))
    assert result == data


def test_sync_multiple_flush_calls(sync_serial_pair: tuple[Serial, Serial]) -> None:
    """Test multiple consecutive flush calls."""
    left, right = sync_serial_pair
    data = b""

    for i in range(5):
        chunk = f"Test data {i}".encode("ascii")
        data += chunk
        left.write(chunk)
        left.flush()

    result = right.readexactly(len(data))
    assert result == data


def test_sync_get_modem_pins(sync_transport_pair: tuple[str, str]) -> None:
    """Test reading modem control bits."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial

    with left_cls(left_path, baudrate=115200) as serial:
        modem_pins = serial.get_modem_pins()
        assert isinstance(modem_pins, ModemPins)
        for field in ["le", "dtr", "rts", "st", "sr", "cts", "car", "rng", "dsr"]:
            value = getattr(modem_pins, field)
            assert value in (PinState.HIGH, PinState.LOW, PinState.UNDEFINED)


def test_sync_set_modem_pins(sync_transport_pair: tuple[str, str]) -> None:
    """Test setting modem control bits."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial

    with left_cls(left_path, baudrate=115200) as serial:
        serial.set_modem_pins(dtr=True, rts=True)
        assert isinstance(serial.get_modem_pins(), ModemPins)

        serial.set_modem_pins(dtr=False)
        assert isinstance(serial.get_modem_pins(), ModemPins)

        serial.set_modem_pins(dtr=False, rts=False)
        assert isinstance(serial.get_modem_pins(), ModemPins)


def test_sync_deprecated_dtr_property(sync_transport_pair: tuple[str, str]) -> None:
    """Test DTR property (deprecated alias)."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial

    with left_cls(left_path, baudrate=115200) as serial:
        serial.dtr = True
        serial.dtr = False


def test_sync_deprecated_rts_property(sync_transport_pair: tuple[str, str]) -> None:
    """Test RTS property (deprecated alias)."""
    left_path, _ = sync_transport_pair
    left_cls = SocketSerial if left_path.startswith("socket://") else Serial

    with left_cls(left_path, baudrate=115200) as serial:
        serial.rts = True
        serial.rts = False


# --- Async Compliance Tests ---


async def test_async_all_bytes(async_serial_pair) -> None:
    """Test that all bytes 0-255 can be transmitted."""
    _, writer_left, reader_right, _ = async_serial_pair
    data = bytes(range(256))
    writer_left.write(data)
    result = await reader_right.readexactly(len(data))
    assert result == data


async def test_async_segmented_binary_data(async_serial_pair) -> None:
    """Test binary data sent in segments."""
    _, writer_left, reader_right, _ = async_serial_pair
    segment_size = 16
    data = bytes(range(256))

    for i in range(0, 256, segment_size):
        segment = data[i : i + segment_size]
        writer_left.write(segment)
        result = await reader_right.readexactly(len(segment))
        assert result == segment


@pytest.mark.parametrize("size", [1, 16, 64, 256, 512, 1024])
async def test_async_binary_payload_sizes(async_serial_pair, size: int) -> None:
    """Test various binary payload sizes."""
    _, writer_left, reader_right, _ = async_serial_pair
    data = bytes([i % 256 for i in range(size)])
    writer_left.write(data)
    result = await reader_right.readexactly(len(data))
    assert result == data


async def test_async_null_bytes(async_serial_pair) -> None:
    """Test that null bytes (0x00) can be transmitted."""
    _, writer_left, reader_right, _ = async_serial_pair
    null_data = b"\x00" * 64
    writer_left.write(null_data)
    result = await reader_right.readexactly(len(null_data))
    assert result == null_data


async def test_async_overlapping_read_write(async_serial_pair) -> None:
    """Test that read and write can overlap, data is buffered."""
    _, writer_left, reader_right, _ = async_serial_pair
    data = bytes(range(256))
    read = b""

    writer_left.write(data[:100])
    read += await reader_right.readexactly(10)
    writer_left.write(data[100:150])
    read += await reader_right.readexactly(10)
    writer_left.write(data[150:])
    read += await reader_right.readexactly(10)
    read += await reader_right.readexactly(256 - 30)

    assert read == data


@pytest.mark.parametrize(
    "baudrate,chunk_size",
    [
        (9600, 1),
        (9600, 16),
        (115200, 1),
        (115200, 16),
        (115200, 64),
        (921600, 1),
        (921600, 16),
        (921600, 256),
        (921600, 1024),
    ],
)
async def test_async_random_large(
    async_transport_pair: tuple[str, str], baudrate: int, chunk_size: int
) -> None:
    """Test random read/write at various speeds."""
    left, right = async_transport_pair
    async with async_create_reader_writer_pair(left, right, baudrate=baudrate) as (
        _rl,
        writer_left,
        reader_right,
        _wr,
    ):
        data = os.urandom(chunk_size)
        writer_left.write(data)
        read_data = await reader_right.readexactly(chunk_size)
        assert read_data == data


@pytest.mark.parametrize("iterations", [16, 32, 64])
async def test_async_repeated_write_read_cycles(
    async_serial_pair, iterations: int
) -> None:
    """Test repeated write/read cycles."""
    _, writer_left, reader_right, _ = async_serial_pair
    data = bytes(range(256))

    for _ in range(iterations):
        writer_left.write(data)
        result = await reader_right.readexactly(len(data))
        assert result == data


async def test_async_buffered_writes_then_read(async_serial_pair) -> None:
    """Test multiple writes followed by a single read."""
    _, writer_left, reader_right, _ = async_serial_pair
    chunk = bytes(range(256))
    iterations = 4

    for _ in range(iterations):
        writer_left.write(chunk)

    total_size = len(chunk) * iterations
    result = await reader_right.readexactly(total_size)
    expected = chunk * iterations
    assert result == expected


@pytest.mark.parametrize("payload_size", [1024, 2048])
async def test_async_large_payload(
    async_transport_pair: tuple[str, str], payload_size: int
) -> None:
    """Test large payload transmission."""
    left, right = async_transport_pair
    async with async_create_reader_writer_pair(left, right, baudrate=921600) as (
        _rl,
        writer_left,
        reader_right,
        _wr,
    ):
        data = bytes([i % 256 for i in range(payload_size)])
        writer_left.write(data)
        result = await reader_right.readexactly(len(data))
        assert result == data


async def test_async_rapid_small_writes(async_serial_pair) -> None:
    """Test rapid succession of small writes."""
    _, writer_left, reader_right, _ = async_serial_pair
    iterations = 256
    received = bytearray()

    for i in range(iterations):
        data = bytes([i % 256])
        writer_left.write(data)
        result = await reader_right.readexactly(1)
        received.extend(result)

    expected = bytes([i % 256 for i in range(iterations)])
    assert bytes(received) == expected


@pytest.mark.parametrize(
    "baudrate,iterations", [(9600, 8), (115200, 64), (921600, 512)]
)
async def test_async_sustained_throughput(
    async_transport_pair: tuple[str, str], baudrate: int, iterations: int
) -> None:
    """Test sustained data throughput at various baudrates."""
    left, right = async_transport_pair
    async with async_create_reader_writer_pair(left, right, baudrate=baudrate) as (
        _rl,
        writer_left,
        reader_right,
        _wr,
    ):
        chunk = os.urandom(1024)
        for _ in range(iterations):
            writer_left.write(chunk)
            result = await reader_right.readexactly(len(chunk))
            assert result == chunk


@pytest.mark.parametrize(
    "baudrate", [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
)
async def test_async_valid_baudrates(
    async_transport_pair: tuple[str, str], baudrate: int
) -> None:
    """Test that valid baudrates are accepted."""
    left, _ = async_transport_pair
    async with async_create_reader_writer(left, baudrate=baudrate) as (_, writer):
        assert writer.transport.baudrate == baudrate
        writer.write(b"test")


@pytest.mark.parametrize(
    "parity", [Parity.NONE, Parity.ODD, Parity.EVEN, Parity.MARK, Parity.SPACE]
)
async def test_async_valid_parity(
    async_transport_pair: tuple[str, str], parity: Parity
) -> None:
    """Test that valid parity settings are accepted."""
    left, _ = async_transport_pair
    async with async_create_reader_writer(left, baudrate=115200, parity=parity) as (
        _,
        writer,
    ):
        assert writer.transport.parity == parity
        writer.write(b"test")


@pytest.mark.parametrize(
    "stopbits,expected",
    [
        (StopBits.ONE, StopBits.ONE),
        (StopBits.ONE_POINT_FIVE, StopBits.ONE_POINT_FIVE),
        (StopBits.TWO, StopBits.TWO),
        (1, StopBits.ONE),
        (1.5, StopBits.ONE_POINT_FIVE),
        (2, StopBits.TWO),
    ],
)
async def test_async_valid_stopbits(
    async_transport_pair: tuple[str, str],
    stopbits: StopBits | int | float,
    expected: StopBits,
) -> None:
    """Test that valid stopbits settings are accepted."""
    left, _ = async_transport_pair
    async with async_create_reader_writer(left, baudrate=115200, stopbits=stopbits) as (
        _,
        writer,
    ):
        assert writer.transport.stopbits == expected
        writer.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
async def test_async_valid_byte_size(
    async_transport_pair: tuple[str, str], byte_size: int
) -> None:
    """Test that valid byte sizes are accepted."""
    left, _ = async_transport_pair
    async with async_create_reader_writer(
        left, baudrate=115200, byte_size=byte_size
    ) as (_, writer):
        writer.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
async def test_async_xonxoff_setting(
    async_transport_pair: tuple[str, str], xonxoff: bool
) -> None:
    """Test that xonxoff setting is accepted."""
    left, _ = async_transport_pair
    async with async_create_reader_writer(left, baudrate=115200, xonxoff=xonxoff) as (
        _,
        writer,
    ):
        writer.write(b"test")


@pytest.mark.parametrize("rtscts", [True, False])
async def test_async_rtscts_setting(
    async_transport_pair: tuple[str, str], rtscts: bool
) -> None:
    """Test that rtscts setting is accepted."""
    left, _ = async_transport_pair
    async with async_create_reader_writer(left, baudrate=115200, rtscts=rtscts) as (
        _,
        writer,
    ):
        writer.write(b"test")


async def test_async_concurrent_writes(async_serial_pair) -> None:
    """Test concurrent writes from multiple tasks."""
    _, writer_left, reader_right, _ = async_serial_pair

    async def write_data(data: bytes) -> None:
        writer_left.write(data)

    data1 = b"A" * 100
    data2 = b"B" * 100
    data3 = b"C" * 100

    await asyncio.gather(
        write_data(data1),
        write_data(data2),
        write_data(data3),
    )

    total_data = await reader_right.readexactly(300)
    assert total_data == b"A" * 100 + b"B" * 100 + b"C" * 100


async def test_async_read_with_timeout(async_serial_pair) -> None:
    """Test reading with timeout."""
    _, writer_left, reader_right, _ = async_serial_pair
    writer_left.write(b"test")

    result = await asyncio.wait_for(reader_right.readexactly(4), timeout=1.0)
    assert result == b"test"

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reader_right.readexactly(1), timeout=0.1)


async def test_async_get_modem_pins(async_transport_pair: tuple[str, str]) -> None:
    """Test reading modem control bits."""
    left, _ = async_transport_pair
    async with async_create_reader_writer(left, baudrate=115200) as (_, writer):
        modem_pins = await writer.transport.get_modem_pins()
        assert isinstance(modem_pins, ModemPins)
        for field in ["le", "dtr", "rts", "st", "sr", "cts", "car", "rng", "dsr"]:
            value = getattr(modem_pins, field)
            assert value in (PinState.HIGH, PinState.LOW, PinState.UNDEFINED)


async def test_async_set_modem_pins(async_transport_pair: tuple[str, str]) -> None:
    """Test setting modem control bits."""
    left, _ = async_transport_pair
    async with async_create_reader_writer(left, baudrate=115200) as (_, writer):
        await writer.transport.set_modem_pins(dtr=True, rts=True)
        assert isinstance(await writer.transport.get_modem_pins(), ModemPins)

        await writer.transport.set_modem_pins(dtr=False)
        assert isinstance(await writer.transport.get_modem_pins(), ModemPins)

        await writer.transport.set_modem_pins(dtr=False, rts=False)
        assert isinstance(await writer.transport.get_modem_pins(), ModemPins)


async def test_async_pause_resume(async_serial_pair) -> None:
    """Test transport pause and resume."""
    reader_left, writer_left, _, writer_right = async_serial_pair

    writer_left.transport.pause_reading()

    writer_right.write(b"A long message")
    await writer_right.drain()

    # Nothing can be read
    with pytest.raises(asyncio.TimeoutError):
        async with asyncio_timeout(1):
            await reader_left.read(1)

    writer_left.transport.resume_reading()
    assert (await reader_left.read(14)) == b"A long message"


async def test_async_backpressure_writer_removal(
    async_transport_pair: tuple[str, str],
) -> None:
    """Test that large writes with backpressure are handled correctly.

    This test catches three issue categories:
    1. AssertionError from writer not being removed when buffer empties
    2. Deadlock (via timeout) from direct writes blocking indefinitely
    3. Timing failures from writer not being added when buffering data
    Source: https://github.com/home-assistant-libs/pyserial-asyncio-fast/pull/36
    """
    left_path, right_path = async_transport_pair
    TEXT = b"Hello, World!"
    COUNT = 8 * 1024
    output_resume_event = asyncio.Event()
    data_received_count = 0

    class Input(asyncio.Protocol):
        _transport: SerialTransport

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            assert isinstance(transport, SerialTransport)
            self._transport = transport

        def data_received(self, data: bytes) -> None:
            nonlocal data_received_count
            data_received_count += len(data)
            self._transport.write(data)

    class Output(asyncio.Protocol):
        """Provides backpressure to writer via output_resume_event."""

        _transport: SerialTransport

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            assert isinstance(transport, SerialTransport)
            self._transport = transport
            output_resume_event.set()

        def pause_writing(self) -> None:
            output_resume_event.clear()

        def resume_writing(self) -> None:
            output_resume_event.set()

    loop = asyncio.get_running_loop()

    in_transport, _ = await create_serial_connection(
        loop, Input, left_path, baudrate=115200
    )
    out_transport, _ = await create_serial_connection(
        loop, Output, right_path, baudrate=115200
    )

    try:
        # Write a bunch of data so that we create a buffer and trigger backpressure
        for _ in range(COUNT):
            async with asyncio_timeout(5):
                await output_resume_event.wait()

            out_transport.write(TEXT)

        # Ensure that the write buffer eventually drains completely
        async with asyncio_timeout(5):
            while out_transport.get_write_buffer_size() > 0:
                await asyncio.sleep(0.1)

        # Verify we received some data on the input side
        assert data_received_count > 0
    finally:
        out_transport.close()
        in_transport.close()


async def test_async_close_is_idempotent(async_serial_pair) -> None:
    """Test closing writer multiple times is safe and drains buffer state."""
    _, writer_left, _, _ = async_serial_pair

    writer_left.close()
    await writer_left.wait_closed()
    assert writer_left.transport.get_write_buffer_size() == 0

    # Second close should be no-op
    writer_left.close()
    await writer_left.wait_closed()
