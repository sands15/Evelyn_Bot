from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class ControlPageStatusRuntimeDeps:
    model_name: str
    router_model_name: str
    summary_model_name: str
    stt_model_name: str
    discord_enabled: bool
    bot_api_host: str
    bot_api_port: int
    control_page_local_url: Callable[[], str]
    voice_input_mode_status_line: Callable[[], str]
    local_mic_status_line: Callable[[], str]
    current_tts_target_name: Callable[[Any], str]
    is_tracked_tts_playback_active: Callable[[int], bool]
    local_tts_snapshot: Callable[[], dict[str, Any]]
    local_mic_runtime_state: Callable[[], dict[str, Any]]
    build_voice_pipeline_snapshot: Callable[[Any], dict[str, Any]]
    format_voice_continuity_detail_lines: Callable[[dict[str, Any]], list[str]]
    build_status_text_payload: Callable[..., str]
    build_local_status_text_payload: Callable[..., str]
    build_voice_status_reply_payload: Callable[..., str]
    build_voice_continuity_reply_payload: Callable[..., str]
    get_control_page_minecraft_snapshot: Callable[[int | None], Awaitable[dict[str, Any]]]
    get_minecraft_world_lease_status: Callable[
        [],
        dict[str, Any],
    ]
    build_control_page_inventory_reply_payload: Callable[[dict[str, Any]], str]
    build_control_page_minecraft_reply_payload: Callable[[dict[str, Any]], str]
    get_autonomy_engine: Callable[[int], Any | None]
    get_routed_autonomy_executor: Callable[[int], Any | None]
    build_control_page_autonomy_reply_payload: Callable[..., str]


def build_control_page_status_text_from_runtime(
    guild: Any,
    minecraft: dict[str, Any],
    *,
    deps: ControlPageStatusRuntimeDeps,
) -> str:
    vc = guild.voice_client
    voice_channel_name = getattr(getattr(vc, "channel", None), "name", None) or "없음"
    listening = bool(vc and hasattr(vc, "is_listening") and vc.is_listening())
    speaking = deps.is_tracked_tts_playback_active(guild.id)
    tts_target = deps.current_tts_target_name(guild) if speaking else "없음"
    return deps.build_status_text_payload(
        guild_name=guild.name,
        voice_channel_name=voice_channel_name,
        listening=listening,
        speaking=speaking,
        tts_target=tts_target,
        voice_input_mode=deps.voice_input_mode_status_line(),
        local_mic_status=deps.local_mic_status_line(),
        main_model=deps.model_name,
        router_model=deps.router_model_name,
        summary_model=deps.summary_model_name,
        stt_model=deps.stt_model_name,
        minecraft=minecraft,
    )


async def build_control_page_status_reply_from_runtime(
    guild: Any,
    *,
    deps: ControlPageStatusRuntimeDeps,
) -> str:
    minecraft = dict(
        await deps.get_control_page_minecraft_snapshot(guild.id)
    )
    minecraft["world_lease"] = (
        deps.get_minecraft_world_lease_status()
    )
    return build_control_page_status_text_from_runtime(guild, minecraft, deps=deps)


def build_control_page_local_status_text_from_runtime(
    runtime_services: dict[str, Any] | None,
    *,
    deps: ControlPageStatusRuntimeDeps,
) -> str:
    local_tts = deps.local_tts_snapshot()
    local_mic = deps.local_mic_runtime_state()
    return deps.build_local_status_text_payload(
        runtime_services,
        discord_enabled=deps.discord_enabled,
        local_url=deps.control_page_local_url(),
        bot_api_host=deps.bot_api_host,
        bot_api_port=deps.bot_api_port,
        main_model=deps.model_name,
        router_model=deps.router_model_name,
        summary_model=deps.summary_model_name,
        stt_model=deps.stt_model_name,
        local_speaking=bool(local_tts.get("active")),
        local_listening=bool(local_mic.get("enabled") and local_mic.get("captureReady")),
        local_mic_status=deps.local_mic_status_line(),
    )


def build_control_page_voice_status_reply_from_runtime(
    guild: Any | None,
    *,
    deps: ControlPageStatusRuntimeDeps,
) -> str:
    vc = guild.voice_client if guild is not None else None
    voice = deps.build_voice_pipeline_snapshot(guild)
    channel_name = getattr(getattr(vc, "channel", None), "name", None) or "none"
    continuity = voice.get("bargeInContinuity") if isinstance(voice.get("bargeInContinuity"), dict) else {}
    return deps.build_voice_status_reply_payload(
        voice,
        channel_name=channel_name,
        voice_input_mode=deps.voice_input_mode_status_line(),
        local_mic_status=deps.local_mic_status_line(),
        continuity_detail_lines=deps.format_voice_continuity_detail_lines(continuity),
    )


def build_control_page_voice_continuity_reply_from_runtime(
    continuity: dict[str, Any],
    *,
    deps: ControlPageStatusRuntimeDeps,
) -> str:
    return deps.build_voice_continuity_reply_payload(
        deps.format_voice_continuity_detail_lines(continuity)
    )


async def build_control_page_inventory_reply_from_runtime(
    guild: Any,
    *,
    deps: ControlPageStatusRuntimeDeps,
) -> str:
    minecraft = await deps.get_control_page_minecraft_snapshot(guild.id)
    return deps.build_control_page_inventory_reply_payload(minecraft)


async def build_control_page_minecraft_reply_from_runtime(
    guild: Any,
    *,
    deps: ControlPageStatusRuntimeDeps,
) -> str:
    minecraft = dict(
        await deps.get_control_page_minecraft_snapshot(guild.id)
    )
    minecraft["world_lease"] = (
        deps.get_minecraft_world_lease_status()
    )
    return deps.build_control_page_minecraft_reply_payload(minecraft)


def build_control_page_autonomy_reply_from_runtime(
    guild: Any,
    *,
    deps: ControlPageStatusRuntimeDeps,
) -> str:
    engine = deps.get_autonomy_engine(guild.id)
    if engine is None:
        return "자율 행동 엔진이 아직 만들어지지 않았어."

    state = getattr(engine, "state", None)
    router = deps.get_routed_autonomy_executor(guild.id)
    allowed_actions = getattr(state, "allowed_actions", []) if state is not None else []
    current_goal = getattr(state, "current_goal", None) if state is not None else None
    current_plan = getattr(state, "current_plan", None) if state is not None else None
    goal = getattr(current_goal, "summary", "없음") if current_goal is not None else "없음"
    plan = getattr(current_plan, "summary", "없음") if current_plan is not None else "없음"
    return deps.build_control_page_autonomy_reply_payload(
        status=str(getattr(state, "status", "")),
        safety_mode=str(getattr(state, "safety_mode", "")),
        goal=str(goal) if goal else "없음",
        plan=str(plan) if plan else "없음",
        drive=getattr(state, "drive_state", None) if state is not None else None,
        failure_count=int(getattr(state, "failure_count", 0) or 0),
        last_error=getattr(state, "last_error", None),
        minecraft_enabled=bool(router and router.is_domain_enabled("minecraft")),
        allowed_actions=list(allowed_actions or []),
    )


__all__ = [
    "ControlPageStatusRuntimeDeps",
    "build_control_page_status_reply_from_runtime",
    "build_control_page_local_status_text_from_runtime",
    "build_control_page_status_text_from_runtime",
    "build_control_page_inventory_reply_from_runtime",
    "build_control_page_minecraft_reply_from_runtime",
    "build_control_page_autonomy_reply_from_runtime",
    "build_control_page_voice_continuity_reply_from_runtime",
    "build_control_page_voice_status_reply_from_runtime",
]
