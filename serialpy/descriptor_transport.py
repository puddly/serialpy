from __future__ import annotations

import os
import errno
import typing
import asyncio
import logging
import warnings


LOGGER = logging.getLogger(__name__)
LOG_THRESHOLD_FOR_CONNLOST_WRITES = 5


class DescriptorTransport(asyncio.transports.Transport):
    """
    A mixture of three private asyncio mixins and base transports:

      1. `asyncio.transports._FlowControlMixin`
      2. `asyncio.unix_events._UnixWritePipeTransport`
      3. `asyncio.unix_events._UnixReadPipeTransport`
    """

    max_size = 256 * 1024  # max bytes we read in one event loop iteration
    transport_name = "file"

    def __init__(
        self,
        loop: asyncio.BaseEventLoop,
        protocol: asyncio.Protocol,
        path: os.PathLike,
        waiter: asyncio.Future | None = None,
        extra: dict[str, typing.Any] | None = None,
    ) -> None:
        super().__init__(extra)
        self._path: os.PathLike | None = path
        self._fileno: int | None = os.open(self._path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)

        self._loop: asyncio.BaseEventLoop = loop
        self._set_write_buffer_limits()
        self._protocol_paused = False

        self._protocol: asyncio.Protocol = protocol
        self._buffer = bytearray()
        self._conn_lost_count = 0
        self._closing = False
        self._paused = False

        self._loop.call_soon(self._protocol.connection_made, self)
        self._loop.call_soon(self._loop.add_reader, self._fileno, self._read_ready)

        if waiter is not None:
            self._loop.call_soon(waiter.set_result, None)

    def _read_ready(self) -> None:
        try:
            data = os.read(self._fileno, self.max_size)
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as exc:
            self._fatal_error(exc, f"Fatal read error in {self.transport_name} transport")
        else:
            if data:
                self._protocol.data_received(data)
            else:
                if self._loop.get_debug():
                    LOGGER.info("%r was closed by peer", self)
                self._closing = True
                self._loop.remove_reader(self._fileno)
                self._loop.call_soon(self._protocol.eof_received)
                self._loop.call_soon(self._call_connection_lost, None)

    def pause_reading(self) -> None:
        if self._closing or self._paused:
            return
        self._paused = True
        self._loop.remove_reader(self._fileno)
        if self._loop.get_debug():
            LOGGER.debug("%r pauses reading", self)

    def resume_reading(self) -> None:
        if self._closing or not self._paused:
            return
        self._paused = False
        self._loop.add_reader(self._fileno, self._read_ready)
        if self._loop.get_debug():
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
                self._loop.call_exception_handler({
                    'message': 'protocol.pause_writing() failed',
                    'exception': exc,
                    'transport': self,
                    'protocol': self._protocol,
                })

    def _maybe_resume_protocol(self) -> None:
        if (self._protocol_paused and
                self.get_write_buffer_size() <= self._low_water):
            self._protocol_paused = False
            try:
                self._protocol.resume_writing()
            except (SystemExit, KeyboardInterrupt):
                raise
            except BaseException as exc:
                self._loop.call_exception_handler({
                    'message': 'protocol.resume_writing() failed',
                    'exception': exc,
                    'transport': self,
                    'protocol': self._protocol,
                })

    def get_write_buffer_limits(self) -> tuple[int, int]:
        return (self._low_water, self._high_water)

    def _set_write_buffer_limits(self, high=None, low=None):
        if high is None:
            if low is None:
                high = 64 * 1024
            else:
                high = 4 * low
        if low is None:
            low = high // 4

        if not high >= low >= 0:
            raise ValueError(f'high ({high!r}) must be >= low ({low!r}) must be >= 0')

        self._high_water = high
        self._low_water = low

    def set_write_buffer_limits(self, high=None, low=None) -> None:
        self._set_write_buffer_limits(high=high, low=low)
        self._maybe_pause_protocol()

    def get_write_buffer_size(self) -> int:
        return len(self._buffer)

    def write(self, data) -> None:
        assert isinstance(data, (bytes, bytearray, memoryview)), repr(data)
        if isinstance(data, bytearray):
            data = memoryview(data)
        if not data:
            return

        if self._closing or self._conn_lost_count > 0:
            if self._conn_lost_count >= LOG_THRESHOLD_FOR_CONNLOST_WRITES:
                LOGGER.warning("Port closed by peer or os.write raised exception.")
            self._conn_lost_count += 1
            return

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
                self._fatal_error(exc, f"Fatal write error in {self.transport_name} transport")
                return
            if n == len(data):
                return
            elif n > 0:
                data = memoryview(data)[n:]
            self._loop.add_writer(self._fileno, self._write_ready)

        self._buffer += data
        self._maybe_pause_protocol()

    def _write_ready(self) -> None:
        assert self._buffer, "Data should not be empty"

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
            self._fatal_error(exc, f"Fatal write error in {self.transport_name} transport")
        else:
            if n == len(self._buffer):
                self._buffer.clear()
                self._loop.remove_writer(self._fileno)
                self._maybe_resume_protocol()  # May append to buffer.
                if self._closing:
                    self._loop.remove_reader(self._fileno)
                    self._call_connection_lost(None)
                return
            elif n > 0:
                del self._buffer[:n]

    def can_write_eof(self) -> bool:
        return True

    def write_eof(self) -> None:
        if self._closing:
            return
        self._closing = True
        if not self._buffer:
            self._loop.remove_reader(self._fileno)
            self._loop.call_soon(self._call_connection_lost, None)

    def set_protocol(self, protocol: asyncio.Protocol) -> None:
        self._protocol = protocol

    def get_protocol(self) -> asyncio.Protocol:
        return self._protocol

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        if self._fileno is not None and not self._closing:
            self.write_eof()

    def __del__(self) -> None:
        if getattr(self, "_fileno", None) is not None:
            warnings.warn(f"unclosed transport {self!r}", ResourceWarning, source=self)
            os.close(self._fileno)
            self._fileno = None

    def _fatal_error(self, exc: Exception | None, message: str = f"Fatal error in {transport_name} transport") -> None:
        # should be called by exception handler only
        if isinstance(exc, OSError) and exc.errno in (errno.EIO, errno.ENXIO):
            if self._loop.get_debug():
                LOGGER.debug("%r: %s", self, message, exc_info=True)
        else:
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
        self._close(None)

    def _close(self, exc: Exception | None = None) -> None:
        self._closing = True
        if self._buffer:
            self._loop.remove_writer(self._fileno)
        self._buffer.clear()
        self._loop.remove_reader(self._fileno)
        self._loop.call_soon(self._call_connection_lost, exc)

    def _call_connection_lost(self, exc: Exception | None) -> None:
        try:
            self._protocol.connection_lost(exc)
        finally:
            os.close(self._fileno)
            self._protocol = None
            self._loop = None
