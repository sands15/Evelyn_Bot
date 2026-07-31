from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from .autonomy import AutonomyEngine
from .autonomy_observation_state import (
    build_autonomy_recent_context_payload,
    build_autonomy_status_payload,
    build_autonomy_summary_payload,
    build_default_autonomy_observation,
)
from .autonomy_router import DefaultAutonomyExecutor, RoutedAutonomyExecutor
from .continuity_commit_contract import (
    require_durable_continuity_receipt,
)


@dataclass(frozen=True)
class AutonomyRuntimeFactoryDeps:
    autonomy_engines: MutableMapping[int, AutonomyEngine]
    get_guild: Callable[[int], Any]
    get_observe_channel_ids: Callable[[int], list[int]]
    get_command_only_channel_ids: Callable[[int], list[int]]
    session_followup_targets: MutableMapping[str, Any]
    clean_text: Callable[[str], str]
    send_discord_text: Callable[..., Awaitable[Any]]
    question_cooldown_hit: Callable[[str], bool]
    evaluate_proactive_question_gate: Callable[..., Any]
    proactive_question_scope_candidates: Callable[..., Any]
    select_question_to_ask: Callable[..., Any]
    runtime_session_key: Callable[..., str]
    get_conversation_history: Callable[..., list[dict[str, Any]]]
    pick_recent_user_text: Callable[[list[dict[str, Any]]], str]
    localtime: Callable[[], Any]
    monotonic: Callable[[], float]
    autonomy_last_cognitive_refresh_at: MutableMapping[int, float]
    autonomy_cognitive_refresh_tasks: MutableMapping[int, asyncio.Task]
    read_cached_cognitive_state: Callable[[int], dict[str, Any]]
    read_vision_watch_state: Callable[[], dict[str, Any]]
    local_tts_snapshot: Callable[[], dict[str, Any]]
    serialize_local_mic_runtime_state: Callable[[], dict[str, Any]]
    get_active_session_count: Callable[[], int]
    get_inflight_llm_requests: Callable[[], int]
    last_autonomy_ping_at: MutableMapping[int, float]
    answer_promises_search: Callable[..., bool]
    append_history: Callable[..., Any]
    schedule_memory_update: Callable[..., Any]
    mark_session_active: Callable[..., Any]
    build_topic_id: Callable[..., str]
    mark_self_state_assistant_output: Callable[..., Any]
    select_and_mark_proactive_question: Callable[..., Any]
    update_cognitive_state: Callable[..., Awaitable[dict[str, Any]]]
    autonomy_cognitive_stale_sec: float
    autonomy_cognitive_min_interval_sec: float
    autonomy_cognitive_force_refresh_sec: float
    vision_watch_interval_sec: float
    active_conversation_text_question_sec: float
    active_conversation_text_sec: float
    autonomy_poll_interval_sec: float
    get_authorized_actions: Callable[[int], list[str]]
    authorize_action: Callable[[int, str], dict[str, Any]]
    record_action_outcome: Callable[[int, str, dict[str, Any]], None]
    commit_session_continuity: Callable[[], Awaitable[dict[str, Any]]]
    log: Callable[..., Any]


def get_or_create_autonomy_engine_from_runtime(
    guild_id: int,
    *,
    deps: AutonomyRuntimeFactoryDeps,
) -> AutonomyEngine:
    engine = deps.autonomy_engines.get(guild_id)
    if engine is not None:
        return engine

    async def find_followup_channel() -> Any | None:
        guild = deps.get_guild(guild_id)
        if guild is None:
            return None
        for channel_id in deps.get_observe_channel_ids(guild_id):
            channel = guild.get_channel(channel_id)
            if channel is not None and hasattr(channel, "send"):
                return channel
        recent_channel_ids = [
            value.get("channel_id")
            for value in deps.session_followup_targets.values()
            if isinstance(value, dict) and value.get("channel_id")
        ]
        for channel_id in reversed(recent_channel_ids):
            channel = guild.get_channel(channel_id)
            if channel is not None and hasattr(channel, "send"):
                return channel
        return None

    async def notify(text: str) -> None:
        text = deps.clean_text(text)
        if not text:
            return
        channel = await find_followup_channel()
        if channel is not None:
            await deps.send_discord_text(channel, text)

    def has_queued_proactive_question(session_key: str, latest_user_text: str) -> bool:
        if deps.question_cooldown_hit(session_key):
            return False
        gate = deps.evaluate_proactive_question_gate(
            guild_id=guild_id,
            source="autonomy",
            user_text=latest_user_text,
            answer_text="",
            awaiting_user_reply=False,
            session_scope_key=session_key,
            session_cooldown_hit=False,
        )
        if not gate.allowed:
            return False
        for scope_type, scope_key in deps.proactive_question_scope_candidates(
            session_memory_key=session_key
        ):
            if deps.select_question_to_ask(
                guild_id,
                scope_type=scope_type,
                scope_key=scope_key,
                session_scope_key=session_key,
            ):
                return True
        return False

    async def default_observe() -> dict[str, Any]:
        channel = await find_followup_channel()
        session_key = deps.runtime_session_key(guild_id=guild_id)
        history = deps.get_conversation_history(session_key=session_key, guild_id=guild_id)
        latest_user_text = deps.pick_recent_user_text(history)
        observe_channel_ids = deps.get_observe_channel_ids(guild_id)
        command_only_channel_ids = deps.get_command_only_channel_ids(guild_id)
        observed_channels: list[dict[str, Any]] = []
        guild = deps.get_guild(guild_id)
        now_local = deps.localtime()
        quiet_hours = now_local.tm_hour < 8 or now_local.tm_hour >= 23
        current_engine = deps.autonomy_engines.get(guild_id)
        last_result = (current_engine.state.last_step_result if current_engine is not None else {}) or {}
        cached_cognitive = deps.read_cached_cognitive_state(guild_id)
        last_refresh_at = float(deps.autonomy_last_cognitive_refresh_at.get(guild_id, 0.0) or 0.0)
        task = deps.autonomy_cognitive_refresh_tasks.get(guild_id)
        router_refresh_inflight = bool(task is not None and not task.done())
        if guild is not None:
            for channel_id in observe_channel_ids[:8]:
                channel_obj = guild.get_channel(channel_id)
                channel_name = getattr(channel_obj, "name", str(channel_id)) if channel_obj is not None else str(channel_id)
                observed_channels.append({"id": channel_id, "name": channel_name})
        return build_default_autonomy_observation(
            connected=channel is not None,
            known_followup_channels=len(
                [
                    value
                    for value in deps.session_followup_targets.values()
                    if isinstance(value, dict) and value.get("channel_id")
                ]
            ),
            inflight_llm_requests=deps.get_inflight_llm_requests(),
            active_sessions=deps.get_active_session_count(),
            history=history,
            last_autonomy_ping_at=float(deps.last_autonomy_ping_at.get(guild_id, 0.0) or 0.0),
            observe_channel_ids=observe_channel_ids,
            command_only_channel_ids=command_only_channel_ids,
            observed_channels=observed_channels,
            quiet_hours=quiet_hours,
            last_result=last_result,
            cached_cognitive=cached_cognitive,
            last_cognitive_refresh_at=last_refresh_at,
            router_refresh_inflight=router_refresh_inflight,
            autonomy_cognitive_stale_sec=deps.autonomy_cognitive_stale_sec,
            autonomy_cognitive_min_interval_sec=deps.autonomy_cognitive_min_interval_sec,
            autonomy_cognitive_force_refresh_sec=deps.autonomy_cognitive_force_refresh_sec,
            vision_watch=deps.read_vision_watch_state(),
            vision_watch_interval_sec=deps.vision_watch_interval_sec,
            local_tts_state=deps.local_tts_snapshot(),
            local_mic_state=deps.serialize_local_mic_runtime_state(),
            queued_proactive_question_available=bool(
                session_key and has_queued_proactive_question(session_key, latest_user_text)
            ),
            answer_promises_search_fn=deps.answer_promises_search,
        )

    async def default_send_followup(
        text: str,
        *,
        awaiting_user_reply: bool = False,
        user_text: str = "[autonomy]",
    ) -> dict[str, Any]:
        channel = await find_followup_channel()
        if channel is None:
            return {"status": "blocked", "reason": "no_followup_channel"}
        await deps.send_discord_text(channel, text)
        session_key = deps.runtime_session_key(guild_id=guild_id)
        deps.append_history(session_key, user_text or "[autonomy]", text, guild_id=guild_id)
        deps.schedule_memory_update(
            guild_id,
            user_text or "[autonomy]",
            text,
            source="autonomy",
            assistant_speaker="Evelyn-Autonomy",
            session_key=session_key,
            runtime_mode="batch",
        )
        deps.mark_session_active(
            session_key,
            ttl_sec=(
                deps.active_conversation_text_question_sec
                if awaiting_user_reply
                else deps.active_conversation_text_sec
            ),
            speaker="assistant",
            awaiting_user_reply=awaiting_user_reply,
            topic_id=deps.build_topic_id("autonomy", text),
            answer_text=text,
            user_text=user_text or "[autonomy]",
        )
        continuity_durable = False
        continuity_generation = 0
        try:
            continuity_status = await deps.commit_session_continuity()
            continuity_receipt = (
                require_durable_continuity_receipt(
                    continuity_status
                )
            )
            continuity_durable = True
            continuity_generation = int(
                continuity_receipt["generation"]
            )
        except Exception as exc:
            deps.log(
                "[AUTONOMY] followup_continuity_commit_failed "
                f"guild={guild_id} errorType={type(exc).__name__}"
            )
        deps.last_autonomy_ping_at[guild_id] = deps.monotonic()
        deps.mark_self_state_assistant_output(proactive=True)
        return {
            "status": "ok",
            "reason": "sent_followup",
            "text": text,
            "verified": True,
            "evidence_code": "discord_send_completed",
            "continuityDurable": continuity_durable,
            "continuityGeneration": continuity_generation,
        }

    async def default_summarize() -> dict[str, Any]:
        history = deps.get_conversation_history(
            session_key=deps.runtime_session_key(guild_id=guild_id),
            guild_id=guild_id,
        )
        result = build_autonomy_summary_payload(
            history,
            active_sessions=deps.get_active_session_count(),
            inflight_llm_requests=deps.get_inflight_llm_requests(),
        )
        result["verified"] = True
        result["evidence_code"] = "summary_payload_built"
        return result

    async def default_check_status() -> dict[str, Any]:
        channel = await find_followup_channel()
        result = build_autonomy_status_payload(
            connected=channel is not None,
            active_sessions=deps.get_active_session_count(),
            inflight_llm_requests=deps.get_inflight_llm_requests(),
            known_followup_channels=len(
                [
                    value
                    for value in deps.session_followup_targets.values()
                    if isinstance(value, dict) and value.get("channel_id")
                ]
            ),
        )
        result["verified"] = True
        result["evidence_code"] = "status_snapshot_built"
        return result

    async def default_summarize_recent_context() -> dict[str, Any]:
        history = deps.get_conversation_history(
            session_key=deps.runtime_session_key(guild_id=guild_id),
            guild_id=guild_id,
        )
        result = build_autonomy_recent_context_payload(history)
        result["verified"] = True
        result["evidence_code"] = "recent_context_payload_built"
        return result

    async def default_maybe_ping_user(text: str) -> dict[str, Any]:
        last_ping_at = float(deps.last_autonomy_ping_at.get(guild_id, 0.0) or 0.0)
        if last_ping_at > 0 and (deps.monotonic() - last_ping_at) < 900:
            return {"status": "blocked", "reason": "ping_cooldown"}
        session_key = deps.runtime_session_key(guild_id=guild_id)
        history = deps.get_conversation_history(session_key=session_key, guild_id=guild_id)
        latest_user_text = deps.pick_recent_user_text(history)
        marked = deps.select_and_mark_proactive_question(
            guild_id=guild_id,
            source="autonomy",
            user_text=latest_user_text,
            answer_text="",
            awaiting_user_reply=False,
            session_key=session_key,
            session_memory_key=session_key,
        )
        if not marked:
            return {
                "status": "ok",
                "reason": "no_queued_proactive_question",
                "skipped": True,
                "verified": True,
                "evidence_code": "proactive_gate_completed",
            }
        return await default_send_followup(
            marked["ask_text"],
            awaiting_user_reply=True,
            user_text=latest_user_text or "[autonomy]",
        )

    async def default_refresh_cognitive_state() -> dict[str, Any]:
        existing = deps.autonomy_cognitive_refresh_tasks.get(guild_id)
        if existing is not None and not existing.done():
            return {"status": "blocked", "reason": "router_refresh_inflight"}
        session_key = deps.runtime_session_key(guild_id=guild_id)
        history = deps.get_conversation_history(session_key=session_key, guild_id=guild_id)
        latest_user_text = deps.pick_recent_user_text(history)
        if not latest_user_text:
            return {"status": "blocked", "reason": "no_recent_user_text"}

        async def run_refresh() -> dict[str, Any]:
            started_mono = deps.monotonic()
            deps.autonomy_last_cognitive_refresh_at[guild_id] = started_mono
            state = await deps.update_cognitive_state(
                guild_id,
                latest_user_text,
                session_key=session_key,
                source="text",
                turn_scope=None,
            )
            elapsed_ms = round((deps.monotonic() - started_mono) * 1000.0, 1)
            return {
                "status": "ok",
                "reason": "router_refreshed",
                "updated_at": state.get("updated_at"),
                "action": state.get("action"),
                "confidence": state.get("confidence"),
                "elapsed_ms": elapsed_ms,
                "text": latest_user_text[:120],
                "verified": True,
                "evidence_code": "cognitive_state_updated",
            }

        task = asyncio.create_task(run_refresh())
        deps.autonomy_cognitive_refresh_tasks[guild_id] = task
        try:
            return await task
        finally:
            if deps.autonomy_cognitive_refresh_tasks.get(guild_id) is task:
                deps.autonomy_cognitive_refresh_tasks.pop(guild_id, None)

    engine = AutonomyEngine(
        guild_id=guild_id,
        executor=RoutedAutonomyExecutor(
            default_executor=DefaultAutonomyExecutor(
                observe_fn=default_observe,
                send_followup_fn=default_send_followup,
                summarize_fn=default_summarize,
                check_status_fn=default_check_status,
                summarize_recent_context_fn=default_summarize_recent_context,
                maybe_ping_user_fn=default_maybe_ping_user,
                refresh_cognitive_state_fn=default_refresh_cognitive_state,
            ),
            executors={},
        ),
        notify=notify,
        poll_interval_sec=deps.autonomy_poll_interval_sec,
        get_authorized_actions=deps.get_authorized_actions,
        authorize_action=deps.authorize_action,
        record_action_outcome=deps.record_action_outcome,
    )
    deps.autonomy_engines[guild_id] = engine
    return engine
