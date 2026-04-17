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
    register_uri_handler,
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

# Backwards compatibility export
serial_for_url = Serial.from_url

__all__ = [
    "create_serial_connection",
    "get_serial_classes",
    "list_serial_ports",
    "open_serial_connection",
    "register_uri_handler",
    "serial_for_url",
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
