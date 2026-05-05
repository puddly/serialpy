"""Windows serial port implementation using Win32 API."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import pywintypes
from typing_extensions import Buffer, Unpack
from win32con import (
    DTR_CONTROL_ENABLE,
    DTR_CONTROL_HANDSHAKE,
    EVENPARITY,
    FILE_ATTRIBUTE_NORMAL,
    FILE_FLAG_OVERLAPPED,
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    GENERIC_READ,
    GENERIC_WRITE,
    MARKPARITY,
    MAXDWORD,
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
from win32event import CreateEvent as _CreateEvent, ResetEvent
from win32file import (
    OVERLAPPED,
    PURGE_RXABORT,
    PURGE_RXCLEAR,
    PURGE_TXABORT,
    PURGE_TXCLEAR,
    CancelIo,
    ClearCommError,
    CloseHandle,
    CreateFile as _CreateFile,
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

from serialx.common import SerialPortInfo
from serialx.serialx_rust import list_serial_ports_impl

from ..common import (
    BaseSerial,
    BaseSerialTransport,
    ConnectKwargs,
    ModemPins,
    Parity,
    PinState,
    StopBits,
    register_uri_handler,
)

if TYPE_CHECKING:
    from _win32typing import PyOVERLAPPED, PySECURITY_ATTRIBUTES

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


def _normalize_windows_port_path(path: os.PathLike[str] | str) -> str:
    """Normalize a Windows serial device path for CreateFile."""
    path = str(path)

    # COM ports >= 10 require the \\.\  prefix for CreateFile
    if not path.startswith("\\\\.\\"):
        path = "\\\\.\\" + path

    return path


class CommTimeouts(NamedTuple):
    """COMMTIMEOUTS struct as a 5-tuple, accepted directly by SetCommTimeouts."""

    ReadIntervalTimeout: int
    ReadTotalTimeoutMultiplier: int
    ReadTotalTimeoutConstant: int
    WriteTotalTimeoutMultiplier: int
    WriteTotalTimeoutConstant: int


def CreateEvent(  # noqa: N802
    *,
    EventAttributes: Any,
    bManualReset: bool,
    bInitialState: bool,
    Name: str | None,
) -> int:
    """Keyword-only wrapper for win32event.CreateEvent."""
    return _CreateEvent(EventAttributes, bManualReset, bInitialState, Name)


def CreateFile(  # noqa: N802
    *,
    FileName: str,
    DesiredAccess: int,
    ShareMode: int,
    SecurityAttributes: PySECURITY_ATTRIBUTES | None,
    CreationDisposition: int,
    FlagsAndAttributes: int,
    TemplateFile: int | None,
) -> int:
    """Keyword-only wrapper for win32file.CreateFile."""
    result = _CreateFile(
        FileName,
        DesiredAccess,
        ShareMode,
        SecurityAttributes,
        CreationDisposition,
        FlagsAndAttributes,
        TemplateFile,
    )

    return cast(int, result)


def _safe_close_handle(handle: int) -> None:
    """Close a Win32 handle, suppressing and logging errors."""
    try:
        CloseHandle(handle)
    except pywintypes.error:
        LOGGER.debug("Failed to close handle %r", handle, exc_info=True)


class Win32Serial(BaseSerial):
    """Windows serial port implementation using Win32 API."""

    def __init__(
        self,
        *args: Any,
        handle: int | None = None,
        inter_byte_timeout: float = 0.01,
        read_buffer_size: int = 4096,
        write_buffer_size: int = 4096,
        **kwargs: Any,
    ) -> None:
        """Initialize the Windows serial port."""
        super().__init__(*args, **kwargs)
        self._handle = handle
        self._inter_byte_timeout = inter_byte_timeout
        self._read_buffer_size = read_buffer_size
        self._write_buffer_size = write_buffer_size
        self._overlapped_read: PyOVERLAPPED | None = None
        self._overlapped_write: PyOVERLAPPED | None = None
        self._commtimeouts: CommTimeouts | None = None

    def _apply_commtimeouts(
        self,
        *,
        read_timeout: float | None = None,
        write_timeout: float | None = None,
    ) -> None:
        """Encode and push timeouts to the kernel; skip the syscall if unchanged."""
        assert self._handle is not None
        interval = (
            max(int(self._inter_byte_timeout * 1000), 1)
            if self._inter_byte_timeout
            else 0
        )

        if read_timeout == 0:
            # Documented sentinel: return immediately with whatever's buffered
            read_interval_timeout = MAXDWORD
            read_total_timeout_constant = 0
        elif read_timeout is None:
            read_interval_timeout = interval
            read_total_timeout_constant = 0
        else:
            read_interval_timeout = interval
            read_total_timeout_constant = max(int(read_timeout * 1000), 1)

        if write_timeout is None or write_timeout == 0:
            write_total_timeout_constant = 0
        else:
            write_total_timeout_constant = max(int(write_timeout * 1000), 1)

        timeouts = CommTimeouts(
            ReadIntervalTimeout=read_interval_timeout,
            ReadTotalTimeoutMultiplier=0,
            ReadTotalTimeoutConstant=read_total_timeout_constant,
            WriteTotalTimeoutMultiplier=0,
            WriteTotalTimeoutConstant=write_total_timeout_constant,
        )

        if self._commtimeouts == timeouts:
            return
        SetCommTimeouts(self._handle, timeouts)
        self._commtimeouts = timeouts

    def _open(self) -> None:
        """Open the serial port."""
        LOGGER.debug("Opening serial port %r", self._path)

        if self._handle is not None:
            raise ValueError("Serial port is already open")

        assert self._path is not None
        path = _normalize_windows_port_path(self._path)

        share_mode = 0 if self._exclusive else FILE_SHARE_READ | FILE_SHARE_WRITE

        try:
            self._handle = CreateFile(
                FileName=path,
                DesiredAccess=GENERIC_READ | GENERIC_WRITE,
                ShareMode=share_mode,
                SecurityAttributes=None,
                CreationDisposition=OPEN_EXISTING,
                FlagsAndAttributes=FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED,
                TemplateFile=None,
            )
        except pywintypes.error as e:
            raise OSError(e.winerror, e.strerror, path) from e

        self._overlapped_read = OVERLAPPED()
        self._overlapped_read.hEvent = CreateEvent(
            EventAttributes=None,
            bManualReset=True,
            bInitialState=False,
            Name=None,
        )
        self._overlapped_write = OVERLAPPED()
        self._overlapped_write.hEvent = CreateEvent(
            EventAttributes=None,
            bManualReset=True,
            bInitialState=False,
            Name=None,
        )

        self._auto_close = True

    @property
    def is_open(self) -> bool:
        """Check if the serial port is open."""
        return self._handle is not None

    def _configure_port(self) -> None:
        """Configure the serial port settings."""
        assert self._handle is not None

        try:
            self._apply_commtimeouts()

            # Setup buffers
            SetupComm(self._handle, self._read_buffer_size, self._write_buffer_size)

            # Clear buffers
            PurgeComm(
                self._handle,
                PURGE_TXABORT | PURGE_RXABORT | PURGE_TXCLEAR | PURGE_RXCLEAR,
            )

            # Configure DCB (Device Control Block)
            dcb = cast(Any, GetCommState(self._handle))  # TODO: fix in typeshed
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

            if self._dsrdtr:
                dcb.fDtrControl = DTR_CONTROL_HANDSHAKE
                dcb.fOutxDsrFlow = 1
            else:
                dcb.fDtrControl = DTR_CONTROL_ENABLE
                dcb.fOutxDsrFlow = 0

            dcb.fDsrSensitivity = 0
            dcb.fErrorChar = 0
            dcb.fNull = 0
            dcb.fAbortOnError = 0

            SetCommState(self._handle, dcb)

            # RTS cannot be manually set when hardware flow control is enabled
            if not self._rtscts:
                self.set_modem_pins(
                    dtr=self._rtsdtr_on_open,
                    rts=self._rtsdtr_on_open,
                )

            # Clear any errors
            ClearCommError(self._handle)
        except pywintypes.error as e:
            LOGGER.debug("Failed to configure port", exc_info=True)
            raise OSError(e.winerror, e.strerror) from e

    def fileno(self) -> int:
        """Return the file descriptor."""
        assert self._handle is not None
        return int(self._handle)

    def _close(self) -> None:
        """Close the serial port and release all handles."""
        if self._handle is not None:
            # Windows has no way to automatically do this on close, we do it manually
            if not self._rtscts:
                # RTS cannot be manually set when hardware flow control is enabled
                try:
                    self.set_modem_pins(
                        dtr=self._rtsdtr_on_close,
                        rts=self._rtsdtr_on_close,
                    )
                except OSError:
                    LOGGER.debug("Failed to set modem pins on close", exc_info=True)

            try:
                CancelIo(self._handle)
            except pywintypes.error:
                LOGGER.debug("Failed to cancel IO on close", exc_info=True)

            _safe_close_handle(self._handle)
            self._handle = None
            self._commtimeouts = None

        if self._overlapped_read is not None and self._overlapped_read.hEvent:
            _safe_close_handle(self._overlapped_read.hEvent)
        self._overlapped_read = None

        if self._overlapped_write is not None and self._overlapped_write.hEvent:
            _safe_close_handle(self._overlapped_write.hEvent)
        self._overlapped_write = None

    def _get_modem_pins(self) -> ModemPins:
        """Get the current modem control bits."""
        assert self._handle is not None

        stat = GetCommModemStatus(self._handle)
        return ModemPins(
            cts=PinState.HIGH if stat & MS_CTS_ON else PinState.LOW,
            dsr=PinState.HIGH if stat & MS_DSR_ON else PinState.LOW,
            rng=PinState.HIGH if stat & MS_RING_ON else PinState.LOW,
            car=PinState.HIGH if stat & MS_RLSD_ON else PinState.LOW,
        )

    def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        """Set the modem control bits."""
        assert self._handle is not None

        if modem_pins.rts is not PinState.UNDEFINED:
            EscapeCommFunction(  # type:ignore[call-arg]
                self._handle, (SETRTS if modem_pins.rts is PinState.HIGH else CLRRTS)
            )

        if modem_pins.dtr is not PinState.UNDEFINED:
            EscapeCommFunction(  # type:ignore[call-arg]
                self._handle, (SETDTR if modem_pins.dtr is PinState.HIGH else CLRDTR)
            )

    def num_unread_bytes(self) -> int:
        """Return the number of bytes waiting to be read."""
        assert self._handle is not None
        _flags, comstat = ClearCommError(self._handle)
        return comstat.cbInQue

    def num_unwritten_bytes(self) -> int:
        """Return the number of bytes waiting to be written."""
        assert self._handle is not None
        _flags, comstat = ClearCommError(self._handle)
        return comstat.cbOutQue

    def _reset_read_buffer(self) -> None:
        """Reset the read buffer."""
        assert self._handle is not None
        PurgeComm(self._handle, PURGE_RXABORT | PURGE_RXCLEAR)

    def _reset_write_buffer(self) -> None:
        """Reset the write buffer."""
        assert self._handle is not None
        PurgeComm(self._handle, PURGE_TXABORT | PURGE_TXCLEAR)

    def _flush(self) -> None:
        """Flush write buffers."""
        assert self._handle is not None
        FlushFileBuffers(self._handle)

    def _readinto(self, b: Buffer, *, timeout: float | None) -> int:
        """Read data into the provided bytearray."""
        assert self._overlapped_read is not None
        assert self._handle is not None

        self._apply_commtimeouts(read_timeout=timeout)
        ResetEvent(self._overlapped_read.hEvent)

        try:
            ReadFile(self._handle, b, self._overlapped_read)  # type:ignore[call-overload]
        except pywintypes.error as e:
            if e.winerror != ERROR_IO_PENDING:
                raise OSError(e.winerror, e.strerror) from e

        try:
            return GetOverlappedResult(self._handle, self._overlapped_read, True)
        except pywintypes.error as e:
            raise OSError(e.winerror, e.strerror) from e

    def _write(self, data: Buffer, *, timeout: float | None) -> int:
        """Write data to the serial port synchronously."""
        assert self._overlapped_write is not None
        assert self._handle is not None

        self._apply_commtimeouts(write_timeout=timeout)
        ResetEvent(self._overlapped_write.hEvent)

        try:
            err, _ = WriteFile(self._handle, data, self._overlapped_write)  # type:ignore[arg-type]
        except pywintypes.error as e:
            raise OSError(e.winerror, e.strerror) from e

        if err == ERROR_IO_PENDING and timeout == 0:
            # Fire-and-forget: the kernel accepted the whole write.
            return memoryview(data).nbytes

        try:
            n = GetOverlappedResult(self._handle, self._overlapped_write, True)
        except pywintypes.error as e:
            raise OSError(e.winerror, e.strerror) from e

        if timeout is not None and timeout > 0 and n != memoryview(data).nbytes:
            raise TimeoutError("Write timeout")

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
        self._open_fut: asyncio.Future[int] | None = None
        self._internal_transport: asyncio.Transport | None = None
        self._close_future: asyncio.Future[None] | None = None
        self._closing: bool = False
        self._connect_in_progress: bool = False

    def serial_close(self) -> None:
        """Close the serial port."""

        def _close_then_notify() -> None:
            assert self._serial is not None
            exc = None

            try:
                self._serial.close()
            except Exception as e:
                exc = e

            self._loop.call_soon_threadsafe(self._call_protocol_connection_lost, exc)

        self._close_future = self._loop.run_in_executor(None, _close_then_notify)

    def serial_shutdown(self, how: int) -> None:
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
        self._resolve_closed_waiter()

    def protocol_pause_writing(self) -> None:
        """Forward pause_writing to the protocol."""
        self._protocol.pause_writing()

    def protocol_resume_writing(self) -> None:
        """Forward resume_writing to the protocol."""
        self._protocol.resume_writing()

    def _maybe_resolve_closed_waiter(self) -> None:
        """Potentially resolve the closed future when the transport is closing."""
        if not self._closing:
            return
        if self._connect_in_progress:
            return
        if self._open_fut is not None:
            return
        if self._internal_transport is not None:
            return
        if self._handle is not None:
            return

        self._resolve_closed_waiter()

    def _on_cancelled_open_done(self, open_fut: asyncio.Future[int]) -> None:
        if self._open_fut is not open_fut:
            return

        self._open_fut = None

        try:
            handle = open_fut.result()
        except BaseException:
            self._maybe_resolve_closed_waiter()
            return

        _safe_close_handle(handle)
        self._maybe_resolve_closed_waiter()

    async def _open(
        self, path: str | os.PathLike[str], *, exclusive: bool = True
    ) -> None:
        """Open the serial port."""
        if self._open_fut is not None:
            raise RuntimeError("Open is already in progress")

        normalized_path = _normalize_windows_port_path(path)
        share_mode = 0 if exclusive else FILE_SHARE_READ | FILE_SHARE_WRITE

        self._open_fut = self._loop.run_in_executor(
            None,
            lambda: CreateFile(
                FileName=normalized_path,
                DesiredAccess=GENERIC_READ | GENERIC_WRITE,
                ShareMode=share_mode,
                SecurityAttributes=None,
                CreationDisposition=OPEN_EXISTING,
                FlagsAndAttributes=FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED,
                TemplateFile=None,
            ),
        )

        try:
            handle = await self._open_fut
        except asyncio.CancelledError:
            self._open_fut.add_done_callback(self._on_cancelled_open_done)
            raise
        except pywintypes.error as e:
            self._open_fut = None
            raise OSError(e.winerror, e.strerror, normalized_path) from e

        self._open_fut = None
        self._handle = handle

    async def _connect(
        self, *, path: str | None = None, **kwargs: Unpack[ConnectKwargs]
    ) -> None:
        """Connect to the serial port."""
        if self._closing:
            self._resolve_closed_waiter()
            return

        self._connect_in_progress = True

        if path is None:
            raise ValueError("A serial path is required")

        try:
            exclusive = kwargs.get("exclusive", True)
            await self._open(path, exclusive=exclusive)

            self._serial = Win32Serial(
                **kwargs,
                path=path,
                handle=self._handle,
            )
            self._extra["serial"] = self._serial

            await self._loop.run_in_executor(None, self._serial.configure_port)

            if self._closing:
                await self._loop.run_in_executor(None, self._serial.close)  # type: ignore[unreachable]
                self._serial = None
                self._handle = None
                self._resolve_closed_waiter()
                return

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
                        "pause_writing": self.protocol_pause_writing,
                        "resume_writing": self.protocol_resume_writing,
                    },
                ),
                extra=self._extra,
            )
            if self._closing:
                self._internal_transport.close()  # type: ignore[unreachable]
        except BaseException:
            if self._handle is not None:
                await self._loop.run_in_executor(None, _safe_close_handle, self._handle)

            self._serial = None
            self._handle = None
            raise
        finally:
            self._connect_in_progress = False
            self._maybe_resolve_closed_waiter()

    def get_write_buffer_size(self) -> int:
        """Return the current size of the write buffer."""
        if self._internal_transport is None:
            return 0

        return self._internal_transport.get_write_buffer_size()

    def get_write_buffer_limits(self) -> tuple[int, int]:
        """Return the write buffer limits."""
        if self._internal_transport is None:
            return (0, 0)

        return self._internal_transport.get_write_buffer_limits()

    def set_write_buffer_limits(
        self, high: int | None = None, low: int | None = None
    ) -> None:
        """Set the write buffer limits."""
        if self._internal_transport is None:
            raise RuntimeError("Transport not connected")

        self._internal_transport.set_write_buffer_limits(high=high, low=low)

    def write(self, data: bytes | bytearray | memoryview) -> None:
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
        else:
            self._maybe_resolve_closed_waiter()

    def abort(self) -> None:
        """Abort the transport immediately."""
        self._closing = True
        if self._internal_transport is not None:
            self._internal_transport.abort()
        else:
            self._maybe_resolve_closed_waiter()

    def pause_reading(self) -> None:
        """Pause reading from the transport."""
        if self._internal_transport is not None:
            self._internal_transport.pause_reading()

    def resume_reading(self) -> None:
        """Resume reading from the transport."""
        if self._internal_transport is not None:
            self._internal_transport.resume_reading()

    def set_protocol(self, protocol: asyncio.Protocol) -> None:  # type: ignore[override]
        """Set the protocol."""
        self._protocol = protocol
        if self._internal_transport is not None:
            self._internal_transport.set_protocol(protocol)

    async def flush(self) -> None:
        """Flush write buffers, waiting until all data is written."""
        assert self._serial is not None
        assert self._internal_transport is not None
        try:
            # Wait for asyncio buffer to drain
            await self._internal_transport._make_empty_waiter()  # type:ignore[attr-defined]

            # Wait for hardware buffer to flush
            await self._loop.run_in_executor(None, self._serial.flush)
        finally:
            self._internal_transport._reset_empty_waiter()  # type:ignore[attr-defined]


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
        for port in list_serial_ports_impl()
    ]


async def async_win32_list_serial_ports() -> list[SerialPortInfo]:
    """List available serial ports on Windows, async."""
    return await asyncio.to_thread(win32_list_serial_ports)


register_uri_handler(
    scheme="device://",
    unique_scheme="windows://",
    sync_cls=Win32Serial,
    async_transport_cls=Win32SerialTransport,
    list_serial_ports_func=win32_list_serial_ports,
    async_list_serial_ports_func=async_win32_list_serial_ports,
    strip_uri_scheme=True,
)
