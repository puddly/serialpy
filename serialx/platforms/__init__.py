"""Individual platform implementations."""

import sys

from serialx.common import SerialPortInfo

try:
    from serialx._serialx_rust import list_serial_ports_impl
except ImportError:
    list_serial_ports_impl = None


def list_serial_ports_native() -> list[SerialPortInfo]:
    """List available serial ports via the Rust `serial` package."""

    if list_serial_ports_impl is None:
        raise RuntimeError(
            "This platform does not support listing serial ports via the native Rust"
            " implementation."
        )

    return [
        SerialPortInfo(
            device=port_name,
            resolved_device=port_name,
            vid=vid,
            pid=pid,
            serial_number=serial_number,
            manufacturer=manufacturer,
            product=product,
        )
        for (
            port_name,
            vid,
            pid,
            serial_number,
            manufacturer,
            product,
        ) in list_serial_ports_impl()
    ]


if sys.platform == "win32":
    from .serial_win32 import (
        Win32Serial as Serial,
        Win32SerialTransport as SerialTransport,
    )

    list_serial_ports = list_serial_ports_native
elif sys.platform == "linux":
    from .serial_posix import (
        PosixSerial as Serial,
        PosixSerialTransport as SerialTransport,
        posix_list_serial_ports as list_serial_ports,
    )
elif sys.platform == "darwin":
    from .serial_darwin import (
        DarwinSerial as Serial,
        DarwinSerialTransport as SerialTransport,
    )

    list_serial_ports = list_serial_ports_native
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")

__all__ = [
    "Serial",
    "SerialTransport",
    "list_serial_ports",
]
