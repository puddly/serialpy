"""ESPHome serial port tests."""

import pytest

try:
    from aioesphomeapi import APIClient
except ImportError:
    pytest.skip(
        "aioesphomeapi is required to run esphome transport tests",
        allow_module_level=True,
    )

from base64 import b64encode
import urllib.parse

from serialx import SerialException, open_serial_connection
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

            # Connect by instance ID instead of name
            url = f"esphome://{parsed.hostname}:{parsed.port}/0"

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
            url = (
                f"esphome://{parsed.hostname}:{parsed.port}?port_name=Serial+Proxy+Left"
            )

            with pytest.raises(
                SerialException, match="The device is using plaintext protocol"
            ):
                await open_serial_connection(
                    url=url,
                    baudrate=115200,
                    noise_psk=base64(b"An unnecessary noise PSK we use."),
                )
