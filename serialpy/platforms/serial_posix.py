"""POSIX serial port implementation."""

from __future__ import annotations

import array
import fcntl
import logging
import os
import sys
import termios
from typing import Literal

from typing_extensions import Buffer

from ..common import BaseSerial, BaseSerialTransport, ModemBits, Parity, StopBits
from ..descriptor_transport import DescriptorTransport

LOGGER = logging.getLogger(__name__)

ASYNC_LOW_LATENCY = 1 << 13
CMSPAR = 0o10000000000
FLUSH_TIMEOUT = 10.0

TCGETS2 = 0x802C542A
TCSETS2 = 0x402C542B

TIOCGSERIAL = getattr(termios, "TIOCGSERIAL", None)
TIOCSSERIAL = getattr(termios, "TIOCSSERIAL", None)
CBAUD = getattr(termios, "CBAUD", 0o00010017)
CBAUDEX = getattr(termios, "CBAUDEX", 0o00010000)
CRTSCTS = getattr(termios, "CRTSCTS", getattr(termios, "CNEW_RTSCTS", None))

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
assert MODEM_BIT_MAPPING.keys() == ModemBits.__annotations__.keys()

POSIX_CHARACTER_SIZE_MAPPING = {
    5: termios.CS5,
    6: termios.CS6,
    7: termios.CS7,
    8: termios.CS8,
}


def modem_bits_mask_of_value(
    modem_bits: ModemBits, mask: Literal[True, False, None]
) -> int:
    """Get modem bit mask for bits matching the specified value."""
    result = 0x00000000

    for name, bit in MODEM_BIT_MAPPING.items():
        value = getattr(modem_bits, name)

        if value == mask:
            result |= bit

    return result


def modem_bits_as_int(modem_bits: ModemBits) -> int:
    """Convert modem bits to integer."""
    result = 0x00000000

    for name, bit in MODEM_BIT_MAPPING.items():
        result |= bit if getattr(modem_bits, name) else 0x00000000

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
        self._low_latency = low_latency

    def open(self) -> None:
        """Open the serial port."""
        LOGGER.debug("Opening serial port %r", self._path)

        if self._fileno is not None:
            raise ValueError("Serial port is already open")

        self._fileno = os.open(self._path, os.O_RDWR | os.O_NOCTTY)
        self._auto_close = True

        if self._exclusive:
            self._lock()

    def _lock(self) -> None:
        """Lock the serial port for exclusive access."""
        LOGGER.debug("Locking serial port %r", self._path)

        assert self._fileno is not None
        fcntl.flock(self._fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self) -> None:
        """Unlock the serial port."""
        LOGGER.debug("Unlocking serial port %r", self._path)

        assert self._fileno is not None
        fcntl.flock(self._fileno, fcntl.LOCK_UN)

    def _set_non_posix_baudrate(self, baudrate: int) -> None:
        """Set the baudrate of the serial port."""
        assert self._fileno is not None

        buffer = array.array("i", [0x00000000] * 64)
        fcntl.ioctl(self._fileno, TCGETS2, buffer)
        buffer[2] &= ~CBAUD  # c_cflag
        buffer[2] |= CBAUDEX

        buffer[9] = self._baudrate  # c_ispeed
        buffer[10] = self._baudrate  # c_ospeed

        fcntl.ioctl(self._fileno, TCSETS2, buffer)

    def configure_port(self) -> None:
        """Configure the serial port settings."""
        LOGGER.debug("Configuring serial port %r", self._path)

        if self._fileno is None:
            raise ValueError("Cannot configure, serial port is not open")

        (
            iflag,
            oflag,
            cflag,
            lflag,
            ispeed,
            ospeed,
            cc,
        ) = termios.tcgetattr(self._fileno)

        # Software flow control
        if self._xonxoff:
            iflag |= termios.IXON | termios.IXOFF | termios.IXANY
        else:
            iflag &= ~(termios.IXON | termios.IXOFF | termios.IXANY)

        # Disable interpretation of special characters
        iflag &= ~(
            termios.IGNBRK
            | termios.BRKINT
            | termios.PARMRK
            | termios.INPCK
            | termios.ISTRIP
            | termios.INLCR
            | termios.IGNCR
            | termios.ICRNL
            | termios.IXON
        )

        # Disable output character processing and mapping
        oflag &= ~(termios.OPOST | termios.ONLCR | termios.OCRNL)

        # Allow reads
        cflag |= termios.CREAD

        # Disable modem-specific signal lines
        cflag |= termios.CLOCAL

        if self._parity == Parity.NONE:
            cflag &= ~(termios.PARENB | termios.PARODD | CMSPAR)
        elif self._parity == Parity.EVEN:
            cflag |= termios.PARENB
            cflag &= ~(termios.PARODD | CMSPAR)
        elif self._parity == Parity.ODD:
            cflag |= termios.PARENB | termios.PARODD
            cflag &= ~CMSPAR
        elif self._parity == Parity.MARK:
            cflag |= termios.PARENB | termios.PARODD | CMSPAR
        elif self._parity == Parity.SPACE:
            cflag |= termios.PARENB
            cflag &= ~(termios.PARODD | CMSPAR)

        # Stop bits
        if self._stopbits == StopBits.TWO:
            cflag |= termios.CSTOPB
        elif self._stopbits == StopBits.ONE_POINT_FIVE:
            LOGGER.warning("1.5 stop bits not supported on POSIX, using 1 stop bit")
            cflag &= ~termios.CSTOPB
        elif self._stopbits == StopBits.ONE:
            cflag &= ~termios.CSTOPB

        cflag &= ~termios.CSIZE
        cflag |= POSIX_CHARACTER_SIZE_MAPPING[self._byte_size]

        # Hardware flow control
        if CRTSCTS is not None:
            if self._rtscts:
                cflag |= CRTSCTS
            else:
                cflag &= ~CRTSCTS

        # Disable canonical mode (newlines)
        lflag &= ~termios.ICANON

        # Disable echo
        lflag &= ~(termios.ECHO | termios.ECHOE | termios.ECHONL)

        # Disable interpretation of special characters
        lflag &= ~termios.ISIG

        # Disable implementation-defined input processing
        lflag &= ~termios.IEXTEN

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

        cc[termios.VMIN] = vmin
        cc[termios.VTIME] = vtime

        try:
            # Set baudrate
            ispeed = getattr(termios, f"B{self._baudrate}")
            ospeed = getattr(termios, f"B{self._baudrate}")
            non_posix_baudrate = False
        except AttributeError:
            # Non-POSIX baudrate, use defaults for `tcsetattr` and then override
            ispeed = termios.B115200
            ospeed = termios.B115200
            non_posix_baudrate = True

        termios.tcsetattr(
            self._fileno,
            termios.TCSANOW,  # TODO: should we use TCSADRAIN or TCSAFLUSH instead?
            [iflag, oflag, cflag, lflag, ispeed, ospeed, cc],
        )

        if non_posix_baudrate:
            LOGGER.debug("Setting non-POSIX baudrate %d", self._baudrate)
            self._set_non_posix_baudrate(self._baudrate)

        if TIOCSSERIAL is not None:
            assert TIOCGSERIAL is not None
            buffer = array.array("i", [0x00000000] * 19 * 8)
            fcntl.ioctl(self._fileno, TIOCGSERIAL, buffer)

            LOGGER.debug("Read low latency %r", buffer)

            if self._low_latency:
                buffer[4] |= ASYNC_LOW_LATENCY
            else:
                buffer[4] &= ~ASYNC_LOW_LATENCY

            LOGGER.debug("Writing low latency %r", buffer)
            fcntl.ioctl(self._fileno, TIOCSSERIAL, buffer)

    def get_modem_bits(self) -> ModemBits:
        """Get current modem control bits."""
        assert self._fileno is not None

        # A `bytearray` is critical here: `bytes` will not be mutated
        buffer = bytearray((0x00000000).to_bytes(4, "little"))
        fcntl.ioctl(self._fileno, termios.TIOCMGET, buffer)

        n = int.from_bytes(buffer, "little")
        return ModemBits(
            **{name: bool(n & bit) for name, bit in MODEM_BIT_MAPPING.items()}
        )

    def set_modem_bits(self, modem_bits: ModemBits) -> None:
        """Set modem control bits."""
        assert self._fileno is not None

        all_bits_set = all(
            getattr(modem_bits, name) is not None for name in MODEM_BIT_MAPPING
        )

        if all_bits_set:
            value = modem_bits_as_int(modem_bits)
            LOGGER.debug("Setting all modem bits: 0x%08X", value)
            fcntl.ioctl(self._fileno, termios.TIOCMSET, value.to_bytes(4, "little"))
        else:
            to_set = modem_bits_mask_of_value(modem_bits, True)
            to_clear = modem_bits_mask_of_value(modem_bits, False)

            if to_set:
                LOGGER.debug("Setting modem bits: 0x%08X", to_set)
                fcntl.ioctl(
                    self._fileno, termios.TIOCMBIS, to_set.to_bytes(4, "little")
                )

            if to_clear:
                LOGGER.debug("Clearing modem bits: 0x%08X", to_clear)
                fcntl.ioctl(
                    self._fileno, termios.TIOCMBIC, to_clear.to_bytes(4, "little")
                )

    def flush(self) -> None:
        """Flush write buffers, waiting until all data is written."""
        assert self._fileno is not None
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
            await self._loop.run_in_executor(None, self._serial.flush)
        finally:
            self._reset_empty_waiter()
