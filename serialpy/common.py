"""Serial port communication utilities."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
import dataclasses
from enum import Enum
import io
from typing import Any, Self


class StopBits(Enum):
    """Stop bits configuration."""

    ONE = 1
    ONE_POINT_FIVE = 1.5
    TWO = 2


class Parity(Enum):
    """Parity configuration."""

    NONE = None
    ODD = 1
    EVEN = 2
    MARK = 3
    SPACE = 4


PARITY_NONE = None

STOPBITS_ONE = 1
STOPBITS_TWO = 2


@dataclasses.dataclass(frozen=True)
class ModemBits:
    """Modem control bits."""

    le: bool | None = None
    dtr: bool | None = None
    rts: bool | None = None
    st: bool | None = None
    sr: bool | None = None
    cts: bool | None = None
    car: bool | None = None
    rng: bool | None = None
    dsr: bool | None = None

    @classmethod
    def all_off(cls) -> Self:
        """Create instance with all bits set to off."""
        return cls(
            le=False,
            dtr=False,
            rts=False,
            st=False,
            sr=False,
            cts=False,
            car=False,
            rng=False,
            dsr=False,
        )


class BaseSerial(io.RawIOBase):
    """Base class for serial port communication."""

    def __init__(
        self,
        path,
        baudrate,
        parity: Parity | None = Parity.NONE,
        stopbits: StopBits | int | float = StopBits.ONE,
        xonxoff=False,
        rtscts=False,
        byte_size: int = 8,
        *,
        buffer_character_count: int = 1,
        buffer_burst_timeout: float = 0.0,
    ) -> None:
        """Initialize serial port configuration."""
        super().__init__()
        self._path = path

        if not isinstance(stopbits, StopBits):
            stopbits = StopBits(stopbits)

        if not isinstance(parity, Parity):
            parity = Parity(parity)

        self._baudrate = baudrate
        self._stopbits = stopbits
        self._xonxoff = xonxoff
        self._rtscts = rtscts
        self._parity = parity
        self._byte_size = byte_size

        self._buffer_character_count = buffer_character_count
        self._buffer_burst_timeout = buffer_burst_timeout
        self._auto_close = False

    @abstractmethod
    def open(self) -> None:
        """Open the serial port."""
        raise NotImplementedError

    @abstractmethod
    def configure_port(self) -> None:
        """Configure the serial port settings."""
        raise NotImplementedError

    @abstractmethod
    def get_modem_bits(self) -> ModemBits:
        """Get modem control bits."""
        raise NotImplementedError

    @abstractmethod
    def set_modem_bits(self, modem_bits: ModemBits) -> None:
        """Set modem control bits."""
        raise NotImplementedError

    @property
    def name(self) -> str:
        """Get the serial port name."""
        return self._path

    @property
    def path(self) -> str:
        """Get the serial port path."""
        raise self._path

    @property
    def baudrate(self) -> int:
        """Get the baud rate."""
        raise self._baudrate

    @property
    def parity(self) -> Parity:
        """Get the parity setting."""
        return self._parity

    @property
    def stopbits(self) -> StopBits:
        """Get the stop bits setting."""
        return self._stopbits

    # Deprecated alias
    @property
    def dtr(self) -> bool | None:
        """Get DTR modem bit."""
        return self.get_modem_bits().dtr

    # Deprecated alias
    @dtr.setter
    def dtr(self, value) -> None:
        """Set DTR modem bit."""
        self.set_modem_bits(ModemBits(dtr=bool(value)))

    # Deprecated alias
    @property
    def rts(self) -> bool | None:
        """Get RTS modem bit."""
        return self.get_modem_bits().rts

    # Deprecated alias
    @rts.setter
    def rts(self, value) -> None:
        """Set RTS modem bit."""
        self.set_modem_bits(ModemBits(rts=bool(value)))

    def readexactly(self, n: int) -> bytes:
        """Read exactly n bytes."""
        buffer = bytearray(n)
        view = memoryview(buffer)
        remaining = n

        while remaining > 0:
            remaining -= self.readinto(view)

        return bytes(buffer)

    def __enter__(self) -> Self:
        """Enter context manager."""
        self.open()
        self.configure_port()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager."""
        self.close()

    def __del__(self) -> None:
        """Cleanup on deletion."""
        if self._auto_close:
            self.close()


class BaseSerialTransport(asyncio.Transport):
    """Base class for serial port asyncio transport."""

    transport_name = "serial"

    def __init__(self, loop, protocol) -> None:
        """Initialize serial transport."""
        super().__init__()
        self._loop = loop
        self._protocol = protocol
        self._extra: dict[str, Any] = {}

        self._serial: BaseSerial | None = None

    @abstractmethod
    async def _connect(self, **kwargs) -> None:
        """Connect to serial port."""
        raise NotImplementedError

    async def connect(self, **kwargs) -> None:
        """Connect to serial port."""
        return await self._connect(**kwargs)
