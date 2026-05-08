"""Tests for FreeBSD serial port listing with mocked command output."""

from __future__ import annotations

import sys

import pytest

if not sys.platform.startswith(("freebsd", "darwin", "linux")):
    pytest.skip("FreeBSD-only tests", allow_module_level=True)


from pathlib import Path
from unittest.mock import patch

from serialx import SerialPortInfo
from serialx.platforms.serial_freebsd import (
    async_freebsd_list_serial_ports,
    freebsd_list_serial_ports,
)

DATA_DIR = Path(__file__).parent / "data" / "freebsd"
SYSCTL_OUTPUT = (DATA_DIR / "sysctl_dev.txt").read_text()
USBCONFIG_OUTPUT = (DATA_DIR / "usbconfig_dump_all_desc.txt").read_text()


async def test_freebsd_list_serial_ports() -> None:
    """Test listing all serial ports with captured FreeBSD command output."""

    def _mock_subprocess_run(args, **kwargs):
        class Result:
            def __init__(self, stdout):
                self.stdout = stdout
                self.returncode = 0

        if args[:3] == ["sysctl", "-e", "dev"]:
            return Result(SYSCTL_OUTPUT)

        if args[:2] == ["usbconfig", "dump_all_desc"]:
            return Result(USBCONFIG_OUTPUT)

        raise ValueError(f"Unexpected command: {args}")

    with patch("subprocess.run", side_effect=_mock_subprocess_run):
        ports_sync = freebsd_list_serial_ports()
        ports_async = await async_freebsd_list_serial_ports()

    assert (
        sorted(ports_sync, key=lambda p: p.device)
        == sorted(ports_async, key=lambda p: p.device)
        == [
            # /dev/cuaU0: FTDI FT232R with custom serial (ugen8.8)
            SerialPortInfo(
                device="/dev/cuaU0",
                resolved_device="/dev/cuaU0",
                vid=0x0403,
                pid=0x6001,
                serial_number="rutabaga",
                manufacturer="FTDI",
                product="FT232R USB UART",
                bcd_device=0x0600,
                interface_description="FT232R USB UART",
                interface_num=0,
            ),
            # /dev/cuaU1: FTDI FT232R (ugen8.2)
            SerialPortInfo(
                device="/dev/cuaU1",
                resolved_device="/dev/cuaU1",
                vid=0x0403,
                pid=0x6001,
                serial_number="A5069RR4",
                manufacturer="FTDI",
                product="FT232R USB UART",
                bcd_device=0x0600,
                interface_description="FT232R USB UART",
                interface_num=0,
            ),
            # /dev/cuaU2: Prolific USB-Serial (ugen8.3)
            SerialPortInfo(
                device="/dev/cuaU2",
                resolved_device="/dev/cuaU2",
                vid=0x067B,
                pid=0x23A3,
                serial_number="DSDCb147613",
                manufacturer="Prolific Technology Inc. ",
                product="USB-Serial Controller ",
                bcd_device=0x0605,
                interface_description=None,
                interface_num=0,
            ),
            # /dev/cuaU3: Silicon Labs CP2102 (ugen8.4)
            SerialPortInfo(
                device="/dev/cuaU3",
                resolved_device="/dev/cuaU3",
                vid=0x10C4,
                pid=0xEA60,
                serial_number="41b06ea8",
                manufacturer="Silicon Labs",
                product="CP2102 USB to UART Bridge Controller",
                bcd_device=0x0100,
                interface_description="CP2102 USB to UART Bridge Controller",
                interface_num=0,
            ),
            # /dev/cuaU4: FTDI FT232R (ugen8.5)
            SerialPortInfo(
                device="/dev/cuaU4",
                resolved_device="/dev/cuaU4",
                vid=0x0403,
                pid=0x6001,
                serial_number="A5069RR4",
                manufacturer="FTDI",
                product="FT232R USB UART",
                bcd_device=0x0600,
                interface_description="FT232R USB UART",
                interface_num=0,
            ),
            # /dev/cuaU5: Nabu Casa Home Assistant Connect ZBT-1 (ugen8.6)
            SerialPortInfo(
                device="/dev/cuaU5",
                resolved_device="/dev/cuaU5",
                vid=0x10C4,
                pid=0xEA60,
                serial_number="a28a310e2bedec118f3d4540ad51a8b2",
                manufacturer="Nabu Casa",
                product="Home Assistant Connect ZBT-1",
                bcd_device=0x0100,
                interface_description=None,
                interface_num=0,
            ),
            # /dev/cuaU6: Nabu Casa ZBT-2 (CDC ACM via umodem, ugen8.7)
            SerialPortInfo(
                device="/dev/cuaU6",
                resolved_device="/dev/cuaU6",
                vid=0x303A,
                pid=0x4001,
                serial_number="10B41DE589E4",
                manufacturer="Nabu Casa",
                product="ZBT-2",
                bcd_device=0x0100,
                interface_description="Nabu Casa ZBT-2",
                interface_num=0,
            ),
        ]
    )


async def test_freebsd_list_serial_ports_no_devices() -> None:
    """Test listing when no serial devices are present."""

    def mock_run(args, **kwargs):
        class Result:
            def __init__(self, stdout):
                self.stdout = stdout
                self.returncode = 0

        if args[:3] == ["sysctl", "-e", "dev"]:
            return Result("dev.uhub.0.%parent=xhci0\n")

        if args[:2] == ["usbconfig", "dump_all_desc"]:
            return Result("")

        raise ValueError(f"Unexpected command: {args}")

    with patch("subprocess.run", side_effect=mock_run):
        ports_sync = freebsd_list_serial_ports()
        ports_async = await async_freebsd_list_serial_ports()

    assert ports_sync == ports_async == []
