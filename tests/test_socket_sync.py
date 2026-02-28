"""Test sync APIs with socket:// endpoints."""

from collections.abc import Iterator
import contextlib
import logging
import queue
import socket
import threading
import time

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
