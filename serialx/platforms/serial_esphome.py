"""ESPHome serial proxy transport."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import suppress
from enum import IntFlag
import functools
import logging
import threading
from typing import Any, TypeVar, cast
import urllib.parse

import aioesphomeapi
from aioesphomeapi import APIClient, SerialProxyDataReceived, SerialProxyParity
from aioesphomeapi.core import (
    APIConnectionError,
    PingRequest,
    PingResponse,
    TimeoutAPIError,
)
from typing_extensions import Buffer

from serialx import SerialException, UnsupportedSetting
from serialx.common import (
    BaseSerial,
    BaseSerialTransport,
    ModemPins,
    Parity,
    PinState,
    StopBits,
)

_T = TypeVar("_T")
_F = TypeVar("_F", bound=Callable[..., Any])

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


def translate_esphome_errors(func: _F) -> _F:
    """Translate aioesphomeapi errors into standard serialx exceptions."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except TimeoutAPIError as exc:
            raise TimeoutError(str(exc)) from exc
        except APIConnectionError as exc:
            raise SerialException(str(exc)) from exc

    return cast(_F, wrapper)


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
        *args,
        connect_timeout: float = 10.0,
        loop: asyncio.AbstractEventLoop | None = None,
        api: APIClient | None = None,
        port_name: str | None = None,
        port_instance: int | None = None,
        **kwargs,
    ) -> None:
        """Initialize ESPHome serial port."""
        super().__init__(*args, **kwargs)

        if self._parity not in PARITY_MAP:
            raise UnsupportedSetting(f"Unsupported parity: {self._parity}")

        if self._stopbits not in STOP_BITS_MAP:
            raise UnsupportedSetting(f"Unsupported stop bits: {self._stopbits}")

        # This API is used by both the sync API and the async API. The sync API manages
        # a temporary event loop while the async one passes through its own.
        self._loop = loop
        self._loop_thread: threading.Thread | None = None
        self._connect_timeout = connect_timeout

        self._api: APIClient | None = api
        self._port_name: str | None = port_name
        self._instance_id: int | None = port_instance
        self._disconnect_api: bool = False

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
        if msg.instance == self._instance_id:
            self._read_buffer.extend(msg.data)
            self._read_event.set()

    def _open(self) -> None:
        """Open the serial port."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(target=self._loop.run_forever)
            self._loop_thread.start()

        self._read_event = asyncio.Event()
        self._call_on_loop(self._async_open())
        self._call_on_loop(self._async_subscribe())

    @property
    def is_open(self) -> bool:
        """Return whether the serial port is open."""
        return self._api is not None

    @translate_esphome_errors
    async def _async_open(self) -> None:
        # Only connect if the API was not passed in externally
        if self._api is None:
            assert self._path is not None
            parsed = urllib.parse.urlparse(str(self._path))
            params = urllib.parse.parse_qs(parsed.query)

            if "port_name" in params:
                port_value = params["port_name"][0]
            else:
                # Backwards compat: esphome://host:port/{instance_id}
                port_value = urllib.parse.unquote(parsed.path.strip("/"))

            if port_value.isdigit():
                self._instance_id = int(port_value)
            else:
                self._port_name = port_value

            self._api = aioesphomeapi.APIClient(
                address=parsed.hostname,
                port=parsed.port or ESPHOME_DEFAULT_PORT,
                password=params["password"][0] if "password" in params else None,
                noise_psk=params["noise_psk"][0] if "noise_psk" in params else None,
            )

            self._disconnect_api = True
            await self._api.connect(login=True)
        else:
            # Don't disconnect an externally-passed API
            self._disconnect_api = False

    @translate_esphome_errors
    async def _async_subscribe(self) -> None:
        assert self._api is not None
        await self._subscribe_instance()
        self._unsub = self._api.subscribe_serial_proxy_data(self._on_data)

    async def _ping(self, *, timeout: float) -> None:
        """Ping the ESPHome API."""
        assert self._api is not None
        conn = self._api._get_connection()

        await conn.send_messages_await_response_complex(
            messages=(PingRequest(),),
            do_append=None,
            do_stop=lambda msg: isinstance(msg, PingResponse),
            msg_types=(PingResponse,),
            timeout=timeout,
        )

    async def _subscribe_instance(self) -> None:
        """Subscribe serial proxy streaming for this instance if supported."""
        if self._api is None or self._instance_subscribed:
            return

        info = await self._api.device_info()

        if self._instance_id is None:
            assert self._port_name is not None

            name_to_info_mapping = {
                proxy_info.name: (index, proxy_info)
                for index, proxy_info in enumerate(info.serial_proxies)
            }

            if self._port_name not in name_to_info_mapping:
                raise ValueError(
                    f"Serial proxy with name {self._port_name!r}"
                    f" does not exist in {name_to_info_mapping!r}"
                )

            instance_id, _proxy_info = name_to_info_mapping[self._port_name]
            self._instance_id = instance_id

        self._api.serial_proxy_subscribe(self._instance_id)

        # Ping to ensure the daemon has processed the subscribe
        await self._ping(timeout=self._connect_timeout)

        self._instance_subscribed = True

    def _unsubscribe_instance(self) -> None:
        """Unsubscribe serial proxy streaming for this instance if supported."""
        if self._api is None or not self._instance_subscribed:
            return

        with suppress(APIConnectionError):
            self._api.serial_proxy_unsubscribe(self._instance_id)

        self._instance_subscribed = False

    def _configure_port(self) -> None:
        """Configure the serial port settings."""
        assert self._api is not None
        self._api.serial_proxy_configure(
            instance=self._instance_id,
            baudrate=self._baudrate,
            flow_control=self._rtscts,
            parity=PARITY_MAP[self._parity],
            stop_bits=STOP_BITS_MAP[self._stopbits],
            data_size=self._byte_size,
        )

    def _set_modem_pins(self, modem_pins: ModemPins) -> None:
        """Set modem control bits."""
        self._call_on_loop(self._async_set_modem_pins(modem_pins))

    @translate_esphome_errors
    async def _async_set_modem_pins(self, modem_pins: ModemPins) -> None:
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
            instance=self._instance_id,
            line_states=self._last_line_state,
        )

        await self._async_get_modem_pins()

    def _get_modem_pins(self) -> ModemPins:
        return self._call_on_loop(self._async_get_modem_pins())

    @translate_esphome_errors
    async def _async_get_modem_pins(self) -> ModemPins:
        assert self._api is not None
        rsp = await self._api.serial_proxy_get_modem_pins(instance=self._instance_id)
        self._last_line_state = rsp.line_states

        return ModemPins(
            dtr=PinState.convert(rsp.line_states & LineStateFlag.DTR),
            rts=PinState.convert(rsp.line_states & LineStateFlag.RTS),
        )

    def num_unread_bytes(self) -> int:
        """Return the number of bytes waiting to be read."""
        return len(self._read_buffer)

    def num_unwritten_bytes(self) -> int:
        """Return the number of bytes waiting to be written."""
        return 0

    def reset_read_buffer(self) -> None:
        """Reset the read buffer."""
        self._read_buffer.clear()

    def reset_write_buffer(self) -> None:
        """Reset the write buffer."""

    @translate_esphome_errors
    async def _async_flush(self) -> None:
        """Flush write buffers."""
        assert self._api is not None
        await self._api.serial_proxy_flush(instance=self._instance_id)

    def flush(self) -> None:
        """Flush write buffers."""
        self._call_on_loop(self._async_flush())

    def _write(self, b: Buffer, *, timeout: float | None) -> int:
        """Write bytes to serial port."""
        assert self._api is not None
        data = bytes(b)
        self._api.serial_proxy_write(instance=self._instance_id, data=data)
        return len(data)

    def _readinto(self, b: Buffer, *, timeout: float | None) -> int:
        """Read bytes from serial port into buffer."""
        try:
            return self._call_on_loop(self._async_readinto(b, timeout))
        except TimeoutError:
            return 0

    async def _async_readinto(self, b: Buffer, timeout: float | None) -> int:
        async with asyncio.timeout(timeout):  # type: ignore[attr-defined]
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

        if self._disconnect_api and self._api is not None:
            self._unsubscribe_instance()
            self._call_on_loop(self._api.disconnect())
            self._api = None

        if self._loop_thread is not None:
            assert self._loop is not None
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join()
            self._loop.close()
            self._loop = None
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

    @translate_esphome_errors
    async def _connect(self, **kwargs) -> None:
        self._serial = ESPHomeSerial(loop=self._loop, **kwargs)
        self._extra["serial"] = self._serial

        await self._serial._async_open()
        self._serial.configure_port()

        assert self._serial._api is not None
        await self._serial._subscribe_instance()
        self._unsub = self._serial._api.subscribe_serial_proxy_data(self._on_data)

        self._protocol.connection_made(self)

    def _on_data(self, msg: SerialProxyDataReceived) -> None:
        if msg.instance == self._serial._instance_id:
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

        self._serial._unsubscribe_instance()

        if self._serial._disconnect_api:
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
        await self._serial._async_flush()

    async def get_modem_pins(self) -> ModemPins:
        """Get modem control bits."""
        return await self._serial._async_get_modem_pins()

    def get_write_buffer_size(self) -> int:
        """Get the number of bytes currently in the write buffer."""
        return 0
