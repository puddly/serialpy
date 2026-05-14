"""Granular lifecycle and race-condition tests for async serial transports."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
import contextlib
import enum
import gc
import importlib
import sys
import threading
from typing import Any
from unittest.mock import patch
import warnings

import pytest

from serialx import BaseSerialTransport, create_serial_connection
from tests.common import SerialBackend, SerialPair


class ProtocolState(enum.Enum):
    """Asyncio Protocol lifecycle state."""

    INIT = "init"  # constructed, before connection_made
    MADE = "made"  # connection_made called, before connection_lost
    LOST = "lost"  # connection_lost called (terminal)


class RecordingProtocol(asyncio.Protocol):
    """asyncio.Protocol that enforces lifecycle invariants and records data."""

    connection_made_transport: BaseSerialTransport | None
    connection_lost_exc: Exception | None

    def __init__(self) -> None:
        """Initialize in the INIT state."""
        self._state = ProtocolState.INIT
        self.violations: list[str] = []
        self.connection_made_transport = None
        self.connection_lost_exc = None
        self.data_received_chunks: list[bytes] = []
        self.eof_received_calls: int = 0
        self.pause_writing_calls: int = 0
        self.resume_writing_calls: int = 0

    @property
    def state(self) -> ProtocolState:
        """Current lifecycle state."""
        return self._state

    def assert_state(self, expected: ProtocolState) -> None:
        """Assert the protocol is in `expected` state."""
        assert self._state is expected, f"state={self._state}, expected={expected}"

    def _require_state(self, callback: str, *expected: ProtocolState) -> None:
        if self._state in expected:
            return
        allowed = ", ".join(s.value for s in expected)
        self.violations.append(
            f"{callback} called in state {self._state.value!r}; allowed: {{{allowed}}}"
        )

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Enforce INIT -> MADE."""
        self._require_state("connection_made", ProtocolState.INIT)
        assert isinstance(transport, BaseSerialTransport)
        self.connection_made_transport = transport
        self._state = ProtocolState.MADE

    def connection_lost(self, exc: Exception | None) -> None:
        """Enforce MADE -> LOST."""
        self._require_state("connection_lost", ProtocolState.MADE)
        self.connection_lost_exc = exc
        self._state = ProtocolState.LOST

    def data_received(self, data: bytes) -> None:
        """Record an incoming chunk; only valid in MADE."""
        self._require_state("data_received", ProtocolState.MADE)
        self.data_received_chunks.append(data)

    def eof_received(self) -> bool | None:
        """Record an EOF; only valid in MADE."""
        self._require_state("eof_received", ProtocolState.MADE)
        self.eof_received_calls += 1
        return None

    def pause_writing(self) -> None:
        """Only valid in MADE."""
        self._require_state("pause_writing", ProtocolState.MADE)
        self.pause_writing_calls += 1

    def resume_writing(self) -> None:
        """Only valid in MADE."""
        self._require_state("resume_writing", ProtocolState.MADE)
        self.resume_writing_calls += 1

    def assert_clean(self) -> None:
        """Fail the test if any state violation was recorded."""
        if self.violations:
            pytest.fail("Protocol violations:\n  " + "\n  ".join(self.violations))

    @property
    def total_received(self) -> bytes:
        """Concatenation of all received chunks."""
        return b"".join(self.data_received_chunks)


# --- Successful lifecycle: callbacks fire exactly once ---


async def test_lifecycle_normal_close_callbacks(serial_pair: SerialPair) -> None:
    """Connect + graceful close: state machine traverses INIT -> MADE -> LOST."""
    loop = asyncio.get_running_loop()
    protocol = RecordingProtocol()

    transport, _ = await create_serial_connection(
        loop, lambda: protocol, serial_pair.left, baudrate=115200
    )
    protocol.assert_state(ProtocolState.MADE)
    assert protocol.connection_made_transport is transport

    transport.close()
    await transport.wait_closed()

    protocol.assert_state(ProtocolState.LOST)
    assert protocol.connection_lost_exc is None
    protocol.assert_clean()


async def test_lifecycle_abort_callbacks(serial_pair: SerialPair) -> None:
    """Connect + abort: traverses INIT -> MADE -> LOST."""
    loop = asyncio.get_running_loop()
    protocol = RecordingProtocol()

    transport, _ = await create_serial_connection(
        loop, lambda: protocol, serial_pair.left, baudrate=115200
    )

    transport.abort()
    await transport.wait_closed()

    assert protocol.state is ProtocolState.LOST
    assert protocol.connection_lost_exc is None
    protocol.assert_clean()


# --- Cancellation during connect ---


async def test_lifecycle_cancel_during_connect_no_callbacks(
    serial_pair: SerialPair,
) -> None:
    """Cancel mid-connect: ends in INIT or LOST, never an inconsistent state."""
    loop = asyncio.get_running_loop()
    protocol = RecordingProtocol()

    connect_task = asyncio.create_task(
        create_serial_connection(
            loop, lambda: protocol, serial_pair.left, baudrate=115200
        )
    )
    await asyncio.sleep(0)
    connect_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await connect_task

    assert protocol.state in (ProtocolState.INIT, ProtocolState.LOST)
    protocol.assert_clean()


# --- Idempotency under repeated close/abort ---


async def test_lifecycle_close_close_one_connection_lost(
    serial_pair: SerialPair,
) -> None:
    """Two close() calls: state machine ensures connection_lost fires once."""
    loop = asyncio.get_running_loop()
    protocol = RecordingProtocol()

    transport, _ = await create_serial_connection(
        loop, lambda: protocol, serial_pair.left, baudrate=115200
    )

    transport.close()
    transport.close()
    await transport.wait_closed()

    assert protocol.state is ProtocolState.LOST
    protocol.assert_clean()


async def test_lifecycle_abort_after_close_one_connection_lost(
    serial_pair: SerialPair,
) -> None:
    """abort() after close()."""
    loop = asyncio.get_running_loop()
    protocol = RecordingProtocol()

    transport, _ = await create_serial_connection(
        loop, lambda: protocol, serial_pair.left, baudrate=115200
    )

    transport.close()
    transport.abort()
    await transport.wait_closed()

    assert protocol.state is ProtocolState.LOST
    protocol.assert_clean()


async def test_lifecycle_close_after_abort_one_connection_lost(
    serial_pair: SerialPair,
) -> None:
    """close() after abort()."""
    loop = asyncio.get_running_loop()
    protocol = RecordingProtocol()

    transport, _ = await create_serial_connection(
        loop, lambda: protocol, serial_pair.left, baudrate=115200
    )

    transport.abort()
    transport.close()
    await transport.wait_closed()

    assert protocol.state is ProtocolState.LOST
    protocol.assert_clean()


# --- Drain vs. abort semantics ---


async def test_lifecycle_close_drains_pending_writes(
    serial_pair: SerialPair,
) -> None:
    """close() with buffered data delivers all bytes to the peer."""
    loop = asyncio.get_running_loop()
    payload = bytes(range(256)) * 16  # 4 KiB
    sender_proto = RecordingProtocol()
    receiver_proto = RecordingProtocol()

    sender, _ = await create_serial_connection(
        loop, lambda: sender_proto, serial_pair.left, baudrate=115200
    )
    receiver, _ = await create_serial_connection(
        loop, lambda: receiver_proto, serial_pair.right, baudrate=115200
    )

    try:
        sender.write(payload)
        sender.close()  # drain semantics
        await sender.wait_closed()

        # Wait until we've seen the full payload or read times out.
        deadline = loop.time() + 5.0
        while len(receiver_proto.total_received) < len(payload):
            if loop.time() >= deadline:
                break
            await asyncio.sleep(0.05)

        assert receiver_proto.total_received == payload
    finally:
        receiver.close()
        await receiver.wait_closed()
    sender_proto.assert_clean()
    receiver_proto.assert_clean()


async def test_lifecycle_abort_during_drain_escalates(
    serial_pair: SerialPair,
) -> None:
    """abort() called while close()'s drain is pending must escalate to abort semantics."""
    loop = asyncio.get_running_loop()
    sender_proto = RecordingProtocol()
    receiver_proto = RecordingProtocol()

    sender, _ = await create_serial_connection(
        loop, lambda: sender_proto, serial_pair.left, baudrate=115200
    )
    receiver, _ = await create_serial_connection(
        loop, lambda: receiver_proto, serial_pair.right, baudrate=115200
    )

    try:
        # Large payload to overflow the kernel TTY buffer and force user-space buffering.
        sender.write(b"\x55" * (4 * 1024 * 1024))

        if sender.get_write_buffer_size() == 0:
            pytest.skip("Backend absorbed the entire write synchronously")

        # close() starts a drain because buffer is non-empty.
        sender.close()
        assert sender.is_closing()
        assert sender.get_write_buffer_size() > 0, (
            "close() with a pending buffer must NOT clear it synchronously"
        )

        # abort() must escalate: buffer cleared, transport heading for closed.
        sender.abort()
        assert sender.get_write_buffer_size() == 0, (
            "abort() during drain must clear the buffer synchronously"
        )

        await sender.wait_closed()
        assert sender_proto.state is ProtocolState.LOST
    finally:
        sender.close()
        receiver.close()
        await sender.wait_closed()
        await receiver.wait_closed()
    sender_proto.assert_clean()
    receiver_proto.assert_clean()


# --- EOF from peer ---


async def test_lifecycle_peer_close_calls_connection_lost(
    serial_pair: SerialPair,
) -> None:
    """When the peer closes, our protocol gets connection_lost(None)."""
    if sys.platform == "win32":
        pytest.skip("Windows does not signal EOF on serial-pair peer close")

    loop = asyncio.get_running_loop()
    left_proto = RecordingProtocol()
    right_proto = RecordingProtocol()

    left, _ = await create_serial_connection(
        loop, lambda: left_proto, serial_pair.left, baudrate=115200
    )
    right, _ = await create_serial_connection(
        loop, lambda: right_proto, serial_pair.right, baudrate=115200
    )

    try:
        right.close()
        await right.wait_closed()

        # Wait for our side to observe the peer close.
        deadline = loop.time() + 5.0
        while left_proto.state is not ProtocolState.LOST:
            if loop.time() >= deadline:
                break
            await asyncio.sleep(0.05)
    finally:
        left.close()
        await left.wait_closed()

    assert left_proto.state is ProtocolState.LOST
    assert left_proto.connection_lost_exc is None
    left_proto.assert_clean()
    right_proto.assert_clean()


# --- is_closing() state machine ---


async def test_lifecycle_is_closing_states(serial_pair: SerialPair) -> None:
    """is_closing() reflects the lifecycle: False -> True at close request -> True after wait."""
    loop = asyncio.get_running_loop()
    protocol = RecordingProtocol()

    transport, _ = await create_serial_connection(
        loop, lambda: protocol, serial_pair.left, baudrate=115200
    )

    assert transport.is_closing() is False

    transport.close()
    assert transport.is_closing() is True

    await transport.wait_closed()
    assert transport.is_closing() is True
    protocol.assert_clean()


# --- Concurrent wait_closed waiters ---


async def test_lifecycle_concurrent_wait_closed(serial_pair: SerialPair) -> None:
    """All concurrent wait_closed() awaiters resolve when the transport closes."""
    loop = asyncio.get_running_loop()
    protocol = RecordingProtocol()

    transport, _ = await create_serial_connection(
        loop, lambda: protocol, serial_pair.left, baudrate=115200
    )

    waiters = [asyncio.create_task(transport.wait_closed()) for _ in range(5)]
    await asyncio.sleep(0)
    transport.close()

    results = await asyncio.gather(*waiters)
    assert results == [None] * 5
    assert protocol.state is ProtocolState.LOST
    protocol.assert_clean()


# --- Open failure path ---


async def test_lifecycle_open_failure_no_callbacks() -> None:
    """A failed os.open: connect raises, with no protocol callbacks fired."""
    if sys.platform == "emscripten":
        pytest.skip("No POSIX/Windows-style device paths under Pyodide")

    loop = asyncio.get_running_loop()
    path = "COM25" if sys.platform == "win32" else "/dev/this_port_does_not_exist"
    protocol = RecordingProtocol()

    with pytest.raises(OSError):
        await create_serial_connection(loop, lambda: protocol, path, baudrate=115200)

    assert protocol.state is ProtocolState.INIT
    protocol.assert_clean()


# --- Close from inside connection_made (re-entrancy) ---


async def test_lifecycle_close_from_connection_made(serial_pair: SerialPair) -> None:
    """A protocol that calls close() inside connection_made: lost still fires once."""
    loop = asyncio.get_running_loop()

    class CloseInsideMade(RecordingProtocol):
        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            super().connection_made(transport)
            assert isinstance(transport, BaseSerialTransport)
            transport.close()

    protocol = CloseInsideMade()
    transport, _ = await create_serial_connection(
        loop, lambda: protocol, serial_pair.left, baudrate=115200
    )

    await transport.wait_closed()

    assert protocol.state is ProtocolState.LOST
    protocol.assert_clean()


async def test_lifecycle_abort_from_connection_made(serial_pair: SerialPair) -> None:
    """A protocol that calls abort() inside connection_made: lost still fires once."""
    loop = asyncio.get_running_loop()

    class AbortInsideMade(RecordingProtocol):
        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            super().connection_made(transport)
            assert isinstance(transport, BaseSerialTransport)
            transport.write(b"this should be discarded")
            transport.abort()

    protocol = AbortInsideMade()
    transport, _ = await create_serial_connection(
        loop, lambda: protocol, serial_pair.left, baudrate=115200
    )

    await transport.wait_closed()

    assert protocol.state is ProtocolState.LOST
    protocol.assert_clean()


# --- Multiple cycles on the same path ---


async def test_lifecycle_repeated_open_close_cycles(
    serial_pair: SerialPair,
) -> None:
    """Repeated open/close cycles each produce one made + one lost callback."""
    loop = asyncio.get_running_loop()

    def make_factory(p: RecordingProtocol) -> Callable[[], RecordingProtocol]:
        return lambda: p

    for _ in range(5):
        protocol = RecordingProtocol()
        transport, _ = await create_serial_connection(
            loop, make_factory(protocol), serial_pair.left, baudrate=115200
        )
        transport.close()
        await transport.wait_closed()

        assert protocol.state is ProtocolState.LOST
        protocol.assert_clean()


# --- Writes after close are silently dropped ---


async def test_lifecycle_write_after_close_is_dropped(serial_pair: SerialPair) -> None:
    """Writes after close() are silently dropped (no exception, no delivery)."""
    loop = asyncio.get_running_loop()
    sender_proto = RecordingProtocol()
    receiver_proto = RecordingProtocol()

    sender, _ = await create_serial_connection(
        loop, lambda: sender_proto, serial_pair.left, baudrate=115200
    )
    receiver, _ = await create_serial_connection(
        loop, lambda: receiver_proto, serial_pair.right, baudrate=115200
    )

    try:
        sender.close()
        # These should be silently ignored
        for _ in range(10):
            sender.write(b"after close")

        await sender.wait_closed()

        # Drain the receiver briefly to make sure nothing leaked through.
        await asyncio.sleep(0.1)
        assert receiver_proto.data_received_chunks == []
    finally:
        receiver.close()
        await receiver.wait_closed()
    sender_proto.assert_clean()
    receiver_proto.assert_clean()


# --- Connection ordering: connection_made must precede any data_received ---


async def test_lifecycle_data_received_after_connection_made(
    serial_pair: SerialPair,
) -> None:
    """data_received must arrive only after connection_made (enforced by state machine)."""
    loop = asyncio.get_running_loop()

    left_proto = RecordingProtocol()
    right_proto = RecordingProtocol()

    left, _ = await create_serial_connection(
        loop, lambda: left_proto, serial_pair.left, baudrate=115200
    )
    right, _ = await create_serial_connection(
        loop, lambda: right_proto, serial_pair.right, baudrate=115200
    )

    try:
        right.write(b"hello")
        await right.flush()

        deadline = loop.time() + 2.0
        while left_proto.total_received != b"hello":
            if loop.time() >= deadline:
                break
            await asyncio.sleep(0.01)

        assert left_proto.total_received == b"hello"
    finally:
        left.close()
        right.close()
        await left.wait_closed()
        await right.wait_closed()
    left_proto.assert_clean()
    right_proto.assert_clean()


# --- _Pure_ wait_closed / close ordering, no transport state checks ---


async def test_lifecycle_wait_closed_before_close_blocks(
    serial_pair: SerialPair,
) -> None:
    """wait_closed() awaited before close() must not resolve until close() is called."""
    loop = asyncio.get_running_loop()
    protocol = RecordingProtocol()

    transport, _ = await create_serial_connection(
        loop, lambda: protocol, serial_pair.left, baudrate=115200
    )

    waiter = asyncio.create_task(transport.wait_closed())
    await asyncio.sleep(0.1)
    assert not waiter.done(), "wait_closed must not resolve before close()"

    transport.close()
    await waiter
    assert protocol.state is ProtocolState.LOST
    protocol.assert_clean()


# --- Connect kwargs: a misconfigured kwarg surfaces as an exception ---


async def test_lifecycle_invalid_kwarg_surfaces_no_callbacks(
    serial_pair: SerialPair,
) -> None:
    """A bad kwarg during connect raises and produces no protocol callbacks."""
    if SerialBackend.SOCKET in serial_pair.backends:
        pytest.skip("socket transport does not validate serial settings")

    loop = asyncio.get_running_loop()
    protocol = RecordingProtocol()

    with pytest.raises(Exception):
        await create_serial_connection(
            loop,
            lambda: protocol,
            serial_pair.left,
            baudrate=115200,
            byte_size=99,
        )

    assert protocol.state in (ProtocolState.INIT, ProtocolState.LOST)
    protocol.assert_clean()


# --- Cancellation race: fd must not leak when os.open is mid-syscall ---


@contextlib.contextmanager
def patch_slow(*targets: str) -> Iterator[tuple[threading.Event, threading.Event]]:
    """Patch each target callable to block on `proceed` after signaling `started`."""
    started = threading.Event()
    proceed = threading.Event()

    def make_slow(real_fn: Callable[..., Any]) -> Callable[..., Any]:
        def slow(*args: Any, **kwargs: Any) -> Any:
            started.set()
            if not proceed.wait(timeout=5.0):
                raise TimeoutError("test setup: proceed never released")
            return real_fn(*args, **kwargs)

        return slow

    with contextlib.ExitStack() as stack:
        for target in targets:
            module_path, _, attr_name = target.rpartition(".")
            module = importlib.import_module(module_path)
            real = getattr(module, attr_name)
            stack.enter_context(patch(target, new=make_slow(real)))

        try:
            yield started, proceed
        finally:
            proceed.set()


async def test_lifecycle_no_fd_leak_when_internal_task_cancelled_during_open(
    serial_pair: SerialPair,
) -> None:
    """Cancelling internal tasks during connect must not leak resources."""
    loop = asyncio.get_running_loop()
    protocol = RecordingProtocol()

    slow_targets = ["os.open"]

    if sys.platform == "win32":
        slow_targets.append("serialx.platforms.serial_win32.CreateFile")

    with patch_slow(*slow_targets) as (started, proceed):
        existing_tasks = asyncio.all_tasks(loop)

        async def connect() -> None:
            transport, _ = await create_serial_connection(
                loop, lambda: protocol, serial_pair.left, baudrate=115200
            )
            transport.close()
            await transport.wait_closed()

        connect_task = asyncio.create_task(connect())

        # Wait briefly for a patched syscall to fire. Network backends never
        # hit one; for those this just gives the connect a head start.
        await loop.run_in_executor(None, started.wait, 1.0)

        # Cancel every transport-internal in-flight task. When a patched
        # syscall is mid-flight this hits the cancel window.
        for task in asyncio.all_tasks(loop) - existing_tasks:
            if task is asyncio.current_task() or task.done():
                continue
            task.cancel()

        # Release any blocked syscall. The real call returns its handle/fd,
        # and the executor tries to deliver to a (possibly cancelled) future.
        proceed.set()

        with contextlib.suppress(asyncio.CancelledError):
            await connect_task


def test_lifecycle_close_without_wait_closed_no_warnings(
    serial_pair: SerialPair,
) -> None:
    """close() and then shutdown doesn't log `Task was destroyed but it is pending`."""
    if SerialBackend.ESPHOME_HOST in serial_pair.backends:
        pytest.skip(
            "TODO: aioesphomeapi has no public sync force-disconnect; "
            "see serial_esphome.py"
        )

    handler_calls: list[dict[str, Any]] = []

    async def main() -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await create_serial_connection(
            loop, asyncio.Protocol, serial_pair.left, baudrate=115200
        )
        transport.close()
        # Intentionally NOT awaiting `wait_closed`

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")

        loop = asyncio.new_event_loop()
        loop.set_exception_handler(lambda _loop, ctx: handler_calls.append(ctx))

        try:
            loop.run_until_complete(main())
        finally:
            loop.close()

        # Force GC so Task.__del__ runs and any "destroyed but pending" diagnostic
        # reaches the exception handler before we check.
        gc.collect()

    assert not caught_warnings
