"""Test async APIs with socket:// endpoints."""

import asyncio
import logging
import sys

if sys.version_info >= (3, 11):
    from asyncio import timeout as asyncio_timeout
else:
    from async_timeout import timeout as asyncio_timeout

import pytest

from serialx import create_serial_connection
from serialx.platforms.serial_socket import SocketSerialTransport
from tests.socket_relay import async_create_socket_pair

LOGGER = logging.getLogger(__name__)


async def test_invalid_socket_uri_async() -> None:
    """Test invalid socket URI is rejected by public async API."""
    loop = asyncio.get_running_loop()

    with pytest.raises(ValueError, match="expected both host and port"):
        await create_serial_connection(
            loop,
            asyncio.Protocol,
            "socket://127.0.0.1",
            baudrate=115200,
        )


async def test_transport_close_before_connect_completes_async() -> None:
    """Test close-before-connect race is handled without duplicate callbacks."""

    class ProbeProtocol(asyncio.Protocol):
        def __init__(self) -> None:
            self.connection_made_calls = 0
            self.connection_lost_calls = 0
            self.connection_lost_future = asyncio.get_running_loop().create_future()

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            self.connection_made_calls += 1

        def connection_lost(self, exc: Exception | None) -> None:
            self.connection_lost_calls += 1
            if not self.connection_lost_future.done():
                self.connection_lost_future.set_result(None)

    async with async_create_socket_pair() as (left, _right):
        loop = asyncio.get_running_loop()
        protocol = ProbeProtocol()
        transport = SocketSerialTransport(loop, protocol)

        connect_task = asyncio.create_task(
            transport.connect(path=left, baudrate=115200)
        )
        transport.close()
        await connect_task
        await asyncio.wait_for(protocol.connection_lost_future, timeout=5)
        await asyncio.sleep(0)

        # connection_made should never fire in this race path.
        assert protocol.connection_made_calls == 0
        # close-before-connect should notify connection_lost exactly once.
        assert protocol.connection_lost_calls == 1
        assert transport.is_closing() is True
        assert transport.get_write_buffer_size() == 0

        # Additional closes are idempotent after connection_lost.
        transport.close()
        await asyncio.sleep(0)
        assert protocol.connection_lost_calls == 1


async def test_transport_backpressure_callbacks_async() -> None:
    """Test backpressure pause/resume callbacks through public async APIs."""
    output_pause_count = 0
    output_resume_count = 0

    async with async_create_socket_pair(relay_read_delay=0.001) as (in_tty, out_tty):
        loop = asyncio.get_running_loop()
        input_lost = loop.create_future()
        output_lost = loop.create_future()

        class Input(asyncio.Protocol):
            def data_received(self, data: bytes) -> None:
                return

            def connection_lost(self, exc: Exception | None) -> None:
                if not input_lost.done():
                    input_lost.set_result(None)

        class Output(asyncio.Protocol):
            _transport: SocketSerialTransport

            def connection_made(self, transport: asyncio.BaseTransport) -> None:
                assert isinstance(transport, SocketSerialTransport)
                self._transport = transport

            def pause_writing(self) -> None:
                nonlocal output_pause_count
                output_pause_count += 1

            def resume_writing(self) -> None:
                nonlocal output_resume_count
                output_resume_count += 1

            def connection_lost(self, exc: Exception | None) -> None:
                if not output_lost.done():
                    output_lost.set_result(None)

        in_transport, _ = await create_serial_connection(
            loop, Input, in_tty, baudrate=115200
        )
        out_transport, _ = await create_serial_connection(
            loop, Output, out_tty, baudrate=115200
        )
        await asyncio.sleep(0)

        payload = b"X" * 65536
        for _ in range(64):
            out_transport.write(payload)

        async with asyncio_timeout(10):
            while out_transport.get_write_buffer_size() > 0:
                await asyncio.sleep(0.05)

        await asyncio.sleep(0.1)
        assert output_pause_count > 0
        assert output_resume_count > 0

        out_transport.close()
        in_transport.close()
        await asyncio.gather(input_lost, output_lost)
