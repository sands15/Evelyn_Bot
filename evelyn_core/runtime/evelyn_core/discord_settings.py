from __future__ import annotations

import time
from typing import Any, Callable

from .config import DEFAULT_COMMAND_PREFIX
from .memory import guild_settings_path, read_json_file, write_json_file


def normalize_command_prefix(prefix: str | None, *, default_prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    prefix = (prefix or "").strip()
    if not prefix:
        return default_prefix
    if any(ch.isspace() for ch in prefix):
        raise ValueError("명령어 시작 부호에는 공백을 넣을 수 없어.")
    if len(prefix) > 5:
        raise ValueError("명령어 시작 부호는 5자 이하로 해줘.")
    return prefix


def get_guild_command_prefix(
    guild_id: int | None,
    *,
    prefix_cache: dict[int, str] | None = None,
    default_prefix: str = DEFAULT_COMMAND_PREFIX,
) -> str:
    if guild_id is None:
        return default_prefix
    if prefix_cache is not None:
        cached = prefix_cache.get(guild_id)
        if cached:
            return cached

    settings = read_json_file(guild_settings_path(guild_id))
    prefix = normalize_command_prefix(str(settings.get("command_prefix", default_prefix)), default_prefix=default_prefix)
    if prefix_cache is not None:
        prefix_cache[guild_id] = prefix
    return prefix


def save_guild_command_prefix(
    guild_id: int,
    prefix: str,
    *,
    prefix_cache: dict[int, str] | None = None,
    default_prefix: str = DEFAULT_COMMAND_PREFIX,
    now: Callable[[], float] = time.time,
) -> str:
    normalized = normalize_command_prefix(prefix, default_prefix=default_prefix)
    settings_path = guild_settings_path(guild_id)
    settings = read_json_file(settings_path)
    settings["command_prefix"] = normalized
    settings["updated_at"] = int(now())
    write_json_file(settings_path, settings)
    if prefix_cache is not None:
        prefix_cache[guild_id] = normalized
    return normalized


def normalize_channel_id_list(values: list[Any] | tuple[Any, ...] | None) -> list[int]:
    normalized: list[int] = []
    for value in values or []:
        try:
            channel_id = int(value)
        except (TypeError, ValueError):
            continue
        if channel_id not in normalized:
            normalized.append(channel_id)
    return normalized


def get_guild_channel_ids(guild_id: int | None, key: str) -> list[int]:
    if guild_id is None:
        return []
    settings = read_json_file(guild_settings_path(guild_id))
    return normalize_channel_id_list(settings.get(key))


def get_guild_observe_channel_ids(guild_id: int | None) -> list[int]:
    return get_guild_channel_ids(guild_id, "observe_channel_ids")


def get_guild_command_only_channel_ids(guild_id: int | None) -> list[int]:
    return get_guild_channel_ids(guild_id, "command_only_channel_ids")


def save_guild_channel_list(
    guild_id: int,
    key: str,
    channel_ids: list[int],
    *,
    now: Callable[[], float] = time.time,
) -> list[int]:
    settings_path = guild_settings_path(guild_id)
    settings = read_json_file(settings_path)
    normalized = normalize_channel_id_list(channel_ids)
    settings[key] = normalized
    settings["updated_at"] = int(now())
    write_json_file(settings_path, settings)
    return normalized


def add_guild_channel_setting(guild_id: int, key: str, channel_id: int) -> list[int]:
    existing = get_guild_channel_ids(guild_id, key)
    if channel_id not in existing:
        existing.append(channel_id)
    return save_guild_channel_list(guild_id, key, existing)


def remove_guild_channel_setting(guild_id: int, key: str, channel_id: int) -> list[int]:
    existing = get_guild_channel_ids(guild_id, key)
    return save_guild_channel_list(guild_id, key, [value for value in existing if value != channel_id])


__all__ = [
    "add_guild_channel_setting",
    "get_guild_channel_ids",
    "get_guild_command_only_channel_ids",
    "get_guild_command_prefix",
    "get_guild_observe_channel_ids",
    "normalize_channel_id_list",
    "normalize_command_prefix",
    "remove_guild_channel_setting",
    "save_guild_channel_list",
    "save_guild_command_prefix",
]
