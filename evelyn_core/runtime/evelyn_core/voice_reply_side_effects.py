from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, MutableMapping

from .continuity_commit_contract import (
    require_durable_continuity_receipt,
)
from .memory_confirmation_contract import (
    explicit_memory_writer_skip_decision,
    is_explicit_memory_confirmation_receipt,
)
from .conversation_memory_receipt import (
    memory_receipt_ref_from_metrics,
)
from .memory_deletion_journal import (
    MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    MemoryDeletionJournalIntegrityError,
)
from .memory_exposure import (
    MemoryExposurePosition,
    current_memory_exposure_position,
    memory_exposure_guard,
)
from .reply_memory_boundary import validate_reply_memory_boundary


VOICE_DELIVERY_FAILURE_CODES = frozenset(
    {
        "voice_connection_unavailable",
        "voice_delivery_empty",
        "voice_delivery_failed",
    }
)
_MEMORY_EXPOSURE_UNSET = object()


@dataclass(frozen=True)
class VoiceReplyMemoryBoundary:
    memory_exposure_position: MemoryExposurePosition | None
    memory_receipt: Any


_voice_reply_memory_boundary: ContextVar[
    VoiceReplyMemoryBoundary | None
] = ContextVar("voice_reply_memory_boundary", default=None)


@contextmanager
def bind_voice_reply_memory_boundary(
    *,
    memory_exposure_position: MemoryExposurePosition | None,
    memory_receipt: Any,
) -> Iterator[VoiceReplyMemoryBoundary]:
    """Hand the delivered reply boundary through existing typed wiring."""

    boundary = VoiceReplyMemoryBoundary(
        memory_exposure_position=memory_exposure_position,
        memory_receipt=memory_receipt,
    )
    token = _voice_reply_memory_boundary.set(boundary)
    try:
        yield boundary
    finally:
        _voice_reply_memory_boundary.reset(token)


def current_voice_reply_memory_boundary(
) -> VoiceReplyMemoryBoundary | None:
    return _voice_reply_memory_boundary.get()


@dataclass(frozen=True)
class VoiceReplySideEffectDeps:
    session_speculative_policies: MutableMapping[str, Any]
    append_history: Callable[..., Any]
    compute_runtime_mode: Callable[[dict[str, Any]], str]
    record_context_pipeline_benchmark: Callable[..., Any]
    schedule_memory_update: Callable[..., Any]
    read_cached_cognitive_state: Callable[..., Any]
    apply_ask_gating: Callable[..., dict[str, Any]]
    schedule_search_followup: Callable[..., Any]
    session_state_snapshot: Callable[[str | None], dict[str, Any]]
    mark_session_active: Callable[..., Any]
    set_room_owner: Callable[..., Any]
    commit_session_continuity: Callable[..., Any]
    log: Callable[..., Any]
    active_conversation_voice_question_sec: float
    active_conversation_voice_sec: float
    active_conversation_awaiting_reply_sec: float
    memory_index_dir: Path | None = None


def _validated_reply_memory_boundary(
    *,
    memory_exposure_position: MemoryExposurePosition | None,
    memory_receipt: Any,
) -> tuple[MemoryExposurePosition | None, dict[str, Any]]:
    """Bind durable side effects to the exact content-free reply inputs."""

    return validate_reply_memory_boundary(
        memory_exposure_position=memory_exposure_position,
        memory_receipt=memory_receipt,
    )


def _record_memory_boundary_failure(
    *,
    metrics: dict[str, Any],
    deps: VoiceReplySideEffectDeps,
) -> None:
    meta = metrics.setdefault("meta", {})
    meta.update(
        {
            "voice_reply_side_effects_state": "failed",
            "voice_reply_side_effects_error": (
                MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR
            ),
            "continuity_commit": "skipped",
            "continuity_error": (
                MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR
            ),
        }
    )
    deps.log(
        "[VOICE TURN] reply_side_effects_rejected error=",
        MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    )


def finalize_voice_reply_side_effects_from_runtime(
    *,
    guild_id: int,
    member: Any,
    session_key: str,
    room_session_key: str,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    voice_reply: Any,
    plain_answer: str,
    metrics: dict[str, Any],
    turn_scope: Any,
    accepted_turn_id: str,
    segment_id: int,
    delivery_succeeded: bool = True,
    failure_code: str = "",
    memory_exposure_position: Any = _MEMORY_EXPOSURE_UNSET,
    memory_receipt: Any = None,
    deps: VoiceReplySideEffectDeps,
) -> None:
    if not delivery_succeeded:
        deps.session_speculative_policies.pop(session_key, None)
        meta = metrics.setdefault("meta", {})
        safe_failure_code = (
            failure_code
            if failure_code in VOICE_DELIVERY_FAILURE_CODES
            else "voice_delivery_failed"
        )
        meta.update(
            {
                "voice_delivery_state": "failed",
                "voice_delivery_error": safe_failure_code,
                "continuity_turn_state": "unanswered_user",
                "error": safe_failure_code,
            }
        )
        meta.setdefault("error_layer", "voice_delivery")
        if meta.get("unanswered_voice_turn_recorded") is True:
            return
        try:
            deps.append_history(
                session_key,
                voice_reply.history_user_text,
                None,
                guild_id=guild_id,
            )
            meta["unanswered_voice_turn_recorded"] = True
            deps.mark_session_active(
                session_key,
                user_id=getattr(member, "id", None),
                ttl_sec=deps.active_conversation_voice_sec,
                speaker="user",
                awaiting_user_reply=False,
                topic_id=voice_reply.topic_id,
                user_text=voice_reply.history_user_text,
            )
            deps.set_room_owner(
                room_session_key,
                getattr(member, "id", None),
                ttl_sec=deps.active_conversation_voice_sec,
                reason="voice_delivery_failed",
                session_key=session_key,
                turn_id=accepted_turn_id,
                segment_id=segment_id,
            )
            continuity_receipt = require_durable_continuity_receipt(
                deps.commit_session_continuity(
                    session_key,
                    accepted_turn_id,
                )
            )
            meta.update(
                {
                    "continuity_commit": "durable",
                    "continuity_generation": int(
                        continuity_receipt["generation"]
                    ),
                }
            )
        except Exception as exc:
            meta.update(
                {
                    "continuity_commit": "failed",
                    "continuity_error": (
                        "conversation_continuity_commit_failed"
                    ),
                }
            )
            deps.log(
                "[VOICE TURN] unanswered_turn_commit_failed errorType=",
                type(exc).__name__,
            )
        return

    try:
        bound_reply = current_voice_reply_memory_boundary()
        if memory_exposure_position is _MEMORY_EXPOSURE_UNSET:
            boundary_exposure = (
                bound_reply.memory_exposure_position
                if bound_reply is not None
                else current_memory_exposure_position()
            )
        else:
            boundary_exposure = memory_exposure_position
        boundary_receipt = (
            memory_receipt
            if memory_receipt is not None
            else bound_reply.memory_receipt
            if bound_reply is not None
            else memory_receipt_ref_from_metrics(metrics)
        )
        reply_exposure, reply_receipt = (
            _validated_reply_memory_boundary(
                memory_exposure_position=boundary_exposure,
                memory_receipt=boundary_receipt,
            )
        )
        with memory_exposure_guard(
            expected_position=reply_exposure,
            required=reply_exposure is not None,
            index_dir=deps.memory_index_dir,
        ):
            deps.session_speculative_policies.pop(session_key, None)
            deps.append_history(
                session_key,
                voice_reply.history_user_text,
                plain_answer,
                guild_id=guild_id,
                memory_receipt=reply_receipt,
            )
            runtime_mode = (
                ((metrics.get("meta") or {}).get("runtime_mode"))
                or deps.compute_runtime_mode(metrics)
            )
            memory_write_receipt = (metrics.get("meta") or {}).get(
                "memory_write_receipt"
            )
            explicit_memory_write = (
                is_explicit_memory_confirmation_receipt(
                    memory_write_receipt
                )
            )
            if explicit_memory_write:
                memory_writer_decision = (
                    explicit_memory_writer_skip_decision()
                )
            else:
                deps.record_context_pipeline_benchmark(
                    metrics=metrics,
                    user_text=voice_reply.history_user_text,
                    answer=plain_answer,
                    source="voice",
                    guild_id=guild_id,
                    session_key=session_key,
                )
                memory_writer_decision = deps.schedule_memory_update(
                    guild_id,
                    voice_reply.history_user_text,
                    plain_answer,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    source="voice",
                    user_speaker=getattr(member, "display_name", ""),
                    assistant_speaker="Evelyn",
                    session_key=session_key,
                    turn_scope=turn_scope,
                    runtime_mode=runtime_mode,
                )
            metrics.setdefault("meta", {})[
                "memory_writer_decision"
            ] = memory_writer_decision
            search_requested = False
            if not explicit_memory_write:
                search_requested = bool(
                    deps.apply_ask_gating(
                        deps.read_cached_cognitive_state(
                            guild_id,
                            room_key=room_key,
                            person_key=person_key,
                            session_memory_key=session_memory_key,
                        ),
                        source="voice",
                    ).get("action")
                    == "search_then_answer"
                )
            awaiting_reply = bool(
                deps.session_state_snapshot(session_key).get(
                    "awaiting_user_reply"
                )
            )
            followup_ttl = (
                deps.active_conversation_voice_question_sec
                if awaiting_reply
                else deps.active_conversation_voice_sec
            )
            deps.mark_session_active(
                session_key,
                user_id=getattr(member, "id", None),
                ttl_sec=followup_ttl,
                speaker="assistant",
                awaiting_user_reply=awaiting_reply,
                topic_id=voice_reply.topic_id,
                answer_text=plain_answer,
                user_text=voice_reply.history_user_text,
            )
            deps.set_room_owner(
                room_session_key,
                getattr(member, "id", None),
                ttl_sec=(
                    deps.active_conversation_awaiting_reply_sec
                    if awaiting_reply
                    else followup_ttl
                ),
                reason="assistant_reply",
                session_key=session_key,
                turn_id=accepted_turn_id,
                segment_id=segment_id,
            )
            try:
                continuity_status = deps.commit_session_continuity(
                    session_key,
                    accepted_turn_id,
                )
                continuity_receipt = require_durable_continuity_receipt(
                    continuity_status
                )
                metrics.setdefault("meta", {}).update(
                    {
                        "continuity_commit": "durable",
                        "continuity_generation": int(
                            continuity_receipt["generation"]
                        ),
                    }
                )
            except MemoryDeletionJournalIntegrityError:
                raise
            except Exception as exc:
                metrics.setdefault("meta", {}).update(
                    {
                        "continuity_commit": "failed",
                        "continuity_error": (
                            "conversation_continuity_commit_failed"
                        ),
                    }
                )
                deps.log(
                    "[VOICE TURN] continuity_commit_failed errorType=",
                    type(exc).__name__,
                )
            if not explicit_memory_write:
                deps.schedule_search_followup(
                    guild_id,
                    session_key,
                    voice_reply.history_user_text,
                    plain_answer,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    channel_id=None,
                    source="search-followup-voice",
                    force=search_requested,
                    turn_scope=None,
                    runtime_mode=runtime_mode,
                    continuity_generation=(
                        metrics.get("meta", {}).get(
                            "continuity_generation"
                        )
                    ),
                )
            metrics.setdefault("meta", {})[
                "voice_reply_side_effects_state"
            ] = "durable"
    except MemoryDeletionJournalIntegrityError:
        _record_memory_boundary_failure(metrics=metrics, deps=deps)
        return
