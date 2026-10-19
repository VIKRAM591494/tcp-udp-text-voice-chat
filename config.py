"""Configuration for the UDP voice and TCP text laboratory prototype."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address


DEFAULT_TCP_PORT = 51000
DEFAULT_UDP_PORT = 51001


@dataclass(slots=True)
class AudioConfig:
    """PCM settings shared by capture and playback."""

    sample_rate: int = 16_000
    channels: int = 1
    sample_width: int = 2  # int16 PCM
    frames_per_packet: int = 320  # 20 ms at 16 kHz
    playout_queue_size: int = 12

    @property
    def packet_bytes(self) -> int:
        return self.frames_per_packet * self.channels * self.sample_width


@dataclass(slots=True)
class NetworkConfig:
    """Addresses used by one app instance.

    For two local instances, give each instance a different ``local_udp_port``.
    The host's ``peer_udp_port`` must point to the client's local UDP port and
    vice versa. TCP clients connect to the host's ``tcp_port``.
    """

    local_ip: str = "0.0.0.0"
    peer_ip: str = "127.0.0.1"
    tcp_port: int = DEFAULT_TCP_PORT
    local_udp_port: int = DEFAULT_UDP_PORT
    peer_udp_port: int = DEFAULT_UDP_PORT + 1
    display_name: str = "Student"

    def validate(self) -> None:
        # 0.0.0.0 is valid for a listening/bound local socket only.
        if self.local_ip != "0.0.0.0":
            ip_address(self.local_ip)
        if self.peer_ip == "0.0.0.0":
            raise ValueError("Peer IP cannot be 0.0.0.0; enter a host or LAN address")
        ip_address(self.peer_ip)
        for port in (self.tcp_port, self.local_udp_port, self.peer_udp_port):
            if not 1 <= port <= 65535:
                raise ValueError(f"Port must be between 1 and 65535: {port}")
        if not self.display_name.strip():
            raise ValueError("Display name cannot be empty")


@dataclass(slots=True)
class AppConfig:
    """Top-level application configuration."""

    audio: AudioConfig
    network: NetworkConfig

    @classmethod
    def defaults(cls) -> "AppConfig":
        return cls(audio=AudioConfig(), network=NetworkConfig())