"""Real PCM audio I/O with an explicit simulation mode for lab fallback."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from config import AudioConfig


class AudioUnavailableError(RuntimeError):
    """Raised when a requested physical input/output device cannot open."""


class AudioIO(ABC):
    """Minimal interface used by capture and playback workers."""

    @abstractmethod
    def read_frame(self) -> bytes:
        """Read one PCM packet from the microphone or simulator."""

    @abstractmethod
    def play_frame(self, frame: bytes) -> None:
        """Play one PCM packet or consume it in simulation mode."""

    @abstractmethod
    def close(self) -> None:
        """Release all audio resources."""


class SoundDeviceAudioIO(AudioIO):
    """PCM capture and playback using the maintained ``sounddevice`` package.

    ``sounddevice`` provides a direct PortAudio binding on macOS and Windows.
    It is imported lazily so the rest of the networking project remains usable
    when no audio library or device is available.
    """

    def __init__(self, config: AudioConfig) -> None:
        try:
            import sounddevice as sd  # type: ignore[import-not-found]
        except ImportError as exc:
            raise AudioUnavailableError(
                "sounddevice is not installed. Install requirements or select Simulation / Audio Test Mode."
            ) from exc
        self._config = config
        try:
            self._input = sd.RawInputStream(
                samplerate=config.sample_rate,
                blocksize=config.frames_per_packet,
                channels=config.channels,
                dtype="int16",
            )
            self._output = sd.RawOutputStream(
                samplerate=config.sample_rate,
                blocksize=config.frames_per_packet,
                channels=config.channels,
                dtype="int16",
            )
            self._input.start()
            self._output.start()
        except Exception as exc:  # Device and permission errors vary by platform.
            self.close()
            raise AudioUnavailableError(f"Could not open microphone/speaker: {exc}") from exc

    def read_frame(self) -> bytes:
        data, overflowed = self._input.read(self._config.frames_per_packet)
        if overflowed:
            # The app logs worker-level errors; capture continues with next frame.
            pass
        return bytes(data)

    def play_frame(self, frame: bytes) -> None:
        self._output.write(frame)

    def close(self) -> None:
        for stream_name in ("_input", "_output"):
            stream = getattr(self, stream_name, None)
            if stream is None:
                continue
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass


class SimulatedAudioIO(AudioIO):
    """A visibly labelled fallback that exercises real sockets with silent PCM.

    Simulation mode does *not* replace the real implementation. It lets a
    classroom demo exercise packetisation, UDP, delay/loss simulation, queues,
    metrics, and TCP chat when microphone permission or speakers are unavailable.
    """

    def __init__(self, config: AudioConfig) -> None:
        self._config = config
        self._frame_period_s = config.frames_per_packet / config.sample_rate
        self._closed = False

    def read_frame(self) -> bytes:
        time.sleep(self._frame_period_s)
        return b"\x00" * self._config.packet_bytes

    def play_frame(self, frame: bytes) -> None:
        # Timing remains controlled by arrival and playout workers.
        if len(frame) != self._config.packet_bytes:
            raise AudioUnavailableError("Simulation received a PCM frame with an unexpected size")

    def close(self) -> None:
        self._closed = True


def create_audio_io(config: AudioConfig, simulation: bool) -> AudioIO:
    """Create the requested audio backend without silently falling back."""
    return SimulatedAudioIO(config) if simulation else SoundDeviceAudioIO(config)