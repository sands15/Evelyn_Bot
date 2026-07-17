from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .memory_update_runtime import MemoryUpdateRuntimeDeps
from .search_answer_runtime import SearchAnswerRuntimeDeps
from .search_followup_runtime import SearchFollowupRuntimeDeps


@dataclass(frozen=True)
class SearchMemoryDependencyCompositionDeps:
    write_memory_turn_records: Callable[..., Any]
    vision_memory_write_enabled: bool
    record_self_identity_turn: Callable[..., Any]
    append_raw_transcript_rows: Callable[..., Any]
    append_turn_rows_to_memory_vault: Callable[..., Any]
    schedule_memory_vault_maintenance: Callable[..., Any]
    memory_refresh_inputs_for_turn: Callable[..., Any]
    get_conversation_history: Callable[..., Any]
    session_last_active_at: MutableMapping[str, float]
    needs_search_or_deep_routing: Callable[..., bool]
    build_memory_writer_decision_for_turn: Callable[..., Any]
    build_memory_writer_decision: Callable[..., Any]
    build_memory_writer_decision_payload: Callable[..., Any]
    plan_memory_writebehind_schedule: Callable[..., Any]
    runtime_session_key: Callable[..., str]
    memory_writebehind_task_key: Callable[..., str]
    should_replace_existing_memory_task: Callable[..., bool]
    mark_memory_writer_status: Callable[..., Any]
    memory_writebehind_status_log: Any
    background_memory_tasks: MutableMapping[str, Any]
    create_turn_scoped_task: Callable[..., Any]
    run_memory_writebehind_steps: Callable[..., Any]
    update_long_term_memory: Callable[..., Any]
    update_cognitive_state: Callable[..., Any]
    model_name: str
    llm_server_url: str
    chat_content_format: str
    stop_tokens: tuple[str, ...] | list[str]
    get_http_session: Callable[..., Any]
    build_chat_messages: Callable[..., Any]
    client_timeout_factory: Callable[..., Any]
    clean_text: Callable[[str], str]
    sanitize_model_output: Callable[..., str]
    strip_search_answer_sources: Callable[..., str]
    bot: Any
    discord_object_factory: Callable[..., Any]
    session_followup_targets: MutableMapping[str, Any]
    background_search_tasks: MutableMapping[str, Any]
    inflight_search_tasks: MutableMapping[str, Any]
    apply_runtime_mode: Callable[..., Any]
    parse_response_action_tag: Callable[..., Any]
    answer_promises_search: Callable[..., bool]
    build_search_query: Callable[..., str]
    remember_session_followup_target: Callable[..., Any]
    memory_summary_path: Callable[..., Any]
    read_text_file: Callable[..., str]
    compact_working_summary: Callable[..., str]
    search_duckduckgo: Callable[..., Any]
    answer_from_search_results: Callable[..., Any]
    resolve_open_question_rows: Callable[..., Any]
    write_json_file: Callable[..., Any]
    cognitive_state_path: Callable[..., Any]
    send_discord_text: Callable[..., Any]
    format_display_text: Callable[..., str]
    speak_answer: Callable[..., Any]
    current_turn_id: Callable[..., Any]
    append_history: Callable[..., Any]
    schedule_memory_update: Callable[..., Any]
    attach_current_task: Callable[..., Any]
    detach_task: Callable[..., Any]
    record_search_followup_queued: Callable[..., Any]
    log: Callable[..., Any]


class SearchMemoryDependencyComposition:
    """Builds memory-update, search-answer, and search-follow-up contracts."""

    def __init__(self, deps: SearchMemoryDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_memory_update_runtime_deps(self) -> MemoryUpdateRuntimeDeps:
        deps = self.deps
        return MemoryUpdateRuntimeDeps(
            write_memory_turn_records=deps.write_memory_turn_records,
            vision_memory_write_enabled=deps.vision_memory_write_enabled,
            record_self_identity_turn=deps.record_self_identity_turn,
            append_raw_transcript_rows=deps.append_raw_transcript_rows,
            append_turn_rows_to_memory_vault=deps.append_turn_rows_to_memory_vault,
            schedule_memory_vault_maintenance=deps.schedule_memory_vault_maintenance,
            memory_refresh_inputs_for_turn=deps.memory_refresh_inputs_for_turn,
            get_conversation_history=deps.get_conversation_history,
            session_last_active_at=deps.session_last_active_at,
            needs_search_or_deep_routing=deps.needs_search_or_deep_routing,
            build_memory_writer_decision_for_turn=deps.build_memory_writer_decision_for_turn,
            build_memory_writer_decision=deps.build_memory_writer_decision,
            build_memory_writer_decision_payload=deps.build_memory_writer_decision_payload,
            plan_memory_writebehind_schedule=deps.plan_memory_writebehind_schedule,
            runtime_session_key=deps.runtime_session_key,
            memory_writebehind_task_key=deps.memory_writebehind_task_key,
            should_replace_existing_memory_task=deps.should_replace_existing_memory_task,
            mark_memory_writer_status=deps.mark_memory_writer_status,
            memory_writebehind_status_log=deps.memory_writebehind_status_log,
            background_memory_tasks=deps.background_memory_tasks,
            create_turn_scoped_task=deps.create_turn_scoped_task,
            run_memory_writebehind_steps=deps.run_memory_writebehind_steps,
            update_long_term_memory=deps.update_long_term_memory,
            update_cognitive_state=deps.update_cognitive_state,
            log=deps.log,
        )

    def build_search_answer_runtime_deps(self) -> SearchAnswerRuntimeDeps:
        deps = self.deps
        return SearchAnswerRuntimeDeps(
            model_name=deps.model_name,
            llm_server_url=deps.llm_server_url,
            chat_content_format=deps.chat_content_format,
            stop_tokens=deps.stop_tokens,
            get_http_session=deps.get_http_session,
            build_chat_messages=deps.build_chat_messages,
            client_timeout_factory=deps.client_timeout_factory,
            clean_text=deps.clean_text,
            sanitize_model_output=deps.sanitize_model_output,
            strip_search_answer_sources=deps.strip_search_answer_sources,
        )

    def build_search_followup_runtime_deps(self) -> SearchFollowupRuntimeDeps:
        deps = self.deps
        return SearchFollowupRuntimeDeps(
            bot=deps.bot,
            discord_object_factory=deps.discord_object_factory,
            session_followup_targets=deps.session_followup_targets,
            background_search_tasks=deps.background_search_tasks,
            inflight_search_tasks=deps.inflight_search_tasks,
            apply_runtime_mode=deps.apply_runtime_mode,
            parse_response_action_tag=deps.parse_response_action_tag,
            answer_promises_search=deps.answer_promises_search,
            build_search_query=deps.build_search_query,
            runtime_session_key=deps.runtime_session_key,
            remember_session_followup_target=deps.remember_session_followup_target,
            get_conversation_history=deps.get_conversation_history,
            memory_summary_path=deps.memory_summary_path,
            read_text_file=deps.read_text_file,
            compact_working_summary=deps.compact_working_summary,
            search_duckduckgo=deps.search_duckduckgo,
            answer_from_search_results=deps.answer_from_search_results,
            resolve_open_question_rows=deps.resolve_open_question_rows,
            write_json_file=deps.write_json_file,
            cognitive_state_path=deps.cognitive_state_path,
            send_discord_text=deps.send_discord_text,
            format_display_text=deps.format_display_text,
            speak_answer=deps.speak_answer,
            current_turn_id=deps.current_turn_id,
            append_history=deps.append_history,
            schedule_memory_update=deps.schedule_memory_update,
            create_turn_scoped_task=deps.create_turn_scoped_task,
            attach_current_task=deps.attach_current_task,
            detach_task=deps.detach_task,
            record_search_followup_queued=deps.record_search_followup_queued,
            log=deps.log,
        )


__all__ = ["SearchMemoryDependencyComposition", "SearchMemoryDependencyCompositionDeps"]
