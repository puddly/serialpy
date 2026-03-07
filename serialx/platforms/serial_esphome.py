"""ESPHome serial proxy transport."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from enum import IntFlag
import logging
from pathlib import Path
import sys
import threading
from typing import Any, TypeVar
import urllib.parse

from typing_extensions import Buffer

if sys.version_info >= (3, 11):
    from asyncio import timeout as asyncio_timeout
else:
    from async_timeout import timeout as asyncio_timeout

import aioesphomeapi
from aioesphomeapi import APIClient, SerialProxyDataReceived, SerialProxyParity

from serialx import UnsupportedSetting
from serialx.common import (
    BaseSerial,
    BaseSerialTransport,
    ModemPins,
    Parity,
    PinState,
    StopBits,
)

_T = TypeVar("_T")

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


class LineStateFlag(IntFlag):
    """Bitmap of serial line states."""

    RTS = 1
    DTR = 2


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
        *,
        loop: asyncio.AbstractEventLoop | None = None,
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

        if self._parity not in PARITY_MAP:
            raise UnsupportedSetting(f"Unsupported parity: {self._parity}")

        if self._stopbits not in STOP_BITS_MAP:
            raise UnsupportedSetting(f"Unsupported stop bits: {self._stopbits}")

        # This API is used by both the sync API and the async API. The sync API manages
        # a temporary event loop while the async one passes through its own.
        self._loop = loop
        self._loop_thread: threading.Thread | None = None

        parsed = urllib.parse.urlparse(str(self._path))
        params = urllib.parse.parse_qs(parsed.query)

        self._host = parsed.hostname
        self._port = parsed.port or ESPHOME_DEFAULT_PORT

        path_str = parsed.path.strip("/")
        self.instance = int(path_str) if path_str else 0

        self._password = params["password"][0] if "password" in params else None
        self._noise_psk = params["noise_psk"][0] if "noise_psk" in params else None

        self._api: APIClient | None = None
        self._read_buffer = bytearray()
        self._read_event = asyncio.Event()
        self._unsub: Callable[[], None] | None = None
        self._instance_subscribed = False

        self._last_line_state = LineStateFlag(0)

    def _call_on_loop(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Dispatch a coroutine to the event loop thread and block."""
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def _on_data(self, msg: SerialProxyDataReceived) -> None:
        if msg.instance == self.instance:
            self._read_buffer.extend(msg.data)
            self._read_event.set()

    def _open(self) -> None:
        """Open the serial port."""
        self._loop = asyncio.new_event_loop()
        self._read_event = asyncio.Event()
        self._loop_thread = threading.Thread(target=self._loop.run_forever)
        self._loop_thread.start()

        self._call_on_loop(self._async_open())
        self._call_on_loop(self._async_subscribe())

    async def _async_open(self) -> None:
        self._api = aioesphomeapi.APIClient(
            self._host,
            self._port,
            password=self._password,
            noise_psk=self._noise_psk,
        )
        await self._api.connect(login=True)

    async def _async_subscribe(self) -> None:
        assert self._api is not None
        self._subscribe_instance()
        self._unsub = self._api.subscribe_serial_proxy_data(self._on_data)

    def _subscribe_instance(self) -> None:
        """Subscribe serial proxy streaming for this instance if supported."""
        if self._api is None or self._instance_subscribed:
            return
        self._api.serial_proxy_subscribe(self.instance)
        self._instance_subscribed = True

    def _unsubscribe_instance(self) -> None:
        """Unsubscribe serial proxy streaming for this instance if supported."""
        if self._api is None or not self._instance_subscribed:
            return
        self._api.serial_proxy_unsubscribe(self.instance)
        self._instance_subscribed = False

    def _configure_port(self) -> None:
        """Configure the serial port settings."""
        assert self._api is not None
        self._api.serial_proxy_configure(
            instance=self.instance,
            baudrate=self._baudrate,
            flow_control=self._rtscts,
            parity=PARITY_MAP[self._parity],
            stop_bits=STOP_BITS_MAP[self._stopbits],
            data_size=self._byte_size,
        )

    def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        assert self._api is not None
        line_states = self._last_line_state

        if modem_pins.rts is PinState.HIGH:
            line_states |= LineStateFlag.RTS
        elif modem_pins.rts is PinState.LOW:
            line_states &= ~LineStateFlag.RTS

        if modem_pins.dtr is PinState.HIGH:
            line_states |= LineStateFlag.DTR
        elif modem_pins.dtr is PinState.LOW:
            line_states &= ~LineStateFlag.DTR

        self._last_line_state = line_states
        self._api.serial_proxy_set_modem_pins(
            instance=self.instance,
            line_states=self._last_line_state,
        )

    def _get_modem_pins(self) -> ModemPins:
        return self._call_on_loop(self._async_get_modem_pins())

    async def _async_get_modem_pins(self) -> ModemPins:
        assert self._api is not None
        rsp = await self._api.serial_proxy_get_modem_pins(instance=self.instance)
        self._last_line_state = rsp.line_states

        return ModemPins(
            dtr=PinState.convert(rsp.line_states & LineStateFlag.DTR),
            rts=PinState.convert(rsp.line_states & LineStateFlag.RTS),
        )

    def flush(self) -> None:
        """Flush write buffers."""
        assert self._api is not None
        self._api.serial_proxy_flush(instance=self.instance)

    def write(self, b: Buffer) -> int:
        """Write bytes to serial port."""
        assert self._api is not None
        data = bytes(b)
        self._api.serial_proxy_write(instance=self.instance, data=data)
        return len(data)

    def readinto(self, b: Buffer) -> int:
        """Read bytes from serial port into buffer."""
        try:
            return self._call_on_loop(self._async_readinto(b, self._read_timeout))
        except TimeoutError:
            return 0

    async def _async_readinto(self, b: Buffer, timeout: float | None) -> int:
        async with asyncio_timeout(timeout):
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

        if self._api is not None:
            self._unsubscribe_instance()
            self._call_on_loop(self._api.disconnect())
            self._api = None

        if self._loop_thread is not None:
            assert self._loop is not None
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join()
            self._loop.close()
            self._loop_thread = None


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

    async def _connect(self, **kwargs) -> None:
        self._serial = ESPHomeSerial(loop=self._loop, **kwargs)

        await self._serial._async_open()
        self._serial.configure_port()

        assert self._serial._api is not None
        self._serial._subscribe_instance()
        self._unsub = self._serial._api.subscribe_serial_proxy_data(self._on_data)

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

        if self._serial is not None and self._serial._api is not None:
            self._serial._unsubscribe_instance()
            api = self._serial._api
            self._serial._api = None
            self._loop.create_task(self._async_close(api))
        else:
            self._call_protocol_connection_lost(None)

    def abort(self) -> None:
        """Abort the transport immediately."""
        self.close()

    async def _async_close(self, api: APIClient) -> None:
        """Close the API connection."""
        try:
            await api.disconnect()
        finally:
            self._call_protocol_connection_lost(None)

    async def flush(self) -> None:
        """Flush write buffers."""
        self._serial.flush()

    async def get_modem_pins(self) -> ModemPins:
        """Get modem control bits."""
        return await self._serial._async_get_modem_pins()

    def get_write_buffer_size(self) -> int:
        """Get the number of bytes currently in the write buffer."""
        return 0
