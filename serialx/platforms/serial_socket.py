"""Socket serial transport."""

from __future__ import annotations

import asyncio
from collections.abc import Buffer
import logging
from pathlib import Path
import socket
import urllib.parse

from serialx.common import BaseSerial, BaseSerialTransport, ModemPins, Parity, StopBits

LOGGER = logging.getLogger(__name__)


class SocketSerial(BaseSerial):
    """Synchronous serial interface over a TCP socket."""

    def __init__(
        self,
        path: str | Path,
        baudrate: int,
        parity: Parity = Parity.NONE,
        stopbits: StopBits = StopBits.ONE,
        xonxoff: bool = False,
        rtscts: bool = False,
        byte_size: int = 8,
        **kwargs,
    ) -> None:
        """Initialize socket serial port."""
        super().__init__(
            path=path,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            byte_size=byte_size,
            **kwargs,
        )

        parsed = urllib.parse.urlparse(str(path))
        self._host = parsed.hostname
        self._port = parsed.port

        self._socket: socket.socket | None = None

        self._async_reader: asyncio.StreamReader | None = None
        self._async_writer: asyncio.StreamWriter | None = None

    def open(self) -> None:
        """Open the socket connection."""
        assert self._host is not None
        assert self._port is not None
        self._socket = socket.create_connection((self._host, self._port))

    async def _async_open(self) -> None:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        self._async_reader = reader
        self._async_writer = writer

    def configure_port(self) -> None:
        """Configure the serial port settings (no-op for sockets)."""

    def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        pass

    def _get_modem_pins(self) -> ModemPins:
        return ModemPins()

    def flush(self) -> None:
        """Flush write buffers (no-op for sockets)."""

    def write(self, b: Buffer) -> int:
        """Write bytes to socket."""
        data = bytes(b)

        if self._socket is not None:
            self._socket.sendall(data)
        elif self._async_writer is not None:
            self._async_writer.write(data)
        else:
            raise RuntimeError("Socket is not open")

        return len(data)

    def readinto(self, b: Buffer) -> int:
        """Read bytes from socket into buffer."""
        if self._socket is None:
            raise RuntimeError("Socket is not open")

        m = memoryview(b).cast("B")
        return self._socket.recv_into(m)

    def close(self) -> None:
        """Close the socket."""
        if self._socket is not None:
            self._socket.close()
            self._socket = None

        if self._async_writer is not None:
            self._async_writer.close()
            self._async_writer = None
            self._async_reader = None


class SocketSerialTransport(BaseSerialTransport):
    """Serial transport over a TCP socket."""

    transport_name = "socket"
    _serial: SocketSerial

    def __init__(
        self, loop: asyncio.AbstractEventLoop, protocol: asyncio.Protocol
    ) -> None:
        """Initialize the socket serial transport."""
        super().__init__(loop, protocol)
        self._read_task: asyncio.Task | None = None

    async def _connect(  # type: ignore[override]
        self,
        *,
        url: str,
        baudrate: int,
        parity: Parity = Parity.NONE,
        stopbits: StopBits = StopBits.ONE,
        xonxoff: bool = False,
        rtscts: bool = False,
        byte_size: int = 8,
        **kwargs,
    ) -> None:
        self._serial = SocketSerial(
            path=url,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            byte_size=byte_size,
        )

        await self._serial._async_open()

        self._read_task = self._loop.create_task(self._read_loop())
        self._protocol.connection_made(self)

    async def _read_loop(self) -> None:
        reader = self._serial._async_reader
        assert reader is not None

        try:
            while not self._closing:
                data = await reader.read(4096)

                if not data:
                    break

                self._protocol.data_received(data)
        finally:
            if not self._closing:
                self.close()

    def write(self, data: bytes | bytearray | memoryview) -> None:
        """Write data to the socket."""
        self._serial.write(data)

    def is_closing(self) -> bool:
        """Return whether the transport is closing."""
        return self._closing

    def close(self) -> None:
        """Close the transport."""
        if self._closing:
            return
        self._closing = True

        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None

        self._serial.close()
        self._protocol.connection_lost(None)

    async def flush(self) -> None:
        """Flush write buffers."""
        writer = self._serial._async_writer

        if writer is not None:
            await writer.drain()

    def get_write_buffer_size(self) -> int:
        """Get the number of bytes currently in the write buffer."""
        return 0
