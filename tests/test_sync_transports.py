"""Sync transport tests."""

from collections.abc import Iterator
import logging
import os
import time

import pytest

from serialx import ModemPins, Parity, PinState, Serial, StopBits, serial_for_url
from serialx.common import BaseSerial
from tests.common import SOCAT_BINARY, create_socat_pair
from tests.socket_relay import create_socket_pair

LOGGER = logging.getLogger(__name__)


@pytest.fixture(params=["socat", "socket"])
def sync_transport_pair(request: pytest.FixtureRequest) -> Iterator[tuple[str, str]]:
    """Yield a connected pair of transports."""
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
) -> Iterator[tuple[BaseSerial, BaseSerial]]:
    """Yield a connected pair of opened Serial objects with default settings."""
    left_path, right_path = sync_transport_pair

    with (
        serial_for_url(left_path, baudrate=115200) as left_serial,
        serial_for_url(right_path, baudrate=115200) as right_serial,
    ):
        yield left_serial, right_serial


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

    with (
        serial_for_url(left_path, baudrate=baudrate) as left,
        serial_for_url(right_path, baudrate=baudrate) as right,
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

    with (
        serial_for_url(left_path, baudrate=921600) as left,
        serial_for_url(right_path, baudrate=921600) as right,
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

    with (
        serial_for_url(left_path, baudrate=baudrate) as left,
        serial_for_url(right_path, baudrate=baudrate) as right,
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
    with serial_for_url(left_path, baudrate=baudrate) as serial:
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
    with serial_for_url(left_path, baudrate=115200, parity=parity) as serial:
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
    with serial_for_url(left_path, baudrate=115200, stopbits=stopbits) as serial:
        assert serial.stopbits == expected
        serial.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
def test_sync_valid_byte_size(
    sync_transport_pair: tuple[str, str], byte_size: int
) -> None:
    """Test that valid byte sizes are accepted."""
    left_path, _ = sync_transport_pair
    with serial_for_url(left_path, baudrate=115200, byte_size=byte_size) as serial:
        serial.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
def test_sync_xonxoff_setting(
    sync_transport_pair: tuple[str, str], xonxoff: bool
) -> None:
    """Test that xonxoff setting is accepted."""
    left_path, _ = sync_transport_pair
    with serial_for_url(left_path, baudrate=115200, xonxoff=xonxoff) as serial:
        serial.write(b"test")


@pytest.mark.parametrize("rtscts", [True, False])
def test_sync_rtscts_setting(
    sync_transport_pair: tuple[str, str], rtscts: bool
) -> None:
    """Test that rtscts setting is accepted."""
    left_path, _ = sync_transport_pair
    with serial_for_url(left_path, baudrate=115200, rtscts=rtscts) as serial:
        serial.write(b"test")


def test_sync_exclusive(sync_transport_pair: tuple[str, str]) -> None:
    """Test that exclusive setting is respected."""
    left_path, _ = sync_transport_pair

    with serial_for_url(left_path, baudrate=115200, exclusive=True) as serial:
        assert serial.exclusive is True

        # Behavior depends on backend
        if left_path.startswith("socket://"):
            # Socket endpoints are not lockable tty devices
            with serial_for_url(left_path, baudrate=115200, exclusive=True) as serial2:
                assert serial2.exclusive is True
        else:
            # Socat PTYs should lock
            with pytest.raises(OSError):
                with serial_for_url(left_path, baudrate=115200, exclusive=True):
                    pass


def test_sync_exclusive_disabled(sync_transport_pair: tuple[str, str]) -> None:
    """Test that exclusive setting is respected."""
    left_path, _ = sync_transport_pair

    with serial_for_url(left_path, baudrate=115200, exclusive=False) as serial1:
        assert serial1.exclusive is False
        with serial_for_url(left_path, baudrate=115200, exclusive=False) as serial2:
            assert serial2.exclusive is False
            serial2.write(b"test")


def test_sync_context_manager_multiple_times(
    sync_transport_pair: tuple[str, str],
) -> None:
    """Test that context manager can be used multiple times."""
    left_path, right_path = sync_transport_pair

    serial_left = serial_for_url(left_path, baudrate=115200)
    serial_right = serial_for_url(right_path, baudrate=115200)

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

    serial_left = serial_for_url(left_path, baudrate=115200)
    serial_right = serial_for_url(right_path, baudrate=115200)

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

    with serial_for_url(left_path, baudrate=115200) as serial:
        modem_pins = serial.get_modem_pins()
        assert isinstance(modem_pins, ModemPins)
        for field in ["le", "dtr", "rts", "st", "sr", "cts", "car", "rng", "dsr"]:
            value = getattr(modem_pins, field)
            assert value in (PinState.HIGH, PinState.LOW, PinState.UNDEFINED)


def test_sync_set_modem_pins(sync_transport_pair: tuple[str, str]) -> None:
    """Test setting modem control bits."""
    left_path, _ = sync_transport_pair

    with serial_for_url(left_path, baudrate=115200) as serial:
        serial.set_modem_pins(dtr=True, rts=True)
        assert isinstance(serial.get_modem_pins(), ModemPins)

        serial.set_modem_pins(dtr=False)
        assert isinstance(serial.get_modem_pins(), ModemPins)

        serial.set_modem_pins(dtr=False, rts=False)
        assert isinstance(serial.get_modem_pins(), ModemPins)


def test_sync_deprecated_dtr_property(sync_transport_pair: tuple[str, str]) -> None:
    """Test DTR property (deprecated alias)."""
    left_path, _ = sync_transport_pair

    with serial_for_url(left_path, baudrate=115200) as serial:
        serial.dtr = True
        serial.dtr = False


def test_sync_deprecated_rts_property(sync_transport_pair: tuple[str, str]) -> None:
    """Test RTS property (deprecated alias)."""
    left_path, _ = sync_transport_pair

    with serial_for_url(left_path, baudrate=115200) as serial:
        serial.rts = True
        serial.rts = False


def test_sync_read_timeout(sync_transport_pair: tuple[str, str]) -> None:
    """Test that reading with a timeout returns 0 bytes after the timeout."""
    left_path, _ = sync_transport_pair

    with serial_for_url(left_path, baudrate=115200, timeout=0.1) as serial:
        start_time = time.time()
        # Try to read 10 bytes when no data is available
        result = serial.read(10)
        end_time = time.time()

        # Should return 0 bytes
        assert len(result) == 0
        # Should have taken at least 0.1 seconds (allowing for some OS jitter)
        assert end_time - start_time >= 0.09


def test_sync_read_timeout_with_partial_data(
    sync_transport_pair: tuple[str, str],
) -> None:
    """Test that reading with a timeout returns available data immediately."""
    left_path, right_path = sync_transport_pair

    with (
        serial_for_url(left_path, baudrate=115200, timeout=1.0) as serial_left,
        serial_for_url(right_path, baudrate=115200, timeout=1.0) as serial_right,
    ):
        # Write 5 bytes from one side
        data = b"hello"
        serial_left.write(data)

        start_time = time.time()
        # Try to read 5 bytes (matching what we wrote)
        result = serial_right.read(5)
        end_time = time.time()

        # Should return 5 bytes immediately
        assert result == data
        # Should have taken much less than 1.0 seconds
        assert end_time - start_time < 0.2


def test_socket_connect_timeout() -> None:
    """Test that connect_timeout is respected by SocketSerial."""
    # We use a non-routable IP to trigger a timeout (TEST-NET-1)
    # 192.0.2.1 is reserved for documentation and shouldn't be reachable
    url = "socket://192.0.2.1:1234"

    start_time = time.time()

    with pytest.raises((OSError, TimeoutError)):
        # connect_timeout is passed to SocketSerial constructor via kwargs
        with serial_for_url(url, baudrate=115200, connect_timeout=0.2):
            pass

    end_time = time.time()

    # Should have timed out after ~0.2s
    # Note: On some systems, "no route to host" might return instantly,
    # so we primarily check that it didn't hang forever.
    duration = end_time - start_time
    assert duration < 1.0
