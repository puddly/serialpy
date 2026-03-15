"""POSIX serial port tests."""

import pytest

from serialx import Serial
from serialx.common import UnsupportedSetting
from tests.common import SerialPair


def test_posix_rtscts_unsupported(serial_pair: SerialPair) -> None:
    """Test that strict POSIX backend rejects RTS/CTS flow control."""
    if serial_pair.serial_class != "PosixSerial":
        pytest.skip("This test is only relevant for the strict PosixSerial backend")

    with pytest.raises(UnsupportedSetting):
        with Serial.from_url(serial_pair.left, baudrate=115200, rtscts=True):
            pass


def test_posix_dsrdtr_unsupported(serial_pair: SerialPair) -> None:
    """Test that strict POSIX backend rejects DSR/DTR flow control."""
    if serial_pair.serial_class != "PosixSerial":
        pytest.skip("This test is only relevant for the strict PosixSerial backend")

    with pytest.raises(UnsupportedSetting):
        with Serial.from_url(serial_pair.left, baudrate=115200, dsrdtr=True):
            pass


def test_posix_one_point_five_stopbits_unsupported(serial_pair: SerialPair) -> None:
    """Test that strict POSIX backend rejects 1.5 stop bits."""
    if serial_pair.serial_class != "PosixSerial":
        pytest.skip("This test is only relevant for the strict PosixSerial backend")

    with pytest.raises(UnsupportedSetting):
        with Serial.from_url(serial_pair.left, baudrate=115200, stopbits=1.5):
            pass


def test_posix_nonstandard_baudrate_unsupported(serial_pair: SerialPair) -> None:
    """Test that strict POSIX backend rejects non-standard baudrates."""
    if serial_pair.serial_class != "PosixSerial":
        pytest.skip("This test is only relevant for the strict PosixSerial backend")

    with pytest.raises(UnsupportedSetting):
        with Serial.from_url(serial_pair.left, baudrate=200000):
            pass
