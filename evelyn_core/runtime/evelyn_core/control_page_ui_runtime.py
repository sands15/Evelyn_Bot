from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ControlPageUiRuntimeDeps:
    control_page_host: str
    control_page_port: int
    local_control_guild_id: int
    local_control_guild_name: str
    control_page_welcome_fallback: str
    clean_text: Callable[[str], str]
    sanitize_control_page_welcome_text_payload: Callable[[str, str], str]
    control_page_ui_command_store: Any
    control_page_chat_log_store: Any


def enqueue_control_page_ui_command_from_runtime(
    action: str,
    *,
    panel_id: str | None,
    deps: ControlPageUiRuntimeDeps,
) -> dict[str, Any]:
    return deps.control_page_ui_command_store.enqueue(action, panel_id=panel_id)


def build_control_page_panel_state_from_runtime(deps: ControlPageUiRuntimeDeps) -> dict[str, Any]:
    return deps.control_page_ui_command_store.panel_state()


def control_page_local_url_from_runtime(deps: ControlPageUiRuntimeDeps) -> str:
    return f"http://{deps.control_page_host}:{deps.control_page_port}/"


def control_page_session_key_from_runtime(
    guild_id: int | None,
    deps: ControlPageUiRuntimeDeps,
) -> str:
    if guild_id is None or int(guild_id) == deps.local_control_guild_id:
        return "control-page:local"
    return f"control-page:{int(guild_id)}"


def control_page_effective_guild_id_from_runtime(
    guild: Any,
    deps: ControlPageUiRuntimeDeps,
) -> int:
    return int(getattr(guild, "id", deps.local_control_guild_id) or deps.local_control_guild_id)


def control_page_effective_guild_name_from_runtime(
    guild: Any,
    deps: ControlPageUiRuntimeDeps,
) -> str:
    if guild is None:
        return deps.local_control_guild_name
    return deps.clean_text(str(getattr(guild, "name", "") or "")) or deps.local_control_guild_name


def append_control_page_chat_log_from_runtime(
    guild_id: int,
    role: str,
    author: str,
    text: str,
    deps: ControlPageUiRuntimeDeps,
) -> None:
    deps.control_page_chat_log_store.append(guild_id, role, author, text)


def get_control_page_chat_log_from_runtime(guild_id: int, deps: ControlPageUiRuntimeDeps) -> list[dict[str, Any]]:
    return deps.control_page_chat_log_store.get(guild_id)


def sanitize_control_page_welcome_text_from_runtime(text: str, deps: ControlPageUiRuntimeDeps) -> str:
    return deps.sanitize_control_page_welcome_text_payload(
        text,
        fallback=deps.control_page_welcome_fallback,
    )


__all__ = [
    "ControlPageUiRuntimeDeps",
    "append_control_page_chat_log_from_runtime",
    "build_control_page_panel_state_from_runtime",
    "control_page_effective_guild_id_from_runtime",
    "control_page_effective_guild_name_from_runtime",
    "control_page_local_url_from_runtime",
    "control_page_session_key_from_runtime",
    "enqueue_control_page_ui_command_from_runtime",
    "get_control_page_chat_log_from_runtime",
    "sanitize_control_page_welcome_text_from_runtime",
]
