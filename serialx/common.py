"""Serial port communication utilities."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
import dataclasses
from enum import Enum
import io
from pathlib import Path
from typing import Any

from typing_extensions import Self


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
        path: str | Path,
        baudrate: int,
        parity: Parity | None = Parity.NONE,
        stopbits: StopBits | int | float = StopBits.ONE,
        xonxoff: bool = False,
        rtscts: bool = False,
        byte_size: int = 8,
        *,
        buffer_character_count: int = 1,
        buffer_burst_timeout: float = 0.01,
        exclusive: bool = True,
    ) -> None:
        """Initialize serial port configuration."""
        super().__init__()

        if not isinstance(stopbits, StopBits):
            stopbits = StopBits(stopbits)

        if not isinstance(parity, Parity):
            parity = Parity(parity)

        self._path = path
        self._baudrate = baudrate
        self._stopbits = stopbits
        self._xonxoff = xonxoff
        self._rtscts = rtscts
        self._parity = parity
        self._byte_size = byte_size
        self._exclusive = exclusive

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

    @abstractmethod
    def flush(self) -> None:
        """Flush write buffers."""
        raise NotImplementedError

    @property
    def path(self) -> str | Path:
        """Get the serial port path."""
        return self._path

    @property
    def baudrate(self) -> int:
        """Get the baud rate."""
        return self._baudrate

    @property
    def parity(self) -> Parity:
        """Get the parity."""
        return self._parity

    @property
    def byte_size(self) -> int:
        """Get the byte size."""
        return self._byte_size

    @property
    def stopbits(self) -> StopBits:
        """Get the number of stop bits."""
        return self._stopbits

    @property
    def exclusive(self) -> bool:
        """Get the exclusive setting."""
        return self._exclusive

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
            read = self.readinto(view)
            view = view[read:]
            remaining -= read

            if read == 0:
                raise EOFError(
                    f"Read only {n - remaining} bytes, expected {n} bytes: {buffer!r}"
                )

        return bytes(buffer)

    def __enter__(self) -> Self:
        """Enter context manager."""
        self.open()

        try:
            self.configure_port()
        except BaseException:
            self.close()
            raise

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

    def __init__(
        self, loop: asyncio.AbstractEventLoop, protocol: asyncio.Protocol
    ) -> None:
        """Initialize serial transport."""
        super().__init__()
        self._loop = loop
        self._protocol = protocol
        self._extra: dict[str, Any] = {}

        self._serial: BaseSerial | None = None
        self._closing: bool = False

    def is_closing(self) -> bool:
        """Return whether the transport is closing."""
        return self._closing

    @property
    def serial(self) -> BaseSerial:
        """Get the serial port instance."""
        assert self._serial is not None
        return self._serial

    @property
    def baudrate(self) -> int:
        """Get the baud rate."""
        assert self._serial is not None
        return self._serial.baudrate

    @property
    def parity(self) -> Parity:
        """Get the parity."""
        assert self._serial is not None
        return self._serial.parity

    @property
    def stopbits(self) -> StopBits:
        """Get the number of stop bits."""
        assert self._serial is not None
        return self._serial.stopbits

    @property
    def byte_size(self) -> int:
        """Get the byte size."""
        assert self._serial is not None
        return self._serial.byte_size

    @property
    def exclusive(self) -> bool:
        """Get the exclusive setting."""
        assert self._serial is not None
        return self._serial.exclusive

    @abstractmethod
    async def _connect(self, **kwargs) -> None:
        """Connect to serial port."""
        raise NotImplementedError

    async def connect(self, **kwargs) -> None:
        """Connect to serial port."""
        return await self._connect(**kwargs)

    async def get_modem_bits(self) -> ModemBits:
        """Get modem control bits."""
        assert self._serial is not None
        return await self._loop.run_in_executor(None, self._serial.get_modem_bits)

    async def set_modem_bits(self, modem_bits: ModemBits) -> None:
        """Set modem control bits."""
        assert self._serial is not None
        await self._loop.run_in_executor(None, self._serial.set_modem_bits, modem_bits)

    async def flush(self) -> None:
        """Flush write buffers, waiting until all data is written."""
        raise NotImplementedError
