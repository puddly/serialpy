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
from typing import Any

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
    Serial as SerialxSerial,
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


class CompatSerial(SerialxSerial):
    """Compatibility base class, maintaining runtime-compatibility with pyserial."""

    def __init__(
        self, *args: Any, _wrap_exceptions: bool = True, **kwargs: Any
    ) -> None:
        super().__init__(*args, _wrap_exceptions=_wrap_exceptions, **kwargs)

    @classmethod
    def from_url(cls, url: str, *args: Any, **kwargs: Any) -> BaseSerial:
        """Create the appropriate serial port subclass for the given URL."""
        return super().from_url(url, *args, _wrap_exceptions=True, **kwargs)  # type:ignore[call-arg]


Serial = CompatSerial
serial_for_url = CompatSerial.from_url
