"""Pyodide serial port tests."""

import sys

import pytest

if sys.platform != "emscripten":
    pytest.skip("Pyodide-only tests", allow_module_level=True)

from serialx.platforms.serial_pyodide import PyodideSerial, PyodideSerialTransport


def test_pyodide_transport_name() -> None:
    """The Pyodide transport registers under the expected name."""
    assert PyodideSerialTransport.transport_name == "pyodide"
    assert issubclass(PyodideSerial, object)
