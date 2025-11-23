"""Serialpy serial port implementation."""

import sys

from .async_serial import create_serial_connection, open_serial_connection
from .common import ModemBits
from .platforms import Serial, SerialTransport

__all__ = [
    "ModemBits",
    "create_serial_connection",
    "open_serial_connection",
    "Serial",
    "SerialTransport",
]

_MODULES_TO_PATCH = ["serial", "serial_asyncio", "serial_asyncio_fast"]


def patch():
    """Patch sys.modules to replace PySerial imports with SerialPy."""

    for module in _MODULES_TO_PATCH:
        sys.modules[module] = sys.modules[__name__]


class SerialException(Exception):
    pass
