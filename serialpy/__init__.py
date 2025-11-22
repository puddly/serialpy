"""Serialpy serial port implementation."""

import sys

from serialpy.async_serial import create_serial_connection, open_serial_connection
from serialpy.common import PARITY_NONE, STOPBITS_ONE, ModemBits

if sys.platform == "win32":
    from .serial_win32 import Win32Serial as Serial
else:
    from .serial_posix import PosixSerial as Serial

__all__ = [
    "ModemBits",
    "STOPBITS_ONE",
    "PARITY_NONE",
    "create_serial_connection",
    "open_serial_connection",
    "Serial",
]

_MODULES_TO_PATCH = ["serial", "serial_asyncio", "serial_asyncio_fast"]


def patch():
    """Patch sys.modules to replace PySerial imports with SerialPy."""

    for module in _MODULES_TO_PATCH:
        sys.modules[module] = sys.modules[__name__]


class SerialException(Exception):
    pass
