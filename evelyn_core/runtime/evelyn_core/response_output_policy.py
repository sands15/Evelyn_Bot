from __future__ import annotations

import json
import re
from dataclasses import dataclass
from collections.abc import Callable, Iterable
from typing import Any

from .text import clean_text, normalize_omnivoice_tags, strip_model_channel_tags, visible_text


@dataclass(frozen=True)
class ResponseOutputPolicyRuntimeDeps:
    session_state_snapshot_fn: Callable[[str | None], dict[str, Any]] | None = None
    answer_gpu_status_answer_fn: Callable[[str], str | None] | None = None
    model_output_stop_tokens: tuple[str, ...] = ()
    sanitize_model_output_cleanup_fn: Callable[[str], str] | None = None


RESPONSE_ACTION_TAGS = {"찾기": "search", "질문": "ask", "대기": "wait", "답변": "answer", "응답": "answer"}


def fallback_answer_for(user_text: str) -> str:
    text = clean_text(user_text)
    if not text:
        return "응, 듣고 있어."
    return "응, 잠깐만."


def parse_response_action_tag(text: str) -> tuple[str | None, str]:
    raw = text or ""
    match = re.match(r"^\s*\[(찾기|질문|대기|답변|응답)\]\s*", raw)
    if not match:
        return None, clean_text(raw)
    action = RESPONSE_ACTION_TAGS.get(match.group(1))
    stripped = clean_text(raw[match.end():])
    return action, stripped


def normalize_friend_style_output(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    cleaned = cleaned.replace("부르셨나요", "불렀어?")
    cleaned = cleaned.replace("말씀하세요", "말해")
    cleaned = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]+", "", cleaned)
    return clean_text(cleaned)


def extract_json_object(text: str) -> dict:
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return {}


def extract_json_object_from_runtime(
    text: str,
    *,
    deps: ResponseOutputPolicyRuntimeDeps | None = None,
) -> dict:
    return extract_json_object(text)


def cleanup_assistant_display_artifacts(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    blocked_fragments = (
        "Ready to tackle this directly now",
        "나는 지금 여기서 제대로 대답할 거야",
        "그 말투는 버릴게",
        "문서처럼 말하지 않을래",
        "방금 문장은 내려놓고",
        "직접 누른 척하면",
    )
    for fragment in blocked_fragments:
        cleaned = cleaned.replace(fragment, " ")
    extra_blocked_fragments = (
        "나는 지금 여기서 제대로 대답할 거야",
        "그 말투는 버릴게",
        "문서처럼 말하지 않을래",
        "방금 문장은 내려놓고",
        "직접 누른 척하면",
        "whispers too much",
        "just a little bit more confirmation needed here",
        "문 닫듯이 끝내고",
    )
    for fragment in extra_blocked_fragments:
        cleaned = cleaned.replace(fragment, " ")
    cleaned = re.sub(r"\bwhispers\b[^.?!。！？]*[.?!。！？]?", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\(?\s*정리\s*\)?", " ", cleaned)
    cleaned = re.sub(r"\s*[,:;]\s*[!)]*\s*", " ", cleaned)
    cleaned = re.sub(r"\*{1,3}", " ", cleaned)
    cleaned = re.sub(r"\s+\)+\s*$", "", cleaned)
    cleaned = re.sub(r"!\s*!\s*", "! ", cleaned)
    lines: list[str] = []
    for line in re.split(r"[\r\n]+", cleaned):
        line = clean_text(line)
        if not line:
            continue
        latin = len(re.findall(r"[A-Za-z]", line))
        hangul = len(re.findall(r"[\uac00-\ud7a3]", line))
        if latin >= 12 and hangul == 0:
            continue
        lines.append(line)
    cleaned = clean_text(" ".join(lines))
    return cleaned.strip(" ,:;!*)(")


def user_explicitly_mentions_minecraft(user_text: str) -> bool:
    text = clean_text(user_text).lower()
    if not text:
        return False
    markers = (
        "minecraft",
        "voyager",
        "마인크래프트",
        "마크",
        "블록",
        "좌표",
        "인벤토리",
        "채굴",
        "길찾",
        "길 찾",
        "광질",
    )
    if not any(marker in text for marker in markers):
        return False
    negative_or_meta_markers = (
        "하지 마",
        "하지마",
        "말하지",
        "말고",
        "빼고",
        "제외",
        "금지",
        "아니",
        "이상한 답",
        "계속",
        "달라고 하는데",
    )
    if any(marker in text for marker in negative_or_meta_markers):
        return False
    return True


def user_explicitly_mentions_minecraft_from_runtime(
    user_text: str,
    *,
    deps: ResponseOutputPolicyRuntimeDeps | None = None,
) -> bool:
    return user_explicitly_mentions_minecraft(user_text)


def answer_contains_minecraft_leak(answer: str) -> bool:
    text = clean_text(answer).lower()
    markers = (
        "minecraft",
        "voyager",
        "마인크래프트",
        "마크",
        "블록",
        "좌표",
        "인벤토리",
        "채굴",
        "길찾",
        "길 찾",
        "광질",
        "block",
        "coordinate",
        "pathfinding",
        "inventory",
        "mining",
    )
    return any(marker in text for marker in markers)


def answer_contains_minecraft_leak_from_runtime(
    answer: str,
    *,
    deps: ResponseOutputPolicyRuntimeDeps | None = None,
) -> bool:
    return answer_contains_minecraft_leak(answer)


def fallback_for_unrequested_minecraft_leak(
    user_text: str,
    *,
    gpu_status_answer_fn: Callable[[str], str | None] | None = None,
) -> str:
    text = clean_text(user_text)
    lowered = text.lower()
    if "작업 상황" in text or "오늘 작업" in text:
        return "지금은 이블린 런타임 안정화랑 응답 품질 확인을 보고 있어."
    if "인사" in text or "안녕" in text:
        return "응, 안녕. 짧게 말할게."
    if "말이 별로" in text or "조용" in text:
        return "알겠어. 딱 필요한 만큼만 말할게."
    if any(marker in lowered for marker in ("vram", "oom", "gpu")):
        if gpu_status_answer_fn is not None:
            return gpu_status_answer_fn(text) or "지금 GPU 상태를 다시 확인해야 해."
        return "지금 GPU 상태를 다시 확인해야 해."
    return "그쪽 얘기는 빼고, 지금 요청에 맞춰 짧게 답할게."


def fallback_for_unrequested_minecraft_leak_from_runtime(
    user_text: str,
    *,
    deps: ResponseOutputPolicyRuntimeDeps | None = None,
) -> str:
    gpu_status_answer_fn = None if deps is None else deps.answer_gpu_status_answer_fn
    return fallback_for_unrequested_minecraft_leak(user_text, gpu_status_answer_fn=gpu_status_answer_fn)


def sanitize_unrequested_minecraft_leak(
    user_text: str,
    answer: str,
    *,
    gpu_status_answer_fn: Callable[[str], str | None] | None = None,
) -> str:
    cleaned = clean_text(answer)
    if not cleaned:
        return cleaned
    if user_explicitly_mentions_minecraft(user_text):
        return cleaned
    if not answer_contains_minecraft_leak(cleaned):
        return cleaned
    return fallback_for_unrequested_minecraft_leak(user_text, gpu_status_answer_fn=gpu_status_answer_fn)


def sanitize_unrequested_minecraft_leak_from_runtime(
    user_text: str,
    answer: str,
    *,
    deps: ResponseOutputPolicyRuntimeDeps | None = None,
) -> str:
    gpu_status_answer_fn = None if deps is None else deps.answer_gpu_status_answer_fn
    return sanitize_unrequested_minecraft_leak(
        user_text,
        answer,
        gpu_status_answer_fn=gpu_status_answer_fn,
    )


def answer_simple_local_chat_query(user_text: str) -> str | None:
    text = clean_text(user_text)
    lowered = text.lower()
    if not text or user_explicitly_mentions_minecraft(text):
        return None
    if "작업 상황" in text or "오늘 작업" in text:
        return "지금은 이블린 응답 품질이랑 로컬 런타임 안정화를 확인 중이야."
    if "말이 별로" in text or "조용" in text:
        return "알겠어. 필요한 만큼만 말할게."
    if text in {"안녕", "안녕.", "하이", "ㅎㅇ"} or "인사만" in text:
        return "응, 안녕."
    if ("짧게" in text or "한마디" in text) and ("자연스럽게" in text or "말해줘" in text):
        return "응, 지금은 짧게 갈게."
    if lowered in {"hello", "hi", "hey"}:
        return "응, 안녕."
    return None


def format_display_text(
    text: str,
    *,
    session_key: str | None = None,
    should_label_question_response_fn: Callable[..., bool] | None = None,
) -> str:
    visible = cleanup_assistant_display_artifacts(normalize_friend_style_output(visible_text(text))).strip()
    if not visible:
        return visible
    if should_label_question_response_fn is not None and should_label_question_response_fn(visible, session_key=session_key):
        return f"[질문] {visible}"
    return visible


def format_display_text_from_runtime(
    text: str,
    *,
    session_key: str | None = None,
    deps: ResponseOutputPolicyRuntimeDeps,
) -> str:
    return format_display_text(
        text,
        session_key=session_key,
        should_label_question_response_fn=(
            lambda visible_text: should_label_question_response_from_runtime(
                visible_text,
                session_key=session_key,
                deps=deps,
            )
        ),
    )


def should_label_question_response(
    text: str,
    *,
    session_key: str | None = None,
    session_state_snapshot_fn: Callable[[str | None], dict[str, Any]] | None = None,
) -> bool:
    visible = normalize_friend_style_output(visible_text(text)).strip()
    if not visible:
        return False
    if visible.startswith("[질문]"):
        return False
    if session_key is not None and session_state_snapshot_fn is not None:
        return bool(session_state_snapshot_fn(session_key).get("awaiting_user_reply"))
    return False


def should_label_question_response_from_runtime(
    text: str,
    *,
    session_key: str | None = None,
    deps: ResponseOutputPolicyRuntimeDeps,
) -> bool:
    session_state_snapshot_fn = None if deps is None else deps.session_state_snapshot_fn
    return should_label_question_response(
        text,
        session_key=session_key,
        session_state_snapshot_fn=session_state_snapshot_fn,
    )


def sanitize_model_output(
    text: str,
    *,
    stop_tokens: Iterable[str] = (),
    cleanup_artifacts_fn: Callable[[str], str] | None = None,
) -> str:
    rendered = text or ""
    rendered = re.sub(r"<think>.*?</think>", "", rendered, flags=re.DOTALL | re.IGNORECASE)
    rendered = re.sub(r"<think>.*$", "", rendered, flags=re.DOTALL | re.IGNORECASE)
    rendered = strip_model_channel_tags(rendered)
    for stop_token in stop_tokens:
        if stop_token:
            rendered = rendered.replace(stop_token, "")
    rendered = normalize_omnivoice_tags(rendered)
    _action, cleaned = parse_response_action_tag(rendered)
    rendered = normalize_friend_style_output(cleaned)
    if cleanup_artifacts_fn is not None:
        rendered = cleanup_artifacts_fn(rendered)
    return rendered


def sanitize_model_output_from_runtime(
    text: str,
    *,
    deps: ResponseOutputPolicyRuntimeDeps | None = None,
) -> str:
    stop_tokens = tuple() if deps is None else deps.model_output_stop_tokens
    cleanup_artifacts_fn = None if deps is None else deps.sanitize_model_output_cleanup_fn
    return sanitize_model_output(
        text,
        stop_tokens=stop_tokens,
        cleanup_artifacts_fn=cleanup_artifacts_fn,
    )


def strip_markdown_noise(text: str) -> str:
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"^[\-\*\d\.\)\s]+", "", text)
    return clean_text(text)


def looks_like_meta_line(text: str) -> bool:
    t = text.strip().lower()
    blocked_prefixes = (
        "thinking process",
        "analyze the request",
        "determine the",
        "draft",
        "option",
        "selecting",
        "refining",
        "final polish",
        "final output generation",
        "role:",
        "language:",
        "format:",
        "length:",
        "tone:",
        "content:",
        "input:",
        "wait",
        "let's",
        "let’s",
        "or:",
    )
    if any(t.startswith(prefix) for prefix in blocked_prefixes):
        return True
    if "**" in text:
        return True
    if len(re.findall(r"[A-Za-z]", text)) > len(re.findall(r"[가-힣]", text)) * 2:
        return True
    return False


def extract_answer_from_reasoning(
    reasoning: str,
    user_text: str,
    *,
    sanitize_output_fn: Callable[[str], str] | None = None,
    stop_tokens: Iterable[str] = (),
    cleanup_artifacts_fn: Callable[[str], str] | None = None,
) -> str:
    text = (
        sanitize_output_fn(reasoning)
        if sanitize_output_fn is not None
        else sanitize_model_output(
            reasoning,
            stop_tokens=stop_tokens,
            cleanup_artifacts_fn=cleanup_artifacts_fn,
        )
    )
    if not text:
        return ""

    text = text.replace("\r", "\n")
    candidates: list[str] = []

    quoted = re.findall(r"[\"“”'‘’]([^\"“”'‘’]{4,120})[\"“”'‘’]", text)
    for quoted_text in quoted:
        candidate = strip_markdown_noise(quoted_text)
        if not candidate or not re.search(r"[가-힣]", candidate):
            continue
        if looks_like_meta_line(candidate):
            continue
        if clean_text(candidate) == clean_text(user_text):
            continue
        candidates.append(candidate)

    explicit_patterns = [
        r"(?:최종\s*답변|답변|response|assistant)\s*[:：]\s*([^\n]{4,120})",
        r"(?:최종\s*출력|final\s*answer)\s*[:：]\s*([^\n]{4,120})",
    ]
    for pattern in explicit_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            candidate = strip_markdown_noise(match)
            if not candidate or not re.search(r"[가-힣]", candidate):
                continue
            if looks_like_meta_line(candidate):
                continue
            if clean_text(candidate) == clean_text(user_text):
                continue
            candidates.append(candidate)

    seen = set()
    filtered: list[str] = []
    for candidate in candidates:
        candidate = clean_text(candidate).strip("\"'“”‘’")
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if len(candidate) < 6 or len(candidate) > 120:
            continue
        filtered.append(candidate)

    return filtered[-1] if filtered else ""


def extract_answer_from_reasoning_from_runtime(
    reasoning: str,
    user_text: str,
    *,
    deps: ResponseOutputPolicyRuntimeDeps | None = None,
) -> str:
    return extract_answer_from_reasoning(
        reasoning,
        user_text,
        sanitize_output_fn=lambda text: sanitize_model_output_from_runtime(text, deps=deps),
    )


__all__ = [
    "answer_contains_minecraft_leak",
    "answer_simple_local_chat_query",
    "cleanup_assistant_display_artifacts",
    "extract_answer_from_reasoning",
    "fallback_for_unrequested_minecraft_leak",
    "fallback_for_unrequested_minecraft_leak_from_runtime",
    "format_display_text",
    "format_display_text_from_runtime",
    "looks_like_meta_line",
    "normalize_friend_style_output",
    "parse_response_action_tag",
    "extract_answer_from_reasoning_from_runtime",
    "sanitize_model_output",
    "sanitize_model_output_from_runtime",
    "sanitize_unrequested_minecraft_leak_from_runtime",
    "sanitize_unrequested_minecraft_leak",
    "should_label_question_response",
    "should_label_question_response_from_runtime",
    "strip_markdown_noise",
    "user_explicitly_mentions_minecraft",
    "user_explicitly_mentions_minecraft_from_runtime",
    "answer_contains_minecraft_leak_from_runtime",
    "ResponseOutputPolicyRuntimeDeps",
]
