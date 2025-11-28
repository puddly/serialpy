"""Test async APIs with dual loopback hardware."""

import asyncio
import os
from typing import cast

import pytest

from serialx import Parity, SerialTransport, StopBits, create_serial_connection
from tests.common import (
    DUAL_LOOPBACK_LEFT,
    DUAL_LOOPBACK_RIGHT,
    async_create_dual_loopback,
    async_create_reader_writer,
)

pytestmark = pytest.mark.skipif(
    not DUAL_LOOPBACK_LEFT or not DUAL_LOOPBACK_RIGHT,
    reason="SERIALX_DUAL_LOOPBACK_LEFT and SERIALX_DUAL_LOOPBACK_RIGHT not set",
)


async def test_all_bytes_async() -> None:
    """Test that all bytes 0-255 can be transmitted."""
    async with async_create_dual_loopback(baudrate=115200) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        # Create a byte array with all possible byte values
        data = bytes(range(256))

        writer_left.write(data)
        result = await reader_right.readexactly(len(data))

        assert result == data


async def test_segmented_binary_data_async() -> None:
    """Test binary data sent in segments."""
    async with async_create_dual_loopback(baudrate=115200) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        # Send all bytes in smaller segments
        segment_size = 16
        data = bytes(range(256))

        for i in range(0, 256, segment_size):
            segment = data[i : i + segment_size]
            writer_left.write(segment)
            result = await reader_right.readexactly(len(segment))
            assert result == segment


@pytest.mark.parametrize(
    "size",
    [1, 16, 64, 256, 512, 1024],
)
async def test_binary_payload_sizes_async(size: int) -> None:
    """Test various binary payload sizes."""
    async with async_create_dual_loopback(baudrate=115200) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        # Create binary data with repeating pattern
        data = bytes([i % 256 for i in range(size)])

        writer_left.write(data)
        result = await reader_right.readexactly(len(data))

        assert result == data


async def test_null_bytes_async() -> None:
    """Test that null bytes (0x00) can be transmitted."""
    async with async_create_dual_loopback(baudrate=115200) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        # Send a bunch of null bytes
        null_data = b"\x00" * 64

        writer_left.write(null_data)
        result = await reader_right.readexactly(len(null_data))

        assert result == null_data


async def test_overlapping_read_write_async() -> None:
    """Test that read and write can overlap, data is buffered."""
    async with async_create_dual_loopback(baudrate=115200) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
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
    ("baudrate", "chunk_size"),
    [
        (9600, 1),
        (9600, 16),
        (115200, 1),
        (115200, 16),
        (115200, 64),
        # (921600, 1),  # Hardware can't reliably handle 921600 with dual loopback
        # (921600, 16),
        # (921600, 256),
        # (921600, 1024),
    ],
)
async def test_random_large_async(baudrate: int, chunk_size: int) -> None:
    """Test random read/write at various speeds."""
    async with async_create_dual_loopback(baudrate=baudrate) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        data = os.urandom(chunk_size)
        writer_left.write(data)

        read_data = await reader_right.readexactly(chunk_size)
        assert read_data == data


@pytest.mark.parametrize(
    "iterations",
    [16, 32, 64],
)
async def test_repeated_write_read_cycles_async(iterations: int) -> None:
    """Test repeated write/read cycles."""
    async with async_create_dual_loopback(baudrate=115200) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        data = bytes(range(256))

        for _i in range(iterations):
            writer_left.write(data)
            result = await reader_right.readexactly(len(data))
            assert result == data


async def test_buffered_writes_then_read_async() -> None:
    """Test multiple writes followed by a single read."""
    async with async_create_dual_loopback(baudrate=115200) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        chunk = bytes(range(256))
        iterations = 4

        # Write multiple chunks
        for _ in range(iterations):
            writer_left.write(chunk)

        # Read all data back
        total_size = len(chunk) * iterations
        result = await reader_right.readexactly(total_size)

        # Verify all data was received correctly
        expected = chunk * iterations
        assert result == expected


@pytest.mark.parametrize(
    "payload_size",
    [1024, 2048],  # Kernel buffers are typically ~4KB, stay well below that
)
async def test_large_payload_async(payload_size: int) -> None:
    """Test large payload transmission."""
    async with async_create_dual_loopback(baudrate=460800) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        data = bytes([i % 256 for i in range(payload_size)])

        writer_left.write(data)
        result = await reader_right.readexactly(len(data))

        assert result == data


async def test_rapid_small_writes_async() -> None:
    """Test rapid succession of small writes."""
    async with async_create_dual_loopback(baudrate=115200) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        # Send many small writes
        iterations = 256
        received = bytearray()

        for i in range(iterations):
            data = bytes([i % 256])
            writer_left.write(data)
            result = await reader_right.readexactly(1)
            received.extend(result)

        # Verify all bytes were received in order
        expected = bytes([i % 256 for i in range(iterations)])
        assert bytes(received) == expected


@pytest.mark.parametrize(
    ("baudrate", "iterations"),
    [
        (9600, 8),
        (115200, 64),
        # (921600, 512),  # Hardware can't reliably handle 921600 with dual loopback
    ],
)
async def test_sustained_throughput_async(baudrate: int, iterations: int) -> None:
    """Test sustained data throughput at various baudrates."""
    async with async_create_dual_loopback(baudrate=baudrate) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        chunk = os.urandom(1024)

        for _ in range(iterations):
            writer_left.write(chunk)
            result = await reader_right.readexactly(len(chunk))
            assert result == chunk


@pytest.mark.parametrize(
    "baudrate",
    [
        9600,
        19200,
        38400,
        57600,
        115200,
        230400,
        460800,
        # 921600,  # Hardware can't reliably handle 921600 with dual loopback
    ],
)
async def test_valid_baudrates_async(baudrate: int) -> None:
    """Test that valid baudrates are accepted."""
    async with async_create_dual_loopback(baudrate=baudrate) as (
        reader,
        writer,
        _,
        _writer_right,
    ):
        # Verify baudrate was set (check transport)
        transport = cast(SerialTransport, writer.transport)
        assert transport.baudrate == baudrate
        writer.write(b"test")


@pytest.mark.parametrize(
    "parity",
    [Parity.NONE, Parity.ODD, Parity.EVEN, Parity.MARK, Parity.SPACE],
)
async def test_valid_parity_async(parity: Parity) -> None:
    """Test that valid parity settings are accepted."""
    async with async_create_dual_loopback(baudrate=115200, parity=parity) as (
        reader,
        writer,
        _,
        _writer_right,
    ):
        transport = cast(SerialTransport, writer.transport)
        assert transport.parity == parity
        writer.write(b"test")


@pytest.mark.parametrize(
    ("stopbits", "expected"),
    [
        (StopBits.ONE, StopBits.ONE),
        (StopBits.ONE_POINT_FIVE, StopBits.ONE_POINT_FIVE),
        (StopBits.TWO, StopBits.TWO),
        (1, StopBits.ONE),
        (1.5, StopBits.ONE_POINT_FIVE),
        (2, StopBits.TWO),
    ],
)
async def test_valid_stopbits_async(
    stopbits: StopBits | int | float,
    expected: StopBits,
) -> None:
    """Test that valid stopbits settings are accepted."""
    async with async_create_dual_loopback(baudrate=115200, stopbits=stopbits) as (
        reader,
        writer,
        _,
        _writer_right,
    ):
        transport = cast(SerialTransport, writer.transport)
        assert transport.stopbits == expected
        writer.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
async def test_valid_byte_size_async(byte_size: int) -> None:
    """Test that valid byte sizes are accepted."""
    async with async_create_dual_loopback(baudrate=115200, byte_size=byte_size) as (
        reader,
        writer,
        _,
        _writer_right,
    ):
        writer.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
async def test_xonxoff_setting_async(xonxoff: bool) -> None:
    """Test that xonxoff setting is accepted."""
    async with async_create_dual_loopback(baudrate=115200, xonxoff=xonxoff) as (
        reader,
        writer,
        _,
        _writer_right,
    ):
        writer.write(b"test")


@pytest.mark.parametrize(
    "rtscts",
    [True, False],
)
async def test_rtscts_setting_async(rtscts: bool) -> None:
    """Test that rtscts setting is accepted."""
    async with async_create_dual_loopback(baudrate=115200, rtscts=rtscts) as (
        reader,
        writer,
        _,
        _writer_right,
    ):
        writer.write(b"test")


async def test_concurrent_writes_async() -> None:
    """Test concurrent writes from multiple tasks."""
    async with async_create_dual_loopback(baudrate=115200) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):

        async def write_data(data: bytes) -> None:
            writer_left.write(data)

        # Write different data concurrently
        data1 = b"A" * 100
        data2 = b"B" * 100
        data3 = b"C" * 100

        await asyncio.gather(
            write_data(data1),
            write_data(data2),
            write_data(data3),
        )

        # Read all data back
        total_data = await reader_right.readexactly(300)
        assert total_data == b"A" * 100 + b"B" * 100 + b"C" * 100


async def test_read_with_timeout_async() -> None:
    """Test reading with timeout."""
    async with async_create_dual_loopback(baudrate=115200) as (
        reader_left,
        writer_left,
        reader_right,
        writer_right,
    ):
        # Write some data
        writer_left.write(b"test")

        # Read with timeout should succeed
        result = await asyncio.wait_for(reader_right.readexactly(4), timeout=1.0)
        assert result == b"test"

        # Reading without data should timeout
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader_right.readexactly(1), timeout=0.1)


async def test_fast_open_close() -> None:
    """Test quickly opening and closing a port."""
    message = b"Fast write and close test" * 10
    connection_lost_event = asyncio.Event()

    class FastCloseProtocol(asyncio.Protocol):
        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            transport = cast(SerialTransport, transport)
            transport.write(message)
            # transport.close()  # Immediately closing after write will not cause data loss
            transport.abort()

        def connection_lost(self, exc: Exception | None) -> None:
            connection_lost_event.set()

    async with async_create_reader_writer(DUAL_LOOPBACK_RIGHT, baudrate=300) as (
        reader,
        writer,
    ):
        read_task = asyncio.create_task(reader.readexactly(len(message)))
        await asyncio.sleep(0)

        transport, _ = await create_serial_connection(
            asyncio.get_running_loop(),
            FastCloseProtocol,
            DUAL_LOOPBACK_LEFT,
            baudrate=300,
        )

        await connection_lost_event.wait()
        assert await read_task == message


async def test_deassert_on_open_async() -> None:
    """Test DTR/CTS deassertion on open."""
    async with async_create_reader_writer(DUAL_LOOPBACK_LEFT, baudrate=115200) as (
        reader_left,
        writer_left,
    ):
        # Open and set DTR/CTS
        async with async_create_reader_writer(
            DUAL_LOOPBACK_RIGHT, baudrate=115200, deassert_on_open=False
        ) as (
            reader_right,
            writer_right,
        ):
            await writer_right.transport.set_modem_pins(dtr=True)
            assert (await writer_left.transport.get_modem_pins()).cts is True

        # It persists
        assert (await writer_left.transport.get_modem_pins()).cts is True

        # When we deassert on open, it should clear
        async with async_create_reader_writer(
            DUAL_LOOPBACK_RIGHT, baudrate=115200, deassert_on_open=True
        ) as (
            reader_right,
            writer_right,
        ):
            assert (await writer_left.transport.get_modem_pins()).cts is False
            await writer_right.transport.set_modem_pins(dtr=True)

        # Nothing changes on close
        assert (await writer_left.transport.get_modem_pins()).cts is True


async def test_hang_up_on_close_async() -> None:
    """Test DTR/CTS hang up on close."""
    async with async_create_reader_writer(DUAL_LOOPBACK_LEFT, baudrate=115200) as (
        reader_left,
        writer_left,
    ):
        # Open and set DTR/CTS
        async with async_create_reader_writer(
            DUAL_LOOPBACK_RIGHT,
            baudrate=115200,
            hang_up_on_close=False,
            deassert_on_open=False,
        ) as (
            reader_right,
            writer_right,
        ):
            await writer_right.transport.set_modem_pins(dtr=True)
            assert (await writer_left.transport.get_modem_pins()).cts is True

        # It persists
        assert (await writer_left.transport.get_modem_pins()).cts is True

        # Without hang up on close, it still persists
        async with async_create_reader_writer(
            DUAL_LOOPBACK_RIGHT,
            baudrate=115200,
            hang_up_on_close=False,
            deassert_on_open=False,
        ) as (
            reader_right,
            writer_right,
        ):
            assert (await writer_left.transport.get_modem_pins()).cts is True

        assert (await writer_left.transport.get_modem_pins()).cts is True

        # When we hang up on close, it should clear
        async with async_create_reader_writer(
            DUAL_LOOPBACK_RIGHT,
            baudrate=115200,
            hang_up_on_close=True,
            deassert_on_open=False,
        ) as (
            reader_right,
            writer_right,
        ):
            assert (await writer_left.transport.get_modem_pins()).cts is True

        assert (await writer_left.transport.get_modem_pins()).cts is False


@pytest.mark.parametrize(
    ("rtscts", "deassert_on_open", "expected_state"),
    [
        (False, None, False),  # No flow control, auto-detect -> clear pins
        (False, False, True),  # No flow control, preserve pins (explicit override)
        (False, True, False),  # No flow control, clear pins (explicit, matches auto)
        (True, None, True),  # Flow control, auto-detect -> preserve pins
        (True, False, True),  # Flow control, preserve pins (explicit, matches auto)
        (True, True, False),  # Flow control, clear pins (explicit override)
    ],
)
async def test_deassert_on_open_with_rtscts_async(
    rtscts: bool, deassert_on_open: bool | None, expected_state: bool
) -> None:
    """Test interaction of deassert_on_open with rtscts."""
    async with async_create_reader_writer(DUAL_LOOPBACK_LEFT, baudrate=115200) as (
        reader_left,
        writer_left,
    ):
        # Set DTR on right side, verify CTS appears on left
        async with async_create_reader_writer(
            DUAL_LOOPBACK_RIGHT,
            baudrate=115200,
            rtscts=False,
            deassert_on_open=False,
        ) as (
            reader_right,
            writer_right,
        ):
            await writer_right.transport.set_modem_pins(dtr=True)
            assert (await writer_left.transport.get_modem_pins()).cts is True

        # DTR persists after close
        assert (await writer_left.transport.get_modem_pins()).cts is True

        # Open with test parameters
        async with async_create_reader_writer(
            DUAL_LOOPBACK_RIGHT,
            baudrate=115200,
            rtscts=rtscts,
            deassert_on_open=deassert_on_open,
        ) as (
            reader_right,
            writer_right,
        ):
            assert (await writer_left.transport.get_modem_pins()).cts is expected_state
