from __future__ import annotations

import os
import io
import array
import fcntl
import typing
import logging
import termios

from .common import BaseSerial, ModemBits, PARITY_NONE, STOPBITS_ONE, STOPBITS_TWO

LOGGER = logging.getLogger(__name__)

ASYNC_LOW_LATENCY = 1 << 13
CMSPAR = 0o10000000000

if hasattr(termios, "CRTSCTS"):
    CRTSCTS = termios.CRTSCTS
elif hasattr(termios, "CNEW_RTSCTS"):
    CRTSCTS = termios.CNEW_RTSCTS
else:
    raise RuntimeError("termios.CRTSCTS missing")


MODEM_BIT_MAPPING = {
    "le": termios.TIOCM_LE,
    "dtr": termios.TIOCM_DTR,
    "rts": termios.TIOCM_RTS,
    "st": termios.TIOCM_ST,
    "sr": termios.TIOCM_SR,
    "cts": termios.TIOCM_CTS,
    "car": termios.TIOCM_CAR,
    "rng": termios.TIOCM_RNG,
    "dsr": termios.TIOCM_DSR,
}


def modem_bits_mask_of_value(modem_bits: ModemBits, mask: typing.Literal[True, False, None]) -> int:
    result = 0x00000000

    for name, bit in MODEM_BIT_MAPPING.items():
        value = getattr(modem_bits, name)

        if value == mask:
            result |= bit

    return result


def modem_bits_as_int(modem_bits: ModemBits) -> int:
    result = 0x00000000

    for name, bit in MODEM_BIT_MAPPING.items():
        result |= bit if getattr(modem_bits, name) else 0x00000000

    return result


class Serial(BaseSerial):
    def __init__(
        self,
        path,
        baudrate,
        stopbits=STOPBITS_ONE,
        xonxoff=False,
        rtscts=False,
        *,
        fileno=None,
    ):
        super().__init__()
        self._path = path
        self._baudrate = baudrate
        self._stopbits = stopbits
        self._xonxoff = xonxoff
        self._rtscts = rtscts

        if fileno is not None:
            self._fileno = fileno
            self._should_cleanup = False
        else:
            self._fileno = os.open(self._path, os.O_RDWR | os.O_NOCTTY)
            self._should_cleanup = True

        self.configure_port()

    def configure_port(self) -> None:
        if self._fileno is None:
            raise ValueError("Cannot configure, serial port is not open")

        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(self._fileno)

        # Software flow control
        if self._xonxoff:
            iflag |= termios.IXON | termios.IXOFF | termios.IXANY
        else:
            iflag &= ~(termios.IXON | termios.IXOFF | termios.IXANY)

        # Disable interpretation of special characters
        iflag &= ~(
            termios.IGNBRK
            | termios.BRKINT
            | termios.PARMRK
            | termios.INPCK
            | termios.ISTRIP
            | termios.INLCR
            | termios.IGNCR
            | termios.ICRNL
            | termios.IXON
        )

        # Disable output character processing and mapping
        oflag &= ~(termios.OPOST | termios.ONLCR | termios.OCRNL)

        # Allow reads
        cflag |= termios.CREAD

        # Disable modem-specific signal lines
        cflag |= termios.CLOCAL

        # No parity bit
        cflag &= ~(termios.PARENB | termios.PARODD | CMSPAR)

        # Stop bits
        if self._stopbits == STOPBITS_ONE:
            cflag &= ~termios.CSTOPB
        else:
            cflag |= termios.CSTOPB

        # 8 bits per byte
        cflag &= ~termios.CSIZE
        cflag |= termios.CS8

        # Hardware flow control
        if self._rtscts:
            cflag |= CRTSCTS
        else:
            cflag &= ~CRTSCTS

        # Disable canonical mode (newlines)
        lflag &= ~termios.ICANON

        # Disable echo
        lflag &= ~(termios.ECHO | termios.ECHOE | termios.ECHONL)

        # Disable interpretation of special characters
        lflag &= ~termios.ISIG

        # Disable implementation-defined input processing
        lflag &= ~termios.IEXTEN

        # Set baudrate
        ispeed = getattr(termios, f"B{self._baudrate}")
        ospeed = getattr(termios, f"B{self._baudrate}")

        # Non-blocking reads
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0

        termios.tcsetattr(
            self._fileno,
            termios.TCSANOW,
            [iflag, oflag, cflag, lflag, ispeed, ospeed, cc],
        )

        self.set_low_latency(True)

    @property
    def name(self) -> str:
        return self.path

    @property
    def path(self) -> str:
        return self._path

    @property
    def baudrate(self) -> int:
        return self._baudrate

    def get_modem_bits(self) -> ModemBits:
        # A `bytearray` is critical here: `bytes` will not be mutated
        buffer = bytearray((0x00000000).to_bytes(4, "little"))
        fcntl.ioctl(self._fileno, termios.TIOCMGET, buffer)

        n = int.from_bytes(buffer, "little")
        return ModemBits(
            **{name: bool(n & bit) for name, bit in MODEM_BIT_MAPPING.items()}
        )

    def set_low_latency(self, low_latency: bool) -> None:
        if not hasattr(termios, "TIOCGSERIAL"):
            LOGGER.warning("Platform does not support low latency mode")
            return

        buffer = array.array("i", [0x00000000] * 19 * 8)
        fcntl.ioctl(self._fileno, termios.TIOCGSERIAL, buffer)

        LOGGER.debug("Read low latency %r", buffer)

        if low_latency:
            buffer[4] |= ASYNC_LOW_LATENCY
        else:
            buffer[4] &= ~ASYNC_LOW_LATENCY

        LOGGER.debug("Writing low latency %r", buffer)

        fcntl.ioctl(self._fileno, termios.TIOCSSERIAL, buffer)

    def set_modem_bits(self, modem_bits: ModemBits | None = None, **kwargs) -> None:
        all_bits_set = all(
            getattr(self, name) is not None for name in MODEM_BIT_MAPPING.keys()
        )

        if all_bits_set:
            value = modem_bits_as_int(modem_bits)
            LOGGER.debug("Setting all modem bits: 0x%08X", value)
            fcntl.ioctl(self._fileno, termios.TIOCMSET, value.to_bytes(4, "little"))
        else:
            to_set = modem_bits_mask_of_value(modem_bits, True)
            to_clear = modem_bits_mask_of_value(modem_bits, False)

            if to_set:
                LOGGER.debug("Setting modem bits: 0x%08X", to_set)
                fcntl.ioctl(
                    self._fileno, termios.TIOCMBIS, to_set.to_bytes(4, "little")
                )

            if to_clear:
                LOGGER.debug("Clearing modem bits: 0x%08X", to_clear)
                fcntl.ioctl(
                    self._fileno, termios.TIOCMBIC, to_clear.to_bytes(4, "little")
                )

    def close(self) -> None:
        if getattr(self, "_should_cleanup", False) and self._fileno is not None:
            os.close(self._fileno)
            self._fileno = None

    def fileno(self) -> int:
        return self._fileno

    def readinto(self, b: bytearray) -> int:
        # `io.IOBase` implements `read`, `readline`, using `readinto`
        chunk = os.read(self._fileno, len(b))
        n = len(chunk)
        b[:n] = chunk

        return n

    def write(self, data: bytes):
        os.write(self._fileno, data)

    def __enter__(self) -> Serial:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()
