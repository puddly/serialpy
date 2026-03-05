"""Socket serial port tests."""

import pytest

from serialx import serial_for_url
from serialx.platforms.serial_socket import SocketSerial
from tests.common import measure_time
from tests.socket_relay import create_socket_pair


def test_socket_connect_timeout_property() -> None:
    """Test that connect_timeout property is accessible."""
    serial = SocketSerial(
        path="socket://127.0.0.1:1234",
        baudrate=115200,
        connect_timeout=0.5,
    )
    assert serial.connect_timeout == 0.5


def test_socket_effective_timeout_mismatched() -> None:
    """Test that mismatched read/write timeouts use min for socket timeout."""
    with create_socket_pair() as (left_url, _right_url):
        serial = serial_for_url(
            left_url, baudrate=115200, read_timeout=2.0, write_timeout=1.0
        )
        assert isinstance(serial, SocketSerial)

        with serial:
            assert serial._socket is not None
            assert serial._socket.gettimeout() == 1.0


def test_socket_invalid_uri() -> None:
    """Test that invalid URIs raise ValueError."""
    with pytest.raises(ValueError, match="expected both host and port"):
        SocketSerial(path="socket://127.0.0.1", baudrate=115200)

    with pytest.raises(ValueError, match="expected both host and port"):
        SocketSerial(path="socket://:1234", baudrate=115200)


def test_socket_connect_timeout() -> None:
    """Test that connect_timeout is respected by SocketSerial."""
    url = "socket://192.0.2.1:1234"

    with measure_time() as elapsed:
        with pytest.raises((OSError, TimeoutError)):
            with serial_for_url(url, baudrate=115200, connect_timeout=0.2):
                pass

    assert elapsed() < 1.0
