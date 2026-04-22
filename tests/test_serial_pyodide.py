"""Pyodide serial port tests."""

import sys

import pytest

if sys.platform != "emscripten":
    pytest.skip("Pyodide-only tests", allow_module_level=True)

import js

from serialx.platforms.serial_pyodide import PyodideSerial, PyodideSerialTransport
from tests.common import async_create_reader_writer


def test_pyodide_transport_name() -> None:
    """The Pyodide transport registers under the expected name."""
    assert PyodideSerialTransport.transport_name == "pyodide"
    assert issubclass(PyodideSerial, object)


async def test_pyodide_roundtrip_bytes() -> None:
    """Writes on one fake port surface as reads on the peer."""
    left, right = js.create_fake_serial_pair()

    async with async_create_reader_writer(
        "pyodide://serial", baudrate=115200, js_port=left
    ) as (_, writer_left):
        async with async_create_reader_writer(
            "pyodide://serial", baudrate=115200, js_port=right
        ) as (reader_right, _):
            writer_left.write(b"hello")
            assert await reader_right.readexactly(5) == b"hello"
