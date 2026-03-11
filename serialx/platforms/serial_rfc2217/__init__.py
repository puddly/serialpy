"""RFC 2217 (Telnet Com Port Control) implementation."""

from __future__ import annotations

import asyncio
from enum import IntEnum
import logging
import sys

if sys.version_info >= (3, 11):
    from asyncio import timeout as asyncio_timeout
else:
    from async_timeout import timeout as asyncio_timeout

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
    Rfc2217CmdId,
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
                    # Escaped IAC -> literal 0xFF data byte
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
        connect_timeout: float = 5.0,
        **kwargs,
    ) -> None:
        """Initialize the RFC 2217 serial port."""
        super().__init__(*args, **kwargs)
        self._receive_buffer_size = receive_buffer_size
        self._connect_timeout = connect_timeout

        self._parser = TelnetParser()
        self._data_buffer = bytearray()
        self._pending_telnet: dict[TelnetCmdId, TelnetCommand] = {}
        self._pending_rfc2217: dict[Rfc2217CmdId, Rfc2217Command] = {}
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
        self._pending_telnet.clear()
        self._pending_rfc2217.clear()
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

    def _build_negotiation_response(self, cmd: WillCmd | DoCmd) -> TelnetCommand | None:
        """Build a response to a telnet option negotiation command.

        Returns None for COM-PORT-OPTION (queued instead), otherwise the response.
        """
        if cmd.option == TelnetOption.COM_PORT_OPTION:
            self._pending_telnet[cmd.CMD_ID] = cmd
            return None

        accepts_option = cmd.option in self._ACCEPTED_OPTIONS

        if isinstance(cmd, WillCmd):
            response: TelnetCommand = (
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
        return response

    def _dispatch_parser_items(
        self, items: list[bytes | TelnetCommand | Rfc2217Command]
    ) -> None:
        """Route parser output: buffer data, handle notifications, queue cmds."""

        # Negotiation responses are deferred until after all items are processed so that
        # the full batch is parsed before any replies are sent.
        responses: list[TelnetCommand] = []

        for item in items:
            if isinstance(item, bytes):
                LOGGER.debug("RX data: %d bytes  [%s]", len(item), item.hex(" "))
                self._data_buffer.extend(item)
            elif isinstance(item, NotifyModemstateCmd):
                LOGGER.debug("RX modemstate notification: %r", item)
                self._modemstate = item.modemstate
            elif isinstance(item, NotifyLinestateCmd):
                LOGGER.debug("RX linestate notification: %r", item)
                self._linestate = item.linestate
            elif isinstance(item, (WillCmd, DoCmd)):
                response = self._build_negotiation_response(item)
                if response is not None:
                    responses.append(response)
            elif isinstance(item, Rfc2217Command):
                LOGGER.debug("RX rfc2217 cmd queued: %r", item)
                self._pending_rfc2217[item.CMD_ID] = item
            else:
                LOGGER.debug("RX telnet cmd queued: %r", item)
                self._pending_telnet[item.CMD_ID] = item

        for response in responses:
            self._send_command(response)

    def _check_negotiation_response(self) -> bool:
        """Consume COM-PORT-OPTION negotiation responses when present."""
        if TelnetCmdId.DO in self._pending_telnet:
            cmd = self._pending_telnet.pop(TelnetCmdId.DO)
            assert isinstance(cmd, DoCmd)
            assert cmd.option == TelnetOption.COM_PORT_OPTION
            self._negotiated = True
            LOGGER.debug("Negotiation complete: server accepted COM-PORT-OPTION")
            return True

        if TelnetCmdId.DONT in self._pending_telnet:
            self._pending_telnet.pop(TelnetCmdId.DONT)
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
        cmd_id = cmd.CMD_ID
        self._send_command(cmd)
        LOGGER.debug("Waiting for %s ack...", type(cmd).__name__)

        while True:
            pending = self._pending_rfc2217.pop(cmd_id, None)

            if pending is not None:
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


class _RFC2217ProxyProtocol(asyncio.Protocol):
    """Bridge protocol between asyncio TCP transport and RFC2217SerialTransport."""

    def __init__(self, serial_transport: RFC2217SerialTransport) -> None:
        """Initialize the proxy protocol."""
        self._serial_transport = serial_transport

    def data_received(self, data: bytes) -> None:
        """Forward received data to the serial transport for parsing."""
        self._serial_transport._data_received(data)

    def pause_writing(self) -> None:
        """Propagate backpressure from TCP transport to serial protocol."""
        self._serial_transport._protocol.pause_writing()

    def resume_writing(self) -> None:
        """Propagate resume signal from TCP transport to serial protocol."""
        self._serial_transport._protocol.resume_writing()

    def connection_lost(self, exc: Exception | None) -> None:
        """Handle TCP connection loss."""
        self._serial_transport._tcp_connection_lost(exc)


class RFC2217SerialTransport(BaseSerialTransport):
    """Async RFC 2217 serial transport over TCP."""

    transport_name = "rfc2217"
    _serial: RFC2217Serial

    def __init__(
        self, loop: asyncio.AbstractEventLoop, protocol: asyncio.Protocol
    ) -> None:
        """Initialize the RFC 2217 serial transport."""
        super().__init__(loop, protocol)
        self._tcp_transport: asyncio.Transport | None = None
        self._tcp_connection_lost_waiter: asyncio.Future[None] | None = None
        self._connection_lost_called = False
        self._telnet_waiters: dict[TelnetCmdId, asyncio.Future[TelnetCommand]] = {}
        self._rfc2217_waiters: dict[Rfc2217CmdId, asyncio.Future[Rfc2217Command]] = {}

    # -- connection lifecycle -----------------------------------------------

    async def _connect(  # type: ignore[override]
        self,
        *,
        path: str,
        baudrate: int,
        parity: Parity = Parity.NONE,
        stopbits: StopBits | int | float = StopBits.ONE,
        xonxoff: bool = False,
        rtscts: bool = False,
        byte_size: int = 8,
        **kwargs,
    ) -> None:
        """Connect to the RFC 2217 server and negotiate COM-PORT-OPTION."""
        self._serial = RFC2217Serial(
            path=path,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            byte_size=byte_size,
        )
        self._extra["serial"] = self._serial

        self._tcp_connection_lost_waiter = self._loop.create_future()

        async with asyncio_timeout(self._serial._connect_timeout):
            tcp_transport, _ = await self._loop.create_connection(
                lambda: _RFC2217ProxyProtocol(self),
                host=self._serial._host,
                port=self._serial._port,
            )
            self._tcp_transport = tcp_transport

            if self._connection_lost_called:
                tcp_transport.close()
                if self._tcp_connection_lost_waiter is not None:
                    await self._tcp_connection_lost_waiter
                self._tcp_transport = None
                return

            await self._negotiate()
            await self._configure_port()

        self._protocol.connection_made(self)

    async def _negotiate(self) -> None:
        """Perform the initial WILL/DO handshake for COM-PORT-OPTION."""
        LOGGER.debug("Starting RFC 2217 negotiation: sending WILL COM-PORT-OPTION")
        self._send_command(WillCmd(option=TelnetOption.COM_PORT_OPTION))

        negotiation_waiter: asyncio.Future[TelnetCommand] = self._loop.create_future()
        self._telnet_waiters[TelnetCmdId.DO] = negotiation_waiter

        cmd = await negotiation_waiter
        assert isinstance(cmd, DoCmd)
        assert cmd.option == TelnetOption.COM_PORT_OPTION
        LOGGER.debug("Negotiation complete: server accepted COM-PORT-OPTION")

    async def _configure_port(self) -> None:
        """Send serial port configuration to the access server."""
        serial = self._serial

        LOGGER.debug(
            "Configuring port: baudrate=%d byte_size=%d parity=%s stopbits=%s "
            "rtscts=%s xonxoff=%s",
            serial._baudrate,
            serial._byte_size,
            serial._parity,
            serial._stopbits,
            serial._rtscts,
            serial._xonxoff,
        )

        if serial._rtscts and serial._xonxoff:
            raise UnsupportedSetting(
                "Cannot enable both RTS/CTS and XON/XOFF flow control"
            )

        flow_control = serial._get_flow_control_command()

        for cmd in (
            SetBaudrateCmd(baudrate=serial._baudrate),
            SetDatasizeCmd(size=serial._byte_size),
            SetParityCmd(parity=RFC2217_PARITY_MAP[serial._parity]),
            SetStopsizeCmd(size=RFC2217_STOPBITS_MAP[serial._stopbits]),
            SetControlCmd(control=flow_control),
            SetModemstateMaskCmd(mask=ModemStateFlag(255)),
            SetLinestateMaskCmd(mask=LineStateFlag(0)),
        ):
            await self._send_and_wait(cmd)

        LOGGER.debug("Port configuration complete")

    # -- low-level send/receive helpers -------------------------------------

    def _send_command(self, cmd: TelnetCommand | Rfc2217Command) -> None:
        """Encode and send a command over the TCP transport."""
        assert self._tcp_transport is not None
        data = encode_command(cmd)
        LOGGER.debug("TX cmd: %r  [%s]", cmd, data.hex(" "))
        self._tcp_transport.write(data)

    def _data_received(self, data: bytes) -> None:
        """Handle raw data from the TCP transport."""
        LOGGER.debug("RX raw: %d bytes  [%s]", len(data), data.hex(" "))
        items = self._serial._parser.feed(data)
        self._dispatch_parser_items(items)

    def _build_negotiation_response(self, cmd: WillCmd | DoCmd) -> TelnetCommand | None:
        """Build a response to a telnet option negotiation command.

        Returns None for COM-PORT-OPTION (resolved via waiter), otherwise the
        response.
        """
        if cmd.option == TelnetOption.COM_PORT_OPTION:
            # Resolve the negotiation waiter if one exists
            waiter = self._telnet_waiters.pop(cmd.CMD_ID, None)
            if waiter is not None and not waiter.done():
                waiter.set_result(cmd)
            else:
                self._serial._pending_telnet[cmd.CMD_ID] = cmd
            return None

        accepts_option = cmd.option in self._serial._ACCEPTED_OPTIONS

        if isinstance(cmd, WillCmd):
            response: TelnetCommand = (
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
        return response

    def _dispatch_parser_items(
        self, items: list[bytes | TelnetCommand | Rfc2217Command]
    ) -> None:
        """Route parser output: buffer data, handle notifications, queue cmds."""
        # Negotiation responses are deferred until after all items are processed
        # so that the full batch is parsed before any replies are sent.
        responses: list[TelnetCommand] = []
        serial_data = bytearray()

        for item in items:
            if isinstance(item, bytes):
                LOGGER.debug("RX data: %d bytes  [%s]", len(item), item.hex(" "))
                serial_data.extend(item)
            elif isinstance(item, NotifyModemstateCmd):
                LOGGER.debug("RX modemstate notification: %r", item)
                self._serial._modemstate = item.modemstate
            elif isinstance(item, NotifyLinestateCmd):
                LOGGER.debug("RX linestate notification: %r", item)
                self._serial._linestate = item.linestate
            elif isinstance(item, (WillCmd, DoCmd)):
                response = self._build_negotiation_response(item)
                if response is not None:
                    responses.append(response)
            elif isinstance(item, Rfc2217Command):
                LOGGER.debug("RX rfc2217 cmd: %r", item)
                waiter = self._rfc2217_waiters.pop(item.CMD_ID, None)
                if waiter is not None and not waiter.done():
                    waiter.set_result(item)
                else:
                    self._serial._pending_rfc2217[item.CMD_ID] = item
            else:
                LOGGER.debug("RX telnet cmd queued: %r", item)
                self._serial._pending_telnet[item.CMD_ID] = item

        for response in responses:
            self._send_command(response)

        if serial_data:
            self._protocol.data_received(bytes(serial_data))

    async def _send_and_wait(self, cmd: Rfc2217Command) -> Rfc2217Command:
        """Send a command and wait for the matching server ack."""
        cmd_id = cmd.CMD_ID

        # Check if an ack already arrived
        pending = self._serial._pending_rfc2217.pop(cmd_id, None)
        if pending is not None:
            self._send_command(cmd)
            LOGGER.debug("RX ack (already pending): %r", pending)
            return pending

        waiter: asyncio.Future[Rfc2217Command] = self._loop.create_future()
        self._rfc2217_waiters[cmd_id] = waiter
        self._send_command(cmd)
        LOGGER.debug("Waiting for %s ack...", type(cmd).__name__)

        result = await waiter
        LOGGER.debug("RX ack: %r", result)
        return result

    # -- write --------------------------------------------------------------

    def write(self, data: bytes | bytearray | memoryview) -> None:
        """Write data to the serial port, escaping IAC bytes."""
        assert self._tcp_transport is not None
        escaped = iac_escape(bytes(data))
        LOGGER.debug("TX data: %d bytes (%d on wire)", len(data), len(escaped))
        self._tcp_transport.write(escaped)

    # -- modem pins ---------------------------------------------------------

    async def get_modem_pins(self) -> ModemPins:
        """Return modem pin state from the last NOTIFY-MODEMSTATE."""
        return self._serial._get_modem_pins()

    async def set_modem_pins(
        self,
        modem_pins: ModemPins | None = None,
        **kwargs,
    ) -> None:
        """Set DTR/RTS via SET_CONTROL commands."""
        if modem_pins is None:
            return

        LOGGER.debug("Setting modem pins: %r", modem_pins)

        if modem_pins.dtr is PinState.HIGH:
            await self._send_and_wait(SetControlCmd(control=ControlCmdId.SET_DTR_ON))
        elif modem_pins.dtr is PinState.LOW:
            await self._send_and_wait(SetControlCmd(control=ControlCmdId.SET_DTR_OFF))

        if modem_pins.rts is PinState.HIGH:
            await self._send_and_wait(SetControlCmd(control=ControlCmdId.SET_RTS_ON))
        elif modem_pins.rts is PinState.LOW:
            await self._send_and_wait(SetControlCmd(control=ControlCmdId.SET_RTS_OFF))

    # -- transport lifecycle ------------------------------------------------

    def _tcp_connection_lost(self, exc: Exception | None) -> None:
        """Handle TCP connection loss."""
        if (
            self._tcp_connection_lost_waiter is not None
            and not self._tcp_connection_lost_waiter.done()
        ):
            self._tcp_connection_lost_waiter.set_result(None)

        if self._connection_lost_called:
            return
        self._connection_lost_called = True
        self._closing = True
        self._tcp_transport = None

        # Fail any pending waiters
        waiter_exc = exc or SerialException("RFC 2217 connection closed by server")
        for waiters in (self._telnet_waiters, self._rfc2217_waiters):
            for waiter in waiters.values():
                if not waiter.done():
                    waiter.set_exception(waiter_exc)
            waiters.clear()

        self._call_protocol_connection_lost(exc)

    def pause_reading(self) -> None:
        """Pause reading from the TCP transport."""
        assert self._tcp_transport is not None
        self._tcp_transport.pause_reading()

    def resume_reading(self) -> None:
        """Resume reading from the TCP transport."""
        assert self._tcp_transport is not None
        self._tcp_transport.resume_reading()

    def close(self) -> None:
        """Close the transport."""
        if self._connection_lost_called:
            return
        self._closing = True

        if self._tcp_transport is not None:
            self._tcp_transport.close()
        else:
            self._tcp_connection_lost(None)

    def abort(self) -> None:
        """Abort the transport immediately."""
        if self._connection_lost_called:
            return
        self._closing = True

        if self._tcp_transport is not None:
            self._tcp_transport.abort()
        else:
            self._tcp_connection_lost(None)

    async def flush(self) -> None:
        """Flush write buffers (no-op, TCP transport handles buffering)."""

    def get_write_buffer_size(self) -> int:
        """Get the number of bytes currently in the write buffer."""
        if self._tcp_transport is not None:
            return self._tcp_transport.get_write_buffer_size()
        return 0
