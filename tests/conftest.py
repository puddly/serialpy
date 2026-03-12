"""Pytest configuration for serialx tests."""

from collections.abc import Collection, Generator
import dataclasses
import importlib
import re
import sys
import time
from unittest.mock import patch

import pytest

import serialx
import serialx.platforms
from tests.common import (
    SER2NET_BINARY,
    SOCAT_BINARY,
    SerialPair,
    SerialPairBackend,
    SerialPairQuirk,
    create_esphome_pair,
    create_ser2net_pair,
    create_socat_pair,
    get_esphome_host_daemon_program,
)
from tests.socket_relay import create_socket_pair

try:
    import aioesphomeapi
except ImportError:
    aioesphomeapi = None

COM0COM_RE = re.compile(r"^CNC[A-Z]\d+$", re.IGNORECASE)
TTY0TTY_RE = re.compile(r"^/dev/tnt\d+$")

RFC2217_WRAPPER_QUIRKS = frozenset(
    {
        SerialPairQuirk.NO_NUM_UNREAD_BYTES,
        SerialPairQuirk.NO_RESET_READ_BUFFER,
        SerialPairQuirk.NO_NUM_UNWRITTEN_BYTES,
        SerialPairQuirk.NO_RESET_WRITE_BUFFER,
        SerialPairQuirk.NO_WRITE_TIMEOUT,
        SerialPairQuirk.NO_WRITE_LIMITS,
        SerialPairQuirk.NO_PAUSE_WRITING_CALLBACKS,
    }
)

SERIAL_PAIR_DEFAULT_QUIRKS: dict[SerialPairBackend, frozenset[SerialPairQuirk]] = {
    SerialPairBackend.SOCAT: frozenset(
        {
            SerialPairQuirk.NO_PIN_READBACK,
            SerialPairQuirk.NO_DTR_CTS,
            SerialPairQuirk.NO_FLOW_CONTROL,
            SerialPairQuirk.NO_NUM_UNWRITTEN_BYTES,
            SerialPairQuirk.NO_RESET_WRITE_BUFFER,
            SerialPairQuirk.NO_PAUSE_WRITING_CALLBACKS,
        }
    ),
    SerialPairBackend.SOCKET: frozenset(
        {
            SerialPairQuirk.NO_PIN_READBACK,
            SerialPairQuirk.NO_DTR_CTS,
            SerialPairQuirk.NO_FLOW_CONTROL,
            SerialPairQuirk.NO_NUM_UNREAD_BYTES,
            SerialPairQuirk.NO_RESET_READ_BUFFER,
            SerialPairQuirk.NO_NUM_UNWRITTEN_BYTES,
            SerialPairQuirk.NO_RESET_WRITE_BUFFER,
            SerialPairQuirk.NO_WRITE_TIMEOUT,
            SerialPairQuirk.NO_WRITE_LIMITS,
            SerialPairQuirk.NO_PAUSE_WRITING_CALLBACKS,
        }
    ),
    SerialPairBackend.ESPHOME: frozenset(
        {
            SerialPairQuirk.NO_PIN_READBACK,
            SerialPairQuirk.NO_DTR_CTS,
            SerialPairQuirk.NO_FLOW_CONTROL,
            SerialPairQuirk.NO_NUM_UNREAD_BYTES,
            SerialPairQuirk.NO_RESET_READ_BUFFER,
            SerialPairQuirk.NO_NUM_UNWRITTEN_BYTES,
            SerialPairQuirk.NO_RESET_WRITE_BUFFER,
            SerialPairQuirk.NO_WRITE_TIMEOUT,
            SerialPairQuirk.NO_PAUSE_READING,
            SerialPairQuirk.NO_WRITE_LIMITS,
            SerialPairQuirk.NO_PAUSE_WRITING_CALLBACKS,
        }
    ),
    SerialPairBackend.ADAPTER: frozenset(
        {
            SerialPairQuirk.NO_DTR_CTS,
            SerialPairQuirk.NO_FLOW_CONTROL,
        }
    ),
    SerialPairBackend.COM0COM: frozenset(
        {
            SerialPairQuirk.NO_PIN_READBACK,
            SerialPairQuirk.NO_WRITE_LIMITS,
            SerialPairQuirk.NO_PAUSE_WRITING_CALLBACKS,
        }
    ),
    SerialPairBackend.TTY0TTY: frozenset(
        {
            SerialPairQuirk.NO_PIN_READBACK,
            SerialPairQuirk.NO_DTR_CTS,
            SerialPairQuirk.NO_FLOW_CONTROL,
            SerialPairQuirk.NO_NUM_UNWRITTEN_BYTES,
            SerialPairQuirk.NO_RESET_WRITE_BUFFER,
            SerialPairQuirk.NO_WRITE_TIMEOUT,
            SerialPairQuirk.NO_PAUSE_WRITING_CALLBACKS,
        }
    ),
    SerialPairBackend.RFC2217: frozenset(
        {
            SerialPairQuirk.NO_PIN_READBACK,
            SerialPairQuirk.NO_DTR_CTS,
            SerialPairQuirk.NO_FLOW_CONTROL,
        }
        | RFC2217_WRAPPER_QUIRKS
    ),
}

SERIAL_PAIR_QUIRK_FLAG_NAMES: dict[str, SerialPairQuirk] = {
    "pin-readback": SerialPairQuirk.NO_PIN_READBACK,
    "dtr-cts": SerialPairQuirk.NO_DTR_CTS,
    "flow-control": SerialPairQuirk.NO_FLOW_CONTROL,
    "num-unread-bytes": SerialPairQuirk.NO_NUM_UNREAD_BYTES,
    "reset-read-buffer": SerialPairQuirk.NO_RESET_READ_BUFFER,
    "num-unwritten-bytes": SerialPairQuirk.NO_NUM_UNWRITTEN_BYTES,
    "reset-write-buffer": SerialPairQuirk.NO_RESET_WRITE_BUFFER,
    "write-timeout": SerialPairQuirk.NO_WRITE_TIMEOUT,
    "pause-reading": SerialPairQuirk.NO_PAUSE_READING,
    "write-limits": SerialPairQuirk.NO_WRITE_LIMITS,
    "pause-writing-callbacks": SerialPairQuirk.NO_PAUSE_WRITING_CALLBACKS,
}

SERIAL_PAIR_QUIRK_OPERATIONS: dict[
    str, tuple[frozenset[SerialPairQuirk], frozenset[SerialPairQuirk]]
] = {
    token: (frozenset(), frozenset({quirk}))
    for token, quirk in SERIAL_PAIR_QUIRK_FLAG_NAMES.items()
}
SERIAL_PAIR_QUIRK_OPERATIONS.update(
    {
        quirk.value: (frozenset({quirk}), frozenset())
        for quirk in SERIAL_PAIR_QUIRK_FLAG_NAMES.values()
    }
)


@dataclasses.dataclass(frozen=True)
class SerialPairSpec:
    """Description of a test serial pair variant before fixture creation."""

    left_backend: SerialPairBackend
    right_backend: SerialPairBackend
    quirks: frozenset[SerialPairQuirk]
    left: str | None = None
    right: str | None = None
    serial_class_override: str | None = None
    esphome_program: str | None = None
    pair_label: str | None = None
    wrapped_left_backend: SerialPairBackend | None = None
    wrapped_right_backend: SerialPairBackend | None = None

    @property
    def backends(self) -> frozenset[SerialPairBackend]:
        """Return the distinct backend families exposed to the test pair."""
        return frozenset({self.left_backend, self.right_backend})

    @property
    def wrapped_backends(self) -> frozenset[SerialPairBackend]:
        """Return backend families used underneath a wrapped pair."""
        return frozenset(
            backend
            for backend in (self.wrapped_left_backend, self.wrapped_right_backend)
            if backend is not None
        )


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
        "skip_backends(*backends): skip test when either endpoint backend matches a listed SerialPairBackend",
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
            "(for example /dev/ttyUSB1,/dev/ttyUSB2,pin-readback,dtr-cts,"
            "flow-control or /dev/tnt0,/dev/tnt1,no-pin-readback,no-dtr-cts,"
            "no-flow-control or rfc2217://127.0.0.1:5001,"
            "rfc2217://127.0.0.1:5002,no-write-timeout)"
        ),
    )


def _is_rfc2217_endpoint(path: str) -> bool:
    """Return True when the endpoint is already an RFC2217 URI."""
    return path.lower().startswith("rfc2217://")


def _classify_endpoint_backend(path: str) -> SerialPairBackend:
    """Classify a single endpoint into a backend family."""
    lower_path = path.lower()
    if lower_path.startswith("rfc2217://"):
        return SerialPairBackend.RFC2217
    if lower_path.startswith("socket://"):
        return SerialPairBackend.SOCKET
    if lower_path.startswith("esphome://"):
        return SerialPairBackend.ESPHOME
    if COM0COM_RE.match(path):
        return SerialPairBackend.COM0COM
    if TTY0TTY_RE.match(path):
        return SerialPairBackend.TTY0TTY
    return SerialPairBackend.ADAPTER


def _coerce_serial_pair_backend(value: object) -> SerialPairBackend:
    """Normalize a backend marker value to SerialPairBackend."""
    if isinstance(value, SerialPairBackend):
        return value
    if isinstance(value, str):
        return SerialPairBackend(value)
    raise TypeError(f"Unsupported backend marker value: {value!r}")


def _coerce_serial_pair_quirk(value: object) -> SerialPairQuirk:
    """Normalize a quirk marker value to SerialPairQuirk."""
    if isinstance(value, SerialPairQuirk):
        return value
    if isinstance(value, str):
        return SerialPairQuirk(value)
    raise TypeError(f"Unsupported quirk marker value: {value!r}")


def _format_serial_pair_quirks(quirks: Collection[SerialPairQuirk]) -> str:
    """Render a quirk set as comma-separated CLI tokens."""
    return ", ".join(sorted(quirk.value for quirk in quirks))


def _resolve_serial_pair_quirks(
    left_backend: SerialPairBackend,
    right_backend: SerialPairBackend,
    raw_flags: list[str],
) -> frozenset[SerialPairQuirk]:
    """Resolve normalized quirks for a backend pair plus explicit flags."""
    quirks = set(
        SERIAL_PAIR_DEFAULT_QUIRKS[left_backend]
        | SERIAL_PAIR_DEFAULT_QUIRKS[right_backend]
    )
    unknown_flags: list[str] = []

    for raw_flag in raw_flags:
        flag = raw_flag.strip().lower()
        if not flag:
            continue

        operation = SERIAL_PAIR_QUIRK_OPERATIONS.get(flag)
        if operation is None:
            unknown_flags.append(raw_flag)
            continue

        adds, removes = operation
        quirks.difference_update(removes)
        quirks.update(adds)

    if unknown_flags:
        raise ValueError(
            "Unknown adapter quirk flag(s): "
            + ", ".join(sorted(set(unknown_flags)))
            + ". Supported flags: "
            + ", ".join(sorted(SERIAL_PAIR_QUIRK_OPERATIONS))
        )

    return frozenset(quirks)


def _get_adapter_pairs(config: pytest.Config) -> list[SerialPairSpec]:
    """Get parsed adapter pair specifications from config."""
    pairs: list[SerialPairSpec] = []

    for pair in config.getoption("--adapter-pair"):
        if "," in pair:
            parts = [part.strip() for part in pair.split(",")]
            expected_format = "LEFT,RIGHT[,FLAG...]"
        else:
            parts = pair.split(":")
            expected_format = "LEFT:RIGHT[:FLAG...]"
        if len(parts) < 2:
            raise ValueError(
                f"Invalid adapter pair format: {pair}. Expected {expected_format}"
            )

        left, right, *raw_flags = parts
        if not left or not right:
            raise ValueError(
                f"Invalid adapter pair format: {pair}. Expected {expected_format}"
            )
        left_backend = _classify_endpoint_backend(left)
        right_backend = _classify_endpoint_backend(right)
        quirks = _resolve_serial_pair_quirks(left_backend, right_backend, raw_flags)
        pairs.append(
            SerialPairSpec(
                left_backend=left_backend,
                right_backend=right_backend,
                left=left,
                right=right,
                quirks=quirks,
                pair_label=pair,
            )
        )

    return pairs


def _derive_rfc2217_quirks(
    quirks: frozenset[SerialPairQuirk],
) -> frozenset[SerialPairQuirk]:
    """Apply RFC2217 transport limitations on top of an adapter pair."""
    return frozenset(set(quirks) | RFC2217_WRAPPER_QUIRKS)


def _build_rfc2217_spec(spec: SerialPairSpec) -> SerialPairSpec:
    """Build an RFC2217 variant that wraps an existing serial pair spec."""
    return dataclasses.replace(
        spec,
        left_backend=SerialPairBackend.RFC2217,
        right_backend=SerialPairBackend.RFC2217,
        quirks=_derive_rfc2217_quirks(spec.quirks),
        wrapped_left_backend=spec.left_backend,
        wrapped_right_backend=spec.right_backend,
    )


def _serial_pair_resource_group(spec: SerialPairSpec) -> list[pytest.MarkDecorator]:
    """Return an xdist group key for specs that share one underlying resource."""
    if spec.left is not None and spec.right is not None:
        return [pytest.mark.xdist_group(name=f"pair:{spec.left}:{spec.right}")]
    else:
        return []


def _can_auto_expand_rfc2217(spec: SerialPairSpec) -> bool:
    """Return True when ser2net can wrap the given concrete endpoints."""
    local_backends = {
        SerialPairBackend.ADAPTER,
        SerialPairBackend.COM0COM,
        SerialPairBackend.TTY0TTY,
    }
    return (
        spec.left is not None
        and spec.right is not None
        and not spec.wrapped_backends
        and spec.left_backend in local_backends
        and spec.right_backend in local_backends
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize tests based on available backends."""
    if "serial_pair" in metafunc.fixturenames:
        params: list[pytest.ParameterSet] = []

        if SOCAT_BINARY:
            socat_spec = SerialPairSpec(
                left_backend=SerialPairBackend.SOCAT,
                right_backend=SerialPairBackend.SOCAT,
                quirks=SERIAL_PAIR_DEFAULT_QUIRKS[SerialPairBackend.SOCAT],
            )
            params.append(
                pytest.param(
                    socat_spec,
                    id="socat",
                )
            )

            if SER2NET_BINARY is not None:
                params.append(
                    pytest.param(
                        _build_rfc2217_spec(socat_spec),
                        id="rfc2217+socat",
                    )
                )

            if (
                sys.version_info >= (3, 11)
                and aioesphomeapi is not None
                and (esphome_program := get_esphome_host_daemon_program()) is not None
            ):
                params.append(
                    pytest.param(
                        SerialPairSpec(
                            left_backend=SerialPairBackend.ESPHOME,
                            right_backend=SerialPairBackend.ESPHOME,
                            esphome_program=esphome_program,
                            quirks=SERIAL_PAIR_DEFAULT_QUIRKS[
                                SerialPairBackend.ESPHOME
                            ],
                        ),
                        id="esphome",
                    )
                )

            for cls_name in _get_posix_serial_classes():
                params.append(
                    pytest.param(
                        SerialPairSpec(
                            left_backend=SerialPairBackend.SOCAT,
                            right_backend=SerialPairBackend.SOCAT,
                            serial_class_override=cls_name,
                            quirks=SERIAL_PAIR_DEFAULT_QUIRKS[SerialPairBackend.SOCAT],
                        ),
                        id=f"socat+{cls_name}",
                    )
                )

        params.append(
            pytest.param(
                SerialPairSpec(
                    left_backend=SerialPairBackend.SOCKET,
                    right_backend=SerialPairBackend.SOCKET,
                    quirks=SERIAL_PAIR_DEFAULT_QUIRKS[SerialPairBackend.SOCKET],
                ),
                id="socket",
            )
        )

        for cls_name in _get_posix_serial_classes():
            params.append(
                pytest.param(
                    SerialPairSpec(
                        left_backend=SerialPairBackend.SOCKET,
                        right_backend=SerialPairBackend.SOCKET,
                        serial_class_override=cls_name,
                        quirks=SERIAL_PAIR_DEFAULT_QUIRKS[SerialPairBackend.SOCKET],
                    ),
                    id=f"socket+{cls_name}",
                )
            )

        for spec in _get_adapter_pairs(metafunc.config):
            assert spec.left is not None
            assert spec.right is not None
            assert spec.pair_label is not None

            resource_group = _serial_pair_resource_group(spec)

            params.append(
                pytest.param(
                    spec,
                    marks=resource_group,
                    id=spec.pair_label,
                )
            )

            if SER2NET_BINARY is not None and _can_auto_expand_rfc2217(spec):
                rfc2217_spec = _build_rfc2217_spec(spec)
                params.append(
                    pytest.param(
                        rfc2217_spec,
                        marks=resource_group,
                        id=f"rfc2217+{spec.pair_label}",
                    )
                )

        metafunc.parametrize("serial_pair", params, indirect=True)

    if "adapter_pair" in metafunc.fixturenames:
        pairs = _get_adapter_pairs(metafunc.config)

        metafunc.parametrize(
            "adapter_pair",
            [
                pytest.param(
                    (spec.left, spec.right),
                    marks=_serial_pair_resource_group(spec),
                    id=spec.pair_label,
                )
                for spec in pairs
            ],
        )


@pytest.fixture
def serial_pair(request: pytest.FixtureRequest) -> Generator[SerialPair]:
    """Yield a connected serial port pair with backend metadata.

    Parametrized over all available backends: socat, esphome, socket,
    ser2net-backed RFC2217 variants, and any physical adapter pairs passed via
    --adapter-pair.
    """
    spec: SerialPairSpec = request.param
    pair_backends = spec.backends
    quirks = spec.quirks

    for marker in request.node.iter_markers("skip_backends"):
        blocked_backends = {_coerce_serial_pair_backend(arg) for arg in marker.args}
        present_backends = pair_backends & blocked_backends
        if present_backends:
            pytest.skip(
                "Skipped for endpoint backends: "
                + ", ".join(sorted(backend.value for backend in present_backends))
            )

    for marker in request.node.iter_markers("skip_quirks"):
        blocked = {_coerce_serial_pair_quirk(arg) for arg in marker.args}
        present_quirks = blocked & quirks
        if present_quirks:
            pytest.skip(
                "Skipped because serial_pair exposes quirks: "
                + _format_serial_pair_quirks(present_quirks)
            )

    # Check if a serial class override is requested (e.g. "PosixSerial")
    serial_class_override = spec.serial_class_override

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
            if spec.backends == {SerialPairBackend.SOCAT}:
                with create_socat_pair() as (left, right):
                    yield SerialPair(
                        left,
                        right,
                        SerialPairBackend.SOCAT,
                        SerialPairBackend.SOCAT,
                        serial_class,
                        quirks,
                    )
            elif spec.backends == {SerialPairBackend.SOCKET}:
                with create_socket_pair() as (left, right):
                    yield SerialPair(
                        left,
                        right,
                        SerialPairBackend.SOCKET,
                        SerialPairBackend.SOCKET,
                        serial_class,
                        quirks,
                    )
        finally:
            importlib.reload(serialx.platforms)
    elif spec.backends == {SerialPairBackend.SOCAT}:
        with create_socat_pair() as (left, right):
            yield SerialPair(
                left,
                right,
                SerialPairBackend.SOCAT,
                SerialPairBackend.SOCAT,
                quirks=quirks,
            )
    elif spec.backends == {SerialPairBackend.ESPHOME}:
        assert spec.esphome_program is not None
        with create_esphome_pair(spec.esphome_program) as (left, right):
            yield SerialPair(
                left,
                right,
                SerialPairBackend.ESPHOME,
                SerialPairBackend.ESPHOME,
                quirks=quirks,
            )
    elif spec.backends == {SerialPairBackend.SOCKET}:
        with create_socket_pair() as (left, right):
            yield SerialPair(
                left,
                right,
                SerialPairBackend.SOCKET,
                SerialPairBackend.SOCKET,
                quirks=quirks,
            )
    elif spec.wrapped_backends:
        if spec.left is not None and spec.right is not None:
            assert SER2NET_BINARY is not None
            with create_ser2net_pair(spec.left, spec.right) as (left, right):
                yield SerialPair(
                    left,
                    right,
                    spec.left_backend,
                    spec.right_backend,
                    quirks=quirks,
                    spawned_ser2net=True,
                )
        elif spec.wrapped_backends == {SerialPairBackend.SOCAT}:
            assert SER2NET_BINARY is not None
            with create_socat_pair() as (adapter_left, adapter_right):
                with create_ser2net_pair(adapter_left, adapter_right) as (left, right):
                    yield SerialPair(
                        left,
                        right,
                        spec.left_backend,
                        spec.right_backend,
                        quirks=quirks,
                        spawned_ser2net=True,
                    )
        else:
            raise AssertionError(f"Unsupported wrapped source spec: {spec!r}")
    elif spec.left is not None and spec.right is not None:
        if (
            spec.backends == {SerialPairBackend.RFC2217}
            or SerialPairBackend.RFC2217 in spec.backends
        ):
            yield SerialPair(
                spec.left,
                spec.right,
                spec.left_backend,
                spec.right_backend,
                quirks=quirks,
            )
        else:
            yield SerialPair(
                spec.left,
                spec.right,
                spec.left_backend,
                spec.right_backend,
                quirks=quirks,
            )
    else:
        raise AssertionError(f"Unsupported serial pair spec: {spec!r}")


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
    if candidate.backends != {SerialPairBackend.COM0COM}:
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
