from __future__ import annotations

import time

from .config import ASK_CONFIDENCE_THRESHOLD_TEXT, ASK_CONFIDENCE_THRESHOLD_VOICE
from .conversation_memory_receipt import sanitize_memory_receipt_ref
from .memory import cognitive_state_path, normalize_cognitive_state, read_json_file
from .text import clean_text, is_user_echo_answer


COGNITIVE_STATE_PROVENANCE_SCHEMA = (
    "cognitive-state.provenance.v1"
)


def _validated_cached_cognitive_state(
    value: object,
) -> dict | None:
    """Accept only cached state proven independent of recalled memory.

    Existing cognitive files predate typed provenance and may contain text
    derived from notes that were later corrected or deleted.  They are
    intentionally ignored until writers persist a verifiable source receipt.
    """

    if not isinstance(value, dict):
        return None
    provenance = value.get("memoryProvenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("schema")
        != COGNITIVE_STATE_PROVENANCE_SCHEMA
    ):
        return None
    receipt_ref = sanitize_memory_receipt_ref(
        provenance.get("memoryReceiptRef")
    )
    if (
        receipt_ref is None
        or receipt_ref.get("state") != "not_used"
    ):
        return None
    return normalize_cognitive_state(value)


def build_fast_cognitive_state(
    user_text: str,
    *,
    action: str,
    current_state: dict | None = None,
    reason_brief: str = "fast_path",
    now: float | None = None,
) -> dict:
    base = normalize_cognitive_state(current_state or {})
    cleaned = clean_text(user_text)
    hint = "짧고 자연스럽게 답해라."
    if action == "wait":
        hint = "지금은 더 듣는 쪽이 자연스럽다. 아주 짧게 반응해라."
    previous_ids = base.get("retrieved_context_ids") or []
    state = {
        "action": action if action in {"answer", "ask", "wait", "search_then_answer"} else "answer",
        "confidence": 0.92 if action == "answer" else 0.82,
        "user_intent": cleaned,
        "state_summary": cleaned,
        "question_for_user": "",
        "main_prompt_hint": hint,
        "reason_brief": reason_brief,
        "retrieved_context_ids": previous_ids if isinstance(previous_ids, list) else [],
        "updated_at": int(time.time() if now is None else now),
    }
    return normalize_cognitive_state(state)


def ask_confidence_threshold_for_source(source: str) -> float:
    return ASK_CONFIDENCE_THRESHOLD_VOICE if source == "voice" else ASK_CONFIDENCE_THRESHOLD_TEXT


def apply_ask_gating(cognitive_state: dict | None = None, *, source: str = "text") -> dict:
    state = normalize_cognitive_state(cognitive_state or {})
    threshold = ask_confidence_threshold_for_source(source)

    if state.get("action") == "ask":
        question_for_user = clean_text(str(state.get("question_for_user", "")))
        confidence = float(state.get("confidence", 0.0) or 0.0)
        if not question_for_user or confidence < threshold:
            gated = dict(state)
            gated["action"] = "wait" if source == "voice" else "answer"
            reason = clean_text(str(gated.get("reason_brief", "")))
            gate_note = f"ask_gated_{source}_{confidence:.2f}_lt_{threshold:.2f}"
            gated["reason_brief"] = clean_text(f"{reason} {gate_note}") if reason else gate_note
            return gated

    return state


def policy_response_for_state(
    cognitive_state: dict | None = None,
    *,
    source: str = "text",
    user_text: str = "",
) -> str | None:
    state = apply_ask_gating(cognitive_state, source=source)
    action = state.get("action", "answer")

    if action == "ask":
        question = clean_text(str(state.get("question_for_user", "")))
        if question and not is_user_echo_answer(user_text, question):
            return question
        return None

    if action == "wait":
        return "응, 계속 말해줘." if source == "voice" else "잠깐, 이어서 말해줘."

    if action == "search_then_answer":
        return "금방 찾아보고 바로 알려줄게."

    return None


def build_cognitive_fallback_state(
    *,
    current_state: dict | None = None,
    user_text: str = "",
    now: float | None = None,
) -> dict:
    state = normalize_cognitive_state(current_state or {})
    if current_state and (
        state.get("state_summary")
        or state.get("user_intent")
        or state.get("main_prompt_hint")
        or state.get("reason_brief")
    ):
        return state
    return normalize_cognitive_state(
        {
            "action": "answer",
            "confidence": 0.5,
            "user_intent": clean_text(user_text),
            "state_summary": clean_text(user_text),
            "question_for_user": "",
            "main_prompt_hint": "짧고 자연스럽게 답해라.",
            "reason_brief": "fallback",
            "retrieved_context_ids": [],
            "updated_at": int(time.time() if now is None else now),
        }
    )


def finalize_cognitive_state(
    result: dict | None,
    *,
    current_state: dict | None = None,
    user_text: str = "",
    now: float | None = None,
) -> dict:
    state = normalize_cognitive_state(result or {})
    base = normalize_cognitive_state(current_state or {})
    if not state.get("state_summary"):
        state["state_summary"] = base.get("state_summary", "") or clean_text(user_text)
    if not state.get("main_prompt_hint"):
        state["main_prompt_hint"] = "짧고 자연스럽게 답해라."
    state["updated_at"] = int(time.time() if now is None else now)
    return normalize_cognitive_state(state)


def read_layered_cognitive_state(
    guild_id: int,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> dict | None:
    if session_memory_key:
        session_state = read_json_file(cognitive_state_path(guild_id, scope_type="session", scope_key=session_memory_key))
        validated = _validated_cached_cognitive_state(session_state)
        if validated is not None:
            return validated
    if person_key:
        person_state = read_json_file(cognitive_state_path(guild_id, scope_type="person", scope_key=person_key))
        validated = _validated_cached_cognitive_state(person_state)
        if validated is not None:
            return validated
    if room_key:
        room_state = read_json_file(cognitive_state_path(guild_id, scope_type="room", scope_key=room_key))
        validated = _validated_cached_cognitive_state(room_state)
        if validated is not None:
            return validated
    guild_state = read_json_file(cognitive_state_path(guild_id))
    return _validated_cached_cognitive_state(guild_state)


def read_cached_cognitive_state(
    guild_id: int | None,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> dict | None:
    if guild_id is None:
        return None
    return read_layered_cognitive_state(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )


__all__ = [
    "COGNITIVE_STATE_PROVENANCE_SCHEMA",
    "apply_ask_gating",
    "ask_confidence_threshold_for_source",
    "build_cognitive_fallback_state",
    "build_fast_cognitive_state",
    "finalize_cognitive_state",
    "policy_response_for_state",
    "read_cached_cognitive_state",
    "read_layered_cognitive_state",
]
