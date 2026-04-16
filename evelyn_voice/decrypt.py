from __future__ import annotations

from .dave_session import DaveSession


class PacketDecryptor:
    def __init__(self, dave_session: DaveSession):
        self.dave_session = dave_session

    def decrypt_audio_packet(self, raw_packet: bytes) -> bytes:
        return self.dave_session.decrypt_rtp(raw_packet)