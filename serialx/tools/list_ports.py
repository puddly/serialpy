"""Pyserial compatibility module."""

from serialx import list_serial_ports as comports

__all__ = ["comports"]
