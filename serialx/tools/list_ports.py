"""Pyserial compatibility module."""

from collections.abc import Iterable

from serialx import SerialPortInfo, list_serial_ports


def grep(regexp: str) -> Iterable[SerialPortInfo]:
    """Search for ports using a regular expression."""
    pattern = re.compile(regexp, flags=re.IGNORECASE)

    for info in list_serial_ports(include_links):
        if pattern.search(info.device) or pattern.search(info.description):
            yield info


comports = list_serial_ports
__all__ = ["comports", "grep"]

if __name__ == "__main__":
    for port in comports():
        print(port)  # noqa: T201
