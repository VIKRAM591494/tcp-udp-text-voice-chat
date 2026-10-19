"""Browser text and voice chat: WebSocket signaling/text + WebRTC media."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
NAME_RE = re.compile(r"[^\x00-\x1f\x7f]{1,30}")
MAX_MESSAGE_CHARS = 2000
MAX_SIGNAL_BYTES = 64 * 1024
MAX_ROOM_PEERS = 2

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("browser_voice_chat")


@dataclass(eq=False)
class Peer:
    client_id: str
    name: str
    websocket: WebSocket


@dataclass
class Room:
    peers: list[Peer] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RoomRegistry:
    """Single-process room storage; no chat history is persisted."""
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}
        self.lock = asyncio.Lock()

    async def get_or_create(self, code: str) -> Room:
        async with self.lock:
            room = self.rooms.get(code)
            if room is None:
                room = Room()
                self.rooms[code] = room
            return room

    async def remove_if_empty(self, code: str, room: Room) -> None:
        async with self.lock:
            if not room.peers and self.rooms.get(code) is room:
                self.rooms.pop(code, None)


registry = RoomRegistry()
app = FastAPI(title="TCP/UDP Chat — Browser Edition", version="1.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "browser-voice-chat"})


@app.get("/ice-config", include_in_schema=False)
async def ice_config() -> JSONResponse:
    """Return public STUN plus optionally configured TURN relay settings.

    TURN credentials must be supplied by the operator. For production, prefer
    short-lived TURN credentials from a provider rather than permanent secrets.
    Browser clients can necessarily inspect any credentials delivered to them.
    """
    servers: list[dict[str, Any]] = [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun.cloudflare.com:3478"},
    ]
    turn_urls = [url.strip() for url in os.getenv("TURN_URLS", "").split(",") if url.strip()]
    turn_username = os.getenv("TURN_USERNAME", "").strip()
    turn_credential = os.getenv("TURN_CREDENTIAL", "").strip()
    if turn_urls and turn_username and turn_credential:
        servers.append({"urls": turn_urls, "username": turn_username, "credential": turn_credential})
    return JSONResponse({"iceServers": servers})


async def safe_send(peer: Peer, payload: dict[str, Any]) -> bool:
    try:
        await peer.websocket.send_json(payload)
        return True
    except Exception:
        return False


async def relay_to_room(room: Room, payload: dict[str, Any]) -> None:
    # The application intentionally supports two-person demonstration rooms only.
    await asyncio.gather(*(safe_send(p, payload) for p in list(room.peers)))


async def relay_signal(room: Room, sender: Peer, message: dict[str, Any]) -> None:
    target_id = message.get("peer_id")
    kind = message.get("kind")
    data = message.get("data")
    if not isinstance(target_id, str) or kind not in {"offer", "answer", "ice"}:
        await safe_send(sender, {"type": "error", "message": "Invalid WebRTC signaling message."})
        return
    try:
        data_size = len(json.dumps(data, separators=(",", ":")))
    except (TypeError, ValueError):
        data_size = MAX_SIGNAL_BYTES + 1
    if data_size > MAX_SIGNAL_BYTES:
        await safe_send(sender, {"type": "error", "message": "Signaling payload is too large."})
        return
    recipient = next((p for p in list(room.peers) if p.client_id == target_id and p is not sender), None)
    if recipient is None:
        await safe_send(sender, {"type": "error", "message": "The other participant is no longer connected."})
        return
    await safe_send(recipient, {
        "type": "signal", "peer_id": sender.client_id, "kind": kind, "data": data
    })


@app.websocket("/ws/{room_code}")
async def websocket_endpoint(websocket: WebSocket, room_code: str) -> None:
    await websocket.accept()
    if not ROOM_RE.fullmatch(room_code):
        await websocket.send_json({"type": "error", "message": "Room code must be 3–32 letters, numbers, hyphens, or underscores."})
        await websocket.close(code=1008)
        return

    supplied_name = websocket.query_params.get("name", "Guest").strip()
    name = supplied_name[:30] if NAME_RE.fullmatch(supplied_name) else "Guest"
    room = await registry.get_or_create(room_code)
    peer = Peer(client_id=uuid.uuid4().hex[:10], name=name, websocket=websocket)

    async with room.lock:
        if len(room.peers) >= MAX_ROOM_PEERS:
            await websocket.send_json({"type": "error", "message": "This room already has two participants."})
            await websocket.close(code=4409)
            await registry.remove_if_empty(room_code, room)
            return
        existing = list(room.peers)
        room.peers.append(peer)

    await safe_send(peer, {
        "type": "welcome",
        "client_id": peer.client_id,
        "name": name,
        "is_initiator": len(existing) == 0,
        "peers": [{"id": p.client_id, "name": p.name} for p in existing],
        "room": room_code,
    })
    for other in existing:
        await safe_send(other, {"type": "peer_joined", "peer": {"id": peer.client_id, "name": peer.name}})
    await relay_to_room(room, {
        "type": "system", "text": f"{name} joined the room.", "timestamp": datetime.now(timezone.utc).isoformat()
    })
    log.info("peer joined room=%s peers=%d", room_code, len(room.peers))

    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await safe_send(peer, {"type": "error", "message": "Messages must be JSON objects."})
                continue
            kind = message.get("type")
            if kind == "chat":
                text = message.get("text")
                if not isinstance(text, str):
                    continue
                text = text.strip()
                if not text:
                    continue
                if len(text) > MAX_MESSAGE_CHARS:
                    await safe_send(peer, {"type": "error", "message": f"Messages are limited to {MAX_MESSAGE_CHARS} characters."})
                    continue
                await relay_to_room(room, {
                    "type": "chat", "client_id": peer.client_id, "name": peer.name,
                    "text": text, "timestamp": datetime.now(timezone.utc).isoformat()
                })
            elif kind == "signal":
                await relay_signal(room, peer, message)
            elif kind == "ping":
                await safe_send(peer, {"type": "pong", "timestamp": time.time()})
            else:
                await safe_send(peer, {"type": "error", "message": "Unknown message type."})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.info("websocket ended room=%s client=%s (%s)", room_code, peer.client_id, type(exc).__name__)
    finally:
        async with room.lock:
            if peer in room.peers:
                room.peers.remove(peer)
            remaining = list(room.peers)
        if remaining:
            await relay_to_room(room, {
                "type": "peer_left", "peer_id": peer.client_id, "become_initiator": True,
                "text": f"{peer.name} left the room."
            })
        else:
            await registry.remove_if_empty(room_code, room)
        log.info("peer left room=%s peers=%d", room_code, len(remaining))