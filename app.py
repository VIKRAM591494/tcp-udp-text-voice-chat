"""Entry point for the Voice Chat UDP + TCP laboratory application."""

from __future__ import annotations

import argparse
import json
import queue
import tempfile
import time
from pathlib import Path

from config import AppConfig, AudioConfig, NetworkConfig
from network_simulator import SimulatorConfig
from workers import SessionController


PROJECT_DIR = Path(__file__).resolve().parent


def _wait_until(predicate: object, timeout: float, description: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.03)
    raise RuntimeError(f"Timed out waiting for {description}")


def run_self_test() -> dict[str, object]:
    """Run a true localhost TCP/UDP integration check without GUI or audio hardware.

    It starts a TCP host and client, exchanges framed text in both directions,
    then sends silent PCM through real localhost UDP sockets while the optional
    simulator drops some outbound packets. This does not manufacture metrics.
    """
    tcp_port, host_udp, client_udp = 58100, 58101, 58102
    with tempfile.TemporaryDirectory(prefix="voice-chat-self-test-") as temp_dir:
        root = Path(temp_dir)
        host = SessionController(AppConfig(
            audio=AudioConfig(),
            network=NetworkConfig("127.0.0.1", "127.0.0.1", tcp_port, host_udp, client_udp, "Host"),
        ), root / "host_logs")
        client = SessionController(AppConfig(
            audio=AudioConfig(),
            network=NetworkConfig("127.0.0.1", "127.0.0.1", tcp_port, client_udp, host_udp, "Client"),
        ), root / "client_logs")
        try:
            host.start_host()
            client.start_client()
            _wait_until(lambda: host.tcp_state == "CONNECTED" and client.tcp_state == "CONNECTED", 4, "TCP connection")
            if not client.send_chat("Loopback TCP message"):
                raise RuntimeError("Client could not send TCP message")
            _wait_until(lambda: not host.chat_queue.empty(), 3, "host TCP message")
            host_message = host.chat_queue.get_nowait()
            if host_message.text != "Loopback TCP message":
                raise RuntimeError("Unexpected TCP message payload")
            # The UI intentionally echoes locally sent text. Clear that local
            # echo before asserting the separate inbound acknowledgement.
            while True:
                try:
                    client.chat_queue.get_nowait()
                except queue.Empty:
                    break
            if not host.send_chat("TCP acknowledgement"):
                raise RuntimeError("Host could not send TCP acknowledgement")
            _wait_until(lambda: not client.chat_queue.empty(), 3, "client TCP acknowledgement")
            client_message = client.chat_queue.get_nowait()
            if client_message.text != "TCP acknowledgement":
                raise RuntimeError("Unexpected TCP acknowledgement")
            client.update_simulator(SimulatorConfig(loss_percent=20.0, delay_ms=5.0, jitter_ms=3.0))
            host.start_voice(simulation_audio=True)
            client.start_voice(simulation_audio=True)
            _wait_until(lambda: host.metrics_snapshot()["voice_packets_received"] >= 8, 5, "UDP voice packets")
            host_snapshot = host.metrics_snapshot()
            client_snapshot = client.metrics_snapshot()
            if not host_snapshot["voice_packets_received"] or not client_snapshot["voice_packets_sent"]:
                raise RuntimeError("UDP voice statistics were not observed")
            return {
                "tcp": {"host_received": host_snapshot["text_messages_received"],
                        "client_received": client_snapshot["text_messages_received"]},
                "udp": {"client_sent": client_snapshot["voice_packets_sent"],
                        "host_received": host_snapshot["voice_packets_received"],
                        "client_simulated_drops": client_snapshot["voice_simulated_drops"]},
                "verified": "real localhost TCP and UDP sockets; simulation audio only",
            }
        finally:
            client.shutdown()
            host.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="UDP voice and TCP text laboratory prototype")
    parser.add_argument("--self-test", action="store_true", help="run real localhost TCP/UDP validation without a GUI")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(run_self_test(), indent=2))
        return 0
    try:
        from ui import launch
        launch(PROJECT_DIR)
    except Exception as exc:
        # The GUI needs a desktop display. Self-test remains usable on CI/headless hosts.
        print(f"Could not start desktop UI: {exc}")
        print("Run `python app.py --self-test` to validate TCP/UDP without a GUI.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())