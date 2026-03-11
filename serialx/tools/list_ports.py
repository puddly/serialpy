"""Pyserial compatibility module."""

from collections.abc import Iterable
import re

from serialx import SerialPortInfo, list_serial_ports


def grep(regexp: str, include_links: bool = False) -> Iterable[SerialPortInfo]:
    """Search for ports using a regular expression."""
    pattern = re.compile(regexp, flags=re.IGNORECASE)

    for info in list_serial_ports():
        if pattern.search(str(info.device)) or pattern.search(info.product or ""):
            yield info


comports = list_serial_ports
__all__ = ["comports", "grep"]

if __name__ == "__main__":
    for port in comports():
        print(port)  # noqa: T201
