from __future__ import annotations

from typing import Any

from evelyn_core.text import clean_text


def split_answer_sentences(answer: str) -> list[str]:
    text = clean_text(answer)
    if not text:
        return []

    sentences: list[str] = []
    current: list[str] = []
    for index, char in enumerate(text):
        current.append(char)
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if char in {"?", "？", "!", "！"}:
            sentences.append(clean_text("".join(current)))
            current = []
            continue
        if char == "." and (not next_char or next_char.isspace()):
            sentences.append(clean_text("".join(current)))
            current = []
    if current:
        sentences.append(clean_text("".join(current)))
    return [sentence for sentence in sentences if sentence]


def sentence_is_question(sentence: str) -> bool:
    return clean_text(sentence).rstrip().endswith(("?", "？"))


def enforce_question_limits(answer: str, route_decision: Any) -> tuple[str, dict[str, Any]]:
    sentences = split_answer_sentences(answer)
    if not sentences:
        return clean_text(answer), {
            "question_count_before": 0,
            "question_count_after": 0,
            "question_removed": False,
        }

    max_questions = max(0, min(1, int(getattr(route_decision, "max_question_count", 0) or 0)))
    kept: list[str] = []
    question_count = 0
    before_count = 0
    removed = False
    for sentence in sentences:
        if sentence_is_question(sentence):
            before_count += 1
            if question_count >= max_questions:
                removed = True
                continue
            question_count += 1
        kept.append(sentence)

    shaped = clean_text(" ".join(kept))
    if not shaped and removed:
        shaped = "응, 알겠어."
    if not shaped:
        shaped = clean_text(answer)
    return shaped, {
        "question_count_before": before_count,
        "question_count_after": question_count,
        "question_removed": removed,
    }


def filter_stream_chunk_for_question_limits(
    chunk: str,
    *,
    max_question_count: int,
    question_count_so_far: int,
) -> tuple[str, dict[str, Any]]:
    sentences = split_answer_sentences(chunk)
    if not sentences:
        return clean_text(chunk), {
            "question_count_before": 0,
            "question_count_after": 0,
            "question_removed": False,
        }

    max_questions = max(0, min(1, int(max_question_count or 0)))
    question_count = max(0, int(question_count_so_far or 0))
    kept: list[str] = []
    before_count = 0
    added_count = 0
    removed = False
    for sentence in sentences:
        if sentence_is_question(sentence):
            before_count += 1
            if question_count >= max_questions:
                removed = True
                continue
            question_count += 1
            added_count += 1
        kept.append(sentence)

    return clean_text(" ".join(kept)), {
        "question_count_before": before_count,
        "question_count_after": added_count,
        "question_removed": removed,
    }
