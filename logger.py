"""Thread-safe event logging for the UI and timestamped log files."""

from __future__ import annotations

import queue
import threading
from datetime import datetime
from pathlib import Path


class EventLogger:
    """Writes human-readable events and exposes them through a non-blocking queue."""

    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / f"voice_chat_{datetime.now():%Y%m%d_%H%M%S}.log"
        self.events: queue.Queue[str] = queue.Queue(maxsize=1_000)
        self._lock = threading.Lock()

    def log(self, category: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] [{category.upper():5}] {message}"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as output:
                output.write(line + "\n")
        try:
            self.events.put_nowait(line)
        except queue.Full:
            try:
                self.events.get_nowait()
                self.events.put_nowait(line)
            except queue.Empty:
                pass

    def clear_ui_queue(self) -> None:
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return