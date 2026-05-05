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


@pytest.mark.parametrize(
    ("port", "expected"),
    [
        # CP2102: product and interface_description are identical
        (
            SerialPortInfo(
                device="/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_ec4903cb-if00-port0",
                resolved_device="/dev/ttyUSB0",
                vid=0x10C4,
                pid=0xEA60,
                serial_number="ec4903cb",
                manufacturer="Silicon Labs",
                product="CP2102 USB to UART Bridge Controller",
                bcd_device=0x0100,
                interface_description="CP2102 USB to UART Bridge Controller",
                interface_num=0,
            ),
            "CP2102 USB to UART Bridge Controller - CP2102 USB to UART Bridge Controller",
        ),
        # Prolific: interface_description is None, falls back to product
        (
            SerialPortInfo(
                device="/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_DSDCb147613-if00-port0",
                resolved_device="/dev/ttyUSB3",
                vid=0x067B,
                pid=0x23A3,
                serial_number="DSDCb147613",
                manufacturer="Prolific Technology Inc.",
                product="USB-Serial Controller",
                bcd_device=0x0605,
                interface_description=None,
                interface_num=0,
            ),
            "USB-Serial Controller",
        ),
        # ZBT-2: distinct product and interface_description (regression case)
        (
            SerialPortInfo(
                device="/dev/serial/by-id/usb-Nabu_Casa_ZBT-2_80B54EEFAE18-if00",
                resolved_device="/dev/ttyACM0",
                vid=0x303A,
                pid=0x4005,
                serial_number="80B54EEFAE18",
                manufacturer="Nabu Casa",
                product="ZBT-2",
                bcd_device=0x0100,
                interface_description="Nabu Casa ZBT-2",
                interface_num=0,
            ),
            "ZBT-2 - Nabu Casa ZBT-2",
        ),
        # Native UART: no USB info, falls back to basename of resolved_device
        (
            SerialPortInfo(
                device="/dev/ttyAMA0",
                resolved_device="/dev/ttyAMA0",
                vid=None,
                pid=None,
                serial_number=None,
                manufacturer=None,
                product=None,
                bcd_device=None,
                interface_description=None,
                interface_num=None,
            ),
            "ttyAMA0",
        ),
        # Bug-for-bug: interface_description without product
        (
            SerialPortInfo(
                device="/dev/ttyUSB9",
                resolved_device="/dev/ttyUSB9",
                vid=0x1234,
                pid=0x5678,
                serial_number=None,
                manufacturer=None,
                product=None,
                bcd_device=None,
                interface_description="Some Interface",
                interface_num=0,
            ),
            "None - Some Interface",
        ),
    ],
)
def test_serial_port_info_description(port: SerialPortInfo, expected: str) -> None:
    """`description` mirrors pyserial's `usb_description()` output."""
    assert port.description == expected


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
