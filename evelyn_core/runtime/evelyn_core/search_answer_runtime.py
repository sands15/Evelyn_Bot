from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .search_tools import render_search_results_for_user


@dataclass(frozen=True)
class SearchAnswerRuntimeDeps:
    model_name: str
    llm_server_url: str
    memory_index_dir: Path
    chat_content_format: str
    stop_tokens: tuple[str, ...] | list[str]
    get_http_session: Callable[[], Awaitable[Any]]
    build_chat_messages: Callable[..., list[dict[str, Any]]]
    client_timeout_factory: Callable[..., Any]
    clean_text: Callable[[str], str]
    sanitize_model_output: Callable[[str], str]
    strip_search_answer_sources: Callable[[str], str]


async def answer_from_search_results_from_runtime(
    query: str,
    results: list[dict],
    *,
    deps: SearchAnswerRuntimeDeps,
) -> str:
    _ = deps
    return render_search_results_for_user(query, results)


__all__ = ["SearchAnswerRuntimeDeps", "answer_from_search_results_from_runtime"]
