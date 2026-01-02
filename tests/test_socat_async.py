"""Test async APIs with socat-created virtual PTY pairs."""

import asyncio
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
    SerialTransport,
    StopBits,
    create_serial_connection,
)
from tests.common import (
    SOCAT_BINARY,
    async_create_reader_writer,
    async_create_reader_writer_pair,
    async_create_socat_pair,
)

pytestmark = pytest.mark.skipif(
    not SOCAT_BINARY,
    reason="socat binary is missing",
)


async def test_all_bytes_async() -> None:
    """Test that all bytes 0-255 can be transmitted."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
    ):
        # Create a byte array with all possible byte values
        data = bytes(range(256))

        writer_left.write(data)
        result = await reader_right.readexactly(len(data))

        assert result == data


async def test_segmented_binary_data_async() -> None:
    """Test binary data sent in segments."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
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
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
    ):
        # Create binary data with repeating pattern
        data = bytes([i % 256 for i in range(size)])

        writer_left.write(data)
        result = await reader_right.readexactly(len(data))

        assert result == data


async def test_null_bytes_async() -> None:
    """Test that null bytes (0x00) can be transmitted."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
    ):
        # Send a bunch of null bytes
        null_data = b"\x00" * 64

        writer_left.write(null_data)
        result = await reader_right.readexactly(len(null_data))

        assert result == null_data


async def test_overlapping_read_write_async() -> None:
    """Test that read and write can overlap, data is buffered."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
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
        (921600, 1),
        (921600, 16),
        (921600, 256),
        (921600, 1024),
    ],
)
async def test_random_large_async(baudrate: int, chunk_size: int) -> None:
    """Test random read/write at various speeds."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=baudrate) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
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
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
    ):
        data = bytes(range(256))

        for _i in range(iterations):
            writer_left.write(data)
            result = await reader_right.readexactly(len(data))
            assert result == data


async def test_buffered_writes_then_read_async() -> None:
    """Test multiple writes followed by a single read."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
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
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=921600) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
    ):
        data = bytes([i % 256 for i in range(payload_size)])

        writer_left.write(data)
        result = await reader_right.readexactly(len(data))

        assert result == data


async def test_rapid_small_writes_async() -> None:
    """Test rapid succession of small writes."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
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
        (921600, 512),
    ],
)
async def test_sustained_throughput_async(baudrate: int, iterations: int) -> None:
    """Test sustained data throughput at various baudrates."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=baudrate) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
    ):
        chunk = os.urandom(1024)

        for _ in range(iterations):
            writer_left.write(chunk)
            result = await reader_right.readexactly(len(chunk))
            assert result == chunk


@pytest.mark.parametrize(
    "baudrate",
    [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600],
)
async def test_valid_baudrates_async(baudrate: int) -> None:
    """Test that valid baudrates are accepted."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer(left, baudrate=baudrate) as (reader, writer),
    ):
        # Verify baudrate was set (check transport)
        assert writer.transport.baudrate == baudrate
        writer.write(b"test")


@pytest.mark.parametrize(
    "parity",
    [Parity.NONE, Parity.ODD, Parity.EVEN, Parity.MARK, Parity.SPACE],
)
async def test_valid_parity_async(parity: Parity) -> None:
    """Test that valid parity settings are accepted."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer(left, baudrate=115200, parity=parity) as (
            reader,
            writer,
        ),
    ):
        assert writer.transport.parity == parity
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
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer(left, baudrate=115200, stopbits=stopbits) as (
            reader,
            writer,
        ),
    ):
        assert writer.transport.stopbits == expected
        writer.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
async def test_valid_byte_size_async(byte_size: int) -> None:
    """Test that valid byte sizes are accepted."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer(left, baudrate=115200, byte_size=byte_size) as (
            reader,
            writer,
        ),
    ):
        writer.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
async def test_xonxoff_setting_async(xonxoff: bool) -> None:
    """Test that xonxoff setting is accepted."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer(left, baudrate=115200, xonxoff=xonxoff) as (
            reader,
            writer,
        ),
    ):
        writer.write(b"test")


@pytest.mark.parametrize(
    "rtscts",
    [True, False],
)
async def test_rtscts_setting_async(rtscts: bool) -> None:
    """Test that rtscts setting is accepted."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer(left, baudrate=115200, rtscts=rtscts) as (
            reader,
            writer,
        ),
    ):
        writer.write(b"test")


async def test_concurrent_writes_async() -> None:
    """Test concurrent writes from multiple tasks."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
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
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
    ):
        # Write some data
        writer_left.write(b"test")

        # Read with timeout should succeed
        result = await asyncio.wait_for(reader_right.readexactly(4), timeout=1.0)
        assert result == b"test"

        # Reading without data should timeout
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader_right.readexactly(1), timeout=0.1)


async def test_get_modem_pins_async() -> None:
    """Test reading modem control bits."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer(left, baudrate=115200) as (reader, writer),
    ):
        modem_pins = await writer.transport.get_modem_pins()

        # Verify we get a ModemPins object
        assert isinstance(modem_pins, ModemPins)

        # All modem pins should be PinState enum values
        for field in ["le", "dtr", "rts", "st", "sr", "cts", "car", "rng", "dsr"]:
            value = getattr(modem_pins, field)
            assert value in (PinState.HIGH, PinState.LOW, PinState.UNDEFINED)


async def test_set_modem_pins_async() -> None:
    """Test setting modem control bits with socat pair."""
    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer(left, baudrate=115200) as (reader, writer),
    ):
        # Note: socat pairs don't support modem control signals properly
        # These calls should not raise errors, but values may be None
        await writer.transport.set_modem_pins(dtr=True, rts=True)
        modem_pins = await writer.transport.get_modem_pins()
        # Verify we get a ModemPins object, values may be None with socat
        assert isinstance(modem_pins, ModemPins)

        await writer.transport.set_modem_pins(dtr=False)
        modem_pins = await writer.transport.get_modem_pins()
        assert isinstance(modem_pins, ModemPins)

        await writer.transport.set_modem_pins(dtr=False, rts=False)
        modem_pins = await writer.transport.get_modem_pins()
        assert isinstance(modem_pins, ModemPins)


# Source: https://github.com/home-assistant-libs/pyserial-asyncio-fast/pull/36
async def test_remove_writer() -> None:
    """Test that large writes with backpressure are handled correctly.

    This test catches three issue categories:
    1. AssertionError from writer not being removed when buffer empties
    2. Deadlock (via timeout) from direct writes blocking indefinitely
    3. Timing failures from writer not being added when buffering data
    """
    TEXT = b"Hello, World!"
    COUNT = 8 * 1024
    output_resume_event = asyncio.Event()
    data_received_count = 0

    async with async_create_socat_pair() as (in_tty, out_tty):

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
            loop, Input, in_tty, baudrate=115200
        )
        out_transport, _ = await create_serial_connection(
            loop, Output, out_tty, baudrate=115200
        )

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

        out_transport.close()
        in_transport.close()


async def test_pause_resume() -> None:
    """Test transport pause and resume."""

    async with (
        async_create_socat_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
    ):
        writer_left.transport.pause_reading()

        writer_right.write(b"A long message")
        await writer_right.drain()

        # Nothing can be read
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio_timeout(1):
                await reader_left.read(1)

        writer_left.transport.resume_reading()
        assert (await reader_left.read(14)) == b"A long message"
