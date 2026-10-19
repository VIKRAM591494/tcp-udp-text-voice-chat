from __future__ import annotations

from app import run_self_test


def test_real_localhost_tcp_udp_loopback() -> None:
    result = run_self_test()
    assert result["tcp"]["host_received"] == 1
    assert result["tcp"]["client_received"] == 1
    assert result["udp"]["client_sent"] > 0
    assert result["udp"]["host_received"] > 0