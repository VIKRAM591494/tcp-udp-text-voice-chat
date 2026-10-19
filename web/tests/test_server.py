from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from web.app import app


def next_type(ws, expected: str, max_reads: int = 8):
    for _ in range(max_reads):
        payload = ws.receive_json()
        if payload.get("type") == expected:
            return payload
    raise AssertionError(f"Didn't receive message type {expected!r}")


def test_health_and_browser_assets():
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        page = client.get("/")
        assert page.status_code == 200
        assert 'id="joinBtn"' in page.text
        assert "Relay" in page.text
        js = client.get("/static/app.js")
        assert js.status_code == 200
        assert "RTCPeerConnection" in js.text


def test_room_welcome_and_text_relay():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/text-relay-101?name=Alex") as alice:
            alice_welcome = next_type(alice, "welcome")
            assert alice_welcome["is_initiator"] is True
            alice.send_json({"type": "ping"})
            assert next_type(alice, "pong")["type"] == "pong"
            with client.websocket_connect("/ws/text-relay-101?name=Sam") as sam:
                sam_welcome = next_type(sam, "welcome")
                assert sam_welcome["is_initiator"] is False
                assert sam_welcome["peers"][0]["name"] == "Alex"
                joined = next_type(alice, "peer_joined")
                assert joined["peer"]["name"] == "Sam"
                sam.send_json({"type": "chat", "text": "Hello from browser"})
                alice_msg = next_type(alice, "chat")
                sam_msg = next_type(sam, "chat")
                assert alice_msg["text"] == "Hello from browser"
                assert sam_msg["client_id"] == sam_welcome["client_id"]
                assert alice_msg["name"] == "Sam"


def test_websocket_signaling_is_only_relayed_to_peer_in_same_room():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signal-room-22?name=Alex") as alice:
            a = next_type(alice, "welcome")
            with client.websocket_connect("/ws/signal-room-22?name=Sam") as sam:
                b = next_type(sam, "welcome")
                next_type(alice, "peer_joined")
                sam.send_json({"type": "signal", "peer_id": a["client_id"], "kind": "offer", "data": {"type": "offer", "sdp": "example"}})
                signal = next_type(alice, "signal")
                assert signal["peer_id"] == b["client_id"]
                assert signal["kind"] == "offer"
                # A forged target ID is rejected and never broadcast to the other peer.
                sam.send_json({"type": "signal", "peer_id": "not-a-room-member", "kind": "offer", "data": {}})
                err = next_type(sam, "error")
                assert "no longer connected" in err["message"]


def test_room_rejects_third_participant_and_invalid_room_code():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/room-limit-33?name=One") as one:
            next_type(one, "welcome")
            with client.websocket_connect("/ws/room-limit-33?name=Two") as two:
                next_type(two, "welcome")
                try:
                    with client.websocket_connect("/ws/room-limit-33?name=Three") as three:
                        error = next_type(three, "error")
                        assert "two participants" in error["message"]
                except WebSocketDisconnect:
                    pass
        try:
            with client.websocket_connect("/ws/no!?name=Bad") as invalid:
                error = next_type(invalid, "error")
                assert "Room code" in error["message"]
        except WebSocketDisconnect:
            pass


def test_chat_message_length_is_limited():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/length-room-4?name=Guest") as ws:
            next_type(ws, "welcome")
            ws.send_json({"type": "chat", "text": "x" * 2001})
            error = next_type(ws, "error")
            assert "limited to 2000" in error["message"]


def test_ice_config_returns_stun_and_optional_turn(monkeypatch):
    with TestClient(app) as client:
        default = client.get("/ice-config").json()["iceServers"]
        assert any(item["urls"].startswith("stun:") for item in default)
        monkeypatch.setenv("TURN_URLS", "turn:relay.example:3478,turns:relay.example:5349")
        monkeypatch.setenv("TURN_USERNAME", "temporary-user")
        monkeypatch.setenv("TURN_CREDENTIAL", "temporary-password")
        relay = client.get("/ice-config").json()["iceServers"][-1]
        assert relay["urls"] == ["turn:relay.example:3478", "turns:relay.example:5349"]
        assert relay["username"] == "temporary-user"
        assert relay["credential"] == "temporary-password"