from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, MutableMapping

from .autonomy import AutonomyEngine
from .autonomy_runtime_factory import (
    AutonomyRuntimeFactoryDeps,
    get_or_create_autonomy_engine_from_runtime,
)


@dataclass(frozen=True)
class AutonomyRuntimeCompositionDeps:
    autonomy_engines: MutableMapping[int, AutonomyEngine]
    get_guild: Callable[[int], Any]
    get_observe_channel_ids: Callable[..., Any]
    get_command_only_channel_ids: Callable[..., Any]
    session_followup_targets: MutableMapping[str, Any]
    clean_text: Callable[[str], str]
    send_discord_text: Callable[..., Any]
    question_cooldown_hit: Callable[..., Any]
    evaluate_proactive_question_gate: Callable[..., Any]
    proactive_question_scope_candidates: Callable[..., Any]
    select_question_to_ask: Callable[..., Any]
    runtime_session_key: Callable[..., str]
    get_conversation_history: Callable[..., Any]
    memory_index_dir: Path
    pick_recent_user_text: Callable[..., Any]
    localtime: Callable[..., Any]
    monotonic: Callable[[], float]
    autonomy_last_cognitive_refresh_at: MutableMapping[int, float]
    autonomy_cognitive_refresh_tasks: MutableMapping[int, Any]
    read_cached_cognitive_state: Callable[..., Any]
    read_vision_watch_state: Callable[..., Any]
    local_tts_snapshot: Callable[[], dict[str, Any]]
    serialize_local_mic_runtime_state: Callable[[], dict[str, Any]]
    get_active_session_count: Callable[[], int]
    get_inflight_llm_requests: Callable[[], int]
    last_autonomy_ping_at: MutableMapping[int, float]
    answer_promises_search: Callable[..., Any]
    start_new_turn: Callable[..., str]
    append_history: Callable[..., Any]
    schedule_memory_update: Callable[..., Any]
    mark_session_active: Callable[..., Any]
    build_topic_id: Callable[..., str]
    mark_self_state_assistant_output: Callable[..., Any]
    select_and_mark_proactive_question: Callable[..., Any]
    update_cognitive_state: Callable[..., Any]
    autonomy_cognitive_stale_sec: float
    autonomy_cognitive_min_interval_sec: float
    autonomy_cognitive_force_refresh_sec: float
    vision_watch_interval_sec: float
    active_conversation_text_question_sec: float
    active_conversation_text_sec: float
    autonomy_poll_interval_sec: float
    get_authorized_actions: Callable[[int], list[str]]
    authorize_action: Callable[[int, str], dict[str, Any]]
    record_action_outcome: Callable[
        [int, str, dict[str, Any]],
        dict[str, bool] | bool | None,
    ]
    commit_session_continuity: Callable[..., Any]
    log: Callable[..., Any]
    build_minecraft_executor: Callable[[int], Any] | None = None
    record_runtime_error: (
        Callable[[str, BaseException], Any] | None
    ) = None


class AutonomyRuntimeComposition:
    """Owns the live dependency root for per-guild autonomy engines."""

    def __init__(self, deps: AutonomyRuntimeCompositionDeps) -> None:
        self.deps = deps

    def build_autonomy_runtime_factory_deps(self) -> AutonomyRuntimeFactoryDeps:
        deps = self.deps
        return AutonomyRuntimeFactoryDeps(
            autonomy_engines=deps.autonomy_engines,
            get_guild=deps.get_guild,
            get_observe_channel_ids=deps.get_observe_channel_ids,
            get_command_only_channel_ids=deps.get_command_only_channel_ids,
            session_followup_targets=deps.session_followup_targets,
            clean_text=deps.clean_text,
            send_discord_text=deps.send_discord_text,
            question_cooldown_hit=deps.question_cooldown_hit,
            evaluate_proactive_question_gate=deps.evaluate_proactive_question_gate,
            proactive_question_scope_candidates=deps.proactive_question_scope_candidates,
            select_question_to_ask=deps.select_question_to_ask,
            runtime_session_key=deps.runtime_session_key,
            get_conversation_history=deps.get_conversation_history,
            memory_index_dir=deps.memory_index_dir,
            pick_recent_user_text=deps.pick_recent_user_text,
            localtime=deps.localtime,
            monotonic=deps.monotonic,
            autonomy_last_cognitive_refresh_at=deps.autonomy_last_cognitive_refresh_at,
            autonomy_cognitive_refresh_tasks=deps.autonomy_cognitive_refresh_tasks,
            read_cached_cognitive_state=deps.read_cached_cognitive_state,
            read_vision_watch_state=deps.read_vision_watch_state,
            local_tts_snapshot=deps.local_tts_snapshot,
            serialize_local_mic_runtime_state=deps.serialize_local_mic_runtime_state,
            get_active_session_count=deps.get_active_session_count,
            get_inflight_llm_requests=deps.get_inflight_llm_requests,
            last_autonomy_ping_at=deps.last_autonomy_ping_at,
            answer_promises_search=deps.answer_promises_search,
            start_new_turn=deps.start_new_turn,
            append_history=deps.append_history,
            schedule_memory_update=deps.schedule_memory_update,
            mark_session_active=deps.mark_session_active,
            build_topic_id=deps.build_topic_id,
            mark_self_state_assistant_output=deps.mark_self_state_assistant_output,
            select_and_mark_proactive_question=deps.select_and_mark_proactive_question,
            update_cognitive_state=deps.update_cognitive_state,
            autonomy_cognitive_stale_sec=deps.autonomy_cognitive_stale_sec,
            autonomy_cognitive_min_interval_sec=deps.autonomy_cognitive_min_interval_sec,
            autonomy_cognitive_force_refresh_sec=deps.autonomy_cognitive_force_refresh_sec,
            vision_watch_interval_sec=deps.vision_watch_interval_sec,
            active_conversation_text_question_sec=deps.active_conversation_text_question_sec,
            active_conversation_text_sec=deps.active_conversation_text_sec,
            autonomy_poll_interval_sec=deps.autonomy_poll_interval_sec,
            get_authorized_actions=deps.get_authorized_actions,
            authorize_action=deps.authorize_action,
            record_action_outcome=deps.record_action_outcome,
            commit_session_continuity=deps.commit_session_continuity,
            log=deps.log,
            build_minecraft_executor=deps.build_minecraft_executor,
            record_runtime_error=deps.record_runtime_error,
        )

    def get_or_create_autonomy_engine(self, guild_id: int) -> AutonomyEngine:
        return get_or_create_autonomy_engine_from_runtime(
            guild_id,
            deps=self.build_autonomy_runtime_factory_deps(),
        )


@dataclass(frozen=True)
class MinecraftAutonomyRouteCompositionDeps:
    create_engine: Callable[[int], Any]
    get_router: Callable[[int], Any]


class MinecraftAutonomyRouteComposition:
    """Owns the explicit Discord-to-Minecraft autonomy route switch."""

    def __init__(self, deps: MinecraftAutonomyRouteCompositionDeps) -> None:
        self.deps = deps

    async def enable(self, guild_id: int) -> bool:
        normalized_guild_id = int(guild_id)
        self.deps.create_engine(normalized_guild_id)
        router = self.deps.get_router(normalized_guild_id)
        if router is None:
            return False
        return bool(await router.enable_domain("minecraft"))

    async def disable(self, guild_id: int) -> bool:
        router = self.deps.get_router(int(guild_id))
        if router is None:
            return False
        return bool(await router.disable_domain("minecraft"))

    def is_enabled(self, guild_id: int) -> bool:
        router = self.deps.get_router(int(guild_id))
        return bool(
            router is not None
            and router.is_domain_enabled("minecraft")
        )


__all__ = [
    "AutonomyRuntimeComposition",
    "AutonomyRuntimeCompositionDeps",
    "MinecraftAutonomyRouteComposition",
    "MinecraftAutonomyRouteCompositionDeps",
]
