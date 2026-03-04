"""Pyserial compatibility module."""

from serialx import list_serial_ports as comports

__all__ = ["comports"]

if __name__ == "__main__":
    for port in comports():
        print(port)
