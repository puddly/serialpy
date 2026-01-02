"""Tests for Linux serial port listing."""

from __future__ import annotations

import sys

import pytest

if sys.platform != "linux":
    pytest.skip("Linux-only tests", allow_module_level=True)

from pathlib import Path
from unittest.mock import patch

from serialx.common import SerialPortInfo
from serialx.platforms import serial_posix
from serialx.platforms.serial_posix import posix_list_serial_ports


def create_usb_serial_device(
    sys_root: Path,
    dev_root: Path,
    *,
    tty_name: str,
    device_path: str,
    vid: str,
    pid: str,
    serial: str,
    manufacturer: str,
    product: str,
    bcd_device: str,
    interface: str | None = None,
    interface_num: str = "00",
    by_id_name: str | None = None,
) -> None:
    """Create a fake usb-serial device (ttyUSB*) in the fake sysfs."""
    full_device_path = sys_root / device_path.lstrip("/")
    ttyusb_dir = full_device_path.parent.parent  # .../ttyUSB0
    interface_path = ttyusb_dir.parent  # .../1-1.1.1.1:1.0
    usb_device_path = interface_path.parent  # .../1-1.1.1.1

    tty_class = sys_root / "class/tty" / tty_name
    tty_class.parent.mkdir(parents=True, exist_ok=True)
    tty_class.symlink_to(Path("../..") / device_path.lstrip("/"))

    full_device_path.mkdir(parents=True, exist_ok=True)
    (full_device_path / "device").symlink_to(ttyusb_dir)

    ttyusb_dir.mkdir(parents=True, exist_ok=True)

    # Create driver and subsystem directories, then symlink to them
    driver_dir = sys_root / "bus/usb-serial/drivers/cp210x"
    driver_dir.mkdir(parents=True, exist_ok=True)
    (ttyusb_dir / "driver").symlink_to(driver_dir)

    subsystem_dir = sys_root / "bus/usb-serial"
    subsystem_dir.mkdir(parents=True, exist_ok=True)
    (ttyusb_dir / "subsystem").symlink_to(subsystem_dir)

    usb_device_path.mkdir(parents=True, exist_ok=True)
    (usb_device_path / "idVendor").write_text(vid + "\n")
    (usb_device_path / "idProduct").write_text(pid + "\n")
    (usb_device_path / "serial").write_text(serial + "\n")
    (usb_device_path / "manufacturer").write_text(manufacturer + "\n")
    (usb_device_path / "product").write_text(product + "\n")
    (usb_device_path / "bcdDevice").write_text(bcd_device + "\n")

    # Interface string and number are at the USB interface level
    interface_path.mkdir(parents=True, exist_ok=True)
    (interface_path / "bInterfaceNumber").write_text(interface_num + "\n")
    if interface is not None:
        (interface_path / "interface").write_text(interface + "\n")

    if by_id_name:
        by_id_dir = dev_root / "serial/by-id"
        by_id_dir.mkdir(parents=True, exist_ok=True)
        (by_id_dir / by_id_name).symlink_to(dev_root / tty_name)


def create_cdc_acm_device(
    sys_root: Path,
    dev_root: Path,
    *,
    tty_name: str,
    device_path: str,
    vid: str,
    pid: str,
    serial: str,
    manufacturer: str,
    product: str,
    bcd_device: str,
    interface: str | None = None,
    interface_num: str = "00",
    by_id_name: str | None = None,
) -> None:
    """Create a fake CDC ACM device (ttyACM*) in the fake sysfs."""
    full_device_path = sys_root / device_path.lstrip("/")
    interface_path = full_device_path.parent.parent  # .../1-1.2:1.0
    usb_device_path = interface_path.parent  # .../1-1.2

    tty_class = sys_root / "class/tty" / tty_name
    tty_class.parent.mkdir(parents=True, exist_ok=True)
    tty_class.symlink_to(Path("../..") / device_path.lstrip("/"))

    full_device_path.mkdir(parents=True, exist_ok=True)
    (full_device_path / "device").symlink_to(interface_path)

    interface_path.mkdir(parents=True, exist_ok=True)

    # Create driver and subsystem directories, then symlink to them
    driver_dir = sys_root / "bus/usb/drivers/cdc_acm"
    driver_dir.mkdir(parents=True, exist_ok=True)
    (interface_path / "driver").symlink_to(driver_dir)

    subsystem_dir = sys_root / "bus/usb"
    subsystem_dir.mkdir(parents=True, exist_ok=True)
    (interface_path / "subsystem").symlink_to(subsystem_dir)

    usb_device_path.mkdir(parents=True, exist_ok=True)
    (usb_device_path / "idVendor").write_text(vid + "\n")
    (usb_device_path / "idProduct").write_text(pid + "\n")
    (usb_device_path / "serial").write_text(serial + "\n")
    (usb_device_path / "manufacturer").write_text(manufacturer + "\n")
    (usb_device_path / "product").write_text(product + "\n")
    (usb_device_path / "bcdDevice").write_text(bcd_device + "\n")

    # Interface string and number are at the USB interface level
    (interface_path / "bInterfaceNumber").write_text(interface_num + "\n")
    if interface is not None:
        (interface_path / "interface").write_text(interface + "\n")

    if by_id_name:
        by_id_dir = dev_root / "serial/by-id"
        by_id_dir.mkdir(parents=True, exist_ok=True)
        (by_id_dir / by_id_name).symlink_to(dev_root / tty_name)


def create_native_serial_device(
    sys_root: Path,
    dev_root: Path,
    *,
    tty_name: str,
    device_path: str,
) -> None:
    """Create a fake native serial device (ttyAMA*) in the fake sysfs."""
    full_device_path = sys_root / device_path.lstrip("/")
    serial_base_path = full_device_path.parent.parent

    tty_class = sys_root / "class/tty" / tty_name
    tty_class.parent.mkdir(parents=True, exist_ok=True)
    tty_class.symlink_to(Path("../..") / device_path.lstrip("/"))

    full_device_path.mkdir(parents=True, exist_ok=True)
    (full_device_path / "device").symlink_to(serial_base_path)

    serial_base_path.mkdir(parents=True, exist_ok=True)

    # Create driver and subsystem directories, then symlink to them
    driver_dir = sys_root / "bus/serial-base/drivers/port"
    driver_dir.mkdir(parents=True, exist_ok=True)
    (serial_base_path / "driver").symlink_to(driver_dir)

    subsystem_dir = sys_root / "bus/serial-base"
    (serial_base_path / "subsystem").symlink_to(subsystem_dir)


@pytest.fixture
def fake_sysfs(tmp_path):
    """Create a fake sysfs structure mimicking Home Assistant OS with a few devices."""
    sys_root = tmp_path / "sys"
    dev_root = tmp_path / "dev"
    sys_root.mkdir()
    dev_root.mkdir()

    # /dev/ttyUSB0: CP2102 through hub
    create_usb_serial_device(
        sys_root,
        dev_root,
        tty_name="ttyUSB0",
        device_path="devices/platform/soc/fe980000.usb/usb1/1-1/1-1.1/1-1.1.1/1-1.1.1.1/1-1.1.1.1:1.0/ttyUSB0/tty/ttyUSB0",
        vid="10c4",
        pid="ea60",
        serial="ec4903cb",
        manufacturer="Silicon Labs",
        product="CP2102 USB to UART Bridge Controller",
        bcd_device="0100",
        interface="CP2102 USB to UART Bridge Controller",
        by_id_name="usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_ec4903cb-if00-port0",
    )

    # /dev/ttyUSB1: Another CP2102 through hub
    create_usb_serial_device(
        sys_root,
        dev_root,
        tty_name="ttyUSB1",
        device_path="devices/platform/soc/fe980000.usb/usb1/1-1/1-1.1/1-1.1.1/1-1.1.1.4/1-1.1.1.4:1.0/ttyUSB1/tty/ttyUSB1",
        vid="10c4",
        pid="ea60",
        serial="41b06ea8",
        manufacturer="Silicon Labs",
        product="CP2102 USB to UART Bridge Controller",
        bcd_device="0100",
        interface="CP2102 USB to UART Bridge Controller",
        by_id_name="usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_41b06ea8-if00-port0",
    )

    # /dev/ttyUSB2: FTDI through hub
    create_usb_serial_device(
        sys_root,
        dev_root,
        tty_name="ttyUSB2",
        device_path="devices/platform/soc/fe980000.usb/usb1/1-1/1-1.1/1-1.1.2/1-1.1.2:1.0/ttyUSB2/tty/ttyUSB2",
        vid="0403",
        pid="6001",
        serial="A5069RR4",
        manufacturer="FTDI",
        product="FT232R USB UART",
        bcd_device="0600",
        interface="FT232R USB UART",
        by_id_name="usb-FTDI_FT232R_USB_UART_A5069RR4-if00-port0",
    )

    # /dev/ttyUSB3: Prolific through hub (no interface string)
    create_usb_serial_device(
        sys_root,
        dev_root,
        tty_name="ttyUSB3",
        device_path="devices/platform/soc/fe980000.usb/usb1/1-1/1-1.1/1-1.1.4/1-1.1.4:1.0/ttyUSB3/tty/ttyUSB3",
        vid="067b",
        pid="23a3",
        serial="DSDCb147613",
        manufacturer="Prolific Technology Inc.",
        product="USB-Serial Controller",
        bcd_device="0605",
        interface=None,
        by_id_name="usb-Prolific_Technology_Inc._USB-Serial_Controller_DSDCb147613-if00-port0",
    )

    # /dev/ttyUSB4: Another FTDI with custom serial
    create_usb_serial_device(
        sys_root,
        dev_root,
        tty_name="ttyUSB4",
        device_path="devices/platform/soc/fe980000.usb/usb1/1-1/1-1.1/1-1.1.3/1-1.1.3:1.0/ttyUSB4/tty/ttyUSB4",
        vid="0403",
        pid="6001",
        serial="rutabaga",
        manufacturer="FTDI",
        product="FT232R USB UART",
        bcd_device="0600",
        interface="FT232R USB UART",
        by_id_name="usb-FTDI_FT232R_USB_UART_rutabaga-if00-port0",
    )

    # /dev/ttyACM0: ZBT-2 CDC ACM device connected directly
    create_cdc_acm_device(
        sys_root,
        dev_root,
        tty_name="ttyACM0",
        device_path="devices/platform/soc/fe980000.usb/usb1/1-1/1-1.2/1-1.2:1.0/tty/ttyACM0",
        vid="303a",
        pid="4005",
        serial="80B54EEFAE18",
        manufacturer="Nabu Casa",
        product="ZBT-2",
        bcd_device="0100",
        interface="Nabu Casa ZBT-2",
        by_id_name="usb-Nabu_Casa_ZBT-2_80B54EEFAE18-if00",
    )

    # /dev/ttyAMA0: Raspberry Pi native UART
    create_native_serial_device(
        sys_root,
        dev_root,
        tty_name="ttyAMA0",
        device_path="devices/platform/soc/fe201000.serial/fe201000.serial:0/fe201000.serial:0.0/tty/ttyAMA0",
    )

    # /dev/ttyAMA1: Another Raspberry Pi native UART
    create_native_serial_device(
        sys_root,
        dev_root,
        tty_name="ttyAMA1",
        device_path="devices/platform/soc/fe201800.serial/fe201800.serial:0/fe201800.serial:0.0/tty/ttyAMA1",
    )

    # /dev/ttyAMA2: Third Raspberry Pi native UART
    create_native_serial_device(
        sys_root,
        dev_root,
        tty_name="ttyAMA2",
        device_path="devices/platform/soc/fe201a00.serial/fe201a00.serial:0/fe201a00.serial:0.0/tty/ttyAMA2",
    )

    with (
        patch.object(serial_posix, "SYS_ROOT", sys_root),
        patch.object(serial_posix, "DEV_ROOT", dev_root),
    ):
        yield sys_root, dev_root


def test_list_serial_ports_linux(fake_sysfs) -> None:
    """Test listing all serial ports on a system mimicking test-yellow-core."""
    sys_root, dev_root = fake_sysfs

    ports = posix_list_serial_ports()
    assert len(ports) == 9

    ports_by_name = {Path(p.resolved_device).name: p for p in ports}

    # /dev/ttyUSB0: CP2102
    assert ports_by_name["ttyUSB0"] == SerialPortInfo(
        device=dev_root
        / "serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_ec4903cb-if00-port0",
        resolved_device=dev_root / "ttyUSB0",
        vid=0x10C4,
        pid=0xEA60,
        serial_number="ec4903cb",
        manufacturer="Silicon Labs",
        product="CP2102 USB to UART Bridge Controller",
        bcd_device=0x0100,
        interface_description="CP2102 USB to UART Bridge Controller",
        interface_num=0,
    )

    # /dev/ttyUSB1: Another CP2102
    assert ports_by_name["ttyUSB1"] == SerialPortInfo(
        device=dev_root
        / "serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_41b06ea8-if00-port0",
        resolved_device=dev_root / "ttyUSB1",
        vid=0x10C4,
        pid=0xEA60,
        serial_number="41b06ea8",
        manufacturer="Silicon Labs",
        product="CP2102 USB to UART Bridge Controller",
        bcd_device=0x0100,
        interface_description="CP2102 USB to UART Bridge Controller",
        interface_num=0,
    )

    # /dev/ttyUSB2: FTDI
    assert ports_by_name["ttyUSB2"] == SerialPortInfo(
        device=dev_root / "serial/by-id/usb-FTDI_FT232R_USB_UART_A5069RR4-if00-port0",
        resolved_device=dev_root / "ttyUSB2",
        vid=0x0403,
        pid=0x6001,
        serial_number="A5069RR4",
        manufacturer="FTDI",
        product="FT232R USB UART",
        bcd_device=0x0600,
        interface_description="FT232R USB UART",
        interface_num=0,
    )

    # /dev/ttyUSB3: Prolific
    assert ports_by_name["ttyUSB3"] == SerialPortInfo(
        device=dev_root
        / "serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_DSDCb147613-if00-port0",
        resolved_device=dev_root / "ttyUSB3",
        vid=0x067B,
        pid=0x23A3,
        serial_number="DSDCb147613",
        manufacturer="Prolific Technology Inc.",
        product="USB-Serial Controller",
        bcd_device=0x0605,
        interface_description=None,
        interface_num=0,
    )

    # /dev/ttyUSB4: FTDI with custom serial
    assert ports_by_name["ttyUSB4"] == SerialPortInfo(
        device=dev_root / "serial/by-id/usb-FTDI_FT232R_USB_UART_rutabaga-if00-port0",
        resolved_device=dev_root / "ttyUSB4",
        vid=0x0403,
        pid=0x6001,
        serial_number="rutabaga",
        manufacturer="FTDI",
        product="FT232R USB UART",
        bcd_device=0x0600,
        interface_description="FT232R USB UART",
        interface_num=0,
    )

    # /dev/ttyACM0: ZBT-2 CDC ACM
    assert ports_by_name["ttyACM0"] == SerialPortInfo(
        device=dev_root / "serial/by-id/usb-Nabu_Casa_ZBT-2_80B54EEFAE18-if00",
        resolved_device=dev_root / "ttyACM0",
        vid=0x303A,
        pid=0x4005,
        serial_number="80B54EEFAE18",
        manufacturer="Nabu Casa",
        product="ZBT-2",
        bcd_device=0x0100,
        interface_description="Nabu Casa ZBT-2",
        interface_num=0,
    )

    # /dev/ttyAMA0: Native UART
    assert ports_by_name["ttyAMA0"] == SerialPortInfo(
        device=dev_root / "ttyAMA0",
        resolved_device=dev_root / "ttyAMA0",
        vid=None,
        pid=None,
        serial_number=None,
        manufacturer=None,
        product=None,
        bcd_device=None,
        interface_description=None,
        interface_num=None,
    )

    # /dev/ttyAMA1: Native UART
    assert ports_by_name["ttyAMA1"] == SerialPortInfo(
        device=dev_root / "ttyAMA1",
        resolved_device=dev_root / "ttyAMA1",
        vid=None,
        pid=None,
        serial_number=None,
        manufacturer=None,
        product=None,
        bcd_device=None,
        interface_description=None,
        interface_num=None,
    )

    # /dev/ttyAMA2: Native UART
    assert ports_by_name["ttyAMA2"] == SerialPortInfo(
        device=dev_root / "ttyAMA2",
        resolved_device=dev_root / "ttyAMA2",
        vid=None,
        pid=None,
        serial_number=None,
        manufacturer=None,
        product=None,
        bcd_device=None,
        interface_description=None,
        interface_num=None,
    )
