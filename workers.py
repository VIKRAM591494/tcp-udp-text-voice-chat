"""Session lifecycle and independent capture, playback, UDP, and TCP workers."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from time import time_ns
from typing import Literal

from audio_io import AudioIO, AudioUnavailableError, create_audio_io
from config import AppConfig, NetworkConfig
from logger import EventLogger
from metrics import SessionMetrics
from network_simulator import NetworkSimulator, SimulatorConfig
from protocol import ChatMessage
from text_tcp import TcpChatTransport
from voice_udp import CapturedFrame, FreshFrameBuffer, VoiceUdpEndpoint


Role = Literal["host", "client", "idle"]


class SessionController:
    """Coordinates the two independent transport lanes for one app instance.

    The controller is deliberately GUI-agnostic. A Tk interface polls its public
    queues and snapshots, while tests can drive the same real sockets headlessly.
    """

    def __init__(self, config: AppConfig, log_dir: Path) -> None:
        self.config = config
        self.logger = EventLogger(log_dir)
        self.metrics = SessionMetrics()
        self.chat_queue: queue.Queue[ChatMessage] = queue.Queue(maxsize=500)
        self.state_queue: queue.Queue[str] = queue.Queue(maxsize=100)
        self._lock = threading.RLock()
        self._role: Role = "idle"
        self._voice_active = threading.Event()
        self._stop_voice = threading.Event()
        self._voice_endpoint: VoiceUdpEndpoint | None = None
        self._tcp = TcpChatTransport(self.metrics, self.logger, self._receive_chat, self._tcp_state_changed)
        self._simulator = NetworkSimulator()
        self._playout_buffer = FreshFrameBuffer(
            config.audio.playout_queue_size, self.metrics, self.logger,
        )
        self._outbound_audio: queue.Queue[CapturedFrame] = queue.Queue(maxsize=24)
        self._audio: AudioIO | None = None
        self._capture_thread: threading.Thread | None = None
        self._udp_sender_thread: threading.Thread | None = None
        self._playback_thread: threading.Thread | None = None
        self._message_id = 0
        self._simulation_audio = False
        self._muted = threading.Event()

    @property
    def role(self) -> Role:
        return self._role

    @property
    def tcp_state(self) -> str:
        return self._tcp.state

    @property
    def udp_state(self) -> str:
        return "ACTIVE" if self._voice_endpoint and self._voice_endpoint.active else "INACTIVE"

    @property
    def voice_state(self) -> str:
        return "RUNNING" if self._voice_active.is_set() else "STOPPED"

    @property
    def simulation_audio(self) -> bool:
        return self._simulation_audio

    @property
    def log_path(self) -> Path:
        return self.logger.path

    def apply_network_config(self, network: NetworkConfig) -> None:
        """Update settings only while disconnected, avoiding live socket ambiguity."""
        network.validate()
        if self._role != "idle":
            raise RuntimeError("Disconnect before changing local or peer network settings")
        self.config.network = network

    def start_host(self) -> None:
        self._start_transport("host")

    def start_client(self) -> None:
        self._start_transport("client")

    def _start_transport(self, role: Role) -> None:
        with self._lock:
            if self._role != "idle":
                raise RuntimeError("Session is already active. Disconnect before starting another role.")
            self.config.network.validate()
            network = self.config.network
            self._voice_endpoint = VoiceUdpEndpoint(
                network.local_ip,
                network.local_udp_port,
                (network.peer_ip, network.peer_udp_port),
                buffer=self._playout_buffer,
                metrics=self.metrics,
                logger=self.logger,
                simulator=self._simulator,
            )
            try:
                self._voice_endpoint.start()
                if role == "host":
                    self._tcp.start_host(network.local_ip, network.tcp_port)
                else:
                    self._tcp.connect_client(network.peer_ip, network.tcp_port)
            except Exception:
                self._voice_endpoint.stop()
                self._voice_endpoint = None
                raise
            self._role = role
            self.logger.log("APP", f"Session started as {role.upper()}")
            self._publish_state(f"{role.upper()} started")

    def start_voice(self, simulation_audio: bool = False) -> None:
        """Start real microphone/speaker I/O or explicitly requested silent PCM mode."""
        with self._lock:
            if self._role == "idle" or self._voice_endpoint is None:
                raise RuntimeError("Start Host or Connect before starting voice")
            if self._voice_active.is_set():
                return
            try:
                self._audio = create_audio_io(self.config.audio, simulation_audio)
            except AudioUnavailableError:
                raise
            self._simulation_audio = simulation_audio
            self._stop_voice.clear()
            self._voice_active.set()
            self._capture_thread = threading.Thread(target=self._capture_loop, name="audio-capture", daemon=True)
            self._udp_sender_thread = threading.Thread(target=self._udp_sender_loop, name="udp-sender", daemon=True)
            self._playback_thread = threading.Thread(target=self._playback_loop, name="audio-playback", daemon=True)
            self._capture_thread.start()
            self._udp_sender_thread.start()
            self._playback_thread.start()
            mode = "SIMULATION / AUDIO TEST" if simulation_audio else "REAL MICROPHONE / SPEAKER"
            self.logger.log("AUDIO", f"Voice started in {mode} mode")
            self._publish_state(f"Voice running: {mode}")

    def _capture_loop(self) -> None:
        while self._voice_active.is_set() and not self._stop_voice.is_set():
            audio = self._audio
            endpoint = self._voice_endpoint
            if audio is None or endpoint is None:
                return
            try:
                frame = audio.read_frame()
                if len(frame) != self.config.audio.packet_bytes:
                    self.logger.log("ERROR", f"Audio frame has {len(frame)} bytes, expected {self.config.audio.packet_bytes}")
                    continue
                if not self._muted.is_set():
                    self._enqueue_captured_frame(CapturedFrame(frame, time_ns()))
            except Exception as exc:
                if not self._stop_voice.is_set():
                    self.logger.log("ERROR", f"Audio capture stopped: {exc}")
                    self._publish_state("Voice stopped after audio capture error")
                self._voice_active.clear()
                return

    def _enqueue_captured_frame(self, captured: CapturedFrame) -> None:
        """Keep the newest audio under load rather than growing send latency."""
        try:
            self._outbound_audio.put_nowait(captured)
        except queue.Full:
            try:
                self._outbound_audio.get_nowait()
                self._outbound_audio.put_nowait(captured)
                self.logger.log("VOICE", "Outbound queue full; stale captured frame discarded")
            except queue.Empty:
                return

    def _udp_sender_loop(self) -> None:
        """Owns the capture-queue → packetisation → UDP send handoff."""
        while self._voice_active.is_set() and not self._stop_voice.is_set():
            endpoint = self._voice_endpoint
            if endpoint is None:
                return
            try:
                captured = self._outbound_audio.get(timeout=0.15)
            except queue.Empty:
                continue
            endpoint.send_audio(captured.payload, captured.capture_timestamp_ns)

    def _playback_loop(self) -> None:
        while self._voice_active.is_set() and not self._stop_voice.is_set():
            packet = self._playout_buffer.get(timeout=0.15)
            if packet is None:
                continue
            audio = self._audio
            if audio is None:
                return
            try:
                audio.play_frame(packet.payload)
                self.metrics.record_playout(packet, self._playout_buffer.qsize())
            except Exception as exc:
                if not self._stop_voice.is_set():
                    self.logger.log("ERROR", f"Audio playback stopped: {exc}")
                    self._publish_state("Voice stopped after speaker error")
                self._voice_active.clear()
                return

    def stop_voice(self) -> None:
        with self._lock:
            if not self._voice_active.is_set() and self._audio is None:
                return
            self._stop_voice.set()
            self._voice_active.clear()
            audio, self._audio = self._audio, None
            if audio is not None:
                audio.close()
        for worker in (self._capture_thread, self._udp_sender_thread, self._playback_thread):
            if worker and worker is not threading.current_thread():
                worker.join(timeout=1.5)
        self._playout_buffer.clear()
        while True:
            try:
                self._outbound_audio.get_nowait()
            except queue.Empty:
                break
        self.logger.log("AUDIO", "Voice stopped and audio resources released")
        self._publish_state("Voice stopped")

    def set_muted(self, muted: bool) -> None:
        """Mute affects only outbound UDP audio; TCP and received audio continue."""
        if muted:
            self._muted.set()
            self.logger.log("AUDIO", "Outbound UDP voice muted")
        else:
            self._muted.clear()
            self.logger.log("AUDIO", "Outbound UDP voice unmuted")

    def send_chat(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        with self._lock:
            self._message_id += 1
            message = ChatMessage.create(self._message_id, self.config.network.display_name, text)
        sent = self._tcp.send_message(message)
        if sent:
            # Echo outgoing messages to the local UI, not to metrics as a receive.
            self._put_chat(message)
        return sent

    def _receive_chat(self, message: ChatMessage) -> None:
        self._put_chat(message)

    def _put_chat(self, message: ChatMessage) -> None:
        try:
            self.chat_queue.put_nowait(message)
        except queue.Full:
            try:
                self.chat_queue.get_nowait()
                self.chat_queue.put_nowait(message)
                self.logger.log("ERROR", "Chat UI queue full; oldest rendered message was discarded")
            except queue.Empty:
                pass

    def _tcp_state_changed(self, state: str) -> None:
        self._publish_state(f"TCP {state}")

    def _publish_state(self, text: str) -> None:
        try:
            self.state_queue.put_nowait(text)
        except queue.Full:
            try:
                self.state_queue.get_nowait()
                self.state_queue.put_nowait(text)
            except queue.Empty:
                pass

    def update_simulator(self, config: SimulatorConfig) -> None:
        self._simulator.update(config)
        self.logger.log(
            "NET",
            f"UDP simulator: loss {config.loss_percent:.1f}%, delay {config.delay_ms:.0f} ms, "
            f"jitter {config.jitter_ms:.0f} ms, reorder {'on' if config.reordering else 'off'}",
        )

    def apply_preset(self, name: str) -> SimulatorConfig:
        presets = {
            "NORMAL": SimulatorConfig(),
            "1–2% LOSS": SimulatorConfig(loss_percent=2.0),
            "5% LOSS": SimulatorConfig(loss_percent=5.0),
            "10% LOSS": SimulatorConfig(loss_percent=10.0),
            "HIGH JITTER": SimulatorConfig(delay_ms=20.0, jitter_ms=80.0),
            "HIGH DELAY": SimulatorConfig(delay_ms=250.0),
            "SEVERE NETWORK": SimulatorConfig(loss_percent=12.0, delay_ms=180.0, jitter_ms=120.0, reordering=True),
        }
        selected = presets[name]
        self.update_simulator(selected)
        return selected

    def worker_count(self) -> int:
        return self._tcp.worker_count + sum(
            bool(worker and worker.is_alive())
            for worker in (self._capture_thread, self._udp_sender_thread, self._playback_thread)
        ) + (1 if self._voice_endpoint and self._voice_endpoint.active else 0)

    def metrics_snapshot(self) -> dict[str, object]:
        config = self._simulator.config
        return self.metrics.snapshot(
            queue_depth=self._playout_buffer.qsize(),
            worker_count=self.worker_count(),
            tcp_state=self.tcp_state,
            udp_state=self.udp_state,
            simulator_delay_ms=config.delay_ms,
            simulator_jitter_ms=config.jitter_ms,
        )

    def export_metrics(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"metrics_{self.log_path.stem.rsplit('_', 1)[-1]}.csv"
        self.metrics.export_csv(path)
        self.logger.log("APP", f"Metrics exported to {path.name}")
        return path

    def disconnect(self) -> None:
        with self._lock:
            if self._role == "idle":
                return
        self.stop_voice()
        self._tcp.stop()
        endpoint, self._voice_endpoint = self._voice_endpoint, None
        if endpoint is not None:
            endpoint.stop()
        self._role = "idle"
        self._publish_state("Disconnected")
        self.logger.log("APP", "Session disconnected cleanly")

    def shutdown(self) -> None:
        self.disconnect()