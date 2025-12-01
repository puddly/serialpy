"""Test async APIs with real loopback adapter."""

import asyncio
import os
from typing import cast

import pytest

from serialx import Parity, PinState, SerialTransport, StopBits
from tests.common import async_create_reader_writer


async def test_all_bytes_loopback_async(loopback_adapter: str) -> None:
    """Test that all bytes 0-255 can be transmitted."""
    async with async_create_reader_writer(loopback_adapter, baudrate=115200) as (
        reader,
        writer,
    ):
        # Create a byte array with all possible byte values
        data = bytes(range(256))

        writer.write(data)
        result = await reader.readexactly(len(data))

        assert result == data


async def test_segmented_binary_data_loopback_async(loopback_adapter: str) -> None:
    """Test binary data sent in segments."""
    async with async_create_reader_writer(loopback_adapter, baudrate=115200) as (
        reader,
        writer,
    ):
        # Send all bytes in smaller segments
        segment_size = 16
        data = bytes(range(256))

        for i in range(0, 256, segment_size):
            segment = data[i : i + segment_size]
            writer.write(segment)
            result = await reader.readexactly(len(segment))
            assert result == segment


@pytest.mark.parametrize(
    "size",
    [1, 16, 64, 256, 512, 1024],
)
async def test_binary_payload_sizes_loopback_async(
    loopback_adapter: str, size: int
) -> None:
    """Test various binary payload sizes."""
    async with async_create_reader_writer(loopback_adapter, baudrate=115200) as (
        reader,
        writer,
    ):
        # Create binary data with repeating pattern
        data = bytes([i % 256 for i in range(size)])

        writer.write(data)
        result = await reader.readexactly(len(data))

        assert result == data


async def test_null_bytes_loopback_async(loopback_adapter: str) -> None:
    """Test that null bytes (0x00) can be transmitted."""
    async with async_create_reader_writer(loopback_adapter, baudrate=115200) as (
        reader,
        writer,
    ):
        # Send a bunch of null bytes
        null_data = b"\x00" * 64

        writer.write(null_data)
        result = await reader.readexactly(len(null_data))

        assert result == null_data


async def test_overlapping_read_write_loopback_async(loopback_adapter: str) -> None:
    """Test that read and write can overlap, data is buffered."""
    async with async_create_reader_writer(loopback_adapter, baudrate=115200) as (
        reader,
        writer,
    ):
        data = bytes(range(256))
        read = b""

        writer.write(data[:100])
        read += await reader.readexactly(10)
        writer.write(data[100:150])
        read += await reader.readexactly(10)
        writer.write(data[150:])
        read += await reader.readexactly(10)
        read += await reader.readexactly(256 - 30)

        assert read == data


@pytest.mark.parametrize(
    ("baudrate", "chunk_size"),
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
async def test_random_large_loopback_async(
    loopback_adapter: str, baudrate: int, chunk_size: int
) -> None:
    """Test random read/write at various speeds."""
    async with async_create_reader_writer(loopback_adapter, baudrate=baudrate) as (
        reader,
        writer,
    ):
        data = os.urandom(chunk_size)
        writer.write(data)

        read_data = await reader.readexactly(chunk_size)
        assert read_data == data


@pytest.mark.parametrize(
    "iterations",
    [16, 32, 64],
)
async def test_repeated_write_read_cycles_loopback_async(
    loopback_adapter: str, iterations: int
) -> None:
    """Test repeated write/read cycles."""
    async with async_create_reader_writer(loopback_adapter, baudrate=115200) as (
        reader,
        writer,
    ):
        data = bytes(range(256))

        for _i in range(iterations):
            writer.write(data)
            result = await reader.readexactly(len(data))
            assert result == data


async def test_buffered_writes_then_read_loopback_async(loopback_adapter: str) -> None:
    """Test multiple writes followed by a single read."""
    async with async_create_reader_writer(loopback_adapter, baudrate=115200) as (
        reader,
        writer,
    ):
        chunk = bytes(range(256))
        iterations = 4

        # Write multiple chunks
        for _ in range(iterations):
            writer.write(chunk)

        # Read all data back
        total_size = len(chunk) * iterations
        result = await reader.readexactly(total_size)

        # Verify all data was received correctly
        expected = chunk * iterations
        assert result == expected


@pytest.mark.parametrize(
    "payload_size",
    [1024, 2048],  # Kernel buffers are typically ~4KB, stay well below that
)
async def test_large_payload_loopback_async(
    loopback_adapter: str, payload_size: int
) -> None:
    """Test large payload transmission."""
    async with async_create_reader_writer(loopback_adapter, baudrate=921600) as (
        reader,
        writer,
    ):
        data = bytes([i % 256 for i in range(payload_size)])

        writer.write(data)
        result = await reader.readexactly(len(data))

        assert result == data


async def test_rapid_small_writes_loopback_async(loopback_adapter: str) -> None:
    """Test rapid succession of small writes."""
    async with async_create_reader_writer(loopback_adapter, baudrate=115200) as (
        reader,
        writer,
    ):
        # Send many small writes
        iterations = 256
        received = bytearray()

        for i in range(iterations):
            data = bytes([i % 256])
            writer.write(data)
            result = await reader.readexactly(1)
            received.extend(result)

        # Verify all bytes were received in order
        expected = bytes([i % 256 for i in range(iterations)])
        assert bytes(received) == expected


@pytest.mark.parametrize(
    ("baudrate", "iterations"),
    [
        (9600, 8),
        (115200, 64),
        (921600, 128),  # Reduced from 512 for hardware loopback adapters
    ],
)
async def test_sustained_throughput_loopback_async(
    loopback_adapter: str, baudrate: int, iterations: int
) -> None:
    """Test sustained data throughput at various baudrates."""
    async with async_create_reader_writer(loopback_adapter, baudrate=baudrate) as (
        reader,
        writer,
    ):
        chunk = os.urandom(1024)

        for _ in range(iterations):
            writer.write(chunk)
            result = await reader.readexactly(len(chunk))
            assert result == chunk


@pytest.mark.parametrize(
    "baudrate",
    [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600],
)
async def test_valid_baudrates_loopback_async(
    loopback_adapter: str, baudrate: int
) -> None:
    """Test that valid baudrates are accepted."""
    async with async_create_reader_writer(loopback_adapter, baudrate=baudrate) as (
        reader,
        writer,
    ):
        # Verify baudrate was set (check transport)
        transport = cast(SerialTransport, writer.transport)
        assert transport.baudrate == baudrate
        writer.write(b"test")


@pytest.mark.parametrize(
    "parity",
    [Parity.NONE, Parity.ODD, Parity.EVEN, Parity.MARK, Parity.SPACE],
)
async def test_valid_parity_loopback_async(
    loopback_adapter: str, parity: Parity
) -> None:
    """Test that valid parity settings are accepted."""
    async with async_create_reader_writer(
        loopback_adapter, baudrate=115200, parity=parity
    ) as (
        reader,
        writer,
    ):
        transport = cast(SerialTransport, writer.transport)
        assert transport.parity == parity
        writer.write(b"test")


@pytest.mark.parametrize(
    ("stopbits", "expected"),
    [
        (StopBits.ONE, StopBits.ONE),
        pytest.param(
            StopBits.ONE_POINT_FIVE,
            StopBits.ONE_POINT_FIVE,
            marks=pytest.mark.xfail(reason="Not all drivers support 1.5 stop bits"),
        ),
        (StopBits.TWO, StopBits.TWO),
        (1, StopBits.ONE),
        pytest.param(
            1.5,
            StopBits.ONE_POINT_FIVE,
            marks=pytest.mark.xfail(reason="Not all drivers support 1.5 stop bits"),
        ),
        (2, StopBits.TWO),
    ],
)
async def test_valid_stopbits_loopback_async(
    loopback_adapter: str,
    stopbits: StopBits | int | float,
    expected: StopBits,
) -> None:
    """Test that valid stopbits settings are accepted."""
    async with async_create_reader_writer(
        loopback_adapter, baudrate=115200, stopbits=stopbits
    ) as (
        reader,
        writer,
    ):
        transport = cast(SerialTransport, writer.transport)
        assert transport.stopbits == expected
        writer.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
async def test_valid_byte_size_loopback_async(
    loopback_adapter: str, byte_size: int
) -> None:
    """Test that valid byte sizes are accepted."""
    async with async_create_reader_writer(
        loopback_adapter, baudrate=115200, byte_size=byte_size
    ) as (
        reader,
        writer,
    ):
        writer.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
async def test_xonxoff_setting_loopback_async(
    loopback_adapter: str, xonxoff: bool
) -> None:
    """Test that xonxoff setting is accepted."""
    async with async_create_reader_writer(
        loopback_adapter, baudrate=115200, xonxoff=xonxoff
    ) as (
        reader,
        writer,
    ):
        writer.write(b"test")


@pytest.mark.parametrize(
    "rtscts",
    [True, False],
)
async def test_rtscts_setting_loopback_async(
    loopback_adapter: str, rtscts: bool
) -> None:
    """Test that rtscts setting is accepted."""
    async with async_create_reader_writer(
        loopback_adapter, baudrate=115200, rtscts=rtscts
    ) as (
        reader,
        writer,
    ):
        writer.write(b"test")


async def test_read_with_timeout_loopback_async(loopback_adapter: str) -> None:
    """Test reading with timeout."""
    async with async_create_reader_writer(loopback_adapter, baudrate=115200) as (
        reader,
        writer,
    ):
        # Write some data
        writer.write(b"test")

        # Read with timeout should succeed
        result = await asyncio.wait_for(reader.readexactly(4), timeout=1.0)
        assert result == b"test"

        # Reading without data should timeout
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.readexactly(1), timeout=0.1)


async def test_set_modem_pins_loopback_async(loopback_adapter: str) -> None:
    """Test setting modem control bits with loopback adapter."""
    async with async_create_reader_writer(loopback_adapter, baudrate=115200) as (
        reader,
        writer,
    ):
        transport = cast(SerialTransport, writer.transport)
        # With a real loopback adapter, modem control signals should work
        await transport.set_modem_pins(dtr=True, rts=True)
        modem_pins = await transport.get_modem_pins()
        assert modem_pins.dtr is PinState.HIGH
        assert modem_pins.rts is PinState.HIGH

        # Set DTR low, leave RTS unchanged
        await transport.set_modem_pins(dtr=False)
        modem_pins = await transport.get_modem_pins()
        assert modem_pins.dtr is PinState.LOW
        assert modem_pins.rts is PinState.HIGH

        # Set both low
        await transport.set_modem_pins(dtr=False, rts=False)
        modem_pins = await transport.get_modem_pins()
        assert modem_pins.dtr is PinState.LOW
        assert modem_pins.rts is PinState.LOW
