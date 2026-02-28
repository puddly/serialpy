"""Test async APIs with socket:// endpoints."""

import asyncio
from collections.abc import AsyncIterator
import contextlib
import logging
import sys

if sys.version_info >= (3, 11):
    from asyncio import timeout as asyncio_timeout
else:
    from async_timeout import timeout as asyncio_timeout

import pytest

from serialx import create_serial_connection
from serialx.platforms.serial_socket import SocketSerialTransport
from tests.common import async_create_reader_writer_pair

LOGGER = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def async_create_socket_pair(
    relay_read_delay: float = 0.0,
) -> AsyncIterator[tuple[str, str]]:
    """Create two socket:// endpoints backed by a bidirectional relay."""
    left_to_right: asyncio.Queue[bytes | None] = asyncio.Queue()
    right_to_left: asyncio.Queue[bytes | None] = asyncio.Queue()
    handler_tasks: set[asyncio.Task[None]] = set()

    async def handle_client(
        side: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        handler_tasks.add(task)

        peer_side = "right" if side == "left" else "left"
        LOGGER.debug("accepted %s client connection", side)
        outbound_queue = left_to_right if side == "left" else right_to_left
        inbound_queue = right_to_left if side == "left" else left_to_right

        async def reader_to_queue() -> None:
            try:
                while data := await reader.read(4096):
                    await outbound_queue.put(data)
                    LOGGER.debug(
                        "queued %d bytes from %s to %s",
                        len(data),
                        side,
                        peer_side,
                    )
                    if relay_read_delay > 0:
                        await asyncio.sleep(relay_read_delay)
            except (BrokenPipeError, ConnectionResetError):
                LOGGER.debug("%s client disconnected abruptly", side)
            finally:
                await outbound_queue.put(None)
                LOGGER.debug("%s client reached EOF", side)

        async def queue_to_writer() -> None:
            while True:
                data = await inbound_queue.get()
                if data is None:
                    return

                try:
                    writer.write(data)
                    await writer.drain()
                    LOGGER.debug(
                        "forwarded %d bytes from %s to %s",
                        len(data),
                        peer_side,
                        side,
                    )
                except (BrokenPipeError, ConnectionResetError, OSError):
                    LOGGER.debug(
                        "failed forwarding bytes from %s to %s",
                        peer_side,
                        side,
                        exc_info=True,
                    )
                    return

        read_task = asyncio.create_task(reader_to_queue())
        write_task = asyncio.create_task(queue_to_writer())

        try:
            done, pending = await asyncio.wait(
                {read_task, write_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for pending_task in pending:
                pending_task.cancel()

            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.gather(*done, return_exceptions=True)
        finally:
            for relay_task in (read_task, write_task):
                if not relay_task.done():
                    relay_task.cancel()
            await asyncio.gather(read_task, write_task, return_exceptions=True)
            writer.close()
            with contextlib.suppress(
                ConnectionResetError,
                BrokenPipeError,
                OSError,
                asyncio.CancelledError,
            ):
                await writer.wait_closed()
            handler_tasks.discard(task)
            LOGGER.debug("closed %s client connection", side)

    left_server = await asyncio.start_server(
        lambda reader, writer: handle_client("left", reader, writer),
        host="127.0.0.1",
        port=0,
    )
    right_server = await asyncio.start_server(
        lambda reader, writer: handle_client("right", reader, writer),
        host="127.0.0.1",
        port=0,
    )

    left_socket_info = left_server.sockets
    right_socket_info = right_server.sockets
    assert left_socket_info is not None and left_socket_info
    assert right_socket_info is not None and right_socket_info

    left_url = f"socket://127.0.0.1:{left_socket_info[0].getsockname()[1]}"
    right_url = f"socket://127.0.0.1:{right_socket_info[0].getsockname()[1]}"
    LOGGER.debug("started socket pair server left=%s right=%s", left_url, right_url)

    try:
        yield (left_url, right_url)
    finally:
        wait_closed_coros = []
        for server in (left_server, right_server):
            try:
                server.close()
            except OSError:  # noqa: PERF203
                continue
            wait_closed_coros.append(server.wait_closed())

        if wait_closed_coros:
            await asyncio.gather(*wait_closed_coros)

        if handler_tasks:
            for task in list(handler_tasks):
                task.cancel()
            await asyncio.gather(*handler_tasks, return_exceptions=True)

        LOGGER.debug("stopped socket pair servers")


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


async def test_transport_close_is_idempotent_async() -> None:
    """Test closing socket writer multiple times is safe and drains buffer state."""
    async with (
        async_create_socket_pair() as (left, right),
        async_create_reader_writer_pair(left, right, baudrate=115200) as (
            reader_left,
            writer_left,
            reader_right,
            writer_right,
        ),
    ):
        writer_left.close()
        await writer_left.wait_closed()
        assert writer_left.transport.get_write_buffer_size() == 0

        writer_left.close()
        await writer_left.wait_closed()
