"""Compatibility shim for ``serial.tools.list_ports_common`` module."""

from __future__ import annotations

from serialx.tools.list_ports_common import *  # noqa: F401,F403
from serialx.tools.list_ports_common import __all__ as _list_ports_common_all

__all__ = _list_ports_common_all
