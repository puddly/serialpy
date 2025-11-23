"""Darwin serial port implementation."""

import array
import fcntl

from .serial_posix import PosixSerial, PosixSerialTransport

IOSSIOSPEED = 0x80045402


class DarwinSerial(PosixSerial):
    """Darwin serial port implementation."""

    def _set_non_posix_baudrate(self, baudrate: int) -> None:
        """Set the baudrate of the serial port."""
        assert self._fileno is not None

        buffer = array.array("i", [self._baudrate])
        fcntl.ioctl(self._fileno, IOSSIOSPEED, buffer)


class DarwinSerialTransport(PosixSerialTransport):
    """Darwin asyncio serial port transport."""

    _serial_cls = DarwinSerial
