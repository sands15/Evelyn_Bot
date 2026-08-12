from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .discord_command_session_runtime import DiscordCommandSessionRuntimeDeps
from .discord_text_turn import DiscordTextMessageHandlerDeps


@dataclass(frozen=True)
class DiscordAppDependencyCompositionDeps:
    process_commands: Callable[..., Any]
    bot_user: Callable[[], Any]
    is_thread_parent: Callable[[Any], bool]
    remember_session_followup_target: Callable[..., Any]
    get_guild_command_prefix: Callable[..., str]
    get_guild_command_only_channel_ids: Callable[..., list[int]]
    contains_wake_word: Callable[..., bool]
    is_session_active_for_user: Callable[..., bool]
    strip_voice_wake_word: Callable[..., str]
    empty_wake_text: str
    log_turn_event: Callable[..., Any]
    current_turn_id: Callable[..., Any]
    resolve_pending_proactive_question_for_turn: Callable[..., Any]
    conversation_ingress: Any
    session_locks: MutableMapping[str, Any]
    reply_slot_locks: MutableMapping[str, Any]
    reply_slot_admission_locks: MutableMapping[str, Any]
    begin_user_text_turn: Callable[..., Any]
    replace_room_turn_scope: Callable[..., Any]
    attach_current_task: Callable[..., Any]
    auto_join_voice: bool
    ensure_voice_client: Callable[..., Any]
    stream_text_reply: Callable[..., Any]
    strip_omnivoice_tags: Callable[..., str]
    execute_voice_delivery_plan: Callable[..., Any]
    detach_task: Callable[..., Any]
    clear_room_turn_scope: Callable[..., Any]
    session_speculative_policies: MutableMapping[str, Any]
    compute_runtime_mode: Callable[..., Any]
    record_context_pipeline_benchmark: Callable[..., Any]
    schedule_memory_update: Callable[..., Any]
    should_force_search_followup: Callable[..., bool]
    schedule_search_followup: Callable[..., Any]
    session_state_snapshot: Callable[..., Any]
    finish_assistant_text_turn: Callable[..., Any]
    commit_session_continuity: Callable[..., Any]
    commit_session_continuity_sync: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    record_runtime_error: Callable[..., Any]
    format_display_text: Callable[..., str]
    resolve_text_thread_id: Callable[..., int | None]
    make_text_session_key: Callable[..., str]
    start_new_turn: Callable[..., str]
    record_command_assistant_turn: Callable[..., Any]
    system_prompt: str
    max_history_items: int
    normal_ttl_sec: float
    question_ttl_sec: float
    log: Callable[..., Any]


class DiscordAppDependencyComposition:
    """Builds Discord text-message and command-session contracts."""

    def __init__(self, deps: DiscordAppDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_discord_text_message_handler_deps(self) -> DiscordTextMessageHandlerDeps:
        deps = self.deps
        return DiscordTextMessageHandlerDeps(
            process_commands=deps.process_commands,
            bot_user=deps.bot_user(),
            is_thread_parent=deps.is_thread_parent,
            remember_session_followup_target=deps.remember_session_followup_target,
            get_guild_command_prefix=deps.get_guild_command_prefix,
            get_guild_command_only_channel_ids=deps.get_guild_command_only_channel_ids,
            contains_wake_word=deps.contains_wake_word,
            is_session_active_for_user=deps.is_session_active_for_user,
            strip_voice_wake_word=deps.strip_voice_wake_word,
            empty_wake_text=deps.empty_wake_text,
            log_turn_event=deps.log_turn_event,
            current_turn_id=deps.current_turn_id,
            resolve_pending_proactive_question_for_turn=(
                deps.resolve_pending_proactive_question_for_turn
            ),
            claim_conversation_ingress=(
                deps.conversation_ingress.claim_discord_text
            ),
            conversation_ingress_recovery_context=(
                deps.conversation_ingress.recovery_context_for_scope
            ),
            mark_ingress_response_ready=(
                deps.conversation_ingress.mark_response_ready
            ),
            mark_ingress_delivery_inflight=(
                deps.conversation_ingress.mark_delivery_inflight
            ),
            mark_ingress_delivery_succeeded=(
                deps.conversation_ingress.mark_delivery_succeeded
            ),
            mark_ingress_delivery_ambiguous=(
                deps.conversation_ingress.mark_delivery_ambiguous
            ),
            begin_ingress_terminal_commit=(
                deps.conversation_ingress.begin_terminal_commit
            ),
            complete_ingress=deps.conversation_ingress.complete,
            session_locks=deps.session_locks,
            reply_slot_locks=deps.reply_slot_locks,
            reply_slot_admission_locks=(
                deps.reply_slot_admission_locks
            ),
            begin_user_text_turn=deps.begin_user_text_turn,
            replace_room_turn_scope=deps.replace_room_turn_scope,
            attach_current_task=deps.attach_current_task,
            auto_join_voice=deps.auto_join_voice,
            ensure_voice_client=deps.ensure_voice_client,
            stream_text_reply=deps.stream_text_reply,
            strip_omnivoice_tags=deps.strip_omnivoice_tags,
            execute_voice_delivery_plan=deps.execute_voice_delivery_plan,
            detach_task=deps.detach_task,
            clear_room_turn_scope=deps.clear_room_turn_scope,
            session_speculative_policies=deps.session_speculative_policies,
            compute_runtime_mode=deps.compute_runtime_mode,
            record_context_pipeline_benchmark=deps.record_context_pipeline_benchmark,
            schedule_memory_update=deps.schedule_memory_update,
            should_force_search_followup=deps.should_force_search_followup,
            schedule_search_followup=deps.schedule_search_followup,
            session_state_snapshot=deps.session_state_snapshot,
            finish_assistant_text_turn=deps.finish_assistant_text_turn,
            commit_session_continuity=(
                deps.commit_session_continuity
            ),
            log_voice_bottleneck_summary=deps.log_voice_bottleneck_summary,
            record_runtime_error=deps.record_runtime_error,
            format_display_text=deps.format_display_text,
            log=deps.log,
        )

    def build_discord_command_session_runtime_deps(
        self,
    ) -> DiscordCommandSessionRuntimeDeps:
        deps = self.deps
        return DiscordCommandSessionRuntimeDeps(
            resolve_text_thread_id=deps.resolve_text_thread_id,
            is_text_thread_parent=deps.is_thread_parent,
            make_text_session_key=deps.make_text_session_key,
            start_new_turn=deps.start_new_turn,
            record_command_assistant_turn=deps.record_command_assistant_turn,
            system_prompt=deps.system_prompt,
            max_history_items=deps.max_history_items,
            normal_ttl_sec=deps.normal_ttl_sec,
            question_ttl_sec=deps.question_ttl_sec,
            commit_session_continuity=deps.commit_session_continuity_sync,
            log=deps.log,
        )


__all__ = [
    "DiscordAppDependencyComposition",
    "DiscordAppDependencyCompositionDeps",
]
