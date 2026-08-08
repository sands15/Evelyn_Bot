from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .memory import memory_questions_path, read_json_file, read_jsonl, write_json_file, write_jsonl
from .text import clean_text, is_user_echo_answer


QUESTION_QUEUE_LIMIT = 120

PROACTIVE_QUESTION_SOURCES = {"text", "control_page", "autonomy"}
PROACTIVE_QUESTION_BLOCK_MARKERS = (
    "\ubb3b\uc9c0",
    "\uc9c8\ubb38\ud558\uc9c0",
    "\ubcf4\uace0\ub9cc",
    "\ub300\ub2f5\ub9cc",
    "\ub2f5\ub9cc",
    "no question",
    "don't ask",
)

DECLINE_MARKERS = (
    "\uc544\ub2c8",
    "\ub098\uc911",
    "\ub418\uc5b4",
    "\uad1c\ucc2e",
    "\ud558\uc9c0\ub9c8",
    "\ubb3b\uc9c0\ub9c8",
    "\ud544\uc694 \uc5c6\uc5b4",
    "no",
    "later",
    "skip",
    "never mind",
)


@dataclass(frozen=True)
class ProactiveQuestionGateDecision:
    allowed: bool
    reason: str
    source: str = ""
    pending_active: bool = False
    session_cooldown_hit: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "source": self.source,
            "pending_active": self.pending_active,
            "session_cooldown_hit": self.session_cooldown_hit,
        }


def proactive_questions_path(guild_id: int, *, scope_type: str = "guild", scope_key: str | None = None) -> Path:
    return memory_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key).with_name("proactive_questions.jsonl")


def pending_proactive_question_path(guild_id: int, *, scope_type: str = "session", scope_key: str | None = None) -> Path:
    return memory_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key).with_name("pending_proactive_question.json")


def load_proactive_questions(guild_id: int, *, scope_type: str = "guild", scope_key: str | None = None) -> list[dict[str, Any]]:
    return read_jsonl(proactive_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key))


def write_proactive_questions(
    guild_id: int,
    rows: list[dict[str, Any]],
    *,
    scope_type: str = "guild",
    scope_key: str | None = None,
) -> None:
    write_jsonl(proactive_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key), rows[-QUESTION_QUEUE_LIMIT:])


def promote_open_questions(
    guild_id: int,
    open_rows: list[dict[str, Any]],
    *,
    scope_type: str = "guild",
    scope_key: str | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    # The provenance-bearing open-question store remains authoritative.
    # Until queue rows have a current deletion receipt, store no raw copy.
    write_proactive_questions(
        guild_id,
        [],
        scope_type=scope_type,
        scope_key=scope_key,
    )
    return []


def read_pending_question(guild_id: int, *, session_scope_key: str | None) -> dict[str, Any]:
    if not session_scope_key:
        return {}
    return read_json_file(pending_proactive_question_path(guild_id, scope_type="session", scope_key=session_scope_key))


def clear_pending_question(guild_id: int, *, session_scope_key: str | None) -> None:
    if not session_scope_key:
        return
    write_json_file(pending_proactive_question_path(guild_id, scope_type="session", scope_key=session_scope_key), {})


def has_active_pending_question(guild_id: int, *, session_scope_key: str | None, now: float | None = None) -> bool:
    pending = read_pending_question(guild_id, session_scope_key=session_scope_key)
    if not pending:
        return False
    return float(pending.get("expires_at") or 0.0) > float(now or time.time())


def _answer_already_contains_question(answer_text: str) -> bool:
    answer = clean_text(answer_text)
    return bool(answer and (answer.count("?") + answer.count("\uae4c") > 0))


def evaluate_proactive_question_gate(
    *,
    guild_id: int | None,
    source: str,
    user_text: str,
    answer_text: str = "",
    awaiting_user_reply: bool = False,
    session_scope_key: str | None = None,
    session_cooldown_hit: bool = False,
    runtime_block_reason: str = "",
    candidate_text: str = "",
    now: float | None = None,
) -> ProactiveQuestionGateDecision:
    normalized_source = clean_text(source).lower()
    if guild_id is None:
        return ProactiveQuestionGateDecision(False, "no_guild", source=normalized_source)
    if normalized_source not in PROACTIVE_QUESTION_SOURCES:
        return ProactiveQuestionGateDecision(False, "unsupported_source", source=normalized_source)
    if awaiting_user_reply:
        return ProactiveQuestionGateDecision(False, "awaiting_user_reply", source=normalized_source)

    pending_active = has_active_pending_question(guild_id, session_scope_key=session_scope_key, now=now)
    if pending_active:
        return ProactiveQuestionGateDecision(False, "pending_question_active", source=normalized_source, pending_active=True)

    if session_cooldown_hit:
        return ProactiveQuestionGateDecision(
            False,
            "session_question_cooldown",
            source=normalized_source,
            session_cooldown_hit=True,
        )

    block_reason = clean_text(runtime_block_reason)
    if block_reason:
        return ProactiveQuestionGateDecision(False, block_reason, source=normalized_source)

    merged = clean_text(f"{user_text} {answer_text}").lower()
    if any(marker in merged for marker in PROACTIVE_QUESTION_BLOCK_MARKERS):
        return ProactiveQuestionGateDecision(False, "user_requested_no_questions", source=normalized_source)

    if _answer_already_contains_question(answer_text):
        return ProactiveQuestionGateDecision(False, "answer_already_has_question", source=normalized_source)

    candidate = clean_text(candidate_text)
    if candidate and is_user_echo_answer(user_text, candidate):
        return ProactiveQuestionGateDecision(False, "candidate_echoes_user", source=normalized_source)

    return ProactiveQuestionGateDecision(True, "allowed", source=normalized_source)


def select_question_to_ask(
    guild_id: int,
    *,
    scope_type: str = "session",
    scope_key: str | None = None,
    session_scope_key: str | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    # Queue rows do not yet carry a deletion-current receipt that can be
    # validated under the memory deletion lease. Keep every consumer closed.
    return None


def mark_question_asked(
    guild_id: int,
    question_id: str,
    *,
    scope_type: str = "session",
    scope_key: str | None = None,
    session_scope_key: str | None = None,
    asked_text: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    # Match selection's fail-closed boundary so callers cannot bypass it by id.
    return {}


def _looks_declined(text: str) -> bool:
    cleaned = clean_text(text).lower()
    return any(marker in cleaned for marker in DECLINE_MARKERS)


def resolve_pending_question_answer(
    guild_id: int,
    user_text: str,
    *,
    session_scope_key: str | None,
    now: float | None = None,
) -> dict[str, Any]:
    pending = read_pending_question(guild_id, session_scope_key=session_scope_key)
    if not pending:
        return {"resolved": False, "reason": "no_pending_question"}
    current_time = int(now or time.time())
    if float(pending.get("expires_at") or 0.0) <= current_time:
        clear_pending_question(guild_id, session_scope_key=session_scope_key)
        return {"resolved": False, "reason": "pending_expired", "question_id": pending.get("question_id")}

    scope_type = clean_text(str(pending.get("scope_type") or "session")) or "session"
    scope_key = pending.get("scope_key")
    question_id = clean_text(str(pending.get("question_id") or ""))
    resolution = "declined" if _looks_declined(user_text) else "answered"

    rows = load_proactive_questions(guild_id, scope_type=scope_type, scope_key=scope_key)
    matched = any(
        isinstance(row, dict)
        and clean_text(str(row.get("id", ""))) == question_id
        for row in rows
    )
    clear_pending_question(guild_id, session_scope_key=session_scope_key)
    return {"resolved": matched, "resolution": resolution, "question_id": question_id}


def should_offer_proactive_question(
    *,
    source: str,
    user_text: str,
    answer_text: str,
    awaiting_user_reply: bool,
) -> bool:
    if source not in {"text", "control_page"}:
        return False
    decision = evaluate_proactive_question_gate(
        guild_id=0,
        source=source,
        user_text=user_text,
        answer_text=answer_text,
        awaiting_user_reply=awaiting_user_reply,
    )
    return decision.allowed
