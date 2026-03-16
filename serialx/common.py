"""Serial port communication utilities."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from asyncio import IncompleteReadError
from collections.abc import Callable, Iterator
from contextlib import contextmanager
import dataclasses
from enum import Enum
import io
import os
from pathlib import Path
import time
from typing import Any
import urllib.parse
import warnings

from typing_extensions import Buffer, Self


class SerialException(Exception):
    """Base serial exception."""


class UnsupportedSetting(SerialException):
    """Raised when an unsupported serial port setting is used."""


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

    def __repr__(self) -> str:
        """Return string representation of modem pins."""

        bits = []

        for bit in ("le", "dtr", "rts", "st", "sr", "cts", "car", "rng", "dsr"):
            value = getattr(self, bit)

            if value is PinState.UNDEFINED:
                continue
            elif value is PinState.HIGH:
                bits.append(bit)
            else:
                bits.append(f"!{bit}")

        return f"{self.__class__.__name__}[{' '.join(bits)}]"


@contextmanager
def measure_time() -> Iterator[Callable[[], float]]:
    """Measure elapsed time in a context."""
    start = time.monotonic()
    end = None

    def get_result() -> float:
        if end is None:
            raise RuntimeError("Context has not exited yet")

        return end - start

    try:
        yield get_result
    finally:
        end = time.monotonic()


class BaseSerial(io.RawIOBase):
    """Base class for serial port communication."""

    def __init__(
        self,
        path: str | Path | None = None,
        baudrate: int = 9600,
        *,
        parity: Parity | None = Parity.NONE,
        stopbits: StopBits | int | float = StopBits.ONE,
        xonxoff: bool = False,
        rtscts: bool = False,
        dsrdtr: bool = False,
        byte_size: int = 8,
        read_timeout: float | None = None,
        write_timeout: float | None = None,
        rtsdtr_on_open: PinState = PinState.HIGH,
        rtsdtr_on_close: PinState = PinState.LOW,
        exclusive: bool = True,
        # pyserial compatibility kwargs
        port: str | None = None,
        timeout: float | None = None,
        bytesize: int | None = None,
        do_not_open: bool | None = None,
        writeTimeout: float | None = None,
        inter_byte_timeout: int | None = None,
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
        self._dsrdtr = dsrdtr
        self._parity = parity
        self._byte_size = byte_size
        self._exclusive = exclusive
        self._read_timeout = read_timeout
        self._write_timeout = write_timeout

        self._rtsdtr_on_open = rtsdtr_on_open
        self._rtsdtr_on_close = rtsdtr_on_close

        self._auto_close = False

        # Compatibility kwargs
        if port is not None:
            self._path = port

        if timeout is not None:
            self._read_timeout = timeout

        if bytesize is not None:
            self._byte_size = bytesize

        if writeTimeout is not None:
            self._write_timeout = writeTimeout

        if do_not_open is False:
            raise RuntimeError("do_not_open=False is not supported")

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

    def readinto(self, b: Buffer, *, timeout: float | None = None) -> int:
        """Read bytes from serial port into buffer."""
        timeout = self._read_timeout if timeout is None else timeout
        return self._readinto(b, timeout=timeout)

    @abstractmethod
    def _readinto(self, b: Buffer, *, timeout: float | None) -> int:
        """Read bytes from serial port into buffer, internal."""
        raise NotImplementedError

    def write(self, data: Buffer, *, timeout: float | None = None) -> int:
        """Write bytes to serial port."""
        timeout = self._write_timeout if timeout is None else timeout
        return self._write(data, timeout=timeout)

    @abstractmethod
    def _write(self, data: Buffer, *, timeout: float | None) -> int:
        """Write bytes to serial port, internal."""
        raise NotImplementedError

    @abstractmethod
    def flush(self) -> None:
        """Flush write buffers."""
        raise NotImplementedError

    @property
    def path(self) -> str | Path | None:
        """Get the serial port path."""
        return self._path

    @property
    def baudrate(self) -> int:
        """Get the baud rate."""
        return self._baudrate

    @baudrate.setter
    def baudrate(self, value: int) -> None:
        """Set baud rate (deprecated)."""
        self._baudrate = value
        self._configure_port()

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

    def readexactly(self, n: int, *, timeout: float | None = None) -> bytes:
        """Read exactly n bytes."""
        buffer = bytearray(n)
        view = memoryview(buffer)
        remaining = n
        timeout = self.read_timeout if timeout is None else timeout

        while remaining > 0:
            with measure_time() as get_elapsed:
                read = self.readinto(view, timeout=timeout)

            if timeout is not None:
                timeout -= get_elapsed()

            view = view[read:]
            remaining -= read

            if read == 0:
                # `IncompleteReadError` is a subclass of `EOFError`
                raise IncompleteReadError(
                    expected=n, partial=bytes(buffer[: n - remaining])
                )

        return bytes(buffer)

    def read_until(
        self,
        expected: bytes = b"\n",
        size: int | None = None,
        *,
        timeout: float | None = None,
    ) -> bytes:
        """Read until the expected sequence is found."""
        buffer = bytearray()
        expected_len = len(expected)
        timeout = self.read_timeout if timeout is None else timeout

        while True:
            with measure_time() as get_elapsed:
                byte = self.readexactly(1, timeout=timeout)

            if timeout is not None:
                timeout -= get_elapsed()

            if not byte:
                break

            buffer += byte

            if buffer[-expected_len:] == expected:
                break

            if size is not None and len(buffer) >= size:
                break

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

    @abstractmethod
    def num_unread_bytes(self) -> int:
        """Return the number of bytes waiting to be read."""
        raise NotImplementedError

    @abstractmethod
    def num_unwritten_bytes(self) -> int:
        """Return the number of bytes waiting to be written."""
        raise NotImplementedError

    @abstractmethod
    def reset_read_buffer(self) -> None:
        """Reset the read buffer."""
        raise NotImplementedError

    @abstractmethod
    def reset_write_buffer(self) -> None:
        """Reset the write buffer."""
        raise NotImplementedError

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return whether the serial port is open."""
        raise NotImplementedError

    # Deprecated aliases
    @property
    def port(self) -> str | None:
        """Deprecated: use `path` instead."""
        return str(self.path) if self.path is not None else None

    @property
    def timeout(self) -> float | None:
        """Deprecated: use `read_timeout` instead."""
        return self.read_timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        self._read_timeout = value

    @property
    def bytesize(self) -> int:
        """Deprecated: use `byte_size` instead."""
        return self.byte_size

    @property
    def writeTimeout(self) -> float | None:
        """Deprecated: use `write_timeout` instead."""
        return self.write_timeout

    def reset_input_buffer(self) -> None:
        """Reset the read buffer (deprecated: use `reset_read_buffer`)."""
        self.reset_read_buffer()

    def reset_output_buffer(self) -> None:
        """Reset the write buffer (deprecated: use `reset_write_buffer`)."""
        self.reset_write_buffer()

    def flushInput(self) -> None:
        """Reset the read buffer (deprecated: use `reset_read_buffer`)."""
        self.reset_read_buffer()

    def flushOutput(self) -> None:
        """Reset the write buffer (deprecated: use `reset_write_buffer`)."""
        self.reset_write_buffer()

    @property
    def in_waiting(self) -> int:
        """Deprecated: use `num_unread_bytes` instead."""
        return self.num_unread_bytes()

    @property
    def out_waiting(self) -> int:
        """Deprecated: use `num_unwritten_bytes` instead."""
        return self.num_unwritten_bytes()

    @property
    def inWaiting(self) -> int:
        """Deprecated: use `num_unread_bytes` instead."""
        return self.in_waiting

    def isOpen(self) -> bool:
        """Return whether the serial port is open (deprecated: use `is_open`)."""
        return self.is_open

    @property
    def dtr(self) -> bool | None:
        """Get DTR modem bit."""
        return self.get_modem_pins().dtr.to_bool()

    @dtr.setter
    def dtr(self, value: bool) -> None:
        """Set DTR modem bit."""
        self.set_modem_pins(dtr=bool(value))

    @property
    def rts(self) -> bool | None:
        """Get RTS modem bit."""
        return self.get_modem_pins().rts.to_bool()

    @rts.setter
    def rts(self, value: bool) -> None:
        """Set RTS modem bit."""
        self.set_modem_pins(rts=bool(value))

    @property
    def cts(self) -> bool | None:
        """Get CTS modem bit."""
        return self.get_modem_pins().cts.to_bool()

    @cts.setter
    def cts(self, value: bool) -> None:
        """Set CTS modem bit."""
        self.set_modem_pins(cts=bool(value))


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

    async def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        """Set modem control bits, internal."""
        await self._loop.run_in_executor(
            None,
            lambda: (
                self._serial._set_modem_pins(modem_pins)
                if self._serial is not None
                else None
            ),
        )

    async def set_modem_pins(
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

        return await self._set_modem_pins(modem_pins)

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

    def __getitem__(self, key: int | slice) -> str | None:
        """Compatibility shim for `serial.tools.list_ports_common.ListPortInfo`."""
        warnings.warn(
            "Slicing `SerialPortInfo` is deprecated, use attributes instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return (str(self.device), self.description, "")[key]

    @property
    def description(self) -> str | None:
        """Deprecated alias for `product`."""
        warnings.warn(
            "`description` is deprecated, use `product` instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.product
