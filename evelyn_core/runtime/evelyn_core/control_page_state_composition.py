from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .control_page_state_handler import ControlPageStateDeps, build_control_page_state_from_runtime


@dataclass(frozen=True)
class ControlPageStateCompositionDeps:
    control_page: Callable[[], Any]
    get_runtime_services: Callable[[], Awaitable[dict[str, Any]]]
    is_control_api_ready: Callable[[dict[str, Any]], bool]
    build_runtime_health: Callable[..., dict[str, Any]]
    discord_enabled: bool
    local_only_mode: bool
    local_control_guild_id: int
    local_control_guild_name: str
    build_commands: Callable[..., Any]
    build_all_commands: Callable[..., Any]
    local_tts_manager: Any
    serialize_local_mic_state: Callable[[], dict[str, Any]]
    read_vision_watch_state: Callable[[], dict[str, Any]]
    local_url: Callable[[], str]
    build_voice_pipeline_snapshot: Callable[..., dict[str, Any]]
    main_model: str
    router_model: str
    summary_model: str
    stt_model: str
    inflight_llm_requests: Callable[[], int]
    tracked_tts_count: Callable[[], int]
    summarize_model_call_metrics: Callable[[], dict[str, Any]]
    summarize_question_metrics: Callable[[], dict[str, Any]]
    ensure_minecraft_snapshot: Callable[..., Awaitable[Any]]
    minecraft_snapshot_cache: Any
    is_tts_active: Callable[[int], bool]
    current_tts_target_name: Callable[..., str]
    serialize_local_mic_target: Callable[..., Any]
    resolve_local_mic_target: Callable[..., Any]
    guilds: Callable[[], list[Any]]
    local_mic_discord_user_ids: set[int]
    voice_debug_audio: bool


class ControlPageStateComposition:
    """Builds the Control Page state from live runtime-owned objects."""

    def __init__(self, deps: ControlPageStateCompositionDeps) -> None:
        self.deps = deps

    async def build_control_page_state(self, guild: Any | None) -> dict[str, Any]:
        deps = self.deps
        control_page = deps.control_page()
        return await build_control_page_state_from_runtime(
            guild,
            ControlPageStateDeps(
                get_runtime_services=deps.get_runtime_services,
                is_control_api_ready=deps.is_control_api_ready,
                build_runtime_health=deps.build_runtime_health,
                discord_enabled=deps.discord_enabled,
                local_only_mode=deps.local_only_mode,
                local_control_guild_id=deps.local_control_guild_id,
                local_control_guild_name=deps.local_control_guild_name,
                ensure_welcome_message=control_page.ensure_welcome_message,
                build_commands=deps.build_commands,
                build_all_commands=deps.build_all_commands,
                build_boot_progress=control_page.build_boot_progress,
                local_tts_snapshot=deps.local_tts_manager.snapshot,
                serialize_local_mic_state=deps.serialize_local_mic_state,
                read_vision_watch_state=deps.read_vision_watch_state,
                build_panel_state=control_page.build_panel_state,
                local_url=deps.local_url,
                get_chat_log=control_page.get_chat_log,
                build_voice_pipeline_snapshot=deps.build_voice_pipeline_snapshot,
                main_model=deps.main_model,
                router_model=deps.router_model,
                summary_model=deps.summary_model,
                stt_model=deps.stt_model,
                inflight_llm_requests=deps.inflight_llm_requests(),
                tracked_tts_count=deps.tracked_tts_count,
                local_tts_enabled=lambda: deps.local_tts_manager.enabled,
                summarize_model_call_metrics=deps.summarize_model_call_metrics,
                summarize_question_metrics=deps.summarize_question_metrics,
                build_local_status_text=control_page.build_local_status_text,
                ensure_minecraft_snapshot=deps.ensure_minecraft_snapshot,
                minecraft_snapshot_has_value=deps.minecraft_snapshot_cache.has_snapshot,
                minecraft_snapshot_copy=control_page.get_minecraft_snapshot_cache_copy,
                is_tts_active=deps.is_tts_active,
                current_tts_target_name=deps.current_tts_target_name,
                serialize_local_mic_target=deps.serialize_local_mic_target,
                resolve_local_mic_target=deps.resolve_local_mic_target,
                guilds=deps.guilds(),
                local_mic_discord_user_ids=deps.local_mic_discord_user_ids,
                voice_debug_audio=deps.voice_debug_audio,
                build_status_text=control_page.build_status_text,
            ),
        )


__all__ = ["ControlPageStateComposition", "ControlPageStateCompositionDeps"]
