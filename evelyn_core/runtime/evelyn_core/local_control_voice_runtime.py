from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class LocalControlVoiceGuild:
    id: int
    name: str
    voice_client: Any = None


@dataclass(slots=True)
class LocalControlVoiceClient:
    guild: LocalControlVoiceGuild
    local_speaker_output: bool = True
    channel: Any = None


@dataclass(slots=True)
class LocalControlVoiceMember:
    id: int
    display_name: str
    name: str
    guild: LocalControlVoiceGuild
    bot: bool = False


def build_local_control_voice_member_from_runtime(
    *,
    local_control_guild_id: int,
    local_control_guild_name: str,
    local_mic_discord_user_ids: set[int],
    local_mic_user_name: str,
) -> LocalControlVoiceMember:
    user_id = min(local_mic_discord_user_ids) if local_mic_discord_user_ids else local_control_guild_id
    guild = LocalControlVoiceGuild(id=local_control_guild_id, name=local_control_guild_name)
    guild.voice_client = LocalControlVoiceClient(guild=guild)
    return LocalControlVoiceMember(
        id=int(user_id),
        display_name=local_mic_user_name,
        name=local_mic_user_name,
        guild=guild,
    )


def is_local_speaker_voice_client_from_runtime(vc: Any) -> bool:
    return bool(getattr(vc, "local_speaker_output", False))
