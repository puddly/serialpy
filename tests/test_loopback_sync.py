"""Test binary data transmission."""

import os

import pytest

from serialx import ModemBits, Parity, Serial, StopBits
from tests.common import LOOPBACK_ADAPTER

# All tests here use a real adapter, skip if not configured
pytestmark = pytest.mark.skipif(
    LOOPBACK_ADAPTER is None,
    reason="Loopback adapter port not set via SERIALX_LOOPBACK_PORT",
)


def test_all_bytes_loopback() -> None:
    """Test that all bytes 0-255 can be transmitted."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        # Create a byte array with all possible byte values
        data = bytes(range(256))

        serial.write(data)
        result = serial.readexactly(len(data))

        assert result == data


def test_segmented_binary_data_loopback() -> None:
    """Test binary data sent in segments."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        # Send all bytes in smaller segments
        segment_size = 16
        data = bytes(range(256))

        for i in range(0, 256, segment_size):
            segment = data[i : i + segment_size]
            serial.write(segment)
            result = serial.readexactly(len(segment))
            assert result == segment


@pytest.mark.parametrize(
    "size",
    [1, 16, 64, 256, 512, 1024],
)
def test_binary_payload_sizes_loopback(size: int) -> None:
    """Test various binary payload sizes."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        # Create binary data with repeating pattern
        data = bytes([i % 256 for i in range(size)])

        serial.write(data)
        result = serial.readexactly(len(data))

        assert result == data


def test_null_bytes_loopback() -> None:
    """Test that null bytes (0x00) can be transmitted."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        # Send a bunch of null bytes
        null_data = b"\x00" * 64

        serial.write(null_data)
        result = serial.readexactly(len(null_data))

        assert result == null_data


def test_overlapping_read_write_loopback() -> None:
    """Test that read and write can overlap, data is buffered."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        data = bytes(range(256))
        read = b""

        serial.write(data[:100])
        read += serial.readexactly(10)
        serial.write(data[100:150])
        read += serial.readexactly(10)
        serial.write(data[150:])
        read += serial.readexactly(10)
        read += serial.readexactly(256 - 30)

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
def test_random_large_loopback(baudrate: int, chunk_size: int) -> None:
    """Test loopback adapter random read/write."""
    with Serial(LOOPBACK_ADAPTER, baudrate=baudrate) as serial:
        data = os.urandom(chunk_size)
        serial.write(data)

        read_data = serial.readexactly(chunk_size)
        assert read_data == data


@pytest.mark.parametrize(
    "iterations",
    [16, 32, 64],
)
def test_repeated_write_read_cycles_loopback(iterations: int) -> None:
    """Test repeated write/read cycles."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        data = bytes(range(256))

        for _i in range(iterations):
            serial.write(data)
            result = serial.readexactly(len(data))
            assert result == data


def test_buffered_writes_then_read_loopback() -> None:
    """Test multiple writes followed by a single read."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        chunk = bytes(range(256))
        iterations = 4

        # Write multiple chunks
        for _ in range(iterations):
            serial.write(chunk)

        # Read all data back
        total_size = len(chunk) * iterations
        result = serial.readexactly(total_size)

        # Verify all data was received correctly
        expected = chunk * iterations
        assert result == expected


@pytest.mark.parametrize(
    "payload_size",
    [1024, 2048],  # Kernel buffers are typically ~4KB, stay well below that
)
def test_large_payload_loopback(payload_size: int) -> None:
    """Test large payload transmission."""
    with Serial(LOOPBACK_ADAPTER, baudrate=921600) as serial:
        data = bytes([i % 256 for i in range(payload_size)])

        serial.write(data)
        result = serial.readexactly(len(data))

        assert result == data


def test_rapid_small_writes_loopback() -> None:
    """Test rapid succession of small writes."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        # Send many small writes
        iterations = 256
        received = bytearray()

        for i in range(iterations):
            data = bytes([i % 256])
            serial.write(data)
            result = serial.readexactly(1)
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
def test_sustained_throughput_loopback(baudrate: int, iterations: int) -> None:
    """Test sustained data throughput at various baudrates."""
    with Serial(LOOPBACK_ADAPTER, baudrate=baudrate) as serial:
        chunk = os.urandom(1024)

        for _ in range(iterations):
            serial.write(chunk)
            result = serial.readexactly(len(chunk))
            assert result == chunk


@pytest.mark.parametrize(
    "baudrate",
    [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600],
)
def test_valid_baudrates_loopback(baudrate: int) -> None:
    """Test that valid baudrates are accepted."""
    with Serial(LOOPBACK_ADAPTER, baudrate=baudrate) as serial:
        assert serial.baudrate == baudrate
        serial.write(b"test")


@pytest.mark.parametrize(
    "parity",
    [Parity.NONE, Parity.ODD, Parity.EVEN, Parity.MARK, Parity.SPACE],
)
def test_valid_parity_loopback(parity: Parity) -> None:
    """Test that valid parity settings are accepted."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200, parity=parity) as serial:
        assert serial.parity == parity
        serial.write(b"test")


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
def test_valid_stopbits_loopback(
    stopbits: StopBits | int | float, expected: StopBits
) -> None:
    """Test that valid stopbits settings are accepted."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200, stopbits=stopbits) as serial:
        assert serial.stopbits == expected
        serial.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
def test_valid_byte_size_loopback(byte_size: int) -> None:
    """Test that valid byte sizes are accepted."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200, byte_size=byte_size) as serial:
        serial.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
def test_xonxoff_setting_loopback(xonxoff: bool) -> None:
    """Test that xonxoff setting is accepted."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200, xonxoff=xonxoff) as serial:
        serial.write(b"test")


@pytest.mark.parametrize(
    "rtscts",
    [True, False],
)
def test_rtscts_setting_loopback(rtscts: bool) -> None:
    """Test that rtscts setting is accepted."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200, rtscts=rtscts) as serial:
        serial.write(b"test")


def test_exclusive_loopback() -> None:
    """Test that exclusive setting is respected."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200, exclusive=True) as serial:
        assert serial.exclusive is True

        # Opening a second one will fail
        with pytest.raises(OSError):
            with Serial(LOOPBACK_ADAPTER, baudrate=115200, exclusive=True):
                pass


def test_exclusive_disabled_loopback() -> None:
    """Test that exclusive setting is respected."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200, exclusive=False) as serial1:
        assert serial1.exclusive is False

        with Serial(LOOPBACK_ADAPTER, baudrate=115200, exclusive=False) as serial2:
            assert serial2.exclusive is False
            serial2.write(b"test")

        assert serial1.readexactly(4) == b"test"


def test_context_manager_multiple_times_loopback() -> None:
    """Test that context manager can be used multiple times."""
    serial = Serial(LOOPBACK_ADAPTER, baudrate=115200)

    # First context
    with serial:
        serial.write(b"test1")
        result = serial.readexactly(5)
        assert result == b"test1"

    # Second context
    with serial:
        serial.write(b"test2")
        result = serial.readexactly(5)
        assert result == b"test2"


def test_open_close_cycles_loopback() -> None:
    """Test multiple open/close cycles."""
    serial = Serial(LOOPBACK_ADAPTER, baudrate=115200)

    # Cycle 1
    serial.open()
    serial.configure_port()
    serial.write(b"1")
    assert serial.readexactly(1) == b"1"
    serial.close()

    # Cycle 2
    serial.open()
    serial.configure_port()
    serial.write(b"2")
    assert serial.readexactly(1) == b"2"
    serial.close()

    # Cycle 3
    serial.open()
    serial.configure_port()
    serial.write(b"3")
    assert serial.readexactly(1) == b"3"
    serial.close()


def test_flush_after_write_loopback() -> None:
    """Test flushing after write operation."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        serial.flush()

        data = b"Test flush operation"
        serial.write(data)
        serial.flush()

        result = serial.readexactly(len(data))
        assert result == data


def test_multiple_flush_calls_loopback() -> None:
    """Test multiple consecutive flush calls."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        data = b""

        for i in range(5):
            chunk = f"Test data {i}".encode("ascii")
            data += chunk

            serial.write(chunk)
            serial.flush()

        result = serial.readexactly(len(data))
        assert result == data


def test_get_modem_bits_loopback() -> None:
    """Test reading modem control bits with loopback adapter."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        modem_bits = serial.get_modem_bits()

        # Verify we get a ModemBits object
        assert isinstance(modem_bits, ModemBits)

        # All modem bits should be either True, False, or None
        for field in ["le", "dtr", "rts", "st", "sr", "cts", "car", "rng", "dsr"]:
            value = getattr(modem_bits, field)
            assert value in (True, False, None)


def test_set_modem_bits_loopback() -> None:
    """Test setting modem control bits with loopback adapter."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        serial.set_modem_bits(dtr=True, rts=True)

        modem_bits = serial.get_modem_bits()
        assert modem_bits.dtr is True
        assert modem_bits.rts is True

        # Set DTR low, leave RTS unchanged
        serial.set_modem_bits(dtr=False)

        modem_bits = serial.get_modem_bits()
        assert modem_bits.dtr is False
        assert modem_bits.rts is True

        # Set both low
        serial.set_modem_bits(dtr=False, rts=False)

        modem_bits = serial.get_modem_bits()
        assert modem_bits.dtr is False
        assert modem_bits.rts is False


def test_deprecated_dtr_property_loopback() -> None:
    """Test DTR property (deprecated alias) with loopback adapter."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        serial.dtr = True
        assert serial.dtr is True

        serial.dtr = False
        assert serial.dtr is False


def test_deprecated_rts_property_loopback() -> None:
    """Test RTS property (deprecated alias) with loopback adapter."""
    with Serial(LOOPBACK_ADAPTER, baudrate=115200) as serial:
        serial.rts = True
        assert serial.rts is True

        serial.rts = False
        assert serial.rts is False
