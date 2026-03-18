"""Compatibility shim for pyserial's ``serial`` package backed by serialx."""

from __future__ import annotations

import importlib.util

if importlib.util.find_spec("serial.serialcli"):
    raise RuntimeError(
        "serialx-compat detected a mixed installation with pyserial files. Uninstall"
        " pyserial, pyserial-asyncio, and pyserial-asyncio-fast before installing"
        " serialx-compat."
    )

from importlib.metadata import version

from serialx import (
    CR,
    EIGHTBITS,
    LF,
    PARITY_EVEN,
    PARITY_NONE,
    PARITY_ODD,
    SEVENBITS,
    STOPBITS_ONE,
    STOPBITS_TWO,
    BaseSerial,
    BaseSerialTransport,
    ModemPins,
    Parity,
    PinState,
    Serial,
    SerialException,
    SerialPortInfo,
    SerialStreamWriter,
    SerialTimeoutException,
    SerialTransport,
    StopBits,
    UnsupportedSetting,
    create_serial_connection,
    get_serial_classes,
    list_serial_ports,
    open_serial_connection,
    serial_for_url,
)

VERSION = version("serialx-compat")
__version__ = VERSION

__all__ = [
    "create_serial_connection",
    "get_serial_classes",
    "list_serial_ports",
    "open_serial_connection",
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
    "VERSION",
    "__version__",
]
