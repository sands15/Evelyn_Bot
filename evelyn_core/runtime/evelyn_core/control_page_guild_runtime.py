from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ControlPageGuildSelectionRuntimeDeps:
    get_requested_guild: Callable[[int], Any | None]
    bot_guilds: Callable[[], Any]
    tracked_tts_playback_guild_ids: Callable[[], list[int]]
    get_tracked_tts_playback: Callable[[int], dict[str, Any] | None]
    get_active_session_user_id: Callable[[str], int | None]
    get_guild_member: Callable[[Any, int], Any | None]
    clean_text: Callable[[str], str]


def select_control_page_guild_from_runtime(
    requested_guild_id: int | None,
    *,
    deps: ControlPageGuildSelectionRuntimeDeps,
) -> Any | None:
    if requested_guild_id is not None:
        return deps.get_requested_guild(int(requested_guild_id))

    preferred_ids: list[int] = []
    try:
        preferred_ids.extend([int(guild_id) for guild_id in deps.tracked_tts_playback_guild_ids()])
    except Exception:
        preferred_ids = []

    for guild in deps.bot_guilds() or []:
        if getattr(guild, "voice_client", None) is not None:
            try:
                preferred_ids.append(int(guild.id))
            except Exception:
                pass

    for guild in deps.bot_guilds() or []:
        try:
            preferred_ids.append(int(guild.id))
        except Exception:
            pass

    seen: set[int] = set()
    for guild_id in preferred_ids:
        if guild_id in seen:
            continue
        seen.add(guild_id)
        guild = deps.get_requested_guild(guild_id)
        if guild is not None:
            return guild
    return None


def resolve_guild_member_name_from_runtime(
    guild: Any,
    user_id: int | None,
    *,
    deps: ControlPageGuildSelectionRuntimeDeps,
) -> str:
    if guild is None or user_id is None:
        return "없음"
    member = deps.get_guild_member(guild, int(user_id))
    if member is None:
        return f"user:{int(user_id)}"
    return (
        deps.clean_text(str(getattr(member, "display_name", None) or getattr(member, "name", None) or getattr(member, "id", "")))
        or f"user:{getattr(member, 'id', int(user_id))}"
    )


def current_tts_target_name_from_runtime(
    guild: Any,
    *,
    deps: ControlPageGuildSelectionRuntimeDeps,
) -> str:
    if guild is None:
        return "없음"
    try:
        guild_id = int(getattr(guild, "id"))
    except Exception:
        return "없음"
    playback = deps.get_tracked_tts_playback(guild_id)
    if not isinstance(playback, dict):
        return "없음"
    session_key = deps.clean_text(str(playback.get("session_key") or ""))
    if not session_key:
        return "없음"
    target_user_id = deps.get_active_session_user_id(session_key)
    return resolve_guild_member_name_from_runtime(guild, target_user_id, deps=deps)


__all__ = [
    "ControlPageGuildSelectionRuntimeDeps",
    "select_control_page_guild_from_runtime",
    "resolve_guild_member_name_from_runtime",
    "current_tts_target_name_from_runtime",
]
