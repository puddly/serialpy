"""Asynchronous serial port support."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any, Generic, TypeVar, cast

from typing_extensions import Unpack

from .common import (
    BaseSerialTransport,
    ConnectKwargs,
    ModemPins,
    Parity,
    SerialException,
    StopBits,
    get_uri_handler,
)

LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T", bound=asyncio.WriteTransport)


class SerialStreamWriter(asyncio.StreamWriter, Generic[_T]):
    """StreamWriter with properly typed transport."""

    @property
    def transport(self) -> _T:
        """Return the underlying transport."""
        return cast(_T, super().transport)


class AsyncSerial(asyncio.StreamReader, asyncio.StreamWriter):
    """Async serial port object with a sync-like API."""

    def __init__(
        self,
        url: str | None,
        *,
        transport_cls: type[BaseSerialTransport] | None = None,
        **kwargs: Unpack[ConnectKwargs],
    ) -> None:
        """Initialize an unopened serial port. Use `open()` or `async with` to connect."""
        # Defer parent initializers until open() — they need a running loop and
        # a transport, neither of which exists at construction time.
        self._url = url
        self._connect_kwargs: ConnectKwargs = kwargs
        self._transport_cls = transport_cls
        self._opened = False

    async def open(self) -> None:
        """Open the serial port connection."""
        if self._opened:
            raise SerialException("AsyncSerial has been opened")
        self._opened = True

        loop = asyncio.get_running_loop()
        # Initialize the StreamReader half. _transport starts as None; the
        # protocol's connection_made will populate it via self.set_transport().
        asyncio.StreamReader.__init__(self, loop=loop)

        protocol = asyncio.StreamReaderProtocol(self, loop=loop)
        transport, _ = await create_serial_connection(
            loop,
            lambda: protocol,
            self._url,
            transport_cls=self._transport_cls,
            **self._connect_kwargs,
        )
        # _transport is already set by protocol.connection_made →
        # self.set_transport(transport). StreamWriter.__init__ assigns the same
        # object to _transport again (idempotent) and wires _protocol, _reader,
        # _loop, and _complete_fut.
        asyncio.StreamWriter.__init__(
            self,
            transport=transport,
            protocol=protocol,
            reader=self,
            loop=loop,
        )

    async def __aenter__(self) -> AsyncSerial:
        """Open the connection and return self."""
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close the connection and wait until it's fully closed."""
        self.close()
        await self.wait_closed()

    def __repr__(self) -> str:
        """Return a debug representation that does not recurse into self._reader=self."""
        transport = getattr(self, "_transport", None)
        return f"<AsyncSerial url={self._url!r} transport={transport!r}>"

    def __del__(self) -> None:
        """Skip StreamWriter.__del__ if the instance was never opened."""
        # StreamWriter.__del__ unconditionally dereferences self._transport,
        # which doesn't exist if the instance was never opened (or open()
        # raised before assigning it).
        if getattr(self, "_transport", None) is None:
            return
        asyncio.StreamWriter.__del__(self)  # type: ignore[attr-defined]

    @property
    def transport(self) -> BaseSerialTransport:
        """Return the underlying serial transport."""
        return cast(BaseSerialTransport, self._transport)  # type: ignore[attr-defined]

    async def flush(self) -> None:
        """Drain app-level buffer, then wait for the OS-level buffer to flush."""
        await self.drain()
        await self.transport.flush()

    async def get_modem_pins(self) -> ModemPins:
        """Get modem control pins."""
        return await self.transport.get_modem_pins()

    async def set_modem_pins(self, *args: Any, **kwargs: Any) -> None:
        """Set modem control pins."""
        await self.transport.set_modem_pins(*args, **kwargs)

    @property
    def baudrate(self) -> int:
        """Get the baud rate."""
        return self.transport.baudrate

    @property
    def parity(self) -> Parity:
        """Get the parity."""
        return self.transport.parity

    @property
    def stopbits(self) -> StopBits:
        """Get the number of stop bits."""
        return self.transport.stopbits

    @property
    def byte_size(self) -> int:
        """Get the byte size."""
        return self.transport.byte_size

    @property
    def exclusive(self) -> bool:
        """Get the exclusive setting."""
        return self.transport.exclusive


async def create_serial_connection(
    loop: asyncio.AbstractEventLoop,
    protocol_factory: Callable[[], asyncio.Protocol],
    url: str | None,
    baudrate: int,
    parity: Parity = Parity.NONE,
    stopbits: StopBits = StopBits.ONE,
    xonxoff: bool = False,
    rtscts: bool = False,
    exclusive: bool = True,
    *,
    transport_cls: type[BaseSerialTransport] | None = None,
    **kwargs: Any,
) -> tuple[BaseSerialTransport, asyncio.Protocol]:
    """Create a serial port connection with asyncio."""
    if transport_cls is None:
        if url is None:
            raise ValueError("One of `url` or `transport_cls` must be provided.")

        handler = await asyncio.get_running_loop().run_in_executor(
            None, get_uri_handler, url
        )
        transport_cls = handler.async_transport_cls

    assert transport_cls is not None
    protocol = protocol_factory()
    transport = transport_cls(loop=loop, protocol=protocol)

    await transport.connect(
        path=url,
        baudrate=baudrate,
        parity=parity,
        stopbits=stopbits,
        xonxoff=xonxoff,
        rtscts=rtscts,
        exclusive=exclusive,
        **kwargs,
    )

    return transport, protocol


async def open_serial_connection(
    *args: Any, **kwargs: Any
) -> tuple[asyncio.StreamReader, SerialStreamWriter[BaseSerialTransport]]:
    """Open a serial port connection using StreamReader and StreamWriter."""
    loop = asyncio.get_running_loop()

    reader = asyncio.StreamReader(loop=loop)
    protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
    transport, _ = await create_serial_connection(
        loop, lambda: protocol, *args, **kwargs
    )
    writer: SerialStreamWriter[BaseSerialTransport] = SerialStreamWriter(
        transport, protocol, reader, loop
    )

    return reader, writer


def async_serial_for_url(
    url: str | None,
    *,
    transport_cls: type[BaseSerialTransport] | None = None,
    **kwargs: Unpack[ConnectKwargs],
) -> AsyncSerial:
    """Build an unopened AsyncSerial. Use `async with` or `await serial.open()`."""
    return AsyncSerial(url, transport_cls=transport_cls, **kwargs)
