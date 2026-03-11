"""RFC 2217 (Telnet Com Port Control) implementation."""

from __future__ import annotations

from enum import IntEnum
import logging

from typing_extensions import Buffer

from ...common import (
    BaseSerialTransport,
    ModemPins,
    Parity,
    PinState,
    SerialException,
    StopBits,
    UnsupportedSetting,
)
from ..serial_socket import SocketSerial
from .types import (
    CMD_ID_TO_CLASS,
    ControlCmdId,
    DoCmd,
    DontCmd,
    LineStateFlag,
    ModemStateFlag,
    NotifyLinestateCmd,
    NotifyModemstateCmd,
    Rfc2217Command,
    Rfc2217Parity,
    Rfc2217StopSize,
    SetBaudrateCmd,
    SetControlCmd,
    SetDatasizeCmd,
    SetLinestateMaskCmd,
    SetModemstateMaskCmd,
    SetParityCmd,
    SetStopsizeCmd,
    TelnetCmdId,
    TelnetCommand,
    TelnetOption,
    WillCmd,
    WontCmd,
    encode_command,
    iac_escape,
)

LOGGER = logging.getLogger(__name__)


# Server response codes are client codes + this offset
SERVER_CMD_OFFSET = 100


RFC2217_PARITY_MAP = {
    Parity.NONE: Rfc2217Parity.NONE,
    Parity.ODD: Rfc2217Parity.ODD,
    Parity.EVEN: Rfc2217Parity.EVEN,
    Parity.MARK: Rfc2217Parity.MARK,
    Parity.SPACE: Rfc2217Parity.SPACE,
}

RFC2217_STOPBITS_MAP = {
    StopBits.ONE: Rfc2217StopSize.ONE,
    StopBits.TWO: Rfc2217StopSize.TWO,
    StopBits.ONE_POINT_FIVE: Rfc2217StopSize.ONE_POINT_FIVE,
}


OPTION_CMD_TO_TYPE: dict[int, type[WillCmd | WontCmd | DoCmd | DontCmd]] = {
    TelnetCmdId.WILL: WillCmd,
    TelnetCmdId.WONT: WontCmd,  # codespell:ignore wont
    TelnetCmdId.DO: DoCmd,
    TelnetCmdId.DONT: DontCmd,
}


class TelnetParserState(IntEnum):
    """States for the telnet protocol parser state machine."""

    NORMAL = 0
    IAC_SEEN = 1
    OPTION_CMD = (
        2  # waiting for option byte after WILL/WONT/DO/DONT  # codespell:ignore wont
    )
    SUBNEG_OPTION = 3  # waiting for option byte after SB
    SUBNEG_DATA = 4  # accumulating subneg payload
    SUBNEG_IAC = 5  # IAC seen inside subnegotiation


class TelnetParser:
    """State machine that separates a telnet byte stream into data and commands.

    Call ``feed()`` with raw bytes from the socket. It returns a list of items,
    each either ``bytes`` (serial data) or a parsed ``TelnetCommand | Rfc2217Command``.
    """

    def __init__(self) -> None:
        """Initialize the telnet parser."""
        self._state = TelnetParserState.NORMAL
        self._pending_cmd: int = 0
        self._subneg_option: int = 0
        self._subneg_buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes | TelnetCommand | Rfc2217Command]:
        """Process raw bytes and return data chunks and commands."""
        LOGGER.debug("Parser feed: %d bytes: %s", len(data), data.hex(" "))
        result: list[bytes | TelnetCommand | Rfc2217Command] = []
        data_buf = bytearray()

        for byte in data:
            if self._state == TelnetParserState.NORMAL:
                if byte == TelnetCmdId.IAC:
                    self._state = TelnetParserState.IAC_SEEN
                else:
                    data_buf.append(byte)

            elif self._state == TelnetParserState.IAC_SEEN:
                if byte == TelnetCmdId.IAC:
                    # Escaped IAC → literal 0xFF data byte
                    data_buf.append(0xFF)
                    self._state = TelnetParserState.NORMAL
                elif byte == TelnetCmdId.SB:
                    # Flush data before command
                    if data_buf:
                        result.append(bytes(data_buf))
                        data_buf.clear()
                    self._state = TelnetParserState.SUBNEG_OPTION
                elif byte in OPTION_CMD_TO_TYPE:
                    if data_buf:
                        result.append(bytes(data_buf))
                        data_buf.clear()
                    self._pending_cmd = byte
                    self._state = TelnetParserState.OPTION_CMD
                else:
                    # NOP, BRK, DM, etc. — ignore
                    self._state = TelnetParserState.NORMAL

            elif self._state == TelnetParserState.OPTION_CMD:
                cmd_cls = OPTION_CMD_TO_TYPE[self._pending_cmd]
                option_cmd = cmd_cls(option=TelnetOption(byte))
                LOGGER.debug("Parsed telnet command: %r", option_cmd)
                result.append(option_cmd)
                self._state = TelnetParserState.NORMAL

            elif self._state == TelnetParserState.SUBNEG_OPTION:
                self._subneg_option = byte
                self._subneg_buffer.clear()
                self._state = TelnetParserState.SUBNEG_DATA

            elif self._state == TelnetParserState.SUBNEG_DATA:
                if byte == TelnetCmdId.IAC:
                    self._state = TelnetParserState.SUBNEG_IAC
                else:
                    self._subneg_buffer.append(byte)

            elif self._state == TelnetParserState.SUBNEG_IAC:
                if byte == TelnetCmdId.IAC:
                    # Escaped IAC inside subneg
                    self._subneg_buffer.append(0xFF)
                    self._state = TelnetParserState.SUBNEG_DATA
                elif byte == TelnetCmdId.SE:
                    cmd = self._finish_subneg()
                    if cmd is not None:
                        result.append(cmd)
                    self._state = TelnetParserState.NORMAL
                else:
                    # Malformed — discard subneg and treat as normal IAC cmd
                    LOGGER.warning(
                        "Malformed subneg: IAC %d inside subneg for option %d",
                        byte,
                        self._subneg_option,
                    )
                    self._state = TelnetParserState.NORMAL

        if data_buf:
            result.append(bytes(data_buf))

        return result

    def _finish_subneg(self) -> Rfc2217Command | None:
        """Parse a completed subnegotiation."""
        option = self._subneg_option
        payload = bytes(self._subneg_buffer)

        LOGGER.debug(
            "Subneg complete: option=%d payload=%s",
            option,
            payload.hex(" ") if payload else "(empty)",
        )

        if option != TelnetOption.COM_PORT_OPTION:
            LOGGER.debug("Ignoring subneg for unknown option %d", option)
            return None

        if not payload:
            LOGGER.debug("Empty COM-PORT-OPTION subneg")
            return None

        cmd_code = payload[0]
        cmd_payload = payload[1:]

        # Server responses use codes offset by +100
        if cmd_code >= SERVER_CMD_OFFSET:
            cmd_code -= SERVER_CMD_OFFSET

        cmd_cls = CMD_ID_TO_CLASS.get(cmd_code)
        if cmd_cls is None:
            LOGGER.debug("Unknown RFC 2217 command code: %d", cmd_code)
            return None

        cmd = cmd_cls.from_bytes(cmd_payload)
        LOGGER.debug("Parsed RFC2217 %s", cmd)
        return cmd


class RFC2217Serial(SocketSerial):
    """RFC 2217 serial port implementation (Synchronous)."""

    # Options we accept when the server offers (WILL) or requests (DO) them
    _ACCEPTED_OPTIONS = frozenset(
        {
            TelnetOption.BINARY,
            TelnetOption.SUPPRESS_GO_AHEAD,
        }
    )

    def __init__(
        self,
        *args,
        receive_buffer_size: int = 4096,
        **kwargs,
    ) -> None:
        """Initialize the RFC 2217 serial port."""
        super().__init__(*args, **kwargs)
        self._receive_buffer_size = receive_buffer_size

        self._parser = TelnetParser()
        self._data_buffer = bytearray()
        self._pending_commands: list[TelnetCommand | Rfc2217Command] = []
        self._modemstate = ModemStateFlag(0)
        self._linestate = LineStateFlag(0)
        self._negotiated = False

    # -- connection lifecycle -----------------------------------------------

    def _open(self) -> None:
        """Open the TCP connection and negotiate COM-PORT-OPTION."""
        super()._open()
        assert self._socket is not None
        LOGGER.debug("TCP connected to %s:%s", self._host, self._port)
        self._negotiate()

    def _negotiate(self) -> None:
        """Perform the initial WILL/DO handshake for COM-PORT-OPTION."""
        assert self._socket is not None
        LOGGER.debug("Starting RFC 2217 negotiation: sending WILL COM-PORT-OPTION")
        self._send_command(WillCmd(option=TelnetOption.COM_PORT_OPTION))

        while True:
            if self._check_negotiation_response():
                return

            self._recv_and_process()

    def _configure_port(self) -> None:
        """Send serial port configuration to the access server."""
        # Let parent set socket timeout
        super()._configure_port()

        if not self._negotiated:
            return

        LOGGER.debug(
            "Configuring port: baudrate=%d byte_size=%d parity=%s stopbits=%s "
            "rtscts=%s xonxoff=%s",
            self._baudrate,
            self._byte_size,
            self._parity,
            self._stopbits,
            self._rtscts,
            self._xonxoff,
        )

        if self._rtscts and self._xonxoff:
            raise UnsupportedSetting(
                "Cannot enable both RTS/CTS and XON/XOFF flow control"
            )

        for cmd in (
            SetBaudrateCmd(baudrate=self._baudrate),
            SetDatasizeCmd(size=self._byte_size),
            SetParityCmd(parity=RFC2217_PARITY_MAP[self._parity]),
            SetStopsizeCmd(size=RFC2217_STOPBITS_MAP[self._stopbits]),
            SetControlCmd(control=self._get_flow_control_command()),
            SetModemstateMaskCmd(mask=ModemStateFlag(255)),
            SetLinestateMaskCmd(mask=LineStateFlag(0)),
        ):
            self._send_and_wait(cmd)

        LOGGER.debug("Port configuration complete")

    def _close(self) -> None:
        """Close the connection and reset state."""
        self._negotiated = False
        self._data_buffer.clear()
        self._pending_commands.clear()
        LOGGER.debug("Closing RFC 2217 connection")
        super()._close()

    # -- low-level send/receive helpers -------------------------------------

    def _send_command(self, cmd: TelnetCommand | Rfc2217Command) -> None:
        """Encode and send a command over the socket."""
        assert self._socket is not None
        data = encode_command(cmd)
        LOGGER.debug("TX cmd: %r  [%s]", cmd, data.hex(" "))
        self._socket.sendall(data)

    def _recv_and_process(self) -> None:
        """Read raw bytes from socket, feed through parser, dispatch results."""
        assert self._socket is not None
        buf = bytearray(self._receive_buffer_size)
        n = self._socket.recv_into(buf)

        if n == 0:
            raise SerialException("RFC 2217 connection closed by server")

        raw = bytes(buf[:n])
        LOGGER.debug("RX raw: %d bytes  [%s]", n, raw.hex(" "))
        self._dispatch_parser_items(self._parser.feed(raw))

    def _get_flow_control_command(self) -> ControlCmdId:
        """Return the RFC 2217 control command for the current flow control."""
        if self._rtscts:
            return ControlCmdId.USE_HARDWARE
        elif self._xonxoff:
            return ControlCmdId.USE_XON_XOFF
        else:
            return ControlCmdId.USE_NO_FLOW_CONTROL

    def _queue_command(self, cmd: TelnetCommand | Rfc2217Command) -> None:
        """Store a parsed command until a caller consumes it."""
        LOGGER.debug("RX cmd queued: %r", cmd)
        self._pending_commands.append(cmd)

    def _handle_negotiation_command(self, cmd: WillCmd | DoCmd) -> None:
        """Respond to telnet option negotiation or queue COM-PORT-OPTION."""
        if cmd.option == TelnetOption.COM_PORT_OPTION:
            self._queue_command(cmd)
            return

        accepts_option = cmd.option in self._ACCEPTED_OPTIONS
        response: TelnetCommand

        if isinstance(cmd, WillCmd):
            response = (
                DoCmd(option=cmd.option)
                if accepts_option
                else DontCmd(option=cmd.option)
            )
        else:
            response = (
                WillCmd(option=cmd.option)
                if accepts_option
                else WontCmd(option=cmd.option)
            )

        action = "accepting" if accepts_option else "refusing"
        LOGGER.debug("RX %r -> %s (%s)", cmd, action, type(response).__name__)
        self._send_command(response)

    def _handle_command(self, cmd: TelnetCommand | Rfc2217Command) -> None:
        """Update local state, respond to telnet negotiation, or queue the command."""
        if isinstance(cmd, NotifyModemstateCmd):
            LOGGER.debug("RX modemstate notification: %r", cmd)
            self._modemstate = cmd.modemstate
            return

        if isinstance(cmd, NotifyLinestateCmd):
            LOGGER.debug("RX linestate notification: %r", cmd)
            self._linestate = cmd.linestate
            return

        if isinstance(cmd, (WillCmd, DoCmd)):
            self._handle_negotiation_command(cmd)
            return

        self._queue_command(cmd)

    def _dispatch_parser_items(
        self, items: list[bytes | TelnetCommand | Rfc2217Command]
    ) -> None:
        """Route parser output: buffer data, handle notifications, queue cmds."""
        for item in items:
            if isinstance(item, bytes):
                LOGGER.debug("RX data: %d bytes  [%s]", len(item), item.hex(" "))
                self._data_buffer.extend(item)
            else:
                self._handle_command(item)

    def _check_negotiation_response(self) -> bool:
        """Consume COM-PORT-OPTION negotiation responses when present."""
        for i, cmd in enumerate(self._pending_commands):
            if isinstance(cmd, DoCmd) and cmd.option == TelnetOption.COM_PORT_OPTION:
                self._pending_commands.pop(i)
                self._negotiated = True
                LOGGER.debug("Negotiation complete: server accepted COM-PORT-OPTION")
                return True

            if isinstance(cmd, DontCmd) and cmd.option == TelnetOption.COM_PORT_OPTION:
                raise SerialException("Server refused COM-PORT-OPTION (sent DONT 44)")

        return False

    def _drain_data_buffer(self, target: memoryview) -> int:
        """Copy buffered serial data into ``target`` when available."""
        if not self._data_buffer:
            return 0

        n = min(len(target), len(self._data_buffer))
        target[:n] = self._data_buffer[:n]
        del self._data_buffer[:n]
        LOGGER.debug("Read %d bytes from data buffer", n)
        return n

    def _send_and_wait(self, cmd: Rfc2217Command) -> Rfc2217Command:
        """Send a command and block until the matching server ack arrives."""
        cmd_type = type(cmd)
        self._send_command(cmd)
        LOGGER.debug("Waiting for %s ack...", cmd_type.__name__)

        while True:
            for i, pending in enumerate(self._pending_commands):
                if isinstance(pending, cmd_type):
                    self._pending_commands.pop(i)
                    break
            else:
                pending = None

            if pending is not None:
                assert not isinstance(pending, (WillCmd, WontCmd, DoCmd, DontCmd))
                LOGGER.debug("RX ack: %r", pending)
                return pending

            self._recv_and_process()

    # -- data read/write (application layer) --------------------------------

    def _write(self, b: Buffer, *, timeout: float | None) -> int:
        """Write data bytes to socket, escaping any 0xFF."""
        assert self._socket is not None
        data = bytes(b)
        escaped = iac_escape(data)
        LOGGER.debug(
            "TX data: %d bytes (%d on wire)  [%s]",
            len(data),
            len(escaped),
            escaped.hex(" "),
        )
        self._socket.settimeout(timeout)
        self._socket.sendall(escaped)
        return len(data)

    def _readinto(self, b: Buffer, *, timeout: float | None) -> int:
        """Read data bytes into buffer, transparently handling telnet framing."""
        m = memoryview(b).cast("B")

        # Serve from already-buffered data first
        n = self._drain_data_buffer(m)
        if n:
            return n

        # Read from socket and feed through parser
        assert self._socket is not None
        self._socket.settimeout(timeout)
        buf = bytearray(max(len(m), self._receive_buffer_size))

        try:
            n = self._socket.recv_into(buf)
        except TimeoutError:
            return 0

        if n == 0:
            return 0

        raw = bytes(buf[:n])
        LOGGER.debug("RX raw (readinto): %d bytes  [%s]", n, raw.hex(" "))
        self._dispatch_parser_items(self._parser.feed(raw))

        # Serve whatever data the parser produced
        n = self._drain_data_buffer(m)
        if n:
            return n

        return 0

    # -- modem pins ---------------------------------------------------------

    def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        """Set DTR/RTS via SET_CONTROL commands."""
        LOGGER.debug("Setting modem pins: %r", modem_pins)

        if modem_pins.dtr is PinState.HIGH:
            self._send_and_wait(SetControlCmd(control=ControlCmdId.SET_DTR_ON))
        elif modem_pins.dtr is PinState.LOW:
            self._send_and_wait(SetControlCmd(control=ControlCmdId.SET_DTR_OFF))

        if modem_pins.rts is PinState.HIGH:
            self._send_and_wait(SetControlCmd(control=ControlCmdId.SET_RTS_ON))
        elif modem_pins.rts is PinState.LOW:
            self._send_and_wait(SetControlCmd(control=ControlCmdId.SET_RTS_OFF))

    def _get_modem_pins(self) -> ModemPins:
        """Return modem pin state from the last NOTIFY-MODEMSTATE."""
        state = self._modemstate

        return ModemPins(
            cts=(PinState.HIGH if state & ModemStateFlag.CTS else PinState.LOW),
            dsr=(PinState.HIGH if state & ModemStateFlag.DSR else PinState.LOW),
            rng=(PinState.HIGH if state & ModemStateFlag.RI else PinState.LOW),
            car=(PinState.HIGH if state & ModemStateFlag.RLSD else PinState.LOW),
        )

    def flush(self) -> None:
        """Flush write buffers (no-op, TCP handles buffering)."""


class RFC2217SerialTransport(BaseSerialTransport):
    """TODO, later."""

    def __init__(self, *args, **kwargs):
        """Initialize the RFC 2217 serial transport."""
        pass
