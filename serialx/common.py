"""Serial port communication utilities."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from asyncio import IncompleteReadError
import dataclasses
from enum import Enum
import io
import os
from pathlib import Path
from typing import Any
import urllib.parse
import warnings

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


class PinState(Enum):
    """Pin state."""

    UNDEFINED = None
    LOW = 0
    HIGH = 1

    @classmethod
    def convert(cls, value: PinState | bool | None) -> PinState:
        """Create PinState from boolean."""
        if isinstance(value, cls):
            return value

        if value is None:
            return cls.UNDEFINED

        return cls.HIGH if value else cls.LOW

    def to_bool(self) -> bool | None:
        """Convert PinState to boolean."""
        if self is PinState.UNDEFINED:
            return None

        return self is PinState.HIGH


@dataclasses.dataclass(frozen=True)
class ModemPins:
    """Modem control bits."""

    le: PinState = PinState.UNDEFINED
    dtr: PinState = PinState.UNDEFINED
    rts: PinState = PinState.UNDEFINED
    st: PinState = PinState.UNDEFINED
    sr: PinState = PinState.UNDEFINED
    cts: PinState = PinState.UNDEFINED
    car: PinState = PinState.UNDEFINED
    rng: PinState = PinState.UNDEFINED
    dsr: PinState = PinState.UNDEFINED

    @classmethod
    def all_off(cls) -> Self:
        """Create instance with all bits set to off."""
        return cls(
            le=PinState.LOW,
            dtr=PinState.LOW,
            rts=PinState.LOW,
            st=PinState.LOW,
            sr=PinState.LOW,
            cts=PinState.LOW,
            car=PinState.LOW,
            rng=PinState.LOW,
            dsr=PinState.LOW,
        )

    def __repr__(self) -> str:
        """Return string representation of modem pins."""

        bits = []

        for bit in (
            "le",
            "dtr",
            "rts",
            "st",
            "sr",
            "cts",
            "car",
            "rng",
            "dsr",
        ):
            value = getattr(self, bit)

            if value is PinState.UNDEFINED:
                continue
            elif value is PinState.HIGH:
                bits.append(bit)
            else:
                bits.append(f"!{bit}")

        return f"{self.__class__.__name__}[{' '.join(bits)}]"


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
        read_timeout: float | None = None,
        write_timeout: float | None = None,
        rtsdtr_on_open: PinState = PinState.HIGH,
        rtsdtr_on_close: PinState = PinState.LOW,
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
        self._read_timeout = read_timeout
        self._write_timeout = write_timeout

        self._rtsdtr_on_open = rtsdtr_on_open
        self._rtsdtr_on_close = rtsdtr_on_close

        self._auto_close = False

    @classmethod
    def from_url(cls, url: str, *args: Any, **kwargs: Any) -> BaseSerial:
        """Create the appropriate serial port subclass for the given URL."""
        serial_cls, _ = get_serial_classes(url)
        return serial_cls(url, *args, **kwargs)

    def open(self) -> None:
        """Open the serial port."""
        self._open()

        try:
            self._configure_port()
        except BaseException:
            self.close()
            raise

    def configure_port(self) -> None:
        """Configure the serial port settings."""
        self._configure_port()

    @abstractmethod
    def _open(self) -> None:
        """Open the serial port (platform-specific)."""
        raise NotImplementedError

    @abstractmethod
    def _configure_port(self) -> None:
        """Configure the serial port settings (platform-specific)."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the serial port."""
        self._close()

    @abstractmethod
    def _close(self) -> None:
        """Close the serial port, internal."""
        raise NotImplementedError

    @property
    def read_timeout(self) -> float | None:
        """Get the read timeout in seconds."""
        return self._read_timeout

    @property
    def write_timeout(self) -> float | None:
        """Get the write timeout in seconds."""
        return self._write_timeout

    def get_modem_pins(self) -> ModemPins:
        """Get modem control bits."""
        return self._get_modem_pins()

    def set_modem_pins(
        self,
        modem_pins: ModemPins | None = None,
        *,
        le: PinState | bool | None = PinState.UNDEFINED,
        dtr: PinState | bool | None = PinState.UNDEFINED,
        rts: PinState | bool | None = PinState.UNDEFINED,
        st: PinState | bool | None = PinState.UNDEFINED,
        sr: PinState | bool | None = PinState.UNDEFINED,
        cts: PinState | bool | None = PinState.UNDEFINED,
        car: PinState | bool | None = PinState.UNDEFINED,
        rng: PinState | bool | None = PinState.UNDEFINED,
        dsr: PinState | bool | None = PinState.UNDEFINED,
    ) -> None:
        """Set modem control bits."""
        if modem_pins is None:
            modem_pins = ModemPins(
                le=PinState.convert(le),
                dtr=PinState.convert(dtr),
                rts=PinState.convert(rts),
                st=PinState.convert(st),
                sr=PinState.convert(sr),
                cts=PinState.convert(cts),
                car=PinState.convert(car),
                rng=PinState.convert(rng),
                dsr=PinState.convert(dsr),
            )

        return self._set_modem_pins(modem_pins)

    @abstractmethod
    def _get_modem_pins(self) -> ModemPins:
        """Get modem control bits, internal."""
        raise NotImplementedError

    @abstractmethod
    def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        """Set modem control bits, internal."""
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
    def rtsdtr_on_open(self) -> PinState:
        """Get the RTS/DTR pin state (on open) setting."""
        return self._rtsdtr_on_open

    @property
    def rtsdtr_on_close(self) -> PinState:
        """Get the RTS/DTR pin state (on close) setting."""
        return self._rtsdtr_on_close

    @property
    def exclusive(self) -> bool:
        """Get the exclusive setting."""
        return self._exclusive

    # Deprecated alias
    @property
    def dtr(self) -> bool | None:
        """Get DTR modem bit."""
        return self.get_modem_pins().dtr.to_bool()

    # Deprecated alias
    @dtr.setter
    def dtr(self, value: bool) -> None:
        """Set DTR modem bit."""
        self.set_modem_pins(dtr=bool(value))

    # Deprecated alias
    @property
    def rts(self) -> bool | None:
        """Get RTS modem bit."""
        return self.get_modem_pins().rts.to_bool()

    # Deprecated alias
    @rts.setter
    def rts(self, value: bool) -> None:
        """Set RTS modem bit."""
        self.set_modem_pins(rts=bool(value))

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
                # `IncompleteReadError` is a subclass of `EOFError`
                raise IncompleteReadError(
                    expected=n, partial=bytes(buffer[: n - remaining])
                )

        return bytes(buffer)

    def __enter__(self) -> Self:
        """Enter context manager."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager."""
        self.close()

    def __del__(self) -> None:
        """Cleanup on deletion."""
        if getattr(self, "_auto_close", False):
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
        self._closed_waiter: asyncio.Future[None] = loop.create_future()

    def is_closing(self) -> bool:
        """Return whether the transport is closing."""
        return self._closing

    def _resolve_closed_waiter(self) -> None:
        if not self._closed_waiter.done():
            self._closed_waiter.set_result(None)

    def _call_protocol_connection_lost(self, exc: Exception | None) -> None:
        try:
            self._protocol.connection_lost(exc)
        except (SystemExit, KeyboardInterrupt):
            raise
        except BaseException as protocol_exc:
            self._loop.call_exception_handler(
                {
                    "message": "protocol.connection_lost() failed",
                    "exception": protocol_exc,
                    "transport": self,
                    "protocol": self._protocol,
                }
            )
        finally:
            self._resolve_closed_waiter()

    def get_protocol(self) -> asyncio.Protocol:
        """Get the protocol used by this transport."""
        return self._protocol

    def set_protocol(self, protocol: asyncio.Protocol) -> None:  # type: ignore[override]
        """Set the protocol to use with this transport."""
        self._protocol = protocol

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

    async def connect(
        self,
        *,
        path: str,
        baudrate: int,
        parity: Parity = Parity.NONE,
        stopbits: StopBits = StopBits.ONE,
        xonxoff: bool = False,
        rtscts: bool = False,
        byte_size: int = 8,
        **kwargs,
    ) -> None:
        """Connect to serial port."""
        return await self._connect(
            path=path,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            byte_size=byte_size,
            **kwargs,
        )

    async def get_modem_pins(self) -> ModemPins:
        """Get modem control bits."""
        assert self._serial is not None
        return await self._loop.run_in_executor(None, self._serial.get_modem_pins)

    async def set_modem_pins(
        self,
        modem_pins: ModemPins | None = None,
        *,
        le: bool | None = None,
        dtr: bool | None = None,
        rts: bool | None = None,
        st: bool | None = None,
        sr: bool | None = None,
        cts: bool | None = None,
        car: bool | None = None,
        rng: bool | None = None,
        dsr: bool | None = None,
    ) -> None:
        """Set modem control bits."""
        await self._loop.run_in_executor(
            None,
            lambda: (
                None
                if self._serial is None
                else self._serial.set_modem_pins(
                    modem_pins,
                    le=le,
                    dtr=dtr,
                    rts=rts,
                    st=st,
                    sr=sr,
                    cts=cts,
                    car=car,
                    rng=rng,
                    dsr=dsr,
                )
            ),
        )

    async def flush(self) -> None:
        """Flush write buffers, waiting until all data is written."""
        raise NotImplementedError

    async def wait_closed(self) -> None:
        """Wait until transport is fully closed."""
        await self._closed_waiter


def get_serial_classes(
    url: str,
) -> tuple[type[BaseSerial], type[BaseSerialTransport]]:
    """Get the appropriate serial and transport classes based on the URL scheme."""
    parsed_path = urllib.parse.urlparse(url)

    if parsed_path.scheme in ("socket", "tcp"):
        from .platforms.serial_socket import (  # noqa: PLC0415
            SocketSerial,
            SocketSerialTransport,
        )

        return SocketSerial, SocketSerialTransport
    elif parsed_path.scheme == "esphome":
        try:
            from .platforms.serial_esphome import (  # noqa: PLC0415
                ESPHomeSerial,
                ESPHomeSerialTransport,
            )
        except ImportError as exc:
            raise RuntimeError(
                "ESPHome serial transport requires extra dependencies."
                " Install with `pip install serialx[esphome]`"
            ) from exc

        return ESPHomeSerial, ESPHomeSerialTransport
    else:
        from .platforms import Serial, SerialTransport  # noqa: PLC0415

        return Serial, SerialTransport


@dataclasses.dataclass
class SerialPortInfo:
    """A serial port."""

    device: os.PathLike
    resolved_device: os.PathLike

    vid: int | None
    pid: int | None
    serial_number: str | None
    manufacturer: str | None
    product: str | None
    bcd_device: int | None
    interface_description: str | None
    interface_num: int | None

    @property
    def description(self) -> str | None:
        """Deprecated alias for `product`."""
        warnings.warn(
            "`description` is deprecated, use `product` instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.product
