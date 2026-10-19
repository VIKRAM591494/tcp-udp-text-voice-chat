from __future__ import annotations

from metrics import SessionMetrics
from protocol import VoicePacket


def test_metrics_detects_gap_duplicate_and_out_of_order_packets() -> None:
    metrics = SessionMetrics()
    metrics.record_voice_received(VoicePacket(sequence=10, timestamp_ns=1, payload=b"a"))
    metrics.record_voice_received(VoicePacket(sequence=12, timestamp_ns=2, payload=b"a"))
    metrics.record_voice_received(VoicePacket(sequence=11, timestamp_ns=3, payload=b"a"))
    metrics.record_voice_received(VoicePacket(sequence=12, timestamp_ns=4, payload=b"a"))
    snapshot = metrics.snapshot(queue_depth=0, worker_count=0, tcp_state="DISCONNECTED", udp_state="ACTIVE",
                                simulator_delay_ms=0, simulator_jitter_ms=0)
    assert snapshot["voice_packets_lost"] == 1
    assert snapshot["voice_out_of_order"] == 1
    assert snapshot["voice_duplicates"] == 1


def test_metrics_returns_na_before_any_timed_observation() -> None:
    metrics = SessionMetrics()
    snapshot = metrics.snapshot(queue_depth=0, worker_count=0, tcp_state="DISCONNECTED", udp_state="INACTIVE",
                                simulator_delay_ms=0, simulator_jitter_ms=0)
    assert snapshot["average_udp_receive_interval_ms"] == "N/A"
    assert snapshot["estimated_capture_to_playout_ms"] == "N/A"