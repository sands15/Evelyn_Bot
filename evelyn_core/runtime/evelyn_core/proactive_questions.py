from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from .memory import memory_questions_path, read_json_file, read_jsonl, write_json_file, write_jsonl
from .text import clean_text


QUESTION_QUEUE_LIMIT = 120
QUESTION_PENDING_TTL_SEC = 15 * 60
QUESTION_DEFAULT_EXPIRE_SEC = 7 * 24 * 60 * 60
QUESTION_COOLDOWN_SEC = 60 * 60
QUESTION_MAX_ASK_COUNT = 2

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


def proactive_questions_path(guild_id: int, *, scope_type: str = "guild", scope_key: str | None = None) -> Path:
    return memory_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key).with_name("proactive_questions.jsonl")


def pending_proactive_question_path(guild_id: int, *, scope_type: str = "session", scope_key: str | None = None) -> Path:
    return memory_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key).with_name("pending_proactive_question.json")


def _question_id(scope_type: str, scope_key: str | None, text: str) -> str:
    raw = f"{scope_type}:{scope_key or ''}:{clean_text(text).lower()}".encode("utf-8", errors="ignore")
    return "pq_" + hashlib.sha1(raw).hexdigest()[:16]


def _question_priority(row: dict[str, Any]) -> float:
    row_type = clean_text(str(row.get("type", ""))).lower()
    text = clean_text(str(row.get("text", ""))).lower()
    priority = 0.45
    if row_type in {"action", "preference", "weather_query"}:
        priority += 0.15
    if any(marker in text for marker in ("\ud655\uc778", "\ubb3c\uc5b4", "\uc120\ud638", "\uc815\ud655", "\ud544\uc694")):
        priority += 0.10
    return max(0.0, min(1.0, priority))


def _strip_confirmation_suffix(text: str) -> str:
    cleaned = clean_text(text)
    for suffix in (
        "\ud655\uc778 \ud544\uc694",
        "\ud655\uc778\uc774 \ud544\uc694",
        "\ud655\uc778\ud544\uc694",
        "\ubb3c\uc5b4\ubcfc \ub9cc\ud55c \uc0ac\uc548",
    ):
        if cleaned.endswith(suffix):
            return clean_text(cleaned[: -len(suffix)])
    return cleaned


def realize_question_text(raw_text: str) -> str:
    cleaned = clean_text(raw_text)
    core = _strip_confirmation_suffix(cleaned) or cleaned
    if any(marker in cleaned for marker in ("\uc815\ud655", "\ud655\uc778")):
        return clean_text(
            f"\uc544\uae4c {core} \ubd80\ubd84\uc740 \uc815\ud655\ud788 \ud655\uc778\ud574\ub450\ub294 \uac8c \uc88b\uc744 \uac83 \uac19\uc544. "
            "\uc9c0\uae08 \uc815\ub9ac\ud574\ub193\uc744\uae4c?"
        )
    if any(marker in cleaned for marker in ("\uc120\ud638", "\uc2a4\ud0c0\uc77c", "\uac15\ub3c4")):
        return clean_text(
            f"\uc544\uae4c {core} \ucabd \uc120\ud638\ub97c \ud655\uc778\ud574\ub450\uba74 \uc88b\uaca0\ub2e4\uace0 \ub290\ub080 \uac8c \uc788\uc5b4. "
            "\uc9c0\uae08 \uc815\ud574\ub193\uc744\uae4c?"
        )
    return clean_text(
        f"\uc544\uae4c \ud655\uc778\uc774 \ud544\uc694\ud558\ub2e4\uace0 \ub0a8\uaca8\ub454 \uac8c \uc788\uc5b4. {core} \uc774 \ubd80\ubd84 "
        "\uc9c0\uae08 \uc815\ub9ac\ud574\ub193\uc744\uae4c?"
    )


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
    current_time = int(now or time.time())
    queue = load_proactive_questions(guild_id, scope_type=scope_type, scope_key=scope_key)
    by_id = {clean_text(str(row.get("id", ""))): dict(row) for row in queue if isinstance(row, dict)}

    for row in open_rows:
        if not isinstance(row, dict):
            continue
        raw_text = clean_text(str(row.get("text", "")))
        if len(raw_text) < 2:
            continue
        question_id = _question_id(scope_type, scope_key, raw_text)
        if question_id in by_id:
            continue
        by_id[question_id] = {
            "id": question_id,
            "scope_type": clean_text(scope_type) or "guild",
            "scope_key": scope_key,
            "source": "open_questions",
            "raw_text": raw_text,
            "ask_text": realize_question_text(raw_text),
            "question_type": clean_text(str(row.get("type", "detail"))) or "detail",
            "priority": _question_priority(row),
            "status": "pending",
            "created_at": int(row.get("saved_at", current_time) or current_time),
            "updated_at": current_time,
            "last_asked_at": None,
            "ask_count": 0,
            "expires_at": current_time + QUESTION_DEFAULT_EXPIRE_SEC,
            "answer_text": "",
        }

    ordered = sorted(by_id.values(), key=lambda item: (float(item.get("created_at") or 0), clean_text(str(item.get("id", "")))))
    write_proactive_questions(guild_id, ordered, scope_type=scope_type, scope_key=scope_key)
    return ordered


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


def _eligible_question(row: dict[str, Any], now: float) -> bool:
    status = clean_text(str(row.get("status", "pending"))) or "pending"
    if status not in {"pending", "asked"}:
        return False
    if int(row.get("ask_count") or 0) >= QUESTION_MAX_ASK_COUNT:
        return False
    if float(row.get("expires_at") or 0.0) <= now:
        return False
    last_asked_at = float(row.get("last_asked_at") or 0.0)
    if last_asked_at and now - last_asked_at < QUESTION_COOLDOWN_SEC:
        return False
    return True


def select_question_to_ask(
    guild_id: int,
    *,
    scope_type: str = "session",
    scope_key: str | None = None,
    session_scope_key: str | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    current_time = float(now or time.time())
    if has_active_pending_question(guild_id, session_scope_key=session_scope_key or scope_key, now=current_time):
        return None
    rows = load_proactive_questions(guild_id, scope_type=scope_type, scope_key=scope_key)
    candidates = [row for row in rows if isinstance(row, dict) and _eligible_question(row, current_time)]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            float(item.get("priority") or 0.0),
            -int(item.get("ask_count") or 0),
            int(item.get("created_at") or 0),
        ),
        reverse=True,
    )
    return candidates[0]


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
    current_time = int(now or time.time())
    rows = load_proactive_questions(guild_id, scope_type=scope_type, scope_key=scope_key)
    selected: dict[str, Any] = {}
    updated: list[dict[str, Any]] = []
    for row in rows:
        current = dict(row)
        if clean_text(str(current.get("id", ""))) == question_id:
            current["status"] = "asked"
            current["updated_at"] = current_time
            current["last_asked_at"] = current_time
            current["ask_count"] = int(current.get("ask_count") or 0) + 1
            if asked_text:
                current["asked_text"] = clean_text(asked_text)
            selected = dict(current)
        updated.append(current)
    write_proactive_questions(guild_id, updated, scope_type=scope_type, scope_key=scope_key)
    if selected and (session_scope_key or scope_key):
        write_json_file(
            pending_proactive_question_path(guild_id, scope_type="session", scope_key=session_scope_key or scope_key),
            {
                "question_id": question_id,
                "scope_type": scope_type,
                "scope_key": scope_key,
                "asked_text": clean_text(asked_text or selected.get("ask_text", "")),
                "asked_at": current_time,
                "expires_at": current_time + QUESTION_PENDING_TTL_SEC,
            },
        )
    return selected


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
    updated: list[dict[str, Any]] = []
    matched = False
    for row in rows:
        current = dict(row)
        if clean_text(str(current.get("id", ""))) == question_id:
            current["status"] = resolution
            current["updated_at"] = current_time
            current["answer_text"] = clean_text(user_text)
            matched = True
        updated.append(current)
    if matched:
        write_proactive_questions(guild_id, updated, scope_type=scope_type, scope_key=scope_key)
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
    if awaiting_user_reply:
        return False
    merged = clean_text(f"{user_text} {answer_text}").lower()
    if any(marker in merged for marker in ("\ubb3b\uc9c0", "\uc9c8\ubb38\ud558\uc9c0", "\ubcf4\uace0\ub9cc", "\ub300\ub2f5\ub9cc", "\ub2f5\ub9cc", "no question", "don't ask")):
        return False
    if answer_text.count("?") + answer_text.count("\uae4c") > 0:
        return False
    return True
