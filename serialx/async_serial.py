"""Asynchronous serial port support."""

from __future__ import annotations

import asyncio
import logging
from typing import Generic, TypeVar
import urllib.parse

from .common import Parity, StopBits
from .platforms import SerialTransport

LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T", bound=asyncio.WriteTransport)


class SerialStreamWriter(asyncio.StreamWriter, Generic[_T]):
    """StreamWriter with properly typed transport."""

    @property
    def transport(self) -> _T:  # type: ignore[override]
        """Return the underlying transport."""
        return super().transport  # type: ignore[return-value]


async def create_serial_connection(
    loop,
    protocol_factory,
    url,
    baudrate,
    parity=Parity.NONE,
    stopbits=StopBits.ONE,
    xonxoff=False,
    rtscts=False,
    exclusive=True,
    *,
    transport_factory=SerialTransport,
    **kwargs,
) -> tuple[SerialTransport, asyncio.Protocol]:
    """Create a serial port connection with asyncio."""
    if not exclusive:
        raise ValueError("Only exclusive=True is supported")

    parsed_path = urllib.parse.urlparse(url)

    protocol: asyncio.Protocol
    if parsed_path.scheme in ("socket", "tcp"):
        transport, protocol = await loop.create_connection(
            protocol_factory, parsed_path.hostname, parsed_path.port
        )
    else:
        protocol = protocol_factory()
        transport = transport_factory(loop=loop, protocol=protocol)

        await transport.connect(
            path=url,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            **kwargs,
        )

    return transport, protocol


async def open_serial_connection(
    *args, **kwargs
) -> tuple[asyncio.StreamReader, SerialStreamWriter[SerialTransport]]:
    """Open a serial port connection using StreamReader and StreamWriter."""
    loop = asyncio.get_running_loop()

    reader = asyncio.StreamReader(loop=loop)
    protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
    transport, _ = await create_serial_connection(
        loop, lambda: protocol, *args, **kwargs
    )
    writer: SerialStreamWriter[SerialTransport] = SerialStreamWriter(
        transport, protocol, reader, loop
    )

    return reader, writer
