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
    stable_ssrc_to_user_id: dict[int, int] = field(default_factory=dict)
    ssrc_binding_source: dict[int, str] = field(default_factory=dict)
    speaking_user_by_ssrc: dict[int, int] = field(default_factory=dict)

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

    def bind_ssrc(
        self,
        user_id: int,
        ssrc: int,
        *,
        source: str = "unknown",
        stable: bool = False,
        allow_override: bool = True,
    ) -> bool:
        user_id = int(user_id)
        ssrc = int(ssrc)

        locked_user_id = self.stable_ssrc_to_user_id.get(ssrc)
        if locked_user_id is not None and locked_user_id != user_id and not allow_override:
            return False

        previous_user_id = self.ssrc_to_user_id.get(ssrc)
        self.ssrc_to_user_id[ssrc] = user_id
        self.user_id_to_ssrc[user_id] = ssrc
        self.ssrc_binding_source[ssrc] = source

        if stable:
            self.stable_ssrc_to_user_id[ssrc] = user_id

        return previous_user_id != user_id

    def get_bound_user_id(self, ssrc: int, *, prefer_stable: bool = True) -> int | None:
        ssrc = int(ssrc)
        if prefer_stable:
            stable_user_id = self.stable_ssrc_to_user_id.get(ssrc)
            if stable_user_id is not None:
                return stable_user_id
        return self.ssrc_to_user_id.get(ssrc)

    def get_binding_source(self, ssrc: int) -> str | None:
        return self.ssrc_binding_source.get(int(ssrc))

    def is_stable_binding(self, ssrc: int) -> bool:
        return int(ssrc) in self.stable_ssrc_to_user_id

    def get_active_speaker_ids(self) -> list[int]:
        return list(dict.fromkeys(self.speaking_user_by_ssrc.values()))

    def set_current_speaking(self, user_id: int | None, ssrc: int | None) -> None:
        self.current_speaking_user_id = user_id
        self.current_speaking_ssrc = ssrc

    def set_speaking(self, user_id: int | None, ssrc: int | None, speaking: bool) -> None:
        if user_id is None and ssrc is None:
            self.set_current_speaking(None, None)
            return

        if user_id is None and ssrc is not None:
            user_id = self.speaking_user_by_ssrc.get(int(ssrc))
        if ssrc is None and user_id is not None:
            ssrc = self.user_id_to_ssrc.get(int(user_id))
        if user_id is None or ssrc is None:
            return

        user_id = int(user_id)
        ssrc = int(ssrc)

        if speaking:
            self.speaking_user_by_ssrc[ssrc] = user_id
            self.set_current_speaking(user_id, ssrc)
            return

        if self.speaking_user_by_ssrc.get(ssrc) == user_id:
            self.speaking_user_by_ssrc.pop(ssrc, None)

        if self.current_speaking_user_id == user_id or self.current_speaking_ssrc == ssrc:
            self.set_current_speaking(None, None)

    def clear_speaking(self, *, user_id: int | None = None, ssrc: int | None = None) -> None:
        self.set_speaking(user_id, ssrc, False)

    def clear_mappings(self) -> None:
        self.ssrc_to_user_id.clear()
        self.user_id_to_ssrc.clear()
        self.stable_ssrc_to_user_id.clear()
        self.ssrc_binding_source.clear()
        self.speaking_user_by_ssrc.clear()
        self.current_speaking_user_id = None
        self.current_speaking_ssrc = None
        self.pending_user_ids.clear()
