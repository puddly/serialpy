import sys

from serialpy.serial import ModemBits, Serial, STOPBITS_ONE, PARITY_NONE
from serialpy.async_serial import SerialTransport, create_serial_connection, open_serial_connection


def patch():
    """
    Patches `sys.modules` so that SerialPy replaces PySerial/PySerial-asyncio imports
    """
    sys.modules["serial"] = sys.modules[__name__]
    sys.modules["serial_asyncio"] = sys.modules[__name__]
