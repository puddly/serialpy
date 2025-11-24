"""Individual platform implementations."""

import sys

if sys.platform == "win32":
    from .serial_win32 import (
        Win32Serial as Serial,
        Win32SerialTransport as SerialTransport,
    )
elif sys.platform == "linux":
    from .serial_posix import (
        PosixSerial as Serial,
        PosixSerialTransport as SerialTransport,
    )
elif sys.platform == "darwin":
    from .serial_darwin import (
        DarwinSerial as Serial,
        DarwinSerialTransport as SerialTransport,
    )
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")

__all__ = [
    "Serial",
    "SerialTransport",
]
