"""Async transport tests."""

import asyncio
import contextlib
import logging
import os
import sys

if sys.version_info >= (3, 11):
    from asyncio import timeout as asyncio_timeout
else:
    from async_timeout import timeout as asyncio_timeout

import pytest

from serialx import (
    BaseSerialTransport,
    ModemPins,
    Parity,
    PinState,
    StopBits,
    create_serial_connection,
    get_serial_classes,
)
from tests.common import (
    SOCAT_BINARY,
    SerialPair,
    async_create_bridged_socat_pair,
    async_create_reader_writer,
    async_create_reader_writer_pair,
)

LOGGER = logging.getLogger(__name__)


# --- Data transmission ---


async def test_async_all_bytes(serial_pair: SerialPair) -> None:
    """Test that all bytes 0-255 can be transmitted."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
        data = bytes(range(256))
        writer_left.write(data)
        result = await reader_right.readexactly(len(data))
        assert result == data


async def test_async_segmented_binary_data(serial_pair: SerialPair) -> None:
    """Test binary data sent in segments."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
        segment_size = 16
        data = bytes(range(256))

        for i in range(0, 256, segment_size):
            segment = data[i : i + segment_size]
            writer_left.write(segment)
            result = await reader_right.readexactly(len(segment))
            assert result == segment


@pytest.mark.parametrize("size", [1, 16, 64, 256, 512, 1024])
async def test_async_binary_payload_sizes(serial_pair: SerialPair, size: int) -> None:
    """Test various binary payload sizes."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
        data = bytes([i % 256 for i in range(size)])
        writer_left.write(data)
        result = await reader_right.readexactly(len(data))
        assert result == data


async def test_async_null_bytes(serial_pair: SerialPair) -> None:
    """Test that null bytes (0x00) can be transmitted."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
        null_data = b"\x00" * 64
        writer_left.write(null_data)
        result = await reader_right.readexactly(len(null_data))
        assert result == null_data


async def test_async_overlapping_read_write(serial_pair: SerialPair) -> None:
    """Test that read and write can overlap, data is buffered."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
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
    serial_pair: SerialPair, baudrate: int, chunk_size: int
) -> None:
    """Test random read/write at various speeds."""
    if (
        baudrate > 230400
        and sys.platform == "darwin"
        and serial_pair.serial_class in ("PosixSerial", "ExtendedPosixSerial")
    ):
        pytest.xfail("macOS termios lacks constants above B230400")

    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=baudrate
    ) as (_, writer_left, reader_right, _):
        data = os.urandom(chunk_size)
        writer_left.write(data)
        read_data = await reader_right.readexactly(chunk_size)
        assert read_data == data


@pytest.mark.parametrize("iterations", [16, 32, 64])
async def test_async_repeated_write_read_cycles(
    serial_pair: SerialPair, iterations: int
) -> None:
    """Test repeated write/read cycles."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
        data = bytes(range(256))

        for _ in range(iterations):
            writer_left.write(data)
            result = await reader_right.readexactly(len(data))
            assert result == data


async def test_async_buffered_writes_then_read(serial_pair: SerialPair) -> None:
    """Test multiple writes followed by a single read."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
        chunk = bytes(range(256))
        iterations = 4

        for _ in range(iterations):
            writer_left.write(chunk)

        total_size = len(chunk) * iterations
        result = await reader_right.readexactly(total_size)
        expected = chunk * iterations
        assert result == expected


@pytest.mark.parametrize("payload_size", [1024, 2048])
async def test_async_large_payload(serial_pair: SerialPair, payload_size: int) -> None:
    """Test large payload transmission."""
    if sys.platform == "darwin" and serial_pair.serial_class in (
        "PosixSerial",
        "ExtendedPosixSerial",
    ):
        pytest.xfail("macOS termios lacks constants above B230400")

    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=921600
    ) as (_, writer_left, reader_right, _):
        data = bytes([i % 256 for i in range(payload_size)])
        writer_left.write(data)
        result = await reader_right.readexactly(len(data))
        assert result == data


async def test_async_rapid_small_writes(serial_pair: SerialPair) -> None:
    """Test rapid succession of small writes."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
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
    serial_pair: SerialPair, baudrate: int, iterations: int
) -> None:
    """Test sustained data throughput at various baudrates."""
    if (
        baudrate > 230400
        and sys.platform == "darwin"
        and serial_pair.serial_class in ("PosixSerial", "ExtendedPosixSerial")
    ):
        pytest.xfail("macOS termios lacks constants above B230400")

    if (serial_pair.backend, baudrate, iterations) == ("esphome", 921600, 512):
        pytest.skip(
            "ESPHome backend is too slow for sustained throughput at 921600/512"
        )

    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=baudrate
    ) as (_, writer_left, reader_right, _):
        chunk = os.urandom(1024)
        for _ in range(iterations):
            writer_left.write(chunk)
            result = await reader_right.readexactly(len(chunk))
            assert result == chunk


# --- Configuration ---


@pytest.mark.parametrize(
    "baudrate", [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
)
async def test_async_valid_baudrates(serial_pair: SerialPair, baudrate: int) -> None:
    """Test that valid baudrates are accepted."""
    if (
        baudrate > 230400
        and sys.platform == "darwin"
        and serial_pair.serial_class in ("PosixSerial", "ExtendedPosixSerial")
    ):
        pytest.xfail("macOS termios lacks constants above B230400")

    async with async_create_reader_writer(serial_pair.left, baudrate=baudrate) as (
        _,
        writer,
    ):
        assert writer.transport.baudrate == baudrate
        writer.write(b"test")


@pytest.mark.parametrize(
    "parity", [Parity.NONE, Parity.ODD, Parity.EVEN, Parity.MARK, Parity.SPACE]
)
async def test_async_valid_parity(serial_pair: SerialPair, parity: Parity) -> None:
    """Test that valid parity settings are accepted."""
    if serial_pair.backend == "esphome" and parity in (Parity.MARK, Parity.SPACE):
        pytest.xfail("ESPHome backend does not support MARK/SPACE parity")

    if serial_pair.serial_class not in ("LinuxSerial", "Win32Serial") and parity in (
        Parity.MARK,
        Parity.SPACE,
    ):
        pytest.skip("MARK/SPACE parity requires CMSPAR (Linux) or Win32")

    async with async_create_reader_writer(
        serial_pair.left, baudrate=115200, parity=parity
    ) as (_, writer):
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
    serial_pair: SerialPair,
    stopbits: StopBits | int | float,
    expected: StopBits,
) -> None:
    """Test that valid stopbits settings are accepted."""
    if serial_pair.backend == "esphome" and expected is StopBits.ONE_POINT_FIVE:
        pytest.xfail("ESPHome backend does not support 1.5 stop bits")

    if (
        serial_pair.serial_class not in ("Win32Serial",)
        and expected is StopBits.ONE_POINT_FIVE
    ):
        pytest.skip("1.5 stop bits only supported on Win32")

    async with async_create_reader_writer(
        serial_pair.left, baudrate=115200, stopbits=stopbits
    ) as (_, writer):
        assert writer.transport.stopbits == expected
        writer.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
async def test_async_valid_byte_size(serial_pair: SerialPair, byte_size: int) -> None:
    """Test that valid byte sizes are accepted."""
    async with async_create_reader_writer(
        serial_pair.left, baudrate=115200, byte_size=byte_size
    ) as (_, writer):
        writer.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
async def test_async_xonxoff_setting(serial_pair: SerialPair, xonxoff: bool) -> None:
    """Test that xonxoff setting is accepted."""
    async with async_create_reader_writer(
        serial_pair.left, baudrate=115200, xonxoff=xonxoff
    ) as (_, writer):
        writer.write(b"test")


@pytest.mark.parametrize("rtscts", [True, False])
async def test_async_rtscts_setting(serial_pair: SerialPair, rtscts: bool) -> None:
    """Test that rtscts setting is accepted."""
    if rtscts and serial_pair.serial_class == "PosixSerial":
        pytest.xfail("Strict POSIX backend does not support RTS/CTS flow control")

    async with async_create_reader_writer(serial_pair.right, baudrate=115200):
        async with async_create_reader_writer(
            serial_pair.left, baudrate=115200, rtscts=rtscts
        ) as (_, writer):
            writer.write(b"test")


# --- Lifecycle ---


async def test_async_concurrent_writes(serial_pair: SerialPair) -> None:
    """Test concurrent writes from multiple tasks."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):

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


async def test_async_read_with_timeout(serial_pair: SerialPair) -> None:
    """Test reading with timeout."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
        writer_left.write(b"test")

        result = await asyncio.wait_for(reader_right.readexactly(4), timeout=1.0)
        assert result == b"test"

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader_right.readexactly(1), timeout=0.1)


async def test_async_close_is_idempotent(serial_pair: SerialPair) -> None:
    """Test closing writer multiple times is safe and drains buffer state."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, _, _):
        writer_left.close()
        await writer_left.wait_closed()
        assert writer_left.transport.get_write_buffer_size() == 0

        # Second close should be no-op
        writer_left.close()
        await writer_left.wait_closed()


@pytest.mark.skip_backends("esphome")
async def test_async_pause_resume(serial_pair: SerialPair) -> None:
    """Test transport pause and resume."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (reader_left, writer_left, _, writer_right):
        writer_left.transport.pause_reading()

        writer_right.write(b"A long message")
        await writer_right.drain()

        # Nothing can be read
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio_timeout(1):
                await reader_left.read(1)

        writer_left.transport.resume_reading()
        assert (await reader_left.read(14)) == b"A long message"


async def test_async_abort(serial_pair: SerialPair) -> None:
    """Test aborting a transport discards buffered data and closes."""
    connection_lost_event = asyncio.Event()

    class AbortProtocol(asyncio.Protocol):
        def connection_lost(self, exc: Exception | None) -> None:
            connection_lost_event.set()

    transport, _ = await create_serial_connection(
        asyncio.get_running_loop(),
        AbortProtocol,
        serial_pair.left,
        baudrate=115200,
    )

    transport.write(b"data that will be discarded on abort")
    transport.abort()

    await asyncio.wait_for(connection_lost_event.wait(), timeout=2.0)
    await asyncio.wait_for(transport.wait_closed(), timeout=2.0)
    assert transport.is_closing()


async def test_async_close_before_connect(serial_pair: SerialPair) -> None:
    """Test close during connect does not crash and stays idempotent."""

    class ProbeProtocol(asyncio.Protocol):
        def __init__(self) -> None:
            self.connection_made_calls = 0
            self.connection_lost_calls = 0

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            self.connection_made_calls += 1

        def connection_lost(self, exc: Exception | None) -> None:
            self.connection_lost_calls += 1

    loop = asyncio.get_running_loop()
    _serial_cls, transport_cls = get_serial_classes(serial_pair.left)
    protocol = ProbeProtocol()
    transport = transport_cls(loop=loop, protocol=protocol)

    connect_task = asyncio.create_task(
        transport.connect(path=serial_pair.left, baudrate=115200)
    )
    await asyncio.sleep(0)
    transport.close()
    transport.close()

    with contextlib.suppress(Exception):
        await asyncio.wait_for(connect_task, timeout=2.0)

    await asyncio.wait_for(transport.wait_closed(), timeout=2.0)

    assert transport.is_closing()
    assert protocol.connection_made_calls <= 1
    assert protocol.connection_lost_calls <= 1


async def test_async_write_bytearray(serial_pair: SerialPair) -> None:
    """Test writing bytearray data."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
        data = bytearray(b"hello bytearray")
        writer_left.write(data)
        result = await reader_right.readexactly(len(data))
        assert result == b"hello bytearray"


async def test_async_write_empty(serial_pair: SerialPair) -> None:
    """Test writing empty data is a no-op."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
        writer_left.write(b"")
        writer_left.write(b"after_empty")
        result = await reader_right.readexactly(len(b"after_empty"))
        assert result == b"after_empty"


async def test_async_transport_api(serial_pair: SerialPair) -> None:
    """Test transport public API methods."""
    async with async_create_reader_writer(serial_pair.left, baudrate=115200) as (
        _,
        writer,
    ):
        transport = writer.transport

        # get/set protocol
        protocol = transport.get_protocol()
        assert protocol is not None
        transport.set_protocol(protocol)
        assert transport.get_protocol() is protocol

        # write buffer size starts at 0
        assert transport.get_write_buffer_size() == 0


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Only DescriptorTransport implements write buffer limits",
)
@pytest.mark.skip_backends("socket", "esphome")
async def test_async_transport_write_buffer_limits(serial_pair: SerialPair) -> None:
    """Test get/set write buffer limits and can_write_eof."""

    async with async_create_reader_writer(serial_pair.left, baudrate=115200) as (
        _,
        writer,
    ):
        transport = writer.transport

        low, high = transport.get_write_buffer_limits()
        assert low >= 0
        assert high >= low

        transport.set_write_buffer_limits(high=128 * 1024, low=32 * 1024)
        assert transport.get_write_buffer_limits() == (32 * 1024, 128 * 1024)

        assert transport.can_write_eof() is True


async def test_async_flush(serial_pair: SerialPair) -> None:
    """Test flushing async transport write buffers."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, reader_right, _):
        writer_left.write(b"flush test data")
        await writer_left.transport.flush()

        result = await reader_right.readexactly(len(b"flush test data"))
        assert result == b"flush test data"


@pytest.mark.skip_backends("esphome")
async def test_async_resume_reading_when_not_paused(serial_pair: SerialPair) -> None:
    """Test that resume_reading when not paused is a no-op."""
    async with async_create_reader_writer_pair(
        serial_pair.left, serial_pair.right, baudrate=115200
    ) as (_, writer_left, _, _):
        # resume without prior pause should be a no-op
        writer_left.transport.resume_reading()


@pytest.mark.skipif(not SOCAT_BINARY, reason="socat binary is missing")
async def test_async_peer_close_triggers_connection_lost() -> None:
    """Test that killing one socat process triggers connection_lost on the other."""
    async with async_create_bridged_socat_pair() as pair:
        connection_lost_event = asyncio.Event()

        class Receiver(asyncio.Protocol):
            def connection_lost(self, exc: Exception | None) -> None:
                connection_lost_event.set()

        loop = asyncio.get_running_loop()

        recv_transport, _ = await create_serial_connection(
            loop, Receiver, pair.left, baudrate=115200
        )
        send_transport, _ = await create_serial_connection(
            loop, asyncio.Protocol, pair.right, baudrate=115200
        )

        send_transport.write(b"goodbye")

        # Kill the right-side socat process; this tears down the bridge
        # and causes EOF on the left side
        pair.right_process.terminate()
        await pair.right_process.wait()

        await asyncio.wait_for(connection_lost_event.wait(), timeout=5.0)

        send_transport.close()

        if not recv_transport.is_closing():
            recv_transport.close()


async def test_async_invalid_uri() -> None:
    """Test invalid URIs are rejected by public async API."""
    loop = asyncio.get_running_loop()

    with pytest.raises(ValueError, match="expected both host and port"):
        await create_serial_connection(
            loop,
            asyncio.Protocol,
            "socket://127.0.0.1",
            baudrate=115200,
        )


async def test_async_get_modem_pins(serial_pair: SerialPair) -> None:
    """Test reading modem control bits."""
    async with async_create_reader_writer(serial_pair.left, baudrate=115200) as (
        _,
        writer,
    ):
        modem_pins = await writer.transport.get_modem_pins()
        assert isinstance(modem_pins, ModemPins)
        for field in ["le", "dtr", "rts", "st", "sr", "cts", "car", "rng", "dsr"]:
            value = getattr(modem_pins, field)
            assert value in (PinState.HIGH, PinState.LOW, PinState.UNDEFINED)


async def test_async_set_modem_pins_api(serial_pair: SerialPair) -> None:
    """Test modem pin writes are accepted on all backends."""

    async with async_create_reader_writer(serial_pair.left, baudrate=115200) as (
        _,
        writer,
    ):
        await writer.transport.set_modem_pins(dtr=True, rts=True)
        pins_high = await writer.transport.get_modem_pins()

        await writer.transport.set_modem_pins(dtr=False, rts=False)
        pins_low = await writer.transport.get_modem_pins()

        for pins in (pins_high, pins_low):
            assert isinstance(pins, ModemPins)
            for field in ["le", "dtr", "rts", "st", "sr", "cts", "car", "rng", "dsr"]:
                value = getattr(pins, field)
                assert value in (PinState.HIGH, PinState.LOW, PinState.UNDEFINED)

        if (
            pins_high.dtr is not PinState.UNDEFINED
            and pins_low.dtr is not PinState.UNDEFINED
        ):
            assert pins_high.dtr is PinState.HIGH
            assert pins_low.dtr is PinState.LOW

        if (
            pins_high.rts is not PinState.UNDEFINED
            and pins_low.rts is not PinState.UNDEFINED
        ):
            assert pins_high.rts is PinState.HIGH
            assert pins_low.rts is PinState.LOW


@pytest.mark.skipif(
    sys.platform == "win32", reason="GetCommModemStatus cannot read back DTR/RTS"
)
@pytest.mark.skip_backends("socket", "socat")
async def test_async_set_modem_pins(serial_pair: SerialPair) -> None:
    """Test setting modem control bits and verifying readback."""

    async with async_create_reader_writer(serial_pair.left, baudrate=115200) as (
        _,
        writer,
    ):
        await writer.transport.set_modem_pins(dtr=True, rts=True)
        modem_pins = await writer.transport.get_modem_pins()
        assert modem_pins.dtr is PinState.HIGH
        assert modem_pins.rts is PinState.HIGH

        await writer.transport.set_modem_pins(dtr=False)
        modem_pins = await writer.transport.get_modem_pins()
        assert modem_pins.dtr is PinState.LOW
        assert modem_pins.rts is PinState.HIGH

        await writer.transport.set_modem_pins(dtr=False, rts=False)
        modem_pins = await writer.transport.get_modem_pins()
        assert modem_pins.dtr is PinState.LOW
        assert modem_pins.rts is PinState.LOW


# --- Backpressure ---
# These tests use a dedicated async socket relay with read delay to reliably
# trigger backpressure conditions.


@pytest.mark.skip_backends("socket", "com0com", "socat", "esphome")
async def test_async_backpressure_callbacks(serial_pair: SerialPair) -> None:
    """Test backpressure pause/resume callbacks through public async APIs."""

    output_pause_count = 0
    output_resume_count = 0

    loop = asyncio.get_running_loop()
    input_lost = loop.create_future()
    output_lost = loop.create_future()

    class Input(asyncio.Protocol):
        def data_received(self, data: bytes) -> None:
            return

        def connection_lost(self, exc: Exception | None) -> None:
            if not input_lost.done():
                input_lost.set_result(None)

    class Output(asyncio.Protocol):
        _transport: BaseSerialTransport | None = None

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            assert isinstance(transport, BaseSerialTransport)
            self._transport = transport

        def pause_writing(self) -> None:
            nonlocal output_pause_count
            output_pause_count += 1

        def resume_writing(self) -> None:
            nonlocal output_resume_count
            output_resume_count += 1

        def connection_lost(self, exc: Exception | None) -> None:
            if not output_lost.done():
                output_lost.set_result(None)

    in_transport, _ = await create_serial_connection(
        loop, Input, serial_pair.left, baudrate=115200
    )
    out_transport, _ = await create_serial_connection(
        loop, Output, serial_pair.right, baudrate=115200
    )
    await asyncio.sleep(0.1)

    out_transport.set_write_buffer_limits(high=1024, low=256)

    # Write enough to overflow the kernel buffer (~4KB for most serial drivers)
    # so that the userspace buffer exceeds `high` and triggers pause_writing.
    payload = b"X" * 8192
    for _ in range(4):
        if out_transport.is_closing():
            break
        out_transport.write(payload)
        await asyncio.sleep(0)

    assert output_pause_count > 0

    assert out_transport.get_write_buffer_size() > 0
    await out_transport.flush()
    assert out_transport.get_write_buffer_size() == 0

    assert output_resume_count > 0

    out_transport.close()
    in_transport.close()
    await asyncio.gather(input_lost, output_lost)


@pytest.mark.skip_backends("socket", "com0com")
async def test_async_backpressure_writer_removal(serial_pair: SerialPair) -> None:
    """Test that large writes with backpressure are handled correctly.

    This test catches three issue categories:
    1. AssertionError from writer not being removed when buffer empties
    2. Deadlock (via timeout) from direct writes blocking indefinitely
    3. Timing failures from writer not being added when buffering data
    Source: https://github.com/home-assistant-libs/pyserial-asyncio-fast/pull/36
    """

    TEXT = b"Hello, World!"
    COUNT = 8 * 1024
    output_resume_event = asyncio.Event()
    data_received_count = 0

    class Input(asyncio.Protocol):
        _transport: BaseSerialTransport

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            assert isinstance(transport, BaseSerialTransport)
            self._transport = transport

        def data_received(self, data: bytes) -> None:
            nonlocal data_received_count
            data_received_count += len(data)
            self._transport.write(data)

    class Output(asyncio.Protocol):
        _transport: BaseSerialTransport

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            assert isinstance(transport, BaseSerialTransport)
            self._transport = transport
            output_resume_event.set()

        def pause_writing(self) -> None:
            output_resume_event.clear()

        def resume_writing(self) -> None:
            output_resume_event.set()

    loop = asyncio.get_running_loop()

    in_transport, _ = await create_serial_connection(
        loop, Input, serial_pair.left, baudrate=115200
    )
    out_transport, _ = await create_serial_connection(
        loop, Output, serial_pair.right, baudrate=115200
    )

    await asyncio.sleep(0.1)

    try:
        for _ in range(COUNT):
            try:
                async with asyncio_timeout(10):
                    await output_resume_event.wait()
            except asyncio.TimeoutError:
                if out_transport.is_closing():
                    break
                raise

            out_transport.write(TEXT)

        async with asyncio_timeout(10):
            while out_transport.get_write_buffer_size() > 0:
                await asyncio.sleep(0.1)

        for _ in range(50):
            if data_received_count > 0:
                break
            await asyncio.sleep(0.1)

        assert data_received_count > 0
    finally:
        out_transport.close()
        in_transport.close()


# --- Adapter-specific tests ---
# These tests require physical adapter pairs (com0com, real hardware)
# and verify cross-port behavior that virtual backends can't emulate.


async def test_async_fast_open_close(serial_pair: SerialPair) -> None:
    """Test quickly opening and closing a port doesn't crash."""
    connection_lost_event = asyncio.Event()

    class FastCloseProtocol(asyncio.Protocol):
        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            assert isinstance(transport, BaseSerialTransport)
            transport.write(b"data that will be discarded on abort")
            transport.abort()

        def connection_lost(self, exc: Exception | None) -> None:
            connection_lost_event.set()

    transport, _ = await create_serial_connection(
        asyncio.get_running_loop(),
        FastCloseProtocol,
        serial_pair.left,
        baudrate=115200,
    )

    await connection_lost_event.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="CloseHandle resets modem signals")
@pytest.mark.require_backends("adapter")
async def test_async_deassert_on_open(serial_pair: SerialPair) -> None:
    """Test DTR/CTS deassertion on open."""

    async with async_create_reader_writer(serial_pair.left, baudrate=115200) as (
        reader_left,
        writer_left,
    ):
        async with async_create_reader_writer(
            serial_pair.right,
            baudrate=115200,
            rtsdtr_on_open=PinState.HIGH,
            rtsdtr_on_close=PinState.HIGH,
        ) as (reader_right, writer_right):
            await writer_right.transport.set_modem_pins(dtr=True)
            await asyncio.sleep(0.05)
            assert (await writer_left.transport.get_modem_pins()).cts is PinState.HIGH

        await asyncio.sleep(0.05)
        assert (await writer_left.transport.get_modem_pins()).cts is PinState.HIGH

        async with async_create_reader_writer(
            serial_pair.right,
            baudrate=115200,
            rtsdtr_on_open=PinState.LOW,
            rtsdtr_on_close=PinState.HIGH,
        ) as (reader_right, writer_right):
            await asyncio.sleep(0.05)
            assert (await writer_left.transport.get_modem_pins()).cts is PinState.LOW
            await writer_right.transport.set_modem_pins(dtr=True)

        await asyncio.sleep(0.05)
        assert (await writer_left.transport.get_modem_pins()).cts is PinState.HIGH


@pytest.mark.skipif(sys.platform == "win32", reason="CloseHandle resets modem signals")
@pytest.mark.require_backends("adapter")
async def test_async_hang_up_on_close(serial_pair: SerialPair) -> None:
    """Test DTR/CTS hang up on close."""

    async with async_create_reader_writer(serial_pair.left, baudrate=115200) as (
        reader_left,
        writer_left,
    ):
        async with async_create_reader_writer(
            serial_pair.right,
            baudrate=115200,
            rtsdtr_on_close=PinState.HIGH,
            rtsdtr_on_open=PinState.HIGH,
        ) as (reader_right, writer_right):
            await writer_right.transport.set_modem_pins(dtr=True)
            await asyncio.sleep(0.05)
            assert (await writer_left.transport.get_modem_pins()).cts is PinState.HIGH

        await asyncio.sleep(0.05)
        assert (await writer_left.transport.get_modem_pins()).cts is PinState.HIGH

        async with async_create_reader_writer(
            serial_pair.right,
            baudrate=115200,
            rtsdtr_on_close=PinState.HIGH,
            rtsdtr_on_open=PinState.HIGH,
        ) as (reader_right, writer_right):
            await asyncio.sleep(0.05)
            assert (await writer_left.transport.get_modem_pins()).cts is PinState.HIGH

        await asyncio.sleep(0.05)
        assert (await writer_left.transport.get_modem_pins()).cts is PinState.HIGH

        async with async_create_reader_writer(
            serial_pair.right,
            baudrate=115200,
            rtsdtr_on_close=PinState.LOW,
            rtsdtr_on_open=PinState.HIGH,
        ) as (reader_right, writer_right):
            await asyncio.sleep(0.05)
            assert (await writer_left.transport.get_modem_pins()).cts is PinState.HIGH

        await asyncio.sleep(0.05)
        assert (await writer_left.transport.get_modem_pins()).cts is PinState.LOW


@pytest.mark.skipif(sys.platform == "win32", reason="CloseHandle resets modem signals")
@pytest.mark.require_backends("adapter")
@pytest.mark.parametrize(
    ("rtscts", "rtsdtr_on_open", "expected_state"),
    [
        (False, PinState.HIGH, PinState.HIGH),
        (False, PinState.LOW, PinState.LOW),
        (True, PinState.HIGH, PinState.HIGH),
        (True, PinState.LOW, PinState.LOW),
    ],
)
async def test_async_deassert_on_open_with_rtscts(
    serial_pair: SerialPair,
    rtscts: bool,
    rtsdtr_on_open: PinState,
    expected_state: PinState,
) -> None:
    """Test interaction of rtsdtr_on_open with rtscts."""

    async with async_create_reader_writer(serial_pair.left, baudrate=115200) as (
        reader_left,
        writer_left,
    ):
        async with async_create_reader_writer(
            serial_pair.right,
            baudrate=115200,
            rtscts=False,
            rtsdtr_on_open=PinState.HIGH,
            rtsdtr_on_close=PinState.HIGH,
        ) as (reader_right, writer_right):
            await writer_right.transport.set_modem_pins(dtr=True)
            await asyncio.sleep(0.05)
            assert (await writer_left.transport.get_modem_pins()).cts is PinState.HIGH

        await asyncio.sleep(0.05)
        assert (await writer_left.transport.get_modem_pins()).cts is PinState.HIGH

        async with async_create_reader_writer(
            serial_pair.right,
            baudrate=115200,
            rtscts=rtscts,
            rtsdtr_on_open=rtsdtr_on_open,
        ) as (reader_right, writer_right):
            await asyncio.sleep(0.05)
            assert (await writer_left.transport.get_modem_pins()).cts is expected_state
