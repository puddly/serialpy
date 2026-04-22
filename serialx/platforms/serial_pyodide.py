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
from .pyodide_types import JsSerialPort, SerialOutputSignals

_LOGGER = logging.getLogger(__name__)

# Registry of paths → JS `SerialPort` instances. Lets out-of-band JS code
# associate a URL with a SerialPort before `create_serial_connection` runs;
# callers that don't name a path share the default slot at _DEFAULT_PATH.
_REGISTERED_JS_PORTS: dict[str, JsSerialPort] = {}
_DEFAULT_PATH = "pyodide://serial"
_SERIAL_PORT_CLOSING_TASKS: list[asyncio.Task[Any]] = []


def register_js_port(path: str, js_port: JsSerialPort) -> None:
    """Associate a URL with a JS `SerialPort` for later connection."""
    _REGISTERED_JS_PORTS[path] = js_port


def unregister_js_port(path: str) -> None:
    """Remove the entry for `path` from the JS port registry, if any."""
    _REGISTERED_JS_PORTS.pop(path, None)


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

    def _flush(self) -> None:
        """Flush write buffers."""
        raise NotImplementedError()

    def num_unread_bytes(self) -> int:
        """Return the number of bytes in the read buffer."""
        raise NotImplementedError()

    def num_unwritten_bytes(self) -> int:
        """Return the number of bytes in the write buffer."""
        raise NotImplementedError()

    def _reset_read_buffer(self) -> None:
        """Clear the read buffer."""
        raise NotImplementedError()

    def _reset_write_buffer(self) -> None:
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
        self._write_buffer_size = 0
        self._closing = False
        self._close_port_task: asyncio.Task[None] | None = None

        self._js_port: JsSerialPort | None = None
        self._js_reader: Any | None = None
        self._js_writer: Any | None = None

        self._reader_task: asyncio.Task[None] | None = None
        self._writer_task: asyncio.Task[None] | None = None

    @classmethod
    def set_global_js_serial_port(cls, js_port: JsSerialPort) -> None:
        """Set the default JS serial port instance (at `pyodide://serial`)."""
        register_js_port(_DEFAULT_PATH, js_port)

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
        # A `SerialPort` object must be passed in externally, or pulled from the global
        js_port: JsSerialPort | None = None,
        **kwargs: Any,
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

        self._serial = PyodideSerial(
            path=path,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            byte_size=byte_size,
            **kwargs,
        )

        if self._serial.stopbits not in _STOPBITS_MAP:
            raise UnsupportedSetting(
                f"Unsupported stopbits setting: {self._serial.stopbits!r}"
            )

        if self._serial.parity not in _PARITY_MAP:
            raise UnsupportedSetting(
                f"Unsupported parity setting: {self._serial.parity!r}"
            )

        if byte_size not in (7, 8):
            raise UnsupportedSetting(f"Unsupported byte_size: {byte_size!r}")

        if js_port is None:
            js_port = _REGISTERED_JS_PORTS.get(path)

        if js_port is None:
            raise SerialException(
                f"No JS serial port registered for {path!r}; call "
                f"`register_js_port(path, js_port)` or pass `js_port=` to `connect`"
            )

        await js_port.open(
            baudRate=self._serial.baudrate,
            dataBits=self._serial.byte_size,
            flowControl=flow_control,
            parity=_PARITY_MAP[self._serial.parity],
            stopBits=_STOPBITS_MAP[self._serial.stopbits],
        )

        self._js_port = js_port
        assert self._js_port is not None

        if self._serial.rtsdtr_on_open is not PinState.UNDEFINED:
            await self.set_modem_pins(
                rts=self._serial.rtsdtr_on_open,
                dtr=self._serial.rtsdtr_on_open,
            )

        self._js_reader = self._js_port.readable.getReader()
        self._js_writer = self._js_port.writable.getWriter()

        self._reader_task = self._loop.create_task(self._reader_loop())
        self._writer_task = self._loop.create_task(self._writer_loop())

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
                assert not isinstance(chunk, type)
                self._write_buffer_size -= len(chunk)
                self._write_queue.task_done()

    async def _reader_loop(self) -> None:
        while self._js_reader is not None:
            result = await self._js_reader.read()
            if result.done:
                self._cleanup(RuntimeError("Other side has closed"))
                return

            assert self._protocol is not None
            self._protocol.data_received(bytes(result.value))

    async def get_modem_pins(self) -> ModemPins:
        """Get modem control bits."""
        assert self._js_port is not None
        result = await self._js_port.getSignals()

        return ModemPins(
            cts=PinState.convert(result.clearToSend),
            car=PinState.convert(result.dataCarrierDetect),
            rng=PinState.convert(result.ringIndicator),
            dsr=PinState.convert(result.dataSetReady),
        )

    async def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        """Set modem control bits, internal."""
        signals = SerialOutputSignals()
        if modem_pins.rts is not PinState.UNDEFINED:
            signals["requestToSend"] = modem_pins.rts is PinState.HIGH
        if modem_pins.dtr is not PinState.UNDEFINED:
            signals["dataTerminalReady"] = modem_pins.dtr is PinState.HIGH

        if signals:
            assert self._js_port is not None
            await self._js_port.setSignals(**signals)

    def write(self, data: bytes | bytearray | memoryview) -> None:
        """Write data to the transport."""
        self._write_buffer_size += len(data)
        self._write_queue.put_nowait(bytes(data))

    def get_write_buffer_size(self) -> int:
        """Return the number of bytes currently queued for writing."""
        return self._write_buffer_size

    async def flush(self) -> None:
        """Flush write buffers, waiting until all data is written."""
        await self._write_queue.join()

    def abort(self) -> None:
        """Close the transport immediately, discarding pending writes."""
        if self._writer_task is not None and not self._writer_task.done():
            self._writer_task.cancel()
        self._cleanup(None)

    def __del__(self) -> None:
        """Clean up the transport if it was not properly closed."""
        self._cleanup(RuntimeError("Transport was not closed!"))

    async def _close_port(self, exception: Exception | None) -> None:
        # Drain pending writes, unless abort() already cancelled the writer.
        if self._writer_task is not None and not self._writer_task.done():
            try:
                async with asyncio_timeout(_WRITE_FLUSH_TIMEOUT):
                    _LOGGER.debug("Waiting for pending writes to finish")
                    self._write_queue.put_nowait(ExitSentinel)
                    await self._writer_task
            except (asyncio.TimeoutError, asyncio.CancelledError):
                _LOGGER.debug("Write task did not drain cleanly; cancelling")
                if not self._writer_task.done():
                    self._writer_task.cancel()
                with contextlib.suppress(BaseException):
                    await self._writer_task

        if self._js_writer is not None:
            self._js_writer.releaseLock()
            self._js_writer = None

        if self._js_port is not None:
            if self._serial.rtsdtr_on_close is not PinState.UNDEFINED:
                with contextlib.suppress(Exception):
                    await self.set_modem_pins(
                        rts=self._serial.rtsdtr_on_close,
                        dtr=self._serial.rtsdtr_on_close,
                    )
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
        elif not self._closed_waiter.done():
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
