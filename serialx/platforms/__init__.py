"""Individual platform implementations."""

import sys

try:
    import termios  # noqa: F401
except ImportError:
    maybe_posix = False
else:
    maybe_posix = True

if sys.platform == "win32":
    from .serial_win32 import (
        Win32Serial as Serial,
        Win32SerialTransport as SerialTransport,
        win32_list_serial_ports as list_serial_ports,
    )
elif sys.platform == "linux":
    from .serial_linux import (
        LinuxSerial as Serial,
        LinuxSerialTransport as SerialTransport,
        linux_list_serial_ports as list_serial_ports,
    )
elif sys.platform == "darwin":
    from .serial_darwin import (
        DarwinSerial as Serial,
        DarwinSerialTransport as SerialTransport,
        darwin_list_serial_ports as list_serial_ports,
    )
elif sys.platform.startswith("freebsd"):
    from .serial_freebsd import (
        FreeBSDSerial as Serial,
        FreeBSDSerialTransport as SerialTransport,
        freebsd_list_serial_ports as list_serial_ports,
    )
elif maybe_posix:
    from .serial_extended_posix import is_extended_posix

    if is_extended_posix():
        from .serial_extended_posix import (
            ExtendedPosixSerial as Serial,
            ExtendedPosixSerialTransport as SerialTransport,
        )
    else:
        from .serial_posix import (
            PosixSerial as Serial,
            PosixSerialTransport as SerialTransport,
        )
    from .serial_posix import posix_list_serial_ports as list_serial_ports
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")

__all__ = [
    "Serial",
    "SerialTransport",
    "list_serial_ports",
]
