"""Darwin serial port implementation."""

from __future__ import annotations

import array
import errno
import fcntl
import logging
import termios

from serialx._serialx_rust import list_serial_ports_darwin_impl
from serialx.common import SerialPortInfo

from .serial_posix import PosixSerial, PosixSerialTransport

LOGGER = logging.getLogger(__name__)

IOSSIOSPEED = 0x80045402

# When we need to set a non-POSIX baudrate, we set the baudrates to a known default and
# then override
NON_POSIX_FALLBACK_BAUDRATE_CONST = termios.B115200


class DarwinSerial(PosixSerial):
    """Darwin serial port implementation."""

    def _build_ispeed(self) -> int:
        return (
            NON_POSIX_FALLBACK_BAUDRATE_CONST
            if self._has_non_posix_baudrate
            else getattr(termios, f"B{self._baudrate}")
        )

    def _build_ospeed(self) -> int:
        return (
            NON_POSIX_FALLBACK_BAUDRATE_CONST
            if self._has_non_posix_baudrate
            else getattr(termios, f"B{self._baudrate}")
        )

    @property
    def _has_non_posix_baudrate(self) -> bool:
        return hasattr(termios, f"B{self._baudrate}")

    def _set_non_posix_baudrate(self, baudrate: int) -> None:
        """Set the baudrate of the serial port."""
        assert self._fileno is not None

        buffer = array.array("i", [baudrate])

        try:
            fcntl.ioctl(self._fileno, IOSSIOSPEED, buffer)
        except OSError as exc:
            if exc.errno == errno.ENOTTY:
                LOGGER.debug("Device is not a serial port, cannot set baudrate")

    def _after_configure_port(self) -> None:  # noqa: C901
        if self._has_non_posix_baudrate:
            LOGGER.debug("Setting non-POSIX baudrate %d", self._baudrate)
            self._set_non_posix_baudrate(self._baudrate)


class DarwinSerialTransport(PosixSerialTransport):
    """Darwin asyncio serial port transport."""

    _serial_cls = DarwinSerial


def darwin_list_serial_ports() -> list[SerialPortInfo]:
    """List available serial ports on macOS using native IOKit via Rust."""
    return [
        SerialPortInfo(
            device=port.device,
            resolved_device=port.device,
            vid=port.vid,
            pid=port.pid,
            serial_number=port.serial_number,
            manufacturer=port.manufacturer,
            product=port.product,
            bcd_device=port.bcd_device,
            interface_description=port.interface_description,
            interface_num=port.interface_num,
        )
        for port in list_serial_ports_darwin_impl()
    ]
