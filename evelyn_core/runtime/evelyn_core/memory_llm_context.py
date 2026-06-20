from __future__ import annotations

import json
from typing import Any

from .memory import compact_memory_text, format_memory_rows_for_llm
from .memory_context_state import merge_recent_memory_rows


def layered_summary_text(layers: dict[str, dict[str, Any]]) -> str:
    summary_lines = [
        f"- {layer['label']}: {layer['summary']}"
        for layer in (layers.get("session"), layers.get("person"), layers.get("room"), layers.get("guild"))
        if layer and layer.get("summary")
    ]
    return "\n".join(summary_lines)


def recent_memory_groups(
    layers: dict[str, dict[str, Any]],
    *,
    raw_limit: int,
    facts_limit: int,
    questions_limit: int,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "raw": merge_recent_memory_rows(
            *(layer["raw"] for layer in layers.values()),
            limit=raw_limit,
        ),
        "facts": merge_recent_memory_rows(
            *(layer["facts"] for layer in layers.values()),
            limit=facts_limit,
        ),
        "questions": merge_recent_memory_rows(
            *(layer["questions"] for layer in layers.values()),
            limit=questions_limit,
        ),
    }


def build_cognitive_state_messages(
    *,
    current_state: dict[str, Any],
    current_summary: str,
    recent_raw: list[dict[str, Any]],
    recent_facts: list[dict[str, Any]],
    recent_questions: list[dict[str, Any]],
    user_text: str,
    raw_limit: int,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                '너는 실시간 대화 조율자다. 반드시 JSON 객체 하나만 출력한다. '
                '형식은 {"action": "answer|ask|wait|search_then_answer", "confidence": number, "user_intent": string, "state_summary": string, "question_for_user": string, "main_prompt_hint": string, "reason_brief": string, "retrieved_context_ids": string[]}. '
                'answer는 지금 답하면 되는 경우다. ask는 사용자의 원래 발화에 이어서 짧게 되묻거나 확인 질문을 하는 편이 자연스러운 경우다. wait는 아직 단정하지 말고 더 듣거나 짧게 여지를 두는 편이 자연스러운 경우다. search_then_answer는 최신 정보나 외부 확인이 필요해서 먼저 짧게 알리고 뒤이어 검색 결과를 전해야 하는 경우다. '
                'question_for_user는 사용자가 한 말이 아니라, 메인 LLM이 사용자에게 되물을 내부 질문 초안이다. 절대로 사용자의 질문을 베껴 쓰거나 사용자가 이미 한 말처럼 적지 마라. '
                'user_intent에는 사용자가 진짜로 하려는 말을 아주 짧게 적어라. state_summary에는 현재 상황을 한두 문장으로 적어라. main_prompt_hint에는 메인 LLM이 말할 때 지켜야 할 한 줄 힌트를 적어라. confidence는 0~1, reason_brief는 아주 짧게 써라. JSON 외 다른 텍스트는 절대 출력하지 마라.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"이전 cognitive_state:\n{json.dumps(current_state, ensure_ascii=False)}\n\n"
                f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                f"최근 raw_transcript:\n{format_memory_rows_for_llm(recent_raw, max_items=raw_limit)}\n\n"
                f"최근 durable_facts:\n{format_memory_rows_for_llm(recent_facts, max_items=4)}\n\n"
                f"최근 open_questions:\n{format_memory_rows_for_llm(recent_questions, max_items=4)}\n\n"
                f"현재 사용자 입력:\n{compact_memory_text(user_text, max_chars=160)}"
            ),
        },
    ]


def build_compact_cognitive_state_messages(*, current_summary: str, user_text: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": build_cognitive_state_messages(
                current_state={},
                current_summary="",
                recent_raw=[],
                recent_facts=[],
                recent_questions=[],
                user_text="",
                raw_limit=0,
            )[0]["content"],
        },
        {
            "role": "user",
            "content": (
                f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                f"현재 사용자 입력:\n{compact_memory_text(user_text, max_chars=120)}"
            ),
        },
    ]


def build_long_term_memory_messages(
    *,
    current_summary: str,
    recent_raw: list[dict[str, Any]],
    recent_facts: list[dict[str, Any]],
    recent_questions: list[dict[str, Any]],
    user_text: str,
    answer: str,
    raw_limit: int,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                '너는 대화 장기기억 관리자이자 상황 정리자다. 반드시 JSON 객체 하나만 출력한다. '
                '형식은 {"summary_update": string, "durable_facts": [{"type": string, "text": string}], "open_questions": [{"type": string, "text": string}]}. '
                'summary_update는 지금 상황을 짧고 자연스러운 한국어로 압축한 누적 요약이다. '
                'durable_facts에는 오래 기억할 만한 선호, 설정, 프로젝트 결정, 반복되는 사실만 넣어라. '
                'open_questions에는 아직 확정되지 않은 추정, 확인이 필요한 질문, 다음에 물어볼 만한 포인트만 넣어라. '
                '잡담, 일회성 노이즈, 이미 해결된 내용은 넣지 마라. JSON 외 다른 텍스트는 절대 출력하지 마라.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                f"최근 raw_transcript:\n{format_memory_rows_for_llm(recent_raw, max_items=raw_limit)}\n\n"
                f"최근 durable_facts:\n{format_memory_rows_for_llm(recent_facts, max_items=6)}\n\n"
                f"최근 open_questions:\n{format_memory_rows_for_llm(recent_questions, max_items=4)}\n\n"
                f"새 대화:\n- user: {compact_memory_text(user_text, max_chars=120)}\n- assistant: {compact_memory_text(answer, max_chars=120)}"
            ),
        },
    ]


def build_compact_long_term_memory_messages(
    *,
    current_summary: str,
    user_text: str,
    answer: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": build_long_term_memory_messages(
                current_summary="",
                recent_raw=[],
                recent_facts=[],
                recent_questions=[],
                user_text="",
                answer="",
                raw_limit=0,
            )[0]["content"],
        },
        {
            "role": "user",
            "content": (
                f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                f"새 대화:\n- user: {compact_memory_text(user_text, max_chars=100)}\n- assistant: {compact_memory_text(answer, max_chars=100)}"
            ),
        },
    ]


def memory_scope_targets(
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> list[tuple[str, str | None]]:
    targets: list[tuple[str, str | None]] = [("guild", None)]
    if room_key:
        targets.append(("room", room_key))
    if person_key:
        targets.append(("person", person_key))
    if session_memory_key:
        targets.append(("session", session_memory_key))
    return targets


__all__ = [
    "build_cognitive_state_messages",
    "build_compact_cognitive_state_messages",
    "build_compact_long_term_memory_messages",
    "build_long_term_memory_messages",
    "layered_summary_text",
    "memory_scope_targets",
    "recent_memory_groups",
]
