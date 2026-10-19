from __future__ import annotations

import pytest

from protocol import ChatMessage, ProtocolError, VoicePacket, decode_text_frames, encode_text_frame


def test_voice_packet_round_trip_preserves_header_and_pcm() -> None:
    packet = VoicePacket(sequence=42, timestamp_ns=1_234_567_890, payload=b"\x01\x02" * 160)
    decoded = VoicePacket.decode(packet.encode())
    assert decoded == packet


def test_voice_packet_rejects_length_mismatch() -> None:
    packet = VoicePacket(sequence=1, timestamp_ns=1, payload=b"pcm").encode()
    with pytest.raises(ProtocolError, match="length"):
        VoicePacket.decode(packet[:-1])


def test_voice_packet_rejects_a_malformed_short_datagram() -> None:
    with pytest.raises(ProtocolError, match="shorter"):
        VoicePacket.decode(b"not a packet")


def test_tcp_decoder_handles_partial_and_coalesced_frames() -> None:
    one = encode_text_frame(b"first")
    two = encode_text_frame(b"second")
    buffer = bytearray(one[:6])
    assert decode_text_frames(buffer) == []
    buffer.extend(one[6:] + two)
    assert decode_text_frames(buffer) == [b"first", b"second"]
    assert buffer == bytearray()


def test_tcp_decoder_rejects_oversized_length_before_payload_arrives() -> None:
    buffer = bytearray((65_537).to_bytes(4, "big"))
    with pytest.raises(ProtocolError, match="Invalid TCP frame length"):
        decode_text_frames(buffer)


def test_chat_message_uses_utf8_json_inside_tcp_frame() -> None:
    message = ChatMessage.create(3, "Vikram", "Hello, உலகம்")
    frame = message.to_bytes()
    payload = decode_text_frames(bytearray(frame))[0]
    assert ChatMessage.from_bytes(payload) == message