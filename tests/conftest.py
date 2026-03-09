"""Pytest configuration for serialx tests."""

from collections.abc import Generator
import importlib
import re
import sys
import time
from unittest.mock import patch

import pytest

import serialx
import serialx.platforms
from tests.common import (
    SOCAT_BINARY,
    SerialPair,
    create_esphome_pair,
    create_socat_pair,
    get_esphome_host_daemon_program,
)
from tests.socket_relay import create_socket_pair

try:
    import aioesphomeapi
except ImportError:
    aioesphomeapi = None

COM0COM_RE = re.compile(r"^CNC[A-Z]\d+$", re.IGNORECASE)


def _get_posix_serial_classes() -> list[str]:
    """Get extra POSIX serial class names to test on this platform.

    On Linux (or any platform with a deeper class hierarchy), we also test
    with the generic POSIX and extended POSIX backends by patching sys.platform
    and is_extended_posix to force the fallback paths in serialx.platforms.
    """
    try:
        import termios  # noqa: F401, PLC0415
    except ImportError:
        return []

    from serialx.platforms.serial_extended_posix import (  # noqa: PLC0415
        is_extended_posix,
    )

    result = ["PosixSerial"]

    if is_extended_posix():
        result.append("ExtendedPosixSerial")

    return result


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "xdist_group(name): group tests for pytest-xdist parallel execution control",
    )
    config.addinivalue_line(
        "markers",
        "skip_backends(*backends): skip test for the listed serial_pair backends",
    )
    config.addinivalue_line(
        "markers",
        "require_backends(*backends): only run test for the listed serial_pair backends",
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom command line options for serial adapter configuration."""
    parser.addoption(
        "--adapter-pair",
        action="append",
        default=[],
        help="Pair of serial adapters in format LEFT:RIGHT (can be specified multiple times)",
    )


def _get_adapter_pairs(config: pytest.Config) -> list[tuple[str, str]]:
    """Get list of adapter pairs from config."""
    pairs = []

    for pair in config.getoption("--adapter-pair"):
        parts = pair.split(":")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid adapter pair format: {pair}. Expected LEFT:RIGHT"
            )

        pairs.append(tuple(parts))

    return pairs


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize tests based on available backends."""
    if "serial_pair" in metafunc.fixturenames:
        params = []

        if SOCAT_BINARY:
            params.append(pytest.param(("socat",), id="socat"))

            if (
                sys.version_info >= (3, 11)
                and aioesphomeapi is not None
                and (esphome_program := get_esphome_host_daemon_program()) is not None
            ):
                params.append(
                    pytest.param(
                        ("esphome", esphome_program),
                        id="esphome",
                    )
                )

            for cls_name in _get_posix_serial_classes():
                params.append(pytest.param(("socat", cls_name), id=f"socat+{cls_name}"))

        params.append(pytest.param(("socket",), id="socket"))

        for cls_name in _get_posix_serial_classes():
            params.append(pytest.param(("socket", cls_name), id=f"socket+{cls_name}"))

        for left, right in _get_adapter_pairs(metafunc.config):
            if COM0COM_RE.match(left) or COM0COM_RE.match(right):
                backend = "com0com"
            else:
                backend = "adapter"

            params.append(
                pytest.param(
                    (backend, left, right),
                    marks=[pytest.mark.xdist_group(name=f"pair:{left}:{right}")],
                    id=f"{left}:{right}",
                )
            )

        metafunc.parametrize("serial_pair", params, indirect=True)

    if "adapter_pair" in metafunc.fixturenames:
        pairs = _get_adapter_pairs(metafunc.config)

        metafunc.parametrize(
            "adapter_pair",
            [
                pytest.param(
                    (left, right),
                    marks=[pytest.mark.xdist_group(name=f"pair:{left}:{right}")],
                    id=f"{left}:{right}",
                )
                for left, right in pairs
            ],
        )


@pytest.fixture
def serial_pair(request: pytest.FixtureRequest) -> Generator[SerialPair]:
    """Yield a connected serial port pair with backend metadata.

    Parametrized over all available backends: socat, esphome, socket,
    and any physical adapter pairs passed via --adapter-pair.
    """
    backend_info = request.param
    backend = backend_info[0]

    for marker in request.node.iter_markers("skip_backends"):
        if backend in marker.args:
            pytest.skip(f"Skipped for backend {backend!r}")

    for marker in request.node.iter_markers("require_backends"):
        if backend not in marker.args:
            pytest.skip(f"Requires backend in {marker.args!r}, got {backend!r}")

    # Check if a serial class override is requested (e.g. "PosixSerial")
    serial_class_override = (
        backend_info[1]
        if len(backend_info) > 1 and backend in ("socat", "socket")
        else None
    )

    if serial_class_override:
        is_extended = serial_class_override == "ExtendedPosixSerial"
        with (
            patch("sys.platform", "unknown"),
            patch(
                "serialx.platforms.serial_extended_posix.is_extended_posix",
                return_value=is_extended,
            ),
        ):
            importlib.reload(serialx.platforms)

        serial_class = serialx.platforms.Serial.__name__

        try:
            if backend == "socat":
                with create_socat_pair() as (left, right):
                    yield SerialPair(left, right, "socat", serial_class)
            elif backend == "socket":
                with create_socket_pair() as (left, right):
                    yield SerialPair(left, right, "socket", serial_class)
        finally:
            importlib.reload(serialx.platforms)
    elif backend == "socat":
        with create_socat_pair() as (left, right):
            yield SerialPair(left, right, "socat")
    elif backend == "esphome":
        with create_esphome_pair(backend_info[1]) as (left, right):
            yield SerialPair(left, right, "esphome")
    elif backend == "socket":
        with create_socket_pair() as (left, right):
            yield SerialPair(left, right, "socket")
    elif backend in ("adapter", "com0com"):
        yield SerialPair(backend_info[1], backend_info[2], backend)


@pytest.fixture(autouse=True)
def _purge_com0com(request: pytest.FixtureRequest) -> None:
    """Purge com0com buffers to prevent data leakage between tests.

    com0com buffers data in its virtual cable even when the receiving port is
    closed.  Previous tests that write without reading leave stale bytes that
    pollute the next test.
    """
    if sys.platform != "win32":
        return

    if "serial_pair" not in request.fixturenames:
        return

    candidate: SerialPair = request.getfixturevalue("serial_pair")
    if candidate.backend != "com0com":
        return

    from win32file import (  # noqa: PLC0415
        PURGE_RXABORT,
        PURGE_RXCLEAR,
        PURGE_TXABORT,
        PURGE_TXCLEAR,
        PurgeComm,
    )

    with (
        serialx.Serial.from_url(candidate.left, baudrate=10_000_000) as serial_left,
        serialx.Serial.from_url(candidate.right, baudrate=10_000_000) as serial_right,
    ):
        flags = PURGE_TXABORT | PURGE_RXABORT | PURGE_TXCLEAR | PURGE_RXCLEAR
        PurgeComm(serial_left._handle, flags)  # type: ignore[attr-defined]
        PurgeComm(serial_right._handle, flags)  # type: ignore[attr-defined]

        time.sleep(0.05)
        PurgeComm(serial_left._handle, flags)  # type: ignore[attr-defined]
        PurgeComm(serial_right._handle, flags)  # type: ignore[attr-defined]
