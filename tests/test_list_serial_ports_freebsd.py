"""Tests for FreeBSD serial port listing with mocked command output."""

from __future__ import annotations

import sys

import pytest

if not sys.platform.startswith(("freebsd", "darwin", "linux")):
    pytest.skip("FreeBSD-only tests", allow_module_level=True)


from pathlib import Path
from unittest.mock import patch

from serialx.common import SerialPortInfo
from serialx.platforms.serial_freebsd import freebsd_list_serial_ports

DATA_DIR = Path(__file__).parent / "data" / "freebsd"
SYSCTL_OUTPUT = (DATA_DIR / "sysctl_dev.txt").read_text()
USBCONFIG_OUTPUT = (DATA_DIR / "usbconfig_dump_device_desc.txt").read_text()


def test_freebsd_list_serial_ports() -> None:
    """Test listing all serial ports with captured FreeBSD command output."""

    def _mock_subprocess_run(args, **kwargs):
        class Result:
            def __init__(self, stdout):
                self.stdout = stdout
                self.returncode = 0

        if args[:3] == ["sysctl", "-e", "dev"]:
            return Result(SYSCTL_OUTPUT)

        if args[:2] == ["usbconfig", "dump_device_desc"]:
            return Result(USBCONFIG_OUTPUT)

        raise ValueError(f"Unexpected command: {args}")

    with patch("subprocess.run", side_effect=_mock_subprocess_run):
        ports = freebsd_list_serial_ports()

    assert len(ports) == 5

    ports_by_device = {str(p.device): p for p in ports}

    # /dev/cuaU0: Nabu Casa ZBT-2 (CDC ACM via umodem)
    assert ports_by_device["/dev/cuaU0"] == SerialPortInfo(
        device="/dev/cuaU0",
        resolved_device="/dev/cuaU0",
        vid=0x303A,
        pid=0x4001,
        serial_number="80B54EEFAE18",
        manufacturer="Nabu Casa",
        product="ZBT-2",
        bcd_device=0x0101,
        interface_description=None,
        interface_num=0,
    )

    # /dev/cuaU1: FTDI FT232R (ugen8.2)
    assert ports_by_device["/dev/cuaU1"] == SerialPortInfo(
        device="/dev/cuaU1",
        resolved_device="/dev/cuaU1",
        vid=0x0403,
        pid=0x6001,
        serial_number="A5069RR4",
        manufacturer="FTDI",
        product="FT232R USB UART",
        bcd_device=0x0600,
        interface_description=None,
        interface_num=0,
    )

    # /dev/cuaU2: FTDI FT232R (ugen8.3)
    assert ports_by_device["/dev/cuaU2"] == SerialPortInfo(
        device="/dev/cuaU2",
        resolved_device="/dev/cuaU2",
        vid=0x0403,
        pid=0x6001,
        serial_number="A5069RR4",
        manufacturer="FTDI",
        product="FT232R USB UART",
        bcd_device=0x0600,
        interface_description=None,
        interface_num=0,
    )

    # /dev/cuaU3: Silicon Labs CP2102 (ugen8.5)
    assert ports_by_device["/dev/cuaU3"] == SerialPortInfo(
        device="/dev/cuaU3",
        resolved_device="/dev/cuaU3",
        vid=0x10C4,
        pid=0xEA60,
        serial_number="ec4903cb",
        manufacturer="Silicon Labs",
        product="CP2102 USB to UART Bridge Controller",
        bcd_device=0x0100,
        interface_description=None,
        interface_num=0,
    )

    # /dev/cuaU4: FTDI FT232R with custom serial (ugen8.6)
    assert ports_by_device["/dev/cuaU4"] == SerialPortInfo(
        device="/dev/cuaU4",
        resolved_device="/dev/cuaU4",
        vid=0x0403,
        pid=0x6001,
        serial_number="rutabaga",
        manufacturer="FTDI",
        product="FT232R USB UART",
        bcd_device=0x0600,
        interface_description=None,
        interface_num=0,
    )


def test_freebsd_list_serial_ports_no_devices() -> None:
    """Test listing when no serial devices are present."""

    def mock_run(args, **kwargs):
        class Result:
            def __init__(self, stdout):
                self.stdout = stdout
                self.returncode = 0

        if args[:3] == ["sysctl", "-e", "dev"]:
            return Result("dev.uhub.0.%parent=xhci0\n")

        if args[:2] == ["usbconfig", "dump_device_desc"]:
            return Result("")

        raise ValueError(f"Unexpected command: {args}")

    with patch("subprocess.run", side_effect=mock_run):
        ports = freebsd_list_serial_ports()

    assert ports == []
