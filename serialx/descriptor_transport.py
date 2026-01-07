"""File descriptor-based asyncio transport."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
import errno
import logging
import os
import typing
from typing import Any
import warnings

LOGGER = logging.getLogger(__name__)
LOG_THRESHOLD_FOR_CONNLOST_WRITES = 5

# Prevent tasks from being garbage collected.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _create_background_task(coro: Coroutine) -> asyncio.Task[None]:
    """Create a background task that will not be garbage collected."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return task


class DescriptorTransport(asyncio.Transport):
    """File descriptor transport using asyncio."""

    max_size = 256 * 1024  # max bytes we read in one event loop iteration
    transport_name = "file"

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        protocol: asyncio.Protocol,
        extra: dict[str, typing.Any] | None = None,
    ) -> None:
        """Initialize the descriptor transport."""
        self._fileno: int | None = None

        self._loop: asyncio.AbstractEventLoop = loop
        self._set_write_buffer_limits()
        self._protocol_paused = False

        self._protocol = protocol
        self._buffer = bytearray()
        self._conn_lost_count = 0
        self._closing = False
        self._paused = False
        self._empty_waiter: asyncio.Future | None = None
        self._extra: dict[str, Any] = {}

        self._close_task: asyncio.Task[None] | None = None

    async def _open(self, path: os.PathLike) -> None:
        self._fileno = await self._loop.run_in_executor(
            None, os.open, path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK
        )

    async def _connect(self) -> None:
        assert self._fileno is not None
        self._loop.add_reader(self._fileno, self._read_ready)

    def _read_ready(self) -> None:
        LOGGER.debug("Event loop woke up reader")
        assert self._fileno is not None
        try:
            data = os.read(self._fileno, self.max_size)
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as exc:
            self._fatal_error(
                exc, f"Fatal read error in {self.transport_name} transport"
            )
        else:
            LOGGER.debug("Received %r", data)

            if data:
                self._protocol.data_received(data)
            else:
                LOGGER.info("%r was closed by peer", self)
                self._closing = True
                self._loop.remove_reader(self._fileno)
                self._loop.call_soon(self._protocol.eof_received)
                self._maybe_background_close(None)

    def pause_reading(self) -> None:
        """Pause reading from the file descriptor."""
        if self._closing or self._paused:
            return
        assert self._fileno is not None
        self._paused = True
        self._loop.remove_reader(self._fileno)

        LOGGER.debug("%r pauses reading", self)

    def resume_reading(self) -> None:
        """Resume reading from the file descriptor."""
        if self._closing or not self._paused:
            return
        assert self._fileno is not None
        self._paused = False
        self._loop.add_reader(self._fileno, self._read_ready)

        LOGGER.debug("%r resumes reading", self)

    def _maybe_pause_protocol(self) -> None:
        size = self.get_write_buffer_size()
        if size <= self._high_water:
            return
        if not self._protocol_paused:
            self._protocol_paused = True
            try:
                self._protocol.pause_writing()
            except (SystemExit, KeyboardInterrupt):
                raise
            except BaseException as exc:
                self._loop.call_exception_handler(
                    {
                        "message": "protocol.pause_writing() failed",
                        "exception": exc,
                        "transport": self,
                        "protocol": self._protocol,
                    }
                )

    def _maybe_resume_protocol(self) -> None:
        if self._protocol_paused and self.get_write_buffer_size() <= self._low_water:
            self._protocol_paused = False
            try:
                self._protocol.resume_writing()
            except (SystemExit, KeyboardInterrupt):
                raise
            except BaseException as exc:
                self._loop.call_exception_handler(
                    {
                        "message": "protocol.resume_writing() failed",
                        "exception": exc,
                        "transport": self,
                        "protocol": self._protocol,
                    }
                )

    def get_write_buffer_limits(self) -> tuple[int, int]:
        """Get the write buffer low and high water limits."""
        return (self._low_water, self._high_water)

    def _set_write_buffer_limits(self, high=None, low=None):
        if high is None:
            if low is None:  # noqa: SIM108
                high = 64 * 1024
            else:
                high = 4 * low
        if low is None:
            low = high // 4

        if not high >= low >= 0:
            raise ValueError(f"high ({high!r}) must be >= low ({low!r}) must be >= 0")

        self._high_water = high
        self._low_water = low

    def set_write_buffer_limits(self, high=None, low=None) -> None:
        """Set the write buffer low and high water limits."""
        self._set_write_buffer_limits(high=high, low=low)
        self._maybe_pause_protocol()

    def get_write_buffer_size(self) -> int:
        """Get the number of bytes currently in the write buffer."""
        return len(self._buffer)

    def _make_empty_waiter(self) -> asyncio.Future:
        """Create a future that resolves when the write buffer is empty."""
        if self._empty_waiter is not None:
            raise RuntimeError("Empty waiter is already set")
        self._empty_waiter = self._loop.create_future()
        if not self._buffer:
            self._empty_waiter.set_result(None)
        return self._empty_waiter

    def _reset_empty_waiter(self) -> None:
        """Reset the empty waiter."""
        self._empty_waiter = None

    def write(self, data) -> None:
        """Write data to the file descriptor."""
        assert isinstance(data, (bytes, bytearray, memoryview)), repr(data)
        LOGGER.debug("Immediately writing %r", data)

        if isinstance(data, bytearray):
            data = memoryview(data)
        if not data:
            return

        if self._closing or self._conn_lost_count > 0:
            if self._conn_lost_count >= LOG_THRESHOLD_FOR_CONNLOST_WRITES:
                LOGGER.warning("Port closed by peer or os.write raised exception.")
            self._conn_lost_count += 1
            return

        assert self._fileno is not None

        if not self._buffer:
            # Attempt to send it right away first.
            try:
                n = os.write(self._fileno, data)
            except (BlockingIOError, InterruptedError):
                n = 0
            except (SystemExit, KeyboardInterrupt):
                raise
            except BaseException as exc:
                self._conn_lost_count += 1
                # XXX: `exc` could actually be a BaseException here, which doesn't match
                # with the typing for `Protocol.connection_lost`
                self._fatal_error(
                    exc,  # type: ignore[arg-type]
                    f"Fatal write error in {self.transport_name} transport",
                )
                return

            len_data = len(data)
            LOGGER.debug("Sent %d of %d bytes", n, len_data)

            if n == len_data:
                return
            elif n > 0:
                data = memoryview(data)[n:]
            self._loop.add_writer(self._fileno, self._write_ready)

        LOGGER.debug("Buffering %r", data)
        self._buffer += data
        self._maybe_pause_protocol()

    def _write_ready(self) -> None:
        assert self._buffer, "Data should not be empty"
        assert self._fileno is not None

        try:
            n = os.write(self._fileno, self._buffer)
        except (BlockingIOError, InterruptedError):
            pass
        except (SystemExit, KeyboardInterrupt):
            raise
        except BaseException as exc:
            self._buffer.clear()
            self._conn_lost_count += 1
            # Remove writer here, _fatal_error() doesn't it
            # because _buffer is empty.
            self._loop.remove_writer(self._fileno)
            if self._empty_waiter is not None:
                self._empty_waiter.set_exception(exc)
            self._fatal_error(
                exc,  # type: ignore[arg-type]
                f"Fatal write error in {self.transport_name} transport",
            )
        else:
            if n == len(self._buffer):
                self._buffer.clear()
                self._loop.remove_writer(self._fileno)
                if self._empty_waiter is not None:
                    self._empty_waiter.set_result(None)
                self._maybe_resume_protocol()  # May append to buffer.
                if self._closing:
                    self._loop.remove_reader(self._fileno)
                    self._maybe_background_close(None)
                return
            elif n > 0:
                del self._buffer[:n]

    def can_write_eof(self) -> bool:
        """Check if EOF can be written."""
        return True

    def write_eof(self) -> None:
        """Write EOF to the file descriptor."""
        if self._closing:
            return
        assert self._fileno is not None
        self._closing = True
        if not self._buffer:
            self._loop.remove_reader(self._fileno)
            self._maybe_background_close(None)

    def set_protocol(self, protocol: asyncio.Protocol) -> None:  # type: ignore[override]
        """Set the protocol to use with this transport."""
        self._protocol = protocol

    def get_protocol(self) -> asyncio.Protocol:
        """Get the protocol used by this transport."""
        return self._protocol

    def close(self) -> None:
        """Close the transport."""
        LOGGER.debug("Closing at the request of the application")
        if self._fileno is not None and not self._closing:
            self.write_eof()

    def __del__(self) -> None:
        """Clean up transport on deletion."""
        if getattr(self, "_fileno", None) is not None:
            assert self._fileno is not None

            warnings.warn(f"unclosed transport {self!r}", ResourceWarning, source=self)
            if self._loop is not None:
                self._loop.remove_reader(self._fileno)

            os.close(self._fileno)

    def _fatal_error(
        self,
        exc: Exception | None,
        message: str = f"Fatal error in {transport_name} transport",
    ) -> None:
        LOGGER.debug("%r: %s", self, message, exc_info=True)

        # should be called by exception handler only
        if not isinstance(exc, OSError) or exc.errno not in (errno.EIO, errno.ENXIO):
            self._loop.call_exception_handler(
                {
                    "message": message,
                    "exception": exc,
                    "transport": self,
                    "protocol": self._protocol,
                }
            )

        self._close(exc)

    def abort(self) -> None:
        """Abort the transport immediately."""
        self._close(None)

    def _close(self, exc: Exception | None = None) -> None:
        self._closing = True
        assert self._fileno is not None
        if self._buffer:
            self._loop.remove_writer(self._fileno)
        self._buffer.clear()
        self._loop.remove_reader(self._fileno)
        self._maybe_background_close(exc)

    def _maybe_background_close(self, exc: Exception | None) -> None:
        """Start background task to close the transport if not already started."""
        LOGGER.debug("Backgrounding a close request: %r", exc)

        if self._close_task is not None:
            LOGGER.debug("Close task already exists, not closing again")
            return

        self._close_task = _create_background_task(self._call_connection_lost(exc))

    async def _call_connection_lost(self, exc: Exception | None) -> None:
        LOGGER.debug("Closing connection: %r", exc)

        try:
            assert self._fileno is not None
            self._loop.remove_reader(self._fileno)

            # For serial ports it would make sense to flush here BUT no modern serial
            # driver requires this: once the data is enqueued, even `os.close` blocks
            # for the entire transmit duration.
            LOGGER.debug("Closing file descriptor %s", self._fileno)
            await self._loop.run_in_executor(None, os.close, self._fileno)

            self._fileno = None
        finally:
            protocol = self._protocol
            self._loop = None  # type: ignore[assignment]
            self._protocol = None  # type: ignore[assignment]
            self._close_task = None

            LOGGER.debug("Calling protocol `connection_lost` with exc=%r", exc)
            protocol.connection_lost(exc)
