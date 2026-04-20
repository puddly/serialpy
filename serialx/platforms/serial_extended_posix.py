"""Extended POSIX serial port implementation.

Adds non-POSIX features that are available on all major UNIX-like platforms.
"""

from __future__ import annotations

import termios

from ..common import register_uri_handler
from .serial_posix import PosixSerial, PosixSerialTransport

CRTSCTS = getattr(termios, "CRTSCTS", getattr(termios, "CNEW_RTSCTS", None))


def is_extended_posix() -> bool:
    """Check if the platform supports extended POSIX serial features."""
    return CRTSCTS is not None


class ExtendedPosixSerial(PosixSerial):
    """Extended POSIX serial port with CRTSCTS support."""

    def _build_flow_control_flags(self) -> tuple[int, int]:
        iflag = 0x00000000
        cflag = 0x00000000

        if self._xonxoff:
            iflag |= termios.IXON | termios.IXOFF | termios.IXANY

        if self._rtscts:
            assert CRTSCTS is not None
            cflag |= CRTSCTS

        return (iflag, cflag)


class ExtendedPosixSerialTransport(PosixSerialTransport):
    """Extended POSIX serial port transport with CRTSCTS support."""

    _serial_cls = ExtendedPosixSerial


if is_extended_posix():
    register_uri_handler(
        scheme="device://",
        unique_scheme="extended-posix://",
        sync_cls=ExtendedPosixSerial,
        async_transport_cls=ExtendedPosixSerialTransport,
        weight=2,
        strip_uri_scheme=True,
    )
