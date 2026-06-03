import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from evelyn_core.query_intents import should_force_search_query
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
    needs_main_llm: bool = True
    needs_memory: bool = True
    needs_runtime_state: bool = True
    needs_minecraft_state: bool = False
    needs_vision: bool = False
    needs_skill_graph: bool = False
    needs_long_context: bool = False
    needs_search: bool = False
    needs_tts: bool = True
    response_mode: str = "normal"
    priority: str = "latency"
    should_interrupt_delivery: bool = False
    ask_mode: str = "none"
    max_question_count: int = 0
    question_hint: str | None = None
    question_reason: str | None = None
    question_source: str = "none"


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
    turn_type: str
    selected_path: str
    reply_source: str
    topic_id: str


def classify_dialogue_turn(raw_user_text: str, *, wake_only_turn: bool = False) -> str:
    if wake_only_turn:
        return "wake_call"

    text = clean_text(raw_user_text).lower()
    compact = re.sub(r"[\s,.!?~…？]+", "", text)
    if not compact:
        return "repair"

    if compact in {"응", "어", "ㅇㅇ", "그래", "맞아", "아니", "ㄴㄴ", "해줘", "하지마", "멈춰"}:
        return "short_confirm"

    minecraft_markers = (
        "마크",
        "마인크래프트",
        "minecraft",
        "나무",
        "돌",
        "철",
        "횃불",
        "곡괭이",
        "도끼",
        "인벤",
        "캐",
        "만들",
        "찾",
    )
    minecraft_status_markers = ("상태", "뭐 하고", "뭐해", "어디", "진행", "인벤")
    if any(marker in text for marker in minecraft_markers):
        if any(marker in text for marker in minecraft_status_markers):
            return "runtime_status"
        return "minecraft_command"

    runtime_status_markers = (
        "지금 뭐",
        "뭐해",
        "뭐 하고",
        "뭐하고",
        "상태",
        "진행",
        "어디까지",
    )
    if any(marker in text for marker in runtime_status_markers):
        return "runtime_status"

    casual_check_markers = (
        "있어",
        "듣고",
        "괜찮",
        "왜 불렀",
        "왜불렀",
        "부른",
        "불렀",
    )
    if any(marker in text for marker in casual_check_markers):
        return "casual_check"

    if should_force_search_query(text):
        return "knowledge_or_search"

    knowledge_markers = (
        "검색",
        "찾아봐",
        "찾아줘",
        "인터넷",
        "웹에서",
        "알려줘",
        "설명해",
        "정리해",
    )
    if any(marker in text for marker in knowledge_markers):
        return "knowledge_or_search"

    if len(compact) <= 1:
        return "repair"

    return "conversation"


def selected_path_for_turn(turn_type: str, *, wake_only_turn: bool = False) -> str:
    if wake_only_turn or turn_type == "wake_call":
        return "cached_audio_fast_path"
    if turn_type in {"casual_check", "short_confirm"}:
        return "light_dialogue_path"
    if turn_type == "runtime_status":
        return "runtime_status_path"
    if turn_type == "minecraft_command":
        return "minecraft_action_path"
    if turn_type == "knowledge_or_search":
        return "search_or_long_answer_path"
    if turn_type == "repair":
        return "repair_path"
    return "main_conversation_path"


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
    needs_main_llm: bool = True,
    needs_memory: bool = True,
    needs_runtime_state: bool = True,
    needs_minecraft_state: bool = False,
    needs_vision: bool = False,
    needs_skill_graph: bool = False,
    needs_long_context: bool = False,
    needs_search: bool = False,
    needs_tts: bool = True,
    response_mode: str = "normal",
    priority: str = "latency",
    should_interrupt_delivery: bool = False,
    ask_mode: str = "none",
    max_question_count: int = 0,
    question_hint: str | None = None,
    question_reason: str | None = None,
    question_source: str = "none",
) -> RouteDecision:
    cleaned_ask_mode = clean_text(ask_mode) or "none"
    cleaned_question_source = clean_text(question_source) or "none"
    return RouteDecision(
        action=action,
        route=route,
        source=source,
        prompt_text=prompt_text,
        user_visible_preface=user_visible_preface,
        needs_main_llm=bool(needs_main_llm),
        needs_memory=bool(needs_memory),
        needs_runtime_state=bool(needs_runtime_state),
        needs_minecraft_state=bool(needs_minecraft_state),
        needs_vision=bool(needs_vision),
        needs_skill_graph=bool(needs_skill_graph),
        needs_long_context=bool(needs_long_context),
        needs_search=needs_search,
        needs_tts=bool(needs_tts),
        response_mode=clean_text(response_mode) or "normal",
        priority=clean_text(priority) or "latency",
        should_interrupt_delivery=should_interrupt_delivery,
        ask_mode=cleaned_ask_mode,
        max_question_count=max(0, int(max_question_count or 0)),
        question_hint=clean_text(question_hint or "") or None,
        question_reason=clean_text(question_reason or "") or None,
        question_source=cleaned_question_source,
    )


def route_decision_policy_dict(route_decision: RouteDecision) -> dict[str, Any]:
    return {
        "needs_main_llm": bool(route_decision.needs_main_llm),
        "needs_memory": bool(route_decision.needs_memory),
        "needs_runtime_state": bool(route_decision.needs_runtime_state),
        "needs_minecraft_state": bool(route_decision.needs_minecraft_state),
        "needs_vision": bool(route_decision.needs_vision),
        "needs_skill_graph": bool(route_decision.needs_skill_graph),
        "needs_long_context": bool(route_decision.needs_long_context),
        "needs_search": bool(route_decision.needs_search),
        "needs_tts": bool(route_decision.needs_tts),
        "response_mode": route_decision.response_mode,
        "priority": route_decision.priority,
        "ask_mode": route_decision.ask_mode,
        "max_question_count": int(route_decision.max_question_count),
        "question_hint": route_decision.question_hint,
        "question_reason": route_decision.question_reason,
        "question_source": route_decision.question_source,
    }


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
    turn_type = classify_dialogue_turn(raw_user_text or transcript.final_text, wake_only_turn=wake_only_turn)
    selected_path = selected_path_for_turn(turn_type, wake_only_turn=wake_only_turn)
    reply_source = "canned_wake_reply" if wake_only_turn else "pipeline"
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
        turn_type=turn_type,
        selected_path=selected_path,
        reply_source=reply_source,
        topic_id=topic_id,
    )
