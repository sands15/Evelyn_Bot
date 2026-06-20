from __future__ import annotations

from typing import Any

from .cognitive_policy_state import read_layered_cognitive_state
from .config import MEMORY_RAW_CONTEXT_LIMIT, MEMORY_RETRIEVE_LIMIT, MEMORY_VAULT_RAW_RETRIEVE_LIMIT
from .memory import merge_memory_rows, normalize_cognitive_state, select_relevant_memory_rows
from .memory_layers import collect_memory_layers
from .memory_vault import build_memory_vault_context
from .text import clean_text


def merge_recent_memory_rows(*row_groups: list[dict], limit: int) -> list[dict]:
    merged = merge_memory_rows(*row_groups)
    merged.sort(key=lambda row: int(row.get("saved_at", 0) or 0))
    return merged[-limit:]


def format_memory_row_lines(rows: list[dict]) -> str:
    return "\n".join(
        f"- {clean_text(str(row.get('speaker', row.get('role', 'unknown')))) or 'unknown'}"
        f" ({clean_text(str(row.get('source', 'unknown'))) or 'unknown'}): {clean_text(str(row.get('text', '')))}"
        for row in rows
        if clean_text(str(row.get("text", "")))
    )


def build_memory_context_payload(
    *,
    layers: dict[str, dict[str, Any]],
    state: dict[str, Any],
    session_state: dict[str, Any],
    vault_context: str,
    facts: list[dict],
    questions: list[dict],
    vault_raw_rows: list[dict],
) -> str:
    parts: list[str] = []
    summary_lines = [
        f"- {layer['label']}: {layer['summary']}"
        for layer in (layers.get("session"), layers.get("person"), layers.get("room"), layers.get("guild"))
        if layer and layer.get("summary")
    ]
    if summary_lines:
        parts.append("현재 작업 요약:\n" + "\n".join(summary_lines))

    session_rows = merge_recent_memory_rows(*(layer["raw"] for layer in (layers.get("session"),) if layer), limit=4)
    if session_rows:
        parts.append("현재 세션 최근 대화:\n" + format_memory_row_lines(session_rows))

    person_rows = merge_recent_memory_rows(*(layer["raw"] for layer in (layers.get("person"),) if layer), limit=4)
    if person_rows:
        parts.append("이 사람과의 최근 대화:\n" + format_memory_row_lines(person_rows))

    room_rows = merge_recent_memory_rows(
        *(layer["raw"] for layer in (layers.get("room"), layers.get("guild")) if layer),
        limit=MEMORY_RAW_CONTEXT_LIMIT,
    )
    if room_rows:
        parts.append("방 최근 대화:\n" + format_memory_row_lines(room_rows))

    if vault_raw_rows:
        parts.append("문서 보관함에서 꺼낸 관련 대화:\n" + format_memory_row_lines(vault_raw_rows))
    if session_state:
        action_label = {
            "answer": "답하기",
            "ask": "질문하기",
            "wait": "더 듣기",
        }.get(state.get("action", "answer"), "답하기")
        state_lines = [f"- 권장 행동: {action_label}"]
        if state.get("user_intent"):
            state_lines.append(f"- 사용자 의도: {state['user_intent']}")
        if state.get("retrieved_context_ids"):
            state_lines.append(f"- 참고 문맥 ID: {', '.join(state['retrieved_context_ids'][:4])}")
        if session_state.get("last_speaker"):
            state_lines.append(f"- 마지막 화자: {session_state['last_speaker']}")
        if session_state.get("awaiting_user_reply"):
            state_lines.append("- 사용자 후속 응답 대기 중")
        if session_state.get("topic_id"):
            state_lines.append(f"- 현재 topic_id: {session_state['topic_id']}")
        parts.append("현재 내부 상태(사용자 발화 아님):\n" + "\n".join(state_lines))
    if vault_context:
        parts.append("Structured memory vault recall:\n" + vault_context)
    if facts:
        parts.append(
            "장기 기억 후보:\n" + "\n".join(f"- {clean_text(str(row.get('text', '')))}" for row in facts)
        )
    if questions:
        parts.append(
            "열린 질문/가설:\n" + "\n".join(f"- {clean_text(str(row.get('text', '')))}" for row in questions)
        )

    if not parts:
        return ""

    return (
        "다음은 이전 대화에서 정리한 참고 메모다. 사실처럼 단정하지 말고, 현재 질문과 맞는 경우에만 자연스럽게 반영해라.\n\n"
        + "\n\n".join(parts)
    )


def build_memory_context(
    guild_id: int,
    user_text: str,
    cognitive_state: dict[str, Any] | None = None,
    *,
    session_key: str | None = None,
    session_state: dict[str, Any] | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> str:
    layers = collect_memory_layers(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    facts = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["facts"] for layer in layers.values())),
        MEMORY_RETRIEVE_LIMIT,
    )
    questions = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["questions"] for layer in layers.values())),
        4,
    )
    vault_raw_rows = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["vault_raw"] for layer in layers.values())),
        MEMORY_VAULT_RAW_RETRIEVE_LIMIT,
    )
    state = read_layered_cognitive_state(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    state = normalize_cognitive_state(state if cognitive_state is None else cognitive_state)
    active_session_state = dict(session_state or {})
    vault_context = build_memory_vault_context(
        guild_id,
        user_text,
        session_key=session_key,
        topic_id=clean_text(str(active_session_state.get("topic_id", ""))) or None,
        source="context_pipeline",
        context_focus=[
            "relevant_memory",
            clean_text(str(state.get("user_intent", ""))),
            clean_text(str(state.get("state_summary", ""))),
        ],
        max_items=5,
    )

    return build_memory_context_payload(
        layers=layers,
        state=state,
        session_state=active_session_state,
        vault_context=vault_context,
        facts=facts,
        questions=questions,
        vault_raw_rows=vault_raw_rows,
    )


__all__ = [
    "build_memory_context",
    "build_memory_context_payload",
    "format_memory_row_lines",
    "merge_recent_memory_rows",
]
