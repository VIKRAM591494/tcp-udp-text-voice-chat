"""Length-prefixed TCP text transport with independent accept/connect and receive workers."""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from typing import Literal

from logger import EventLogger
from metrics import SessionMetrics
from protocol import ChatMessage, ProtocolError, decode_text_frames


ConnectionCallback = Callable[[str], None]
MessageCallback = Callable[[ChatMessage], None]


class TcpChatTransport:
    """One server-or-client TCP connection carrying framed chat messages.

    The accept/connect path and the receive loop never run in Tk's UI thread.
    A lock protects writes because a TCP frame must remain contiguous even if two
    callers request sends at the same time.
    """

    def __init__(self, metrics: SessionMetrics, logger: EventLogger,
                 on_message: MessageCallback, on_state: ConnectionCallback) -> None:
        self.metrics = metrics
        self.logger = logger
        self.on_message = on_message
        self.on_state = on_state
        self._running = threading.Event()
        self._server_socket: socket.socket | None = None
        self._connection: socket.socket | None = None
        self._state_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._accept_thread: threading.Thread | None = None
        self._receive_thread: threading.Thread | None = None
        self._connect_thread: threading.Thread | None = None
        self._mode: Literal["host", "client"] | None = None
        self._state = "DISCONNECTED"

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    @property
    def worker_count(self) -> int:
        return sum(bool(thread and thread.is_alive()) for thread in (
            self._accept_thread, self._receive_thread, self._connect_thread,
        ))

    def start_host(self, local_ip: str, port: int) -> None:
        if self._running.is_set():
            raise RuntimeError("TCP transport already running")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.settimeout(0.25)
        try:
            listener.bind((local_ip, port))
            listener.listen(1)
        except OSError:
            listener.close()
            raise
        self._server_socket = listener
        self._mode = "host"
        self._running.set()
        self._set_state("LISTENING")
        self._accept_thread = threading.Thread(target=self._accept_loop, name="tcp-accept", daemon=True)
        self._accept_thread.start()
        self.logger.log("TCP", f"TCP host listening on {local_ip}:{port}")

    def connect_client(self, peer_ip: str, port: int) -> None:
        if self._running.is_set():
            raise RuntimeError("TCP transport already running")
        self._mode = "client"
        self._running.set()
        self._set_state("CONNECTING")
        self._connect_thread = threading.Thread(
            target=self._connect_worker, args=(peer_ip, port), name="tcp-connect", daemon=True,
        )
        self._connect_thread.start()

    def _connect_worker(self, peer_ip: str, port: int) -> None:
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(4.0)
        try:
            connection.connect((peer_ip, port))
            connection.settimeout(0.25)
            if not self._running.is_set():
                connection.close()
                return
            self._activate_connection(connection, f"Connected to {peer_ip}:{port}")
        except OSError as exc:
            connection.close()
            self.logger.log("ERROR", f"TCP connection failed: {exc}")
            self._running.clear()
            self._set_state("DISCONNECTED")

    def _accept_loop(self) -> None:
        listener = self._server_socket
        if listener is None:
            return
        while self._running.is_set():
            try:
                connection, address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            connection.settimeout(0.25)
            with self._state_lock:
                occupied = self._connection is not None
            if occupied:
                connection.close()
                self.logger.log("TCP", "Rejected an extra TCP peer; this lab prototype supports one peer")
                continue
            self._activate_connection(connection, f"TCP peer connected from {address[0]}:{address[1]}")
            return

    def _activate_connection(self, connection: socket.socket, event: str) -> None:
        with self._state_lock:
            self._connection = connection
        self._set_state("CONNECTED")
        self.logger.log("TCP", event)
        self._receive_thread = threading.Thread(target=self._receive_loop, name="tcp-receiver", daemon=True)
        self._receive_thread.start()

    def send_message(self, message: ChatMessage) -> bool:
        with self._state_lock:
            connection = self._connection
        if connection is None or self.state != "CONNECTED":
            self.logger.log("ERROR", "Chat send failed: TCP peer is not connected")
            return False
        encoded = message.to_bytes()
        try:
            with self._send_lock:
                connection.sendall(encoded)
            self.metrics.record_text_sent(message, len(encoded))
            self.logger.log("TCP", f"Message #{message.message_id} sent")
            return True
        except OSError as exc:
            self.logger.log("ERROR", f"TCP send failed: {exc}")
            self._close_connection("DISCONNECTED")
            return False

    def _receive_loop(self) -> None:
        buffer = bytearray()
        while self._running.is_set():
            with self._state_lock:
                connection = self._connection
            if connection is None:
                return
            try:
                data = connection.recv(4096)
            except TimeoutError:
                continue
            except OSError as exc:
                if self._running.is_set():
                    self.logger.log("ERROR", f"TCP receive failed: {exc}")
                self._close_connection("DISCONNECTED")
                return
            if not data:
                self.logger.log("TCP", "TCP peer closed the chat connection")
                self._close_connection("DISCONNECTED")
                return
            buffer.extend(data)
            try:
                payloads = decode_text_frames(buffer)
            except ProtocolError as exc:
                self.logger.log("ERROR", f"Malformed TCP frame: {exc}")
                self._close_connection("DISCONNECTED")
                return
            for payload in payloads:
                try:
                    message = ChatMessage.from_bytes(payload)
                except ProtocolError as exc:
                    self.logger.log("ERROR", f"Malformed chat message: {exc}")
                    continue
                self.metrics.record_text_received(message, len(payload) + 4)
                self.logger.log("TCP", f"Message #{message.message_id} received from {message.sender}")
                self.on_message(message)

    def _close_connection(self, state: str) -> None:
        with self._state_lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        if self._running.is_set() and self._mode == "host" and state == "DISCONNECTED":
            # Keep the listener open so a restarted peer can reconnect.
            self._set_state("LISTENING")
            self._accept_thread = threading.Thread(target=self._accept_loop, name="tcp-accept", daemon=True)
            self._accept_thread.start()
        else:
            self._set_state(state)

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state
        self.on_state(state)

    def stop(self) -> None:
        self._running.clear()
        self._close_connection("DISCONNECTED")
        listener, self._server_socket = self._server_socket, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        for thread in (self._accept_thread, self._receive_thread, self._connect_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=1.5)
        self.logger.log("TCP", "TCP text transport stopped")