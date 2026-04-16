from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserAudioState:
    user_id: int | None = None
    ssrc: int | None = None
    speaking: bool = False


@dataclass
class VoiceRuntimeState:
    guild_id: int | None = None
    channel_id: int | None = None
    endpoint: str | None = None
    token: str | None = None
    session_id: str | None = None

    ws_connected: asyncio.Event = field(default_factory=asyncio.Event)
    udp_ready: asyncio.Event = field(default_factory=asyncio.Event)
    receive_ready: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)

    dave_epoch: int | None = None
    dave_protocol_version: int | None = None
    dave_ready: bool = False
    dave_status: str | None = None

    voice_mode: str | None = None
    voice_secret_key: bytes | None = None
    audio_codec: str | None = None
    secure_frames_version: int | None = None

    ssrc_to_user_id: dict[int, int] = field(default_factory=dict)
    user_id_to_ssrc: dict[int, int] = field(default_factory=dict)
    dave_ssrc_to_user_id: dict[int, int] = field(default_factory=dict)

    current_speaking_user_id: int | None = None
    current_speaking_ssrc: int | None = None
    pending_user_ids: list[int] = field(default_factory=list)

    last_voice_ws_op: int | None = None
    last_voice_ws_payload: Any | None = None
    last_server_seq: int | None = None

    external_sender_data: Any | None = None
    pending_welcome: Any | None = None
    pending_commit_welcome: Any | None = None
    pending_proposals: Any | None = None
    pending_transition_id: int | None = None

    dave_apply_attempts: int = 0
    last_dave_apply_error: str | None = None

    def bind_ssrc(self, user_id: int, ssrc: int) -> None:
        self.ssrc_to_user_id[ssrc] = user_id
        self.user_id_to_ssrc[user_id] = ssrc

    def bind_dave_ssrc(self, user_id: int, ssrc: int) -> None:
        self.dave_ssrc_to_user_id[ssrc] = user_id
        self.bind_ssrc(user_id, ssrc)

    def get_preferred_user_id(self, ssrc: int) -> int | None:
        return self.dave_ssrc_to_user_id.get(ssrc, self.ssrc_to_user_id.get(ssrc))

    def set_current_speaking(self, user_id: int | None, ssrc: int | None) -> None:
        self.current_speaking_user_id = user_id
        self.current_speaking_ssrc = ssrc

    def clear_mappings(self) -> None:
        self.ssrc_to_user_id.clear()
        self.user_id_to_ssrc.clear()
        self.dave_ssrc_to_user_id.clear()
        self.current_speaking_user_id = None
        self.current_speaking_ssrc = None
        self.pending_user_ids.clear()