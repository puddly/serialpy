"""Linux serial port implementation."""

from __future__ import annotations

import array
import ctypes
import errno
import fcntl
import logging
from pathlib import Path
import termios

from .. import UnsupportedSetting
from ..common import Parity, SerialPortInfo
from .serial_posix import PosixSerial, PosixSerialTransport

LOGGER = logging.getLogger(__name__)

SYS_ROOT = Path("/sys")
DEV_ROOT = Path("/dev")

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


class LinuxSerial(PosixSerial):
    """POSIX serial port implementation."""

    def __init__(
        self,
        *args,
        low_latency: bool = True,
        **kwargs,
    ):
        """Initialize POSIX serial port."""
        super().__init__(*args, **kwargs)
        self._low_latency = low_latency

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
        termios2_speed_struct.c_ispeed = baudrate
        termios2_speed_struct.c_ospeed = baudrate

        # The ctypes structures mutate the buffer in place
        LOGGER.debug(
            "Writing termios2 struct (c_ispeed offset %d bytes): %r",
            offset,
            buffer.hex(),
        )
        fcntl.ioctl(self._fileno, TCSETS2, buffer)

    def _build_parity_flags(self) -> int:
        if self._parity == Parity.NONE:
            return 0
        elif self._parity == Parity.EVEN:
            return termios.PARENB
        elif self._parity == Parity.ODD:
            return termios.PARENB | termios.PARODD
        elif self._parity == Parity.MARK:
            return termios.PARENB | termios.PARODD | CMSPAR
        elif self._parity == Parity.SPACE:
            return termios.PARENB | CMSPAR
        else:
            raise UnsupportedSetting(f"Unsupported parity {self._parity}")

    def _build_flow_control_flags(self) -> tuple[int, int]:
        iflag = 0x00000000
        cflag = 0x00000000

        if self._xonxoff:
            iflag |= termios.IXON | termios.IXOFF | termios.IXANY

        if self._rtscts:
            if CRTSCTS is None:
                raise UnsupportedSetting(
                    "RTS/CTS flow control not supported on this platform"
                )
            else:
                cflag |= CRTSCTS

        return (iflag, cflag)

    @property
    def _has_non_posix_baudrate(self) -> bool:
        return hasattr(termios, f"B{self._baudrate}")

    def _build_ispeed(self) -> int:
        return (
            NON_POSIX_FALLBACK_BAUDRATE_CONST
            if self._has_non_posix_baudrate
            else getattr(termios, f"B{self._baudrate}")
        )

    def _build_ospeed(self) -> int:
        return (
            NON_POSIX_FALLBACK_BAUDRATE_CONST
            if self._has_non_posix_baudrate
            else getattr(termios, f"B{self._baudrate}")
        )

    def _after_configure_port(self) -> None:  # noqa: C901
        if self._has_non_posix_baudrate:
            LOGGER.debug("Setting non-POSIX baudrate %d", self._baudrate)
            self._set_non_posix_baudrate(self._baudrate)

        if TIOCSSERIAL is not None:
            try:
                self._set_low_latency(self._low_latency)
            except OSError as exc:
                if exc.errno in (errno.ENOTTY, errno.EOPNOTSUPP):
                    LOGGER.debug("Device does not support setting low latency")
                else:
                    raise

        self.set_modem_pins(dtr=self._rtsdtr_on_open, rts=self._rtsdtr_on_open)

        # Flush input and output buffers to discard stale data
        assert self._fileno is not None
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


class LinuxSerialTransport(PosixSerialTransport):
    """POSIX serial port transport using asyncio."""

    _serial_cls = LinuxSerial


def linux_list_serial_ports() -> list[SerialPortInfo]:
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
