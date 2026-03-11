"""Pyserial compatibility constants."""

from .common import Parity, StopBits

SerialTimeoutException = TimeoutError

EIGHTBITS = 8
SEVENBITS = 7

PARITY_NONE = Parity.NONE
PARITY_EVEN = Parity.EVEN
PARITY_ODD = Parity.ODD

STOPBITS_ONE = StopBits.ONE
STOPBITS_TWO = StopBits.TWO

CR = b"\r"
LF = b"\n"
