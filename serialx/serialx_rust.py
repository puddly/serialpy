"""Centralized access to optional Rust extensions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from serialx._serialx_rust import RustSerialPortInfo

LOGGER = logging.getLogger(__name__)

__all__ = ["list_serial_ports_impl"]

try:
    from serialx._serialx_rust import list_serial_ports_impl
except ImportError:

    def list_serial_ports_impl() -> list[RustSerialPortInfo]:
        """Return an empty list when the Rust extension is unavailable."""
        LOGGER.warning(
            "serialx Rust extension failed to load; serial port enumeration will "
            "not be available"
        )

        return []
