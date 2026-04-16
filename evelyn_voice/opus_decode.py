from __future__ import annotations


class OpusDecoder:
    SAMPLE_RATE = 48000
    CHANNELS = 2

    def __init__(self) -> None:
        self.ready = True

    def decode(self, opus_payload: bytes) -> bytes:
        """
        Phase 3에서 discord.opus.Decoder 또는 opuslib로 교체.
        현재는 미구현.
        """
        raise NotImplementedError("Opus decode is not implemented yet")