"""Pytest configuration for serialx tests."""

from __future__ import annotations

from collections.abc import Generator
import contextlib
import dataclasses
import importlib
import sys
import time
from unittest.mock import patch

import pytest

import serialx
import serialx.platforms
from tests.common import (
    ESPHOME_HOST_BINARY,
    HUB4COM_BINARY,
    SER2NET_BINARY,
    SERIAL_PAIR_DEFAULT_QUIRKS,
    SOCAT_BINARY,
    SerialBackend,
    SerialPair,
    SerialQuirk,
    UnresolvedSerialPair,
    create_esphome_pair,
    create_hub4com_pair,
    create_ser2net_pair,
    create_socat_pair,
)
from tests.socket_relay import create_socket_pair


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
        "skip_quirks(*quirks): skip test when serial_pair exposes any listed quirk",
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom command line options for serial adapter configuration."""
    parser.addoption(
        "--adapter-pair",
        action="append",
        default=[],
        help=(
            "Pair of serial endpoints in format LEFT,RIGHT[,FLAG...] "
            "(e.g. /dev/tnt0,/dev/tnt1,no-pin-readback,no-rts-cts "
            "or rfc2217://127.0.0.1:5001,"
            "rfc2217://127.0.0.1:5002,no-write-timeout)"
        ),
    )


def _get_endpoint_backend(path: str) -> SerialBackend:
    """Classify a single endpoint into a backend family."""
    lower_path = path.lower()
    if lower_path.startswith("rfc2217://"):
        return SerialBackend.SER2NET
    if lower_path.startswith("socket://"):
        return SerialBackend.SOCKET
    if lower_path.startswith("esphome://"):
        return SerialBackend.ESPHOME
    return SerialBackend.ADAPTER


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize tests based on available backends."""

    adapters = []

    # Physical adapters passed in with a CLI flag
    for pair in metafunc.config.getoption("--adapter-pair"):
        parts = [part.strip() for part in pair.split(",")]
        expected_format = "LEFT,RIGHT[,FLAG...]"

        if len(parts) < 2:
            raise ValueError(
                f"Invalid adapter pair format: {pair}. Expected {expected_format}"
            )

        left, right, *raw_flags = parts

        if not left or not right:
            raise ValueError(
                f"Invalid adapter pair format: {pair}. Expected {expected_format}"
            )

        left_backend = _get_endpoint_backend(left)
        right_backend = _get_endpoint_backend(right)

        adapters.append(
            UnresolvedSerialPair(
                backends=(),
                left=left,
                right=right,
                original_left=left,
                original_right=right,
                quirks=(
                    SERIAL_PAIR_DEFAULT_QUIRKS[left_backend]
                    | SERIAL_PAIR_DEFAULT_QUIRKS[right_backend]
                    | frozenset({SerialQuirk(raw_flag) for raw_flag in raw_flags})
                ),
            )
        )

    if "serial_pair" in metafunc.fixturenames:
        # `socat` can always be used to create virtual serial port pairs
        if SOCAT_BINARY:
            adapters.append(
                UnresolvedSerialPair(
                    backends=(SerialBackend.SOCAT,),
                    left=None,
                    right=None,
                    original_left="gen",
                    original_right="gen",
                    quirks=SERIAL_PAIR_DEFAULT_QUIRKS[SerialBackend.SOCAT],
                )
            )

        # Transport chains build on top of adapters
        specs = []

        # We can always create a TCP server
        specs.append(
            UnresolvedSerialPair(
                backends=(SerialBackend.SOCKET,),
                left=None,
                right=None,
                original_left="gen",
                original_right="gen",
                quirks=SERIAL_PAIR_DEFAULT_QUIRKS[SerialBackend.SOCKET],
            )
        )

        for adapter_spec in adapters:
            specs.append(adapter_spec)

            if SER2NET_BINARY is not None:
                ser2net_spec = adapter_spec.chain(SerialBackend.SER2NET)

                # ser2net only polls modem line states every second
                specs.append(
                    dataclasses.replace(ser2net_spec, modem_line_propagation_delay=1.1)
                )

            if HUB4COM_BINARY is not None:
                specs.append(adapter_spec.chain(SerialBackend.HUB4COM))

            if sys.version_info >= (3, 11) and ESPHOME_HOST_BINARY is not None:
                specs.append(adapter_spec.chain(SerialBackend.ESPHOME_HOST))

        # For POSIX, we should test base classes on platforms that extend them
        for cls_name in _get_posix_serial_classes():
            for adapter in adapters:
                specs.append(dataclasses.replace(adapter, serial_class=cls_name))

        # Build the pytest parameter groups to limit concurrency to underlying resources
        params = []

        for spec in specs:
            marks = []

            if spec.left is not None:
                marks.append(pytest.mark.xdist_group(name=spec.left))

            if spec.right is not None:
                marks.append(pytest.mark.xdist_group(name=spec.right))

            backends = [b.name for b in spec.backends]

            if spec.original_left != "gen" and spec.original_right != "gen":
                backends.append(f"{spec.original_left}-{spec.original_right}")

            param_id = "+".join(backends)

            if spec.serial_class:
                param_id += f"({spec.serial_class})"

            params.append(pytest.param(spec, marks=marks, id=param_id))

        # Finally, emit tests
        metafunc.parametrize("serial_pair", params, indirect=True)


@pytest.fixture
def serial_pair(request: pytest.FixtureRequest) -> Generator[SerialPair]:
    """Fixture for a connected serial port pair with the provided backend."""
    spec: UnresolvedSerialPair = request.param

    for marker in request.node.iter_markers("skip_backends"):
        if set(marker.args) & set(spec.backends):
            pytest.skip(
                f"Skipping, blocked backends {marker.args} exist in spec {spec}"
            )

    for marker in request.node.iter_markers("skip_quirks"):
        if set(marker.args) & set(spec.quirks):
            pytest.skip(f"Skipping, blocked quirks {marker.args} exist in spec {spec}")

    # Now we create the chained backends
    stack = contextlib.ExitStack()
    left = spec.left
    right = spec.right

    for backend in spec.backends[::-1]:
        match backend:
            # Synthetic backends don't have an underlying serial port
            case SerialBackend.SOCAT:
                assert left is None and right is None
                left, right = stack.enter_context(create_socat_pair())

            case SerialBackend.SOCKET:
                assert left is None and right is None
                left, right = stack.enter_context(create_socket_pair())

            # Wrapped backends require one
            case SerialBackend.ESPHOME_HOST:
                assert left is not None and right is not None
                left, right = stack.enter_context(create_esphome_pair(left, right))

            case SerialBackend.SER2NET:
                assert left is not None and right is not None
                left, right = stack.enter_context(create_ser2net_pair(left, right))

            case SerialBackend.HUB4COM:
                assert left is not None and right is not None
                left, right = stack.enter_context(create_hub4com_pair(left, right))

            case _:
                raise ValueError(f"Unsupported backend: {backend!r}")

    # At this point, both left and right should exist
    assert left is not None
    assert right is not None
    assert spec.original_left is not None
    assert spec.original_right is not None

    # Check if a serial class override is requested (e.g. "PosixSerial")
    if spec.serial_class:
        with (
            patch("sys.platform", "unknown"),
            patch(
                "serialx.platforms.serial_extended_posix.is_extended_posix",
                return_value=(spec.serial_class == "ExtendedPosixSerial"),
            ),
        ):
            importlib.reload(serialx.platforms)

        assert serialx.platforms.Serial.__name__ == spec.serial_class

    # Finally, emit the spec
    try:
        yield SerialPair(
            left=left,
            right=right,
            original_left=spec.original_left,
            original_right=spec.original_right,
            backends=spec.backends,
            quirks=spec.quirks,
            serial_class=serialx.platforms.Serial.__name__,
        )
    finally:
        stack.close()

        if spec.serial_class:
            importlib.reload(serialx.platforms)


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
    if not (candidate.left.startswith("CNC") or candidate.right.startswith("CNC")):
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
