"""Asynchronous serial port support."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Generic, TypeVar

from .common import BaseSerialTransport, Parity, StopBits, get_serial_classes

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
    protocol_factory: Callable[[], asyncio.Protocol],
    url,
    baudrate,
    parity=Parity.NONE,
    stopbits=StopBits.ONE,
    xonxoff=False,
    rtscts=False,
    exclusive=True,
    **kwargs,
) -> tuple[BaseSerialTransport, asyncio.Protocol]:
    """Create a serial port connection with asyncio."""
    if not exclusive:
        raise ValueError("Only exclusive=True is supported")

    _, transport_cls = await asyncio.get_running_loop().run_in_executor(
        None, get_serial_classes, url
    )

    protocol = protocol_factory()
    transport = transport_cls(loop=loop, protocol=protocol)

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
