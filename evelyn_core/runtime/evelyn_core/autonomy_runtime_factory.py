from __future__ import annotations

import asyncio
import inspect
import math
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, MutableMapping

from .autonomy import AutonomyEngine, AutonomyExecutionContext
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
from .discord_ingress import (
    DiscordTextIngressContext,
    build_text_ingress_context,
)
from .discord_delivery import is_definitive_discord_send_failure
from .conversation_memory_exposure import (
    capture_combined_memory_exposure,
    filter_conversation_history_for_memory_exposure,
    memory_receipt_ref_from_exposure,
)
from .memory_exposure import (
    current_memory_exposure_position,
    memory_exposure_guard,
)
from .memory_deletion_journal import (
    MemoryDeletionJournalIntegrityError,
)


@dataclass(frozen=True)
class AutonomyRuntimeFactoryDeps:
    autonomy_engines: MutableMapping[int, AutonomyEngine]
    get_guild: Callable[[int], Any]
    get_observe_channel_ids: Callable[[int], list[int]]
    get_command_only_channel_ids: Callable[[int], list[int]]
    session_followup_targets: MutableMapping[str, Any]
    session_last_active_at: MutableMapping[str, float]
    is_session_active_for_user: Callable[[str, int], bool]
    session_locks: MutableMapping[str, asyncio.Lock]
    reply_slot_locks: MutableMapping[str, asyncio.Lock]
    reply_slot_admission_locks: MutableMapping[str, asyncio.Lock]
    clean_text: Callable[[str], str]
    send_discord_text: Callable[..., Awaitable[Any]]
    question_cooldown_hit: Callable[[str], bool]
    evaluate_proactive_question_gate: Callable[..., Any]
    proactive_question_scope_candidates: Callable[..., Any]
    select_question_to_ask: Callable[..., Any]
    get_conversation_history: Callable[..., list[dict[str, Any]]]
    memory_index_dir: Path
    pick_recent_user_text: Callable[[list[dict[str, Any]]], str]
    localtime: Callable[[], Any]
    monotonic: Callable[[], float]
    autonomy_last_cognitive_refresh_at: MutableMapping[int, float]
    autonomy_cognitive_refresh_tasks: MutableMapping[int, asyncio.Task]
    read_cached_cognitive_state: Callable[..., dict[str, Any] | None]
    read_vision_watch_state: Callable[[], dict[str, Any]]
    local_tts_snapshot: Callable[[], dict[str, Any]]
    serialize_local_mic_runtime_state: Callable[[], dict[str, Any]]
    get_active_session_count: Callable[[], int]
    get_inflight_llm_requests: Callable[[], int]
    last_autonomy_ping_at: MutableMapping[int, float]
    answer_promises_search: Callable[..., bool]
    start_new_turn: Callable[..., str]
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
    record_action_outcome: Callable[
        [int, str, dict[str, Any]],
        dict[str, bool] | bool | None,
    ]
    commit_session_continuity: Callable[..., Awaitable[dict[str, Any]]]
    log: Callable[..., Any]
    build_minecraft_executor: Callable[[int], Any] | None = None
    record_runtime_error: (
        Callable[[str, BaseException], Any] | None
    ) = None
    conversation_ingress: Any | None = None


_TEXT_FOLLOWUP_SESSION = re.compile(
    r"^guild:(?P<guild>\d+):text:(?P<channel>\d+)"
    r"(?::thread:(?P<thread>\d+))?:user:(?P<user>\d+)$"
)


async def _drain_child_under_cancellation(
    task: asyncio.Task,
) -> tuple[Any, bool]:
    cancellation_seen = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_seen = True
    return task.result(), cancellation_seen


def _text_followup_ingress(
    session_key: object,
    target: object,
    *,
    guild_id: int,
) -> DiscordTextIngressContext | None:
    if not isinstance(session_key, str) or not isinstance(target, dict):
        return None
    match = _TEXT_FOLLOWUP_SESSION.fullmatch(session_key)
    if match is None:
        return None
    parsed = {
        name: int(value) if value is not None else None
        for name, value in match.groupdict().items()
    }
    if (
        parsed["guild"] != guild_id
        or any(
            value is not None and value <= 0
            for value in parsed.values()
        )
    ):
        return None
    target_channel_id = target.get("channel_id")
    if (
        type(target_channel_id) is not int
        or target_channel_id != parsed["channel"]
    ):
        return None
    ingress = build_text_ingress_context(
        guild_id=parsed["guild"],
        channel_id=parsed["channel"],
        user_id=parsed["user"],
        thread_id=parsed["thread"],
        message_id=(
            target.get("message_id")
            if type(target.get("message_id")) is int
            else None
        ),
    )
    return ingress if ingress.session_key == session_key else None


def get_or_create_autonomy_engine_from_runtime(
    guild_id: int,
    *,
    deps: AutonomyRuntimeFactoryDeps,
) -> AutonomyEngine:
    engine = deps.autonomy_engines.get(guild_id)
    if engine is not None:
        return engine

    target_unset = object()
    observed_target_bound = False
    observed_target: (
        tuple[str, DiscordTextIngressContext, Any, float] | None
    ) = None

    def guild_channel(guild: Any, channel_id: int) -> Any | None:
        channel = guild.get_channel(channel_id)
        if channel is None:
            get_thread = getattr(guild, "get_thread", None)
            if callable(get_thread):
                channel = get_thread(channel_id)
        return channel

    def followup_targets() -> list[
        tuple[str, DiscordTextIngressContext, Any, float]
    ]:
        guild = deps.get_guild(guild_id)
        if guild is None:
            return []
        configured_ids: list[int] = []
        for raw_channel_id in (
            deps.get_observe_channel_ids(guild_id)
        ):
            if type(raw_channel_id) is not int or raw_channel_id <= 0:
                continue
            configured_ids.append(raw_channel_id)

        candidates: list[
            tuple[int, float, str, DiscordTextIngressContext, Any]
        ] = []
        for session_key, stored_target in list(
            deps.session_followup_targets.items()
        ):
            ingress = _text_followup_ingress(
                session_key,
                stored_target,
                guild_id=guild_id,
            )
            if ingress is None:
                continue
            try:
                last_active = float(
                    deps.session_last_active_at.get(session_key, 0.0)
                )
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(last_active) or last_active <= 0.0:
                continue
            try:
                active = deps.is_session_active_for_user(
                    session_key,
                    ingress.user_id,
                )
            except Exception:
                continue
            if not active:
                continue
            channel = guild_channel(guild, ingress.channel_id)
            if channel is None or not hasattr(channel, "send"):
                continue
            policy_rank = 0
            if configured_ids:
                parent_id = getattr(
                    getattr(channel, "parent", None),
                    "id",
                    None,
                )
                matching_ranks = [
                    index
                    for index, configured_channel_id in enumerate(
                        configured_ids
                    )
                    if configured_channel_id
                    in {ingress.channel_id, parent_id}
                ]
                if not matching_ranks:
                    continue
                policy_rank = min(matching_ranks)
            candidates.append(
                (
                    policy_rank,
                    -last_active,
                    session_key,
                    ingress,
                    channel,
                )
            )
        candidates.sort(key=lambda item: (item[1], item[0], item[2]))
        return [
            (session_key, ingress, channel, -negative_last_active)
            for (
                _policy_rank,
                negative_last_active,
                session_key,
                ingress,
                channel,
            ) in candidates
        ]

    def select_followup_target(
        preferred_session_key: object = target_unset,
    ) -> tuple[str, DiscordTextIngressContext, Any, float] | None:
        candidates = followup_targets()
        if preferred_session_key is target_unset:
            return candidates[0] if candidates else None
        return next(
            (
                candidate
                for candidate in candidates
                if candidate[0] == preferred_session_key
            ),
            None,
        )

    def bound_followup_target(
    ) -> tuple[str, DiscordTextIngressContext, Any, float] | None:
        nonlocal observed_target_bound, observed_target
        if observed_target_bound:
            return observed_target
        observed_target = select_followup_target()
        observed_target_bound = True
        return observed_target

    def followup_target_is_current(
        target: tuple[str, DiscordTextIngressContext, Any, float],
    ) -> bool:
        session_key, ingress, channel, last_active = target
        current = select_followup_target(session_key)
        return bool(
            current is not None
            and current[1].message_id == ingress.message_id
            and current[2] is channel
            and current[3] == last_active
        )

    async def claim_followup_reply_slot(
        target: tuple[str, DiscordTextIngressContext, Any, float],
    ) -> tuple[asyncio.Lock | None, str]:
        ingress = target[1]
        reply_lock = deps.reply_slot_locks.setdefault(
            ingress.reply_slot_key,
            asyncio.Lock(),
        )
        admission_lock = deps.reply_slot_admission_locks.setdefault(
            ingress.reply_slot_key,
            asyncio.Lock(),
        )
        async with admission_lock:
            if reply_lock.locked():
                return None, "followup_reply_slot_busy"
            await reply_lock.acquire()
        try:
            target_is_current = followup_target_is_current(target)
        except BaseException:
            reply_lock.release()
            raise
        if not target_is_current:
            reply_lock.release()
            return None, "no_followup_channel"
        return reply_lock, ""

    async def find_followup_channel() -> Any | None:
        guild = deps.get_guild(guild_id)
        if guild is None:
            return None
        configured_ids = [
            channel_id
            for channel_id in deps.get_observe_channel_ids(guild_id)
            if type(channel_id) is int and channel_id > 0
        ]
        for channel_id in configured_ids:
            channel = guild_channel(guild, channel_id)
            if channel is not None and hasattr(channel, "send"):
                return channel
        if configured_ids:
            return None
        target = select_followup_target()
        return target[2] if target is not None else None

    def current_action_authorization(
        action_key: str,
        action_run_id: str,
        *,
        expected_grant_id: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        if not action_key or not action_run_id:
            return None, "authorization_required"
        try:
            try:
                inspect.signature(deps.authorize_action).bind(
                    guild_id,
                    action_key,
                    action_run_id=action_run_id,
                )
            except (TypeError, ValueError):
                decision = deps.authorize_action(
                    guild_id,
                    action_key,
                )
            else:
                decision = deps.authorize_action(
                    guild_id,
                    action_key,
                    action_run_id=action_run_id,
                )
        except Exception:
            return None, "authorization_audit_unavailable"
        if not isinstance(decision, dict):
            return None, "authorization_audit_unavailable"
        if decision.get("allowed") is not True:
            code = str(decision.get("code") or "")
            if code not in {
                "authorization_action_unsupported",
                "authorization_audit_unavailable",
                "authorization_required",
                "authorization_scope_denied",
            }:
                code = "authorization_required"
            return None, code
        grant_id = str(decision.get("grantId") or "")
        if expected_grant_id is not None and (
            not expected_grant_id or grant_id != expected_grant_id
        ):
            return None, "authorization_changed_during_action"
        returned_run_id = str(decision.get("actionRunId") or "")
        if returned_run_id and returned_run_id != action_run_id:
            return None, "authorization_changed_during_action"
        return decision, ""

    def blocked_authorization_result(reason: str) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason": reason,
            "verified": False,
        }

    async def notify(
        text: str,
        *,
        action_run_id: str = "",
    ) -> None:
        text = deps.clean_text(text)
        if not text:
            return
        resolved_action_run_id = (
            action_run_id or secrets.token_hex(12)
        )
        target = bound_followup_target()
        if target is None:
            return
        reply_lock, _reason = await claim_followup_reply_slot(target)
        if reply_lock is None:
            return
        try:
            authorization, authorization_error = (
                current_action_authorization(
                    "assistant:send_followup",
                    resolved_action_run_id,
                    expected_grant_id=None,
                )
            )
        except BaseException:
            reply_lock.release()
            raise
        if authorization_error or authorization is None:
            reply_lock.release()
            return
        result = await send_claimed_followup(
            target,
            reply_lock,
            text,
            user_text="[autonomy:error]",
            delivery_kind="notify",
            action_run_id=resolved_action_run_id,
            authorization_action_key="assistant:send_followup",
            expected_authorization_grant_id=str(
                authorization.get("grantId") or ""
            ),
        )
        audit_result = {
            **result,
            "_authorization_grant_id": str(
                authorization.get("grantId") or ""
            ),
            "_action_run_id": resolved_action_run_id,
        }
        recorded = deps.record_action_outcome(
            guild_id,
            "assistant:send_followup",
            audit_result,
        )
        if not (
            recorded is True
            or (
                isinstance(recorded, dict)
                and recorded.get("recorded") is True
            )
        ):
            raise RuntimeError("authorization_audit_unavailable")
        if isinstance(recorded, dict):
            if recorded.get("verified") is not True:
                raise RuntimeError("outcome_unverified")
            if (
                authorization.get("grantId")
                and recorded.get("authorizationCurrent") is not True
            ):
                raise RuntimeError(
                    "authorization_changed_during_action"
                )
        if result.get("_post_effect_integrity_failure") is True:
            raise MemoryDeletionJournalIntegrityError()
        if result.get("_post_effect_cancellation") is True:
            raise asyncio.CancelledError()

    def has_queued_proactive_question(
        target: tuple[str, DiscordTextIngressContext, Any, float],
        latest_user_text: str,
    ) -> bool:
        session_key, ingress, _channel, _last_active = target
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
            session_memory_key=ingress.session_memory_key
        ):
            if deps.select_question_to_ask(
                guild_id,
                scope_type=scope_type,
                scope_key=scope_key,
                session_scope_key=session_key,
            ):
                return True
        return False

    @contextmanager
    def memory_safe_history(
        session_key: str | None,
    ) -> Iterator[list[dict[str, Any]]]:
        if session_key is None:
            yield []
            return
        outcome = filter_conversation_history_for_memory_exposure(
            deps.get_conversation_history(
                session_key=session_key,
                guild_id=guild_id,
            ),
            memory_index_dir=deps.memory_index_dir,
        )
        exposure = capture_combined_memory_exposure(
            current_memory_exposure_position(),
            outcome.memory_exposure_position,
        )
        with memory_exposure_guard(
            expected_position=exposure,
            required=exposure is not None,
            index_dir=deps.memory_index_dir,
        ):
            yield list(outcome.messages)

    async def default_observe() -> dict[str, Any]:
        nonlocal observed_target_bound, observed_target
        observed_target = select_followup_target()
        observed_target_bound = True
        target = observed_target
        channel = await find_followup_channel()
        session_key = target[0] if target is not None else None
        with memory_safe_history(session_key) as history:
            latest_user_text = deps.pick_recent_user_text(history)
            observe_channel_ids = deps.get_observe_channel_ids(guild_id)
            command_only_channel_ids = deps.get_command_only_channel_ids(guild_id)
            observed_channels: list[dict[str, Any]] = []
            guild = deps.get_guild(guild_id)
            now_local = deps.localtime()
            quiet_hours = now_local.tm_hour < 8 or now_local.tm_hour >= 23
            current_engine = deps.autonomy_engines.get(guild_id)
            last_result = (current_engine.state.last_step_result if current_engine is not None else {}) or {}
            ingress = target[1] if target is not None else None
            cached_cognitive = deps.read_cached_cognitive_state(
                guild_id,
                room_key=ingress.room_key if ingress is not None else None,
                person_key=(
                    ingress.person_key if ingress is not None else None
                ),
                session_memory_key=(
                    ingress.session_memory_key
                    if ingress is not None
                    else None
                ),
            )
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
                    {candidate[1].channel_id for candidate in followup_targets()}
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
                    target
                    and has_queued_proactive_question(
                        target,
                        latest_user_text,
                    )
                ),
                answer_promises_search_fn=deps.answer_promises_search,
            )

    async def send_claimed_followup(
        target: tuple[str, DiscordTextIngressContext, Any, float],
        reply_lock: asyncio.Lock,
        text: str,
        *,
        awaiting_user_reply: bool = False,
        user_text: str = "[autonomy]",
        delivery_kind: str,
        action_run_id: str,
        authorization_action_key: str = "",
        expected_authorization_grant_id: str | None = None,
    ) -> dict[str, Any]:
        session_key, ingress, channel, _last_active = target
        continuity_durable = False
        continuity_generation = 0
        finalize_error: Exception | None = None
        post_effect_cancellation = False
        post_effect_integrity_failure = False
        ingress_owner = deps.conversation_ingress
        expected_guild_epoch: int | None = None
        entry_id = ""
        turn_id = ""
        assistant_hash = ""
        source_delivery_id = (
            f"autonomy:{delivery_kind}:{action_run_id}"
        )
        try:
            stored_user_text = user_text or "[autonomy]"
            memory_receipt = memory_receipt_ref_from_exposure(
                current_memory_exposure_position()
            )
            topic_id = deps.build_topic_id("autonomy", text)
            if ingress_owner is not None:
                expected_guild_epoch = ingress_owner.guild_epoch(
                    guild_id
                )
                claim = ingress_owner.claim_discord_autonomy(
                    guild_id=guild_id,
                    expected_guild_epoch=expected_guild_epoch,
                    scope=session_key,
                    source_delivery_id=source_delivery_id,
                    accepted_text=stored_user_text,
                )
                if claim.get("shouldProcess") is not True:
                    phase = str(claim.get("phase") or "")
                    if phase == "completed":
                        return {
                            "status": "ok",
                            "reason": "sent_followup",
                            "verified": True,
                            "evidence_code": "discord_send_completed",
                            "continuityDurable": True,
                            "continuityGeneration": int(
                                claim.get("continuityGeneration") or 0
                            ),
                        }
                    if phase in {
                        "delivery_succeeded",
                        "terminal_committing",
                    }:
                        return {
                            "status": "ok",
                            "reason": "sent_but_continuity_pending",
                            "verified": True,
                            "evidence_code": "discord_send_completed",
                            "continuityDurable": False,
                            "continuityGeneration": 0,
                        }
                    raise RuntimeError(
                        "autonomy_delivery_not_replayable"
                    )
                if claim.get("guildEpoch") != expected_guild_epoch:
                    raise RuntimeError(
                        "conversation_ingress_epoch_not_current"
                    )
                entry_id = str(claim["entryId"])
                turn_id = str(claim["turnId"])
                binding = ingress_owner.bind_response(
                    entry_id,
                    guild_id=guild_id,
                    expected_guild_epoch=expected_guild_epoch,
                    assistant_text=text,
                    memory_receipt_ref=memory_receipt,
                )
                assistant_hash = str(
                    binding.get("assistantHash") or ""
                )
                ingress_owner.mark_delivery_inflight(
                    entry_id,
                    guild_id=guild_id,
                    expected_guild_epoch=expected_guild_epoch,
                    delivery_ref=source_delivery_id,
                )

            if expected_authorization_grant_id is not None:
                _, authorization_error = current_action_authorization(
                    authorization_action_key,
                    action_run_id,
                    expected_grant_id=(
                        expected_authorization_grant_id
                    ),
                )
                if authorization_error:
                    if ingress_owner is not None:
                        error_code = (
                            "conversation_ingress_delivery_failed"
                        )
                        ingress_owner.mark_delivery_ambiguous(
                            entry_id,
                            guild_id=guild_id,
                            expected_guild_epoch=(
                                expected_guild_epoch
                            ),
                            error_code=error_code,
                        )
                        ingress_owner.discard_ambiguous(
                            entry_id,
                            guild_id=guild_id,
                            expected_guild_epoch=(
                                expected_guild_epoch
                            ),
                            assistant_hash=assistant_hash,
                            delivery_ref=source_delivery_id,
                            error_code=error_code,
                        )
                    return blocked_authorization_result(
                        authorization_error
                    )

            delivery_task = asyncio.create_task(
                deps.send_discord_text(channel, text),
                name=f"autonomy-discord-send:{guild_id}",
            )
            try:
                _, cancelled_during_send = (
                    await _drain_child_under_cancellation(
                        delivery_task
                    )
                )
                post_effect_cancellation = (
                    post_effect_cancellation
                    or cancelled_during_send
                )
            except asyncio.CancelledError:
                if ingress_owner is not None:
                    try:
                        ingress_owner.mark_delivery_ambiguous(
                            entry_id,
                            guild_id=guild_id,
                            expected_guild_epoch=(
                                expected_guild_epoch
                            ),
                        )
                    except Exception:
                        pass
                raise
            except MemoryDeletionJournalIntegrityError:
                raise
            except Exception as exc:
                definitive = is_definitive_discord_send_failure(
                    exc
                )
                if ingress_owner is not None:
                    error_code = (
                        "conversation_ingress_delivery_failed"
                        if definitive
                        else (
                            "conversation_ingress_delivery_ambiguous"
                        )
                    )
                    try:
                        binding = ingress_owner.mark_delivery_ambiguous(
                            entry_id,
                            guild_id=guild_id,
                            expected_guild_epoch=(
                                expected_guild_epoch
                            ),
                            error_code=error_code,
                        )
                        if definitive:
                            ingress_owner.discard_ambiguous(
                                entry_id,
                                guild_id=guild_id,
                                expected_guild_epoch=(
                                    expected_guild_epoch
                                ),
                                assistant_hash=str(
                                    binding.get("assistantHash") or ""
                                ),
                                delivery_ref=source_delivery_id,
                                error_code=error_code,
                            )
                    except Exception:
                        pass
                if deps.record_runtime_error is not None:
                    try:
                        deps.record_runtime_error(
                            "autonomy_followup_send_failed",
                            exc,
                        )
                    except Exception:
                        pass
                raise
            delivery_receipt_error: Exception | None = None
            if ingress_owner is not None:
                try:
                    ingress_owner.mark_delivery_succeeded(
                        entry_id,
                        guild_id=guild_id,
                        expected_guild_epoch=expected_guild_epoch,
                        delivery_ref=source_delivery_id,
                    )
                except Exception as exc:
                    delivery_receipt_error = exc
            deps.last_autonomy_ping_at[guild_id] = deps.monotonic()
            state_lock = deps.session_locks.setdefault(
                session_key,
                asyncio.Lock(),
            )

            async def finalize_delivered_followup() -> Exception | None:
                nonlocal continuity_durable, continuity_generation
                if delivery_receipt_error is not None:
                    return delivery_receipt_error
                try:
                    async with state_lock:
                        if ingress_owner is None:
                            active_turn_id = deps.start_new_turn(
                                session_key
                            )
                        else:
                            active_turn_id = deps.start_new_turn(
                                session_key,
                                turn_id=turn_id,
                            )
                            if active_turn_id != turn_id:
                                raise RuntimeError(
                                    "conversation_ingress_turn_binding_mismatch"
                                )
                        deps.append_history(
                            session_key,
                            stored_user_text,
                            text,
                            guild_id=guild_id,
                            memory_receipt=memory_receipt,
                        )
                        deps.mark_session_active(
                            session_key,
                            user_id=ingress.user_id,
                            ttl_sec=(
                                deps.active_conversation_text_question_sec
                                if awaiting_user_reply
                                else deps.active_conversation_text_sec
                            ),
                            speaker="assistant",
                            awaiting_user_reply=awaiting_user_reply,
                            topic_id=topic_id,
                            answer_text=text,
                            user_text=stored_user_text,
                        )
                        if ingress_owner is None:
                            continuity_status = (
                                await deps.commit_session_continuity(
                                    session_key,
                                    active_turn_id,
                                )
                            )
                        else:
                            continuity_status = (
                                await deps.commit_session_continuity(
                                    session_key,
                                    active_turn_id,
                                    before_commit=lambda generation: (
                                        ingress_owner.begin_terminal_commit(
                                            entry_id,
                                            guild_id=guild_id,
                                            expected_guild_epoch=(
                                                expected_guild_epoch
                                            ),
                                            continuity_generation=(
                                                generation
                                            ),
                                            assistant_text=text,
                                            memory_receipt_ref=(
                                                memory_receipt
                                            ),
                                        )
                                    ),
                                )
                            )
                        continuity_receipt = (
                            require_durable_continuity_receipt(
                                continuity_status
                            )
                        )
                        generation = int(
                            continuity_receipt["generation"]
                        )
                        if ingress_owner is not None:
                            ingress_owner.complete(
                                entry_id,
                                guild_id=guild_id,
                                expected_guild_epoch=(
                                    expected_guild_epoch
                                ),
                                continuity_generation=generation,
                                assistant_text=text,
                                memory_receipt_ref=memory_receipt,
                            )
                        continuity_durable = True
                        continuity_generation = generation
                except MemoryDeletionJournalIntegrityError:
                    raise
                except Exception as exc:
                    return exc
                return None

            finalize_task = asyncio.create_task(
                finalize_delivered_followup(),
                name=f"autonomy-followup-finalize:{guild_id}",
            )
            try:
                (
                    finalize_error,
                    cancelled_during_finalize,
                ) = await _drain_child_under_cancellation(
                    finalize_task
                )
                post_effect_cancellation = (
                    post_effect_cancellation
                    or cancelled_during_finalize
                )
            except asyncio.CancelledError:
                post_effect_cancellation = True
                finalize_error = RuntimeError(
                    "autonomy_followup_finalize_cancelled"
                )
        finally:
            if reply_lock.locked():
                reply_lock.release()

        post_effect_error: Exception | None = None
        if finalize_error is None:
            try:
                deps.schedule_memory_update(
                    guild_id,
                    stored_user_text,
                    text,
                    room_key=ingress.room_key,
                    person_key=ingress.person_key,
                    session_memory_key=ingress.session_memory_key,
                    source="autonomy",
                    assistant_speaker="Evelyn-Autonomy",
                    session_key=session_key,
                    runtime_mode="batch",
                )
                deps.mark_self_state_assistant_output(proactive=True)
            except asyncio.CancelledError:
                raise
            except MemoryDeletionJournalIntegrityError:
                post_effect_integrity_failure = True
            except Exception as exc:
                post_effect_error = exc

        reported_error = finalize_error or post_effect_error
        if reported_error is not None:
            error_event = (
                "sent_but_continuity_pending"
                if finalize_error is not None
                else "followup_finalize_failed"
            )
            if deps.record_runtime_error is not None:
                try:
                    deps.record_runtime_error(
                        "autonomy_followup_finalize_failed",
                        reported_error,
                    )
                except Exception:
                    pass
            try:
                deps.log(
                    f"[AUTONOMY] {error_event} guild={guild_id} "
                    f"errorType={type(reported_error).__name__}"
                )
            except Exception:
                pass
        result = {
            "status": "ok",
            "reason": (
                "sent_followup"
                if finalize_error is None
                else "sent_but_continuity_pending"
            ),
            "verified": True,
            "evidence_code": "discord_send_completed",
            "continuityDurable": continuity_durable,
            "continuityGeneration": continuity_generation,
        }
        if post_effect_cancellation:
            result["_post_effect_cancellation"] = True
        if post_effect_integrity_failure:
            result["_post_effect_integrity_failure"] = True
        return result

    async def default_send_followup(
        text: str,
        *,
        awaiting_user_reply: bool = False,
        user_text: str = "[autonomy]",
        context: AutonomyExecutionContext | None = None,
    ) -> dict[str, Any]:
        target = bound_followup_target()
        if target is None:
            return {"status": "blocked", "reason": "no_followup_channel"}
        reply_lock, reason = await claim_followup_reply_slot(target)
        if reply_lock is None:
            return {"status": "blocked", "reason": reason}
        action_run_id = (
            context.action_run_id
            if context is not None and context.action_run_id
            else secrets.token_hex(12)
        )
        if context is not None:
            if context.action_key != "assistant:send_followup":
                reply_lock.release()
                return blocked_authorization_result(
                    "authorization_scope_denied"
                )
            try:
                authorization, authorization_error = (
                    current_action_authorization(
                        context.action_key,
                        action_run_id,
                        expected_grant_id=(
                            context.authorization_grant_id
                        ),
                    )
                )
            except BaseException:
                reply_lock.release()
                raise
            if authorization_error or authorization is None:
                reply_lock.release()
                return blocked_authorization_result(
                    authorization_error
                    or "authorization_audit_unavailable"
                )
        return await send_claimed_followup(
            target,
            reply_lock,
            text,
            awaiting_user_reply=awaiting_user_reply,
            user_text=user_text,
            delivery_kind="followup",
            action_run_id=action_run_id,
            authorization_action_key=(
                context.action_key if context is not None else ""
            ),
            expected_authorization_grant_id=(
                context.authorization_grant_id
                if context is not None
                else None
            ),
        )

    async def default_summarize() -> dict[str, Any]:
        target = bound_followup_target()
        session_key = target[0] if target is not None else None
        with memory_safe_history(session_key) as history:
            result = build_autonomy_summary_payload(
                history,
                active_sessions=deps.get_active_session_count(),
                inflight_llm_requests=deps.get_inflight_llm_requests(),
            )
            result.pop("summary", None)
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
                {candidate[1].channel_id for candidate in followup_targets()}
            ),
        )
        result["verified"] = True
        result["evidence_code"] = "status_snapshot_built"
        return result

    async def default_summarize_recent_context() -> dict[str, Any]:
        target = bound_followup_target()
        session_key = target[0] if target is not None else None
        with memory_safe_history(session_key) as history:
            result = build_autonomy_recent_context_payload(history)
            result.pop("summary", None)
            result.pop("count", None)
            result["verified"] = True
            result["evidence_code"] = "recent_context_payload_built"
            return result

    async def default_maybe_ping_user(
        text: str,
        *,
        context: AutonomyExecutionContext | None = None,
    ) -> dict[str, Any]:
        last_ping_at = float(deps.last_autonomy_ping_at.get(guild_id, 0.0) or 0.0)
        if last_ping_at > 0 and (deps.monotonic() - last_ping_at) < 900:
            return {"status": "blocked", "reason": "ping_cooldown"}
        target = bound_followup_target()
        if target is None:
            return {"status": "blocked", "reason": "no_followup_channel"}
        session_key, ingress, _channel, _last_active = target
        with memory_safe_history(session_key) as history:
            latest_user_text = deps.pick_recent_user_text(history)
            reply_lock, reason = await claim_followup_reply_slot(target)
            if reply_lock is None:
                return {"status": "blocked", "reason": reason}
            action_run_id = (
                context.action_run_id
                if context is not None and context.action_run_id
                else secrets.token_hex(12)
            )
            if context is not None:
                if context.action_key != "assistant:maybe_ping_user":
                    reply_lock.release()
                    return blocked_authorization_result(
                        "authorization_scope_denied"
                    )
                try:
                    authorization, authorization_error = (
                        current_action_authorization(
                            context.action_key,
                            action_run_id,
                            expected_grant_id=(
                                context.authorization_grant_id
                            ),
                        )
                    )
                except BaseException:
                    reply_lock.release()
                    raise
                if authorization_error or authorization is None:
                    reply_lock.release()
                    return blocked_authorization_result(
                        authorization_error
                        or "authorization_audit_unavailable"
                    )
            try:
                marked = deps.select_and_mark_proactive_question(
                    guild_id=guild_id,
                    source="autonomy",
                    user_text=latest_user_text,
                    answer_text="",
                    awaiting_user_reply=False,
                    session_key=session_key,
                    session_memory_key=ingress.session_memory_key,
                )
                if not marked:
                    ask_text = None
                else:
                    ask_text = marked.get("ask_text")
                ask_text_valid = bool(
                    isinstance(ask_text, str) and ask_text.strip()
                )
            except BaseException:
                reply_lock.release()
                raise
            if not ask_text_valid:
                reply_lock.release()
                return {
                    "status": "ok",
                    "reason": "no_queued_proactive_question",
                    "skipped": True,
                    "verified": True,
                    "evidence_code": "proactive_gate_completed",
                }
            return await send_claimed_followup(
                target,
                reply_lock,
                ask_text,
                awaiting_user_reply=True,
                user_text="[autonomy]",
                delivery_kind="ping",
                action_run_id=action_run_id,
                authorization_action_key=(
                    context.action_key if context is not None else ""
                ),
                expected_authorization_grant_id=(
                    context.authorization_grant_id
                    if context is not None
                    else None
                ),
            )

    async def default_refresh_cognitive_state() -> dict[str, Any]:
        existing = deps.autonomy_cognitive_refresh_tasks.get(guild_id)
        if existing is not None and not existing.done():
            return {"status": "blocked", "reason": "router_refresh_inflight"}
        target = bound_followup_target()
        if target is None:
            return {"status": "blocked", "reason": "no_followup_channel"}
        session_key, ingress, _channel, _last_active = target
        reply_lock, reason = await claim_followup_reply_slot(target)
        if reply_lock is None:
            return {"status": "blocked", "reason": reason}
        owns_reply_lock = True
        task = asyncio.current_task()
        if task is None:
            reply_lock.release()
            return {
                "status": "blocked",
                "reason": "router_refresh_task_unavailable",
            }
        state_lock = deps.session_locks.setdefault(
            session_key,
            asyncio.Lock(),
        )
        deps.autonomy_cognitive_refresh_tasks[guild_id] = task
        try:
            async with state_lock:
                if not followup_target_is_current(target):
                    return {
                        "status": "blocked",
                        "reason": "no_followup_channel",
                    }
                reply_lock.release()
                owns_reply_lock = False
                with memory_safe_history(session_key) as history:
                    latest_user_text = deps.pick_recent_user_text(history)
                    if not latest_user_text:
                        return {
                            "status": "blocked",
                            "reason": "no_recent_user_text",
                        }
                    started_mono = deps.monotonic()
                    deps.autonomy_last_cognitive_refresh_at[guild_id] = (
                        started_mono
                    )
                    state = await deps.update_cognitive_state(
                        guild_id,
                        latest_user_text,
                        session_key=session_key,
                        room_key=ingress.room_key,
                        person_key=ingress.person_key,
                        session_memory_key=ingress.session_memory_key,
                        source="text",
                        turn_scope=None,
                    )
                    elapsed_ms = round(
                        (deps.monotonic() - started_mono) * 1000.0,
                        1,
                    )
                    return {
                        "status": "ok",
                        "reason": "router_refreshed",
                        "updated_at": state.get("updated_at"),
                        "action": state.get("action"),
                        "confidence": state.get("confidence"),
                        "elapsed_ms": elapsed_ms,
                        "verified": True,
                        "evidence_code": "cognitive_state_updated",
                    }
        finally:
            if owns_reply_lock and reply_lock.locked():
                reply_lock.release()
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
            executors=(
                {
                    "minecraft": deps.build_minecraft_executor(
                        guild_id
                    )
                }
                if deps.build_minecraft_executor is not None
                else {}
            ),
        ),
        notify=notify,
        poll_interval_sec=deps.autonomy_poll_interval_sec,
        get_authorized_actions=deps.get_authorized_actions,
        authorize_action=deps.authorize_action,
        record_action_outcome=deps.record_action_outcome,
        memory_index_dir=deps.memory_index_dir,
    )
    deps.autonomy_engines[guild_id] = engine
    return engine
