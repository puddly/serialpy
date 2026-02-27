"""Socket serial transport."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import socket
import urllib.parse

from typing_extensions import Buffer

from serialx.common import BaseSerial, BaseSerialTransport, ModemPins, Parity, StopBits

LOGGER = logging.getLogger(__name__)


class SocketSerial(BaseSerial):
    """Synchronous serial interface over a TCP socket."""

    def __init__(
        self,
        path: str | Path,
        baudrate: int,
        parity: Parity = Parity.NONE,
        stopbits: StopBits | int | float = StopBits.ONE,
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
        if parsed.hostname is None or parsed.port is None:
            raise ValueError(f"Invalid socket URI, expected both host and port: {path}")

        self._host = parsed.hostname
        self._port = parsed.port

        self._socket: socket.socket | None = None

    def open(self) -> None:
        """Open the socket connection."""
        assert self._host is not None
        assert self._port is not None
        self._socket = socket.create_connection((self._host, self._port))

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
        assert self._socket is not None

        data = bytes(b)
        self._socket.sendall(data)
        return len(data)

    def readinto(self, b: Buffer) -> int:
        """Read bytes from socket into buffer."""
        assert self._socket is not None

        m = memoryview(b).cast("B")
        return self._socket.recv_into(m)

    def close(self) -> None:
        """Close the socket."""
        if self._socket is not None:
            self._socket.close()
            self._socket = None


class _SocketProxyProtocol(asyncio.Protocol):
    """Bridge protocol between asyncio TCP transport and SocketSerialTransport."""

    def __init__(self, serial_transport: SocketSerialTransport) -> None:
        self._serial_transport = serial_transport

    def data_received(self, data: bytes) -> None:
        self._serial_transport._data_received(data)

    def pause_writing(self) -> None:
        self._serial_transport._pause_writing()

    def resume_writing(self) -> None:
        self._serial_transport._resume_writing()

    def connection_lost(self, exc: Exception | None) -> None:
        self._serial_transport._connection_lost(exc)


class SocketSerialTransport(BaseSerialTransport):
    """Serial transport over a TCP socket."""

    transport_name = "socket"
    _serial: SocketSerial

    def __init__(
        self, loop: asyncio.AbstractEventLoop, protocol: asyncio.Protocol
    ) -> None:
        """Initialize the socket serial transport."""
        super().__init__(loop, protocol)
        self._tcp_transport: asyncio.Transport | None = None
        self._connection_lost_called = False

    async def _connect(  # type: ignore[override]
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
        self._serial = SocketSerial(
            path=path,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            byte_size=byte_size,
        )

        tcp_transport, _ = await self._loop.create_connection(
            lambda: _SocketProxyProtocol(self),
            host=self._serial._host,
            port=self._serial._port,
        )
        self._tcp_transport = tcp_transport

        if self._connection_lost_called:
            self._tcp_transport = None
            return

        self._protocol.connection_made(self)

    def _data_received(self, data: bytes) -> None:
        """Handle data received from the TCP transport."""
        self._protocol.data_received(data)

    def _pause_writing(self) -> None:
        """Propagate backpressure from TCP transport to serial protocol."""
        self._protocol.pause_writing()

    def _resume_writing(self) -> None:
        """Propagate resume signal from TCP transport to serial protocol."""
        self._protocol.resume_writing()

    def _connection_lost(self, exc: Exception | None) -> None:
        """Handle connection lost from the TCP transport."""
        if self._connection_lost_called:
            return
        self._connection_lost_called = True
        self._closing = True
        self._tcp_transport = None
        self._protocol.connection_lost(exc)

    def write(self, data: bytes | bytearray | memoryview) -> None:
        """Write data to the socket."""
        assert self._tcp_transport is not None
        self._tcp_transport.write(data)

    def pause_reading(self) -> None:
        """Pause reading from the socket transport."""
        assert self._tcp_transport is not None
        self._tcp_transport.pause_reading()

    def resume_reading(self) -> None:
        """Resume reading from the socket transport."""
        assert self._tcp_transport is not None
        self._tcp_transport.resume_reading()

    def is_closing(self) -> bool:
        """Return whether the transport is closing."""
        return self._closing

    def close(self) -> None:
        """Close the transport."""
        if self._connection_lost_called:
            return
        self._closing = True

        if self._tcp_transport is not None:
            self._tcp_transport.close()
        else:
            self._connection_lost(None)

    async def flush(self) -> None:
        """Flush write buffers (no-op, TCP transport handles buffering)."""

    def get_write_buffer_size(self) -> int:
        """Get the number of bytes currently in the write buffer."""
        if self._tcp_transport is not None:
            return self._tcp_transport.get_write_buffer_size()
        return 0
