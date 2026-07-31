from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, MutableMapping

from .assistant_contracts import AcceptedVoiceTurn, RejectedVoiceTurn
from .room_session_state import (
    clear_room_owner,
    is_room_owner_active,
    room_state_snapshot,
    set_room_owner,
    set_room_reply_in_progress,
)
from .text import clean_text, is_user_echo_answer
from .explicit_memory_confirmation import (
    execute_explicit_memory_confirmation,
    is_explicit_memory_confirmation_command,
)
from .voice_pipeline import AnswerPayload, DeliveryPlan, RouteDecision, TranscriptResult, VoiceReplyRequest, VoiceSegment
from .voice_barge_in import remember_voice_utterance_for_merge


@dataclass(frozen=True)
class VoiceReplyLifecycle:
    accepted_turn_id: str
    should_cancel_old_scope: bool
    owner_ttl_sec: float
    topic_id: str
    history_user_text: str


@dataclass(frozen=True)
class VoiceAcceptedTurnActivation:
    accepted_turn_id: str
    accepted_turn: AcceptedVoiceTurn
    should_cancel_old_scope: bool
    owner_ttl_sec: float
    topic_id: str
    history_user_text: str


@dataclass(frozen=True)
class VoiceReplyExecutionState:
    turn_scope: Any
    turn_task: Any


@dataclass(frozen=True)
class VoiceAcceptedReplyExecution:
    accepted_turn_id: str
    accepted_turn: AcceptedVoiceTurn
    topic_id: str
    turn_scope: Any
    turn_task: Any


@dataclass(frozen=True)
class VoiceReplyDeliveryResult:
    answer_text: str
    plain_answer_text: str
    used_wake_only_reply: bool


@dataclass(frozen=True)
class VoiceReplyPreparationResult:
    accepted: bool
    gate_mode: str | None
    drop_reason: str | None
    voice_reply: VoiceReplyRequest | None


@dataclass(frozen=True)
class VoiceReplyDeliveryRuntime:
    accepted_turn_id: str
    turn_scope: Any
    turn_task: Any
    lock: asyncio.Lock
    on_final_answer: Callable[[str], Any]
    report_waiting_on_lock: Callable[[VoiceReplyRequest], None]
    report_delivery_error: Callable[[Exception], None]


@dataclass(frozen=True)
class VoiceTranscriptReplyContext:
    guild_id: int
    transcript: TranscriptResult
    voice_segment: VoiceSegment
    session_key: str | None
    room_session_key: str
    owner_user_id: int | None
    source_turn_id: str
    segment_id: int
    voiced_ms: float
    raw_seconds: float
    rms: float
    wake_detected: bool
    reply_in_progress: bool
    metrics: MutableMapping[str, Any]
    session_topic_seed: str
    now_monotonic: float
    ingress_source: str
    queue_wait_ms: float
    active_conversation_awaiting_reply_sec: float
    active_conversation_voice_sec: float
    member: Any
    canned_wake_reply: str
    room_key: str | None
    person_key: str | None
    session_memory_key: str | None


@dataclass(frozen=True)
class VoiceTranscriptReplyDeps:
    should_reply_to_voice: Callable[..., tuple[bool, str, str | None]]
    register_drop_reason: Callable[..., Any]
    log_voice_stage: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    reset_session_bad_audio: Callable[[str | None], Any]
    build_voice_reply_request: Callable[..., VoiceReplyRequest]
    build_topic_id: Callable[[str | None], str]
    session_last_stt_text: MutableMapping[str, str]
    room_last_voice_reply_at: MutableMapping[str, float]
    update_room_speaker_activity: Callable[..., Any]
    pick_active_speaker: Callable[[str | None], int | None]
    start_new_turn: Callable[..., str]
    update_session_state: Callable[..., Any]
    set_room_owner: Callable[..., Any]
    session_partial_stt_text: MutableMapping[str, str]
    session_committed_stt_text: MutableMapping[str, str]
    partial_stt_cache: MutableMapping[str, Any]
    make_turn_scope: Callable[[str], Any]
    replace_room_turn_scope: Callable[..., Any]
    attach_current_task: Callable[[Any], Any]
    set_room_reply_in_progress: Callable[..., Any]
    session_locks: MutableMapping[str, asyncio.Lock]
    visible_text: Callable[[str], str]
    print_fn: Callable[..., Any]
    get_voice_client: Callable[[], Any]
    speak_answer: Callable[..., Any]
    ask_llm_and_speak_streaming: Callable[..., Any]
    record_voice_pipeline_failure: Callable[..., Any]
    finalize_voice_reply_side_effects: Callable[..., Any]
    strip_omnivoice_tags: Callable[[str], str]
    get_room_turn_scope: Callable[[str | None], Any]
    detach_task: Callable[[Any, Any], None]
    clear_room_turn_scope: Callable[[str | None, Any], None]
    room_last_voice_utterance_for_merge: MutableMapping[str, Any] | None = None


@dataclass(frozen=True)
class VoiceTurnRequest:
    user_text: str
    guild_id: int | None = None
    session_key: str | None = None
    room_key: str | None = None
    person_key: str | None = None
    session_memory_key: str | None = None
    source: str = "text"
    debug_text: str | None = None
    metrics: MutableMapping[str, Any] | None = None
    turn_scope: Any = None
    on_sentence: Callable[[str], Awaitable[None]] | None = None
    on_first_chunk: Callable[[], None] | None = None


@dataclass(frozen=True)
class VoiceTurnRouteContext:
    messages: list[dict[str, Any]]
    cognitive_state: dict[str, Any] | None
    route_decision: RouteDecision
    gated_state: dict[str, Any] | None
    awaiting_user_reply: bool


@dataclass(frozen=True)
class VoiceTurnResult:
    answer_text: str
    route_context: VoiceTurnRouteContext
    handled_by: str


@dataclass(frozen=True)
class VoiceTurnOrchestratorDeps:
    prepare_route_context: Callable[..., Awaitable[tuple[list[dict[str, Any]], dict | None, RouteDecision, dict | None, bool]]]
    maybe_handle_short_circuit_route: Callable[..., Awaitable[tuple[str | None, Callable[[], None] | None]]]
    maybe_execute_registered_route: Callable[..., Awaitable[str | None]]
    run_main_llm_turn: Callable[..., Awaitable[str]]
    emit_delivery_plan_chunks: Callable[..., Awaitable[Any]]
    build_answer_payload_from_text: Callable[[str], AnswerPayload]
    build_delivery_plan: Callable[..., DeliveryPlan]
    split_tts_sentences: Callable[..., Any]


def mark_voice_turn_error_layer(request: VoiceTurnRequest, layer: str, exc: BaseException) -> None:
    metrics = request.metrics
    if metrics is None:
        return
    meta = metrics.setdefault("meta", {})
    meta["error_layer"] = layer
    meta["error"] = type(exc).__name__


class VoiceTurnOrchestrator:
    def __init__(self, deps: VoiceTurnOrchestratorDeps) -> None:
        self._deps = deps

    async def execute(self, request: VoiceTurnRequest) -> VoiceTurnResult:
        if request.turn_scope is not None:
            request.turn_scope.raise_if_cancelled()

        try:
            route_context = await self._prepare_route_context(request)
        except Exception as exc:
            mark_voice_turn_error_layer(request, "voice_turn_orchestrator.route_context", exc)
            raise

        on_first_chunk = request.on_first_chunk

        try:
            short_circuit_answer, on_first_chunk = await self._deps.maybe_handle_short_circuit_route(
                route_decision=route_context.route_decision,
                source=request.source,
                guild_id=request.guild_id,
                user_text=request.user_text,
                session_key=request.session_key,
                room_key=request.room_key,
                person_key=request.person_key,
                session_memory_key=request.session_memory_key,
                debug_text=request.debug_text,
                on_sentence=request.on_sentence,
                on_first_chunk=on_first_chunk,
                awaiting_user_reply=route_context.awaiting_user_reply,
                metrics=request.metrics,
                messages=route_context.messages,
                cognitive_state=route_context.cognitive_state,
            )
        except Exception as exc:
            mark_voice_turn_error_layer(request, "voice_turn_orchestrator.short_circuit", exc)
            raise

        if short_circuit_answer is not None and not is_user_echo_answer(request.user_text, short_circuit_answer):
            return VoiceTurnResult(
                answer_text=short_circuit_answer,
                route_context=route_context,
                handled_by="short_circuit",
            )

        try:
            skill_route_answer = await self._deps.maybe_execute_registered_route(
                route_decision=route_context.route_decision,
                user_text=request.user_text,
                source=request.source,
                guild_id=request.guild_id,
                session_key=request.session_key,
                room_key=request.room_key,
                person_key=request.person_key,
                session_memory_key=request.session_memory_key,
                debug_text=request.debug_text,
                metrics=request.metrics,
                cognitive_state=route_context.cognitive_state,
                messages=route_context.messages,
            )
        except Exception as exc:
            mark_voice_turn_error_layer(request, "voice_turn_orchestrator.skill_route", exc)
            raise

        if skill_route_answer and not is_user_echo_answer(request.user_text, skill_route_answer):
            if on_first_chunk is not None:
                on_first_chunk()
                on_first_chunk = None
            try:
                await self._deps.emit_delivery_plan_chunks(
                    self._deps.build_delivery_plan(
                        self._deps.build_answer_payload_from_text(skill_route_answer),
                        include_voice=request.on_sentence is not None and route_context.route_decision.needs_tts,
                        split_chunks=self._deps.split_tts_sentences,
                    ),
                    on_sentence=request.on_sentence,
                )
            except Exception as exc:
                mark_voice_turn_error_layer(request, "voice_turn_orchestrator.delivery", exc)
                raise
            return VoiceTurnResult(
                answer_text=skill_route_answer,
                route_context=route_context,
                handled_by="skill_route",
            )

        if not route_context.route_decision.needs_main_llm:
            answer = route_context.route_decision.user_visible_preface
            if not answer or is_user_echo_answer(request.user_text, answer):
                answer = ""
        if not route_context.route_decision.needs_main_llm and answer:
            if on_first_chunk is not None:
                on_first_chunk()
                on_first_chunk = None
            try:
                await self._deps.emit_delivery_plan_chunks(
                    self._deps.build_delivery_plan(
                        self._deps.build_answer_payload_from_text(answer),
                        include_voice=request.on_sentence is not None and route_context.route_decision.needs_tts,
                        split_chunks=self._deps.split_tts_sentences,
                    ),
                    on_sentence=request.on_sentence,
                )
            except Exception as exc:
                mark_voice_turn_error_layer(request, "voice_turn_orchestrator.delivery", exc)
                raise
            return VoiceTurnResult(
                answer_text=answer,
                route_context=route_context,
                handled_by="policy_no_main_llm",
            )

        try:
            answer = await self._deps.run_main_llm_turn(
                request=request,
                route_context=route_context,
                on_first_chunk=on_first_chunk,
            )
        except Exception as exc:
            mark_voice_turn_error_layer(request, "voice_turn_orchestrator.main_llm", exc)
            raise

        return VoiceTurnResult(
            answer_text=answer,
            route_context=route_context,
            handled_by="main_llm",
        )

    async def _prepare_route_context(self, request: VoiceTurnRequest) -> VoiceTurnRouteContext:
        messages, cognitive_state, route_decision, gated_state, awaiting_user_reply = await self._deps.prepare_route_context(
            request.user_text,
            guild_id=request.guild_id,
            session_key=request.session_key,
            room_key=request.room_key,
            person_key=request.person_key,
            session_memory_key=request.session_memory_key,
            source=request.source,
            debug_text=request.debug_text,
            metrics=request.metrics,
            turn_scope=request.turn_scope,
        )
        return VoiceTurnRouteContext(
            messages=messages,
            cognitive_state=cognitive_state,
            route_decision=route_decision,
            gated_state=gated_state,
            awaiting_user_reply=awaiting_user_reply,
        )


def build_voice_reply_lifecycle(
    *,
    accepted_turn_id: str,
    gate_mode: str,
    reply_in_progress: bool,
    active_conversation_awaiting_reply_sec: float,
    active_conversation_voice_sec: float,
    topic_id: str,
    history_user_text: str,
) -> VoiceReplyLifecycle:
    owner_ttl_sec = (
        active_conversation_awaiting_reply_sec
        if gate_mode == "owner_followup"
        else active_conversation_voice_sec
    )
    should_cancel_old_scope = not (gate_mode == "owner_followup" and reply_in_progress)
    return VoiceReplyLifecycle(
        accepted_turn_id=accepted_turn_id,
        should_cancel_old_scope=should_cancel_old_scope,
        owner_ttl_sec=owner_ttl_sec,
        topic_id=topic_id,
        history_user_text=history_user_text,
    )
def activate_accepted_voice_turn(
    *,
    session_key: str | None,
    room_session_key: str,
    user_id: int,
    source_turn_id: str,
    segment_id: int,
    gate_mode: str,
    reply_in_progress: bool,
    voice_reply: VoiceReplyRequest,
    voice_segment: VoiceSegment,
    transcript: TranscriptResult,
    ingress_source: str,
    queue_wait_ms: float,
    active_conversation_awaiting_reply_sec: float,
    active_conversation_voice_sec: float,
    start_new_turn: Callable[..., str],
    update_session_state: Callable[..., Any],
    set_room_owner: Callable[..., Any],
    session_partial_stt_text: MutableMapping[str, str],
    session_committed_stt_text: MutableMapping[str, str],
    partial_stt_cache: MutableMapping[str, Any],
    room_last_voice_utterance_for_merge: MutableMapping[str, Any] | None = None,
) -> VoiceAcceptedTurnActivation:
    accepted_turn_id = start_new_turn(session_key, turn_id=source_turn_id)
    if (
        room_last_voice_utterance_for_merge is not None
        and session_key is not None
        and room_session_key is not None
        and user_id is not None
    ):
        remember_voice_utterance_for_merge(
            room_last_voice_utterance_for_merge,
            room_session_key=room_session_key,
            session_key=session_key,
            user_id=user_id,
            text=transcript.final_text,
            accepted_at=time.monotonic(),
            turn_id=accepted_turn_id,
            segment_id=segment_id,
            clean_text=clean_text,
        )
    lifecycle = build_voice_reply_lifecycle(
        accepted_turn_id=accepted_turn_id,
        gate_mode=gate_mode,
        reply_in_progress=reply_in_progress,
        active_conversation_awaiting_reply_sec=active_conversation_awaiting_reply_sec,
        active_conversation_voice_sec=active_conversation_voice_sec,
        topic_id=voice_reply.topic_id,
        history_user_text=voice_reply.history_user_text,
    )
    if session_key:
        session_partial_stt_text[session_key] = ""
        session_committed_stt_text[session_key] = ""
        partial_stt_cache.pop(session_key, None)
    set_room_owner(
        room_session_key,
        user_id,
        ttl_sec=lifecycle.owner_ttl_sec,
        reason=gate_mode,
        session_key=session_key,
        turn_id=accepted_turn_id,
        segment_id=segment_id,
    )
    update_session_state(
        session_key,
        user_id=user_id,
        speaker="user",
        ttl_sec=lifecycle.owner_ttl_sec,
        awaiting_user_reply=False,
        topic_id=lifecycle.topic_id,
        user_text=lifecycle.history_user_text,
    )
    accepted_turn = build_accepted_voice_turn(
        accepted_turn_id=accepted_turn_id,
        segment=voice_segment,
        transcript=transcript,
        gate_mode=gate_mode,
        ingress_source=ingress_source,
        queue_wait_ms=queue_wait_ms,
        reply_scope_key=room_session_key,
        topic_id=lifecycle.topic_id,
        history_user_text=lifecycle.history_user_text,
    )
    return VoiceAcceptedTurnActivation(
        accepted_turn_id=accepted_turn_id,
        accepted_turn=accepted_turn,
        should_cancel_old_scope=lifecycle.should_cancel_old_scope,
        owner_ttl_sec=lifecycle.owner_ttl_sec,
        topic_id=lifecycle.topic_id,
        history_user_text=lifecycle.history_user_text,
    )
def begin_voice_reply_execution(
    *,
    room_session_key: str,
    accepted_turn_id: str,
    should_cancel_old_scope: bool,
    owner_user_id: int,
    make_turn_scope: Callable[[str], Any],
    replace_room_turn_scope: Callable[..., Any],
    attach_current_task: Callable[[Any], Any],
    set_room_reply_in_progress: Callable[..., Any],
) -> VoiceReplyExecutionState:
    turn_scope = make_turn_scope(accepted_turn_id)
    replace_room_turn_scope(
        room_session_key,
        turn_scope,
        cancel_old=should_cancel_old_scope,
    )
    turn_task = attach_current_task(turn_scope)
    set_room_reply_in_progress(room_session_key, True, owner_user_id=owner_user_id)
    return VoiceReplyExecutionState(
        turn_scope=turn_scope,
        turn_task=turn_task,
    )


def accept_voice_reply_execution(
    *,
    session_key: str | None,
    room_session_key: str,
    user_id: int,
    source_turn_id: str,
    segment_id: int,
    gate_mode: str,
    reply_in_progress: bool,
    voice_reply: VoiceReplyRequest,
    voice_segment: VoiceSegment,
    transcript: TranscriptResult,
    ingress_source: str,
    queue_wait_ms: float,
    active_conversation_awaiting_reply_sec: float,
    active_conversation_voice_sec: float,
    metrics: MutableMapping[str, Any],
    start_new_turn: Callable[..., str],
    update_session_state: Callable[..., Any],
    set_room_owner: Callable[..., Any],
    session_partial_stt_text: MutableMapping[str, str],
    session_committed_stt_text: MutableMapping[str, str],
    partial_stt_cache: MutableMapping[str, Any],
    owner_user_id: int,
    make_turn_scope: Callable[[str], Any],
    replace_room_turn_scope: Callable[..., Any],
    attach_current_task: Callable[[Any], Any],
    set_room_reply_in_progress: Callable[..., Any],
    room_last_voice_utterance_for_merge: MutableMapping[str, Any] | None = None,
) -> VoiceAcceptedReplyExecution:
    activation = activate_accepted_voice_turn(
        session_key=session_key,
        room_session_key=room_session_key,
        user_id=user_id,
        source_turn_id=source_turn_id,
        segment_id=segment_id,
        gate_mode=gate_mode,
        reply_in_progress=reply_in_progress,
        voice_reply=voice_reply,
        voice_segment=voice_segment,
        transcript=transcript,
        ingress_source=ingress_source,
        queue_wait_ms=queue_wait_ms,
        active_conversation_awaiting_reply_sec=active_conversation_awaiting_reply_sec,
        active_conversation_voice_sec=active_conversation_voice_sec,
        start_new_turn=start_new_turn,
        update_session_state=update_session_state,
        set_room_owner=set_room_owner,
        session_partial_stt_text=session_partial_stt_text,
        session_committed_stt_text=session_committed_stt_text,
        partial_stt_cache=partial_stt_cache,
        room_last_voice_utterance_for_merge=room_last_voice_utterance_for_merge,
    )
    metrics.setdefault("meta", {})["accepted_turn_contract"] = activation.accepted_turn
    metrics.setdefault("meta", {}).update(
        {
            "topic_id": activation.topic_id,
            "turn_id": activation.accepted_turn_id,
            "owner_user_id": user_id,
        }
    )
    execution_state = begin_voice_reply_execution(
        room_session_key=room_session_key,
        accepted_turn_id=activation.accepted_turn_id,
        should_cancel_old_scope=activation.should_cancel_old_scope,
        owner_user_id=owner_user_id,
        make_turn_scope=make_turn_scope,
        replace_room_turn_scope=replace_room_turn_scope,
        attach_current_task=attach_current_task,
        set_room_reply_in_progress=set_room_reply_in_progress,
    )
    return VoiceAcceptedReplyExecution(
        accepted_turn_id=activation.accepted_turn_id,
        accepted_turn=activation.accepted_turn,
        topic_id=activation.topic_id,
        turn_scope=execution_state.turn_scope,
        turn_task=execution_state.turn_task,
    )


def prepare_voice_reply_for_delivery(
    *,
    guild_id: int,
    transcript: TranscriptResult,
    voice_segment: VoiceSegment,
    session_key: str | None,
    room_session_key: str,
    owner_user_id: int | None,
    active_speaker_user_id: int | None,
    metrics: MutableMapping[str, Any],
    session_topic_seed: str,
    now_monotonic: float,
    should_reply_to_voice: Callable[..., tuple[bool, str, str | None]],
    register_drop_reason: Callable[..., Any],
    log_voice_stage: Callable[..., Any],
    log_voice_bottleneck_summary: Callable[..., Any],
    reset_session_bad_audio: Callable[[str | None], Any],
    build_voice_reply_request: Callable[..., VoiceReplyRequest],
    build_topic_id: Callable[[str | None], str],
    session_last_stt_text: MutableMapping[str, str],
    room_last_voice_reply_at: MutableMapping[str, float],
) -> VoiceReplyPreparationResult:
    ok, reason, gate_mode = should_reply_to_voice(
        guild_id,
        transcript.final_text,
        wake_detected=transcript.wake_detected,
        wake_match_mode=transcript.wake_match_mode,
        session_key=voice_segment.session_key,
        room_session_key=voice_segment.room_session_key,
        user_id=voice_segment.speaker_user_id,
        active_speaker_user_id=active_speaker_user_id,
        ignore_tts_suppression=bool(
            metrics.get("meta", {}).get("tts_interrupted_by_user_audio")
            or metrics.get("meta", {}).get("local_tts_interrupted_by_user_audio")
        ),
    )
    metrics.setdefault("meta", {}).update(
        {
            "owner_user_id": owner_user_id,
            "reply_gate_passed_by": gate_mode if ok else None,
            "reply_gate_blocked_by": None if ok else gate_mode,
        }
    )
    if not ok:
        register_drop_reason(
            metrics,
            reason,
            session_key=session_key,
            room_session_key=room_session_key,
            owner_user_id=owner_user_id,
            text=transcript.final_text,
        )
        log_voice_stage(metrics, "응답 차단", extra=f"reason={reason} gate={gate_mode}")
        log_voice_bottleneck_summary(
            metrics,
            label="voice_drop",
            extra=f"drop={reason}",
            event_name="voice_drop_summary",
        )
        return VoiceReplyPreparationResult(
            accepted=False,
            gate_mode=gate_mode,
            drop_reason=reason,
            voice_reply=None,
        )

    reset_session_bad_audio(session_key)
    if session_key:
        session_last_stt_text[session_key] = transcript.final_text
    room_last_voice_reply_at[room_session_key] = now_monotonic

    voice_reply = build_voice_reply_request(
        transcript=transcript,
        segment=voice_segment,
        gate_mode=gate_mode,
        session_topic_seed=session_topic_seed,
        build_topic_id=build_topic_id,
    )
    metrics.setdefault("meta", {}).update(
        {
            "turn_type": voice_reply.turn_type,
            "selected_path": voice_reply.selected_path,
            "reply_source": voice_reply.reply_source,
            "wake_only_turn": voice_reply.wake_only_turn,
        }
    )
    log_voice_stage(
        metrics,
        "응답 게이트 통과",
        extra=(
            f"gate={gate_mode} turn_type={voice_reply.turn_type} "
            f"path={voice_reply.selected_path} user_text={voice_reply.raw_user_text!r}"
        ),
    )
    return VoiceReplyPreparationResult(
        accepted=True,
        gate_mode=gate_mode,
        drop_reason=None,
        voice_reply=voice_reply,
    )


def update_active_speaker_for_voice_reply(
    *,
    room_session_key: str,
    speaker_user_id: int,
    voiced_ms: float,
    raw_seconds: float,
    rms: float,
    wake_detected: bool,
    metrics: MutableMapping[str, Any],
    update_room_speaker_activity: Callable[..., Any],
    pick_active_speaker: Callable[[str | None], int | None],
) -> int | None:
    update_room_speaker_activity(
        room_session_key,
        speaker_user_id,
        voiced_ms=voiced_ms,
        raw_seconds=raw_seconds,
        rms=rms,
        wake_detected=wake_detected,
    )
    active_speaker_user_id = pick_active_speaker(room_session_key)
    metrics.setdefault("meta", {})["active_speaker_user_id"] = active_speaker_user_id
    return active_speaker_user_id


def finish_voice_reply_execution(
    *,
    room_session_key: str,
    owner_user_id: int,
    turn_scope: Any,
    turn_task: Any,
    get_room_turn_scope: Callable[[str | None], Any],
    set_room_reply_in_progress: Callable[..., Any],
    detach_task: Callable[[Any, Any], None],
    clear_room_turn_scope: Callable[[str | None, Any], None],
) -> None:
    current_scope = get_room_turn_scope(room_session_key)
    if current_scope is turn_scope or current_scope is None:
        set_room_reply_in_progress(room_session_key, False, owner_user_id=owner_user_id)
    detach_task(turn_scope, turn_task)
    clear_room_turn_scope(room_session_key, turn_scope)


def finalize_delivered_voice_reply(
    *,
    guild_id: int,
    member: Any,
    session_key: str | None,
    room_session_key: str,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    voice_reply: VoiceReplyRequest,
    plain_answer: str,
    metrics: MutableMapping[str, Any],
    turn_scope: Any,
    accepted_turn_id: str,
    segment_id: int,
    gate_mode: str,
    finalize_voice_reply_side_effects: Callable[..., Any],
    log_voice_stage: Callable[..., Any],
    delivery_succeeded: bool = True,
    failure_code: str = "",
) -> None:
    finalize_voice_reply_side_effects(
        guild_id=guild_id,
        member=member,
        session_key=session_key,
        room_session_key=room_session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        voice_reply=voice_reply,
        plain_answer=plain_answer,
        metrics=metrics,
        turn_scope=turn_scope,
        accepted_turn_id=accepted_turn_id,
        segment_id=segment_id,
        delivery_succeeded=delivery_succeeded,
        failure_code=failure_code,
    )
    log_voice_stage(
        metrics,
        (
            "voice_worker_turn 완료"
            if delivery_succeeded
            else "voice_worker_turn 전달 실패 보존"
        ),
        extra=(
            f"speaker={member.display_name} gate={gate_mode}"
            if delivery_succeeded
            else f"failure_code={failure_code or 'voice_delivery_failed'}"
        ),
    )


def get_room_reply_lock(
    *,
    room_session_key: str,
    session_locks: MutableMapping[str, asyncio.Lock],
) -> asyncio.Lock:
    return session_locks.setdefault(room_session_key, asyncio.Lock())


def prepare_voice_reply_delivery_runtime(
    *,
    accepted_execution: VoiceAcceptedReplyExecution,
    room_session_key: str,
    session_locks: MutableMapping[str, asyncio.Lock],
    speaker_display_name: str,
    visible_text: Callable[[str], str],
    print_fn: Callable[..., Any],
) -> VoiceReplyDeliveryRuntime:
    async def on_final_answer(answer_text: str) -> None:
        print_fn(f"💬 [Evelyn] {visible_text(answer_text)}")

    def report_waiting_on_lock(reply: VoiceReplyRequest) -> None:
        print_fn(
            f"[VOICE WAIT] room={room_session_key} speaker={speaker_display_name} text={reply.history_user_text!r}"
        )

    def report_delivery_error(exc: Exception) -> None:
        print_fn(
            f"❌ [LLM/TTS] errorType={type(exc).__name__}"
        )

    return VoiceReplyDeliveryRuntime(
        accepted_turn_id=accepted_execution.accepted_turn_id,
        turn_scope=accepted_execution.turn_scope,
        turn_task=accepted_execution.turn_task,
        lock=get_room_reply_lock(
            room_session_key=room_session_key,
            session_locks=session_locks,
        ),
        on_final_answer=on_final_answer,
        report_waiting_on_lock=report_waiting_on_lock,
        report_delivery_error=report_delivery_error,
    )


def prepare_accepted_voice_reply_delivery_runtime(
    *,
    session_key: str | None,
    room_session_key: str,
    user_id: int,
    source_turn_id: str,
    segment_id: int,
    gate_mode: str,
    reply_in_progress: bool,
    voice_reply: VoiceReplyRequest,
    voice_segment: VoiceSegment,
    transcript: TranscriptResult,
    ingress_source: str,
    queue_wait_ms: float,
    metrics: MutableMapping[str, Any],
    active_conversation_awaiting_reply_sec: float,
    active_conversation_voice_sec: float,
    start_new_turn: Callable[..., str],
    update_session_state: Callable[..., Any],
    set_room_owner: Callable[..., Any],
    session_partial_stt_text: MutableMapping[str, str],
    session_committed_stt_text: MutableMapping[str, str],
    partial_stt_cache: MutableMapping[str, Any],
    owner_user_id: int,
    make_turn_scope: Callable[[str], Any],
    replace_room_turn_scope: Callable[..., Any],
    attach_current_task: Callable[[Any], Any],
    set_room_reply_in_progress: Callable[..., Any],
    session_locks: MutableMapping[str, asyncio.Lock],
    speaker_display_name: str,
    visible_text: Callable[[str], str],
    print_fn: Callable[..., Any],
    room_last_voice_utterance_for_merge: MutableMapping[str, Any] | None = None,
) -> VoiceReplyDeliveryRuntime:
    accepted_execution = accept_voice_reply_execution(
        session_key=session_key,
        room_session_key=room_session_key,
        user_id=user_id,
        source_turn_id=source_turn_id,
        segment_id=segment_id,
        gate_mode=gate_mode,
        reply_in_progress=reply_in_progress,
        voice_reply=voice_reply,
        voice_segment=voice_segment,
        transcript=transcript,
        ingress_source=ingress_source,
        queue_wait_ms=queue_wait_ms,
        active_conversation_awaiting_reply_sec=active_conversation_awaiting_reply_sec,
        active_conversation_voice_sec=active_conversation_voice_sec,
        metrics=metrics,
        start_new_turn=start_new_turn,
        update_session_state=update_session_state,
        set_room_owner=set_room_owner,
        session_partial_stt_text=session_partial_stt_text,
        session_committed_stt_text=session_committed_stt_text,
        partial_stt_cache=partial_stt_cache,
        room_last_voice_utterance_for_merge=room_last_voice_utterance_for_merge,
        owner_user_id=owner_user_id,
        make_turn_scope=make_turn_scope,
        replace_room_turn_scope=replace_room_turn_scope,
        attach_current_task=attach_current_task,
        set_room_reply_in_progress=set_room_reply_in_progress,
    )
    return prepare_voice_reply_delivery_runtime(
        accepted_execution=accepted_execution,
        room_session_key=room_session_key,
        session_locks=session_locks,
        speaker_display_name=speaker_display_name,
        visible_text=visible_text,
        print_fn=print_fn,
    )


async def run_locked_voice_reply_delivery(
    *,
    room_session_key: str,
    lock: asyncio.Lock,
    get_voice_client: Callable[[], Any],
    member: Any,
    voice_reply: VoiceReplyRequest,
    canned_wake_reply: str,
    accepted_turn_id: str,
    session_key: str | None,
    guild_id: int,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    metrics: MutableMapping[str, Any],
    turn_scope: Any,
    segment_id: int,
    gate_mode: str,
    on_final_answer: Callable[[str], Any] | None,
    speak_answer: Callable[..., Any],
    ask_llm_and_speak_streaming: Callable[..., Any],
    record_voice_pipeline_failure: Callable[..., Any],
    finalize_voice_reply_side_effects: Callable[..., Any],
    log_voice_stage: Callable[..., Any],
    strip_omnivoice_tags: Callable[[str], str],
    report_waiting_on_lock: Callable[[VoiceReplyRequest], None] | None,
    report_delivery_error: Callable[[Exception], None],
) -> VoiceReplyDeliveryResult | None:
    if lock.locked():
        if report_waiting_on_lock is not None:
            report_waiting_on_lock(voice_reply)
        log_voice_stage(metrics, "voice_reply_lock_wait", extra=f"room={room_session_key}")

    async with lock:
        log_voice_stage(metrics, "voice_reply_lock_acquired", extra=f"room={room_session_key}")
        vc = get_voice_client()
        if vc is None:
            metrics.setdefault("meta", {})[
                "voice_delivery_failure_code"
            ] = "voice_connection_unavailable"
            record_voice_pipeline_failure(
                "voice_connection_unavailable",
                "voice_connection_unavailable",
                metrics,
                stage="voice_connection",
            )
            finalize_delivered_voice_reply(
                guild_id=guild_id,
                member=member,
                session_key=session_key,
                room_session_key=room_session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                voice_reply=voice_reply,
                plain_answer="",
                metrics=metrics,
                turn_scope=turn_scope,
                accepted_turn_id=accepted_turn_id,
                segment_id=segment_id,
                gate_mode=gate_mode,
                finalize_voice_reply_side_effects=(
                    finalize_voice_reply_side_effects
                ),
                log_voice_stage=log_voice_stage,
                delivery_succeeded=False,
                failure_code="voice_connection_unavailable",
            )
            return None

        delivery_result = await deliver_voice_reply(
            voice_reply=voice_reply,
            canned_wake_reply=canned_wake_reply,
            vc=vc,
            accepted_turn_id=accepted_turn_id,
            session_key=session_key,
            guild_id=guild_id,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            metrics=metrics,
            turn_scope=turn_scope,
            on_final_answer=on_final_answer,
            speak_answer=speak_answer,
            ask_llm_and_speak_streaming=ask_llm_and_speak_streaming,
            record_voice_pipeline_failure=record_voice_pipeline_failure,
            log_voice_stage=log_voice_stage,
            strip_omnivoice_tags=strip_omnivoice_tags,
            report_delivery_error=report_delivery_error,
        )
        if delivery_result is None:
            failure_code = str(
                metrics.get("meta", {}).get(
                    "voice_delivery_failure_code"
                )
                or "voice_delivery_failed"
            )
            finalize_delivered_voice_reply(
                guild_id=guild_id,
                member=member,
                session_key=session_key,
                room_session_key=room_session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                voice_reply=voice_reply,
                plain_answer="",
                metrics=metrics,
                turn_scope=turn_scope,
                accepted_turn_id=accepted_turn_id,
                segment_id=segment_id,
                gate_mode=gate_mode,
                finalize_voice_reply_side_effects=(
                    finalize_voice_reply_side_effects
                ),
                log_voice_stage=log_voice_stage,
                delivery_succeeded=False,
                failure_code=failure_code,
            )
            return None

        finalize_delivered_voice_reply(
            guild_id=guild_id,
            member=member,
            session_key=session_key,
            room_session_key=room_session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            voice_reply=voice_reply,
            plain_answer=delivery_result.plain_answer_text,
            metrics=metrics,
            turn_scope=turn_scope,
            accepted_turn_id=accepted_turn_id,
            segment_id=segment_id,
            gate_mode=gate_mode,
            finalize_voice_reply_side_effects=finalize_voice_reply_side_effects,
            log_voice_stage=log_voice_stage,
        )
        return delivery_result


async def execute_accepted_voice_reply(
    *,
    delivery_runtime: VoiceReplyDeliveryRuntime,
    room_session_key: str,
    owner_user_id: int,
    get_voice_client: Callable[[], Any],
    member: Any,
    voice_reply: VoiceReplyRequest,
    canned_wake_reply: str,
    session_key: str | None,
    guild_id: int,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    metrics: MutableMapping[str, Any],
    segment_id: int,
    gate_mode: str,
    speak_answer: Callable[..., Any],
    ask_llm_and_speak_streaming: Callable[..., Any],
    record_voice_pipeline_failure: Callable[..., Any],
    finalize_voice_reply_side_effects: Callable[..., Any],
    log_voice_stage: Callable[..., Any],
    strip_omnivoice_tags: Callable[[str], str],
    get_room_turn_scope: Callable[[str | None], Any],
    set_room_reply_in_progress: Callable[..., Any],
    detach_task: Callable[[Any, Any], None],
    clear_room_turn_scope: Callable[[str | None, Any], None],
) -> VoiceReplyDeliveryResult | None:
    try:
        return await run_locked_voice_reply_delivery(
            room_session_key=room_session_key,
            lock=delivery_runtime.lock,
            get_voice_client=get_voice_client,
            member=member,
            voice_reply=voice_reply,
            canned_wake_reply=canned_wake_reply,
            accepted_turn_id=delivery_runtime.accepted_turn_id,
            session_key=session_key,
            guild_id=guild_id,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            metrics=metrics,
            turn_scope=delivery_runtime.turn_scope,
            segment_id=segment_id,
            gate_mode=gate_mode,
            on_final_answer=delivery_runtime.on_final_answer,
            speak_answer=speak_answer,
            ask_llm_and_speak_streaming=ask_llm_and_speak_streaming,
            record_voice_pipeline_failure=record_voice_pipeline_failure,
            finalize_voice_reply_side_effects=finalize_voice_reply_side_effects,
            log_voice_stage=log_voice_stage,
            strip_omnivoice_tags=strip_omnivoice_tags,
            report_waiting_on_lock=delivery_runtime.report_waiting_on_lock,
            report_delivery_error=delivery_runtime.report_delivery_error,
        )
    finally:
        finish_voice_reply_execution(
            room_session_key=room_session_key,
            owner_user_id=owner_user_id,
            turn_scope=delivery_runtime.turn_scope,
            turn_task=delivery_runtime.turn_task,
            get_room_turn_scope=get_room_turn_scope,
            set_room_reply_in_progress=set_room_reply_in_progress,
            detach_task=detach_task,
            clear_room_turn_scope=clear_room_turn_scope,
        )


async def prepare_and_execute_accepted_voice_reply(
    *,
    session_key: str | None,
    room_session_key: str,
    user_id: int,
    source_turn_id: str,
    segment_id: int,
    gate_mode: str,
    reply_in_progress: bool,
    voice_reply: VoiceReplyRequest,
    voice_segment: VoiceSegment,
    transcript: TranscriptResult,
    ingress_source: str,
    queue_wait_ms: float,
    metrics: MutableMapping[str, Any],
    active_conversation_awaiting_reply_sec: float,
    active_conversation_voice_sec: float,
    start_new_turn: Callable[..., str],
    update_session_state: Callable[..., Any],
    set_room_owner: Callable[..., Any],
    session_partial_stt_text: MutableMapping[str, str],
    session_committed_stt_text: MutableMapping[str, str],
    partial_stt_cache: MutableMapping[str, Any],
    owner_user_id: int,
    make_turn_scope: Callable[[str], Any],
    replace_room_turn_scope: Callable[..., Any],
    attach_current_task: Callable[[Any], Any],
    set_room_reply_in_progress: Callable[..., Any],
    session_locks: MutableMapping[str, asyncio.Lock],
    speaker_display_name: str,
    visible_text: Callable[[str], str],
    print_fn: Callable[..., Any],
    get_voice_client: Callable[[], Any],
    member: Any,
    canned_wake_reply: str,
    guild_id: int,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    speak_answer: Callable[..., Any],
    ask_llm_and_speak_streaming: Callable[..., Any],
    record_voice_pipeline_failure: Callable[..., Any],
    finalize_voice_reply_side_effects: Callable[..., Any],
    log_voice_stage: Callable[..., Any],
    strip_omnivoice_tags: Callable[[str], str],
    get_room_turn_scope: Callable[[str | None], Any],
    detach_task: Callable[[Any, Any], None],
    clear_room_turn_scope: Callable[[str | None, Any], None],
    room_last_voice_utterance_for_merge: MutableMapping[str, Any] | None = None,
) -> VoiceReplyDeliveryResult | None:
    delivery_runtime = prepare_accepted_voice_reply_delivery_runtime(
        session_key=session_key,
        room_session_key=room_session_key,
        user_id=user_id,
        source_turn_id=source_turn_id,
        segment_id=segment_id,
        gate_mode=gate_mode,
        reply_in_progress=reply_in_progress,
        voice_reply=voice_reply,
        voice_segment=voice_segment,
        transcript=transcript,
        ingress_source=ingress_source,
        queue_wait_ms=queue_wait_ms,
        metrics=metrics,
        active_conversation_awaiting_reply_sec=active_conversation_awaiting_reply_sec,
        active_conversation_voice_sec=active_conversation_voice_sec,
        start_new_turn=start_new_turn,
        update_session_state=update_session_state,
        set_room_owner=set_room_owner,
        session_partial_stt_text=session_partial_stt_text,
        session_committed_stt_text=session_committed_stt_text,
        partial_stt_cache=partial_stt_cache,
        owner_user_id=owner_user_id,
        make_turn_scope=make_turn_scope,
        replace_room_turn_scope=replace_room_turn_scope,
        attach_current_task=attach_current_task,
        set_room_reply_in_progress=set_room_reply_in_progress,
        session_locks=session_locks,
        speaker_display_name=speaker_display_name,
        visible_text=visible_text,
        print_fn=print_fn,
    )
    return await execute_accepted_voice_reply(
        delivery_runtime=delivery_runtime,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        get_voice_client=get_voice_client,
        member=member,
        voice_reply=voice_reply,
        canned_wake_reply=canned_wake_reply,
        session_key=session_key,
        guild_id=guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        metrics=metrics,
        segment_id=segment_id,
        gate_mode=gate_mode,
        speak_answer=speak_answer,
        ask_llm_and_speak_streaming=ask_llm_and_speak_streaming,
        record_voice_pipeline_failure=record_voice_pipeline_failure,
        finalize_voice_reply_side_effects=finalize_voice_reply_side_effects,
        log_voice_stage=log_voice_stage,
        strip_omnivoice_tags=strip_omnivoice_tags,
        get_room_turn_scope=get_room_turn_scope,
        set_room_reply_in_progress=set_room_reply_in_progress,
        detach_task=detach_task,
        clear_room_turn_scope=clear_room_turn_scope,
    )


async def handle_prepared_voice_reply(
    *,
    reply_prep: VoiceReplyPreparationResult,
    transcript_final_text: str,
    session_key: str | None,
    room_session_key: str,
    user_id: int,
    source_turn_id: str,
    segment_id: int,
    reply_in_progress: bool,
    voice_segment: VoiceSegment,
    transcript: TranscriptResult,
    ingress_source: str,
    queue_wait_ms: float,
    metrics: MutableMapping[str, Any],
    active_conversation_awaiting_reply_sec: float,
    active_conversation_voice_sec: float,
    start_new_turn: Callable[..., str],
    update_session_state: Callable[..., Any],
    set_room_owner: Callable[..., Any],
    session_partial_stt_text: MutableMapping[str, str],
    session_committed_stt_text: MutableMapping[str, str],
    partial_stt_cache: MutableMapping[str, Any],
    owner_user_id: int,
    make_turn_scope: Callable[[str], Any],
    replace_room_turn_scope: Callable[..., Any],
    attach_current_task: Callable[[Any], Any],
    set_room_reply_in_progress: Callable[..., Any],
    session_locks: MutableMapping[str, asyncio.Lock],
    speaker_display_name: str,
    visible_text: Callable[[str], str],
    print_fn: Callable[..., Any],
    get_voice_client: Callable[[], Any],
    member: Any,
    canned_wake_reply: str,
    guild_id: int,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    speak_answer: Callable[..., Any],
    ask_llm_and_speak_streaming: Callable[..., Any],
    record_voice_pipeline_failure: Callable[..., Any],
    finalize_voice_reply_side_effects: Callable[..., Any],
    log_voice_stage: Callable[..., Any],
    strip_omnivoice_tags: Callable[[str], str],
    get_room_turn_scope: Callable[[str | None], Any],
    detach_task: Callable[[Any, Any], None],
    clear_room_turn_scope: Callable[[str | None, Any], None],
) -> VoiceReplyDeliveryResult | None:
    gate_mode = reply_prep.gate_mode
    if not reply_prep.accepted or gate_mode is None or reply_prep.voice_reply is None:
        if reply_prep.drop_reason:
            print_fn(f"[STT IGNORE] {reply_prep.drop_reason}: {transcript_final_text!r}")
        return None

    return await prepare_and_execute_accepted_voice_reply(
        session_key=session_key,
        room_session_key=room_session_key,
        user_id=user_id,
        source_turn_id=source_turn_id,
        segment_id=segment_id,
        gate_mode=gate_mode,
        reply_in_progress=reply_in_progress,
        voice_reply=reply_prep.voice_reply,
        voice_segment=voice_segment,
        transcript=transcript,
        ingress_source=ingress_source,
        queue_wait_ms=queue_wait_ms,
        metrics=metrics,
        active_conversation_awaiting_reply_sec=active_conversation_awaiting_reply_sec,
        active_conversation_voice_sec=active_conversation_voice_sec,
        start_new_turn=start_new_turn,
        update_session_state=update_session_state,
        set_room_owner=set_room_owner,
        session_partial_stt_text=session_partial_stt_text,
        session_committed_stt_text=session_committed_stt_text,
        partial_stt_cache=partial_stt_cache,
        owner_user_id=owner_user_id,
        make_turn_scope=make_turn_scope,
        replace_room_turn_scope=replace_room_turn_scope,
        attach_current_task=attach_current_task,
        set_room_reply_in_progress=set_room_reply_in_progress,
        session_locks=session_locks,
        speaker_display_name=speaker_display_name,
        visible_text=visible_text,
        print_fn=print_fn,
        get_voice_client=get_voice_client,
        member=member,
        canned_wake_reply=canned_wake_reply,
        guild_id=guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        speak_answer=speak_answer,
        ask_llm_and_speak_streaming=ask_llm_and_speak_streaming,
        record_voice_pipeline_failure=record_voice_pipeline_failure,
        finalize_voice_reply_side_effects=finalize_voice_reply_side_effects,
        log_voice_stage=log_voice_stage,
        strip_omnivoice_tags=strip_omnivoice_tags,
        room_last_voice_utterance_for_merge=room_last_voice_utterance_for_merge,
        get_room_turn_scope=get_room_turn_scope,
        detach_task=detach_task,
        clear_room_turn_scope=clear_room_turn_scope,
    )


async def process_voice_reply_from_transcript(
    *,
    guild_id: int,
    transcript: TranscriptResult,
    transcript_final_text: str,
    voice_segment: VoiceSegment,
    session_key: str | None,
    room_session_key: str,
    owner_user_id: int | None,
    speaker_user_id: int,
    speaker_display_name: str,
    source_turn_id: str,
    segment_id: int,
    reply_in_progress: bool,
    voiced_ms: float,
    raw_seconds: float,
    rms: float,
    wake_detected: bool,
    metrics: MutableMapping[str, Any],
    session_topic_seed: str,
    now_monotonic: float,
    should_reply_to_voice: Callable[..., tuple[bool, str, str | None]],
    register_drop_reason: Callable[..., Any],
    log_voice_stage: Callable[..., Any],
    log_voice_bottleneck_summary: Callable[..., Any],
    reset_session_bad_audio: Callable[[str | None], Any],
    build_voice_reply_request: Callable[..., VoiceReplyRequest],
    build_topic_id: Callable[[str | None], str],
    session_last_stt_text: MutableMapping[str, str],
    room_last_voice_reply_at: MutableMapping[str, float],
    update_room_speaker_activity: Callable[..., Any],
    pick_active_speaker: Callable[[str | None], int | None],
    ingress_source: str,
    queue_wait_ms: float,
    active_conversation_awaiting_reply_sec: float,
    active_conversation_voice_sec: float,
    start_new_turn: Callable[..., str],
    update_session_state: Callable[..., Any],
    set_room_owner: Callable[..., Any],
    session_partial_stt_text: MutableMapping[str, str],
    session_committed_stt_text: MutableMapping[str, str],
    partial_stt_cache: MutableMapping[str, Any],
    make_turn_scope: Callable[[str], Any],
    replace_room_turn_scope: Callable[..., Any],
    attach_current_task: Callable[[Any], Any],
    set_room_reply_in_progress: Callable[..., Any],
    session_locks: MutableMapping[str, asyncio.Lock],
    visible_text: Callable[[str], str],
    print_fn: Callable[..., Any],
    get_voice_client: Callable[[], Any],
    member: Any,
    canned_wake_reply: str,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    speak_answer: Callable[..., Any],
    ask_llm_and_speak_streaming: Callable[..., Any],
    record_voice_pipeline_failure: Callable[..., Any],
    finalize_voice_reply_side_effects: Callable[..., Any],
    strip_omnivoice_tags: Callable[[str], str],
    get_room_turn_scope: Callable[[str | None], Any],
    detach_task: Callable[[Any, Any], None],
    clear_room_turn_scope: Callable[[str | None, Any], None],
    room_last_voice_utterance_for_merge: MutableMapping[str, Any] | None = None,
) -> VoiceReplyDeliveryResult | None:
    active_speaker_user_id = update_active_speaker_for_voice_reply(
        room_session_key=room_session_key,
        speaker_user_id=speaker_user_id,
        voiced_ms=voiced_ms,
        raw_seconds=raw_seconds,
        rms=rms,
        wake_detected=wake_detected,
        metrics=metrics,
        update_room_speaker_activity=update_room_speaker_activity,
        pick_active_speaker=pick_active_speaker,
    )
    reply_prep = prepare_voice_reply_for_delivery(
        guild_id=guild_id,
        transcript=transcript,
        voice_segment=voice_segment,
        session_key=session_key,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        active_speaker_user_id=active_speaker_user_id,
        metrics=metrics,
        session_topic_seed=session_topic_seed,
        now_monotonic=now_monotonic,
        should_reply_to_voice=should_reply_to_voice,
        register_drop_reason=register_drop_reason,
        log_voice_stage=log_voice_stage,
        log_voice_bottleneck_summary=log_voice_bottleneck_summary,
        reset_session_bad_audio=reset_session_bad_audio,
        build_voice_reply_request=build_voice_reply_request,
        build_topic_id=build_topic_id,
        session_last_stt_text=session_last_stt_text,
        room_last_voice_reply_at=room_last_voice_reply_at,
    )
    return await handle_prepared_voice_reply(
        reply_prep=reply_prep,
        transcript_final_text=transcript_final_text,
        session_key=session_key,
        room_session_key=room_session_key,
        user_id=speaker_user_id,
        source_turn_id=source_turn_id,
        segment_id=segment_id,
        reply_in_progress=reply_in_progress,
        voice_segment=voice_segment,
        transcript=transcript,
        ingress_source=ingress_source,
        queue_wait_ms=queue_wait_ms,
        metrics=metrics,
        active_conversation_awaiting_reply_sec=active_conversation_awaiting_reply_sec,
        active_conversation_voice_sec=active_conversation_voice_sec,
        start_new_turn=start_new_turn,
        update_session_state=update_session_state,
        set_room_owner=set_room_owner,
        session_partial_stt_text=session_partial_stt_text,
        session_committed_stt_text=session_committed_stt_text,
        partial_stt_cache=partial_stt_cache,
        owner_user_id=speaker_user_id,
        make_turn_scope=make_turn_scope,
        replace_room_turn_scope=replace_room_turn_scope,
        attach_current_task=attach_current_task,
        set_room_reply_in_progress=set_room_reply_in_progress,
        session_locks=session_locks,
        speaker_display_name=speaker_display_name,
        visible_text=visible_text,
        print_fn=print_fn,
        get_voice_client=get_voice_client,
        member=member,
        canned_wake_reply=canned_wake_reply,
        guild_id=guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        speak_answer=speak_answer,
        ask_llm_and_speak_streaming=ask_llm_and_speak_streaming,
        record_voice_pipeline_failure=record_voice_pipeline_failure,
        finalize_voice_reply_side_effects=finalize_voice_reply_side_effects,
        log_voice_stage=log_voice_stage,
        strip_omnivoice_tags=strip_omnivoice_tags,
        room_last_voice_utterance_for_merge=room_last_voice_utterance_for_merge,
        get_room_turn_scope=get_room_turn_scope,
        detach_task=detach_task,
        clear_room_turn_scope=clear_room_turn_scope,
    )


async def process_voice_reply_from_transcript_context(
    *,
    context: VoiceTranscriptReplyContext,
    deps: VoiceTranscriptReplyDeps,
) -> VoiceReplyDeliveryResult | None:
    return await process_voice_reply_from_transcript(
        guild_id=context.guild_id,
        transcript=context.transcript,
        transcript_final_text=context.transcript.final_text,
        voice_segment=context.voice_segment,
        session_key=context.session_key,
        room_session_key=context.room_session_key,
        owner_user_id=context.owner_user_id,
        speaker_user_id=int(context.member.id),
        speaker_display_name=str(context.member.display_name),
        source_turn_id=context.source_turn_id,
        segment_id=context.segment_id,
        reply_in_progress=context.reply_in_progress,
        voiced_ms=context.voiced_ms,
        raw_seconds=context.raw_seconds,
        rms=context.rms,
        wake_detected=context.wake_detected,
        metrics=context.metrics,
        session_topic_seed=context.session_topic_seed,
        now_monotonic=context.now_monotonic,
        should_reply_to_voice=deps.should_reply_to_voice,
        register_drop_reason=deps.register_drop_reason,
        log_voice_stage=deps.log_voice_stage,
        log_voice_bottleneck_summary=deps.log_voice_bottleneck_summary,
        reset_session_bad_audio=deps.reset_session_bad_audio,
        build_voice_reply_request=deps.build_voice_reply_request,
        build_topic_id=deps.build_topic_id,
        session_last_stt_text=deps.session_last_stt_text,
        room_last_voice_reply_at=deps.room_last_voice_reply_at,
        update_room_speaker_activity=deps.update_room_speaker_activity,
        pick_active_speaker=deps.pick_active_speaker,
        ingress_source=context.ingress_source,
        queue_wait_ms=context.queue_wait_ms,
        active_conversation_awaiting_reply_sec=context.active_conversation_awaiting_reply_sec,
        active_conversation_voice_sec=context.active_conversation_voice_sec,
        start_new_turn=deps.start_new_turn,
        update_session_state=deps.update_session_state,
        set_room_owner=deps.set_room_owner,
        session_partial_stt_text=deps.session_partial_stt_text,
        session_committed_stt_text=deps.session_committed_stt_text,
        partial_stt_cache=deps.partial_stt_cache,
        make_turn_scope=deps.make_turn_scope,
        replace_room_turn_scope=deps.replace_room_turn_scope,
        attach_current_task=deps.attach_current_task,
        set_room_reply_in_progress=deps.set_room_reply_in_progress,
        session_locks=deps.session_locks,
        visible_text=deps.visible_text,
        print_fn=deps.print_fn,
        get_voice_client=deps.get_voice_client,
        member=context.member,
        canned_wake_reply=context.canned_wake_reply,
        room_key=context.room_key,
        person_key=context.person_key,
        session_memory_key=context.session_memory_key,
        speak_answer=deps.speak_answer,
        ask_llm_and_speak_streaming=deps.ask_llm_and_speak_streaming,
        record_voice_pipeline_failure=deps.record_voice_pipeline_failure,
        finalize_voice_reply_side_effects=deps.finalize_voice_reply_side_effects,
        strip_omnivoice_tags=deps.strip_omnivoice_tags,
        room_last_voice_utterance_for_merge=deps.room_last_voice_utterance_for_merge,
        get_room_turn_scope=deps.get_room_turn_scope,
        detach_task=deps.detach_task,
        clear_room_turn_scope=deps.clear_room_turn_scope,
    )


async def deliver_voice_reply(
    *,
    voice_reply: VoiceReplyRequest,
    canned_wake_reply: str,
    vc: Any,
    accepted_turn_id: str,
    session_key: str | None,
    guild_id: int,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    metrics: MutableMapping[str, Any],
    turn_scope: Any,
    on_final_answer: Callable[[str], Any] | None,
    speak_answer: Callable[..., Any],
    ask_llm_and_speak_streaming: Callable[..., Any],
    record_voice_pipeline_failure: Callable[..., Any],
    log_voice_stage: Callable[..., Any],
    strip_omnivoice_tags: Callable[[str], str],
    report_delivery_error: Callable[[Exception], None],
) -> VoiceReplyDeliveryResult | None:
    try:
        memory_command_matched = False
        memory_command_reply = ""
        memory_write_receipt = None
        memory_command_error = ""
        if is_explicit_memory_confirmation_command(
            voice_reply.history_user_text
        ):
            (
                memory_command_matched,
                memory_command_reply,
                memory_write_receipt,
                memory_command_error,
            ) = await asyncio.to_thread(
                execute_explicit_memory_confirmation,
                voice_reply.history_user_text,
                action_id=accepted_turn_id,
                evidence_turn_id=accepted_turn_id,
                source="discord-user",
            )
        if memory_command_matched:
            answer = memory_command_reply
            metrics.setdefault("meta", {}).update(
                {
                    "turn_type": "memory_confirmation",
                    "selected_path": (
                        "explicit_memory_confirmation"
                    ),
                    "reply_source": (
                        "explicit_memory_confirmation"
                    ),
                    "memory_write_receipt": (
                        memory_write_receipt
                    ),
                    "memory_write_error": memory_command_error,
                }
            )
            if on_final_answer is not None:
                await on_final_answer(answer)
            try:
                await speak_answer(
                    vc,
                    answer,
                    turn_id=accepted_turn_id,
                    session_key=session_key,
                    turn_scope=turn_scope,
                    metrics=metrics,
                )
            except Exception as e:
                record_voice_pipeline_failure(
                    "tts_playback_failed",
                    e,
                    metrics,
                    stage="memory_confirmation_speak_answer",
                )
                raise
            used_wake_only_reply = False
        elif voice_reply.wake_only_turn:
            answer = canned_wake_reply
            metrics.setdefault("meta", {}).update(
                {
                    "turn_type": voice_reply.turn_type,
                    "selected_path": voice_reply.selected_path,
                    "reply_source": "canned_wake_reply",
                }
            )
            log_voice_stage(metrics, "웨이크 전용 턴 canned reply", extra=f"answer={answer!r}")
            if on_final_answer is not None:
                await on_final_answer(answer)
            try:
                await speak_answer(
                    vc,
                    answer,
                    turn_id=accepted_turn_id,
                    session_key=session_key,
                    turn_scope=turn_scope,
                    metrics=metrics,
                )
            except Exception as e:
                record_voice_pipeline_failure("tts_playback_failed", e, metrics, stage="wake_only_speak_answer")
                raise
            used_wake_only_reply = True
        else:
            metrics.setdefault("meta", {}).update(
                {
                    "turn_type": voice_reply.turn_type,
                    "selected_path": voice_reply.selected_path,
                    "reply_source": "llm_streaming",
                }
            )
            try:
                answer = await ask_llm_and_speak_streaming(
                    vc,
                    voice_reply.prompt_user_text,
                    guild_id=guild_id,
                    on_final_answer=on_final_answer,
                    session_key=session_key,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    source="voice",
                    debug_text=voice_reply.history_user_text,
                    metrics=metrics,
                    turn_scope=turn_scope,
                )
            except Exception as e:
                record_voice_pipeline_failure("voice_delivery_failed", e, metrics, stage="llm_tts_delivery")
                raise
            used_wake_only_reply = False
        log_voice_stage(metrics, "LLM/TTS 완료", extra=f"answer_len={len(answer)}")
    except Exception as e:
        metrics.setdefault("meta", {})[
            "voice_delivery_failure_code"
        ] = "voice_delivery_failed"
        report_delivery_error(e)
        return None

    answer = clean_text(answer)
    if not answer:
        metrics.setdefault("meta", {})[
            "voice_delivery_failure_code"
        ] = "voice_delivery_empty"
        record_voice_pipeline_failure(
            "voice_delivery_empty",
            "voice_delivery_empty",
            metrics,
            stage="answer_finalize",
        )
        log_voice_stage(metrics, "최종 답변 비어있음")
        return None

    plain_answer = strip_omnivoice_tags(answer)
    if not plain_answer:
        plain_answer = answer

    return VoiceReplyDeliveryResult(
        answer_text=answer,
        plain_answer_text=plain_answer,
        used_wake_only_reply=used_wake_only_reply,
    )


@dataclass(frozen=True)
class VoiceIngressDequeuePlan:
    queue_wait_ms: float
    max_age_ms: float
    queue_depth_at_dequeue: int
    should_drop_stale: bool
    drop_reason: str | None = None


@dataclass(frozen=True)
class VoiceIngressEnqueueResult:
    accepted: bool
    reason: str | None = None
    dropped_oldest_item: dict[str, Any] | None = None


def build_voice_ingress_item(
    *,
    member: Any,
    pcm_bytes: bytes,
    debug_meta: Mapping[str, Any] | None,
    session_key: str,
    room_session_key: str,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    turn_id: str,
    segment_id: int,
    ingress_during_reply: bool,
    owner_user_id_on_ingress: int | None,
    queue_depth_at_enqueue: int,
    enqueued_at: float,
) -> dict[str, Any]:
    enqueue_meta = dict(debug_meta or {})
    enqueue_meta.update(
        {
            "turn_id": turn_id,
            "segment_id": segment_id,
            "room_session_key": room_session_key,
            "voice_queue_depth_at_enqueue": queue_depth_at_enqueue,
        }
    )
    return {
        "member": member,
        "pcm_bytes": pcm_bytes,
        "debug_meta": enqueue_meta,
        "session_key": session_key,
        "room_session_key": room_session_key,
        "room_key": room_key,
        "person_key": person_key,
        "session_memory_key": session_memory_key,
        "turn_id": turn_id,
        "segment_id": segment_id,
        "ingress_during_reply": ingress_during_reply,
        "owner_user_id_on_ingress": owner_user_id_on_ingress,
        "enqueued_at": enqueued_at,
    }


def evaluate_voice_ingress_dequeue(
    item: Mapping[str, Any],
    *,
    now_monotonic: float,
    max_age_sec: float,
    queue_depth_at_dequeue: int,
) -> VoiceIngressDequeuePlan:
    enqueued_at = float(item.get("enqueued_at") or now_monotonic)
    queue_wait_ms = max(0.0, (now_monotonic - enqueued_at) * 1000.0)
    max_age_ms = max(0.0, max_age_sec * 1000.0)
    should_drop_stale = bool(max_age_ms and queue_wait_ms > max_age_ms)
    return VoiceIngressDequeuePlan(
        queue_wait_ms=queue_wait_ms,
        max_age_ms=max_age_ms,
        queue_depth_at_dequeue=queue_depth_at_dequeue,
        should_drop_stale=should_drop_stale,
        drop_reason="voice_queue_stale" if should_drop_stale else None,
    )


def apply_voice_ingress_dequeue_debug_meta(item: MutableMapping[str, Any], plan: VoiceIngressDequeuePlan) -> None:
    debug_meta = item.get("debug_meta")
    if not isinstance(debug_meta, dict):
        return
    debug_meta["queue_wait_ms"] = round(plan.queue_wait_ms, 1)
    debug_meta["voice_queue_depth_at_dequeue"] = plan.queue_depth_at_dequeue
    if plan.drop_reason:
        debug_meta["voice_drop_reason"] = plan.drop_reason


def enqueue_voice_ingress_item(
    queue: asyncio.Queue,
    item: dict[str, Any],
    *,
    drop_oldest_on_full: bool,
) -> VoiceIngressEnqueueResult:
    try:
        queue.put_nowait(item)
        return VoiceIngressEnqueueResult(accepted=True)
    except asyncio.QueueFull:
        if drop_oldest_on_full:
            try:
                dropped = queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                dropped = None
            else:
                try:
                    queue.put_nowait(item)
                    return VoiceIngressEnqueueResult(
                        accepted=True,
                        reason="queue_full_drop_oldest",
                        dropped_oldest_item=dropped if isinstance(dropped, dict) else None,
                    )
                except asyncio.QueueFull:
                    pass
        return VoiceIngressEnqueueResult(accepted=False, reason="queue_full")


def build_accepted_voice_turn(
    *,
    accepted_turn_id: str,
    segment: VoiceSegment,
    transcript: TranscriptResult | None,
    gate_mode: str,
    ingress_source: str,
    queue_wait_ms: float,
    reply_scope_key: str,
    topic_id: str | None,
    history_user_text: str | None,
) -> AcceptedVoiceTurn:
    return AcceptedVoiceTurn(
        accepted_turn_id=accepted_turn_id,
        segment=segment,
        transcript=transcript,
        gate_mode=gate_mode,
        ingress_source=ingress_source,
        queue_wait_ms=queue_wait_ms,
        accepted_at_unix=time.time(),
        reply_scope_key=reply_scope_key,
        metadata={
            "topic_id": topic_id,
            "history_user_text": history_user_text,
        },
    )


def build_rejected_voice_turn(
    *,
    segment: VoiceSegment,
    ingress_source: str,
    drop_reason: str,
    queue_wait_ms: float,
    topic_id: str | None,
    gate_mode: str | None,
    owner_user_id: int | None,
    detail_text: str | None = None,
) -> RejectedVoiceTurn:
    return RejectedVoiceTurn(
        segment=segment,
        ingress_source=ingress_source,
        drop_reason=drop_reason,
        drop_detail=clean_text(str(detail_text or "")) or None,
        queue_wait_ms=queue_wait_ms,
        rejected_at_unix=time.time(),
        metadata={
            "gate_mode": gate_mode,
            "owner_user_id": owner_user_id,
            "topic_id": topic_id,
        },
    )
