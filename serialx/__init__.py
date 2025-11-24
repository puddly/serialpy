"""serialx serial port implementation."""

import sys

from .async_serial import create_serial_connection, open_serial_connection
from .common import ModemBits, Parity, StopBits
from .platforms import Serial, SerialTransport

__all__ = [
    "create_serial_connection",
    "open_serial_connection",
    "ModemBits",
    "Parity",
    "Serial",
    "SerialTransport",
    "StopBits",
]

_MODULES_TO_PATCH = ["serial", "serial_asyncio", "serial_asyncio_fast"]


def patch():
    """Patch sys.modules to replace PySerial imports with serialx."""

    for module in _MODULES_TO_PATCH:
        sys.modules[module] = sys.modules[__name__]


class SerialException(Exception):
    pass
