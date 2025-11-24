"""Darwin serial port implementation."""

import array
import errno
import fcntl
import logging

from .serial_posix import PosixSerial, PosixSerialTransport

LOGGER = logging.getLogger(__name__)

IOSSIOSPEED = 0x80045402


class DarwinSerial(PosixSerial):
    """Darwin serial port implementation."""

    def _set_non_posix_baudrate(self, baudrate: int) -> None:
        """Set the baudrate of the serial port."""
        assert self._fileno is not None

        buffer = array.array("i", [self._baudrate])

        try:
            fcntl.ioctl(self._fileno, IOSSIOSPEED, buffer)
        except OSError as exc:
            if exc.errno == errno.ENOTTY:
                LOGGER.debug("Device is not a serial port, cannot set baudrate")


class DarwinSerialTransport(PosixSerialTransport):
    """Darwin asyncio serial port transport."""

    _serial_cls = DarwinSerial
