from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class DiscordSettingsRuntimeDeps:
    default_command_prefix: str
    prefix_cache: dict[int, str] | None
    normalize_command_prefix_payload: Callable[..., str]
    get_guild_command_prefix_payload: Callable[..., str]
    save_guild_command_prefix_payload: Callable[..., str]
    get_guild_observe_channel_ids_payload: Callable[..., list[int]]
    get_guild_command_only_channel_ids_payload: Callable[..., list[int]]
    save_guild_channel_list_payload: Callable[..., list[int]]
    add_guild_channel_setting_payload: Callable[..., list[int]]
    remove_guild_channel_setting_payload: Callable[..., list[int]]
    now: Callable[[], float]


def build_discord_settings_runtime_deps(
    *,
    default_command_prefix: str,
    prefix_cache: dict[int, str] | None,
    now: Callable[[], float] = time.time,
) -> DiscordSettingsRuntimeDeps:
    from .discord_settings import (
        add_guild_channel_setting,
        get_guild_command_only_channel_ids,
        get_guild_command_prefix,
        get_guild_observe_channel_ids,
        normalize_command_prefix,
        remove_guild_channel_setting,
        save_guild_channel_list,
        save_guild_command_prefix,
    )

    return DiscordSettingsRuntimeDeps(
        default_command_prefix=default_command_prefix,
        prefix_cache=prefix_cache,
        normalize_command_prefix_payload=normalize_command_prefix,
        get_guild_command_prefix_payload=get_guild_command_prefix,
        save_guild_command_prefix_payload=save_guild_command_prefix,
        get_guild_observe_channel_ids_payload=get_guild_observe_channel_ids,
        get_guild_command_only_channel_ids_payload=get_guild_command_only_channel_ids,
        save_guild_channel_list_payload=save_guild_channel_list,
        add_guild_channel_setting_payload=add_guild_channel_setting,
        remove_guild_channel_setting_payload=remove_guild_channel_setting,
        now=now,
    )


def resolve_command_prefix_from_runtime(
    guild_id: int | None,
    *,
    get_guild_command_prefix: Callable[[int | None], str],
) -> str:
    return get_guild_command_prefix(guild_id)


def normalize_command_prefix_from_runtime(prefix: str | None, *, deps: DiscordSettingsRuntimeDeps) -> str:
    return deps.normalize_command_prefix_payload(prefix, default_prefix=deps.default_command_prefix)


def get_guild_command_prefix_from_runtime(
    guild_id: int | None,
    *,
    deps: DiscordSettingsRuntimeDeps,
) -> str:
    return deps.get_guild_command_prefix_payload(
        guild_id,
        prefix_cache=deps.prefix_cache,
        default_prefix=deps.default_command_prefix,
    )


def save_guild_command_prefix_from_runtime(guild_id: int, prefix: str, *, deps: DiscordSettingsRuntimeDeps) -> str:
    return deps.save_guild_command_prefix_payload(
        guild_id,
        prefix,
        prefix_cache=deps.prefix_cache,
        default_prefix=deps.default_command_prefix,
        now=deps.now,
    )


def get_guild_observe_channel_ids_from_runtime(guild_id: int | None, *, deps: DiscordSettingsRuntimeDeps) -> list[int]:
    return deps.get_guild_observe_channel_ids_payload(guild_id)


def get_guild_command_only_channel_ids_from_runtime(
    guild_id: int | None,
    *,
    deps: DiscordSettingsRuntimeDeps,
) -> list[int]:
    return deps.get_guild_command_only_channel_ids_payload(guild_id)


def save_guild_channel_list_from_runtime(
    guild_id: int,
    key: str,
    channel_ids: list[int],
    *,
    deps: DiscordSettingsRuntimeDeps,
) -> list[int]:
    return deps.save_guild_channel_list_payload(
        guild_id,
        key,
        channel_ids,
        now=deps.now,
    )


def add_guild_channel_setting_from_runtime(
    guild_id: int,
    key: str,
    channel_id: int,
    *,
    deps: DiscordSettingsRuntimeDeps,
) -> list[int]:
    return deps.add_guild_channel_setting_payload(guild_id, key, channel_id)


def remove_guild_channel_setting_from_runtime(
    guild_id: int,
    key: str,
    channel_id: int,
    *,
    deps: DiscordSettingsRuntimeDeps,
) -> list[int]:
    return deps.remove_guild_channel_setting_payload(guild_id, key, channel_id)


__all__ = [
    "DiscordSettingsRuntimeDeps",
    "build_discord_settings_runtime_deps",
    "resolve_command_prefix_from_runtime",
    "add_guild_channel_setting_from_runtime",
    "get_guild_command_only_channel_ids_from_runtime",
    "get_guild_command_prefix_from_runtime",
    "get_guild_observe_channel_ids_from_runtime",
    "normalize_command_prefix_from_runtime",
    "remove_guild_channel_setting_from_runtime",
    "save_guild_channel_list_from_runtime",
    "save_guild_command_prefix_from_runtime",
]
