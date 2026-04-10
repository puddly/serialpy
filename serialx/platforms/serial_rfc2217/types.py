"""RFC 2217 (Telnet Com Port Control) implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
import dataclasses
from enum import IntEnum, IntFlag
import logging
from typing import ClassVar

from typing_extensions import Self

LOGGER = logging.getLogger(__name__)


class TelnetOption(IntEnum):
    """Telnet option codes."""

    BINARY = 0
    ECHO = 1
    SUPPRESS_GO_AHEAD = 3
    COM_PORT_OPTION = 44

    @classmethod
    def _missing_(cls, value: object) -> Self:
        """Allow unknown option codes to pass through as int values."""
        assert isinstance(value, int)
        obj = int.__new__(cls, value)
        obj._name_ = f"UNKNOWN_{value}"
        obj._value_ = value
        return obj


class TelnetCmdId(IntEnum):
    """Telnet command bytes (follow IAC)."""

    SE = 240
    NOP = 241
    DM = 242
    BRK = 243
    IP = 244
    AO = 245
    AYT = 246
    EC = 247
    EL = 248
    GA = 249
    SB = 250
    WILL = 251
    WONT = 252  # codespell:ignore wont
    DO = 253
    DONT = 254
    IAC = 255


class Rfc2217CmdId(IntEnum):
    """RFC 2217 subnegotiation command codes (client to server)."""

    SIGNATURE = 0
    SET_BAUDRATE = 1
    SET_DATASIZE = 2
    SET_PARITY = 3
    SET_STOPSIZE = 4
    SET_CONTROL = 5
    NOTIFY_LINESTATE = 6
    NOTIFY_MODEMSTATE = 7
    FLOWCONTROL_SUSPEND = 8
    FLOWCONTROL_RESUME = 9
    SET_LINESTATE_MASK = 10
    SET_MODEMSTATE_MASK = 11
    PURGE_DATA = 12


class ControlCmdId(IntEnum):
    """Values for SET_CONTROL command."""

    # Outbound/Both flow control
    REQ_FLOW_CONTROL = 0
    USE_NO_FLOW_CONTROL = 1
    USE_XON_XOFF = 2
    USE_HARDWARE = 3

    # Break
    REQ_BREAK_STATE = 4
    SET_BREAK_ON = 5
    SET_BREAK_OFF = 6

    # DTR
    REQ_DTR_STATE = 7
    SET_DTR_ON = 8
    SET_DTR_OFF = 9

    # RTS
    REQ_RTS_STATE = 10
    SET_RTS_ON = 11
    SET_RTS_OFF = 12

    # Inbound flow control
    REQ_FLOW_CONTROL_IN = 13
    USE_NO_FLOW_CONTROL_IN = 14
    USE_XON_XOFF_IN = 15
    USE_HARDWARE_IN = 16
    USE_DCD_FLOW_CONTROL = 17
    USE_DTR_FLOW_CONTROL_IN = 18
    USE_DSR_FLOW_CONTROL = 19


class Rfc2217Parity(IntEnum):
    """RFC 2217 parity values for SET_PARITY."""

    REQUEST = 0
    NONE = 1
    ODD = 2
    EVEN = 3
    MARK = 4
    SPACE = 5


class Rfc2217StopSize(IntEnum):
    """RFC 2217 stop bit values for SET_STOPSIZE."""

    REQUEST = 0
    ONE = 1
    TWO = 2
    ONE_POINT_FIVE = 3


class LineStateFlag(IntFlag):
    """Bitmask for NOTIFY_LINESTATE and SET_LINESTATE_MASK."""

    DATA_READY = 1
    OVERRUN_ERROR = 2
    PARITY_ERROR = 4
    FRAMING_ERROR = 8
    BREAK_DETECT = 16
    TRANSFER_HOLDING_REG_EMPTY = 32
    TRANSFER_SHIFT_REG_EMPTY = 64
    TIMEOUT_ERROR = 128


class ModemStateFlag(IntFlag):
    """Bitmask for NOTIFY_MODEMSTATE and SET_MODEMSTATE_MASK."""

    DELTA_CTS = 1
    DELTA_DSR = 2
    TRAILING_EDGE_RI = 4
    DELTA_RLSD = 8
    CTS = 16
    DSR = 32
    RI = 64
    RLSD = 128  # Carrier Detect


class PurgeDataValue(IntEnum):
    """Values for PURGE_DATA command."""

    RECEIVE = 1
    TRANSMIT = 2
    BOTH = 3


class BaseCommand(ABC):
    """Base class for commands."""

    CMD_ID: ClassVar[int]

    @abstractmethod
    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Telnet-level commands (IAC <cmd> <option>)
# ---------------------------------------------------------------------------


class TelnetCommand(BaseCommand):
    """Base class for telnet commands."""

    CMD_ID: ClassVar[TelnetCmdId]


@dataclasses.dataclass(frozen=True, kw_only=True)
class TelnetOptionCommand(TelnetCommand, ABC):
    """Base class for telnet option negotiation commands."""

    option: TelnetOption

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return bytes([self.option])

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(option=TelnetOption(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class WillCmd(TelnetOptionCommand):
    """IAC WILL <option> — offer to perform an option."""

    CMD_ID: ClassVar[TelnetCmdId] = TelnetCmdId.WILL


@dataclasses.dataclass(frozen=True, kw_only=True)
class WontCmd(TelnetOptionCommand):
    """IAC WONT <option> — refuse to perform an option."""  # codespell:ignore wont

    CMD_ID: ClassVar[TelnetCmdId] = TelnetCmdId.WONT  # codespell:ignore wont


@dataclasses.dataclass(frozen=True, kw_only=True)
class DoCmd(TelnetOptionCommand):
    """IAC DO <option> — request the other side perform an option."""

    CMD_ID: ClassVar[TelnetCmdId] = TelnetCmdId.DO


@dataclasses.dataclass(frozen=True, kw_only=True)
class DontCmd(TelnetOptionCommand):
    """IAC DONT <option> — demand the other side stop performing an option."""

    CMD_ID: ClassVar[TelnetCmdId] = TelnetCmdId.DONT


# ---------------------------------------------------------------------------
# RFC 2217 subnegotiation commands (IAC SB 44 <cmd> <payload> IAC SE)
# ---------------------------------------------------------------------------


class Rfc2217Command(BaseCommand):
    """Base class for RFC 2217 subnegotiation commands."""

    CMD_ID: ClassVar[Rfc2217CmdId]


@dataclasses.dataclass(frozen=True, kw_only=True)
class SignatureCmd(Rfc2217Command):
    """Exchange signature/identity strings. Empty = request."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.SIGNATURE
    signature: bytes = b""

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return self.signature

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(signature=payload)


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetBaudrateCmd(Rfc2217Command):
    """Set baud rate. 0 = query current value."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.SET_BAUDRATE
    baudrate: int

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return self.baudrate.to_bytes(4, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(baudrate=int.from_bytes(payload, "big"))


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetDatasizeCmd(Rfc2217Command):
    """Set data bit size. 0 = query, 5-8 = actual size."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.SET_DATASIZE
    size: int

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return self.size.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(size=payload[0])


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetParityCmd(Rfc2217Command):
    """Set parity."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.SET_PARITY
    parity: Rfc2217Parity

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return self.parity.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(parity=Rfc2217Parity(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetStopsizeCmd(Rfc2217Command):
    """Set stop bits."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.SET_STOPSIZE
    size: Rfc2217StopSize

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return self.size.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(size=Rfc2217StopSize(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetControlCmd(Rfc2217Command):
    """Set flow control, break, DTR, or RTS."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.SET_CONTROL
    control: ControlCmdId

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return self.control.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(control=ControlCmdId(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class NotifyLinestateCmd(Rfc2217Command):
    """Server notification of UART line state change."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.NOTIFY_LINESTATE
    linestate: LineStateFlag

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return self.linestate.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(linestate=LineStateFlag(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class NotifyModemstateCmd(Rfc2217Command):
    """Server notification of modem state change."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.NOTIFY_MODEMSTATE
    modemstate: ModemStateFlag

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return self.modemstate.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(modemstate=ModemStateFlag(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class FlowcontrolSuspendCmd(Rfc2217Command):
    """Request the receiver suspend transmission."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.FLOWCONTROL_SUSPEND

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return b""

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls()


@dataclasses.dataclass(frozen=True, kw_only=True)
class FlowcontrolResumeCmd(Rfc2217Command):
    """Request the receiver resume transmission."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.FLOWCONTROL_RESUME

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return b""

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls()


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetLinestateMaskCmd(Rfc2217Command):
    """Set which line state changes trigger NOTIFY_LINESTATE. Default: 0."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.SET_LINESTATE_MASK
    mask: LineStateFlag

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return self.mask.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(mask=LineStateFlag(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetModemstateMaskCmd(Rfc2217Command):
    """Set which modem state changes trigger NOTIFY_MODEMSTATE. Default: 255."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.SET_MODEMSTATE_MASK
    mask: ModemStateFlag

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return self.mask.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(mask=ModemStateFlag(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class PurgeDataCmd(Rfc2217Command):
    """Purge access server data buffers."""

    CMD_ID: ClassVar[Rfc2217CmdId] = Rfc2217CmdId.PURGE_DATA
    what: PurgeDataValue

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        return self.what.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        """Parse from bytes."""
        return cls(what=PurgeDataValue(payload[0]))


CMD_ID_TO_CLASS: dict[Rfc2217CmdId, type[Rfc2217Command]] = {
    cls.CMD_ID: cls
    for cls in (
        SignatureCmd,
        SetBaudrateCmd,
        SetDatasizeCmd,
        SetParityCmd,
        SetStopsizeCmd,
        SetControlCmd,
        NotifyLinestateCmd,
        NotifyModemstateCmd,
        FlowcontrolSuspendCmd,
        FlowcontrolResumeCmd,
        SetLinestateMaskCmd,
        SetModemstateMaskCmd,
        PurgeDataCmd,
    )
}


def iac_escape(data: bytes) -> bytes:
    """Double any 0xFF bytes so they aren't mistaken for IAC."""
    return data.replace(b"\xff", b"\xff\xff")


def encode_command(cmd: TelnetCommand | Rfc2217Command) -> bytes:
    """Encode a command to its wire representation.

    For telnet commands: IAC <cmd> <option>
    For RFC 2217 commands: IAC SB 44 <cmd_id> <payload> IAC SE
    """

    if isinstance(cmd, TelnetCommand):
        encoded = bytes([TelnetCmdId.IAC, cmd.CMD_ID]) + cmd.to_bytes()
    else:
        encoded = (
            bytes(
                [
                    TelnetCmdId.IAC,
                    TelnetCmdId.SB,
                    TelnetOption.COM_PORT_OPTION,
                    cmd.CMD_ID,
                ]
            )
            + iac_escape(cmd.to_bytes())
            + bytes([TelnetCmdId.IAC, TelnetCmdId.SE])
        )

    return encoded
