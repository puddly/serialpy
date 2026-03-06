"""Tests for ESPHome serial transport behavior."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aioesphomeapi")

from serialx.platforms import serial_esphome


class _DummyAPIClient:
    """Simple API client test double."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.connected = False
        self.disconnected = False
        self.serial_proxy_configure_calls: list[dict] = []
        self.serial_proxy_subscribe_calls: list[int] = []
        self.serial_proxy_unsubscribe_calls: list[int] = []
        self.stream_subscribed = False
        self.stream_unsubscribed = False

    async def connect(self, *, login: bool = True) -> None:
        self.connected = login

    async def disconnect(self) -> None:
        self.disconnected = True

    def serial_proxy_configure(self, **kwargs) -> None:
        self.serial_proxy_configure_calls.append(kwargs)

    def serial_proxy_subscribe(self, instance: int) -> None:
        self.serial_proxy_subscribe_calls.append(instance)

    def serial_proxy_unsubscribe(self, instance: int) -> None:
        self.serial_proxy_unsubscribe_calls.append(instance)

    def subscribe_serial_proxy_data(self, _callback):
        self.stream_subscribed = True

        def _unsub() -> None:
            self.stream_unsubscribed = True

        return _unsub

    def serial_proxy_write(self, *, instance: int, data: bytes) -> None:
        _ = (instance, data)

    async def serial_proxy_flush(self, *, instance: int) -> None:
        _ = instance


class _DummyAPIClientNoInstanceSubscription:
    """API client without instance subscribe APIs (older aioesphomeapi)."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.connected = False
        self.disconnected = False
        self.stream_subscribed = False
        self.stream_unsubscribed = False

    async def connect(self, *, login: bool = True) -> None:
        self.connected = login

    async def disconnect(self) -> None:
        self.disconnected = True

    def serial_proxy_configure(self, **_kwargs) -> None:
        return

    def subscribe_serial_proxy_data(self, _callback):
        self.stream_subscribed = True

        def _unsub() -> None:
            self.stream_unsubscribed = True

        return _unsub

    def serial_proxy_write(self, *, instance: int, data: bytes) -> None:
        _ = (instance, data)

    async def serial_proxy_flush(self, *, instance: int) -> None:
        _ = instance


@pytest.mark.asyncio
async def test_transport_subscribes_instance_and_unsubscribes_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure instance subscription is managed with the transport lifecycle."""
    api = _DummyAPIClient()
    monkeypatch.setattr(serial_esphome.aioesphomeapi, "APIClient", lambda *_a, **_k: api)

    loop = asyncio.get_running_loop()
    protocol = asyncio.Protocol()
    transport = serial_esphome.ESPHomeSerialTransport(loop=loop, protocol=protocol)

    await transport.connect(url="esphome://example-host/1", baudrate=9600)

    assert api.connected
    assert api.stream_subscribed
    assert api.serial_proxy_subscribe_calls == [1]

    transport.close()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert api.stream_unsubscribed
    assert api.serial_proxy_unsubscribe_calls == [1]
    assert api.disconnected


@pytest.mark.asyncio
async def test_transport_works_without_instance_subscribe_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep compatibility when aioesphomeapi lacks instance subscribe methods."""
    api = _DummyAPIClientNoInstanceSubscription()
    monkeypatch.setattr(serial_esphome.aioesphomeapi, "APIClient", lambda *_a, **_k: api)

    loop = asyncio.get_running_loop()
    protocol = asyncio.Protocol()
    transport = serial_esphome.ESPHomeSerialTransport(loop=loop, protocol=protocol)

    await transport.connect(url="esphome://example-host/2", baudrate=9600)
    transport.close()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert api.connected
    assert api.stream_subscribed
    assert api.stream_unsubscribed
    assert api.disconnected
