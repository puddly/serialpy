"""Asynchronous serial port support."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Generic, TypeVar
import urllib.parse

from .common import BaseSerialTransport, Parity, StopBits
from .platforms import SerialTransport
from .platforms.serial_socket import SocketSerialTransport

ESPHomeSerialTransport: type[BaseSerialTransport] | None = None

try:
    from .platforms.serial_esphome import ESPHomeSerialTransport
except ImportError:
    ESPHomeSerialTransport = None


LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T", bound=asyncio.WriteTransport)


class SerialStreamWriter(asyncio.StreamWriter, Generic[_T]):
    """StreamWriter with properly typed transport."""

    @property
    def transport(self) -> _T:  # type: ignore[override]
        """Return the underlying transport."""
        return super().transport  # type: ignore[return-value]


def get_protocol_handler(url: str) -> type[BaseSerialTransport]:
    """Get the appropriate protocol handler based on the URL scheme."""
    parsed_path = urllib.parse.urlparse(url)

    if parsed_path.scheme in ("socket", "tcp"):
        return SocketSerialTransport
    elif parsed_path.scheme == "esphome":
        if ESPHomeSerialTransport is None:
            raise RuntimeError(
                "aioesphomeapi is required for esphome:// URLs. "
                "Install it with: pip install serialx[esphome]"
            )

        return ESPHomeSerialTransport
    else:
        # We fall back to the platform-specific transport
        return SerialTransport


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

    transport_cls = get_protocol_handler(url)

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
