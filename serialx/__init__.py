"""serialx serial port implementation."""

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
from .compat import (
    CR,
    EIGHTBITS,
    LF,
    PARITY_EVEN,
    PARITY_NONE,
    PARITY_ODD,
    SEVENBITS,
    STOPBITS_ONE,
    STOPBITS_TWO,
    SerialTimeoutException,
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
    # Compatibility with pyserial
    "SerialTimeoutException",
    "EIGHTBITS",
    "SEVENBITS",
    "PARITY_NONE",
    "PARITY_EVEN",
    "PARITY_ODD",
    "STOPBITS_ONE",
    "STOPBITS_TWO",
    "CR",
    "LF",
]


def serial_for_url(url, *args, **kwargs) -> BaseSerial:
    """Create a serial port for the given URL."""
    serial_cls, _serial_transport = get_serial_classes(url)
    return serial_cls(url, *args, **kwargs)
