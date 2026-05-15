from dataclasses import dataclass, field
from typing import Any

import numpy as np

from evelyn_core.text import clean_text, clean_tts_text, strip_voice_wake_word


@dataclass(frozen=True)
class VoiceSegment:
    guild_id: int
    room_session_key: str
    session_key: str
    speaker_user_id: int
    speaker_name: str
    audio16k: np.ndarray
    sampling_rate: int
    duration_sec: float
    segment_id: int
    owner_user_id: int | None


@dataclass(frozen=True)
class TranscriptResult:
    wake_detected: bool
    wake_match_mode: str | None
    wake_alias: str | None
    probe_text: str
    confirm_text: str
    reject_reason: str | None
    partial_text: str
    committed_text: str
    final_text: str
    speaker_user_id: int | None
    duration_sec: float


@dataclass(frozen=True)
class RouteDecision:
    action: str
    route: str
    source: str
    prompt_text: str
    user_visible_preface: str | None = None
    needs_search: bool = False
    should_interrupt_delivery: bool = False


@dataclass(frozen=True)
class ActionResult:
    action: str
    answer_text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnswerPayload:
    display_text: str
    spoken_text: str
    should_store_history: bool = True
    followup_state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeliveryPlan:
    text_message: str | None
    tts_chunks: tuple[str, ...] = ()
    should_play_voice: bool = False


@dataclass(frozen=True)
class VoiceReplyRequest:
    transcript: TranscriptResult
    segment: VoiceSegment
    gate_mode: str
    raw_user_text: str
    prompt_user_text: str
    history_user_text: str
    wake_only_turn: bool
    topic_id: str


def build_voice_segment(
    *,
    guild_id: int,
    room_session_key: str,
    session_key: str,
    speaker_user_id: int,
    speaker_name: str,
    audio16k: np.ndarray,
    sampling_rate: int,
    duration_sec: float,
    segment_id: int,
    owner_user_id: int | None,
) -> VoiceSegment:
    return VoiceSegment(
        guild_id=guild_id,
        room_session_key=room_session_key,
        session_key=session_key,
        speaker_user_id=speaker_user_id,
        speaker_name=speaker_name,
        audio16k=audio16k,
        sampling_rate=sampling_rate,
        duration_sec=duration_sec,
        segment_id=segment_id,
        owner_user_id=owner_user_id,
    )


def build_transcript_result(
    *,
    wake_detected: bool,
    wake_match_mode: str | None,
    wake_alias: str | None,
    probe_text: str,
    confirm_text: str,
    reject_reason: str | None,
    partial_text: str,
    committed_text: str,
    final_text: str,
    speaker_user_id: int | None,
    duration_sec: float,
) -> TranscriptResult:
    return TranscriptResult(
        wake_detected=wake_detected,
        wake_match_mode=wake_match_mode,
        wake_alias=wake_alias,
        probe_text=probe_text,
        confirm_text=confirm_text,
        reject_reason=reject_reason,
        partial_text=partial_text,
        committed_text=committed_text,
        final_text=final_text,
        speaker_user_id=speaker_user_id,
        duration_sec=duration_sec,
    )


def build_route_decision(
    *,
    action: str,
    route: str,
    source: str,
    prompt_text: str,
    user_visible_preface: str | None = None,
    needs_search: bool = False,
    should_interrupt_delivery: bool = False,
) -> RouteDecision:
    return RouteDecision(
        action=action,
        route=route,
        source=source,
        prompt_text=prompt_text,
        user_visible_preface=user_visible_preface,
        needs_search=needs_search,
        should_interrupt_delivery=should_interrupt_delivery,
    )


def build_answer_payload(
    *,
    display_text: str,
    spoken_text: str | None = None,
    should_store_history: bool = True,
    followup_state: dict[str, Any] | None = None,
) -> AnswerPayload:
    return AnswerPayload(
        display_text=display_text,
        spoken_text=spoken_text if spoken_text is not None else display_text,
        should_store_history=should_store_history,
        followup_state=dict(followup_state or {}),
    )


def build_answer_payload_from_text(
    answer_text: str,
    *,
    spoken_text: str | None = None,
    should_store_history: bool = True,
    followup_state: dict[str, Any] | None = None,
) -> AnswerPayload:
    cleaned_display = clean_text(answer_text)
    cleaned_spoken = clean_tts_text(spoken_text if spoken_text is not None else answer_text)
    return build_answer_payload(
        display_text=cleaned_display,
        spoken_text=cleaned_spoken or cleaned_display,
        should_store_history=should_store_history,
        followup_state=followup_state,
    )


def build_action_result(
    *,
    action: str,
    answer_text: str,
    metadata: dict[str, Any] | None = None,
) -> ActionResult:
    return ActionResult(
        action=action,
        answer_text=clean_text(answer_text),
        metadata=dict(metadata or {}),
    )


def action_result_to_answer_payload(action_result: ActionResult) -> AnswerPayload:
    return build_answer_payload_from_text(
        action_result.answer_text,
        followup_state=action_result.metadata,
    )


def build_delivery_plan(
    answer_payload: AnswerPayload,
    *,
    include_voice: bool,
    text_message: str | None = None,
    split_chunks: Any = None,
) -> DeliveryPlan:
    spoken_text = clean_tts_text(answer_payload.spoken_text)
    tts_chunks: list[str] = []
    if include_voice and spoken_text:
        if split_chunks is not None:
            ready_chunks, _ = split_chunks(spoken_text, force=True)
        else:
            ready_chunks = [spoken_text]
        if not ready_chunks and spoken_text:
            ready_chunks = [spoken_text]
        tts_chunks = [chunk for chunk in ready_chunks if chunk]
    return DeliveryPlan(
        text_message=text_message if text_message is not None else answer_payload.display_text,
        tts_chunks=tuple(tts_chunks),
        should_play_voice=include_voice and bool(tts_chunks),
    )


def build_voice_reply_request(
    *,
    transcript: TranscriptResult,
    segment: VoiceSegment,
    gate_mode: str,
    session_topic_seed: str = "",
    build_topic_id: Any = None,
) -> VoiceReplyRequest:
    raw_user_text = strip_voice_wake_word(transcript.final_text)
    wake_only_turn = not bool(clean_text(raw_user_text))
    history_user_text = raw_user_text or transcript.final_text
    prompt_user_text = raw_user_text or "사용자가 너를 이름만 불렀다. 아주 짧고 자연스럽게 반응해라."
    topic_id = build_topic_id(history_user_text, session_topic_seed) if build_topic_id is not None else history_user_text
    return VoiceReplyRequest(
        transcript=transcript,
        segment=segment,
        gate_mode=gate_mode,
        raw_user_text=raw_user_text,
        prompt_user_text=prompt_user_text,
        history_user_text=history_user_text,
        wake_only_turn=wake_only_turn,
        topic_id=topic_id,
    )
