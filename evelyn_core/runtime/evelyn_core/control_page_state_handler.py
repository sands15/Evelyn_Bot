from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .control_page_state import (
    build_control_page_guild_state_view,
    build_control_page_local_state_view,
    build_control_page_runtime_diagnostics,
    is_control_page_minecraft_session_active,
)
from .minecraft_runtime_snapshot import minecraft_runtime_status_fields


@dataclass(frozen=True)
class ControlPageStateDeps:
    get_runtime_services: Any
    is_control_api_ready: Any
    build_runtime_health: Any
    discord_enabled: bool
    local_only_mode: bool
    local_control_guild_id: int
    local_control_guild_name: str
    ensure_welcome_message: Any
    build_commands: Any
    build_all_commands: Any
    build_boot_progress: Any
    local_tts_snapshot: Any
    serialize_local_mic_state: Any
    read_vision_watch_state: Any
    build_panel_state: Any
    local_url: Any
    get_chat_log: Any
    build_voice_pipeline_snapshot: Any
    main_model: str
    router_model: str
    summary_model: str
    stt_model: str
    inflight_llm_requests: int
    tracked_tts_count: Any
    local_tts_enabled: Any
    summarize_model_call_metrics: Any
    summarize_question_metrics: Any
    build_local_status_text: Any
    ensure_minecraft_snapshot: Any
    minecraft_snapshot_has_value: Any
    minecraft_snapshot_copy: Any
    is_tts_active: Any
    current_tts_target_name: Any
    serialize_local_mic_target: Any
    resolve_local_mic_target: Any
    guilds: Any
    local_mic_discord_user_ids: Any
    voice_debug_audio: bool
    build_status_text: Any


async def build_control_page_state_from_runtime(guild: Any, deps: ControlPageStateDeps) -> dict[str, Any]:
    runtime_services = await deps.get_runtime_services()
    runtime_diagnostics = build_control_page_runtime_diagnostics(
        runtime_services,
        control_api_ready=deps.is_control_api_ready(runtime_services),
    )
    runtime_health = deps.build_runtime_health(services=runtime_services)
    if guild is None:
        local_mode = bool(not deps.discord_enabled)
        await deps.ensure_welcome_message(None, runtime_services=runtime_services)
        commands = deps.build_commands(minecraft_session_active=False)
        boot_progress = deps.build_boot_progress(runtime_services, guild_available=local_mode)
        local_tts = deps.local_tts_snapshot()
        local_mic = deps.serialize_local_mic_state()
        local_listening = bool(local_mic.get("enabled") and local_mic.get("captureReady"))
        return build_control_page_local_state_view(
            generated_at=time.time(),
            local_url=deps.local_url(),
            local_mode=local_mode,
            local_guild_id=deps.local_control_guild_id,
            local_guild_name=deps.local_control_guild_name,
            commands=commands,
            all_commands=deps.build_all_commands(),
            chat_messages=deps.get_chat_log(deps.local_control_guild_id),
            panel_state=deps.build_panel_state(),
            runtime_services=runtime_services,
            runtime_diagnostics=runtime_diagnostics,
            runtime_health=runtime_health,
            boot_progress=boot_progress,
            local_tts=local_tts,
            local_mic=local_mic,
            local_listening=local_listening,
            voice_pipeline=deps.build_voice_pipeline_snapshot(guild),
            vision_watch=deps.read_vision_watch_state(),
            main_model=deps.main_model,
            router_model=deps.router_model,
            summary_model=deps.summary_model,
            stt_model=deps.stt_model,
            inflight_llm_requests=deps.inflight_llm_requests,
            tracked_tts_count=deps.tracked_tts_count(),
            output_mode="local_speaker" if deps.local_only_mode and deps.local_tts_enabled() else "none",
            model_call_metrics=deps.summarize_model_call_metrics(),
            question_metrics=deps.summarize_question_metrics(),
            status_text=deps.build_local_status_text(runtime_services),
        )

    await deps.ensure_welcome_message(guild, runtime_services=runtime_services)
    vc = guild.voice_client
    await deps.ensure_minecraft_snapshot(guild.id, wait=not deps.minecraft_snapshot_has_value())
    minecraft = deps.minecraft_snapshot_copy()
    speaking = deps.is_tts_active(guild.id)
    listening = bool(vc and hasattr(vc, "is_listening") and vc.is_listening())
    boot_progress = deps.build_boot_progress(
        runtime_services,
        guild_available=True,
        listening=listening,
    )
    tts_target_name = deps.current_tts_target_name(guild) if speaking else "없음"
    local_mic_target = deps.serialize_local_mic_target(
        deps.resolve_local_mic_target(guilds=deps.guilds, preferred_user_ids=deps.local_mic_discord_user_ids)
    )
    minecraft_session_active = is_control_page_minecraft_session_active(minecraft)
    return build_control_page_guild_state_view(
        generated_at=time.time(),
        local_url=deps.local_url(),
        guild_id=guild.id,
        guild_name=guild.name,
        voice_channel_name=getattr(getattr(vc, "channel", None), "name", None) or "없음",
        listening=listening,
        speaking=speaking,
        tts_target_name=tts_target_name,
        commands=deps.build_commands(minecraft_session_active=minecraft_session_active),
        all_commands=deps.build_all_commands(),
        chat_messages=deps.get_chat_log(guild.id),
        panel_state=deps.build_panel_state(),
        runtime_services=runtime_services,
        runtime_diagnostics=runtime_diagnostics,
        runtime_health=runtime_health,
        boot_progress=boot_progress,
        local_tts=deps.local_tts_snapshot(),
        local_mic=deps.serialize_local_mic_state(),
        voice_pipeline=deps.build_voice_pipeline_snapshot(guild),
        vision_watch=deps.read_vision_watch_state(),
        main_model=deps.main_model,
        router_model=deps.router_model,
        summary_model=deps.summary_model,
        stt_model=deps.stt_model,
        inflight_llm_requests=deps.inflight_llm_requests,
        tracked_tts_count=deps.tracked_tts_count(),
        output_mode="discord_voice" if deps.discord_enabled else "local_speaker",
        model_call_metrics=deps.summarize_model_call_metrics(),
        question_metrics=deps.summarize_question_metrics(),
        minecraft=minecraft,
        minecraft_session_active=minecraft_session_active,
        minecraft_status_fields=minecraft_runtime_status_fields(minecraft),
        voice_debug_audio=deps.voice_debug_audio,
        local_mic_target=local_mic_target,
        status_text=deps.build_status_text(guild, minecraft),
    )


__all__ = [
    "ControlPageStateDeps",
    "build_control_page_state_from_runtime",
]
