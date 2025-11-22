"""Asynchronous serial port support."""

from __future__ import annotations

import asyncio
import logging
import sys
import urllib.parse

from .common import PARITY_NONE, STOPBITS_ONE

if sys.platform == "win32":
    from .serial_win32 import Win32SerialTransport as SerialTransport
else:
    from .serial_posix import PosixSerialTransport as SerialTransport


LOGGER = logging.getLogger(__name__)


async def create_serial_connection(
    loop,
    protocol_factory,
    url,
    baudrate,
    parity=PARITY_NONE,
    stopbits=STOPBITS_ONE,
    xonxoff=False,
    rtscts=False,
    exclusive=True,
    *,
    transport_factory=SerialTransport,
    **kwargs,  # Add **kwargs here
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
            parity=parity,  # Add parity
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            **kwargs,  # Pass **kwargs here
        )

    return transport, protocol


async def open_serial_connection(
    *args, **kwargs
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a serial port connection using StreamReader and StreamWriter."""
    loop = asyncio.get_running_loop()

    reader = asyncio.StreamReader(loop=loop)
    protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
    transport, _ = await create_serial_connection(
        loop, lambda: protocol, *args, **kwargs
    )
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)

    return reader, writer
