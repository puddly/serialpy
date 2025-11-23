"""Test APIs with socat-created virtual PTY pairs."""

import asyncio
from collections.abc import AsyncIterator
import contextlib
import os
import sys
import tempfile

if sys.version_info >= (3, 11):
    from asyncio import timeout as asyncio_timeout
else:
    from async_timeout import timeout as asyncio_timeout


import serialpy


@contextlib.asynccontextmanager
async def async_create_socat_pair() -> AsyncIterator[tuple[str, str]]:
    """Create a pair of virtual PTYs using socat."""
    with tempfile.TemporaryDirectory() as tmpdir:
        in_tty = os.path.join(tmpdir, "ttyTestIn")
        out_tty = os.path.join(tmpdir, "ttyTestOut")

        proc = await asyncio.create_subprocess_exec(
            "socat",
            f"PTY,link={in_tty},raw,echo=0",
            f"PTY,link={out_tty},raw,echo=0",
        )

        # Give socat time to set up the PTYs
        await asyncio.sleep(0.5)

        assert proc.returncode is None

        yield (in_tty, out_tty)

        proc.terminate()
        await proc.wait()


# Source: https://github.com/home-assistant-libs/pyserial-asyncio-fast/pull/36
async def test_remove_writer() -> None:
    """Test that large writes with backpressure are handled correctly.

    This test catches three issue categories:
    1. AssertionError from writer not being removed when buffer empties
    2. Deadlock (via timeout) from direct writes blocking indefinitely
    3. Timing failures from writer not being added when buffering data
    """
    TEXT = b"Hello, World!"
    COUNT = 8 * 1024
    output_resume_event = asyncio.Event()
    data_received_count = 0

    async with async_create_socat_pair() as (in_tty, out_tty):

        class Input(asyncio.Protocol):
            _transport: serialpy.SerialTransport

            def connection_made(self, transport: asyncio.BaseTransport) -> None:
                assert isinstance(transport, serialpy.SerialTransport)
                self._transport = transport

            def data_received(self, data: bytes) -> None:
                nonlocal data_received_count
                data_received_count += len(data)
                self._transport.write(data)

        class Output(asyncio.Protocol):
            """Provides backpressure to writer via output_resume_event."""

            _transport: serialpy.SerialTransport

            def connection_made(self, transport: asyncio.BaseTransport) -> None:
                assert isinstance(transport, serialpy.SerialTransport)
                self._transport = transport
                output_resume_event.set()

            def pause_writing(self) -> None:
                output_resume_event.clear()

            def resume_writing(self) -> None:
                output_resume_event.set()

        loop = asyncio.get_running_loop()

        in_transport, _ = await serialpy.create_serial_connection(
            loop, Input, in_tty, baudrate=115200
        )
        out_transport, _ = await serialpy.create_serial_connection(
            loop, Output, out_tty, baudrate=115200
        )

        # Write a bunch of data so that we create a buffer and trigger backpressure
        for _ in range(COUNT):
            async with asyncio_timeout(5):
                await output_resume_event.wait()

            out_transport.write(TEXT)

        # Ensure that the write buffer eventually drains completely
        async with asyncio_timeout(5):
            while out_transport.get_write_buffer_size() > 0:
                await asyncio.sleep(0.1)

        # Verify we received some data on the input side
        assert data_received_count > 0

        out_transport.close()
        in_transport.close()
