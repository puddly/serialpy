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
            SerialPairFeature.READ_BUFFER,
        }
    ),
    SerialPairBackend.RFC2217: frozenset({SerialPairFeature.RXTX}),
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

    left_backend: SerialPairBackend
    right_backend: SerialPairBackend
    features: frozenset[SerialPairFeature]
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
            "Pair of serial endpoints in format LEFT,RIGHT[,FLAG...] "
            "(for example /dev/ttyUSB1,/dev/ttyUSB2,rxtx,hw,nobuf or "
            "rfc2217://127.0.0.1:5001,rfc2217://127.0.0.1:5002,rxtx)"
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
    left_backend: SerialPairBackend,
    right_backend: SerialPairBackend,
    raw_flags: list[str],
) -> frozenset[SerialPairFeature]:
    """Resolve normalized feature tags for a backend plus explicit flags."""
    features = set(
        SERIAL_PAIR_DEFAULT_FEATURES[left_backend]
        & SERIAL_PAIR_DEFAULT_FEATURES[right_backend]
    )
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
        features = _resolve_serial_pair_features(left_backend, right_backend, raw_flags)
        pairs.append(
            SerialPairSpec(
                left_backend=left_backend,
                right_backend=right_backend,
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


def _build_rfc2217_spec(spec: SerialPairSpec) -> SerialPairSpec:
    """Build an RFC2217 variant that wraps an existing serial pair spec."""
    return dataclasses.replace(
        spec,
        left_backend=SerialPairBackend.RFC2217,
        right_backend=SerialPairBackend.RFC2217,
        features=_derive_rfc2217_features(spec.features),
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
                features=SERIAL_PAIR_DEFAULT_FEATURES[SerialPairBackend.SOCAT],
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
                            left_backend=SerialPairBackend.SOCAT,
                            right_backend=SerialPairBackend.SOCAT,
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
                    left_backend=SerialPairBackend.SOCKET,
                    right_backend=SerialPairBackend.SOCKET,
                    features=SERIAL_PAIR_DEFAULT_FEATURES[SerialPairBackend.SOCKET],
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
                        features=SERIAL_PAIR_DEFAULT_FEATURES[SerialPairBackend.SOCKET],
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
    features = spec.features

    for marker in request.node.iter_markers("skip_backends"):
        blocked_backends = {_coerce_serial_pair_backend(arg) for arg in marker.args}
        present_backends = pair_backends & blocked_backends
        if present_backends:
            pytest.skip(
                "Skipped for endpoint backends: "
                + ", ".join(sorted(backend.value for backend in present_backends))
            )

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
        present_features = blocked & features
        if present_features:
            pytest.skip(
                "Skipped because serial_pair exposes features: "
                + _format_serial_pair_features(present_features)
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
                        features,
                    )
            elif spec.backends == {SerialPairBackend.SOCKET}:
                with create_socket_pair() as (left, right):
                    yield SerialPair(
                        left,
                        right,
                        SerialPairBackend.SOCKET,
                        SerialPairBackend.SOCKET,
                        serial_class,
                        features,
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
                features=features,
            )
    elif spec.backends == {SerialPairBackend.ESPHOME}:
        assert spec.esphome_program is not None
        with create_esphome_pair(spec.esphome_program) as (left, right):
            yield SerialPair(
                left,
                right,
                SerialPairBackend.ESPHOME,
                SerialPairBackend.ESPHOME,
                features=features,
            )
    elif spec.backends == {SerialPairBackend.SOCKET}:
        with create_socket_pair() as (left, right):
            yield SerialPair(
                left,
                right,
                SerialPairBackend.SOCKET,
                SerialPairBackend.SOCKET,
                features=features,
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
                    features=features,
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
                        features=features,
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
                features=features,
            )
        else:
            yield SerialPair(
                spec.left,
                spec.right,
                spec.left_backend,
                spec.right_backend,
                features=features,
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
