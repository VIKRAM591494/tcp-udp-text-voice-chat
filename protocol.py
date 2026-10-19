"""Inspectable UDP and TCP wire formats.

Voice datagrams carry a small binary header. Chat uses a four-byte network-byte-
order length prefix followed by a UTF-8 JSON document. Keeping both formats here
makes the transport contract easy to inspect during a laboratory demonstration.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from time import time_ns
from typing import Any


VOICE_MAGIC = b"VCT1"
VOICE_VERSION = 1
VOICE_HEADER = struct.Struct("!4sBIQH")
MAX_AUDIO_PAYLOAD = 65_000
MAX_TEXT_FRAME = 64 * 1024


class ProtocolError(ValueError):
    """Raised for malformed, unsupported, or oversized wire data."""


@dataclass(frozen=True, slots=True)
class VoicePacket:
    """A single self-contained UDP audio frame."""

    sequence: int
    timestamp_ns: int
    payload: bytes
    version: int = VOICE_VERSION

    @classmethod
    def create(cls, sequence: int, payload: bytes) -> "VoicePacket":
        return cls(sequence=sequence, timestamp_ns=time_ns(), payload=payload)

    def encode(self) -> bytes:
        if not 0 <= self.sequence <= 0xFFFFFFFF:
            raise ProtocolError("Voice sequence number is out of range")
        if len(self.payload) > MAX_AUDIO_PAYLOAD:
            raise ProtocolError("Voice payload is too large for one datagram")
        return VOICE_HEADER.pack(
            VOICE_MAGIC,
            self.version,
            self.sequence,
            self.timestamp_ns,
            len(self.payload),
        ) + self.payload

    @classmethod
    def decode(cls, data: bytes) -> "VoicePacket":
        if len(data) < VOICE_HEADER.size:
            raise ProtocolError("UDP datagram is shorter than the voice header")
        magic, version, sequence, timestamp_ns, length = VOICE_HEADER.unpack_from(data)
        if magic != VOICE_MAGIC:
            raise ProtocolError("Unexpected UDP voice packet identifier")
        if version != VOICE_VERSION:
            raise ProtocolError(f"Unsupported voice protocol version: {version}")
        payload = data[VOICE_HEADER.size:]
        if length != len(payload):
            raise ProtocolError("Voice payload length does not match packet header")
        return cls(sequence=sequence, timestamp_ns=timestamp_ns, payload=payload, version=version)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A text message serialised inside a length-prefixed TCP frame."""

    message_id: int
    sender: str
    text: str
    timestamp_ns: int

    @classmethod
    def create(cls, message_id: int, sender: str, text: str) -> "ChatMessage":
        if not text.strip():
            raise ProtocolError("Message cannot be empty")
        return cls(message_id=message_id, sender=sender, text=text, timestamp_ns=time_ns())

    def to_bytes(self) -> bytes:
        document = json.dumps(
            {
                "id": self.message_id,
                "sender": self.sender,
                "text": self.text,
                "timestamp_ns": self.timestamp_ns,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return encode_text_frame(document)

    @classmethod
    def from_bytes(cls, payload: bytes) -> "ChatMessage":
        try:
            value: dict[str, Any] = json.loads(payload.decode("utf-8"))
            message = cls(
                message_id=int(value["id"]),
                sender=str(value["sender"]),
                text=str(value["text"]),
                timestamp_ns=int(value["timestamp_ns"]),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("Malformed chat message") from exc
        if not message.text.strip() or len(message.text) > MAX_TEXT_FRAME:
            raise ProtocolError("Invalid chat text")
        return message


def encode_text_frame(payload: bytes) -> bytes:
    """Return the length-prefixed TCP representation of ``payload``."""
    if not payload or len(payload) > MAX_TEXT_FRAME:
        raise ProtocolError("Text frame is empty or exceeds the configured maximum")
    return struct.pack("!I", len(payload)) + payload


def decode_text_frames(buffer: bytearray) -> list[bytes]:
    """Remove every complete TCP frame from ``buffer`` and return its payloads.

    This function deliberately handles partial and coalesced ``recv`` data. TCP
    does not preserve application-message boundaries.
    """
    frames: list[bytes] = []
    while len(buffer) >= 4:
        length = struct.unpack("!I", buffer[:4])[0]
        if length == 0 or length > MAX_TEXT_FRAME:
            raise ProtocolError(f"Invalid TCP frame length: {length}")
        if len(buffer) < 4 + length:
            break
        frames.append(bytes(buffer[4:4 + length]))
        del buffer[:4 + length]
    return frames