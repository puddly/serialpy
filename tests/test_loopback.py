"""Test APIs with a loopback adapter."""

import contextlib
import os
import random

import pytest

import serialpy

LOOPBACK_ADAPTER = os.environ.get("SERIALPY_LOOPBACK_PORT")

# All tests here use a real adapter, skip if not configured
pytestmark = pytest.mark.skipif(
    LOOPBACK_ADAPTER is None,
    reason="Loopback adapter port not set via SERIALPY_LOOPBACK_PORT",
)


@pytest.mark.parametrize(
    ("baudrate", "chunk_size"),
    [
        (9600, 1),
        (9600, 16),
        (115200, 1),
        (115200, 16),
        (115200, 64),
        (921600, 1),
        (921600, 16),
        (921600, 256),
        (921600, 1024),
    ],
)
def test_loopback_sync(baudrate: int, chunk_size: int) -> None:
    """Test loopback adapter synchronously."""
    random.seed(0)

    with serialpy.Serial(LOOPBACK_ADAPTER, baudrate=baudrate) as serial:
        data = os.urandom(chunk_size)
        serial.write(data)

        read_data = serial.readexactly(chunk_size)
        assert read_data == data


@pytest.mark.parametrize(
    ("baudrate", "chunk_size"),
    [
        (9600, 1),
        (9600, 16),
        (115200, 1),
        (115200, 16),
        (115200, 64),
        (921600, 1),
        (921600, 16),
        (921600, 256),
        (921600, 1024),
    ],
)
async def test_loopback_async(baudrate: int, chunk_size: int) -> None:
    """Test loopback adapter asynchronously."""
    random.seed(0)

    reader, writer = await serialpy.open_serial_connection(
        LOOPBACK_ADAPTER, baudrate=baudrate
    )

    with contextlib.closing(writer):
        data = os.urandom(chunk_size)
        writer.write(data)

        read_data = await reader.readexactly(chunk_size)
        assert read_data == data
