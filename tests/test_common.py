"""Tests for the URI handler registration API."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import Mock

import pytest

from serialx import (
    Platform,
    async_list_serial_ports,
    get_serial_classes,
    list_serial_ports,
    register_uri_handler,
)
from serialx.common import (
    _REGISTERED_URI_HANDLERS,
    BaseSerial,
    BaseSerialTransport,
    SerialPortInfo,
    UnknownUriScheme,
    get_uri_handler,
)


async def _async_list_serial_ports() -> list[SerialPortInfo]:
    return []


@pytest.fixture(autouse=True)
def ensure_registry_untouched() -> Generator[None]:
    """Snapshot the URI handler registry and restore it after each test."""
    snapshot = {
        scheme: list(items)
        for scheme, items in _REGISTERED_URI_HANDLERS.items()
        if items
    }

    try:
        yield
    finally:
        after = {
            scheme: list(items)
            for scheme, items in _REGISTERED_URI_HANDLERS.items()
            if items
        }
        if after != snapshot:
            pytest.fail(
                f"URI handlers were leaked by the test! Before: {snapshot}, after: {after}"
            )


def test_register_uri_handler_validation() -> None:
    """`register_uri_handler` rejects bad schemes and duplicate registrations."""
    with pytest.raises(ValueError, match="must end with"):
        register_uri_handler(
            scheme="bad",
            unique_scheme="test-unique-1://",
            sync_cls=BaseSerial,  # type:ignore[type-abstract]
            async_transport_cls=BaseSerialTransport,  # type:ignore[type-abstract]
            list_serial_ports_func=list,
            async_list_serial_ports_func=_async_list_serial_ports,
        )

    with pytest.raises(ValueError, match="must end with"):
        register_uri_handler(
            scheme="test-shared-1://",
            unique_scheme="bad",
            sync_cls=BaseSerial,  # type:ignore[type-abstract]
            async_transport_cls=BaseSerialTransport,  # type:ignore[type-abstract]
            list_serial_ports_func=list,
            async_list_serial_ports_func=_async_list_serial_ports,
        )

    unregister = register_uri_handler(
        scheme="test-shared-1://",
        unique_scheme="test-unique-1://",
        sync_cls=BaseSerial,  # type:ignore[type-abstract]
        async_transport_cls=BaseSerialTransport,  # type:ignore[type-abstract]
        list_serial_ports_func=list,
        async_list_serial_ports_func=_async_list_serial_ports,
    )

    try:
        with pytest.raises(ValueError, match="not unique"):
            register_uri_handler(
                scheme="test-shared-1://",
                unique_scheme="test-unique-1://",
                sync_cls=BaseSerial,  # type:ignore[type-abstract]
                async_transport_cls=BaseSerialTransport,  # type:ignore[type-abstract]
                list_serial_ports_func=list,
                async_list_serial_ports_func=_async_list_serial_ports,
            )
    finally:
        unregister()


def test_register_uri_handler_dispatch_and_unregister() -> None:
    """Registered handlers are discoverable via the public sync/async APIs."""
    mock_sync_cls = Mock(spec=type[BaseSerial])
    mock_async_transport_cls = Mock(spec=type[BaseSerialTransport])

    unregister = register_uri_handler(
        scheme="test-shared-2://",
        unique_scheme="test-unique-2://",
        sync_cls=mock_sync_cls,
        async_transport_cls=mock_async_transport_cls,
        list_serial_ports_func=list,
        async_list_serial_ports_func=_async_list_serial_ports,
    )

    for url in ("test-unique-2://", "test-shared-2://host/path"):
        handler = get_uri_handler(url)
        assert handler.sync_cls is mock_sync_cls
        assert handler.async_transport_cls is mock_async_transport_cls

        sync_cls, async_transport_cls = get_serial_classes(url)
        assert sync_cls is mock_sync_cls
        assert async_transport_cls is mock_async_transport_cls

    unregister()

    with pytest.raises(UnknownUriScheme):
        get_uri_handler("test-unique-2://")

    with pytest.raises(UnknownUriScheme):
        get_uri_handler("test-shared-2://")


@pytest.mark.parametrize("platform", list(Platform))
def test_list_serial_ports_all_platforms(platform: Platform) -> None:
    """Sync listing returns a list of `SerialPortInfo` for every platform."""
    try:
        ports = list_serial_ports(platform)
    except UnknownUriScheme:
        pytest.skip(f"{platform} is not registered in this environment")
        return

    assert isinstance(ports, list)
    for port in ports:
        assert isinstance(port, SerialPortInfo)


@pytest.mark.parametrize("platform", list(Platform))
async def test_async_list_serial_ports_all_platforms(platform: Platform) -> None:
    """Async listing returns a list of `SerialPortInfo` for every platform."""
    try:
        ports = await async_list_serial_ports(platform)
    except UnknownUriScheme:
        pytest.skip(f"{platform} is not registered in this environment")
        return

    assert isinstance(ports, list)
    for port in ports:
        assert isinstance(port, SerialPortInfo)
