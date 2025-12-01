"""Test sync APIs with dual loopback hardware."""

import os

import pytest

from serialx import Parity, PinState, Serial, StopBits
from tests.common import create_dual_loopback


def test_all_bytes_dual(adapter_pair: tuple[str, str]) -> None:
    """Test that all bytes 0-255 can be transmitted."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
    ):
        # Create a byte array with all possible byte values
        data = bytes(range(256))

        serial_left.write(data)
        result = serial_right.readexactly(len(data))

        assert result == data


def test_segmented_binary_data_dual(adapter_pair: tuple[str, str]) -> None:
    """Test binary data sent in segments."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
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
def test_binary_payload_sizes_dual(adapter_pair: tuple[str, str], size: int) -> None:
    """Test various binary payload sizes."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
    ):
        # Create binary data with repeating pattern
        data = bytes([i % 256 for i in range(size)])

        serial_left.write(data)
        result = serial_right.readexactly(len(data))

        assert result == data


def test_null_bytes_dual(adapter_pair: tuple[str, str]) -> None:
    """Test that null bytes (0x00) can be transmitted."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
    ):
        # Send a bunch of null bytes
        null_data = b"\x00" * 64

        serial_left.write(null_data)
        result = serial_right.readexactly(len(null_data))

        assert result == null_data


def test_overlapping_read_write_dual(adapter_pair: tuple[str, str]) -> None:
    """Test that read and write can overlap, data is buffered."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
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
        # (921600, 1),  # Hardware can't reliably handle 921600 with dual loopback
        # (921600, 16),
        # (921600, 256),
        # (921600, 1024),
    ],
)
def test_random_large_dual(
    adapter_pair: tuple[str, str], baudrate: int, chunk_size: int
) -> None:
    """Test loopback adapter random read/write."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=baudrate) as (
        serial_left,
        serial_right,
    ):
        data = os.urandom(chunk_size)
        serial_left.write(data)

        read_data = serial_right.readexactly(chunk_size)
        assert read_data == data


@pytest.mark.parametrize(
    "iterations",
    [16, 32, 64],
)
def test_repeated_write_read_cycles_dual(
    adapter_pair: tuple[str, str], iterations: int
) -> None:
    """Test repeated write/read cycles."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
    ):
        data = bytes(range(256))

        for _i in range(iterations):
            serial_left.write(data)
            result = serial_right.readexactly(len(data))
            assert result == data


def test_buffered_writes_then_read_dual(adapter_pair: tuple[str, str]) -> None:
    """Test multiple writes followed by a single read."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
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
def test_large_payload_dual(adapter_pair: tuple[str, str], payload_size: int) -> None:
    """Test large payload transmission."""
    left_port, right_port = adapter_pair
    # Using 115200 instead of 921600 due to hardware limitations
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
    ):
        data = bytes([i % 256 for i in range(payload_size)])

        serial_left.write(data)
        result = serial_right.readexactly(len(data))

        assert result == data


def test_rapid_small_writes_dual(adapter_pair: tuple[str, str]) -> None:
    """Test rapid succession of small writes."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
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
        # (921600, 512),  # Hardware can't reliably handle 921600 with dual loopback
    ],
)
def test_sustained_throughput_dual(
    adapter_pair: tuple[str, str], baudrate: int, iterations: int
) -> None:
    """Test sustained data throughput at various baudrates."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=baudrate) as (
        serial_left,
        serial_right,
    ):
        chunk = os.urandom(1024)

        for _ in range(iterations):
            serial_left.write(chunk)
            result = serial_right.readexactly(len(chunk))
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
def test_valid_baudrates_dual(adapter_pair: tuple[str, str], baudrate: int) -> None:
    """Test that valid baudrates are accepted."""
    left_port, right_port = adapter_pair
    with Serial(adapter_pair[0], baudrate=baudrate) as serial:
        assert serial.baudrate == baudrate
        serial.write(b"test")


@pytest.mark.parametrize(
    "parity",
    [Parity.NONE, Parity.ODD, Parity.EVEN, Parity.MARK, Parity.SPACE],
)
def test_valid_parity_dual(adapter_pair: tuple[str, str], parity: Parity) -> None:
    """Test that valid parity settings are accepted."""
    left_port, right_port = adapter_pair
    with Serial(adapter_pair[0], baudrate=115200, parity=parity) as serial:
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
def test_valid_stopbits_dual(
    adapter_pair: tuple[str, str],
    stopbits: StopBits | int | float,
    expected: StopBits,
) -> None:
    """Test that valid stopbits settings are accepted."""
    left_port, right_port = adapter_pair
    with Serial(adapter_pair[0], baudrate=115200, stopbits=stopbits) as serial:
        assert serial.stopbits == expected
        serial.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
def test_valid_byte_size_dual(adapter_pair: tuple[str, str], byte_size: int) -> None:
    """Test that valid byte sizes are accepted."""
    left_port, right_port = adapter_pair
    with Serial(adapter_pair[0], baudrate=115200, byte_size=byte_size) as serial:
        serial.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
def test_xonxoff_setting_dual(adapter_pair: tuple[str, str], xonxoff: bool) -> None:
    """Test that xonxoff setting is accepted."""
    left_port, right_port = adapter_pair
    with Serial(adapter_pair[0], baudrate=115200, xonxoff=xonxoff) as serial:
        serial.write(b"test")


@pytest.mark.parametrize(
    "rtscts",
    [True, False],
)
def test_rtscts_setting_dual(adapter_pair: tuple[str, str], rtscts: bool) -> None:
    """Test that rtscts setting is accepted."""
    left_port, right_port = adapter_pair
    with Serial(adapter_pair[0], baudrate=115200, rtscts=rtscts) as serial:
        serial.write(b"test")


def test_exclusive_dual(adapter_pair: tuple[str, str]) -> None:
    """Test that exclusive setting is respected."""
    left_port, right_port = adapter_pair
    with Serial(adapter_pair[0], baudrate=115200, exclusive=True) as serial:
        assert serial.exclusive is True

        # Opening a second one will fail
        with pytest.raises(OSError):
            with Serial(adapter_pair[0], baudrate=115200, exclusive=True):
                pass


def test_exclusive_disabled_dual(adapter_pair: tuple[str, str]) -> None:
    """Test that exclusive setting is respected."""
    left_port, right_port = adapter_pair
    with Serial(adapter_pair[0], baudrate=115200, exclusive=False) as serial1:
        assert serial1.exclusive is False

        # Can open the same port again without exclusive
        with Serial(adapter_pair[0], baudrate=115200, exclusive=False) as serial2:
            assert serial2.exclusive is False
            # Both can write without error
            serial2.write(b"test")


def test_context_manager_multiple_times_dual(adapter_pair: tuple[str, str]) -> None:
    """Test that context manager can be used multiple times."""
    left_port, right_port = adapter_pair
    serial_left = Serial(adapter_pair[0], baudrate=115200)
    serial_right = Serial(adapter_pair[1], baudrate=115200)

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


def test_open_close_cycles_dual(adapter_pair: tuple[str, str]) -> None:
    """Test multiple open/close cycles."""
    left_port, right_port = adapter_pair
    serial_left = Serial(adapter_pair[0], baudrate=115200)
    serial_right = Serial(adapter_pair[1], baudrate=115200)

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


def test_flush_after_write_dual(adapter_pair: tuple[str, str]) -> None:
    """Test flushing after write operation."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
    ):
        serial_left.flush()

        data = b"Test flush operation"
        serial_left.write(data)
        serial_left.flush()

        result = serial_right.read(len(data))
        assert result == data


def test_multiple_flush_calls_dual(adapter_pair: tuple[str, str]) -> None:
    """Test multiple consecutive flush calls."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
    ):
        data = b""

        for i in range(5):
            chunk = f"Test data {i}".encode("ascii")
            data += chunk

            serial_left.write(chunk)
            serial_left.flush()

        result = serial_right.readexactly(len(data))
        assert result == data


def test_fast_open_close(adapter_pair: tuple[str, str]) -> None:
    """Test quickly opening and closing a port."""

    message = b"Fast write and close test"

    with Serial(adapter_pair[0], baudrate=300) as serial_left:
        with Serial(adapter_pair[1], baudrate=300) as serial_right:
            serial_right.write(message)

        assert serial_left.readexactly(len(message)) == message


def test_deprecated_dtr_cts_dual(adapter_pair: tuple[str, str]) -> None:
    """Test DTR and CTS properties (deprecated alias)."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
    ):
        serial_left.dtr = True
        assert serial_right.get_modem_pins().cts is PinState.HIGH

        serial_right.dtr = True
        assert serial_left.get_modem_pins().cts is PinState.HIGH

        serial_left.dtr = False
        assert serial_right.get_modem_pins().cts is PinState.LOW

        serial_right.dtr = False
        assert serial_left.get_modem_pins().cts is PinState.LOW


def test_dtr_cts_dual(adapter_pair: tuple[str, str]) -> None:
    """Test DTR and CTS."""
    left_port, right_port = adapter_pair
    with create_dual_loopback(left_port, right_port, baudrate=115200) as (
        serial_left,
        serial_right,
    ):
        serial_left.set_modem_pins(dtr=True)
        assert serial_right.get_modem_pins().cts is PinState.HIGH

        serial_right.set_modem_pins(dtr=True)
        assert serial_left.get_modem_pins().cts is PinState.HIGH

        serial_left.set_modem_pins(dtr=False)
        assert serial_right.get_modem_pins().cts is PinState.LOW

        serial_right.set_modem_pins(dtr=False)
        assert serial_left.get_modem_pins().cts is PinState.LOW


def test_deassert_on_open(adapter_pair: tuple[str, str]) -> None:
    """Test DTR/CTS deassertion on open."""
    left_port, right_port = adapter_pair
    with Serial(left_port, baudrate=115200) as serial_left:
        # Open and set DTR/CTS
        with Serial(
            right_port,
            baudrate=115200,
            rtsdtr_on_open=PinState.HIGH,
            rtsdtr_on_close=PinState.HIGH,
        ) as serial_right:
            serial_right.set_modem_pins(dtr=True)
            assert serial_left.get_modem_pins().cts is PinState.HIGH

        # It persists
        assert serial_left.get_modem_pins().cts is PinState.HIGH

        # When we deassert on open, it should clear
        with Serial(
            right_port,
            baudrate=115200,
            rtsdtr_on_open=PinState.LOW,
            rtsdtr_on_close=PinState.HIGH,
        ) as serial_right:
            assert serial_left.get_modem_pins().cts is PinState.LOW
            serial_right.set_modem_pins(dtr=True)

        # Nothing changes on close
        assert serial_left.get_modem_pins().cts is PinState.HIGH


def test_hang_up_on_close(adapter_pair: tuple[str, str]) -> None:
    """Test DTR/CTS hang up on close."""
    left_port, right_port = adapter_pair
    with Serial(adapter_pair[0], baudrate=115200) as serial_left:
        # Open and set DTR/CTS
        with Serial(
            right_port,
            baudrate=115200,
            rtsdtr_on_close=PinState.HIGH,
            rtsdtr_on_open=PinState.HIGH,
        ) as serial_right:
            serial_right.set_modem_pins(dtr=True)
            assert serial_left.get_modem_pins().cts is PinState.HIGH

        # It persists
        assert serial_left.get_modem_pins().cts is PinState.HIGH

        # Without hang up on close, it still persists
        with Serial(
            right_port,
            baudrate=115200,
            rtsdtr_on_close=PinState.HIGH,
            rtsdtr_on_open=PinState.HIGH,
        ) as serial_right:
            assert serial_left.get_modem_pins().cts is PinState.HIGH

        assert serial_left.get_modem_pins().cts is PinState.HIGH

        # When we hang up on close, it should clear
        with Serial(
            right_port,
            baudrate=115200,
            rtsdtr_on_close=PinState.LOW,
            rtsdtr_on_open=PinState.HIGH,
        ) as serial_right:
            assert serial_left.get_modem_pins().cts is PinState.HIGH

        assert serial_left.get_modem_pins().cts is PinState.LOW


@pytest.mark.parametrize(
    ("rtscts", "rtsdtr_on_open", "expected_state"),
    [
        (False, PinState.HIGH, PinState.HIGH),  # No flow control, preserve pins
        (False, PinState.LOW, PinState.LOW),  # No flow control, clear pins
        (True, PinState.HIGH, PinState.HIGH),  # Flow control, preserve pins
        (True, PinState.LOW, PinState.LOW),  # Flow control, clear pins
    ],
)
def test_deassert_on_open_with_rtscts(
    adapter_pair: tuple[str, str],
    rtscts: bool,
    rtsdtr_on_open: PinState,
    expected_state: PinState,
) -> None:
    """Test interaction of rtsdtr_on_open with rtscts."""
    left_port, right_port = adapter_pair
    with Serial(left_port, baudrate=115200) as serial_left:
        # Set DTR on right side, verify CTS appears on left
        with Serial(
            right_port,
            baudrate=115200,
            rtscts=False,
            rtsdtr_on_open=PinState.HIGH,
        ) as serial_right:
            serial_right.set_modem_pins(dtr=True)
            assert serial_left.get_modem_pins().cts is PinState.HIGH

        # DTR persists after close
        assert serial_left.get_modem_pins().cts is PinState.HIGH

        # Open with test parameters
        with Serial(
            right_port,
            baudrate=115200,
            rtscts=rtscts,
            rtsdtr_on_open=rtsdtr_on_open,
        ) as serial_right:
            assert serial_left.get_modem_pins().cts is expected_state
