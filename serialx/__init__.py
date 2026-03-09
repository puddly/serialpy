"""serialx serial port implementation."""

import sys

from .async_serial import (
    SerialStreamWriter,
    create_serial_connection,
    open_serial_connection,
)
from .common import (
    BaseSerial,
    BaseSerialTransport,
    ModemPins,
    Parity,
    PinState,
    SerialException,
    SerialPortInfo,
    StopBits,
    UnsupportedSetting,
    get_serial_classes,
)
from .platforms import Serial, SerialTransport, list_serial_ports

__all__ = [
    "create_serial_connection",
    "get_serial_classes",
    "list_serial_ports",
    "open_serial_connection",
    "ModemPins",
    "Parity",
    "PinState",
    "BaseSerial",
    "BaseSerialTransport",
    "Serial",
    "SerialException",
    "UnsupportedSetting",
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


def serial_for_url(url, *args, **kwargs) -> BaseSerial:
    """Create a serial port for the given URL."""
    serial_cls, _serial_transport = get_serial_classes(url)
    return serial_cls(url, *args, **kwargs)
