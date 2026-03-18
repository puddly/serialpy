"""Compatibility shim for pyserial's ``serial.tools.list_ports`` module."""

from __future__ import annotations

from serialx.tools.list_ports import *  # noqa: F401,F403
from serialx.tools.list_ports import __all__ as _list_ports_all

__all__ = _list_ports_all
