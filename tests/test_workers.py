from __future__ import annotations

from pathlib import Path

from config import AppConfig, AudioConfig, NetworkConfig
from logger import EventLogger
from metrics import SessionMetrics
from protocol import VoicePacket
from voice_udp import FreshFrameBuffer
from workers import SessionController


def test_fresh_playout_buffer_evicts_oldest_frame_when_full(tmp_path: Path) -> None:
    metrics = SessionMetrics()
    buffer = FreshFrameBuffer(1, metrics, EventLogger(tmp_path))
    first = VoicePacket(sequence=1, timestamp_ns=1, payload=b"a")
    newest = VoicePacket(sequence=2, timestamp_ns=2, payload=b"b")
    assert buffer.put(first)
    assert buffer.put(newest)
    assert buffer.get() == newest
    snapshot = metrics.snapshot(queue_depth=0, worker_count=0, tcp_state="DISCONNECTED", udp_state="ACTIVE",
                                simulator_delay_ms=0, simulator_jitter_ms=0)
    assert snapshot["voice_late"] == 1


def test_shutdown_releases_host_socket_and_returns_to_idle(tmp_path: Path) -> None:
    controller = SessionController(AppConfig(
        audio=AudioConfig(),
        network=NetworkConfig("127.0.0.1", "127.0.0.1", 58200, 58201, 58202, "Host"),
    ), tmp_path / "logs")
    controller.start_host()
    assert controller.udp_state == "ACTIVE"
    controller.shutdown()
    assert controller.role == "idle"
    assert controller.udp_state == "INACTIVE"