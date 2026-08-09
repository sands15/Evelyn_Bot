from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class LlmWarmupRuntimeDeps:
    get_http_session: Callable[[], Awaitable[Any]]
    client_timeout: Callable[..., Any]
    mark_startup_component: Callable[[str, str, str], Any]
    llm_server_url: str
    model_name: str
    main_llm_chat_content_format: str
    voice_llm_max_tokens: int
    main_llm_stop_tokens: tuple[str, ...] | list[str]
    build_chat_messages: Callable[..., Any]
    decode_sse_stream_line: Callable[[bytes], dict[str, Any] | None]
    log: Callable[..., Any] = print


async def warmup_llm_from_runtime(*, deps: LlmWarmupRuntimeDeps) -> None:
    deps.mark_startup_component("main_warmup", "running", "Main LLM warmup request")
    session = await deps.get_http_session()
    payload = {
        "model": deps.model_name,
        "messages": deps.build_chat_messages(
            [{"role": "user", "content": "짧게: 준비됐으면 '응'만 답해."}],
            content_format=deps.main_llm_chat_content_format,
        ),
        "temperature": 0.0,
        "max_tokens": min(8, deps.voice_llm_max_tokens),
        "stream": True,
        "cache_prompt": True,
        "stop": list(deps.main_llm_stop_tokens),
    }
    deps.log("[STARTUP] llm_warmup_begin")
    async with session.post(deps.llm_server_url, json=payload, timeout=deps.client_timeout(total=20)) as resp:
        if resp.status != 200:
            deps.mark_startup_component("main_warmup", "failed", "llm_warmup_failed")
            raise RuntimeError("LLM warmup failed")
        async for raw_line in resp.content:
            event = deps.decode_sse_stream_line(raw_line)
            if not event or event.get("done"):
                continue
            if event.get("delta_text"):
                deps.mark_startup_component("main_warmup", "done", "")
                deps.log("[STARTUP] llm_warmup_done")
                return
    deps.mark_startup_component("main_warmup", "done", "no streamed chunk")
    deps.log("[STARTUP] llm_warmup_done_no_chunk")
