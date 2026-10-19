"""Optional UDP-only delay, loss, jitter, and reordering simulation."""

from __future__ import annotations

import heapq
import random
import threading
from dataclasses import dataclass
from time import monotonic
from typing import Callable


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    loss_percent: float = 0.0
    delay_ms: float = 0.0
    jitter_ms: float = 0.0
    reordering: bool = False

    def validate(self) -> None:
        if not 0.0 <= self.loss_percent <= 100.0:
            raise ValueError("Packet loss must be between 0 and 100 percent")
        if self.delay_ms < 0 or self.jitter_ms < 0:
            raise ValueError("Delay and jitter cannot be negative")


class NetworkSimulator:
    """Schedules outbound UDP sends without replacing the real socket path.

    It sits immediately before ``socket.sendto``. A configured drop means a
    datagram intentionally never reaches the operating system; delay and jitter
    schedule a later real ``sendto`` call. TCP never passes through this class.
    """

    def __init__(self, config: SimulatorConfig | None = None, *, seed: int | None = None) -> None:
        self._config = config or SimulatorConfig()
        self._config.validate()
        self._random = random.Random(seed)
        self._condition = threading.Condition()
        self._pending: list[tuple[float, int, bytes, tuple[str, int], Callable[[bytes, tuple[str, int]], None]]]
        self._pending = []
        self._counter = 0
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def config(self) -> SimulatorConfig:
        with self._condition:
            return self._config

    def update(self, config: SimulatorConfig) -> None:
        config.validate()
        with self._condition:
            self._config = config
            self._condition.notify_all()

    def start(self) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._run, name="udp-network-simulator", daemon=True)
        self._thread.start()

    def submit(self, payload: bytes, target: tuple[str, int],
               send_callback: Callable[[bytes, tuple[str, int]], None]) -> bool:
        """Queue a datagram and return false only when simulation drops it."""
        with self._condition:
            config = self._config
            if self._random.random() * 100 < config.loss_percent:
                return False
            variation = self._random.uniform(-config.jitter_ms, config.jitter_ms)
            delay_ms = max(0.0, config.delay_ms + variation)
            if config.reordering and self._random.random() < 0.25:
                # Intentionally vary release timing so adjacent packets may swap.
                delay_ms += self._random.uniform(0.0, max(1.0, config.jitter_ms + 25.0))
            self._counter += 1
            heapq.heappush(self._pending, (
                monotonic() + delay_ms / 1000, self._counter, payload, target, send_callback,
            ))
            self._condition.notify_all()
        return True

    def stop(self) -> None:
        self._running.clear()
        with self._condition:
            self._pending.clear()
            self._condition.notify_all()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.5)

    def _run(self) -> None:
        while self._running.is_set():
            with self._condition:
                while self._running.is_set() and not self._pending:
                    self._condition.wait(timeout=0.25)
                if not self._running.is_set():
                    return
                due, _order, payload, target, callback = self._pending[0]
                remaining = due - monotonic()
                if remaining > 0:
                    self._condition.wait(timeout=min(remaining, 0.25))
                    continue
                heapq.heappop(self._pending)
            try:
                callback(payload, target)
            except OSError:
                # The voice endpoint logs socket errors and handles shutdown.
                continue