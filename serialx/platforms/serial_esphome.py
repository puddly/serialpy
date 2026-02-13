"""ESPHome serial proxy transport."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import urllib.parse

import aioesphomeapi

from serialx.common import BaseSerialTransport, ModemPins, Parity, PinState, StopBits

LOGGER = logging.getLogger(__name__)

ESPHOME_DEFAULT_PORT = 6053

PARITY_MAP = {
    Parity.NONE: aioesphomeapi.SerialProxyParity.NONE,
    Parity.EVEN: aioesphomeapi.SerialProxyParity.EVEN,
    Parity.ODD: aioesphomeapi.SerialProxyParity.ODD,
}

STOP_BITS_MAP = {
    StopBits.ONE: 1,
    StopBits.TWO: 2,
}


class ESPHomeSerialTransport(BaseSerialTransport):
    """Serial transport over ESPHome serial proxy API."""

    transport_name = "esphome"

    def __init__(
        self, loop: asyncio.AbstractEventLoop, protocol: asyncio.Protocol
    ) -> None:
        """Initialize the ESPHome serial transport."""
        super().__init__(loop, protocol)
        self._api: aioesphomeapi.APIClient | None = None
        self._instance: int = 0
        self._unsub: Callable[[], None] | None = None

        self._baudrate: int = 0
        self._parity: Parity = Parity.NONE
        self._stopbits: StopBits = StopBits.ONE
        self._byte_size: int = 8

    async def _connect(  # type: ignore[override]
        self,
        *,
        url: str,
        baudrate: int,
        parity: Parity = Parity.NONE,
        stopbits: StopBits = StopBits.ONE,
        xonxoff: bool = False,
        rtscts: bool = False,
        byte_size: int = 8,
        **kwargs,
    ) -> None:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)

        host = parsed.hostname
        port = parsed.port or ESPHOME_DEFAULT_PORT

        # Instance from path: "/0" -> 0, "/" -> 0, "" -> 0
        path = parsed.path.strip("/")
        self._instance = int(path) if path else 0

        password = params["password"][0] if "password" in params else None
        noise_psk = params["noise_psk"][0] if "noise_psk" in params else None

        self._baudrate = baudrate
        self._parity = parity
        self._stopbits = stopbits
        self._byte_size = byte_size

        self._api = aioesphomeapi.APIClient(
            host,
            port,
            password=password,
            noise_psk=noise_psk,
        )

        await self._api.connect(login=True)

        self._unsub = self._api.subscribe_serial_proxy_data(self._on_data)

        self._api.serial_proxy_configure(
            instance=self._instance,
            baudrate=baudrate,
            flow_control=rtscts,
            parity=PARITY_MAP[parity],
            stop_bits=STOP_BITS_MAP[stopbits],
            data_size=byte_size,
        )

        self._protocol.connection_made(self)

    def _on_data(self, msg: aioesphomeapi.SerialProxyDataReceived) -> None:
        if msg.instance == self._instance:
            self._protocol.data_received(msg.data)

    @property
    def baudrate(self) -> int:
        """Get the baud rate."""
        return self._baudrate

    @property
    def parity(self) -> Parity:
        """Get the parity."""
        return self._parity

    @property
    def stopbits(self) -> StopBits:
        """Get the number of stop bits."""
        return self._stopbits

    @property
    def byte_size(self) -> int:
        """Get the byte size."""
        return self._byte_size

    @property
    def exclusive(self) -> bool:
        """Get the exclusive setting."""
        return True

    def write(self, data: bytes | bytearray | memoryview) -> None:
        """Write data to the serial proxy."""
        assert self._api is not None
        self._api.serial_proxy_write(instance=self._instance, data=bytes(data))

    def is_closing(self) -> bool:
        """Return whether the transport is closing."""
        return self._closing

    def close(self) -> None:
        """Close the transport."""
        if self._closing:
            return
        self._closing = True

        if self._unsub is not None:
            self._unsub()
            self._unsub = None

        if self._api is not None:
            api = self._api
            self._api = None
            self._loop.create_task(self._async_close(api))

    async def _async_close(self, api: aioesphomeapi.APIClient) -> None:
        try:
            await api.disconnect()
        finally:
            self._protocol.connection_lost(None)

    async def flush(self) -> None:
        """Flush write buffers."""
        assert self._api is not None
        await self._api.serial_proxy_flush(instance=self._instance)

    async def get_modem_pins(self) -> ModemPins:
        """Get modem control bits."""
        assert self._api is not None
        resp = await self._api.serial_proxy_get_modem_pins(instance=self._instance)
        return ModemPins(
            dtr=PinState.convert(resp.dtr),
            rts=PinState.convert(resp.rts),
        )

    async def set_modem_pins(
        self,
        modem_pins: ModemPins | None = None,
        **kwargs,
    ) -> None:
        """Set modem control bits."""
        assert self._api is not None

        if modem_pins is None:
            modem_pins = ModemPins(
                dtr=PinState.convert(kwargs.get("dtr")),
                rts=PinState.convert(kwargs.get("rts")),
            )

        self._api.serial_proxy_set_modem_pins(
            instance=self._instance,
            dtr=modem_pins.dtr is PinState.HIGH,
            rts=modem_pins.rts is PinState.HIGH,
        )

    def get_write_buffer_size(self) -> int:
        """Get the number of bytes currently in the write buffer."""
        return 0
