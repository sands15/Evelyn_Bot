from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .memory_deletion_journal import MemoryDeletionPosition
from .memory_deletion_outbound import memory_deletion_outbound_request


@dataclass(frozen=True)
class JsonLlmRequestRuntimeDeps:
    model_name: str
    endpoint: str
    model_role: str
    error_label: str
    get_http_session: Callable[[], Awaitable[Any]]
    client_timeout_factory: Callable[..., Any]
    monotonic: Callable[[], float]
    clean_text: Callable[[str], str]
    extract_json_object: Callable[[str], dict]
    record_model_call_trace: Callable[..., None]


async def ask_json_llm_from_runtime(
    messages: list[dict],
    *,
    deps: JsonLlmRequestRuntimeDeps,
    max_tokens: int,
    timeout_seconds: float,
    purpose: str,
    hot_path: bool,
    turn_id: str | None = None,
    session_key: str | None = None,
    source: str | None = None,
    guild_id: int | None = None,
    memory_deletion_position: MemoryDeletionPosition | None = None,
    memory_boundary_required: bool = False,
    memory_deletion_index_dir: Path | None = None,
) -> dict:
    session = await deps.get_http_session()
    payload = {
        "model": deps.model_name,
        "messages": messages,
        "temperature": 0.0 if hot_path else 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if hot_path:
        payload["cache_prompt"] = True
        payload["response_format"] = {"type": "json_object"}
    timeout = deps.client_timeout_factory(total=timeout_seconds)
    started_at = deps.monotonic()

    async with memory_deletion_outbound_request(
        session.post,
        deps.endpoint,
        json=payload,
        timeout=timeout,
        expected_position=memory_deletion_position,
        memory_boundary_required=memory_boundary_required,
        memory_index_dir=memory_deletion_index_dir,
    ) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"{deps.error_label} 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            result: dict = {}
            deps.record_model_call_trace(
                model_role=deps.model_role,
                purpose=purpose,
                hot_path=hot_path,
                started_at=started_at,
                success=True,
                model_name=deps.model_name,
                endpoint=deps.endpoint,
                turn_id=turn_id,
                session_key=session_key,
                source=source,
                guild_id=guild_id,
            )
            return result

        msg = choices[0].get("message", {})
        text = deps.clean_text(msg.get("content", "") or msg.get("reasoning_content", ""))
        result = deps.extract_json_object(text)
        deps.record_model_call_trace(
            model_role=deps.model_role,
            purpose=purpose,
            hot_path=hot_path,
            started_at=started_at,
            success=True,
            model_name=deps.model_name,
            endpoint=deps.endpoint,
            turn_id=turn_id,
            session_key=session_key,
            source=source,
            guild_id=guild_id,
        )
        return result
