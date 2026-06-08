from __future__ import annotations

import json
import re
from typing import Any, Callable

from ...text import clean_text
from ...voice_pipeline import RouteDecision


def _extract_image_urls_from_text(text: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"url=(https?://[^\s;]+)", text):
        url = match.group(1).strip().rstrip(").,]")
        if url and url not in urls:
            urls.append(url)
    for match in re.finditer(r"https?://[^\s<>)]+", text):
        url = match.group(0).strip().rstrip(").,]")
        lowered = url.lower()
        if any(lowered.endswith(suffix) for suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif")) and url not in urls:
            urls.append(url)
    return urls[:4]


def _as_openai_text_content(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                normalized.append({"type": "text", "text": item["text"]})
            elif item.get("type") == "image_url":
                image_url = item.get("image_url")
                if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                    normalized.append({"type": "image_url", "image_url": {"url": image_url["url"]}})
                elif isinstance(image_url, str):
                    normalized.append({"type": "image_url", "image_url": {"url": image_url}})
        return normalized
    text = clean_text(str(value))
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if "[Attached Visual Inputs]" in text:
        for url in _extract_image_urls_from_text(text):
            content.append({"type": "image_url", "image_url": {"url": url}})
    return content


def build_chat_messages(
    messages: list[dict[str, Any]],
    *,
    content_format: str = "plain",
) -> list[dict[str, Any]]:
    if clean_text(content_format).lower() not in {"openai", "content-array", "content_array"}:
        return list(messages)

    normalized: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = clean_text(str(message.get("role") or "user")) or "user"
        content = message.get("content", "")
        if role == "user":
            content = _as_openai_text_content(content)
        normalized.append(
            {
                **message,
                "role": role,
                "content": content,
            }
        )
    return normalized


def build_main_llm_payload(
    *,
    model_name: str,
    messages: list[dict[str, Any]],
    final_user_text: str,
    source: str,
    stream: bool,
    content_format: str = "plain",
    temperature: float | None = None,
    max_tokens: int | None = None,
    stop_tokens: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    final_messages = build_chat_messages(
        messages + [{"role": "user", "content": final_user_text}],
        content_format=content_format,
    )
    payload = {
        "model": model_name,
        "messages": final_messages,
        "temperature": temperature if temperature is not None else (0.3 if source == "voice" else 0.1),
        "max_tokens": max_tokens,
        "stream": stream,
        "cache_prompt": True,
    }
    if stop_tokens:
        payload["stop"] = list(stop_tokens)
    return payload


def extract_main_llm_answer_from_choice(
    choice: dict[str, Any],
    user_text: str,
    *,
    sanitize_output: Callable[[Any], str],
    parse_response_action_tag: Callable[[str], tuple[str | None, str]],
    extract_answer_from_reasoning: Callable[[str, str], str],
) -> tuple[str, str, str]:
    msg = choice.get("message", {})
    raw_answer = msg.get("content", "")
    _response_action, answer = parse_response_action_tag(sanitize_output(raw_answer))
    reasoning = msg.get("reasoning_content", "")
    finish_reason = str(choice.get("finish_reason", "") or "")
    if answer:
        return answer, "answer", finish_reason
    extracted = extract_answer_from_reasoning(reasoning, user_text)
    if extracted:
        return extracted, "reasoning", finish_reason
    return "", "", finish_reason


def _extract_text_payload(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def extract_stream_delta_text(data: dict[str, Any]) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""
    choice = choices[0]
    delta = choice.get("delta") or {}
    content = _extract_text_payload(delta.get("content"))
    if content:
        return content
    message = choice.get("message") or {}
    content = _extract_text_payload(message.get("content"))
    if content:
        return content
    text = choice.get("text")
    return text if isinstance(text, str) else ""


def extract_stream_reasoning_text(data: dict[str, Any]) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""
    choice = choices[0]
    delta = choice.get("delta") or {}
    reasoning = _extract_text_payload(delta.get("reasoning_content"))
    if reasoning:
        return reasoning
    reasoning = _extract_text_payload(delta.get("reasoning"))
    if reasoning:
        return reasoning
    message = choice.get("message") or {}
    reasoning = _extract_text_payload(message.get("reasoning_content"))
    if reasoning:
        return reasoning
    reasoning = _extract_text_payload(message.get("reasoning"))
    return reasoning


def decode_sse_stream_line(raw_line: bytes) -> dict[str, Any] | None:
    line = raw_line.decode("utf-8", errors="ignore").strip()
    if not line or line.startswith(":"):
        return None
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line:
        return None
    if line == "[DONE]":
        return {"done": True}
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return {
        "done": False,
        "delta_text": extract_stream_delta_text(data),
        "reasoning_text": extract_stream_reasoning_text(data),
    }


def build_route_decision_from_state(
    *,
    cognitive_state: dict[str, Any] | None,
    source: str,
    user_text: str,
    policy_response: str | None,
    apply_ask_gating: Callable[..., dict[str, Any]],
    build_route_decision: Callable[..., RouteDecision],
) -> RouteDecision:
    gated_state = apply_ask_gating(cognitive_state, source=source) if cognitive_state is not None else None
    action = clean_text(str((gated_state or {}).get("action") or ("answer" if policy_response else "main_direct"))) or "answer"
    if action == "search_then_answer":
        route = "search_executor"
    elif action not in {"answer", "ask", "wait", "main_direct"} and not policy_response:
        route = action
    else:
        route = "policy_short_circuit" if policy_response else "main_direct"
    prompt_text = clean_text(str((gated_state or {}).get("question_for_user") or user_text)) or clean_text(user_text)
    return build_route_decision(
        action=action,
        route=route,
        source=source,
        prompt_text=prompt_text,
        user_visible_preface=clean_text(policy_response) or None,
        needs_main_llm=not bool(clean_text(policy_response)),
        needs_memory=not bool(clean_text(policy_response)),
        needs_runtime_state=True,
        needs_search=action == "search_then_answer",
        needs_tts=True,
        response_mode="short" if source == "voice" else "normal",
        priority="accuracy" if action in {"ask", "wait", "search_then_answer"} else "latency",
        should_interrupt_delivery=action in {"answer", "search_then_answer"},
    )


def should_await_user_reply_for_route(*, gated_state: dict[str, Any] | None, route_action: str) -> bool:
    action = clean_text(str((gated_state or {}).get("action") or route_action or ""))
    return action in {"ask", "wait", "search_then_answer"}
