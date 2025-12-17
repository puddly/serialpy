"""Tests for macOS serial port listing."""

from __future__ import annotations

import sys

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS-only tests", allow_module_level=True)

from serialx.common import SerialPortInfo
from serialx.platforms.serial_darwin import darwin_list_serial_ports


def test_list_serial_ports_macos() -> None:
    """Test listing serial ports on macOS."""
    ports = darwin_list_serial_ports()

    for port in ports:
        assert isinstance(port, SerialPortInfo)
        assert isinstance(port.device, str)
        assert port.vid is None or isinstance(port.vid, int)
        assert port.pid is None or isinstance(port.pid, int)
