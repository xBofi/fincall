"""The contract between a phone carrier and the voice pipeline.

A carrier adapter (Twilio Media Streams, Vonage WebSockets, ...) does two things:

1. It implements `CarrierChannel`, so the bridge can talk *to* the call: send audio frames in
   the carrier's own format, place a playback marker, clear what is queued (barge-in) and hang
   up.
2. It calls the bridge when something comes *from* the call: `begin()` once the media stream is
   authenticated, `receive_audio(frame)` for every inbound frame, `mark_played(name)` when the
   carrier confirms a marker was reached, `receive_dtmf(digit)` for key presses and `close()`
   when the stream ends.

Everything else (STT, the graph, TTS, barge-in policy, the delivery ledger, latency metrics,
the recording) lives in `bridge.CallBridge` and never knows which carrier it is talking to. The
only carrier-specific fact the bridge needs is the audio format, which is why it is part of the
channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

Encoding = Literal["mulaw", "linear16"]


@dataclass(frozen=True)
class AudioFormat:
    """Wire format of a carrier's media stream: mono, fixed-size frames."""

    encoding: Encoding
    rate: int
    frame_ms: int = 20

    @property
    def bytes_per_sample(self) -> int:
        return 1 if self.encoding == "mulaw" else 2

    @property
    def frame_bytes(self) -> int:
        return self.rate * self.frame_ms // 1000 * self.bytes_per_sample

    @property
    def silence(self) -> bytes:
        """One byte pattern of digital silence (μ-law 0xFF, linear16 0x0000)."""
        return b"\xff" if self.encoding == "mulaw" else b"\x00"


#: Twilio Media Streams: G.711 μ-law, 8 kHz, base64 inside JSON messages.
MULAW_8K = AudioFormat("mulaw", 8000)
#: Vonage WebSockets with `content-type: audio/l16;rate=16000`: raw signed 16-bit
#: little-endian PCM in binary messages.
LINEAR16_16K = AudioFormat("linear16", 16000)


@runtime_checkable
class CarrierChannel(Protocol):
    """What the bridge needs from a carrier. Twilio is the reference implementation."""

    audio_format: AudioFormat

    async def send_audio(self, frame: bytes) -> None:
        """Queue one frame of bot audio, already in `audio_format`."""

    async def mark(self, name: str) -> None:
        """Queue a marker after the audio sent so far. The adapter must call
        `bridge.mark_played(name)` when the carrier reports it was reached."""

    async def clear(self) -> None:
        """Drop the bot audio that is queued but not played yet (barge-in)."""

    async def hang_up(self) -> None:
        """End the call. Must be idempotent."""


# Kept so older imports keep working; new code should use `CarrierChannel`.
PhoneChannel = CarrierChannel
