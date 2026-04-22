"""Pyodide serial port implementation using the Web Serial API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

if sys.version_info >= (3, 11):
    from asyncio import timeout as asyncio_timeout
else:
    from async_timeout import timeout as asyncio_timeout

from typing import Any, final

import js
from typing_extensions import Buffer

from ..common import (
    BaseSerial,
    BaseSerialTransport,
    ModemPins,
    Parity,
    PinState,
    SerialException,
    StopBits,
    UnsupportedSetting,
    register_uri_handler,
)

JsSerialPort = Any

_LOGGER = logging.getLogger(__name__)

# Global serial port, so out-of-band JS code can set it before the transport is created
_GLOBAL_SERIAL_PORT = None
_GLOBAL_SERIAL_PORT_NAME = "pyodide://serial"
_SERIAL_PORT_CLOSING_TASKS: list[asyncio.Task[Any]] = []

_WRITE_FLUSH_TIMEOUT = 5.0

_PARITY_MAP = {
    Parity.NONE: "none",
    Parity.ODD: "odd",
    Parity.EVEN: "even",
}

_STOPBITS_MAP = {
    StopBits.ONE: 1,
    StopBits.TWO: 2,
}


@final
class ExitSentinel:
    """A sentinel object to signal writer loop exit."""


class PyodideSerial(BaseSerial):
    """Synchronous serial port implementation for Pyodide."""

    def _open(self) -> None:
        raise NotImplementedError()

    def _configure_port(self) -> None:
        raise NotImplementedError()

    def _close(self) -> None:
        raise NotImplementedError()

    def _get_modem_pins(self) -> ModemPins:
        raise NotImplementedError()

    def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        raise NotImplementedError()

    def _readinto(self, b: Buffer, *, timeout: float | None) -> int:
        raise NotImplementedError()

    def _write(self, data: Buffer, *, timeout: float | None) -> int:
        raise NotImplementedError()

    def flush(self) -> None:
        """Flush write buffers."""
        raise NotImplementedError()

    def num_unread_bytes(self) -> int:
        """Return the number of bytes in the read buffer."""
        raise NotImplementedError()

    def num_unwritten_bytes(self) -> int:
        """Return the number of bytes in the write buffer."""
        raise NotImplementedError()

    def reset_read_buffer(self) -> None:
        """Clear the read buffer."""
        raise NotImplementedError()

    def reset_write_buffer(self) -> None:
        """Clear the write buffer."""
        raise NotImplementedError()

    @property
    def is_open(self) -> bool:
        """Return whether the port is open."""
        raise NotImplementedError()


class PyodideSerialTransport(BaseSerialTransport):
    """Async serial transport for Pyodide using the Web Serial API."""

    transport_name = "pyodide"
    _serial: PyodideSerial

    def __init__(
        self, loop: asyncio.AbstractEventLoop, protocol: asyncio.Protocol
    ) -> None:
        """Initialize the Pyodide serial transport."""
        super().__init__(loop, protocol)

        self._write_queue: asyncio.Queue[bytes | type[ExitSentinel]] = asyncio.Queue()
        self._closing = False
        self._close_port_task: asyncio.Task[None] | None = None

        self._js_port = None
        self._js_reader = None
        self._js_writer = None

        self._reader_task = None
        self._writer_task = None

    @classmethod
    def set_global_js_serial_port(cls, js_port: JsSerialPort) -> None:
        """Set the global JS serial port instance."""
        global _GLOBAL_SERIAL_PORT  # noqa: PLW0603
        _GLOBAL_SERIAL_PORT = js_port

    async def _connect(
        self,
        *,
        path: str,
        baudrate: int,
        parity: Parity = Parity.NONE,
        stopbits: StopBits = StopBits.ONE,
        xonxoff: bool = False,
        rtscts: bool = False,
        byte_size: int = 8,
        # A `SerialPort` object must be passed in externally, or pulled from the global
        js_port: JsSerialPort | None = None,
        **kwargs,
    ) -> None:
        if rtscts and xonxoff:
            raise UnsupportedSetting(
                "Hardware and software flow control cannot both be enabled on this platform"
            )
        elif rtscts:
            flow_control = "hardware"
        elif xonxoff:
            flow_control = "software"
        else:
            flow_control = "none"

        if stopbits not in _STOPBITS_MAP:
            raise UnsupportedSetting(f"Unsupported stopbits setting: {stopbits!r}")

        if parity not in _PARITY_MAP:
            raise UnsupportedSetting(f"Unsupported parity setting: {parity!r}")

        if js_port is None:
            if path != _GLOBAL_SERIAL_PORT_NAME:
                raise UnsupportedSetting(
                    f"Path must be {_GLOBAL_SERIAL_PORT_NAME!r} when using the global serial port instance. Got {path!r} instead."
                )

            js_port = _GLOBAL_SERIAL_PORT

        if js_port is None:
            raise SerialException(
                "No JS serial port object has been provided. Set one globally or pass an instance into `connect`"
            )

        await js_port.open(
            baudRate=baudrate,
            dataBits=byte_size,
            flowControl=flow_control,
            parity=_PARITY_MAP[parity],
            stopBits=_STOPBITS_MAP[stopbits],
        )

        self._js_port = js_port
        assert self._js_port is not None
        self._js_reader = self._js_port.readable.getReader()
        self._js_writer = self._js_port.writable.getWriter()

        self._reader_task = self._loop.create_task(self._reader_loop())
        self._writer_task = self._loop.create_task(self._writer_loop())

        self._js_port = js_port
        self._protocol.connection_made(self)

    async def _writer_loop(self) -> None:
        while True:
            chunk = await self._write_queue.get()

            if chunk is ExitSentinel or self._js_writer is None:
                _LOGGER.debug("Received exit sentinel, exiting")
                self._write_queue.task_done()
                return

            try:
                await self._js_writer.write(js.Uint8Array.new(chunk))
            except Exception as e:
                _LOGGER.exception("Error writing to serial port")
                self._cleanup(e)
                break
            finally:
                self._write_queue.task_done()

    async def _reader_loop(self) -> None:
        while self._js_reader is not None:
            result = await self._js_reader.read()
            if result.done:
                self._cleanup(RuntimeError("Other side has closed"))
                return

            assert self._protocol is not None
            self._protocol.data_received(bytes(result.value))

    async def _get_modem_pins(self) -> ModemPins:
        """Get modem control bits, internal."""
        assert self._js_port is not None
        result = await self._js_port.getSignals()

        return ModemPins(
            cts=result.clearToSend,
            car=result.dataCarrierDetect,
            rng=result.ringIndicator,
            dsr=result.dataSetReady,
        )

    async def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        """Set modem control bits, internal."""
        kwargs = {}

        if modem_pins.rts is not PinState.UNDEFINED:
            kwargs["requestToSend"] = modem_pins.rts.to_bool()
        if modem_pins.dtr is not PinState.UNDEFINED:
            kwargs["dataTerminalReady"] = modem_pins.dtr.to_bool()

        if kwargs:
            assert self._js_port is not None
            await self._js_port.setSignals(**kwargs)

    def write(self, data: bytes) -> None:
        """Write data to the transport."""
        self._write_queue.put_nowait(data)

    async def flush(self) -> None:
        """Flush write buffers, waiting until all data is written."""
        await self._write_queue.join()

    def __del__(self) -> None:
        """Clean up the transport if it was not properly closed."""
        self._cleanup(RuntimeError("Transport was not closed!"))

    async def _close_port(self, exception: Exception | None) -> None:
        _LOGGER.debug("Flushing pending writes")

        # First, wait for writes to finish
        if self._writer_task is not None:
            try:
                async with asyncio_timeout(_WRITE_FLUSH_TIMEOUT):
                    _LOGGER.debug("Waiting for pending writes to finish")
                    self._write_queue.put_nowait(ExitSentinel)
                    await self._writer_task
            except asyncio.TimeoutError:
                _LOGGER.debug("Write task did not exit in time, cancelling it")
                with contextlib.suppress(asyncio.CancelledError):
                    self._writer_task.cancel()
                    await self._writer_task

        if self._js_writer is not None:
            self._js_writer.releaseLock()
            self._js_writer = None

        if self._js_port is not None:
            await self._js_port.close()
            self._js_port = None

        assert self._close_port_task is not None

        # If the task cannot be removed, we should still call `connection_lost`
        with contextlib.suppress(ValueError):
            _SERIAL_PORT_CLOSING_TASKS.remove(self._close_port_task)

        # Only now do we call `connection_lost`
        self._call_protocol_connection_lost(exception)

    def _cleanup(self, exception: Exception | None) -> None:
        self._closing = True

        # The reader task should be cancelled. We do not cancel the writer task, we wait
        # for it to cleanly exit.
        if self._reader_task is not None:
            self._reader_task.cancel()

        if self._js_reader is not None:
            self._js_reader.releaseLock()
            self._js_reader = None

        if self._js_port is not None and self._close_port_task is None:
            self._close_port_task = asyncio.create_task(self._close_port(exception))
            _SERIAL_PORT_CLOSING_TASKS.append(self._close_port_task)
        elif self._protocol is not None:
            # If we have no serial port but have a connected protocol, we still need to
            # notify the protocol that the connection is lost
            self._call_protocol_connection_lost(exception)

    def close(self) -> None:
        """Close the transport."""
        self._cleanup(None)


register_uri_handler(
    scheme="device://",
    unique_scheme="pyodide://",
    sync_cls=PyodideSerial,
    async_transport_cls=PyodideSerialTransport,
    weight=-1,
)
