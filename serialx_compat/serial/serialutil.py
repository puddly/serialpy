"""Compatibility shim for pyserial's ``serial.serialutil`` module."""

from __future__ import annotations

from serialx.serialutil import *  # noqa: F401,F403
from serialx.serialutil import __all__ as _serialutil_all

__all__ = _serialutil_all
