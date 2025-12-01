"""Pytest configuration for serialx tests."""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "xdist_group(name): group tests for pytest-xdist parallel execution control",
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom command line options for serial adapter configuration."""
    parser.addoption(
        "--loopback-adapter",
        action="append",
        default=[],
        help="Serial loopback adapter device path (can be specified multiple times)",
    )
    parser.addoption(
        "--adapter-pair",
        action="append",
        default=[],
        help="Pair of serial adapters in format LEFT:RIGHT (can be specified multiple times)",
    )


def _get_loopback_adapters(config: pytest.Config) -> list[str]:
    """Get list of loopback adapters from config."""
    return config.getoption("--loopback-adapter")


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
    """Parametrize tests based on configured adapters."""
    if "loopback_adapter" in metafunc.fixturenames:
        adapters = _get_loopback_adapters(metafunc.config)

        if adapters:
            metafunc.parametrize(
                "loopback_adapter",
                [
                    pytest.param(
                        adapter,
                        marks=[pytest.mark.xdist_group(name=f"adapter:{adapter}")],
                        id=f"{adapter}",
                    )
                    for adapter in adapters
                ],
            )
        else:
            pytest.skip("No loopback adapters configured via --loopback-adapter")

    if "adapter_pair" in metafunc.fixturenames:
        pairs = _get_adapter_pairs(metafunc.config)

        if pairs:
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
        else:
            pytest.skip("No adapter pairs configured via --adapter-pair")
