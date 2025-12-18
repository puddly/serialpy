"""Windows serial port implementation using Win32 API."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import pywintypes
from typing_extensions import Buffer
from win32con import (
    DTR_CONTROL_ENABLE,
    EVENPARITY,
    FILE_ATTRIBUTE_NORMAL,
    FILE_FLAG_OVERLAPPED,
    GENERIC_READ,
    GENERIC_WRITE,
    MARKPARITY,
    NOPARITY,
    ODDPARITY,
    ONE5STOPBITS,
    ONESTOPBIT,
    OPEN_EXISTING,
    RTS_CONTROL_ENABLE,
    RTS_CONTROL_HANDSHAKE,
    SPACEPARITY,
    TWOSTOPBITS,
)
from win32event import INFINITE, CreateEvent, ResetEvent, WaitForSingleObject
from win32file import (
    OVERLAPPED,
    PURGE_RXABORT,
    PURGE_RXCLEAR,
    PURGE_TXABORT,
    PURGE_TXCLEAR,
    ClearCommError,
    CloseHandle,
    CreateFile,
    EscapeCommFunction,
    FlushFileBuffers,
    GetCommModemStatus,
    GetCommState,
    GetOverlappedResult,
    PurgeComm,
    ReadFile,
    SetCommState,
    SetCommTimeouts,
    SetupComm,
    WriteFile,
)
from winerror import ERROR_IO_PENDING

from serialx._serialx_rust import list_serial_ports_windows_impl
from serialx.common import SerialPortInfo

from ..common import (
    BaseSerial,
    BaseSerialTransport,
    ModemPins,
    Parity,
    PinState,
    StopBits,
)

# Constants missing from win32con
MS_CTS_ON = 0x0010
MS_DSR_ON = 0x0020
MS_RING_ON = 0x0040
MS_RLSD_ON = 0x0080
SETXOFF = 1
SETXON = 2
SETRTS = 3
CLRRTS = 4
SETDTR = 5
CLRDTR = 6

LOGGER = logging.getLogger(__name__)

WIN32_PARITY_MAP = {
    Parity.NONE: NOPARITY,
    Parity.ODD: ODDPARITY,
    Parity.EVEN: EVENPARITY,
    Parity.MARK: MARKPARITY,
    Parity.SPACE: SPACEPARITY,
}

WIN32_STOPBITS_MAP = {
    StopBits.ONE: ONESTOPBIT,
    StopBits.ONE_POINT_FIVE: ONE5STOPBITS,
    StopBits.TWO: TWOSTOPBITS,
}


class Win32Serial(BaseSerial):
    """Windows serial port implementation using Win32 API."""

    def __init__(self, *args, handle=None, **kwargs):
        """Initialize the Windows serial port."""
        super().__init__(*args, **kwargs)
        self._handle = handle

        self._overlapped_read = OVERLAPPED()
        self._overlapped_read.hEvent = CreateEvent(None, 1, 0, None)
        self._overlapped_write = OVERLAPPED()
        self._overlapped_write.hEvent = CreateEvent(None, 1, 0, None)

    def open(self) -> None:
        """Open the serial port."""
        LOGGER.debug("Opening serial port %r", self._path)

        if self._handle is not None:
            raise ValueError("Serial port is already open")

        path = str(self._path)

        # COM9+ need to be opened with a \\.\ prefix
        if path.upper().startswith("COM") and int(path[3:]) > 8:
            path = "\\\\.\\" + path

        try:
            self._handle = CreateFile(
                path,
                GENERIC_READ | GENERIC_WRITE,
                0,  # Exclusive access
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED,
                None,
            )
        except pywintypes.error as e:
            raise OSError(e.winerror, e.strerror, path) from e

        self._auto_close = True

    def configure_port(self) -> None:
        """Configure the serial port settings."""
        try:
            interval = int(1000 * self._buffer_burst_timeout)
            if interval <= 0 and self._buffer_burst_timeout > 0:
                interval = 1  # Minimum 1ms if burst timeout is set but small

            timeouts = (
                # ReadIntervalTimeout
                interval,
                # ReadTotalTimeoutMultiplier
                0,
                # ReadTotalTimeoutConstant
                0,
                # WriteTotalTimeoutMultiplier
                0,
                # WriteTotalTimeoutConstant
                0,
            )
            SetCommTimeouts(self._handle, timeouts)

            # Setup buffers (input, output) - standard pyserial size
            SetupComm(self._handle, 4096, 4096)

            # Clear buffers
            PurgeComm(
                self._handle,
                PURGE_TXABORT | PURGE_RXABORT | PURGE_TXCLEAR | PURGE_RXCLEAR,
            )

            # Configure DCB (Device Control Block)
            dcb = GetCommState(self._handle)
            dcb.BaudRate = self._baudrate
            dcb.ByteSize = self._byte_size
            dcb.StopBits = WIN32_STOPBITS_MAP[self._stopbits]
            dcb.Parity = WIN32_PARITY_MAP[self._parity]
            dcb.fBinary = 1  # Always True on Windows

            # Flow Control
            if self._rtscts:
                dcb.fRtsControl = RTS_CONTROL_HANDSHAKE
                dcb.fOutxCtsFlow = 1
            else:
                dcb.fRtsControl = RTS_CONTROL_ENABLE
                dcb.fOutxCtsFlow = 0

            if self._xonxoff:
                dcb.fOutX = 1
                dcb.fInX = 1
            else:
                dcb.fOutX = 0
                dcb.fInX = 0

            # Always enable DTR by default (similar to pyserial)
            dcb.fDtrControl = DTR_CONTROL_ENABLE

            # Explicitly disable DSR sensitivity and other flags that might block IO
            dcb.fOutxDsrFlow = 0
            dcb.fDsrSensitivity = 0
            dcb.fErrorChar = 0
            dcb.fNull = 0
            dcb.fAbortOnError = 0

            SetCommState(self._handle, dcb)

            self.set_modem_pins(dtr=self._rtsdtr_on_open, rts=self._rtsdtr_on_open)

            # Clear any errors
            ClearCommError(self._handle)
        except pywintypes.error as e:
            raise OSError(e.winerror, e.strerror) from e

    def fileno(self) -> int:
        """Return the file descriptor."""
        return int(self._handle)

    def close(self):
        """Close the serial port and release all handles."""
        if self._handle is not None:
            # Windows has no way to automatically do this on close, we do it manually
            self.set_modem_pins(dtr=self._rtsdtr_on_close, rts=self._rtsdtr_on_close)

        if self._handle is not None:
            CloseHandle(self._handle)
            self._handle = None

        if self._overlapped_read.hEvent:
            CloseHandle(self._overlapped_read.hEvent)
            self._overlapped_read.hEvent = None

        if self._overlapped_write.hEvent:
            CloseHandle(self._overlapped_write.hEvent)
            self._overlapped_write.hEvent = None

    def _get_modem_pins(self) -> ModemPins:
        """Get the current modem control bits."""
        stat = GetCommModemStatus(self._handle)
        return ModemPins(
            cts=PinState.HIGH if stat & MS_CTS_ON else PinState.LOW,
            dsr=PinState.HIGH if stat & MS_DSR_ON else PinState.LOW,
            rng=PinState.HIGH if stat & MS_RING_ON else PinState.LOW,
            car=PinState.HIGH if stat & MS_RLSD_ON else PinState.LOW,
        )

    def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        """Set the modem control bits."""
        if modem_pins.rts is not None:
            func = SETRTS if modem_pins.rts else CLRRTS
            EscapeCommFunction(self._handle, func)

        if modem_pins.dtr is not None:
            func = SETDTR if modem_pins.dtr else CLRDTR
            EscapeCommFunction(self._handle, func)

    def flush(self) -> None:
        """Flush write buffers."""
        FlushFileBuffers(self._handle)

    def readinto(self, b: Buffer) -> int:
        """Read data into the provided bytearray."""
        ResetEvent(self._overlapped_read.hEvent)

        try:
            rc, _ = ReadFile(self._handle, b, self._overlapped_read)
        except pywintypes.error as e:
            if e.winerror != ERROR_IO_PENDING:
                raise OSError(e.winerror, e.strerror) from e

            # Might not be reached if ReadFile returns result instead of raising
            rc = ERROR_IO_PENDING

        if rc == ERROR_IO_PENDING:
            # IO is pending, wait for it
            WaitForSingleObject(self._overlapped_read.hEvent, INFINITE)

        # Get the actual number of bytes read
        try:
            n = GetOverlappedResult(self._handle, self._overlapped_read, True)
        except pywintypes.error as e:
            raise OSError(e.winerror, e.strerror) from e

        return n

    def write(self, data: Buffer) -> int:
        """Write data to the serial port synchronously."""
        ResetEvent(self._overlapped_write.hEvent)

        try:
            err, n = WriteFile(self._handle, data, self._overlapped_write)
        except pywintypes.error as e:
            if e.winerror != ERROR_IO_PENDING:
                raise OSError(e.winerror, e.strerror) from e

            WaitForSingleObject(self._overlapped_write.hEvent, INFINITE)
            n = GetOverlappedResult(self._handle, self._overlapped_write, True)

        return n


class _MethodProxy:
    """Proxy object that forwards attribute access to a mapping."""

    def __init__(self, name: str, mapping: dict[str, Any]):
        """Initialize the method proxy."""
        self._name = name
        self._mapping = mapping

    def __getattr__(self, name: str) -> Any:
        """Forward attribute access to the mapping."""
        return self._mapping[name]


class Win32SerialTransport(BaseSerialTransport):
    """Windows serial transport using ProactorEventLoop."""

    def __init__(
        self, loop: asyncio.AbstractEventLoop, protocol: asyncio.Protocol
    ) -> None:
        """Initialize the Windows serial transport."""
        if not hasattr(loop, "_make_duplex_pipe_transport"):
            raise RuntimeError(
                f"Win32SerialTransport requires ProactorEventLoop, got {loop}"
            )

        super().__init__(loop, protocol)

        self._handle: int | None = None
        self._internal_transport = None
        self._closing: bool = False

    def serial_close(self):
        """Close the serial port."""
        assert self._serial is not None
        self._loop.run_in_executor(None, self._close_serial)

    def _close_serial(self) -> None:
        """Close the serial connection, internal."""
        assert self._serial is not None
        self._serial.close()
        self._loop.call_soon_threadsafe(self._protocol.connection_lost, None)

    def serial_shutdown(self, how) -> None:
        """Shutdown the serial connection."""
        # Intentionally ignored

    def serial_fileno(self) -> int:
        """Return the file descriptor."""
        assert self._serial is not None
        return self._serial.fileno()

    def protocol_data_received(self, data: bytes) -> None:
        """Forward data_received to the protocol."""
        self._protocol.data_received(data)

    def protocol_connection_made(self, transport: asyncio.Transport) -> None:
        """Forward connection_made to the protocol."""

        # Ignore `transport` and pass self instead
        self._protocol.connection_made(self)

    def protocol_connection_lost(self, exc: Exception | None) -> None:
        """Forward connection_lost to the protocol."""
        pass

    async def _open(self, path: os.PathLike) -> None:
        """Open the serial port."""
        self._handle = await self._loop.run_in_executor(
            None,
            lambda: CreateFile(
                path,
                GENERIC_READ | GENERIC_WRITE,
                0,  # Exclusive access
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED,
                None,
            ),
        )

    async def _connect(self, **kwargs) -> None:
        """Connect to the serial port."""
        path = kwargs.pop("path")
        await self._open(path)

        # Ensure buffer_burst_timeout is set to a small value to enable
        # "Wait for first byte, then return on gap" behavior for ReadFile.
        # If 0 (default), ReadFile with default timeouts might wait for full buffer.
        original_burst_timeout = kwargs.get("buffer_burst_timeout", 0)
        if original_burst_timeout == 0:
            kwargs["buffer_burst_timeout"] = 0.01

        self._serial = Win32Serial(
            **kwargs,
            path=path,
            handle=self._handle,
            # buffer_character_count is not used by Proactor transport
            buffer_character_count=0,
        )
        self._extra["serial"] = self._serial

        await self._loop.run_in_executor(None, self._serial.configure_port)

        # Use the internal _make_duplex_pipe_transport to create a true overlapping
        # bidirectional transport on the single handle.
        assert hasattr(self._loop, "_make_duplex_pipe_transport")
        self._internal_transport = self._loop._make_duplex_pipe_transport(
            # Proxy access to serial and protocol attributes through this instance
            sock=_MethodProxy(
                "sock",
                {
                    "fileno": self.serial_fileno,
                    "close": self.serial_close,
                    "shutdown": self.serial_shutdown,
                },
            ),
            protocol=_MethodProxy(
                "protocol",
                {
                    "connection_made": self.protocol_connection_made,
                    "data_received": self.protocol_data_received,
                    "connection_lost": self.protocol_connection_lost,
                },
            ),
            extra=self._extra,
        )

    def write(self, data):
        """Write data to the transport."""
        if self._internal_transport is None:
            raise RuntimeError("Transport not connected")

        self._internal_transport.write(data)

    def close(self) -> None:
        """Close the transport."""
        self._closing = True
        if self._internal_transport is not None:
            # Internal transport closes self._serial via sock.close()
            self._internal_transport.close()

    def pause_reading(self):
        """Pause reading from the transport."""
        if self._internal_transport is not None:
            self._internal_transport.pause_reading()

    def resume_reading(self):
        """Resume reading from the transport."""
        if self._internal_transport is not None:
            self._internal_transport.resume_reading()

    def set_protocol(self, protocol: asyncio.Protocol) -> None:  # type: ignore[override]
        """Set the protocol."""
        self._protocol = protocol
        if self._internal_transport is not None:
            self._internal_transport.set_protocol(protocol)

    def get_protocol(self) -> asyncio.Protocol:
        """Return the current protocol."""
        return self._protocol

    async def flush(self) -> None:
        """Flush write buffers, waiting until all data is written."""
        assert self._serial is not None
        assert self._internal_transport is not None
        try:
            # Wait for asyncio buffer to drain
            await self._internal_transport._make_empty_waiter()

            # Wait for hardware buffer to flush
            await self._loop.run_in_executor(None, self._serial.flush)
        finally:
            self._internal_transport._reset_empty_waiter()


def win32_list_serial_ports() -> list[SerialPortInfo]:
    """List available serial ports on Windows."""
    return [
        SerialPortInfo(
            device=port.device,
            resolved_device=port.device,
            vid=port.vid,
            pid=port.pid,
            serial_number=port.serial_number,
            manufacturer=port.manufacturer,
            product=port.product,
            bcd_device=port.bcd_device,
            interface_description=port.interface_description,
            interface_num=port.interface_num,
        )
        for port in list_serial_ports_windows_impl()
    ]
