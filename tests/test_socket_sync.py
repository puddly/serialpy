"""Test sync APIs with socket:// endpoints."""

from collections.abc import Iterator
import contextlib
import logging
import os
import queue
import socket
import threading
import time

import pytest

from serialx import ModemPins, Parity, PinState, StopBits, get_serial_classes
from serialx.platforms.serial_socket import SocketSerial, SocketSerialTransport

LOGGER = logging.getLogger(__name__)


class _SocketPairRelay:
    def __init__(self) -> None:
        self.left_to_right: queue.Queue[bytes] = queue.Queue()
        self.right_to_left: queue.Queue[bytes] = queue.Queue()
        self.stop_event = threading.Event()
        self.active_connections: dict[str, socket.socket | None] = {
            "left": None,
            "right": None,
        }
        self.active_lock = threading.Lock()
        self.relay_threads: list[threading.Thread] = []
        self.left_server = self._make_server()
        self.right_server = self._make_server()
        self.left_url = f"socket://127.0.0.1:{self.left_server.getsockname()[1]}"
        self.right_url = f"socket://127.0.0.1:{self.right_server.getsockname()[1]}"

    @staticmethod
    def _close_socket(sock: socket.socket | None) -> None:
        if sock is None:
            return
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            sock.close()

    @staticmethod
    def _make_server() -> socket.socket:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(0.1)
        return server

    def _set_active_connection(self, side: str, conn: socket.socket) -> None:
        with self.active_lock:
            previous = self.active_connections[side]
            self.active_connections[side] = conn
        if previous is not conn:
            self._close_socket(previous)

    def _clear_active_connection(self, side: str, conn: socket.socket) -> None:
        with self.active_lock:
            if self.active_connections[side] is conn:
                self.active_connections[side] = None

    def _get_active_connection(self, side: str) -> socket.socket | None:
        with self.active_lock:
            return self.active_connections[side]

    def _reader_loop(
        self,
        side: str,
        conn: socket.socket,
        outbound_queue: queue.Queue[bytes],
        peer_side: str,
    ) -> None:
        try:
            while not self.stop_event.is_set():
                data = conn.recv(4096)
                if not data:
                    LOGGER.debug("%s client reached EOF", side)
                    return
                outbound_queue.put(data)
                LOGGER.debug(
                    "queued %d bytes from %s to %s",
                    len(data),
                    side,
                    peer_side,
                )
        except OSError:
            LOGGER.debug("%s client disconnected abruptly", side, exc_info=True)
        finally:
            self._clear_active_connection(side, conn)
            self._close_socket(conn)
            LOGGER.debug("closed %s client connection", side)

    def _accept_loop(
        self,
        side: str,
        server_sock: socket.socket,
        outbound_queue: queue.Queue[bytes],
        peer_side: str,
    ) -> None:
        while not self.stop_event.is_set():
            try:
                conn, _ = server_sock.accept()
            except TimeoutError:
                continue
            except OSError:
                if not self.stop_event.is_set():
                    LOGGER.debug("%s server accept failed", side, exc_info=True)
                return

            LOGGER.debug("accepted %s client connection", side)
            self._set_active_connection(side, conn)

            reader_thread = threading.Thread(
                target=self._reader_loop,
                args=(side, conn, outbound_queue, peer_side),
                daemon=True,
            )
            self.relay_threads.append(reader_thread)
            reader_thread.start()

    def _writer_loop(
        self,
        side: str,
        inbound_queue: queue.Queue[bytes],
        peer_side: str,
    ) -> None:
        while not self.stop_event.is_set():
            try:
                data = inbound_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            conn = self._get_active_connection(side)
            while conn is None and not self.stop_event.is_set():
                time.sleep(0.001)
                conn = self._get_active_connection(side)
            if conn is None:
                continue

            try:
                conn.sendall(data)
                LOGGER.debug(
                    "forwarded %d bytes from %s to %s",
                    len(data),
                    peer_side,
                    side,
                )
            except OSError:
                self._clear_active_connection(side, conn)
                self._close_socket(conn)
                LOGGER.debug(
                    "failed forwarding bytes from %s to %s",
                    peer_side,
                    side,
                    exc_info=True,
                )

    def start(self) -> None:
        LOGGER.debug(
            "started socket pair server left=%s right=%s",
            self.left_url,
            self.right_url,
        )
        self.relay_threads.extend(
            [
                threading.Thread(
                    target=self._accept_loop,
                    args=("left", self.left_server, self.left_to_right, "right"),
                    daemon=True,
                ),
                threading.Thread(
                    target=self._accept_loop,
                    args=("right", self.right_server, self.right_to_left, "left"),
                    daemon=True,
                ),
                threading.Thread(
                    target=self._writer_loop,
                    args=("left", self.right_to_left, "right"),
                    daemon=True,
                ),
                threading.Thread(
                    target=self._writer_loop,
                    args=("right", self.left_to_right, "left"),
                    daemon=True,
                ),
            ]
        )
        for relay_thread in self.relay_threads:
            relay_thread.start()

    def close(self) -> None:
        self.stop_event.set()
        self._close_socket(self.left_server)
        self._close_socket(self.right_server)
        with self.active_lock:
            connections = list(self.active_connections.values())
            self.active_connections["left"] = None
            self.active_connections["right"] = None
        for conn in connections:
            self._close_socket(conn)
        for relay_thread in self.relay_threads:
            relay_thread.join(timeout=1)
        LOGGER.debug("stopped socket pair servers")


@contextlib.contextmanager
def create_socket_pair() -> Iterator[tuple[str, str]]:
    """Create two socket:// endpoints backed by a bidirectional relay."""
    relay = _SocketPairRelay()
    relay.start()
    try:
        yield (relay.left_url, relay.right_url)
    finally:
        relay.close()


def test_socket_url_uses_dedicated_socket_platform() -> None:
    """Test socket:// uses dedicated socket serial and transport classes."""
    serial_cls, transport_cls = get_serial_classes("socket://127.0.0.1:12345")

    assert serial_cls is SocketSerial
    assert transport_cls is SocketSerialTransport


def test_all_bytes_socket() -> None:
    """Test that all bytes 0-255 can be transmitted."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial_left,
        SocketSerial(right, baudrate=115200) as serial_right,
    ):
        # Create a byte array with all possible byte values
        data = bytes(range(256))

        serial_left.write(data)
        result = serial_right.readexactly(len(data))

        assert result == data


def test_segmented_binary_data_socket() -> None:
    """Test binary data sent in segments."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial_left,
        SocketSerial(right, baudrate=115200) as serial_right,
    ):
        # Send all bytes in smaller segments
        segment_size = 16
        data = bytes(range(256))

        for i in range(0, 256, segment_size):
            segment = data[i : i + segment_size]
            serial_left.write(segment)
            result = serial_right.readexactly(len(segment))
            assert result == segment


@pytest.mark.parametrize(
    "size",
    [1, 16, 64, 256, 512, 1024],
)
def test_binary_payload_sizes_socket(size: int) -> None:
    """Test various binary payload sizes."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial_left,
        SocketSerial(right, baudrate=115200) as serial_right,
    ):
        # Create binary data with repeating pattern
        data = bytes([i % 256 for i in range(size)])

        serial_left.write(data)
        result = serial_right.readexactly(len(data))

        assert result == data


def test_null_bytes_socket() -> None:
    """Test that null bytes (0x00) can be transmitted."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial_left,
        SocketSerial(right, baudrate=115200) as serial_right,
    ):
        # Send a bunch of null bytes
        null_data = b"\x00" * 64

        serial_left.write(null_data)
        result = serial_right.readexactly(len(null_data))

        assert result == null_data


def test_overlapping_read_write_socket() -> None:
    """Test that read and write can overlap, data is buffered."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial_left,
        SocketSerial(right, baudrate=115200) as serial_right,
    ):
        data = bytes(range(256))
        read = b""

        serial_left.write(data[:100])
        read += serial_right.readexactly(10)
        serial_left.write(data[100:150])
        read += serial_right.readexactly(10)
        serial_left.write(data[150:])
        read += serial_right.readexactly(10)
        read += serial_right.readexactly(256 - 30)

        assert read == data


@pytest.mark.parametrize(
    ("baudrate", "chunk_size"),
    [
        (9600, 1),
        (9600, 16),
        (115200, 1),
        (115200, 16),
        (115200, 64),
        (921600, 1),
        (921600, 16),
        (921600, 256),
        (921600, 1024),
    ],
)
def test_random_large_socket(baudrate: int, chunk_size: int) -> None:
    """Test loopback adapter random read/write."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=baudrate) as serial_left,
        SocketSerial(right, baudrate=baudrate) as serial_right,
    ):
        data = os.urandom(chunk_size)
        serial_left.write(data)

        read_data = serial_right.readexactly(chunk_size)
        assert read_data == data


@pytest.mark.parametrize(
    "iterations",
    [16, 32, 64],
)
def test_repeated_write_read_cycles_socket(iterations: int) -> None:
    """Test repeated write/read cycles."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial_left,
        SocketSerial(right, baudrate=115200) as serial_right,
    ):
        data = bytes(range(256))

        for _i in range(iterations):
            serial_left.write(data)
            result = serial_right.readexactly(len(data))
            assert result == data


def test_buffered_writes_then_read_socket() -> None:
    """Test multiple writes followed by a single read."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial_left,
        SocketSerial(right, baudrate=115200) as serial_right,
    ):
        chunk = bytes(range(256))
        iterations = 4

        # Write multiple chunks
        for _ in range(iterations):
            serial_left.write(chunk)

        # Read all data back
        total_size = len(chunk) * iterations
        result = serial_right.readexactly(total_size)

        # Verify all data was received correctly
        expected = chunk * iterations
        assert result == expected


@pytest.mark.parametrize(
    "payload_size",
    [1024, 2048],  # Kernel buffers are typically ~4KB, stay well below that
)
def test_large_payload_socket(payload_size: int) -> None:
    """Test large payload transmission."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=921600) as serial_left,
        SocketSerial(right, baudrate=921600) as serial_right,
    ):
        data = bytes([i % 256 for i in range(payload_size)])

        serial_left.write(data)
        result = serial_right.readexactly(len(data))

        assert result == data


def test_rapid_small_writes_socket() -> None:
    """Test rapid succession of small writes."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial_left,
        SocketSerial(right, baudrate=115200) as serial_right,
    ):
        # Send many small writes
        iterations = 256
        received = bytearray()

        for i in range(iterations):
            data = bytes([i % 256])
            serial_left.write(data)
            result = serial_right.readexactly(1)
            received.extend(result)

        # Verify all bytes were received in order
        expected = bytes([i % 256 for i in range(iterations)])
        assert bytes(received) == expected


@pytest.mark.parametrize(
    ("baudrate", "iterations"),
    [
        (9600, 8),
        (115200, 64),
        (921600, 512),
    ],
)
def test_sustained_throughput_socket(baudrate: int, iterations: int) -> None:
    """Test sustained data throughput at various baudrates."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=baudrate) as serial_left,
        SocketSerial(right, baudrate=baudrate) as serial_right,
    ):
        chunk = os.urandom(1024)

        for _ in range(iterations):
            serial_left.write(chunk)
            result = serial_right.readexactly(len(chunk))
            assert result == chunk


@pytest.mark.parametrize(
    "baudrate",
    [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600],
)
def test_valid_baudrates_socket(baudrate: int) -> None:
    """Test that valid baudrates are accepted."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=baudrate) as serial,
    ):
        assert serial.baudrate == baudrate
        serial.write(b"test")


@pytest.mark.parametrize(
    "parity",
    [Parity.NONE, Parity.ODD, Parity.EVEN, Parity.MARK, Parity.SPACE],
)
def test_valid_parity_socket(parity: Parity) -> None:
    """Test that valid parity settings are accepted."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200, parity=parity) as serial,
    ):
        assert serial.parity == parity
        serial.write(b"test")


@pytest.mark.parametrize(
    ("stopbits", "expected"),
    [
        (StopBits.ONE, StopBits.ONE),
        (StopBits.ONE_POINT_FIVE, StopBits.ONE_POINT_FIVE),
        (StopBits.TWO, StopBits.TWO),
        (1, StopBits.ONE),
        (1.5, StopBits.ONE_POINT_FIVE),
        (2, StopBits.TWO),
    ],
)
def test_valid_stopbits_socket(
    stopbits: StopBits | int | float,
    expected: StopBits,
) -> None:
    """Test that valid stopbits settings are accepted."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200, stopbits=stopbits) as serial,
    ):
        assert serial.stopbits == expected
        serial.write(b"test")


@pytest.mark.parametrize("byte_size", [5, 6, 7, 8])
def test_valid_byte_size_socket(byte_size: int) -> None:
    """Test that valid byte sizes are accepted."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200, byte_size=byte_size) as serial,
    ):
        serial.write(b"test")


@pytest.mark.parametrize("xonxoff", [True, False])
def test_xonxoff_setting_socket(xonxoff: bool) -> None:
    """Test that xonxoff setting is accepted."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200, xonxoff=xonxoff) as serial,
    ):
        serial.write(b"test")


@pytest.mark.parametrize(
    "rtscts",
    [True, False],
)
def test_rtscts_setting_socket(rtscts: bool) -> None:
    """Test that rtscts setting is accepted."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200, rtscts=rtscts) as serial,
    ):
        serial.write(b"test")


def test_exclusive_socket() -> None:
    """Test that exclusive setting is respected."""
    with create_socket_pair() as (left, right):
        with SocketSerial(left, baudrate=115200, exclusive=True) as serial:
            assert serial.exclusive is True

            # Socket endpoints are not lockable tty devices, so this should still open.
            with SocketSerial(left, baudrate=115200, exclusive=True) as serial_2:
                assert serial_2.exclusive is True


def test_exclusive_disabled_socket() -> None:
    """Test that exclusive setting is respected."""
    with create_socket_pair() as (left, right):
        with SocketSerial(left, baudrate=115200, exclusive=False) as serial1:
            assert serial1.exclusive is False

            # Can open the same port again without exclusive
            with SocketSerial(left, baudrate=115200, exclusive=False) as serial2:
                assert serial2.exclusive is False
                # Both can write without error
                serial2.write(b"test")


def test_context_manager_multiple_times_socket() -> None:
    """Test that context manager can be used multiple times."""
    with create_socket_pair() as (left, right):
        serial_left = SocketSerial(left, baudrate=115200)
        serial_right = SocketSerial(right, baudrate=115200)

        # First context
        with serial_left, serial_right:
            serial_left.write(b"test1")
            result = serial_right.readexactly(5)
            assert result == b"test1"

        # Second context
        with serial_left, serial_right:
            serial_left.write(b"test2")
            result = serial_right.readexactly(5)
            assert result == b"test2"


def test_open_close_cycles_socket() -> None:
    """Test multiple open/close cycles."""
    with create_socket_pair() as (left, right):
        serial_left = SocketSerial(left, baudrate=115200)
        serial_right = SocketSerial(right, baudrate=115200)

        # Cycle 1
        serial_left.open()
        serial_left.configure_port()
        serial_right.open()
        serial_right.configure_port()
        serial_left.write(b"1")
        assert serial_right.readexactly(1) == b"1"
        serial_left.close()
        serial_right.close()

        # Cycle 2
        serial_left.open()
        serial_left.configure_port()
        serial_right.open()
        serial_right.configure_port()
        serial_left.write(b"2")
        assert serial_right.readexactly(1) == b"2"
        serial_left.close()
        serial_right.close()

        # Cycle 3
        serial_left.open()
        serial_left.configure_port()
        serial_right.open()
        serial_right.configure_port()
        serial_left.write(b"3")
        assert serial_right.readexactly(1) == b"3"
        serial_left.close()
        serial_right.close()


def test_flush_after_write_socket() -> None:
    """Test flushing after write operation."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial_left,
        SocketSerial(right, baudrate=115200) as serial_right,
    ):
        serial_left.flush()

        data = b"Test flush operation"
        serial_left.write(data)
        serial_left.flush()

        result = serial_right.readexactly(len(data))
        assert result == data


def test_multiple_flush_calls_socket() -> None:
    """Test multiple consecutive flush calls."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial_left,
        SocketSerial(right, baudrate=115200) as serial_right,
    ):
        data = b""

        for i in range(5):
            chunk = f"Test data {i}".encode("ascii")
            data += chunk

            serial_left.write(chunk)
            serial_left.flush()

        result = serial_right.readexactly(len(data))
        assert result == data


def test_get_modem_pins_socket() -> None:
    """Test reading modem control bits with loopback adapter."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial,
    ):
        modem_pins = serial.get_modem_pins()

        # Verify we get a ModemPins object
        assert isinstance(modem_pins, ModemPins)

        # All modem pins should be PinState enum values
        for field in ["le", "dtr", "rts", "st", "sr", "cts", "car", "rng", "dsr"]:
            value = getattr(modem_pins, field)
            assert value in (PinState.HIGH, PinState.LOW, PinState.UNDEFINED)


def test_set_modem_pins_socket() -> None:
    """Test setting modem control bits with socket pair."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial,
    ):
        # Note: socket pairs don't support modem control signals properly
        # These calls should not raise errors, but values may be None
        serial.set_modem_pins(dtr=True, rts=True)
        modem_pins = serial.get_modem_pins()
        # Verify we get a ModemPins object, values may be None with socket
        assert isinstance(modem_pins, ModemPins)

        serial.set_modem_pins(dtr=False)
        modem_pins = serial.get_modem_pins()
        assert isinstance(modem_pins, ModemPins)

        serial.set_modem_pins(dtr=False, rts=False)
        modem_pins = serial.get_modem_pins()
        assert isinstance(modem_pins, ModemPins)


def test_deprecated_dtr_property_socket() -> None:
    """Test DTR property (deprecated alias) with socket pair."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial,
    ):
        # Note: socket pairs don't support modem control signals properly
        # These calls should not raise errors, but values may be None with socket
        serial.dtr = True
        # DTR may be None with socket, just verify no error occurs
        serial.dtr = False


def test_deprecated_rts_property_socket() -> None:
    """Test RTS property (deprecated alias) with socket pair."""
    with (
        create_socket_pair() as (left, right),
        SocketSerial(left, baudrate=115200) as serial,
    ):
        # Note: socket pairs don't support modem control signals properly
        # These calls should not raise errors, but values may be None with socket
        serial.rts = True
        # RTS may be None with socket, just verify no error occurs
        serial.rts = False
