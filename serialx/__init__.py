"""serialx serial port implementation."""

import sys

from .async_serial import (
    SerialStreamWriter,
    create_serial_connection,
    open_serial_connection,
)
from .common import ModemPins, Parity, PinState, SerialPortInfo, StopBits
from .platforms import Serial, SerialTransport, list_serial_ports

__all__ = [
    "create_serial_connection",
    "list_serial_ports",
    "open_serial_connection",
    "ModemPins",
    "Parity",
    "PinState",
    "Serial",
    "SerialPortInfo",
    "SerialStreamWriter",
    "SerialTransport",
    "StopBits",
]

_MODULES_TO_PATCH = ["serial", "serial_asyncio", "serial_asyncio_fast"]


def patch_pyserial():
    """Patch sys.modules to replace PySerial imports with serialx."""

    for module in _MODULES_TO_PATCH:
        sys.modules[module] = sys.modules[__name__]


class SerialException(Exception):
    pass
