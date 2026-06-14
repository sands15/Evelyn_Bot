from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, MutableMapping


_INCOMPLETE_SUFFIXES = (
    "and",
    "but",
    "or",
    "so",
    "because",
    "then",
    "also",
    "\uadf8\ub9ac\uace0",
    "\uadfc\ub370",
    "\uadf8\ub7f0\ub370",
    "\uc544\ub2c8",
    "\uadf8\ub7ec\uba74",
    "\uadf8\ub7fc",
    "\uadf8\ub798\uc11c",
    "\uadf8\uac8c",
    "\uc774\uac8c",
    "\uc800\uac8c",
    "\ud574\uc11c",
    "\ub2c8\uae4c",
    "\uc73c\uba74",
    "\uba74",
    "\uace0",
)
_COMPLETE_QUESTION_SUFFIXES = (
    "?",
    "\ubb50\uc57c",
    "\ubb50\uc9c0",
    "\ub204\uad6c\uc57c",
    "\uc5b4\ub514\uc57c",
    "\uc5b8\uc81c\uc57c",
    "\uc65c\uc57c",
    "\ub9de\uc544",
    "\ub3fc",
    "\ub420\uae4c",
    "\ud560\uae4c",
    "\uc788\uc5b4",
    "\uc5c6\uc5b4",
    "\ud574\uc918",
    "\uc54c\ub824\uc918",
)


@dataclass
class VoiceUtteranceMergeRecord:
    text: str
    room_session_key: str
    session_key: str
    user_id: int
    accepted_at: float
    turn_id: str
    segment_id: int
    consumed_by_turn_id: str | None = None


def _compact_text(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace())


def looks_like_complete_question(text: str, *, clean_text: Callable[[str], str]) -> bool:
    normalized = clean_text(text).rstrip()
    if not normalized:
        return False
    if normalized.endswith("?"):
        return True
    compact = _compact_text(normalized.rstrip(".!。！？?"))
    return any(compact.endswith(suffix) for suffix in _COMPLETE_QUESTION_SUFFIXES if suffix != "?")


def looks_like_incomplete_or_short_utterance(text: str, *, clean_text: Callable[[str], str]) -> bool:
    normalized = clean_text(text).rstrip(".!。！？?")
    if not normalized:
        return False
    compact = _compact_text(normalized)
    if len(compact) <= 12:
        return True
    lowered = normalized.lower()
    return any(lowered.endswith(suffix) for suffix in _INCOMPLETE_SUFFIXES)


def resolve_barge_in_merge_window_sec(
    previous_text: str,
    *,
    base_window_sec: float,
    tts_interrupted_window_sec: float,
    incomplete_window_sec: float,
    complete_question_window_sec: float,
    clean_text: Callable[[str], str],
) -> tuple[float, str]:
    if looks_like_complete_question(previous_text, clean_text=clean_text):
        return max(0.0, float(complete_question_window_sec)), "complete_question"
    if looks_like_incomplete_or_short_utterance(previous_text, clean_text=clean_text):
        return max(0.0, float(incomplete_window_sec)), "incomplete_or_short"
    return max(float(base_window_sec), float(tts_interrupted_window_sec), 0.0), "tts_interrupted"


def remember_voice_utterance_for_merge(
    records: MutableMapping[str, VoiceUtteranceMergeRecord],
    *,
    room_session_key: str | None,
    session_key: str | None,
    user_id: int | None,
    text: str,
    accepted_at: float,
    turn_id: str | None,
    segment_id: int,
    clean_text: Callable[[str], str],
) -> VoiceUtteranceMergeRecord | None:
    normalized_text = clean_text(text)
    if not room_session_key or not session_key or user_id is None or not normalized_text:
        return None
    record = VoiceUtteranceMergeRecord(
        text=normalized_text,
        room_session_key=room_session_key,
        session_key=session_key,
        user_id=int(user_id),
        accepted_at=float(accepted_at),
        turn_id=str(turn_id or ""),
        segment_id=int(segment_id),
    )
    records[room_session_key] = record
    return record


def maybe_merge_barge_in_utterance(
    records: MutableMapping[str, VoiceUtteranceMergeRecord],
    *,
    room_session_key: str | None,
    session_key: str | None,
    user_id: int | None,
    current_text: str,
    current_turn_id: str | None,
    interrupted_at: float | None,
    merge_window_sec: float,
    clean_text: Callable[[str], str],
    tts_interrupted_window_sec: float | None = None,
    incomplete_window_sec: float | None = None,
    complete_question_window_sec: float | None = None,
    adaptive_window_enabled: bool = True,
) -> tuple[str, dict[str, object] | None]:
    current = clean_text(current_text)
    if not current or not room_session_key or not session_key or user_id is None or interrupted_at is None:
        return current, None

    record = records.get(room_session_key)
    if record is None:
        return current, None
    if record.consumed_by_turn_id:
        return current, None
    if record.session_key != session_key or record.user_id != int(user_id):
        return current, None
    if record.turn_id and current_turn_id and record.turn_id == current_turn_id:
        return current, None

    delta_sec = float(interrupted_at) - float(record.accepted_at)
    if delta_sec < 0.0:
        return current, None

    previous = clean_text(record.text)
    if not previous or previous == current:
        return current, None

    base_window_sec = max(0.0, float(merge_window_sec))
    effective_window_sec = base_window_sec
    window_reason = "base"
    if adaptive_window_enabled:
        effective_window_sec, window_reason = resolve_barge_in_merge_window_sec(
            previous,
            base_window_sec=base_window_sec,
            tts_interrupted_window_sec=tts_interrupted_window_sec
            if tts_interrupted_window_sec is not None
            else base_window_sec,
            incomplete_window_sec=incomplete_window_sec
            if incomplete_window_sec is not None
            else base_window_sec,
            complete_question_window_sec=complete_question_window_sec
            if complete_question_window_sec is not None
            else base_window_sec,
            clean_text=clean_text,
        )

    if delta_sec > effective_window_sec:
        return current, None

    merged = clean_text(f"{previous} {current}")
    if not merged:
        return current, None

    record.consumed_by_turn_id = str(current_turn_id or "")
    return merged, {
        "previous_text": previous,
        "current_text": current,
        "merged_text": merged,
        "delta_sec": round(delta_sec, 4),
        "window_sec": float(effective_window_sec),
        "window_reason": window_reason,
        "base_window_sec": base_window_sec,
        "previous_turn_id": record.turn_id,
        "current_turn_id": str(current_turn_id or ""),
        "previous_segment_id": record.segment_id,
    }
