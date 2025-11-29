"""Test binary data transmission."""

import os

import pytest

from serialx import ModemPins, Parity, PinState, Serial, StopBits
from tests.common import SOCAT_BINARY, create_socat_pair

pytestmark = pytest.mark.skipif(
    not SOCAT_BINARY,
    reason="socat binary is missing",
)


def test_all_bytes_socat() -> None:
    """Test that all bytes 0-255 can be transmitted."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial_left,
        Serial(right, baudrate=115200) as serial_right,
    ):
        # Create a byte array with all possible byte values
        data = bytes(range(256))

        serial_left.write(data)
        result = serial_right.readexactly(len(data))

        assert result == data


def test_segmented_binary_data_socat() -> None:
    """Test binary data sent in segments."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial_left,
        Serial(right, baudrate=115200) as serial_right,
    ):
        # Send all bytes in smaller segments
        segment_size = 16
        data = bytes(range(256))

        for i in range(0, 256, segment_size):
            segment = data[i : i + segment_size]
            serial_left.write(segment)
            result = serial_right.readexactly(len(segment))
            assert result == segment


@pytest.mark.parametrize(
    "size",
    [1, 16, 64, 256, 512, 1024],
)
def test_binary_payload_sizes_socat(size: int) -> None:
    """Test various binary payload sizes."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial_left,
        Serial(right, baudrate=115200) as serial_right,
    ):
        # Create binary data with repeating pattern
        data = bytes([i % 256 for i in range(size)])

        serial_left.write(data)
        result = serial_right.readexactly(len(data))

        assert result == data


def test_null_bytes_socat() -> None:
    """Test that null bytes (0x00) can be transmitted."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial_left,
        Serial(right, baudrate=115200) as serial_right,
    ):
        # Send a bunch of null bytes
        null_data = b"\x00" * 64

        serial_left.write(null_data)
        result = serial_right.readexactly(len(null_data))

        assert result == null_data


def test_overlapping_read_write_socat() -> None:
    """Test that read and write can overlap, data is buffered."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial_left,
        Serial(right, baudrate=115200) as serial_right,
    ):
        data = bytes(range(256))
        read = b""

        serial_left.write(data[:100])
        read += serial_right.readexactly(10)
        serial_left.write(data[100:150])
        read += serial_right.readexactly(10)
        serial_left.write(data[150:])
        read += serial_right.readexactly(10)
        read += serial_right.readexactly(256 - 30)

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
def test_random_large_socat(baudrate: int, chunk_size: int) -> None:
    """Test loopback adapter random read/write."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=baudrate) as serial_left,
        Serial(right, baudrate=baudrate) as serial_right,
    ):
        data = os.urandom(chunk_size)
        serial_left.write(data)

        read_data = serial_right.readexactly(chunk_size)
        assert read_data == data


@pytest.mark.parametrize(
    "iterations",
    [16, 32, 64],
)
def test_repeated_write_read_cycles_socat(iterations: int) -> None:
    """Test repeated write/read cycles."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial_left,
        Serial(right, baudrate=115200) as serial_right,
    ):
        data = bytes(range(256))

        for _i in range(iterations):
            serial_left.write(data)
            result = serial_right.readexactly(len(data))
            assert result == data


def test_buffered_writes_then_read_socat() -> None:
    """Test multiple writes followed by a single read."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial_left,
        Serial(right, baudrate=115200) as serial_right,
    ):
        chunk = bytes(range(256))
        iterations = 4

        # Write multiple chunks
        for _ in range(iterations):
            serial_left.write(chunk)

        # Read all data back
        total_size = len(chunk) * iterations
        result = serial_right.readexactly(total_size)

        # Verify all data was received correctly
        expected = chunk * iterations
        assert result == expected


@pytest.mark.parametrize(
    "payload_size",
    [1024, 2048],  # Kernel buffers are typically ~4KB, stay well below that
)
def test_large_payload_socat(payload_size: int) -> None:
    """Test large payload transmission."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=921600) as serial_left,
        Serial(right, baudrate=921600) as serial_right,
    ):
        data = bytes([i % 256 for i in range(payload_size)])

        serial_left.write(data)
        result = serial_right.readexactly(len(data))

        assert result == data


def test_rapid_small_writes_socat() -> None:
    """Test rapid succession of small writes."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial_left,
        Serial(right, baudrate=115200) as serial_right,
    ):
        # Send many small writes
        iterations = 256
        received = bytearray()

        for i in range(iterations):
            data = bytes([i % 256])
            serial_left.write(data)
            result = serial_right.readexactly(1)
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
def test_sustained_throughput_socat(baudrate: int, iterations: int) -> None:
    """Test sustained data throughput at various baudrates."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=baudrate) as serial_left,
        Serial(right, baudrate=baudrate) as serial_right,
    ):
        chunk = os.urandom(1024)

        for _ in range(iterations):
            serial_left.write(chunk)
            result = serial_right.readexactly(len(chunk))
            assert result == chunk


@pytest.mark.parametrize(
    "baudrate",
    [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600],
)
def test_valid_baudrates_socat(baudrate: int) -> None:
    """Test that valid baudrates are accepted."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=baudrate) as serial,
    ):
        assert serial.baudrate == baudrate
        serial.write(b"test")


@pytest.mark.parametrize(
    "parity",
    [Parity.NONE, Parity.ODD, Parity.EVEN, Parity.MARK, Parity.SPACE],
)
def test_valid_parity_socat(parity: Parity) -> None:
    """Test that valid parity settings are accepted."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200, parity=parity) as serial,
    ):
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
def test_valid_stopbits_socat(
    stopbits: StopBits | int | float,
    expected: StopBits,
) -> None:
    """Test that valid stopbits settings are accepted."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200, stopbits=stopbits) as serial,
    ):
        assert serial.stopbits == expected
        serial.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
def test_valid_byte_size_socat(byte_size: int) -> None:
    """Test that valid byte sizes are accepted."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200, byte_size=byte_size) as serial,
    ):
        serial.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
def test_xonxoff_setting_socat(xonxoff: bool) -> None:
    """Test that xonxoff setting is accepted."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200, xonxoff=xonxoff) as serial,
    ):
        serial.write(b"test")


@pytest.mark.parametrize(
    "rtscts",
    [True, False],
)
def test_rtscts_setting_socat(rtscts: bool) -> None:
    """Test that rtscts setting is accepted."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200, rtscts=rtscts) as serial,
    ):
        serial.write(b"test")


def test_exclusive_socat() -> None:
    """Test that exclusive setting is respected."""
    with create_socat_pair() as (left, right):
        with Serial(left, baudrate=115200, exclusive=True) as serial:
            assert serial.exclusive is True

            # Opening a second one will fail
            with pytest.raises(OSError):
                with Serial(left, baudrate=115200, exclusive=True):
                    pass


def test_exclusive_disabled_socat() -> None:
    """Test that exclusive setting is respected."""
    with create_socat_pair() as (left, right):
        with Serial(left, baudrate=115200, exclusive=False) as serial1:
            assert serial1.exclusive is False

            # Can open the same port again without exclusive
            with Serial(left, baudrate=115200, exclusive=False) as serial2:
                assert serial2.exclusive is False
                # Both can write without error
                serial2.write(b"test")


def test_context_manager_multiple_times_socat() -> None:
    """Test that context manager can be used multiple times."""
    with create_socat_pair() as (left, right):
        serial_left = Serial(left, baudrate=115200)
        serial_right = Serial(right, baudrate=115200)

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


def test_open_close_cycles_socat() -> None:
    """Test multiple open/close cycles."""
    with create_socat_pair() as (left, right):
        serial_left = Serial(left, baudrate=115200)
        serial_right = Serial(right, baudrate=115200)

        # Cycle 1
        serial_left.open()
        serial_left.configure_port()
        serial_right.open()
        serial_right.configure_port()
        serial_left.write(b"1")
        assert serial_right.readexactly(1) == b"1"
        serial_left.close()
        serial_right.close()

        # Cycle 2
        serial_left.open()
        serial_left.configure_port()
        serial_right.open()
        serial_right.configure_port()
        serial_left.write(b"2")
        assert serial_right.readexactly(1) == b"2"
        serial_left.close()
        serial_right.close()

        # Cycle 3
        serial_left.open()
        serial_left.configure_port()
        serial_right.open()
        serial_right.configure_port()
        serial_left.write(b"3")
        assert serial_right.readexactly(1) == b"3"
        serial_left.close()
        serial_right.close()


def test_flush_after_write_socat() -> None:
    """Test flushing after write operation."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial_left,
        Serial(right, baudrate=115200) as serial_right,
    ):
        serial_left.flush()

        data = b"Test flush operation"
        serial_left.write(data)
        serial_left.flush()

        result = serial_right.readexactly(len(data))
        assert result == data


def test_multiple_flush_calls_socat() -> None:
    """Test multiple consecutive flush calls."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial_left,
        Serial(right, baudrate=115200) as serial_right,
    ):
        data = b""

        for i in range(5):
            chunk = f"Test data {i}".encode("ascii")
            data += chunk

            serial_left.write(chunk)
            serial_left.flush()

        result = serial_right.readexactly(len(data))
        assert result == data


def test_get_modem_pins_socat() -> None:
    """Test reading modem control bits with loopback adapter."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial,
    ):
        modem_pins = serial.get_modem_pins()

        # Verify we get a ModemPins object
        assert isinstance(modem_pins, ModemPins)

        # All modem pins should be PinState enum values
        for field in ["le", "dtr", "rts", "st", "sr", "cts", "car", "rng", "dsr"]:
            value = getattr(modem_pins, field)
            assert value in (PinState.HIGH, PinState.LOW, PinState.UNDEFINED)


def test_set_modem_pins_socat() -> None:
    """Test setting modem control bits with socat pair."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial,
    ):
        # Note: socat pairs don't support modem control signals properly
        # These calls should not raise errors, but values may be None
        serial.set_modem_pins(dtr=True, rts=True)
        modem_pins = serial.get_modem_pins()
        # Verify we get a ModemPins object, values may be None with socat
        assert isinstance(modem_pins, ModemPins)

        serial.set_modem_pins(dtr=False)
        modem_pins = serial.get_modem_pins()
        assert isinstance(modem_pins, ModemPins)

        serial.set_modem_pins(dtr=False, rts=False)
        modem_pins = serial.get_modem_pins()
        assert isinstance(modem_pins, ModemPins)


def test_deprecated_dtr_property_socat() -> None:
    """Test DTR property (deprecated alias) with socat pair."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial,
    ):
        # Note: socat pairs don't support modem control signals properly
        # These calls should not raise errors, but values may be None with socat
        serial.dtr = True
        # DTR may be None with socat, just verify no error occurs
        serial.dtr = False


def test_deprecated_rts_property_socat() -> None:
    """Test RTS property (deprecated alias) with socat pair."""
    with (
        create_socat_pair() as (left, right),
        Serial(left, baudrate=115200) as serial,
    ):
        # Note: socat pairs don't support modem control signals properly
        # These calls should not raise errors, but values may be None with socat
        serial.rts = True
        # RTS may be None with socat, just verify no error occurs
        serial.rts = False
