"""Sync transport tests."""

from asyncio import IncompleteReadError
from collections.abc import Iterator
import logging
import os
import sys
import time

import pytest

from serialx import ModemPins, Parity, PinState, Serial, StopBits, serial_for_url
from serialx.common import BaseSerial
from tests.common import SerialPair, measure_time

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def serial_opened_pair(
    serial_pair: SerialPair,
) -> Iterator[tuple[BaseSerial, BaseSerial]]:
    """Yield a connected pair of opened Serial objects with default settings."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        yield left, right


# --- Data transmission ---


def test_sync_all_bytes(serial_pair: SerialPair) -> None:
    """Test that all bytes 0-255 can be transmitted."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        data = bytes(range(256))
        left.write(data)
        assert right.readexactly(len(data)) == data


def test_sync_serial_for_url_factory(serial_pair: SerialPair) -> None:
    """Test the top-level serial_for_url factory on all backends."""
    with (
        serial_for_url(serial_pair.left, baudrate=115200) as left,
        serial_for_url(serial_pair.right, baudrate=115200) as right,
    ):
        data = b"serial_for_url"
        left.write(data)
        assert right.readexactly(len(data)) == data


def test_sync_segmented_binary_data(serial_pair: SerialPair) -> None:
    """Test binary data sent in segments."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        segment_size = 16
        data = bytes(range(256))

        for i in range(0, 256, segment_size):
            segment = data[i : i + segment_size]
            left.write(segment)
            assert right.readexactly(len(segment)) == segment


@pytest.mark.parametrize("size", [1, 16, 64, 256, 512, 1024])
def test_sync_binary_payload_sizes(serial_pair: SerialPair, size: int) -> None:
    """Test various binary payload sizes."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        data = bytes([i % 256 for i in range(size)])
        left.write(data)
        assert right.readexactly(len(data)) == data


def test_sync_null_bytes(serial_pair: SerialPair) -> None:
    """Test that null bytes (0x00) can be transmitted."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        null_data = b"\x00" * 64
        left.write(null_data)
        assert right.readexactly(len(null_data)) == null_data


def test_sync_overlapping_read_write(serial_pair: SerialPair) -> None:
    """Test that read and write can overlap, data is buffered."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
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
    serial_pair: SerialPair, baudrate: int, chunk_size: int
) -> None:
    """Test random read/write at various speeds."""
    if (
        baudrate > 230400
        and sys.platform == "darwin"
        and serial_pair.serial_class in ("PosixSerial", "ExtendedPosixSerial")
    ):
        pytest.xfail("macOS termios lacks constants above B230400")

    with (
        Serial.from_url(serial_pair.left, baudrate=baudrate) as left,
        Serial.from_url(serial_pair.right, baudrate=baudrate) as right,
    ):
        data = os.urandom(chunk_size)
        left.write(data)
        assert right.readexactly(chunk_size) == data


@pytest.mark.parametrize("iterations", [16, 32, 64])
def test_sync_repeated_write_read_cycles(
    serial_pair: SerialPair, iterations: int
) -> None:
    """Test repeated write/read cycles."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        data = bytes(range(256))

        for _ in range(iterations):
            left.write(data)
            assert right.readexactly(len(data)) == data


def test_sync_buffered_writes_then_read(serial_pair: SerialPair) -> None:
    """Test multiple writes followed by a single read."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        chunk = bytes(range(256))
        iterations = 4

        for _ in range(iterations):
            left.write(chunk)

        assert right.readexactly(len(chunk) * iterations) == chunk * iterations


@pytest.mark.parametrize("payload_size", [1024, 2048])
def test_sync_large_payload(serial_pair: SerialPair, payload_size: int) -> None:
    """Test large payload transmission."""
    if sys.platform == "darwin" and serial_pair.serial_class in (
        "PosixSerial",
        "ExtendedPosixSerial",
    ):
        pytest.xfail("macOS termios lacks constants above B230400")

    with (
        Serial.from_url(serial_pair.left, baudrate=921600) as left,
        Serial.from_url(serial_pair.right, baudrate=921600) as right,
    ):
        data = bytes([i % 256 for i in range(payload_size)])
        left.write(data)
        assert right.readexactly(len(data)) == data


def test_sync_rapid_small_writes(serial_pair: SerialPair) -> None:
    """Test rapid succession of small writes."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        iterations = 256
        received = bytearray()

        for i in range(iterations):
            left.write(bytes([i % 256]))
            received.extend(right.readexactly(1))

        assert bytes(received) == bytes([i % 256 for i in range(iterations)])


@pytest.mark.parametrize(
    "baudrate,iterations", [(9600, 8), (115200, 64), (921600, 512)]
)
def test_sync_sustained_throughput(
    serial_pair: SerialPair, baudrate: int, iterations: int
) -> None:
    """Test sustained data throughput at various baudrates."""
    if (
        baudrate > 230400
        and sys.platform == "darwin"
        and serial_pair.serial_class in ("PosixSerial", "ExtendedPosixSerial")
    ):
        pytest.xfail("macOS termios lacks constants above B230400")

    if serial_pair.backend == "esphome" and (baudrate, iterations) == (921600, 512):
        pytest.skip(
            "ESPHome backend is too slow for sustained throughput at 921600/512"
        )

    with (
        Serial.from_url(serial_pair.left, baudrate=baudrate) as left,
        Serial.from_url(serial_pair.right, baudrate=baudrate) as right,
    ):
        chunk = os.urandom(1024)
        for _ in range(iterations):
            left.write(chunk)
            assert right.readexactly(len(chunk)) == chunk


# --- Configuration ---


@pytest.mark.parametrize(
    "baudrate", [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
)
def test_sync_valid_baudrates(serial_pair: SerialPair, baudrate: int) -> None:
    """Test that valid baudrates are accepted."""
    if (
        baudrate > 230400
        and sys.platform == "darwin"
        and serial_pair.serial_class in ("PosixSerial", "ExtendedPosixSerial")
    ):
        pytest.xfail("macOS termios lacks constants above B230400")

    with Serial.from_url(serial_pair.left, baudrate=baudrate) as serial:
        assert serial.baudrate == baudrate
        serial.write(b"test")


@pytest.mark.parametrize(
    "parity", [Parity.NONE, Parity.ODD, Parity.EVEN, Parity.MARK, Parity.SPACE]
)
def test_sync_valid_parity(serial_pair: SerialPair, parity: Parity) -> None:
    """Test that valid parity settings are accepted."""
    if serial_pair.backend == "esphome" and parity in (Parity.MARK, Parity.SPACE):
        pytest.xfail("ESPHome backend does not support MARK/SPACE parity")
    if serial_pair.serial_class not in ("LinuxSerial", "Win32Serial") and parity in (
        Parity.MARK,
        Parity.SPACE,
    ):
        pytest.skip("MARK/SPACE parity requires CMSPAR (Linux) or Win32")

    with Serial.from_url(serial_pair.left, baudrate=115200, parity=parity) as serial:
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

    with Serial.from_url(
        serial_pair.left, baudrate=115200, stopbits=stopbits
    ) as serial:
        assert serial.stopbits == expected
        serial.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
def test_sync_valid_byte_size(serial_pair: SerialPair, byte_size: int) -> None:
    """Test that valid byte sizes are accepted."""
    with Serial.from_url(
        serial_pair.left, baudrate=115200, byte_size=byte_size
    ) as serial:
        serial.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
def test_sync_xonxoff_setting(serial_pair: SerialPair, xonxoff: bool) -> None:
    """Test that xonxoff setting is accepted."""
    with Serial.from_url(serial_pair.left, baudrate=115200, xonxoff=xonxoff) as serial:
        serial.write(b"test")


@pytest.mark.parametrize("rtscts", [True, False])
def test_sync_rtscts_setting(serial_pair: SerialPair, rtscts: bool) -> None:
    """Test that rtscts setting is accepted."""
    if rtscts and serial_pair.serial_class == "PosixSerial":
        pytest.xfail("Strict POSIX backend does not support RTS/CTS flow control")

    # Open both sides: on com0com, opening right asserts DTR which raises CTS on left
    with Serial.from_url(serial_pair.right, baudrate=115200):
        with Serial.from_url(serial_pair.left, baudrate=115200, rtscts=rtscts) as left:
            left.write(b"test")


def test_sync_exclusive(serial_pair: SerialPair) -> None:
    """Test that exclusive setting is respected."""
    if serial_pair.backend == "socket":
        pytest.skip("Socket backend does not support exclusivity")

    if serial_pair.backend == "esphome":
        # TODO: exclusivity needs to be implemented
        pytest.xfail("ESPHome backend does not support exclusivity")

    with Serial.from_url(serial_pair.left, baudrate=115200, exclusive=True) as serial:
        assert serial.exclusive is True

        with pytest.raises(OSError):
            with Serial.from_url(serial_pair.left, baudrate=115200, exclusive=True):
                pass


def test_sync_exclusive_disabled(serial_pair: SerialPair) -> None:
    """Test that non-exclusive mode allows multiple opens."""
    if sys.platform == "win32":
        pytest.skip("Windows does not support shared access")

    with Serial.from_url(serial_pair.left, baudrate=115200, exclusive=False) as serial1:
        assert serial1.exclusive is False

        with Serial.from_url(
            serial_pair.left, baudrate=115200, exclusive=False
        ) as serial2:
            assert serial2.exclusive is False
            serial2.write(b"test")


# --- Lifecycle ---


def test_sync_context_manager_multiple_times(serial_pair: SerialPair) -> None:
    """Test that context manager can be used multiple times."""
    left = Serial.from_url(serial_pair.left, baudrate=115200)
    right = Serial.from_url(serial_pair.right, baudrate=115200)

    with left, right:
        left.write(b"test1")
        assert right.readexactly(5) == b"test1"

    with left, right:
        left.write(b"test2")
        assert right.readexactly(5) == b"test2"


def test_sync_open_close_cycles(serial_pair: SerialPair) -> None:
    """Test multiple open/close cycles."""
    left = Serial.from_url(serial_pair.left, baudrate=115200)
    right = Serial.from_url(serial_pair.right, baudrate=115200)

    for i in range(1, 4):
        left.open()
        right.open()

        chunk = str(i).encode("ascii")
        left.write(chunk)
        assert right.readexactly(1) == chunk

        left.close()
        right.close()


def test_sync_flush_after_write(serial_pair: SerialPair) -> None:
    """Test flushing after write operation."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        left.flush()

        data = b"Test flush operation"
        left.write(data)
        left.flush()

        assert right.readexactly(len(data)) == data


def test_sync_multiple_flush_calls(serial_pair: SerialPair) -> None:
    """Test multiple consecutive flush calls."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        data = b""

        for i in range(5):
            chunk = f"Test data {i}".encode("ascii")
            data += chunk
            left.write(chunk)
            left.flush()

        assert right.readexactly(len(data)) == data


# --- Modem pins ---


def test_sync_get_modem_pins(serial_pair: SerialPair) -> None:
    """Test reading modem control bits."""
    with Serial.from_url(serial_pair.left, baudrate=115200) as serial:
        modem_pins = serial.get_modem_pins()
        assert isinstance(modem_pins, ModemPins)

        for field in ["le", "dtr", "rts", "st", "sr", "cts", "car", "rng", "dsr"]:
            value = getattr(modem_pins, field)
            assert value in (PinState.HIGH, PinState.LOW, PinState.UNDEFINED)


def test_sync_set_modem_pins_api(serial_pair: SerialPair) -> None:
    """Test modem pin writes are accepted on all backends."""
    with Serial.from_url(serial_pair.left, baudrate=115200) as serial:
        serial.set_modem_pins(dtr=True, rts=True)
        pins_high = serial.get_modem_pins()

        serial.set_modem_pins(dtr=False, rts=False)
        pins_low = serial.get_modem_pins()

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
def test_sync_set_modem_pins(serial_pair: SerialPair) -> None:
    """Test setting modem control bits and verifying readback."""

    with Serial.from_url(serial_pair.left, baudrate=115200) as serial:
        serial.set_modem_pins(dtr=True, rts=True)
        modem_pins = serial.get_modem_pins()
        assert modem_pins.dtr is PinState.HIGH
        assert modem_pins.rts is PinState.HIGH

        serial.set_modem_pins(dtr=False)
        modem_pins = serial.get_modem_pins()
        assert modem_pins.dtr is PinState.LOW
        assert modem_pins.rts is PinState.HIGH

        serial.set_modem_pins(dtr=False, rts=False)
        modem_pins = serial.get_modem_pins()
        assert modem_pins.dtr is PinState.LOW
        assert modem_pins.rts is PinState.LOW


@pytest.mark.skipif(
    sys.platform == "win32", reason="GetCommModemStatus cannot read back DTR/RTS"
)
@pytest.mark.skip_backends("socket", "socat")
def test_sync_deprecated_dtr_property(serial_pair: SerialPair) -> None:
    """Test DTR property (deprecated alias)."""

    with Serial.from_url(serial_pair.left, baudrate=115200) as serial:
        serial.dtr = True
        assert serial.dtr is True

        serial.dtr = False
        assert serial.dtr is False


@pytest.mark.skipif(
    sys.platform == "win32", reason="GetCommModemStatus cannot read back DTR/RTS"
)
@pytest.mark.skip_backends("socket", "socat")
def test_sync_deprecated_rts_property(serial_pair: SerialPair) -> None:
    """Test RTS property (deprecated alias)."""

    with Serial.from_url(serial_pair.left, baudrate=115200) as serial:
        serial.rts = True
        assert serial.rts is True

        serial.rts = False
        assert serial.rts is False


# --- Timeouts ---


def test_sync_read_timeout(serial_pair: SerialPair) -> None:
    """Test that reading with a timeout returns 0 bytes after the timeout."""
    with Serial.from_url(serial_pair.left, baudrate=115200, read_timeout=0.1) as serial:
        assert serial.read_timeout == 0.1

        with measure_time() as elapsed:
            result = serial.read(10)

        assert len(result) == 0
        assert elapsed() >= 0.09


def test_sync_read_timeout_with_partial_data(serial_pair: SerialPair) -> None:
    """Test that reading with a timeout returns available data immediately."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200, read_timeout=1.0) as left,
        Serial.from_url(serial_pair.right, baudrate=115200, read_timeout=1.0) as right,
    ):
        left.write(b"hello")

        with measure_time() as elapsed:
            result = right.read(5)

        assert result == b"hello"
        assert elapsed() < 0.2


def test_sync_readexactly_partial_timeout(serial_pair: SerialPair) -> None:
    """Test that readexactly(10) with only 5 bytes raises IncompleteReadError."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200, read_timeout=0.5) as left,
        Serial.from_url(serial_pair.right, baudrate=115200, read_timeout=0.5) as right,
    ):
        left.write(b"hello")

        with measure_time() as elapsed:
            with pytest.raises(IncompleteReadError) as exc_info:
                right.readexactly(10)

        assert exc_info.value.partial == b"hello"
        assert elapsed() == pytest.approx(0.5, abs=0.1)


@pytest.mark.skip_backends("socket", "esphome")
def test_sync_write_timeout(serial_pair: SerialPair) -> None:
    """Test that write timeout works when buffer is full."""

    with Serial.from_url(serial_pair.left, baudrate=9600, write_timeout=0.1) as serial:
        data = b"x" * 1024

        with pytest.raises(TimeoutError):
            for _ in range(1000):
                serial.write(data)


# --- Adapter-pair-specific tests ---
# These tests require physical adapter pairs (com0com, real hardware)
# and verify cross-port behavior that virtual backends can't emulate.


@pytest.mark.require_backends("adapter")
def test_dtr_cts(serial_pair: SerialPair) -> None:
    """Test that DTR on one side controls CTS on the other."""

    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        left.set_modem_pins(rts=False)
        right.set_modem_pins(rts=False)

        left.set_modem_pins(dtr=True)
        time.sleep(0.05)
        assert right.get_modem_pins().cts is PinState.HIGH

        right.set_modem_pins(dtr=True)
        time.sleep(0.05)
        assert left.get_modem_pins().cts is PinState.HIGH

        left.set_modem_pins(dtr=False)
        time.sleep(0.05)
        assert right.get_modem_pins().cts is PinState.LOW

        right.set_modem_pins(dtr=False)
        time.sleep(0.05)
        assert left.get_modem_pins().cts is PinState.LOW


@pytest.mark.require_backends("adapter")
def test_deprecated_dtr_cts(serial_pair: SerialPair) -> None:
    """Test DTR/CTS cross-port behavior via deprecated property aliases."""

    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200) as right,
    ):
        left.set_modem_pins(rts=False)
        right.set_modem_pins(rts=False)

        left.dtr = True
        time.sleep(0.05)
        assert right.get_modem_pins().cts is PinState.HIGH

        right.dtr = True
        time.sleep(0.05)
        assert left.get_modem_pins().cts is PinState.HIGH

        left.dtr = False
        time.sleep(0.05)
        assert right.get_modem_pins().cts is PinState.LOW

        right.dtr = False
        time.sleep(0.05)
        assert left.get_modem_pins().cts is PinState.LOW


def test_fast_open_close(serial_pair: SerialPair) -> None:
    """Test quickly opening and closing a port."""

    message = b"Fast write and close test"

    with Serial.from_url(serial_pair.left, baudrate=115200) as left:
        with Serial.from_url(serial_pair.right, baudrate=115200) as right:
            right.write(message)

        assert left.readexactly(len(message)) == message


@pytest.mark.skipif(sys.platform == "win32", reason="CloseHandle resets modem signals")
@pytest.mark.require_backends("adapter")
def test_deassert_on_open(serial_pair: SerialPair) -> None:
    """Test DTR/CTS deassertion on open."""

    with Serial.from_url(serial_pair.left, baudrate=115200) as left:
        with Serial.from_url(
            serial_pair.right,
            baudrate=115200,
            rtsdtr_on_open=PinState.HIGH,
            rtsdtr_on_close=PinState.HIGH,
        ) as right:
            right.set_modem_pins(dtr=True)
            time.sleep(0.05)
            assert left.get_modem_pins().cts is PinState.HIGH

        # rtsdtr_on_close=HIGH keeps DTR asserted
        time.sleep(0.05)
        assert left.get_modem_pins().cts is PinState.HIGH

        with Serial.from_url(
            serial_pair.right,
            baudrate=115200,
            rtsdtr_on_open=PinState.LOW,
            rtsdtr_on_close=PinState.HIGH,
        ) as right:
            # rtsdtr_on_open=LOW deasserts DTR
            time.sleep(0.05)
            assert left.get_modem_pins().cts is PinState.LOW
            right.set_modem_pins(dtr=True)

        # rtsdtr_on_close=HIGH keeps DTR asserted
        time.sleep(0.05)
        assert left.get_modem_pins().cts is PinState.HIGH


@pytest.mark.skipif(sys.platform == "win32", reason="CloseHandle resets modem signals")
@pytest.mark.require_backends("adapter")
def test_hang_up_on_close(serial_pair: SerialPair) -> None:
    """Test DTR/CTS hang up on close."""

    with Serial.from_url(serial_pair.left, baudrate=115200) as left:
        with Serial.from_url(
            serial_pair.right,
            baudrate=115200,
            rtsdtr_on_close=PinState.HIGH,
            rtsdtr_on_open=PinState.HIGH,
        ) as right:
            right.set_modem_pins(dtr=True)
            time.sleep(0.05)
            assert left.get_modem_pins().cts is PinState.HIGH

        time.sleep(0.05)
        assert left.get_modem_pins().cts is PinState.HIGH

        with Serial.from_url(
            serial_pair.right,
            baudrate=115200,
            rtsdtr_on_close=PinState.HIGH,
            rtsdtr_on_open=PinState.HIGH,
        ) as right:
            time.sleep(0.05)
            assert left.get_modem_pins().cts is PinState.HIGH

        time.sleep(0.05)
        assert left.get_modem_pins().cts is PinState.HIGH

        with Serial.from_url(
            serial_pair.right,
            baudrate=115200,
            rtsdtr_on_close=PinState.LOW,
            rtsdtr_on_open=PinState.HIGH,
        ) as right:
            time.sleep(0.05)
            assert left.get_modem_pins().cts is PinState.HIGH

        time.sleep(0.05)
        assert left.get_modem_pins().cts is PinState.LOW


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
def test_deassert_on_open_with_rtscts(
    serial_pair: SerialPair,
    rtscts: bool,
    rtsdtr_on_open: PinState,
    expected_state: PinState,
) -> None:
    """Test interaction of rtsdtr_on_open with rtscts."""

    with Serial.from_url(serial_pair.left, baudrate=115200) as left:
        with Serial.from_url(
            serial_pair.right,
            baudrate=115200,
            rtscts=False,
            rtsdtr_on_open=PinState.HIGH,
            rtsdtr_on_close=PinState.HIGH,
        ) as right:
            right.set_modem_pins(dtr=True)
            time.sleep(0.05)
            assert left.get_modem_pins().cts is PinState.HIGH

        time.sleep(0.05)
        assert left.get_modem_pins().cts is PinState.HIGH

        with Serial.from_url(
            serial_pair.right,
            baudrate=115200,
            rtscts=rtscts,
            rtsdtr_on_open=rtsdtr_on_open,
        ):
            time.sleep(0.05)
            assert left.get_modem_pins().cts is expected_state


@pytest.mark.require_backends("adapter")
def test_write_timeout_cts_held(serial_pair: SerialPair) -> None:
    """Test that write timeout fires when CTS is deasserted (flow control hold)."""

    with Serial.from_url(serial_pair.right, baudrate=9600) as right:
        right.set_modem_pins(dtr=False)
        time.sleep(0.1)

        with Serial.from_url(
            serial_pair.left,
            baudrate=9600,
            rtscts=True,
            write_timeout=0.5,
        ) as left:
            with measure_time() as elapsed:
                with pytest.raises(TimeoutError):
                    left.write(b"x" * 1024)

            assert elapsed() == pytest.approx(0.5, abs=0.2)
