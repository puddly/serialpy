"""Test async APIs with socket:// endpoints."""

import asyncio
import logging

import pytest

from serialx import create_serial_connection

LOGGER = logging.getLogger(__name__)


async def test_invalid_socket_uri_async() -> None:
    """Test invalid socket URI is rejected by public async API."""
    loop = asyncio.get_running_loop()

    with pytest.raises(ValueError, match="expected both host and port"):
        await create_serial_connection(
            loop,
            asyncio.Protocol,
            "socket://127.0.0.1",
            baudrate=115200,
        )
