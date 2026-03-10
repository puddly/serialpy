"""Tests for pyserial API compatibility shims."""

from serialx import Serial, SerialPortInfo
from serialx.tools.list_ports import comports, grep
from serialx.tools.list_ports_common import ListPortInfo
from tests.common import SerialPair


def test_compat_constructor_kwargs(serial_pair: SerialPair) -> None:
    """Test that pyserial-style constructor kwargs map to the correct properties."""
    with Serial.from_url(
        serial_pair.left,
        baudrate=9600,
        timeout=1.5,
        writeTimeout=2.0,
        bytesize=7,
    ) as s:
        # pyserial kwarg -> serialx property
        assert s.read_timeout == 1.5
        assert s.write_timeout == 2.0
        assert s.byte_size == 7

        # pyserial deprecated property aliases read back correctly
        assert s.port == str(s.path)
        assert s.timeout == 1.5
        assert s.writeTimeout == 2.0
        assert s.bytesize == 7
        assert s.baudrate == 9600


def test_compat_deprecated_aliases(serial_pair: SerialPair) -> None:
    """Test deprecated method and property aliases on an opened serial port."""
    with (
        Serial.from_url(serial_pair.left, baudrate=115200) as left,
        Serial.from_url(serial_pair.right, baudrate=115200, timeout=0.2) as right,
    ):
        # isOpen
        assert left.isOpen() is True
        assert left.is_open is True

        # in_waiting / inWaiting
        assert right.in_waiting == 0
        assert right.inWaiting == 0

        left.write(b"hello")
        left.flush()

        right.readexactly(5)

        # out_waiting
        assert left.out_waiting >= 0

        # reset_input_buffer / flushInput -> reset_read_buffer
        right.reset_input_buffer()
        right.flushInput()

        # reset_output_buffer / flushOutput -> reset_write_buffer
        left.reset_output_buffer()
        left.flushOutput()


def test_compat_timeout_setter(serial_pair: SerialPair) -> None:
    """Test that the deprecated .timeout setter mutates read_timeout."""
    with Serial.from_url(serial_pair.left, baudrate=115200) as s:
        s.timeout = 0.1
        assert s.read_timeout == 0.1
        assert s.timeout == 0.1

        s.timeout = 0.5
        assert s.read_timeout == 0.5


def test_compat_baudrate_setter(serial_pair: SerialPair) -> None:
    """Test that the deprecated .baudrate setter reconfigures the port."""
    with Serial.from_url(serial_pair.left, baudrate=9600) as s:
        assert s.baudrate == 9600
        s.baudrate = 115200
        assert s.baudrate == 115200


def test_compat_no_arg_construction() -> None:
    """Test that Serial can be constructed with no args (deferred open pattern)."""
    s = Serial()
    assert s.path is None
    assert s.baudrate == 9600
    assert s.port is None


def test_compat_tools_module() -> None:
    """Test that serialx.tools.list_ports provides pyserial-compatible API."""
    # comports is list_serial_ports
    ports = comports()
    assert isinstance(ports, list)

    for port in ports:
        assert isinstance(port, SerialPortInfo)

    # ListPortInfo is SerialPortInfo
    assert ListPortInfo is SerialPortInfo

    # grep returns an iterable (may be empty on CI with no ports)
    results = list(grep(".*"))
    for result in results:
        assert isinstance(result, SerialPortInfo)
