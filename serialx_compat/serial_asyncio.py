"""Compatibility shim for ``serial_asyncio`` backed by serialx."""

from __future__ import annotations

from importlib.metadata import version

from serialx import (
    SerialStreamWriter,
    SerialTransport,
    create_serial_connection,
    open_serial_connection,
)

VERSION = version("serialx-compat")
__version__ = VERSION

__all__ = [
    "SerialStreamWriter",
    "SerialTransport",
    "create_serial_connection",
    "open_serial_connection",
    "VERSION",
    "__version__",
]
