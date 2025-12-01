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
            # For pytest-xdist: group tests by adapter so same adapter doesn't run in parallel
            # Use adapter path as the group ID to ensure tests using the same adapter are sequential
            ids = []
            for adapter in adapters:
                # Create a short, filesystem-safe ID for the group
                # Use the adapter path itself as the group ID
                ids.append(adapter)

            metafunc.parametrize("loopback_adapter", adapters, ids=ids)
        else:
            # No adapters configured, skip all tests requiring loopback_adapter
            pytest.skip("No loopback adapters configured via --loopback-adapter")

    # Check if this test function needs an adapter pair
    if "adapter_pair" in metafunc.fixturenames:
        pairs = _get_adapter_pairs(metafunc.config)
        if pairs:
            # For pytest-xdist: group tests by adapter pair
            # Use both adapters in the group to ensure no conflicts
            ids = []
            for left, right in pairs:
                ids.append(f"{left}:{right}")

            metafunc.parametrize("adapter_pair", pairs, ids=ids)
        else:
            # No pairs configured, skip all tests requiring adapter_pair
            pytest.skip("No adapter pairs configured via --adapter-pair")


def pytest_collection_modifyitems(items):
    """Add xdist_group marks to tests based on their adapter parameters."""
    for item in items:
        # Check if this test uses loopback_adapter parameter
        if hasattr(item, "callspec") and "loopback_adapter" in item.callspec.params:
            adapter = item.callspec.params["loopback_adapter"]
            # Add xdist_group mark with the adapter path as the group name
            item.add_marker(pytest.mark.xdist_group(name=f"adapter:{adapter}"))

        # Check if this test uses adapter_pair parameter
        elif hasattr(item, "callspec") and "adapter_pair" in item.callspec.params:
            left, right = item.callspec.params["adapter_pair"]
            # Group by both adapters to ensure no conflicts
            # Tests using the same pair will be in the same group
            item.add_marker(pytest.mark.xdist_group(name=f"pair:{left}:{right}"))
