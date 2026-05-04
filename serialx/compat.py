"""Pyserial compatibility constants."""

from .common import Parity, StopBits

SerialTimeoutException = TimeoutError

FIVEBITS = 5
SIXBITS = 6
SEVENBITS = 7
EIGHTBITS = 8

PARITY_NONE = Parity.NONE
PARITY_EVEN = Parity.EVEN
PARITY_ODD = Parity.ODD
PARITY_MARK = Parity.MARK
PARITY_SPACE = Parity.SPACE

STOPBITS_ONE = StopBits.ONE
STOPBITS_ONE_POINT_FIVE = StopBits.ONE_POINT_FIVE
STOPBITS_TWO = StopBits.TWO

CR = b"\r"
LF = b"\n"
