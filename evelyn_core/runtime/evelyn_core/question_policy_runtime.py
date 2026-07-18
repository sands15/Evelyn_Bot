from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class QuestionPolicyRuntimeDeps:
    normalize_question_policy_mapping_payload: Callable[[dict[str, Any] | None, str], dict[str, Any]]
    extract_question_policy_from_route_meta_payload: Callable[[dict[str, Any] | None], dict[str, Any]]
    user_wants_direct_answer_payload: Callable[[str], bool]
    user_frustration_with_questions_payload: Callable[[str], bool]
    is_continuable_technical_topic_payload: Callable[[str], bool]


@dataclass(frozen=True)
class QuestionPolicyStateRuntimeDeps:
    question_cooldown_hit_payload: Callable[..., bool]
    apply_fast_path_question_policy_payload: Callable[..., tuple[Any, bool]]
    record_question_trace_payload: Callable[..., None]
    summarize_question_metrics_payload: Callable[[], dict[str, Any]]
    proactive_scope_candidates_payload: Callable[..., list[tuple[str, str | None]]]
    record_session_question_asked_payload: Callable[..., None]
    resolve_pending_proactive_question_for_turn_payload: Callable[..., dict[str, Any]]
    select_and_mark_proactive_question_payload: Callable[..., dict[str, Any] | None]
    maybe_append_proactive_question_payload: Callable[..., tuple[str, bool]]


def normalize_question_policy_mapping_from_runtime(
    value: dict[str, Any] | None,
    *,
    default_source: str = "none",
    deps: QuestionPolicyRuntimeDeps,
) -> dict[str, Any]:
    return deps.normalize_question_policy_mapping_payload(
        value,
        default_source=default_source,
    )


def extract_question_policy_from_route_meta_from_runtime(
    route_meta: dict[str, Any] | None,
    *,
    deps: QuestionPolicyRuntimeDeps,
) -> dict[str, Any]:
    return deps.extract_question_policy_from_route_meta_payload(route_meta)


def user_wants_direct_answer_from_runtime(text: str, *, deps: QuestionPolicyRuntimeDeps) -> bool:
    return deps.user_wants_direct_answer_payload(text)


def user_frustration_with_questions_from_runtime(text: str, *, deps: QuestionPolicyRuntimeDeps) -> bool:
    return deps.user_frustration_with_questions_payload(text)


def is_continuable_technical_topic_from_runtime(text: str, *, deps: QuestionPolicyRuntimeDeps) -> bool:
    return deps.is_continuable_technical_topic_payload(text)


def question_cooldown_hit_from_runtime(session_key: str | None, *, now: float | None = None, deps: QuestionPolicyStateRuntimeDeps) -> bool:
    return deps.question_cooldown_hit_payload(session_key, now=now)


def apply_fast_path_question_policy_from_runtime(
    route_decision: Any,
    *,
    user_text: str,
    session_key: str | None,
    route_meta_question_policy: dict[str, Any] | None = None,
    deps: QuestionPolicyStateRuntimeDeps,
) -> tuple[Any, bool]:
    return deps.apply_fast_path_question_policy_payload(
        route_decision,
        user_text=user_text,
        session_key=session_key,
        route_meta_question_policy=route_meta_question_policy,
    )


def record_question_trace_from_runtime(
    *,
    route_decision: Any,
    answer: str,
    shape_meta: dict[str, Any],
    metrics: dict | None,
    cooldown_hit: bool = False,
    deps: QuestionPolicyStateRuntimeDeps,
) -> None:
    deps.record_question_trace_payload(
        route_decision=route_decision,
        answer=answer,
        shape_meta=shape_meta,
        metrics=metrics,
        cooldown_hit=cooldown_hit,
    )


def summarize_question_metrics_from_runtime(*, deps: QuestionPolicyStateRuntimeDeps) -> dict[str, Any]:
    return deps.summarize_question_metrics_payload()


def proactive_question_scope_candidates_from_runtime(
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    deps: QuestionPolicyStateRuntimeDeps,
) -> list[tuple[str, str | None]]:
    return deps.proactive_scope_candidates_payload(
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )


def record_session_question_asked_from_runtime(session_key: str | None, *, now: float | None = None, deps: QuestionPolicyStateRuntimeDeps) -> None:
    deps.record_session_question_asked_payload(session_key, now=now)


def resolve_pending_proactive_question_for_turn_from_runtime(
    guild_id: int | None,
    user_text: str,
    *,
    session_key: str | None = None,
    session_memory_key: str | None = None,
    metrics: dict | None = None,
    deps: QuestionPolicyStateRuntimeDeps,
) -> dict[str, Any]:
    return deps.resolve_pending_proactive_question_for_turn_payload(
        guild_id,
        user_text,
        session_key=session_key,
        session_memory_key=session_memory_key,
        metrics=metrics,
    )


def select_and_mark_proactive_question_from_runtime(
    *,
    guild_id: int | None,
    source: str,
    user_text: str,
    answer_text: str = "",
    awaiting_user_reply: bool = False,
    room_key: str | None = None,
    person_key: str | None = None,
    session_key: str | None = None,
    session_memory_key: str | None = None,
    runtime_block_reason: str = "",
    metrics: dict | None = None,
    deps: QuestionPolicyStateRuntimeDeps,
) -> dict[str, Any] | None:
    return deps.select_and_mark_proactive_question_payload(
        guild_id=guild_id,
        source=source,
        user_text=user_text,
        answer_text=answer_text,
        awaiting_user_reply=awaiting_user_reply,
        room_key=room_key,
        person_key=person_key,
        session_key=session_key,
        session_memory_key=session_memory_key,
        runtime_block_reason=runtime_block_reason,
        metrics=metrics,
    )


def maybe_append_proactive_question_from_runtime(
    answer_text: str,
    *,
    guild_id: int | None,
    source: str,
    user_text: str,
    awaiting_user_reply: bool,
    room_key: str | None = None,
    person_key: str | None = None,
    session_key: str | None = None,
    session_memory_key: str | None = None,
    metrics: dict | None = None,
    deps: QuestionPolicyStateRuntimeDeps,
) -> tuple[str, bool]:
    return deps.maybe_append_proactive_question_payload(
        answer_text,
        guild_id=guild_id,
        source=source,
        user_text=user_text,
        awaiting_user_reply=awaiting_user_reply,
        room_key=room_key,
        person_key=person_key,
        session_key=session_key,
        session_memory_key=session_memory_key,
        metrics=metrics,
    )


__all__ = [
    "QuestionPolicyRuntimeDeps",
    "extract_question_policy_from_route_meta_from_runtime",
    "is_continuable_technical_topic_from_runtime",
    "normalize_question_policy_mapping_from_runtime",
    "user_frustration_with_questions_from_runtime",
    "user_wants_direct_answer_from_runtime",
    "QuestionPolicyStateRuntimeDeps",
    "question_cooldown_hit_from_runtime",
    "apply_fast_path_question_policy_from_runtime",
    "record_question_trace_from_runtime",
    "summarize_question_metrics_from_runtime",
    "proactive_question_scope_candidates_from_runtime",
    "record_session_question_asked_from_runtime",
    "resolve_pending_proactive_question_for_turn_from_runtime",
    "select_and_mark_proactive_question_from_runtime",
    "maybe_append_proactive_question_from_runtime",
]
