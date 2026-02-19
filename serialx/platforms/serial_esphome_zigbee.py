"""ESPHome Zigbee proxy transport."""

from __future__ import annotations

import asyncio
from collections.abc import Buffer, Callable
import logging
from pathlib import Path
import urllib.parse

from aioesphomeapi import APIClient
from aioesphomeapi.model import ZigbeeProxyFrame, ZigbeeProxyRequestType

from serialx.common import BaseSerial, BaseSerialTransport, Parity, StopBits

LOGGER = logging.getLogger(__name__)

ESPHOME_DEFAULT_PORT = 6053


class ESPHomeZigbeeSerial(BaseSerial):
    """Synchronous serial interface over ESPHome Zigbee proxy API.

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
        """Initialize ESPHome Zigbee serial port."""
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

    def _on_data(self, msg: ZigbeeProxyFrame) -> None:
        self._read_buffer.extend(msg.data)
        self._read_event.set()

    def open(self) -> None:
        """Open the serial port."""
        asyncio.run(self._async_open())
        assert self.api is not None
        self._unsub = self.api.subscribe_zigbee_proxy_frame(self._on_data)
        self.api.send_zigbee_proxy_request(ZigbeeProxyRequestType.SUBSCRIBE)

    async def _async_open(self) -> None:
        self.api = APIClient(
            self._host,
            self._port,
            password=self._password,
            noise_psk=self._noise_psk,
        )
        await self.api.connect(login=True)

    def configure_port(self) -> None:
        """Configure the serial port settings (no-op for Zigbee proxy)."""

    def _set_modem_pins(self, modem_pins) -> None:
        pass

    def _get_modem_pins(self):
        from serialx.common import ModemPins

        return ModemPins()

    def flush(self) -> None:
        """Flush write buffers (no-op for Zigbee proxy)."""

    def write(self, b: Buffer) -> int:
        """Write bytes to serial port."""
        assert self.api is not None
        data = bytes(b)
        self.api.send_zigbee_proxy_frame(data)
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

    def close(self) -> None:
        """Close the serial port."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

        if self.api is not None:
            self.api.send_zigbee_proxy_request(ZigbeeProxyRequestType.UNSUBSCRIBE)
            asyncio.run(self.api.disconnect())
            self.api = None


class ESPHomeZigbeeTransport(BaseSerialTransport):
    """Serial transport over ESPHome Zigbee proxy API."""

    transport_name = "esphome_zigbee"
    _serial: ESPHomeZigbeeSerial

    def __init__(
        self, loop: asyncio.AbstractEventLoop, protocol: asyncio.Protocol
    ) -> None:
        """Initialize the ESPHome Zigbee transport."""
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
        self._serial = ESPHomeZigbeeSerial(
            path=path,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            xonxoff=xonxoff,
            rtscts=rtscts,
            byte_size=byte_size,
        )

        await self._serial._async_open()

        assert self._serial.api is not None
        self._unsub = self._serial.api.subscribe_zigbee_proxy_frame(self._on_data)
        self._serial.api.send_zigbee_proxy_request(ZigbeeProxyRequestType.SUBSCRIBE)

        self._protocol.connection_made(self)

    def _on_data(self, msg: ZigbeeProxyFrame) -> None:
        self._protocol.data_received(msg.data)

    def write(self, data: bytes | bytearray | memoryview) -> None:
        """Write data to the Zigbee proxy."""
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
            api = self._serial.api
            self._serial.api = None
            self._loop.create_task(self._async_close(api))

    async def _async_close(self, api: APIClient) -> None:
        """Close the API connection."""
        try:
            api.send_zigbee_proxy_request(ZigbeeProxyRequestType.UNSUBSCRIBE)
            await api.disconnect()
        finally:
            self._protocol.connection_lost(None)

    def get_write_buffer_size(self) -> int:
        """Get the number of bytes currently in the write buffer."""
        return 0
