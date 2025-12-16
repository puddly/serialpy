"""Individual platform implementations."""

import sys

if sys.platform == "win32":
    from .serial_win32 import (
        Win32Serial as Serial,
        Win32SerialTransport as SerialTransport,
        win32_list_serial_ports as list_serial_ports,
    )
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
        darwin_list_serial_ports as list_serial_ports,
    )
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")

__all__ = [
    "Serial",
    "SerialTransport",
    "list_serial_ports",
]
