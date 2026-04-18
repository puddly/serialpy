"""ESPHome serial port tests."""

import pytest

try:
    from aioesphomeapi import APIClient
except ImportError:
    pytest.skip(
        "aioesphomeapi is required to run esphome transport tests",
        allow_module_level=True,
    )

import asyncio
from base64 import b64encode
from unittest.mock import patch
import urllib.parse

from serialx import (
    Platform,
    SerialException,
    SerialPortInfo,
    async_list_serial_ports,
    list_serial_ports,
    open_serial_connection,
)
from serialx.platforms.serial_esphome import (
    ESPHOME_DEFAULT_PORT,
    ESPHomeSerialTransport,
)

from .common import ESPHOME_HOST_BINARY, create_esphome_pair, create_socat_pair


def base64(key: bytes) -> str:
    """Base64 encode a Noise key."""
    assert len(key) == 32

    return b64encode(key).decode("ascii")


@pytest.mark.skipif(not ESPHOME_HOST_BINARY, reason="esphome host binary not available")
async def test_externally_passed_api() -> None:
    """Test passing an ESPHome API instance externally."""
    with create_socat_pair() as (socat_left, socat_right):
        with create_esphome_pair(socat_left, socat_right) as (left, _right):
            # Connect to the ESPHome API externally
            parsed = urllib.parse.urlparse(left)
            api = APIClient(
                address=parsed.hostname,
                port=parsed.port or ESPHOME_DEFAULT_PORT,
                password=None,
            )
            await api.connect(login=True)

            # Create a serial reader/writer pair
            for _attempt in range(10):
                reader, writer = await open_serial_connection(
                    url=None,
                    transport_cls=ESPHomeSerialTransport,
                    api=api,
                    port_name="Serial Proxy Left",
                    baudrate=115200,
                )

                writer.write(b"test")
                await writer.drain()

                writer.close()
                await writer.wait_closed()

            # The API is still connected
            await api.device_info()


@pytest.mark.skipif(not ESPHOME_HOST_BINARY, reason="esphome host binary not available")
async def test_externally_passed_api_close_after_disconnect() -> None:
    """Test closing the transport after the API has been disconnected."""
    with create_socat_pair() as (socat_left, socat_right):
        with create_esphome_pair(socat_left, socat_right) as (left, _right):
            parsed = urllib.parse.urlparse(left)
            api = APIClient(
                address=parsed.hostname,
                port=parsed.port or ESPHOME_DEFAULT_PORT,
                password=None,
            )
            await api.connect(login=True)

            reader, writer = await open_serial_connection(
                url=None,
                transport_cls=ESPHomeSerialTransport,
                api=api,
                port_name="Serial Proxy Left",
                baudrate=115200,
            )

            # Disconnect the API before closing the transport
            await api.disconnect()

            writer.close()
            await writer.wait_closed()


@pytest.mark.skipif(not ESPHOME_HOST_BINARY, reason="esphome host binary not available")
async def test_connect_by_instance_id() -> None:
    """Test connecting to an ESPHome serial proxy by instance ID."""
    with create_socat_pair() as (socat_left, socat_right):
        with create_esphome_pair(socat_left, socat_right) as (left, _right):
            parsed = urllib.parse.urlparse(left)

            # Connect by instance ID instead of name, with a password
            url = f"esphome://{parsed.hostname}:{parsed.port}/0?password=unused"

            reader, writer = await open_serial_connection(
                url=url,
                baudrate=115200,
            )

            writer.write(b"test")
            await writer.drain()

            writer.close()
            await writer.wait_closed()


@pytest.mark.skipif(not ESPHOME_HOST_BINARY, reason="esphome host binary not available")
async def test_connect_by_invalid_name() -> None:
    """Test that connecting with an invalid port name raises ValueError."""
    with create_socat_pair() as (socat_left, socat_right):
        with create_esphome_pair(socat_left, socat_right) as (left, _right):
            parsed = urllib.parse.urlparse(left)
            url = f"esphome://{parsed.hostname}:{parsed.port}?port_name=Nonexistent"

            with pytest.raises(ValueError, match="does not exist"):
                await open_serial_connection(
                    url=url,
                    baudrate=115200,
                )


@pytest.mark.skipif(not ESPHOME_HOST_BINARY, reason="esphome host binary not available")
async def test_connect_plaintext_to_encrypted_server() -> None:
    """Test that connecting without encryption to an encrypted server raises."""
    with create_socat_pair() as (socat_left, socat_right):
        with create_esphome_pair(
            socat_left,
            socat_right,
            noise_psk=base64(b"A noise PSK we do not provide..."),
        ) as (left, _right):
            parsed = urllib.parse.urlparse(left)
            url = (
                f"esphome://{parsed.hostname}:{parsed.port}?port_name=Serial+Proxy+Left"
            )

            with pytest.raises(SerialException, match="Connection requires encryption"):
                await open_serial_connection(
                    url=url,
                    baudrate=115200,
                )


@pytest.mark.skipif(not ESPHOME_HOST_BINARY, reason="esphome host binary not available")
async def test_connect_encrypted_plaintext_to_server() -> None:
    """Test that connecting with encryption to an unencrypted server raises."""
    with create_socat_pair() as (socat_left, socat_right):
        with create_esphome_pair(
            socat_left,
            socat_right,
        ) as (left, _right):
            parsed = urllib.parse.urlparse(left)
            noise_psk = base64(b"An unnecessary noise PSK we use.")

            url = (
                f"esphome://{parsed.hostname}:{parsed.port}"
                f"?port_name=Serial+Proxy+Left"
                f"&key={noise_psk}"
            )

            with pytest.raises(
                SerialException, match="The device is using plaintext protocol"
            ):
                await open_serial_connection(url=url, baudrate=115200)


async def test_connect_timeout_raises_timeout_error() -> None:
    """Test that a TCP connect timeout is translated to TimeoutError."""

    with patch("aioesphomeapi.connection.TCP_CONNECT_TIMEOUT", 1.0):
        with pytest.raises(TimeoutError, match="Timeout while connecting"):
            # 192.0.2.1 is TEST-NET-1 (RFC 5737), packets are silently dropped
            await open_serial_connection(
                url="esphome://192.0.2.1:6053?port_name=test", baudrate=115200
            )


@pytest.mark.skipif(not ESPHOME_HOST_BINARY, reason="esphome host binary not available")
async def test_noise_psk_key_alias() -> None:
    """Test that connecting without encryption to an encrypted server raises."""
    key = base64(b"A noise PSK 32 bytes in length..")

    with create_socat_pair() as (socat_left, socat_right):
        with create_esphome_pair(
            socat_left,
            socat_right,
            noise_psk=key,
        ) as (left, _right):
            parsed = urllib.parse.urlparse(left)
            with pytest.raises(
                ValueError, match="Both `key` and `noise_psk` cannot be provided"
            ):
                await open_serial_connection(
                    url=f"esphome://{parsed.hostname}:{parsed.port}",
                    port_name="Serial Proxy Left",
                    noise_psk=key,
                    key=key,
                    baudrate=115200,
                )

            with pytest.raises(
                ValueError, match="Both `key` and `noise_psk` cannot be provided"
            ):
                await open_serial_connection(
                    url=f"esphome://{parsed.hostname}:{parsed.port}?key={key}&noise_psk={key}",
                    port_name="Serial Proxy Left",
                    baudrate=115200,
                )

            reader, writer = await open_serial_connection(
                url=f"esphome://{parsed.hostname}:{parsed.port}",
                port_name="Serial Proxy Left",
                noise_psk=key,  # alias
                baudrate=115200,
            )

            writer.close()
            await writer.wait_closed()


def _expected_esphome_ports(netloc: str) -> list[SerialPortInfo]:
    return [
        SerialPortInfo(
            device=f"esphome://{netloc}/?port_name=Serial+Proxy+Left",
            resolved_device=f"esphome://{netloc}/?port_name=Serial+Proxy+Left",
            vid=None,
            pid=None,
            serial_number="98:35:69:AB:F6:79",
            manufacturer="Host",
            product="host",
            bcd_device=None,
            interface_description="Serial Proxy Left",
            interface_num=None,
        ),
        SerialPortInfo(
            device=f"esphome://{netloc}/?port_name=Serial+Proxy+Right",
            resolved_device=f"esphome://{netloc}/?port_name=Serial+Proxy+Right",
            vid=None,
            pid=None,
            serial_number="98:35:69:AB:F6:79",
            manufacturer="Host",
            product="host",
            bcd_device=None,
            interface_description="Serial Proxy Right",
            interface_num=None,
        ),
    ]


@pytest.mark.skipif(not ESPHOME_HOST_BINARY, reason="esphome host binary not available")
async def test_esphome_list_serial_ports() -> None:
    """Test listing ESPHome serial ports asynchronously via an externally-passed API."""
    assert list_serial_ports(Platform.ESPHOME) == []
    assert await async_list_serial_ports(Platform.ESPHOME) == []

    with create_socat_pair() as (socat_left, socat_right):
        with create_esphome_pair(socat_left, socat_right) as (left, _right):
            parsed = urllib.parse.urlparse(left)
            api = APIClient(
                address=parsed.hostname,
                port=parsed.port or ESPHOME_DEFAULT_PORT,
                password=None,
            )
            await api.connect(login=True)

            serial_ports = await async_list_serial_ports(Platform.ESPHOME, api=api)
            assert serial_ports == _expected_esphome_ports(parsed.netloc)


@pytest.mark.skipif(not ESPHOME_HOST_BINARY, reason="esphome host binary not available")
async def test_async_esphome_list_serial_ports_via_uri() -> None:
    """Test listing ESPHome serial ports asynchronously via a URI."""
    with create_socat_pair() as (socat_left, socat_right):
        with create_esphome_pair(socat_left, socat_right) as (left, _right):
            parsed = urllib.parse.urlparse(left)

            serial_ports = await async_list_serial_ports(Platform.ESPHOME, path=left)
            assert serial_ports == _expected_esphome_ports(parsed.netloc)


@pytest.mark.skipif(not ESPHOME_HOST_BINARY, reason="esphome host binary not available")
def test_sync_esphome_list_serial_ports_via_uri() -> None:
    """Test listing ESPHome serial ports synchronously via a URI."""
    assert list_serial_ports(Platform.ESPHOME) == []

    with create_socat_pair() as (socat_left, socat_right):
        with create_esphome_pair(socat_left, socat_right) as (left, _right):
            parsed = urllib.parse.urlparse(left)

            serial_ports = list_serial_ports(Platform.ESPHOME, path=left)
            assert serial_ports == _expected_esphome_ports(parsed.netloc)


@pytest.mark.skipif(not ESPHOME_HOST_BINARY, reason="esphome host binary not available")
async def test_sync_esphome_list_serial_ports_external_api() -> None:
    """Test sync listing of ESPHome serial ports with an externally-passed API."""
    test_loop = asyncio.get_running_loop()

    with create_socat_pair() as (socat_left, socat_right):
        with create_esphome_pair(socat_left, socat_right) as (left, _right):
            parsed = urllib.parse.urlparse(left)
            api = APIClient(
                address=parsed.hostname,
                port=parsed.port or ESPHOME_DEFAULT_PORT,
                password=None,
            )
            await api.connect(login=True)

            serial_ports = await asyncio.to_thread(
                list_serial_ports, Platform.ESPHOME, api=api, loop=test_loop
            )
            assert serial_ports == _expected_esphome_ports(parsed.netloc)

            # The externally-passed API must remain connected.
            await api.device_info()
            await api.disconnect()
