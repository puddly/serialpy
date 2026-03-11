"""RFC 2217 (Telnet Com Port Control) implementation."""

from __future__ import annotations

import dataclasses
from enum import IntEnum, IntFlag
from typing import Union


class TelnetOption(IntEnum):
    """Telnet option codes (the byte following WILL/WONT/DO/DONT)."""

    BINARY = 0
    ECHO = 1
    SUPPRESS_GO_AHEAD = 3
    COM_PORT_OPTION = 44

    @classmethod
    def _missing_(cls, value: object) -> TelnetOption:
        """Allow unknown option codes to pass through as int values."""
        obj = int.__new__(cls, value)
        obj._name_ = f"UNKNOWN_{value}"
        obj._value_ = value
        return obj


# Server response codes are client codes + this offset
SERVER_CMD_OFFSET = 100


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
    WONT = 252
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


class LinestateFlag(IntFlag):
    """Bitmask for NOTIFY_LINESTATE and SET_LINESTATE_MASK."""

    DATA_READY = 1
    OVERRUN_ERROR = 2
    PARITY_ERROR = 4
    FRAMING_ERROR = 8
    BREAK_DETECT = 16
    TRANSFER_HOLDING_REG_EMPTY = 32
    TRANSFER_SHIFT_REG_EMPTY = 64
    TIMEOUT_ERROR = 128


class ModemstateFlag(IntFlag):
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


# ---------------------------------------------------------------------------
# Telnet-level commands (IAC <cmd> <option>)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, kw_only=True)
class WillCmd:
    """IAC WILL <option> — offer to perform an option."""

    option: TelnetOption


@dataclasses.dataclass(frozen=True, kw_only=True)
class WontCmd:
    """IAC WONT <option> — refuse to perform an option."""

    option: TelnetOption


@dataclasses.dataclass(frozen=True, kw_only=True)
class DoCmd:
    """IAC DO <option> — request the other side perform an option."""

    option: TelnetOption


@dataclasses.dataclass(frozen=True, kw_only=True)
class DontCmd:
    """IAC DONT <option> — demand the other side stop performing an option."""

    option: TelnetOption


# ---------------------------------------------------------------------------
# RFC 2217 subnegotiation commands (IAC SB 44 <cmd> <payload> IAC SE)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, kw_only=True)
class SignatureCmd:
    """Exchange signature/identity strings. Empty = request."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.SIGNATURE
    signature: bytes = b""

    def to_bytes(self) -> bytes:
        return self.signature

    @classmethod
    def from_bytes(cls, payload: bytes) -> SignatureCmd:
        return cls(signature=payload)


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetBaudrateCmd:
    """Set baud rate. 0 = query current value."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.SET_BAUDRATE
    baudrate: int

    def to_bytes(self) -> bytes:
        return self.baudrate.to_bytes(4, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> SetBaudrateCmd:
        return cls(baudrate=int.from_bytes(payload, "big"))


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetDatasizeCmd:
    """Set data bit size. 0 = query, 5-8 = actual size."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.SET_DATASIZE
    size: int

    def to_bytes(self) -> bytes:
        return self.size.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> SetDatasizeCmd:
        return cls(size=payload[0])


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetParityCmd:
    """Set parity."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.SET_PARITY
    parity: Rfc2217Parity

    def to_bytes(self) -> bytes:
        return self.parity.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> SetParityCmd:
        return cls(parity=Rfc2217Parity(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetStopsizeCmd:
    """Set stop bits."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.SET_STOPSIZE
    size: Rfc2217StopSize

    def to_bytes(self) -> bytes:
        return self.size.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> SetStopsizeCmd:
        return cls(size=Rfc2217StopSize(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetControlCmd:
    """Set flow control, break, DTR, or RTS."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.SET_CONTROL
    control: ControlCmdId

    def to_bytes(self) -> bytes:
        return self.control.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> SetControlCmd:
        return cls(control=ControlCmdId(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class NotifyLinestateCmd:
    """Server notification of UART line state change."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.NOTIFY_LINESTATE
    linestate: LinestateFlag

    def to_bytes(self) -> bytes:
        return self.linestate.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> NotifyLinestateCmd:
        return cls(linestate=LinestateFlag(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class NotifyModemstateCmd:
    """Server notification of modem state change."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.NOTIFY_MODEMSTATE
    modemstate: ModemstateFlag

    def to_bytes(self) -> bytes:
        return self.modemstate.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> NotifyModemstateCmd:
        return cls(modemstate=ModemstateFlag(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class FlowcontrolSuspendCmd:
    """Request the receiver suspend transmission."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.FLOWCONTROL_SUSPEND

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, payload: bytes) -> FlowcontrolSuspendCmd:
        return cls()


@dataclasses.dataclass(frozen=True, kw_only=True)
class FlowcontrolResumeCmd:
    """Request the receiver resume transmission."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.FLOWCONTROL_RESUME

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, payload: bytes) -> FlowcontrolResumeCmd:
        return cls()


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetLinestateMaskCmd:
    """Set which line state changes trigger NOTIFY_LINESTATE. Default: 0."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.SET_LINESTATE_MASK
    mask: LinestateFlag

    def to_bytes(self) -> bytes:
        return self.mask.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> SetLinestateMaskCmd:
        return cls(mask=LinestateFlag(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class SetModemstateMaskCmd:
    """Set which modem state changes trigger NOTIFY_MODEMSTATE. Default: 255."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.SET_MODEMSTATE_MASK
    mask: ModemstateFlag

    def to_bytes(self) -> bytes:
        return self.mask.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> SetModemstateMaskCmd:
        return cls(mask=ModemstateFlag(payload[0]))


@dataclasses.dataclass(frozen=True, kw_only=True)
class PurgeDataCmd:
    """Purge access server data buffers."""

    CMD_ID: dataclasses.ClassVar[int] = Rfc2217CmdId.PURGE_DATA
    what: PurgeDataValue

    def to_bytes(self) -> bytes:
        return self.what.to_bytes(1, "big")

    @classmethod
    def from_bytes(cls, payload: bytes) -> PurgeDataCmd:
        return cls(what=PurgeDataValue(payload[0]))


TelnetCommand = Union[WillCmd, WontCmd, DoCmd, DontCmd]

Rfc2217Command = Union[
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
]

Command = Union[TelnetCommand, Rfc2217Command]

# All RFC 2217 command classes, used to build lookup tables
RFC2217_CMD_CLASSES: list[type] = [
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
]

CMD_ID_TO_CLASS: dict[int, type[Rfc2217Command]] = {
    cls.CMD_ID: cls for cls in _RFC2217_CMD_CLASSES
}

TELNET_CMD_MAP: dict[type, TelnetCmdId] = {
    WillCmd: TelnetCmdId.WILL,
    WontCmd: TelnetCmdId.WONT,
    DoCmd: TelnetCmdId.DO,
    DontCmd: TelnetCmdId.DONT,
}


def iac_escape(data: bytes) -> bytes:
    """Double any 0xFF bytes so they aren't mistaken for IAC."""
    return data.replace(b"\xff", b"\xff\xff")


def encode_command(cmd: Command, *, server: bool = False) -> bytes:
    """Encode a command to its wire representation.

    For telnet commands: IAC <cmd> <option>
    For RFC 2217 commands: IAC SB 44 <cmd_id> <payload> IAC SE
    """

    # Telnet option negotiation commands
    telnet_cmd_id = TELNET_CMD_MAP.get(type(cmd))
    if telnet_cmd_id is not None:
        encoded = bytes([TelnetCmdId.IAC, telnet_cmd_id, cmd.option])
        LOGGER.debug("Encode telnet %r -> %s", cmd, encoded.hex(" "))
        return encoded

    # RFC 2217 subnegotiation commands
    code = cmd.CMD_ID + SERVER_CMD_OFFSET if server else cmd.CMD_ID
    payload = cmd.to_bytes()

    encoded = (
        bytes([TelnetCmdId.IAC, TelnetCmdId.SB, TelnetOption.COM_PORT_OPTION, code])
        + iac_escape(payload)
        + bytes([TelnetCmdId.IAC, TelnetCmdId.SE])
    )
    LOGGER.debug("Encode RFC2217 %r -> %s", cmd, encoded.hex(" "))
    return encoded
