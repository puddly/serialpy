"""ESPHome serial proxy transport."""

from __future__ import annotations

import asyncio
from collections.abc import Buffer, Callable
import logging
from pathlib import Path
import urllib.parse

from aioesphomeapi import APIClient, SerialProxyDataReceived, SerialProxyParity

from serialx.common import (
    BaseSerial,
    BaseSerialTransport,
    ModemPins,
    Parity,
    PinState,
    StopBits,
)

LOGGER = logging.getLogger(__name__)

ESPHOME_DEFAULT_PORT = 6053

PARITY_MAP = {
    Parity.NONE: SerialProxyParity.NONE,
    Parity.EVEN: SerialProxyParity.EVEN,
    Parity.ODD: SerialProxyParity.ODD,
}

STOP_BITS_MAP = {
    StopBits.ONE: 1,
    StopBits.TWO: 2,
}


class ESPHomeSerial(BaseSerial):
    """Synchronous serial interface over ESPHome serial proxy API.

    ESPHome does not have a native synchronous API, using this interface is heavily
    discouraged. Please use the async API.
    """

    def __init__(
        self,
        path: str | Path,
        baudrate: int,
        parity: Parity = Parity.NONE,
        stopbits: StopBits = StopBits.ONE,
        xonxoff: bool = False,
        rtscts: bool = False,
        byte_size: int = 8,
        **kwargs,
    ) -> None:
        """Initialize ESPHome serial port."""
        super().__init__(
            path=path,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            byte_size=byte_size,
            **kwargs,
        )

        parsed = urllib.parse.urlparse(str(path))
        params = urllib.parse.parse_qs(parsed.query)

        self._host = parsed.hostname
        self._port = parsed.port or ESPHOME_DEFAULT_PORT

        path_str = parsed.path.strip("/")
        self.instance = int(path_str) if path_str else 0

        self._password = params["password"][0] if "password" in params else None
        self._noise_psk = params["noise_psk"][0] if "noise_psk" in params else None

        self.api: APIClient | None = None
        self._read_buffer = bytearray()
        self._read_event = asyncio.Event()
        self._unsub: Callable[[], None] | None = None
        self._instance_subscribed = False

    def _on_data(self, msg: SerialProxyDataReceived) -> None:
        if msg.instance == self.instance:
            self._read_buffer.extend(msg.data)
            self._read_event.set()

    def _open(self) -> None:
        """Open the serial port."""
        asyncio.run(self._async_open())
        assert self.api is not None
        self._subscribe_instance()
        self._unsub = self.api.subscribe_serial_proxy_data(self._on_data)

    async def _async_open(self) -> None:
        self.api = APIClient(
            self._host,
            self._port,
            password=self._password,
            noise_psk=self._noise_psk,
        )
        await self.api.connect(login=True)

    def _subscribe_instance(self) -> None:
        """Subscribe serial proxy streaming for this instance if supported."""
        if self.api is None or self._instance_subscribed:
            return
        self.api.serial_proxy_subscribe(self.instance)
        self._instance_subscribed = True

    def _unsubscribe_instance(self) -> None:
        """Unsubscribe serial proxy streaming for this instance if supported."""
        if self.api is None or not self._instance_subscribed:
            return
        self.api.serial_proxy_unsubscribe(self.instance)
        self._instance_subscribed = False

    def _configure_port(self) -> None:
        """Configure the serial port settings."""
        assert self.api is not None
        self.api.serial_proxy_configure(
            instance=self.instance,
            baudrate=self._baudrate,
            flow_control=self._rtscts,
            parity=PARITY_MAP[self._parity],
            stop_bits=STOP_BITS_MAP[self._stopbits],
            data_size=self._byte_size,
        )

    def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        assert self.api is not None
        self.api.serial_proxy_set_modem_pins(
            instance=self.instance,
            rts=modem_pins.rts is PinState.HIGH,
            dtr=modem_pins.dtr is PinState.HIGH,
        )

    def _get_modem_pins(self) -> ModemPins:
        return asyncio.run(self._async_get_modem_pins())

    async def _async_get_modem_pins(self) -> ModemPins:
        assert self.api is not None
        resp = await self.api.serial_proxy_get_modem_pins(instance=self.instance)
        return ModemPins(
            dtr=PinState.convert(resp.dtr),
            rts=PinState.convert(resp.rts),
        )

    def flush(self) -> None:
        """Flush write buffers."""
        assert self.api is not None
        self.api.serial_proxy_flush(instance=self.instance)

    def write(self, b: Buffer) -> int:
        """Write bytes to serial port."""
        assert self.api is not None
        data = bytes(b)
        self.api.serial_proxy_write(instance=self.instance, data=data)
        return len(data)

    def readinto(self, b: Buffer) -> int:
        """Read bytes from serial port into buffer."""
        return asyncio.run(self._async_readinto(b))

    async def _async_readinto(self, b: Buffer) -> int:
        while not self._read_buffer:
            self._read_event.clear()
            await self._read_event.wait()

        m = memoryview(b).cast("B")
        n = min(len(m), len(self._read_buffer))
        m[:n] = self._read_buffer[:n]
        del self._read_buffer[:n]
        return n

    def _close(self) -> None:
        """Close the serial port."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

        if self.api is not None:
            self._unsubscribe_instance()
            asyncio.run(self.api.disconnect())
            self.api = None


class ESPHomeSerialTransport(BaseSerialTransport):
    """Serial transport over ESPHome serial proxy API."""

    transport_name = "esphome"
    _serial: ESPHomeSerial

    def __init__(
        self, loop: asyncio.AbstractEventLoop, protocol: asyncio.Protocol
    ) -> None:
        """Initialize the ESPHome serial transport."""
        super().__init__(loop, protocol)
        self._unsub: Callable[[], None] | None = None

    async def _connect(  # type: ignore[override]
        self,
        *,
        path: str,
        baudrate: int,
        parity: Parity = Parity.NONE,
        stopbits: StopBits = StopBits.ONE,
        xonxoff: bool = False,
        rtscts: bool = False,
        byte_size: int = 8,
        **kwargs,
    ) -> None:
        self._serial = ESPHomeSerial(
            path=path,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            byte_size=byte_size,
        )

        await self._serial._async_open()
        self._serial.configure_port()

        assert self._serial.api is not None
        self._serial._subscribe_instance()
        self._unsub = self._serial.api.subscribe_serial_proxy_data(self._on_data)

        self._protocol.connection_made(self)

    def _on_data(self, msg: SerialProxyDataReceived) -> None:
        if msg.instance == self._serial.instance:
            self._protocol.data_received(msg.data)

    def write(self, data: bytes | bytearray | memoryview) -> None:
        """Write data to the serial proxy."""
        self._serial.write(data)

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

        if self._serial is not None and self._serial.api is not None:
            self._serial._unsubscribe_instance()
            api = self._serial.api
            self._serial.api = None
            self._loop.create_task(self._async_close(api))

    async def _async_close(self, api: APIClient) -> None:
        """Close the API connection."""
        try:
            await api.disconnect()
        finally:
            self._protocol.connection_lost(None)

    async def flush(self) -> None:
        """Flush write buffers."""
        assert self._serial is not None
        # TODO: this needs to block
        self._serial.flush()

    async def get_modem_pins(self) -> ModemPins:
        """Get modem control bits."""
        return await self._serial._async_get_modem_pins()

    def get_write_buffer_size(self) -> int:
        """Get the number of bytes currently in the write buffer."""
        return 0
