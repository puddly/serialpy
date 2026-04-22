"""Fixture helper: register a `js.create_fake_serial_pair()` pair under URLs.

Imported only from code paths gated on `sys.platform == "emscripten"`.
"""

from collections.abc import Iterator
import contextlib

import js

from serialx.platforms.serial_pyodide import register_js_port, unregister_js_port

_PAIR_COUNTER = 0


@contextlib.contextmanager
def create_pyodide_pair() -> Iterator[tuple[str, str]]:
    """Create a fake Web Serial pair and register each side at a unique URL."""
    global _PAIR_COUNTER  # noqa: PLW0603
    _PAIR_COUNTER += 1
    left_url = f"pyodide://pair{_PAIR_COUNTER}-left"
    right_url = f"pyodide://pair{_PAIR_COUNTER}-right"

    left_port, right_port = js.create_fake_serial_pair()
    register_js_port(left_url, left_port)
    register_js_port(right_url, right_port)
    try:
        yield (left_url, right_url)
    finally:
        unregister_js_port(left_url)
        unregister_js_port(right_url)
