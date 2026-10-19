from __future__ import annotations

import threading

from network_simulator import NetworkSimulator, SimulatorConfig


def test_simulator_drops_at_100_percent_without_calling_socket_boundary() -> None:
    simulator = NetworkSimulator(SimulatorConfig(loss_percent=100), seed=1)
    called = threading.Event()
    simulator.start()
    try:
        accepted = simulator.submit(b"packet", ("127.0.0.1", 9), lambda *_: called.set())
        assert not accepted
        assert not called.wait(0.1)
    finally:
        simulator.stop()


def test_simulator_dispatches_non_dropped_payload_to_real_send_boundary() -> None:
    simulator = NetworkSimulator(SimulatorConfig(delay_ms=1), seed=1)
    received: list[tuple[bytes, tuple[str, int]]] = []
    done = threading.Event()

    def callback(payload: bytes, target: tuple[str, int]) -> None:
        received.append((payload, target))
        done.set()

    simulator.start()
    try:
        assert simulator.submit(b"packet", ("127.0.0.1", 9999), callback)
        assert done.wait(0.5)
        assert received == [(b"packet", ("127.0.0.1", 9999))]
    finally:
        simulator.stop()