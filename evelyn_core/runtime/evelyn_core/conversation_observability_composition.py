from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .observability_metrics import (
    mark_turn_stage_from_runtime,
    new_turn_metrics_from_runtime,
    record_context_pipeline_benchmark_from_runtime,
    record_model_call_trace_from_runtime,
    register_drop_reason_from_runtime,
)
from .question_policy_runtime import (
    apply_fast_path_question_policy_from_runtime,
    extract_question_policy_from_route_meta_from_runtime,
    is_continuable_technical_topic_from_runtime,
    maybe_append_proactive_question_from_runtime,
    normalize_question_policy_mapping_from_runtime,
    proactive_question_scope_candidates_from_runtime,
    question_cooldown_hit_from_runtime,
    record_question_trace_from_runtime,
    record_session_question_asked_from_runtime,
    resolve_pending_proactive_question_for_turn_from_runtime,
    select_and_mark_proactive_question_from_runtime,
    summarize_question_metrics_from_runtime,
    user_frustration_with_questions_from_runtime,
    user_wants_direct_answer_from_runtime,
)


def _redact_validation_attempt_token(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_validation_attempt_token(nested)
            for key, nested in value.items()
            if "".join(ch for ch in str(key).lower() if ch.isalnum())
            != "validationattemptid"
        }
    if isinstance(value, list):
        return [_redact_validation_attempt_token(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_validation_attempt_token(item) for item in value)
    return value


def _safe_observability_print(output: Callable[..., Any], message: str) -> None:
    try:
        output(message)
    except Exception:
        pass


@dataclass(frozen=True)
class ConversationObservabilityCompositionDeps:
    question_policy: Callable[[], Any]
    question_policy_state: Callable[[], Any]
    turn_scope_registry: Any
    turn_stage_metrics: dict[str, dict[str, float]]
    model_call_metrics_store: Any
    write_turn_trace_event: Callable[..., None]
    turn_trace_json_log: bool
    bottleneck_events: Any
    summary_events: Any
    console_only_stt_and_reply: bool
    voice_bottleneck_logs: bool
    voice_trace_all_events: bool
    turn_trace_log_dir: Path
    turn_trace_file_lock: Any
    original_print: Callable[..., Any]
    trace_print: Callable[..., Any]
    monotonic: Callable[[], float]
    now: Callable[[], float]
    benchmark_log_path: Path
    project_root: Path
    log: Callable[..., Any]
    record_turn_stage_metric: Callable[..., None]
    summarize_voice_p95_metrics: Callable[..., dict[str, float | int]]
    get_search_followup_queued_count: Callable[[], int]
    build_rejected_voice_turn: Callable[..., Any]
    voice_validation_observer: Callable[[str, dict[str, Any]], Any] | None = None


class ConversationObservabilityComposition:
    """Owns turn tracing, scope, metrics, and question-policy adapters."""

    def __init__(self, deps: ConversationObservabilityCompositionDeps) -> None:
        self.deps = deps

    def log_turn_event(self, event: str, **payload) -> None:
        deps = self.deps
        public_payload = _redact_validation_attempt_token(payload)
        if deps.voice_validation_observer is not None:
            try:
                deps.voice_validation_observer(event, dict(payload))
            except Exception as exc:
                _safe_observability_print(
                    deps.original_print,
                    "[VOICE VALIDATION OBSERVER ERROR] "
                    f"errorType={type(exc).__name__}",
                )
        try:
            deps.write_turn_trace_event(
                event,
                public_payload,
                turn_trace_json_log=deps.turn_trace_json_log,
                bottleneck_events=deps.bottleneck_events,
                summary_events=deps.summary_events,
                console_only_stt_and_reply=deps.console_only_stt_and_reply,
                voice_bottleneck_logs=deps.voice_bottleneck_logs,
                voice_trace_all_events=deps.voice_trace_all_events,
                log_dir=deps.turn_trace_log_dir,
                file_lock=deps.turn_trace_file_lock,
                original_print=deps.original_print,
                trace_print=deps.trace_print,
            )
        except Exception as exc:
            _safe_observability_print(
                deps.original_print,
                "[TURN TRACE SINK ERROR] "
                f"errorType={type(exc).__name__}",
            )

    def record_model_call_trace(
        self,
        *,
        model_role: str,
        purpose: str,
        hot_path: bool,
        started_at: float,
        success: bool,
        metrics: dict | None = None,
        first_token_ms: float | None = None,
        error: BaseException | str | None = None,
        model_name: str | None = None,
        endpoint: str | None = None,
        turn_id: str | None = None,
        session_key: str | None = None,
        source: str | None = None,
        guild_id: int | None = None,
    ) -> None:
        record_model_call_trace_from_runtime(
            model_role=model_role,
            purpose=purpose,
            hot_path=hot_path,
            started_at=started_at,
            success=success,
            monotonic=self.deps.monotonic,
            record_model_call_metric=self.record_model_call_metric,
            log_turn_event=self.log_turn_event,
            metrics=metrics,
            first_token_ms=first_token_ms,
            error=error,
            model_name=model_name,
            endpoint=endpoint,
            turn_id=turn_id,
            session_key=session_key,
            source=source,
            guild_id=guild_id,
        )

    def record_context_pipeline_benchmark(
        self,
        *,
        metrics: dict | None,
        user_text: str,
        answer: str,
        source: str,
        guild_id: int | None,
        session_key: str | None,
    ) -> None:
        record_context_pipeline_benchmark_from_runtime(
            metrics=metrics,
            user_text=user_text,
            answer=answer,
            source=source,
            guild_id=guild_id,
            session_key=session_key,
            now=self.deps.now,
            benchmark_log_path=self.deps.benchmark_log_path,
            project_root=self.deps.project_root,
            log=self.deps.log,
        )

    def merge_log_event_payload(
        self,
        *,
        explicit: dict[str, Any],
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged = dict(extra or {})
        for key in explicit.keys():
            merged.pop(key, None)
        merged.update(explicit)
        return merged

    def replace_room_turn_scope(self, room_id: str, new_scope: Any, *, cancel_old: bool = True) -> Any:
        return self.deps.turn_scope_registry.replace_room_scope(
            room_id, new_scope, cancel_old=cancel_old
        )

    def get_room_turn_scope(self, room_id: str | None) -> Any:
        return self.deps.turn_scope_registry.get_room_scope(room_id)

    def attach_current_task(self, turn_scope: Any | None) -> Any:
        return self.deps.turn_scope_registry.attach_current_task(turn_scope)

    def detach_task(self, turn_scope: Any | None, task: Any | None) -> None:
        self.deps.turn_scope_registry.detach_task(turn_scope, task)

    def create_turn_scoped_task(self, coro: Awaitable[Any], turn_scope: Any | None = None) -> Any:
        return self.deps.turn_scope_registry.create_scoped_task(coro, turn_scope=turn_scope)

    def clear_room_turn_scope(self, room_id: str | None, turn_scope: Any | None = None) -> None:
        self.deps.turn_scope_registry.clear_room_scope(room_id, turn_scope)

    def record_turn_stage(self, turn_id: str | None, stage: str, elapsed_ms: float) -> None:
        self.deps.record_turn_stage_metric(
            self.deps.turn_stage_metrics, turn_id, stage, elapsed_ms
        )

    def record_model_call_metric(
        self,
        *,
        model_role: str,
        purpose: str,
        hot_path: bool,
        success: bool,
        latency_ms: float,
        first_token_ms: float | None = None,
    ) -> None:
        self.deps.model_call_metrics_store.record_model_call(
            model_role=model_role,
            purpose=purpose,
            hot_path=hot_path,
            success=success,
            latency_ms=latency_ms,
            first_token_ms=first_token_ms,
        )

    def replay_model_call_metrics_from_turn_trace(
        self, *, max_files: int = 7, max_lines_per_file: int = 12000
    ) -> dict[str, int]:
        return self.deps.model_call_metrics_store.replay_model_calls_from_turn_trace(
            max_files=max_files,
            max_lines_per_file=max_lines_per_file,
        )

    def ensure_model_call_metrics_replayed(self) -> None:
        self.deps.model_call_metrics_store.ensure_replayed()

    def record_turn_path_summary(
        self, meta: dict[str, Any], marks: dict[str, Any], total_ms: float
    ) -> None:
        self.deps.model_call_metrics_store.record_turn_path_summary(meta, marks, total_ms)

    def summarize_turn_path_metrics(self) -> list[dict[str, Any]]:
        return self.deps.model_call_metrics_store.summarize_turn_paths()

    def summarize_model_call_metrics(self) -> dict[str, Any]:
        return self.deps.model_call_metrics_store.summarize_model_calls()

    def normalize_question_policy_mapping(
        self, value: dict[str, Any] | None, *, default_source: str = "none"
    ) -> dict[str, Any]:
        return normalize_question_policy_mapping_from_runtime(
            value,
            default_source=default_source,
            deps=self.deps.question_policy(),
        )

    def extract_question_policy_from_route_meta(
        self, route_meta: dict[str, Any] | None
    ) -> dict[str, Any]:
        return extract_question_policy_from_route_meta_from_runtime(
            route_meta, deps=self.deps.question_policy()
        )

    def user_wants_direct_answer(self, text: str) -> bool:
        return user_wants_direct_answer_from_runtime(text, deps=self.deps.question_policy())

    def user_frustration_with_questions(self, text: str) -> bool:
        return user_frustration_with_questions_from_runtime(
            text, deps=self.deps.question_policy()
        )

    def is_continuable_technical_topic(self, text: str) -> bool:
        return is_continuable_technical_topic_from_runtime(
            text, deps=self.deps.question_policy()
        )

    def question_cooldown_hit(
        self, session_key: str | None, *, now: float | None = None
    ) -> bool:
        return question_cooldown_hit_from_runtime(
            session_key, now=now, deps=self.deps.question_policy_state()
        )

    def apply_fast_path_question_policy(
        self,
        route_decision: Any,
        *,
        user_text: str,
        session_key: str | None,
        route_meta_question_policy: dict[str, Any] | None = None,
    ) -> tuple[Any, bool]:
        return apply_fast_path_question_policy_from_runtime(
            route_decision,
            user_text=user_text,
            session_key=session_key,
            route_meta_question_policy=route_meta_question_policy,
            deps=self.deps.question_policy_state(),
        )

    def record_question_trace(
        self,
        *,
        route_decision: Any,
        answer: str,
        shape_meta: dict[str, Any],
        metrics: dict | None,
        cooldown_hit: bool = False,
    ) -> None:
        record_question_trace_from_runtime(
            route_decision=route_decision,
            answer=answer,
            shape_meta=shape_meta,
            metrics=metrics,
            cooldown_hit=cooldown_hit,
            deps=self.deps.question_policy_state(),
        )

    def summarize_question_metrics(self) -> dict[str, Any]:
        return summarize_question_metrics_from_runtime(
            deps=self.deps.question_policy_state()
        )

    def proactive_question_scope_candidates(
        self,
        *,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
    ) -> list[tuple[str, str | None]]:
        return proactive_question_scope_candidates_from_runtime(
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            deps=self.deps.question_policy_state(),
        )

    def record_session_question_asked(
        self, session_key: str | None, *, now: float | None = None
    ) -> None:
        record_session_question_asked_from_runtime(
            session_key, now=now, deps=self.deps.question_policy_state()
        )

    def resolve_pending_proactive_question_for_turn(
        self,
        guild_id: int | None,
        user_text: str,
        *,
        session_key: str | None = None,
        session_memory_key: str | None = None,
        metrics: dict | None = None,
    ) -> dict[str, Any]:
        return resolve_pending_proactive_question_for_turn_from_runtime(
            guild_id,
            user_text,
            session_key=session_key,
            session_memory_key=session_memory_key,
            metrics=metrics,
            deps=self.deps.question_policy_state(),
        )

    def select_and_mark_proactive_question(
        self,
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
    ) -> dict[str, Any] | None:
        return select_and_mark_proactive_question_from_runtime(
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
            deps=self.deps.question_policy_state(),
        )

    def maybe_append_proactive_question(
        self,
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
    ) -> tuple[str, bool]:
        return maybe_append_proactive_question_from_runtime(
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
            deps=self.deps.question_policy_state(),
        )

    def summarize_p95_metrics(self) -> dict[str, float | int]:
        return self.deps.summarize_voice_p95_metrics(
            self.deps.turn_stage_metrics,
            search_followup_queued_count=self.deps.get_search_followup_queued_count(),
            cancelled_stale_turn_count=(
                self.deps.turn_scope_registry.cancelled_stale_turn_count
            ),
        )

    def new_turn_metrics(
        self,
        *,
        source: str,
        session_key: str | None = None,
        room_session_key: str | None = None,
        guild_id: int | None = None,
        user_id: int | None = None,
        owner_user_id: int | None = None,
        topic_id: str | None = None,
        turn_id: str | None = None,
        segment_id: int | None = None,
        chunk_index: int | None = None,
    ) -> dict:
        return new_turn_metrics_from_runtime(
            source=source,
            monotonic=self.deps.monotonic,
            log_turn_event=self.log_turn_event,
            session_key=session_key,
            room_session_key=room_session_key,
            guild_id=guild_id,
            user_id=user_id,
            owner_user_id=owner_user_id,
            topic_id=topic_id,
            turn_id=turn_id,
            segment_id=segment_id,
            chunk_index=chunk_index,
        )

    def mark_turn_stage(
        self,
        metrics: dict | None,
        key: str,
        *,
        event_name: str | None = None,
        **extra,
    ) -> None:
        mark_turn_stage_from_runtime(
            metrics,
            key,
            monotonic=self.deps.monotonic,
            record_turn_stage=self.record_turn_stage,
            merge_log_event_payload=self.merge_log_event_payload,
            log_turn_event=self.log_turn_event,
            event_name=event_name,
            **extra,
        )

    def register_drop_reason(self, metrics: dict | None, reason: str, **extra) -> None:
        register_drop_reason_from_runtime(
            metrics,
            reason,
            build_rejected_voice_turn=self.deps.build_rejected_voice_turn,
            merge_log_event_payload=self.merge_log_event_payload,
            log_turn_event=self.log_turn_event,
            **extra,
        )
