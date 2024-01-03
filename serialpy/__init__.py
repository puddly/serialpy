import sys

from serialpy.serial import ModemBits, Serial, STOPBITS_ONE, PARITY_NONE
from serialpy.async_serial import SerialTransport, create_serial_connection, open_serial_connection

_MODULES_TO_PATCH = ["serial", "serial_asyncio", "serial_asyncio_fast"]


def patch():
    """
    Patches `sys.modules` so that SerialPy replaces PySerial/PySerial-asyncio imports
    """

    for module in _MODULES_TO_PATCH:
        sys.modules[module] = sys.modules[__name__]
