"""FreeBSD serial port implementation."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys

from ..common import SerialPortInfo, register_uri_handler
from .serial_extended_posix import ExtendedPosixSerial, ExtendedPosixSerialTransport

LOGGER = logging.getLogger(__name__)

_PNPINFO_RE = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')
_LOCATION_RE = re.compile(r"(\w+)=(\S+)")
_MANUFACTURER_RE = re.compile(r"iManufacturer\s*=\s*0x\w+\s+<(.+)>")
_PRODUCT_RE = re.compile(r"iProduct\s*=\s*0x\w+\s+<(.+)>")


class FreeBSDSerial(ExtendedPosixSerial):
    """FreeBSD serial port implementation."""


class FreeBSDSerialTransport(ExtendedPosixSerialTransport):
    """FreeBSD serial port transport."""

    _serial_cls = FreeBSDSerial


def _parse_pnpinfo(pnpinfo: str) -> dict[str, str]:
    """Parse a sysctl %pnpinfo value into a dict.

    Example input:
        vendor=0x0403 product=0x6001 sernum="A5069RR4" release=0x0600 ...
    """
    result = {}
    for match in _PNPINFO_RE.finditer(pnpinfo):
        key = match.group(1)
        value = match.group(2) if match.group(2) is not None else match.group(3)
        result[key] = value
    return result


def _parse_location(location: str) -> dict[str, str]:
    """Parse a sysctl %location value into a dict.

    Example input:
        bus=0 hubaddr=1 port=1 devaddr=2 interface=0 ugen=ugen0.2
    """
    result = {}
    for match in _LOCATION_RE.finditer(location):
        result[match.group(1)] = match.group(2)
    return result


def _get_all_usb_strings() -> dict[str, tuple[str | None, str | None]]:
    """Get manufacturer and product strings for all USB devices via usbconfig."""
    result = subprocess.run(
        ["usbconfig", "dump_device_desc"],
        capture_output=True,
        text=True,
        check=True,
    )

    devices: dict[str, tuple[str | None, str | None]] = {}
    current_ugen: str | None = None
    manufacturer: str | None = None
    product: str | None = None

    for line in result.stdout.splitlines():
        if line.startswith("ugen"):
            if current_ugen is not None:
                devices[current_ugen] = (manufacturer, product)
            current_ugen = line.split(":")[0]
            manufacturer = None
            product = None
            continue

        match = _MANUFACTURER_RE.search(line)
        if match:
            manufacturer = match.group(1)
            continue

        match = _PRODUCT_RE.search(line)
        if match:
            product = match.group(1)

    if current_ugen is not None:
        devices[current_ugen] = (manufacturer, product)

    return devices


def _parse_sysctl(text: str) -> dict[str, str]:
    """Parse `sysctl -e dev` output, folding multi-line values into the prior key."""
    sysctl: dict[str, str] = {}
    current_key = ""

    for line in text.splitlines():
        if line.startswith("dev."):
            current_key, _, value = line.partition("=")
            sysctl[current_key] = value
        else:
            sysctl[current_key] += "\n" + line

    return sysctl


def freebsd_list_serial_ports() -> list[SerialPortInfo]:
    """List available serial ports on FreeBSD."""
    sysctl_text = subprocess.run(
        ["sysctl", "-e", "dev"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    sysctl = _parse_sysctl(sysctl_text)

    usb_strings = _get_all_usb_strings()
    results = []

    for key, value in sysctl.items():
        if not key.endswith(".ttyname"):
            continue

        parent = key.removesuffix(".ttyname")
        ttyname = value
        pnpinfo = _parse_pnpinfo(sysctl[f"{parent}.%pnpinfo"])
        location = _parse_location(sysctl[f"{parent}.%location"])

        manufacturer, product = usb_strings.get(location["ugen"], (None, None))

        device = f"/dev/cua{ttyname}"
        vid_str = pnpinfo.get("vendor")
        pid_str = pnpinfo.get("product")
        release_str = pnpinfo.get("release")

        results.append(
            SerialPortInfo(
                device=device,
                resolved_device=device,
                vid=int(vid_str, 16) if vid_str else None,
                pid=int(pid_str, 16) if pid_str else None,
                serial_number=pnpinfo.get("sernum"),
                manufacturer=manufacturer,
                product=product,
                bcd_device=int(release_str, 16) if release_str else None,
                interface_description=None,
                interface_num=(
                    int(location["interface"]) if "interface" in location else None
                ),
            )
        )

    return results


async def async_freebsd_list_serial_ports() -> list[SerialPortInfo]:
    """List serial ports on FreeBSD, async."""
    return await asyncio.to_thread(freebsd_list_serial_ports)


if sys.platform.startswith("freebsd"):
    register_uri_handler(
        scheme="device://",
        unique_scheme="freebsd://",
        sync_cls=FreeBSDSerial,
        async_transport_cls=FreeBSDSerialTransport,
        list_serial_ports_func=freebsd_list_serial_ports,
        async_list_serial_ports_func=async_freebsd_list_serial_ports,
        weight=3,
        strip_uri_scheme=True,
    )
