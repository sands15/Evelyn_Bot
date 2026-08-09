from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .conversation_memory_exposure import (
    capture_combined_memory_exposure,
    filter_conversation_history_for_memory_exposure,
)
from .memory_exposure import current_memory_exposure_position

@dataclass(frozen=True)
class ControlPageUiRuntimeDeps:
    memory_index_dir: Path
    control_page_host: str
    control_page_port: int
    local_control_guild_id: int
    local_control_guild_name: str
    control_page_welcome_fallback: str
    clean_text: Callable[[str], str]
    sanitize_control_page_welcome_text_payload: Callable[[str, str], str]
    control_page_ui_command_store: Any
    control_page_chat_log_store: Any


@dataclass(frozen=True)
class ControlPageWelcomeRuntimeDeps:
    effective_guild_name: Callable[[Any | None], str]
    effective_guild_id: Callable[[Any | None], int]
    build_main_llm_payload: Callable[..., dict[str, Any]]
    model_name: str
    main_llm_chat_content_format: str
    main_llm_stop_tokens: tuple[str, ...] | list[str]
    get_http_session: Callable[[], Awaitable[Any]]
    client_timeout_factory: Callable[..., Any]
    welcome_llm_timeout_sec: float
    llm_server_url: str
    extract_main_llm_answer_from_choice: Callable[..., tuple[str, str, str]]
    sanitize_model_output: Callable[[str], str]
    parse_response_action_tag: Callable[[str], Any]
    extract_answer_from_reasoning: Callable[[str, str], str]
    sanitize_welcome_text: Callable[[str], str]
    record_model_call_trace: Callable[..., None]
    monotonic: Callable[[], float]
    welcome_fallback: str
    clean_text: Callable[[str], str]
    log: Callable[[str], Any]


async def generate_control_page_welcome_text_from_runtime(
    guild: Any | None,
    *,
    deps: ControlPageWelcomeRuntimeDeps,
) -> str:
    guild_name = deps.effective_guild_name(guild)
    user_text = "컨트롤 페이지 첫 화면에 띄울 짧은 환영문구를 하나만 만들어줘."
    prompt = (
        "너는 이블린(E.V.E.L.Y.N)이다. 정훈이 컨트롤 페이지를 처음 열었을 때 보일 첫 말풍선을 만든다.\n"
        "조건:\n"
        "- 한국어 한 문장만 출력한다.\n"
        "- 18~55자 정도로 짧게 쓴다.\n"
        "- 살짝 재치있고 따뜻하지만 과장하지 않는다.\n"
        "- 명령어 설명, /memory 안내, 기능 소개, 마크다운, 따옴표, 이모지는 쓰지 않는다.\n"
        "- 현재 상태를 확인한 척하지 않는다.\n"
        f"- 현재 공간 이름: {guild_name}\n"
    )
    payload = deps.build_main_llm_payload(
        model_name=deps.model_name,
        messages=[],
        final_user_text=prompt,
        source="control_page",
        stream=False,
        content_format=deps.main_llm_chat_content_format,
        temperature=0.65,
        max_tokens=72,
        stop_tokens=deps.main_llm_stop_tokens,
    )
    started_at = deps.monotonic()
    try:
        session = await deps.get_http_session()
        timeout = deps.client_timeout_factory(total=deps.welcome_llm_timeout_sec)
        async with session.post(deps.llm_server_url, json=payload, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError("control_page_welcome_failed")
            data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("LLM returned empty choices")
        answer, _answer_source, _finish_reason = deps.extract_main_llm_answer_from_choice(
            choices[0],
            user_text,
            sanitize_output=deps.sanitize_model_output,
            parse_response_action_tag=deps.parse_response_action_tag,
            extract_answer_from_reasoning=deps.extract_answer_from_reasoning,
        )
        welcome = deps.sanitize_welcome_text(answer)
        deps.record_model_call_trace(
            model_role="main_llm",
            purpose="control_page_welcome",
            hot_path=False,
            started_at=started_at,
            success=True,
            model_name=deps.model_name,
            endpoint=deps.llm_server_url,
            source="control_page",
            guild_id=deps.effective_guild_id(guild),
        )
        return welcome
    except Exception as exc:
        deps.record_model_call_trace(
            model_role="main_llm",
            purpose="control_page_welcome",
            hot_path=False,
            started_at=started_at,
            success=False,
            error=type(exc).__name__,
            model_name=deps.model_name,
            endpoint=deps.llm_server_url,
            source="control_page",
            guild_id=deps.effective_guild_id(guild),
        )
        deps.log(f"[CONTROL PAGE] welcome_generation_failed errorType={type(exc).__name__}")
        return deps.clean_text(deps.welcome_fallback)


def enqueue_control_page_ui_command_from_runtime(
    action: str,
    *,
    panel_id: str | None,
    deps: ControlPageUiRuntimeDeps,
) -> dict[str, Any]:
    return deps.control_page_ui_command_store.enqueue(action, panel_id=panel_id)


def build_control_page_panel_state_from_runtime(deps: ControlPageUiRuntimeDeps) -> dict[str, Any]:
    return deps.control_page_ui_command_store.panel_state()


def control_page_local_url_from_runtime(deps: ControlPageUiRuntimeDeps) -> str:
    return f"http://{deps.control_page_host}:{deps.control_page_port}/"


def control_page_session_key_from_runtime(
    guild_id: int | None,
    deps: ControlPageUiRuntimeDeps,
) -> str:
    if guild_id is None or int(guild_id) == deps.local_control_guild_id:
        return "control-page:local"
    return f"control-page:{int(guild_id)}"


def control_page_effective_guild_id_from_runtime(
    guild: Any,
    deps: ControlPageUiRuntimeDeps,
) -> int:
    return int(getattr(guild, "id", deps.local_control_guild_id) or deps.local_control_guild_id)


def control_page_effective_guild_name_from_runtime(
    guild: Any,
    deps: ControlPageUiRuntimeDeps,
) -> str:
    if guild is None:
        return deps.local_control_guild_name
    return deps.clean_text(str(getattr(guild, "name", "") or "")) or deps.local_control_guild_name


def append_control_page_chat_log_from_runtime(
    guild_id: int,
    role: str,
    author: str,
    text: str,
    deps: ControlPageUiRuntimeDeps,
    *,
    memory_receipt_ref: Any = None,
) -> None:
    deps.control_page_chat_log_store.append(
        guild_id,
        role,
        author,
        text,
        memory_receipt_ref,
    )


def get_control_page_chat_log_from_runtime(guild_id: int, deps: ControlPageUiRuntimeDeps) -> list[dict[str, Any]]:
    outcome = filter_conversation_history_for_memory_exposure(
        deps.control_page_chat_log_store.get(guild_id),
        memory_index_dir=deps.memory_index_dir,
    )
    capture_combined_memory_exposure(
        current_memory_exposure_position(),
        outcome.memory_exposure_position,
    )
    public_rows: list[dict[str, Any]] = []
    for message in outcome.messages:
        public_message = dict(message)
        public_message.pop("memoryReceipt", None)
        public_message.pop("memoryReceiptRef", None)
        public_message.pop("_memoryReceiptRef", None)
        public_rows.append(public_message)
    return public_rows


def sanitize_control_page_welcome_text_from_runtime(text: str, deps: ControlPageUiRuntimeDeps) -> str:
    return deps.sanitize_control_page_welcome_text_payload(
        text,
        fallback=deps.control_page_welcome_fallback,
    )


__all__ = [
    "ControlPageUiRuntimeDeps",
    "ControlPageWelcomeRuntimeDeps",
    "append_control_page_chat_log_from_runtime",
    "build_control_page_panel_state_from_runtime",
    "control_page_effective_guild_id_from_runtime",
    "control_page_effective_guild_name_from_runtime",
    "control_page_local_url_from_runtime",
    "control_page_session_key_from_runtime",
    "enqueue_control_page_ui_command_from_runtime",
    "get_control_page_chat_log_from_runtime",
    "generate_control_page_welcome_text_from_runtime",
    "sanitize_control_page_welcome_text_from_runtime",
]
