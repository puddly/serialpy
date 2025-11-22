"""Windows serial port implementation using Win32 API."""

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

from .common import BaseSerial, BaseSerialTransport, ModemBits, Parity, StopBits

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

        path = self._path

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
            CloseHandle(self._handle)
            self._handle = None

        if self._overlapped_read.hEvent:
            CloseHandle(self._overlapped_read.hEvent)
            self._overlapped_read.hEvent = None

        if self._overlapped_write.hEvent:
            CloseHandle(self._overlapped_write.hEvent)
            self._overlapped_write.hEvent = None

    def get_modem_bits(self) -> ModemBits:
        """Get the current modem control bits."""
        stat = GetCommModemStatus(self._handle)
        return ModemBits(
            cts=bool(stat & MS_CTS_ON),
            dsr=bool(stat & MS_DSR_ON),
            rng=bool(stat & MS_RING_ON),
            car=bool(stat & MS_RLSD_ON),
        )

    def set_modem_bits(self, modem_bits: ModemBits) -> None:
        """Set the modem control bits."""
        if modem_bits.rts is not None:
            func = SETRTS if modem_bits.rts else CLRRTS
            EscapeCommFunction(self._handle, func)

        if modem_bits.dtr is not None:
            func = SETDTR if modem_bits.dtr else CLRDTR
            EscapeCommFunction(self._handle, func)

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

    def __init__(self, loop, protocol):
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
        self._loop.call_soon(self._serial.close)
        self._loop.call_soon(self._protocol.connection_lost, None)

    def serial_shutdown(self, how) -> None:
        """Shutdown the serial connection."""

    def serial_fileno(self) -> int:
        """Return the file descriptor."""
        assert self._serial is not None
        return self._serial.fileno()

    def protocol_data_received(self, data: bytes) -> None:
        """Forward data_received to the protocol."""
        self._protocol.data_received(data)

    def protocol_connection_made(self, transport: asyncio.Transport) -> None:
        """Forward connection_made to the protocol."""
        self._protocol.connection_made(self)

    def protocol_connection_lost(self, exc: Exception | None) -> None:
        """Forward connection_lost to the protocol."""
        self._protocol.connection_lost(exc)

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

        # Call connection_made with THIS wrapper transport, not the internal one
        self._protocol.connection_made(self)

    def write(self, data):
        """Write data to the transport."""
        if self._internal_transport is None:
            raise RuntimeError("Transport not connected")

        self._internal_transport.write(data)

    def close(self) -> None:
        """Close the transport."""
        self._closing = True
        if self._internal_transport:
            self._internal_transport.close()
        # Internal transport closes the serial object (self._serial) via sock.close()

    def is_closing(self) -> bool:
        """Return whether the transport is closing."""
        return self._closing

    def pause_reading(self):
        """Pause reading from the transport."""
        if self._internal_transport is not None:
            self._internal_transport.pause_reading()

    def resume_reading(self):
        """Resume reading from the transport."""
        if self._internal_transport is not None:
            self._internal_transport.resume_reading()

    def set_protocol(self, protocol: asyncio.BaseProtocol) -> None:
        """Set the protocol."""
        self._protocol = protocol  # type: ignore[assignment]
        if self._internal_transport is not None:
            self._internal_transport.set_protocol(protocol)

    def get_protocol(self) -> asyncio.Protocol:
        """Return the current protocol."""
        return self._protocol
