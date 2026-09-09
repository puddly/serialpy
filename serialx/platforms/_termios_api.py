"""termios entry points re-raising `termios.error` as `OSError`."""

from __future__ import annotations

from collections.abc import Callable
import functools
import sys
import termios
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")


def wrap_termios_error(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    @functools.wraps(fn)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return fn(*args, **kwargs)
        except termios.error as e:
            raise OSError(*e.args) from e

    return wrapper


tcgetattr = wrap_termios_error(termios.tcgetattr)
tcsetattr = wrap_termios_error(termios.tcsetattr)
tcsendbreak = wrap_termios_error(termios.tcsendbreak)
tcdrain = wrap_termios_error(termios.tcdrain)
tcflush = wrap_termios_error(termios.tcflush)
tcflow = wrap_termios_error(termios.tcflow)
if sys.version_info >= (3, 11):
    tcgetwinsize = wrap_termios_error(termios.tcgetwinsize)
    tcsetwinsize = wrap_termios_error(termios.tcsetwinsize)
