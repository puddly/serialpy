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
    SerialPairFeature,
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

SERIAL_PAIR_DEFAULT_FEATURES: dict[SerialPairBackend, frozenset[SerialPairFeature]] = {
    SerialPairBackend.SOCAT: frozenset(
        {
            SerialPairFeature.RXTX,
            SerialPairFeature.READ_BUFFER,
            SerialPairFeature.WRITE_TIMEOUT,
        }
    ),
    SerialPairBackend.SOCKET: frozenset({SerialPairFeature.RXTX}),
    SerialPairBackend.ESPHOME: frozenset({SerialPairFeature.RXTX}),
    SerialPairBackend.ADAPTER: frozenset(
        {
            SerialPairFeature.RXTX,
            SerialPairFeature.READ_BUFFER,
            SerialPairFeature.WRITE_BUFFER,
            SerialPairFeature.WRITE_TIMEOUT,
        }
    ),
    SerialPairBackend.COM0COM: frozenset(
        {
            SerialPairFeature.RXTX,
            SerialPairFeature.HW,
            SerialPairFeature.READ_BUFFER,
            SerialPairFeature.WRITE_BUFFER,
            SerialPairFeature.WRITE_TIMEOUT,
        }
    ),
    SerialPairBackend.TTY0TTY: frozenset(
        {
            SerialPairFeature.RXTX,
            SerialPairFeature.HW,
            SerialPairFeature.READ_BUFFER,
        }
    ),
}

SERIAL_PAIR_FEATURE_ALIASES: dict[str, tuple[str, ...]] = {
    "buf": ("readbuf", "writebuf"),
    "nobuf": ("noreadbuf", "nowritebuf"),
}

SERIAL_PAIR_FEATURE_OPERATIONS: dict[
    str, tuple[frozenset[SerialPairFeature], frozenset[SerialPairFeature]]
] = {
    "rxtx": (frozenset({SerialPairFeature.RXTX}), frozenset()),
    "hw": (frozenset({SerialPairFeature.HW}), frozenset()),
    "nohw": (frozenset(), frozenset({SerialPairFeature.HW})),
    "readbuf": (frozenset({SerialPairFeature.READ_BUFFER}), frozenset()),
    "noreadbuf": (frozenset(), frozenset({SerialPairFeature.READ_BUFFER})),
    "writebuf": (frozenset({SerialPairFeature.WRITE_BUFFER}), frozenset()),
    "nowritebuf": (frozenset(), frozenset({SerialPairFeature.WRITE_BUFFER})),
    "writetimeout": (frozenset({SerialPairFeature.WRITE_TIMEOUT}), frozenset()),
    "nowritetimeout": (frozenset(), frozenset({SerialPairFeature.WRITE_TIMEOUT})),
}


@dataclasses.dataclass(frozen=True)
class SerialPairSpec:
    """Description of a test serial pair variant before fixture creation."""

    backend: SerialPairBackend
    features: frozenset[SerialPairFeature]
    left: str | None = None
    right: str | None = None
    serial_class_override: str | None = None
    esphome_program: str | None = None
    pair_label: str | None = None


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
        "skip_backends(*backends): skip test for the listed SerialPairBackend values",
    )
    config.addinivalue_line(
        "markers",
        "require_features(*features): skip test unless serial_pair exposes all listed features",
    )
    config.addinivalue_line(
        "markers",
        "skip_features(*features): skip test when serial_pair exposes any listed feature",
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom command line options for serial adapter configuration."""
    parser.addoption(
        "--adapter-pair",
        action="append",
        default=[],
        help=(
            "Pair of serial adapters in format LEFT:RIGHT[:FLAG...] "
            "(for example /dev/ttyUSB1:/dev/ttyUSB2:rxtx:hw:nobuf)"
        ),
    )


def _classify_adapter_backend(left: str, right: str) -> SerialPairBackend:
    """Classify an explicit adapter pair into a backend family."""
    if COM0COM_RE.match(left) or COM0COM_RE.match(right):
        return SerialPairBackend.COM0COM
    elif TTY0TTY_RE.match(left) or TTY0TTY_RE.match(right):
        return SerialPairBackend.TTY0TTY
    else:
        return SerialPairBackend.ADAPTER


def _coerce_serial_pair_backend(value: object) -> SerialPairBackend:
    """Normalize a backend marker value to SerialPairBackend."""
    if isinstance(value, SerialPairBackend):
        return value
    if isinstance(value, str):
        return SerialPairBackend(value)
    raise TypeError(f"Unsupported backend marker value: {value!r}")


def _coerce_serial_pair_feature(value: object) -> SerialPairFeature:
    """Normalize a feature marker value to SerialPairFeature."""
    if isinstance(value, SerialPairFeature):
        return value
    if isinstance(value, str):
        return SerialPairFeature(value)
    raise TypeError(f"Unsupported feature marker value: {value!r}")


def _format_serial_pair_features(features: Collection[SerialPairFeature]) -> str:
    """Render a feature set as comma-separated CLI tokens."""
    return ", ".join(sorted(feature.value for feature in features))


def _resolve_serial_pair_features(
    backend: SerialPairBackend, raw_flags: list[str]
) -> frozenset[SerialPairFeature]:
    """Resolve normalized feature tags for a backend plus explicit flags."""
    features = set(SERIAL_PAIR_DEFAULT_FEATURES[backend])
    unknown_flags: list[str] = []

    for raw_flag in raw_flags:
        flag = raw_flag.strip().lower()
        if not flag:
            continue

        expanded = SERIAL_PAIR_FEATURE_ALIASES.get(flag, (flag,))
        for expanded_flag in expanded:
            operation = SERIAL_PAIR_FEATURE_OPERATIONS.get(expanded_flag)
            if operation is None:
                unknown_flags.append(raw_flag)
                continue

            adds, removes = operation
            features.difference_update(removes)
            features.update(adds)

    if unknown_flags:
        raise ValueError(
            "Unknown adapter feature flag(s): "
            + ", ".join(sorted(set(unknown_flags)))
            + ". Supported flags: "
            + ", ".join(
                sorted(
                    set(SERIAL_PAIR_FEATURE_ALIASES)
                    | set(SERIAL_PAIR_FEATURE_OPERATIONS)
                )
            )
        )

    return frozenset(features)


def _get_adapter_pairs(config: pytest.Config) -> list[SerialPairSpec]:
    """Get parsed adapter pair specifications from config."""
    pairs: list[SerialPairSpec] = []

    for pair in config.getoption("--adapter-pair"):
        parts = pair.split(":")
        if len(parts) < 2:
            raise ValueError(
                f"Invalid adapter pair format: {pair}. Expected LEFT:RIGHT[:FLAG...]"
            )

        left, right, *raw_flags = parts
        backend = _classify_adapter_backend(left, right)
        features = _resolve_serial_pair_features(backend, raw_flags)
        pairs.append(
            SerialPairSpec(
                backend=backend,
                left=left,
                right=right,
                features=features,
                pair_label=pair,
            )
        )

    return pairs


def _derive_rfc2217_features(
    features: frozenset[SerialPairFeature],
) -> frozenset[SerialPairFeature]:
    """Apply RFC2217 transport limitations on top of an adapter pair."""
    return frozenset(
        feature
        for feature in features
        if feature in (SerialPairFeature.RXTX, SerialPairFeature.HW)
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize tests based on available backends."""
    if "serial_pair" in metafunc.fixturenames:
        params: list[pytest.ParameterSet] = []

        if SOCAT_BINARY:
            params.append(
                pytest.param(
                    SerialPairSpec(
                        backend=SerialPairBackend.SOCAT,
                        features=SERIAL_PAIR_DEFAULT_FEATURES[SerialPairBackend.SOCAT],
                    ),
                    id="socat",
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
                            backend=SerialPairBackend.ESPHOME,
                            esphome_program=esphome_program,
                            features=SERIAL_PAIR_DEFAULT_FEATURES[
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
                            backend=SerialPairBackend.SOCAT,
                            serial_class_override=cls_name,
                            features=SERIAL_PAIR_DEFAULT_FEATURES[
                                SerialPairBackend.SOCAT
                            ],
                        ),
                        id=f"socat+{cls_name}",
                    )
                )

        params.append(
            pytest.param(
                SerialPairSpec(
                    backend=SerialPairBackend.SOCKET,
                    features=SERIAL_PAIR_DEFAULT_FEATURES[SerialPairBackend.SOCKET],
                ),
                id="socket",
            )
        )

        for cls_name in _get_posix_serial_classes():
            params.append(
                pytest.param(
                    SerialPairSpec(
                        backend=SerialPairBackend.SOCKET,
                        serial_class_override=cls_name,
                        features=SERIAL_PAIR_DEFAULT_FEATURES[SerialPairBackend.SOCKET],
                    ),
                    id=f"socket+{cls_name}",
                )
            )

        for spec in _get_adapter_pairs(metafunc.config):
            assert spec.left is not None
            assert spec.right is not None
            assert spec.pair_label is not None
            params.append(
                pytest.param(
                    spec,
                    marks=[
                        pytest.mark.xdist_group(name=f"pair:{spec.left}:{spec.right}")
                    ],
                    id=spec.pair_label,
                )
            )

            if SER2NET_BINARY is not None:
                params.append(
                    pytest.param(
                        dataclasses.replace(
                            spec,
                            backend=SerialPairBackend.RFC2217,
                            features=_derive_rfc2217_features(spec.features),
                        ),
                        marks=[
                            pytest.mark.xdist_group(
                                name=f"rfc2217:{spec.left}:{spec.right}"
                            )
                        ],
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
                    marks=[
                        pytest.mark.xdist_group(name=f"pair:{spec.left}:{spec.right}")
                    ],
                    id=spec.pair_label,
                )
                for spec in pairs
            ],
        )


@pytest.fixture
def serial_pair(request: pytest.FixtureRequest) -> Generator[SerialPair]:
    """Yield a connected serial port pair with backend metadata.

    Parametrized over all available backends: socat, esphome, socket,
    and any physical adapter pairs passed via --adapter-pair.
    """
    spec: SerialPairSpec = request.param
    backend = spec.backend
    features = spec.features

    for marker in request.node.iter_markers("skip_backends"):
        blocked_backends = {_coerce_serial_pair_backend(arg) for arg in marker.args}
        if backend in blocked_backends:
            pytest.skip(f"Skipped for backend {backend!r}")

    for marker in request.node.iter_markers("require_features"):
        required = {_coerce_serial_pair_feature(arg) for arg in marker.args}
        missing = required - features
        if missing:
            pytest.skip(
                "Skipped because serial_pair lacks features: "
                + _format_serial_pair_features(missing)
            )

    for marker in request.node.iter_markers("skip_features"):
        blocked = {_coerce_serial_pair_feature(arg) for arg in marker.args}
        present = blocked & features
        if present:
            pytest.skip(
                "Skipped because serial_pair exposes features: "
                + _format_serial_pair_features(present)
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
            if backend is SerialPairBackend.SOCAT:
                with create_socat_pair() as (left, right):
                    yield SerialPair(
                        left,
                        right,
                        SerialPairBackend.SOCAT,
                        serial_class,
                        features,
                    )
            elif backend is SerialPairBackend.SOCKET:
                with create_socket_pair() as (left, right):
                    yield SerialPair(
                        left,
                        right,
                        SerialPairBackend.SOCKET,
                        serial_class,
                        features,
                    )
        finally:
            importlib.reload(serialx.platforms)
    elif backend is SerialPairBackend.SOCAT:
        with create_socat_pair() as (left, right):
            yield SerialPair(left, right, SerialPairBackend.SOCAT, features=features)
    elif backend is SerialPairBackend.ESPHOME:
        assert spec.esphome_program is not None
        with create_esphome_pair(spec.esphome_program) as (left, right):
            yield SerialPair(left, right, SerialPairBackend.ESPHOME, features=features)
    elif backend is SerialPairBackend.SOCKET:
        with create_socket_pair() as (left, right):
            yield SerialPair(left, right, SerialPairBackend.SOCKET, features=features)
    elif backend is SerialPairBackend.RFC2217:
        assert SER2NET_BINARY is not None
        assert spec.left is not None
        assert spec.right is not None
        with create_ser2net_pair(spec.left, spec.right) as (left, right):
            yield SerialPair(left, right, SerialPairBackend.RFC2217, features=features)
    elif backend in (
        SerialPairBackend.ADAPTER,
        SerialPairBackend.COM0COM,
        SerialPairBackend.TTY0TTY,
    ):
        assert spec.left is not None
        assert spec.right is not None
        yield SerialPair(spec.left, spec.right, backend, features=features)


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
    if candidate.backend is not SerialPairBackend.COM0COM:
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
