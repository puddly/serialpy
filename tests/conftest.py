"""Pytest configuration for serialx tests."""

import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "xdist_group(name): group tests for pytest-xdist parallel execution control",
    )


def pytest_addoption(parser):
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


def _get_loopback_adapters(config):
    """Get list of loopback adapters from config."""
    return config.getoption("--loopback-adapter")


def _get_adapter_pairs(config):
    """Get list of adapter pairs from config."""
    pairs_raw = config.getoption("--adapter-pair")
    pairs = []

    for pair in pairs_raw:
        parts = pair.split(":")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid adapter pair format: {pair}. Expected LEFT:RIGHT"
            )
        pairs.append((parts[0], parts[1]))

    return pairs


def pytest_generate_tests(metafunc):
    """Parametrize tests based on configured adapters."""
    # Check if this test function needs a loopback adapter
    if "loopback_adapter" in metafunc.fixturenames:
        adapters = _get_loopback_adapters(metafunc.config)
        if adapters:
            #  Create marks for each adapter value
            argvalues = []
            for adapter in adapters:
                # Create a parametrize mark with xdist_group for this specific adapter
                mark = pytest.mark.xdist_group(name=f"adapter:{adapter}")
                argvalues.append(pytest.param(adapter, marks=[mark], id=adapter))

            metafunc.parametrize("loopback_adapter", argvalues)
        else:
            # No adapters configured, skip all tests requiring loopback_adapter
            pytest.skip("No loopback adapters configured via --loopback-adapter")

    # Check if this test function needs an adapter pair
    if "adapter_pair" in metafunc.fixturenames:
        pairs = _get_adapter_pairs(metafunc.config)
        if pairs:
            argvalues = []
            for left, right in pairs:
                group_name = f"pair:{left}:{right}"
                mark = pytest.mark.xdist_group(name=group_name)
                argvalues.append(
                    pytest.param((left, right), marks=[mark], id=f"{left}:{right}")
                )

            metafunc.parametrize("adapter_pair", argvalues)
        else:
            # No pairs configured, skip all tests requiring adapter_pair
            pytest.skip("No adapter pairs configured via --adapter-pair")
