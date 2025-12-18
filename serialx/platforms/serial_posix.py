"""POSIX serial port implementation."""

from __future__ import annotations

import array
import asyncio
import ctypes
import errno
import fcntl
import logging
import os
from pathlib import Path
import sys
import termios
import time

if sys.version_info >= (3, 11):
    from asyncio import timeout as asyncio_timeout
else:
    from async_timeout import timeout as asyncio_timeout

from typing_extensions import Buffer

from ..common import (
    BaseSerial,
    BaseSerialTransport,
    ModemPins,
    Parity,
    PinState,
    SerialPortInfo,
    StopBits,
)
from ..descriptor_transport import DescriptorTransport

LOGGER = logging.getLogger(__name__)

SYS_ROOT = Path("/sys")
DEV_ROOT = Path("/dev")

FLUSH_TIMEOUT = 10.0

# Reportedly, some drivers benefit from delaying between the `open` syscall and using
# `TCIOFLUSH`. Otherwise, the flush operation does not work reliably and stale data may
# be read.
AFTER_OPEN_DELAY = 0.01

ASYNC_LOW_LATENCY = 1 << 13
CMSPAR = 0o10000000000
TCGETS = 0x5401
TCGETS2 = 0x802C542A
TCSETS2 = 0x402C542B

TIOCGSERIAL = getattr(termios, "TIOCGSERIAL", None)
TIOCSSERIAL = getattr(termios, "TIOCSSERIAL", None)
CBAUD = getattr(termios, "CBAUD", 0o00010017)
CBAUDEX = getattr(termios, "CBAUDEX", 0o00010000)
CRTSCTS = getattr(termios, "CRTSCTS", getattr(termios, "CNEW_RTSCTS", None))

# When we need to set a non-POSIX baudrate, we set the baudrates to a known default and
# then override
NON_POSIX_FALLBACK_BAUDRATE = 115200
NON_POSIX_FALLBACK_BAUDRATE_CONST = termios.B115200

MODEM_BIT_MAPPING = {
    "le": termios.TIOCM_LE,
    "dtr": termios.TIOCM_DTR,
    "rts": termios.TIOCM_RTS,
    "st": termios.TIOCM_ST,
    "sr": termios.TIOCM_SR,
    "cts": termios.TIOCM_CTS,
    "car": termios.TIOCM_CAR,
    "rng": termios.TIOCM_RNG,
    "dsr": termios.TIOCM_DSR,
}
assert MODEM_BIT_MAPPING.keys() == ModemPins.__annotations__.keys()

POSIX_CHARACTER_SIZE_MAPPING = {
    5: termios.CS5,
    6: termios.CS6,
    7: termios.CS7,
    8: termios.CS8,
}


class TermiosStruct(ctypes.Structure):
    """The `termios` struct."""

    _fields_ = [
        ("c_iflag", ctypes.c_uint32),
        ("c_oflag", ctypes.c_uint32),
        ("c_cflag", ctypes.c_uint32),
        ("c_lflag", ctypes.c_uint32),
        ("c_line", ctypes.c_uint8),
        ("c_cc", ctypes.c_uint8 * 64),  # NCCS is usually 19 bytes, let's be safe
    ]


class Termios2SpeedStruct(ctypes.Structure):
    """The extra `c_ispeed` and `c_ospeed` members at the end of `struct termios2`."""

    _fields_ = [
        ("c_ispeed", ctypes.c_uint32),
        ("c_ospeed", ctypes.c_uint32),
    ]


def modem_pins_mask_of_value(modem_pins: ModemPins, mask: PinState) -> int:
    """Get modem bit mask for bits matching the specified value."""
    result = 0x00000000

    for name, bit in MODEM_BIT_MAPPING.items():
        value = getattr(modem_pins, name)

        if value is mask:
            result |= bit

    return result


def modem_pins_as_int(modem_pins: ModemPins) -> int:
    """Convert modem pins to integer."""
    result = 0x00000000

    for name, bit in MODEM_BIT_MAPPING.items():
        result |= bit if getattr(modem_pins, name) else 0x00000000

    return result


class PosixSerial(BaseSerial):
    """POSIX serial port implementation."""

    def __init__(
        self,
        *args,
        fileno: int | None = None,
        low_latency: bool = True,
        **kwargs,
    ):
        """Initialize POSIX serial port."""
        super().__init__(*args, **kwargs)
        self._fileno: int | None = fileno
        self._low_latency: bool = low_latency

    def open(self) -> None:
        """Open the serial port."""
        LOGGER.debug("Opening serial port %r", self._path)

        if self._fileno is not None:
            raise ValueError("Serial port is already open")

        self._fileno = os.open(self._path, os.O_RDWR | os.O_NOCTTY)
        self._auto_close = True

        if self._exclusive:
            self._lock()

        time.sleep(AFTER_OPEN_DELAY)

    def _lock(self) -> None:
        """Lock the serial port for exclusive access."""
        LOGGER.debug("Locking serial port %r", self._path)

        assert self._fileno is not None

        try:
            fcntl.flock(self._fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OSError(
                errno.EBUSY,
                f"Serial port {self._path!r} is already locked by another process",
            ) from exc

    def _unlock(self) -> None:
        """Unlock the serial port."""
        LOGGER.debug("Unlocking serial port %r", self._path)

        assert self._fileno is not None
        fcntl.flock(self._fileno, fcntl.LOCK_UN)

    def _set_non_posix_baudrate(self, baudrate: int) -> None:
        """Set the baudrate of the serial port, must be called after `tcsetattr`."""
        assert self._fileno is not None

        # The termios2 struct is going to be smaller than the sum of these two objects
        buffer = bytearray(
            ctypes.sizeof(TermiosStruct) + ctypes.sizeof(Termios2SpeedStruct)
        )
        fcntl.ioctl(self._fileno, TCGETS2, buffer)

        # The POSIX baudrates are stored in the lower bits of `c_cflag`. We clear them.
        termios_struct = TermiosStruct.from_buffer(buffer)
        termios_struct.c_cflag &= ~CBAUD
        termios_struct.c_cflag |= CBAUDEX

        # `termios2` extends `termios` with two extra fields. The problem is that these
        # fields appear *after* the `c_cc` array, which has a length defined by `NCCS`,
        # a constant that we do not have access to. We overcome this by searching for
        # the speed fields directly, since we set them to a known value earlier.
        try:
            temp_speed_buffer = bytearray(ctypes.sizeof(Termios2SpeedStruct))
            temp_speed_struct = Termios2SpeedStruct.from_buffer(temp_speed_buffer)
            temp_speed_struct.c_ispeed = NON_POSIX_FALLBACK_BAUDRATE
            temp_speed_struct.c_ospeed = NON_POSIX_FALLBACK_BAUDRATE

            offset = buffer.index(temp_speed_buffer)
        except ValueError as exc:
            raise RuntimeError(
                f"Could not determine offset of termios2 speed fields: {buffer.hex()}"
            ) from exc

        termios2_speed_struct = Termios2SpeedStruct.from_buffer(buffer, offset)
        termios2_speed_struct.c_ispeed = self._baudrate
        termios2_speed_struct.c_ospeed = self._baudrate

        # The ctypes structures mutate the buffer in place
        LOGGER.debug(
            "Writing termios2 struct (c_ispeed offset %d bytes): %r",
            offset,
            buffer.hex(),
        )
        fcntl.ioctl(self._fileno, TCSETS2, buffer)

    def configure_port(self) -> None:  # noqa: C901
        """Configure the serial port settings."""
        LOGGER.debug("Configuring serial port %r", self._path)

        if self._fileno is None:
            raise ValueError("Cannot configure, serial port is not open")

        cflag = 0x00000000

        # Enable receiver
        cflag |= termios.CREAD

        # Ignore modem control lines
        cflag |= termios.CLOCAL

        # Lower modem control lines after last process closes the device (hang up)
        if self._rtsdtr_on_close is PinState.UNDEFINED:
            pass
        elif self._rtsdtr_on_close is PinState.HIGH:
            LOGGER.warning("POSIX only supports setting RTS/DTR to LOW on close")
        else:
            cflag |= termios.HUPCL

        # Character size
        if self._byte_size == 5:
            cflag |= termios.CS5
        elif self._byte_size == 6:
            cflag |= termios.CS6
        elif self._byte_size == 7:
            cflag |= termios.CS7
        elif self._byte_size == 8:
            cflag |= termios.CS8
        else:
            raise ValueError(
                f"Unsupported byte size {self._byte_size}, must be 5, 6, 7, or 8"
            )

        # Parity
        if self._parity == Parity.NONE:
            pass
        elif self._parity == Parity.EVEN:
            cflag |= termios.PARENB
        elif self._parity == Parity.ODD:
            cflag |= termios.PARENB | termios.PARODD
        elif self._parity == Parity.MARK:
            cflag |= termios.PARENB | termios.PARODD | CMSPAR
        elif self._parity == Parity.SPACE:
            cflag |= termios.PARENB | CMSPAR

        # Stop bits
        if self._stopbits == StopBits.TWO:
            cflag |= termios.CSTOPB
        elif self._stopbits == StopBits.ONE_POINT_FIVE:
            LOGGER.warning("1.5 stop bits not supported on POSIX, using 1 stop bit")
        elif self._stopbits == StopBits.ONE:
            pass

        # Hardware flow control
        if self._rtscts:
            if CRTSCTS is None:
                LOGGER.warning("RTS/CTS flow control not supported on this platform")
            else:
                cflag |= CRTSCTS

        iflag = 0x00000000

        # Software flow control
        if self._xonxoff:
            iflag |= termios.IXON | termios.IXOFF | termios.IXANY

        oflag = 0x00000000
        lflag = 0x00000000

        # Only emit reads if VMIN characters have been read, after no more data comes in
        # for VTIME seconds
        vmin = self._buffer_character_count
        vtime = int(self._buffer_burst_timeout * 10)

        if not 0 <= vmin <= 255:
            raise ValueError(
                f"VMIN must be in range 0-255 (buffer_character_count={self._buffer_character_count})"
            )

        if not 0 <= vtime <= 255:
            raise ValueError(
                f"VTIME must be in range 0-255 (buffer_burst_timeout={self._buffer_burst_timeout})"
            )

        try:
            # Set baudrate
            ispeed = getattr(termios, f"B{self._baudrate}")
            ospeed = getattr(termios, f"B{self._baudrate}")
            non_posix_baudrate = False
        except AttributeError:
            # Non-POSIX baudrate, use defaults for `tcsetattr` and then override
            ispeed = NON_POSIX_FALLBACK_BAUDRATE_CONST
            ospeed = NON_POSIX_FALLBACK_BAUDRATE_CONST
            non_posix_baudrate = True

        (
            _iflag,
            _oflag,
            _cflag,
            _lflag,
            _ispeed,
            _ospeed,
            cc,
        ) = termios.tcgetattr(self._fileno)

        cc[termios.VMIN] = vmin
        cc[termios.VTIME] = vtime

        LOGGER.debug(
            "Configuring serial port: %r",
            [iflag, oflag, cflag, lflag, ispeed, ospeed, cc],
        )

        # Finally, set up the serial port
        termios.tcsetattr(
            self._fileno,
            termios.TCSANOW,  # TODO: should we use TCSADRAIN or TCSAFLUSH instead?
            [iflag, oflag, cflag, lflag, ispeed, ospeed, cc],
        )

        if non_posix_baudrate:
            LOGGER.debug("Setting non-POSIX baudrate %d", self._baudrate)
            self._set_non_posix_baudrate(self._baudrate)

        if TIOCSSERIAL is not None:
            try:
                self._set_low_latency(self._low_latency)
            except OSError as exc:
                if exc.errno == errno.ENOTTY:
                    LOGGER.debug("Device is not a serial port, cannot set low latency")
                else:
                    raise

        self.set_modem_pins(dtr=self._rtsdtr_on_open, rts=self._rtsdtr_on_open)

        # Flush input and output buffers to discard stale data
        termios.tcflush(self._fileno, termios.TCIOFLUSH)

    def _set_low_latency(self, value: bool) -> None:
        """Set low latency mode."""
        assert self._fileno is not None
        assert TIOCGSERIAL is not None
        assert TIOCSSERIAL is not None

        LOGGER.debug("Setting low latency mode: %r", value)

        buffer = array.array("i", [0x00000000] * 19 * 8)

        fcntl.ioctl(self._fileno, TIOCGSERIAL, buffer)

        if self._low_latency:
            buffer[4] |= ASYNC_LOW_LATENCY
        else:
            buffer[4] &= ~ASYNC_LOW_LATENCY

        fcntl.ioctl(self._fileno, TIOCSSERIAL, buffer)

    def _get_modem_pins(self) -> ModemPins:
        """Get current modem control bits."""
        assert self._fileno is not None

        # A `bytearray` is critical here: `bytes` will not be mutated
        buffer = bytearray((0x00000000).to_bytes(4, "little"))

        try:
            fcntl.ioctl(self._fileno, termios.TIOCMGET, buffer)
        except OSError as exc:
            if exc.errno == errno.ENOTTY:
                LOGGER.debug("Device is not a serial port, cannot get modem pins")
                return ModemPins()

        n = int.from_bytes(buffer, "little")
        return ModemPins(
            **{
                name: PinState.HIGH if n & bit else PinState.LOW
                for name, bit in MODEM_BIT_MAPPING.items()
            }
        )

    def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        """Set modem control bits."""
        assert self._fileno is not None

        LOGGER.debug("Setting modem pins: %r", modem_pins)

        all_pins_set = all(
            getattr(modem_pins, name) is not PinState.UNDEFINED
            for name in MODEM_BIT_MAPPING
        )

        try:
            if all_pins_set:
                value = modem_pins_as_int(modem_pins)
                LOGGER.debug("Setting all with TIOCMSET: 0x%08X", value)
                fcntl.ioctl(self._fileno, termios.TIOCMSET, value.to_bytes(4, "little"))
            else:
                to_set = modem_pins_mask_of_value(modem_pins, PinState.HIGH)
                to_clear = modem_pins_mask_of_value(modem_pins, PinState.LOW)

                if to_set:
                    LOGGER.debug("Setting TIOCMBIS: 0x%08X", to_set)
                    fcntl.ioctl(
                        self._fileno, termios.TIOCMBIS, to_set.to_bytes(4, "little")
                    )

                if to_clear:
                    LOGGER.debug("TIOCMBIC: 0x%08X", to_clear)
                    fcntl.ioctl(
                        self._fileno, termios.TIOCMBIC, to_clear.to_bytes(4, "little")
                    )
        except OSError as exc:
            if exc.errno == errno.ENOTTY:
                LOGGER.debug("Device is not a serial port, cannot set modem pins")

    def flush(self) -> None:
        """Flush write buffers, waiting until all data is written."""
        assert self._fileno is not None
        LOGGER.debug("Flushing file descriptor %r", self._fileno)
        termios.tcdrain(self._fileno)

    def close(self) -> None:
        """Close the serial port."""
        if self._fileno is not None:
            if self._exclusive:
                self._unlock()

            os.close(self._fileno)
            self._fileno = None

    def fileno(self) -> int:
        """Get the file descriptor number."""
        assert self._fileno is not None
        return self._fileno

    # `io.IOBase` implements `read`, `readline`, using `readinto`
    if sys.version_info >= (3, 14):

        def readinto(self, b: Buffer) -> int:
            """Read bytes from serial port into buffer."""
            n = os.readinto(self._fileno, b)
            LOGGER.debug("Read %d bytes", n)

            return n

    else:

        def readinto(self, b: Buffer) -> int:
            """Read bytes from serial port into buffer."""
            assert self._fileno is not None

            m = memoryview(b).cast("B")
            size = len(m)
            LOGGER.debug("Reading up to %d bytes", size)

            chunk = os.read(self._fileno, size)

            n = len(chunk)
            m[:n] = chunk
            LOGGER.debug("Read %d bytes: %r", n, chunk)

            return n

    def write(self, data: Buffer) -> int:
        """Write bytes to serial port."""
        LOGGER.debug("Writing %d bytes: %r", len(data), data)  # type: ignore[arg-type]
        assert self._fileno is not None
        return os.write(self._fileno, data)  # type: ignore[arg-type]


class PosixSerialTransport(DescriptorTransport, BaseSerialTransport):
    """POSIX serial port transport using asyncio."""

    _serial_cls = PosixSerial

    async def _connect(self, *, path: os.PathLike, **kwargs) -> None:  # type: ignore[override]
        """Connect to serial port."""
        await super()._open(path)

        self._serial = self._serial_cls(
            **kwargs,
            path=path,
            # `DescriptorTransport` opened the port
            fileno=self._fileno,
            # Nonblocking mode
            buffer_character_count=0,
            buffer_burst_timeout=0,
        )
        self._extra["serial"] = self._serial

        await asyncio.sleep(AFTER_OPEN_DELAY)

        await self._loop.run_in_executor(None, self._serial.configure_port)
        await super()._connect()
        self._protocol.connection_made(self)

    async def flush(self) -> None:
        """Flush write buffers, waiting until all data is written."""
        assert self._serial is not None

        try:
            # Wait for internal buffer to drain
            await self._make_empty_waiter()

            # Wait for hardware buffer to flush (with timeout)
            async with asyncio_timeout(FLUSH_TIMEOUT):
                await self._loop.run_in_executor(None, self._serial.flush)
        finally:
            self._reset_empty_waiter()


def posix_list_serial_ports() -> list[SerialPortInfo]:
    """List serial ports on Linux."""
    by_id_symlinks = {}
    by_id_path = DEV_ROOT / "serial/by-id"

    if by_id_path.exists():
        for symlink in by_id_path.iterdir():
            by_id_symlinks[symlink.resolve()] = symlink

    results = []

    for path in (SYS_ROOT / "class/tty").iterdir():
        if not path.name.startswith("tty"):
            continue

        tty_device = path / "device"
        if not (tty_device / "driver").exists():
            continue

        device = DEV_ROOT / path.name
        resolved = tty_device.resolve()
        subsystem = (resolved / "subsystem").resolve().name
        unique_device = by_id_symlinks.get(device, device)

        if subsystem == "usb-serial":
            # USB-serial chips
            usb_interface = resolved.parent
            usb_device = usb_interface.parent
            interface_file = usb_interface / "interface"
            info = SerialPortInfo(
                device=unique_device,
                resolved_device=device,
                vid=int((usb_device / "idVendor").read_text(), 16),
                pid=int((usb_device / "idProduct").read_text(), 16),
                serial_number=(usb_device / "serial").read_text()[:-1],
                manufacturer=(usb_device / "manufacturer").read_text()[:-1],
                product=(usb_device / "product").read_text()[:-1],
                bcd_device=int((usb_device / "bcdDevice").read_text(), 16),
                interface_description=(
                    interface_file.read_text()[:-1] if interface_file.exists() else None
                ),
                interface_num=int((usb_interface / "bInterfaceNumber").read_text(), 16),
            )
        elif subsystem == "usb":
            # CDC ACM devices
            usb_interface = resolved
            usb_device = usb_interface.parent
            interface_file = usb_interface / "interface"
            info = SerialPortInfo(
                device=unique_device,
                resolved_device=device,
                vid=int((usb_device / "idVendor").read_text(), 16),
                pid=int((usb_device / "idProduct").read_text(), 16),
                serial_number=(usb_device / "serial").read_text()[:-1],
                manufacturer=(usb_device / "manufacturer").read_text()[:-1],
                product=(usb_device / "product").read_text()[:-1],
                bcd_device=int((usb_device / "bcdDevice").read_text(), 16),
                interface_description=(
                    interface_file.read_text()[:-1] if interface_file.exists() else None
                ),
                interface_num=int((usb_interface / "bInterfaceNumber").read_text(), 16),
            )
        elif subsystem == "serial-base":
            # Native serial ports
            info = SerialPortInfo(
                device=unique_device,
                resolved_device=device,
                vid=None,
                pid=None,
                serial_number=None,
                manufacturer=None,
                product=None,
                bcd_device=None,
                interface_description=None,
                interface_num=None,
            )
        else:
            LOGGER.warning(
                "Unknown serial device subsystem %r for device %r",
                subsystem,
                device,
            )

        results.append(info)

    return results
