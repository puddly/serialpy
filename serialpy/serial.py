from __future__ import annotations

import os
import io
import array
import fcntl
import typing
import logging
import termios
import dataclasses

LOGGER = logging.getLogger(__name__)

PARITY_NONE = None

STOPBITS_ONE = 1
STOPBITS_TWO = 2

ASYNC_LOW_LATENCY = (1 << 13)
CMSPAR = 0o10000000000

if hasattr(termios, "CRTSCTS"):
    CRTSCTS = termios.CRTSCTS
elif hasattr(termios, "CNEW_RTSCTS"):
    CRTSCTS = termios.CNEW_RTSCTS
else:
    raise RuntimeError("termios.CRTSCTS missing")


@dataclasses.dataclass(frozen=True)
class ModemBits:
    le: bool | None = None
    dtr: bool | None = None
    rts: bool | None = None
    st: bool | None = None
    sr: bool | None = None
    cts: bool | None = None
    car: bool | None = None
    rng: bool | None = None
    dsr: bool | None = None

    _mapping = {
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

    @classmethod
    def all_off(cls) -> ModemBits:
        return cls.from_int(0x00000000)

    @classmethod
    def from_int(cls, n: int) -> ModemBits:
        return cls(**{name: bool(n & bit) for name, bit in cls._mapping.items()})

    @property
    def all_bits_set(self) -> bool:
        return all(getattr(self, name) is not None for name in self._mapping.keys())

    def mask_of_value(self, mask: typing.Literal[True, False, None]) -> int:
        result = 0x00000000

        for name, bit in self._mapping.items():
            value = getattr(self, name)

            if value == mask:
                result |= bit

        return result

    def as_int(self) -> int:
        if not self.all_bits_set:
            raise ValueError(f"Cannot convert to int when bit is not set: {self!r}")

        result = 0x00000000

        for name, bit in self._mapping.items():
            result |= bit if getattr(self, name) else 0x00000000

        return result


class Serial(io.RawIOBase):
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
            cflag &= ~CRTSCTS
        else:
            cflag |= CRTSCTS

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

        return ModemBits.from_int(int.from_bytes(buffer, "little"))

    def set_low_latency(self, low_latency: bool) -> None:
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
        if modem_bits is None:
            modem_bits = ModemBits(**kwargs)
        elif kwargs:
            raise ValueError("`modem_bits` and keyword arguments are mutually exclusive")

        if modem_bits.all_bits_set:
            value = modem_bits.as_int()
            LOGGER.debug("Setting all modem bits: 0x%08X", value)
            fcntl.ioctl(self._fileno, termios.TIOCMSET, value.to_bytes(4, "little"))
        else:
            to_set = modem_bits.mask_of_value(True)
            to_clear = modem_bits.mask_of_value(False)

            if to_set:
                LOGGER.debug("Setting modem bits: 0x%08X", to_set)
                fcntl.ioctl(self._fileno, termios.TIOCMBIS, to_set.to_bytes(4, "little"))

            if to_clear:
                LOGGER.debug("Clearing modem bits: 0x%08X", to_clear)
                fcntl.ioctl(self._fileno, termios.TIOCMBIC, to_clear.to_bytes(4, "little"))

    @property
    def dtr(self) -> bool:
        return self.get_modem_bits().dtr

    @dtr.setter
    def dtr(self, value) -> None:
        self.set_modem_bits(ModemBits(dtr=bool(value)))

    @property
    def rts(self) -> bool:
        return self.get_modem_bits().rts

    @rts.setter
    def rts(self, value) -> None:
        self.set_modem_bits(ModemBits(rts=bool(value)))

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

    def readexactly(self, n: int) -> bytes:
        buffer = bytearray(n)
        view = memoryview(buffer)
        remaining = n

        while remaining > 0:
            remaining -= self.readinto(view)

        return bytes(buffer)

    def write(self, data: bytes):
        os.write(self._fileno, data)

    def __enter__(self) -> Serial:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()