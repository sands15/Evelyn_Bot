from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .control_page_contracts import memory_panel_reply
from .control_page_server import open_path_with_system, open_url_with_system
from .control_page_state import (
    build_control_page_autonomy_reply_payload,
    build_control_page_inventory_reply_payload,
    build_control_page_local_status_text_payload,
    build_control_page_minecraft_reply_payload,
    build_control_page_status_text_payload,
    build_control_page_voice_continuity_reply_payload,
    build_control_page_voice_status_reply_payload,
    control_page_open_memory_vault_tool_reply,
    execute_control_page_memory_tool,
    execute_control_page_minecraft_tool,
    execute_control_page_runtime_tool,
    execute_control_page_voice_tool,
    memory_vault_obsidian_url,
)
from .control_page_status_runtime import ControlPageStatusRuntimeDeps
from .control_page_tool_runtime import ControlPageToolRuntimeDeps
from .control_page_tools import (
    build_control_page_help_reply,
    control_page_tool_policy_error,
    control_page_tool_registry_prompt,
)
from .memory_vault import ensure_memory_vault_layout


@dataclass(frozen=True)
class ControlPageStatusToolCompositionDeps:
    memory_index_dir: Path
    control_page: Callable[[], Any]
    model_name: str
    router_model_name: str
    summary_model_name: str
    stt_model_name: str
    discord_enabled: bool
    bot_api_host: str
    bot_api_port: int
    control_page_local_url: Callable[..., str]
    voice_input_mode_status_line: Callable[..., str]
    local_mic_status_line: Callable[..., str]
    current_tts_target_name: Callable[..., str]
    is_tracked_tts_playback_active: Callable[[int], bool]
    local_tts_snapshot: Callable[[], dict[str, Any]]
    local_mic_runtime_state: Callable[[], dict[str, Any]]
    build_voice_pipeline_snapshot: Callable[..., dict[str, Any]]
    format_voice_continuity_detail_lines: Callable[..., Any]
    autonomy_engines: Mapping[int, Any]
    get_routed_autonomy_executor: Callable[..., Any]
    clean_text: Callable[[str], str]
    create_task: Callable[..., Any]
    restart_bot_process: Callable[..., Any]
    get_conversation_history: Callable[..., list[dict[str, Any]]]
    record_tool_assistant_turn: Callable[..., Any]
    control_page_effective_guild_id: Callable[..., int]
    control_page_session_key: Callable[..., str]
    system_prompt: str
    max_history_items: int
    active_conversation_text_sec: float
    router_llm_enabled: bool
    route_timeout_sec: float
    ask_router_llm: Callable[..., Any]
    current_turn_id: Callable[..., Any]
    schedule_local_shutdown: Callable[..., Any]
    schedule_stack_shutdown: Callable[..., Any]
    schedule_bot_shutdown: Callable[..., Any]
    set_input_mode: Callable[..., Any]
    restore_voice_channel: Callable[..., Any]
    reset_continuity_probe: Callable[..., Any]
    get_minecraft_world_lease_status: Callable[
        [],
        dict[str, Any],
    ]
    enable_mode: Callable[..., Any]
    disable_mode: Callable[..., Any]
    set_minecraft_goal: Callable[..., Any]
    format_position: Callable[..., str]
    log: Callable[..., Any] = print


class ControlPageStatusToolComposition:
    """Builds Control Page status and tool contracts from live runtime adapters."""

    def __init__(self, deps: ControlPageStatusToolCompositionDeps) -> None:
        self.deps = deps

    def build_control_page_status_runtime_deps(self) -> ControlPageStatusRuntimeDeps:
        deps = self.deps
        control_page = deps.control_page()
        return ControlPageStatusRuntimeDeps(
            model_name=deps.model_name,
            router_model_name=deps.router_model_name,
            summary_model_name=deps.summary_model_name,
            stt_model_name=deps.stt_model_name,
            discord_enabled=deps.discord_enabled,
            bot_api_host=deps.bot_api_host,
            bot_api_port=deps.bot_api_port,
            control_page_local_url=deps.control_page_local_url,
            voice_input_mode_status_line=deps.voice_input_mode_status_line,
            local_mic_status_line=deps.local_mic_status_line,
            current_tts_target_name=deps.current_tts_target_name,
            is_tracked_tts_playback_active=deps.is_tracked_tts_playback_active,
            local_tts_snapshot=deps.local_tts_snapshot,
            local_mic_runtime_state=deps.local_mic_runtime_state,
            build_voice_pipeline_snapshot=deps.build_voice_pipeline_snapshot,
            format_voice_continuity_detail_lines=deps.format_voice_continuity_detail_lines,
            build_status_text_payload=build_control_page_status_text_payload,
            build_local_status_text_payload=build_control_page_local_status_text_payload,
            build_voice_status_reply_payload=build_control_page_voice_status_reply_payload,
            build_voice_continuity_reply_payload=build_control_page_voice_continuity_reply_payload,
            get_control_page_minecraft_snapshot=control_page.safe_get_minecraft_snapshot,
            get_minecraft_world_lease_status=(
                deps.get_minecraft_world_lease_status
            ),
            build_control_page_inventory_reply_payload=build_control_page_inventory_reply_payload,
            build_control_page_minecraft_reply_payload=build_control_page_minecraft_reply_payload,
            get_autonomy_engine=deps.autonomy_engines.get,
            get_routed_autonomy_executor=deps.get_routed_autonomy_executor,
            build_control_page_autonomy_reply_payload=build_control_page_autonomy_reply_payload,
        )

    def build_control_page_tool_runtime_deps(self) -> ControlPageToolRuntimeDeps:
        deps = self.deps
        control_page = deps.control_page()
        return ControlPageToolRuntimeDeps(
            memory_index_dir=deps.memory_index_dir,
            clean_text=deps.clean_text,
            enqueue_control_page_ui_command=control_page.enqueue_ui_command,
            memory_panel_reply=memory_panel_reply,
            create_task=deps.create_task,
            restart_bot_process=deps.restart_bot_process,
            get_conversation_history=deps.get_conversation_history,
            record_tool_assistant_turn=deps.record_tool_assistant_turn,
            control_page_effective_guild_id=deps.control_page_effective_guild_id,
            control_page_session_key=deps.control_page_session_key,
            system_prompt=deps.system_prompt,
            max_history_items=deps.max_history_items,
            active_conversation_text_sec=deps.active_conversation_text_sec,
            router_llm_enabled=deps.router_llm_enabled,
            route_timeout_sec=deps.route_timeout_sec,
            control_page_tool_registry_prompt=control_page_tool_registry_prompt,
            ask_router_llm=deps.ask_router_llm,
            current_turn_id=deps.current_turn_id,
            log=deps.log,
            control_page_tool_policy_error=control_page_tool_policy_error,
            build_control_page_help_reply=build_control_page_help_reply,
            execute_control_page_memory_tool=execute_control_page_memory_tool,
            execute_control_page_runtime_tool=execute_control_page_runtime_tool,
            execute_control_page_voice_tool=execute_control_page_voice_tool,
            execute_control_page_minecraft_tool=execute_control_page_minecraft_tool,
            ensure_vault_layout=ensure_memory_vault_layout,
            open_vault_tool_reply=control_page_open_memory_vault_tool_reply,
            vault_obsidian_url=memory_vault_obsidian_url,
            open_url=open_url_with_system,
            open_path=open_path_with_system,
            guild_getter_runtime={
                "get_runtime_services": control_page.get_runtime_services,
                "build_local_status_text": control_page.build_local_status_text,
                "build_status_reply": control_page.build_status_reply,
                "schedule_local_shutdown": deps.schedule_local_shutdown,
                "schedule_stack_shutdown": deps.schedule_stack_shutdown,
                "schedule_bot_shutdown": deps.schedule_bot_shutdown,
                "build_autonomy_reply": control_page.build_autonomy_reply,
                "build_voice_status_reply": control_page.build_voice_status_reply,
                "set_input_mode": deps.set_input_mode,
                "input_mode_status_line": deps.voice_input_mode_status_line,
                "restore_voice_channel": deps.restore_voice_channel,
                "build_voice_continuity_reply": control_page.build_voice_continuity_reply,
                "reset_continuity_probe": deps.reset_continuity_probe,
                "build_inventory_reply": control_page.build_inventory_reply,
                "build_minecraft_reply": control_page.build_minecraft_reply,
                "enable_mode": deps.enable_mode,
                "disable_mode": deps.disable_mode,
                "set_minecraft_goal": deps.set_minecraft_goal,
                "format_position": deps.format_position,
            },
        )


__all__ = [
    "ControlPageStatusToolComposition",
    "ControlPageStatusToolCompositionDeps",
]
