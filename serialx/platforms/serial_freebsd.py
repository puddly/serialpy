"""FreeBSD serial port implementation."""

from __future__ import annotations

import logging
from pathlib import Path
import re
import subprocess

from ..common import SerialPortInfo
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


def _run_sysctl(*args: str) -> str:
    """Run sysctl and return stdout."""
    result = subprocess.run(
        ["sysctl", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


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


def _get_usb_strings(ugen: str) -> tuple[str | None, str | None]:
    """Get manufacturer and product strings from usbconfig."""
    result = subprocess.run(
        ["usbconfig", "-d", ugen, "dump_device_desc"],
        capture_output=True,
        text=True,
        check=True,
    )

    manufacturer = None
    product = None

    for line in result.stdout.splitlines():
        match = _MANUFACTURER_RE.search(line)
        if match:
            manufacturer = match.group(1)

        match = _PRODUCT_RE.search(line)
        if match:
            product = match.group(1)

    return manufacturer, product


def freebsd_list_serial_ports() -> list[SerialPortInfo]:
    """List available serial ports on FreeBSD."""
    results = []
    ttyname_nodes = [
        n for n in _run_sysctl("-N", "dev").splitlines() if n.endswith(".ttyname")
    ]

    for node in ttyname_nodes:
        parent = node.removesuffix(".ttyname")

        output = _run_sysctl(
            f"{parent}.ttyname",
            f"{parent}.%pnpinfo",
            f"{parent}.%location",
        )

        values = {}
        for line in output.splitlines():
            key, _, value = line.partition(": ")
            values[key.strip()] = value.strip()

        ttyname = values[f"{parent}.ttyname"]
        pnpinfo_raw = values[f"{parent}.%pnpinfo"]
        location_raw = values[f"{parent}.%location"]

        pnpinfo = _parse_pnpinfo(pnpinfo_raw)
        location = _parse_location(location_raw)

        device = Path(f"/dev/cua{ttyname}")
        manufacturer, product = _get_usb_strings(location["ugen"])

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
