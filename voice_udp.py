"""UDP voice endpoint and bounded freshness-first playout buffer."""

from __future__ import annotations

import queue
import socket
import threading
from dataclasses import dataclass
from time import time_ns
from typing import Callable

from config import AudioConfig
from logger import EventLogger
from metrics import SessionMetrics
from network_simulator import NetworkSimulator
from protocol import ProtocolError, VoicePacket


@dataclass(slots=True)
class CapturedFrame:
    payload: bytes
    capture_timestamp_ns: int


class FreshFrameBuffer:
    """A bounded queue that discards the oldest received frame under pressure."""

    def __init__(self, maxsize: int, metrics: SessionMetrics, logger: EventLogger) -> None:
        self._queue: queue.Queue[VoicePacket] = queue.Queue(maxsize=maxsize)
        self._metrics = metrics
        self._logger = logger
        self._last_played: int | None = None

    def put(self, packet: VoicePacket) -> bool:
        """Store a fresh packet; reject older packets and evict stale queue head."""
        if self._last_played is not None and packet.sequence <= self._last_played:
            self._metrics.record_late_packet(packet)
            self._logger.log("VOICE", f"Packet #{packet.sequence} discarded as late")
            return False
        try:
            self._queue.put_nowait(packet)
        except queue.Full:
            try:
                dropped = self._queue.get_nowait()
                self._metrics.record_late_packet(dropped)
                self._logger.log("VOICE", f"Queue full; stale packet #{dropped.sequence} discarded")
            except queue.Empty:
                return False
            self._queue.put_nowait(packet)
        self._metrics.record_queue_depth(self._queue.qsize())
        return True

    def get(self, timeout: float = 0.15) -> VoicePacket | None:
        try:
            packet = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if self._last_played is not None and packet.sequence <= self._last_played:
            self._metrics.record_late_packet(packet)
            return None
        self._last_played = packet.sequence
        return packet

    def qsize(self) -> int:
        return self._queue.qsize()

    def clear(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return


class VoiceUdpEndpoint:
    """Owns the real UDP socket and its independent receive loop."""

    def __init__(self, local_ip: str, local_port: int, peer: tuple[str, int], *,
                 buffer: FreshFrameBuffer, metrics: SessionMetrics, logger: EventLogger,
                 simulator: NetworkSimulator) -> None:
        self.local_ip = local_ip
        self.local_port = local_port
        self.peer = peer
        self.buffer = buffer
        self.metrics = metrics
        self.logger = logger
        self.simulator = simulator
        self._socket: socket.socket | None = None
        self._running = threading.Event()
        self._receiver: threading.Thread | None = None
        self._sequence = 0
        self._sequence_lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._running.is_set() and self._socket is not None

    def start(self) -> None:
        if self.active:
            return
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.settimeout(0.25)
        try:
            udp_socket.bind((self.local_ip, self.local_port))
        except OSError:
            udp_socket.close()
            raise
        self._socket = udp_socket
        self._running.set()
        self.simulator.start()
        self._receiver = threading.Thread(target=self._receive_loop, name="udp-receiver", daemon=True)
        self._receiver.start()
        self.logger.log("UDP", f"UDP receiver active on {self.local_ip}:{self.local_port}")

    def send_audio(self, payload: bytes, capture_timestamp_ns: int | None = None) -> VoicePacket | None:
        """Packetise and schedule an actual UDP datagram through the simulator."""
        if not self.active or self._socket is None:
            return None
        with self._sequence_lock:
            self._sequence = (self._sequence + 1) & 0xFFFFFFFF
            packet = VoicePacket(
                sequence=self._sequence,
                timestamp_ns=capture_timestamp_ns if capture_timestamp_ns is not None else time_ns(),
                payload=payload,
            )
        try:
            encoded = packet.encode()
        except ProtocolError as exc:
            self.logger.log("ERROR", f"Voice packet rejected: {exc}")
            return None
        self.metrics.record_voice_sent(packet)
        accepted = self.simulator.submit(encoded, self.peer, self._send_datagram)
        if not accepted:
            self.metrics.record_simulated_drop(packet)
            self.logger.log("NET", f"Voice packet #{packet.sequence} dropped by UDP simulator")
        return packet

    def _send_datagram(self, encoded: bytes, target: tuple[str, int]) -> None:
        udp_socket = self._socket
        if not self._running.is_set() or udp_socket is None:
            return
        try:
            udp_socket.sendto(encoded, target)
        except OSError as exc:
            if self._running.is_set():
                self.logger.log("ERROR", f"UDP send failed: {exc}")

    def _receive_loop(self) -> None:
        while self._running.is_set():
            udp_socket = self._socket
            if udp_socket is None:
                return
            try:
                data, source = udp_socket.recvfrom(65_535)
            except TimeoutError:
                continue
            except OSError as exc:
                if self._running.is_set():
                    self.logger.log("ERROR", f"UDP receive failed: {exc}")
                return
            try:
                packet = VoicePacket.decode(data)
            except ProtocolError as exc:
                self.logger.log("ERROR", f"Malformed UDP packet from {source[0]}:{source[1]}: {exc}")
                continue
            status = self.metrics.record_voice_received(packet)
            if status == "RECEIVED":
                self.logger.log("UDP", f"Voice packet #{packet.sequence} received")
            else:
                self.logger.log("UDP", f"Voice packet #{packet.sequence}: {status.lower()}")
            self.buffer.put(packet)

    def stop(self) -> None:
        self._running.clear()
        udp_socket, self._socket = self._socket, None
        if udp_socket is not None:
            try:
                udp_socket.close()
            except OSError:
                pass
        self.simulator.stop()
        if self._receiver and self._receiver is not threading.current_thread():
            self._receiver.join(timeout=1.5)
        self.buffer.clear()
        self.logger.log("UDP", "UDP voice endpoint stopped")