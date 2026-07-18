from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .control_page_guild_runtime import ControlPageGuildSelectionRuntimeDeps
from .control_page_state import sanitize_control_page_welcome_text_payload
from .control_page_ui_runtime import ControlPageUiRuntimeDeps, ControlPageWelcomeRuntimeDeps
from .skills.routing import build_main_llm_payload, extract_main_llm_answer_from_choice
from .text import clean_text


@dataclass(frozen=True)
class ControlPageUiDependencyCompositionDeps:
    control_page: Callable[[], Any]
    control_page_host: str
    control_page_port: int
    local_control_guild_id: int
    local_control_guild_name: str
    control_page_welcome_fallback: str
    control_page_ui_command_store: Any
    control_page_chat_log_store: Any
    get_requested_guild: Callable[[int], Any]
    bot_guilds: Callable[[], list[Any]]
    tracked_tts_playback_guild_ids: Callable[[], Any]
    get_tracked_tts_playback: Callable[[int], Any]
    get_active_session_user_id: Callable[[str], Any]
    get_guild_member: Callable[..., Any]
    effective_guild_id: Callable[..., int]
    model_name: str
    main_llm_chat_content_format: str
    main_llm_stop_tokens: tuple[str, ...]
    get_http_session: Callable[..., Any]
    client_timeout_factory: Callable[..., Any]
    welcome_llm_timeout_sec: float
    llm_server_url: str
    sanitize_model_output: Callable[..., str]
    parse_response_action_tag: Callable[..., Any]
    extract_answer_from_reasoning: Callable[..., str]
    record_model_call_trace: Callable[..., Any]
    monotonic: Callable[[], float]
    log: Callable[..., Any] = print


class ControlPageUiDependencyComposition:
    """Builds UI, guild-selection, and welcome contracts for the Control Page."""

    def __init__(self, deps: ControlPageUiDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_control_page_ui_runtime_deps(self) -> ControlPageUiRuntimeDeps:
        deps = self.deps
        return ControlPageUiRuntimeDeps(
            control_page_host=deps.control_page_host,
            control_page_port=deps.control_page_port,
            local_control_guild_id=deps.local_control_guild_id,
            local_control_guild_name=deps.local_control_guild_name,
            control_page_welcome_fallback=deps.control_page_welcome_fallback,
            clean_text=clean_text,
            sanitize_control_page_welcome_text_payload=sanitize_control_page_welcome_text_payload,
            control_page_ui_command_store=deps.control_page_ui_command_store,
            control_page_chat_log_store=deps.control_page_chat_log_store,
        )

    def build_control_page_guild_selection_runtime_deps(
        self,
    ) -> ControlPageGuildSelectionRuntimeDeps:
        deps = self.deps
        return ControlPageGuildSelectionRuntimeDeps(
            get_requested_guild=deps.get_requested_guild,
            bot_guilds=deps.bot_guilds,
            tracked_tts_playback_guild_ids=deps.tracked_tts_playback_guild_ids,
            get_tracked_tts_playback=deps.get_tracked_tts_playback,
            get_active_session_user_id=deps.get_active_session_user_id,
            get_guild_member=deps.get_guild_member,
            clean_text=clean_text,
        )

    def build_control_page_welcome_runtime_deps(self) -> ControlPageWelcomeRuntimeDeps:
        deps = self.deps
        control_page = deps.control_page()
        return ControlPageWelcomeRuntimeDeps(
            effective_guild_name=control_page.effective_guild_name,
            effective_guild_id=deps.effective_guild_id,
            build_main_llm_payload=build_main_llm_payload,
            model_name=deps.model_name,
            main_llm_chat_content_format=deps.main_llm_chat_content_format,
            main_llm_stop_tokens=deps.main_llm_stop_tokens,
            get_http_session=deps.get_http_session,
            client_timeout_factory=deps.client_timeout_factory,
            welcome_llm_timeout_sec=deps.welcome_llm_timeout_sec,
            llm_server_url=deps.llm_server_url,
            extract_main_llm_answer_from_choice=extract_main_llm_answer_from_choice,
            sanitize_model_output=deps.sanitize_model_output,
            parse_response_action_tag=deps.parse_response_action_tag,
            extract_answer_from_reasoning=deps.extract_answer_from_reasoning,
            sanitize_welcome_text=control_page.sanitize_welcome_text,
            record_model_call_trace=deps.record_model_call_trace,
            monotonic=deps.monotonic,
            welcome_fallback=deps.control_page_welcome_fallback,
            clean_text=clean_text,
            log=deps.log,
        )


__all__ = [
    "ControlPageUiDependencyComposition",
    "ControlPageUiDependencyCompositionDeps",
]
