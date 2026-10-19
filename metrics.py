"""Measured session metrics and packet/message inspector records."""

from __future__ import annotations

import csv
import threading
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from time import time_ns
from typing import Deque

from protocol import ChatMessage, VoicePacket


@dataclass(slots=True)
class VoiceInspectorRow:
    sequence: int
    timestamp_ns: int
    payload_size: int
    arrival_ns: int | None
    delay_ms: float | None
    status: str


@dataclass(slots=True)
class TextInspectorRow:
    message_id: int
    timestamp_ns: int
    size: int
    direction: str
    status: str
    latency_ms: float | None


class SessionMetrics:
    """Collects genuine observations without generating illustrative values."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started_ns = time_ns()
        self._voice_sent = 0
        self._voice_received = 0
        self._voice_lost = 0
        self._voice_duplicate = 0
        self._voice_out_of_order = 0
        self._voice_late = 0
        self._simulated_drops = 0
        self._payload_sizes: Deque[int] = deque(maxlen=500)
        self._arrival_intervals_ms: Deque[float] = deque(maxlen=500)
        self._capture_to_send_ms: Deque[float] = deque(maxlen=500)
        self._capture_to_playout_ms: Deque[float] = deque(maxlen=500)
        self._queue_depths: Deque[int] = deque(maxlen=500)
        self._max_queue_depth = 0
        self._last_arrival_ns: int | None = None
        self._highest_received: int | None = None
        self._last_played_sequence: int | None = None
        self._text_sent = 0
        self._text_received = 0
        self._text_bytes = 0
        self._text_latency_ms: Deque[float] = deque(maxlen=500)
        self.voice_rows: Deque[VoiceInspectorRow] = deque(maxlen=200)
        self.text_rows: Deque[TextInspectorRow] = deque(maxlen=200)

    def record_voice_sent(self, packet: VoicePacket) -> None:
        with self._lock:
            self._voice_sent += 1
            self._payload_sizes.append(len(packet.payload))
            self._capture_to_send_ms.append(max(0.0, (time_ns() - packet.timestamp_ns) / 1_000_000))
            self.voice_rows.append(VoiceInspectorRow(
                packet.sequence, packet.timestamp_ns, len(packet.payload), None, None, "SENT"
            ))

    def record_simulated_drop(self, packet: VoicePacket) -> None:
        with self._lock:
            self._simulated_drops += 1
            self.voice_rows.append(VoiceInspectorRow(
                packet.sequence, packet.timestamp_ns, len(packet.payload), None, None, "SIMULATED LOSS"
            ))

    def record_voice_received(self, packet: VoicePacket) -> str:
        """Classify arrival order and return its inspector status."""
        now = time_ns()
        with self._lock:
            status = "RECEIVED"
            if self._highest_received is None:
                self._highest_received = packet.sequence
            elif packet.sequence == self._highest_received:
                self._voice_duplicate += 1
                status = "DUPLICATE"
            elif packet.sequence < self._highest_received:
                self._voice_out_of_order += 1
                status = "OUT OF ORDER"
            else:
                if packet.sequence > self._highest_received + 1:
                    self._voice_lost += packet.sequence - self._highest_received - 1
                self._highest_received = packet.sequence
            self._voice_received += 1
            delay_ms = max(0.0, (now - packet.timestamp_ns) / 1_000_000)
            if self._last_arrival_ns is not None:
                self._arrival_intervals_ms.append((now - self._last_arrival_ns) / 1_000_000)
            self._last_arrival_ns = now
            self.voice_rows.append(VoiceInspectorRow(
                packet.sequence, packet.timestamp_ns, len(packet.payload), now, delay_ms, status
            ))
            return status

    def record_late_packet(self, packet: VoicePacket) -> None:
        with self._lock:
            self._voice_late += 1
            self.voice_rows.append(VoiceInspectorRow(
                packet.sequence, packet.timestamp_ns, len(packet.payload), time_ns(), None, "LATE / DROPPED"
            ))

    def record_playout(self, packet: VoicePacket, queue_depth: int) -> None:
        now = time_ns()
        with self._lock:
            self._last_played_sequence = packet.sequence
            self._capture_to_playout_ms.append(max(0.0, (now - packet.timestamp_ns) / 1_000_000))
            self._queue_depths.append(queue_depth)
            self._max_queue_depth = max(self._max_queue_depth, queue_depth)

    def record_queue_depth(self, depth: int) -> None:
        with self._lock:
            self._queue_depths.append(depth)
            self._max_queue_depth = max(self._max_queue_depth, depth)

    def record_text_sent(self, message: ChatMessage, size: int) -> None:
        with self._lock:
            self._text_sent += 1
            self._text_bytes += size
            self.text_rows.append(TextInspectorRow(
                message.message_id, message.timestamp_ns, size, "OUT", "SENT", None
            ))

    def record_text_received(self, message: ChatMessage, size: int) -> None:
        now = time_ns()
        with self._lock:
            self._text_received += 1
            self._text_bytes += size
            latency = max(0.0, (now - message.timestamp_ns) / 1_000_000)
            self._text_latency_ms.append(latency)
            self.text_rows.append(TextInspectorRow(
                message.message_id, message.timestamp_ns, size, "IN", "RECEIVED", latency
            ))

    def snapshot(self, *, queue_depth: int, worker_count: int, tcp_state: str,
                 udp_state: str, simulator_delay_ms: float, simulator_jitter_ms: float) -> dict[str, object]:
        with self._lock:
            received_or_lost = self._voice_received + self._voice_lost
            loss_percent = (self._voice_lost / received_or_lost * 100) if received_or_lost else 0.0
            return {
                "voice_packets_sent": self._voice_sent,
                "voice_packets_received": self._voice_received,
                "voice_packets_lost": self._voice_lost,
                "voice_loss_percent": round(loss_percent, 2),
                "voice_simulated_drops": self._simulated_drops,
                "voice_out_of_order": self._voice_out_of_order,
                "voice_late": self._voice_late,
                "voice_duplicates": self._voice_duplicate,
                "average_packet_size_bytes": round(fmean(self._payload_sizes), 1) if self._payload_sizes else "N/A",
                "average_udp_receive_interval_ms": round(fmean(self._arrival_intervals_ms), 2) if self._arrival_intervals_ms else "N/A",
                "average_capture_to_send_ms": round(fmean(self._capture_to_send_ms), 2) if self._capture_to_send_ms else "N/A",
                "estimated_capture_to_playout_ms": round(fmean(self._capture_to_playout_ms), 2) if self._capture_to_playout_ms else "N/A",
                "queue_depth": queue_depth,
                "maximum_queue_depth": self._max_queue_depth,
                "text_messages_sent": self._text_sent,
                "text_messages_received": self._text_received,
                "text_bytes_transmitted": self._text_bytes,
                "average_text_send_to_render_ms": round(fmean(self._text_latency_ms), 2) if self._text_latency_ms else "N/A",
                "tcp_status": tcp_state,
                "udp_status": udp_state,
                "active_workers": worker_count,
                "uptime_seconds": round((time_ns() - self._started_ns) / 1_000_000_000, 1),
                "simulated_delay_ms": simulator_delay_ms,
                "simulated_jitter_ms": simulator_jitter_ms,
            }

    def metric_history(self) -> dict[str, list[float]]:
        """Measured series for the in-app graph. No sample values are added."""
        with self._lock:
            return {
                "arrival_interval_ms": list(self._arrival_intervals_ms),
                "queue_depth": [float(value) for value in self._queue_depths],
                "estimated_playout_ms": list(self._capture_to_playout_ms),
                "text_latency_ms": list(self._text_latency_ms),
            }

    def export_csv(self, path: Path) -> None:
        with self._lock, path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=[
                "kind", "sequence_or_id", "timestamp_ns", "payload_or_size", "arrival_ns",
                "delay_or_latency_ms", "direction", "status",
            ])
            writer.writeheader()
            for row in self.voice_rows:
                writer.writerow({
                    "kind": "voice", "sequence_or_id": row.sequence, "timestamp_ns": row.timestamp_ns,
                    "payload_or_size": row.payload_size, "arrival_ns": row.arrival_ns,
                    "delay_or_latency_ms": row.delay_ms, "direction": "", "status": row.status,
                })
            for row in self.text_rows:
                writer.writerow({
                    "kind": "text", "sequence_or_id": row.message_id, "timestamp_ns": row.timestamp_ns,
                    "payload_or_size": row.size, "arrival_ns": "",
                    "delay_or_latency_ms": row.latency_ms, "direction": row.direction, "status": row.status,
                })