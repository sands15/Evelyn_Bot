from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class SearchAnswerRuntimeDeps:
    model_name: str
    llm_server_url: str
    chat_content_format: str
    stop_tokens: tuple[str, ...] | list[str]
    get_http_session: Callable[[], Awaitable[Any]]
    build_chat_messages: Callable[..., list[dict[str, Any]]]
    client_timeout_factory: Callable[..., Any]
    clean_text: Callable[[str], str]
    sanitize_model_output: Callable[[str], str]
    strip_search_answer_sources: Callable[[str], str]


def _fallback_answer(results: list[dict], *, deps: SearchAnswerRuntimeDeps) -> str:
    return deps.strip_search_answer_sources(f"찾아보니까 {results[0].get('snippet', '')}")


async def answer_from_search_results_from_runtime(
    query: str,
    results: list[dict],
    *,
    deps: SearchAnswerRuntimeDeps,
) -> str:
    if not results:
        return "찾아봤는데 지금 바로 쓸 만한 결과를 못 찾았어. 검색어를 조금 더 구체적으로 말해주면 다시 찾아볼게."

    session = await deps.get_http_session()
    payload = {
        "model": deps.model_name,
        "messages": deps.build_chat_messages(
            [
                {
                    "role": "system",
                    "content": (
                        "너는 검색 결과를 짧게 정리하는 비서다. 검색은 이미 끝났다. "
                        "'찾아볼게', '찾는 중', '확인해볼게' 같은 표현은 절대 쓰지 마라. "
                        "찾은 내용만 한국어로 바로 말해라. 출처, 링크, URL, 참고자료 목록, 괄호 citation은 절대 출력하지 마라."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"사용자 질문:\n{query}\n\n"
                        + "검색 결과:\n"
                        + "\n".join(
                            f"- {deps.clean_text(row.get('title', ''))} | {deps.clean_text(row.get('snippet', ''))}"
                            for row in results[:5]
                        )
                    ),
                },
            ],
            content_format=deps.chat_content_format,
        ),
        "temperature": 0.1,
        "max_tokens": 220,
        "stream": False,
        "cache_prompt": True,
        "stop": list(deps.stop_tokens),
    }

    async with session.post(
        deps.llm_server_url,
        json=payload,
        timeout=deps.client_timeout_factory(total=45),
    ) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"검색 정리 LLM 오류: {resp.status} / {error_text[:300]}")
        data = await resp.json()

    choices = data.get("choices", [])
    if not choices:
        return _fallback_answer(results, deps=deps)

    message = choices[0].get("message", {})
    answer = deps.sanitize_model_output(message.get("content", ""))
    if answer:
        return deps.strip_search_answer_sources(answer)
    return _fallback_answer(results, deps=deps)


__all__ = ["SearchAnswerRuntimeDeps", "answer_from_search_results_from_runtime"]
