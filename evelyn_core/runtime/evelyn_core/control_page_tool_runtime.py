from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .conversation_memory_exposure import (
    capture_combined_memory_exposure,
    filter_conversation_history_for_memory_exposure,
)
from .conversation_memory_receipt import (
    capture_conversation_memory_receipt_ref,
    current_conversation_memory_receipt_ref,
    not_used_memory_receipt_ref,
    reset_conversation_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from .memory_exposure import (
    current_memory_exposure_position,
    memory_exposure_guard,
    reset_memory_exposure_position,
)
from .memory_deletion_journal import MemoryDeletionJournalIntegrityError

@dataclass(frozen=True)
class ControlPageToolRuntimeDeps:
    memory_index_dir: Path
    clean_text: Callable[[str], str]
    enqueue_control_page_ui_command: Callable[..., dict[str, Any]]
    memory_panel_reply: Callable[[str], str]
    create_task: Callable[[Awaitable[Any]], Any]
    restart_bot_process: Callable[[], Awaitable[Any]]
    get_conversation_history: Callable[..., list[dict[str, Any]]]
    record_tool_assistant_turn: Callable[..., None]
    control_page_effective_guild_id: Callable[[Any], int]
    control_page_session_key: Callable[[int | None], str]
    system_prompt: str
    max_history_items: int
    active_conversation_text_sec: float
    router_llm_enabled: bool = True
    route_timeout_sec: float = 2.0
    control_page_tool_registry_prompt: Callable[[], str] | None = None
    ask_router_llm: Callable[..., Awaitable[dict[str, Any] | None]] | None = None
    current_turn_id: Callable[[str | None], str | None] | None = None
    log: Callable[..., Any] | None = None
    control_page_tool_policy_error: Callable[..., str | None] | None = None
    build_control_page_help_reply: Callable[[], str] | None = None
    execute_control_page_memory_tool: Callable[..., Awaitable[str | None]] | None = None
    execute_control_page_runtime_tool: Callable[..., Awaitable[str | None]] | None = None
    execute_control_page_voice_tool: Callable[..., Awaitable[str | None]] | None = None
    execute_control_page_minecraft_tool: Callable[..., Awaitable[str | None]] | None = None
    ensure_vault_layout: Callable[[], Any] | None = None
    open_vault_tool_reply: Callable[..., str] | None = None
    vault_obsidian_url: Callable[..., str] | None = None
    open_url: Callable[[str], None] | None = None
    open_path: Callable[[Any], None] | None = None
    guild_getter_runtime: dict[str, Any] | None = None


@dataclass(frozen=True)
class ControlPageInputRuntimeDeps:
    clean_text: Callable[[str], str]
    control_page_effective_guild_id: Callable[[Any], int]
    control_page_session_key: Callable[[int | None], str]
    cheap_control_page_tool_decision: Callable[[str], dict[str, Any] | None]
    execute_control_page_tool: Callable[[Any | None, dict[str, Any]], Awaitable[str]]
    remember_control_page_tool_turn: Callable[..., None]
    should_route_control_page_tool_candidate: Callable[[str], bool]
    decide_control_page_tool_call: Callable[..., Awaitable[dict[str, Any] | None]]
    control_page_tool_decision_from_llm: Callable[[dict[str, Any] | None], dict[str, Any] | None]
    control_page_tool_policy_error: Callable[..., str | None]
    control_page_tool_reply_from_execution: Callable[[dict[str, Any], str], str]
    should_force_search_query: Callable[[str], bool]
    answer_control_page_search_text: Callable[[Any | None, str], Awaitable[str]]
    answer_control_page_text: Callable[[Any | None, str], Awaitable[str]]


def execute_control_page_memory_panel_action_from_runtime(
    action: str,
    *,
    deps: ControlPageToolRuntimeDeps,
) -> str:
    cleaned_action = deps.clean_text(action).lower()
    if cleaned_action not in {"open", "close", "toggle"}:
        cleaned_action = "toggle"
    deps.enqueue_control_page_ui_command(cleaned_action, panel_id="memory")
    return deps.memory_panel_reply(cleaned_action)


def execute_control_page_restart_command_from_runtime(
    *,
    deps: ControlPageToolRuntimeDeps,
) -> str:
    deps.create_task(deps.restart_bot_process())
    return "응, 이블린 다시 시작할게. 잠깐만 기다려줘."


def recent_control_page_history_for_router_from_runtime(
    *,
    session_key: str,
    guild_id: int | None,
    limit: int = 6,
    deps: ControlPageToolRuntimeDeps,
) -> str:
    outcome = filter_conversation_history_for_memory_exposure(
        deps.get_conversation_history(
            system_prompt=deps.system_prompt,
            session_key=session_key,
            guild_id=guild_id,
        ),
        memory_index_dir=deps.memory_index_dir,
    )
    capture_combined_memory_exposure(
        current_memory_exposure_position(),
        outcome.memory_exposure_position,
    )
    capture_conversation_memory_receipt_ref(
        outcome.memory_receipt_ref
    )
    lines: list[str] = []
    bounded_limit = max(0, int(limit))
    recent_rows = outcome.messages[-bounded_limit:] if bounded_limit else ()
    for row in recent_rows:
        role = deps.clean_text(str(row.get("role") or ""))
        content = deps.clean_text(str(row.get("content") or ""))
        if role and content:
            lines.append(f"{role}: {content[:180]}")
    return "\n".join(lines)


def remember_control_page_tool_turn_from_runtime(
    guild: Any | None,
    user_text: str,
    reply_text: str,
    decision: dict[str, Any],
    *,
    deps: ControlPageToolRuntimeDeps,
    memory_receipt_ref: Any = None,
) -> None:
    guild_id = deps.control_page_effective_guild_id(guild)
    session_key = deps.control_page_session_key(guild_id)
    deps.record_tool_assistant_turn(
        session_key,
        user_text,
        reply_text,
        tool_name=deps.clean_text(str(decision.get("tool") or "")),
        system_prompt=deps.system_prompt,
        max_history_items=deps.max_history_items,
        guild_id=guild_id,
        ttl_sec=deps.active_conversation_text_sec,
        memory_receipt=(
            memory_receipt_ref
            if memory_receipt_ref is not None
            else unattributed_memory_receipt_ref()
        ),
    )


async def decide_control_page_tool_call_from_runtime(
    text: str,
    *,
    guild_id: int | None,
    session_key: str,
    deps: ControlPageToolRuntimeDeps,
) -> dict[str, Any] | None:
    if not deps.router_llm_enabled or deps.ask_router_llm is None:
        return None
    user_text = deps.clean_text(text)
    if not user_text:
        return None
    registry_prompt = deps.control_page_tool_registry_prompt() if deps.control_page_tool_registry_prompt else ""
    recent = recent_control_page_history_for_router_from_runtime(
        session_key=session_key,
        guild_id=guild_id,
        deps=deps,
    ) or "(none)"
    messages = [
        {
            "role": "system",
            "content": (
                "You are Evelyn's control-page tool router. "
                "Only classify ambiguous short control-page commands. "
                "Return exactly one JSON object and no other text. "
                "Available allowlisted tools: "
                f"{registry_prompt}. "
                "The router may choose only these tools; never invent tools, shell commands, paths, or code. "
                "For control_page.memory_panel, arguments must be {\"action\":\"open|close|toggle\"}. "
                "If the user is clearly asking for a tool, return "
                '{"tool_call":{"name":"control_page.memory_panel","arguments":{"action":"open"}},"confidence":0.92,"reply":"응, 메모리 패널 열어둘게."}. '
                "If no UI tool should be called, return "
                '{"tool_call":null,"confidence":0.0,"reply":""}. '
                "Do not call a tool for ordinary questions, explanations, styling requests, implementation requests, or discussion. "
                "Never call high-risk tools; ask for explicit slash commands instead. "
                "When you do call a UI tool, write reply in Evelyn's style: Korean, warm and sharp, casual 반말, "
                "one short sentence, no stiff '~습니다' or '~입니다' endings, no extra explanation."
            ),
        },
        {"role": "system", "content": "Recent conversation:\n" + recent},
        {"role": "user", "content": user_text},
    ]
    try:
        exposure_position = current_memory_exposure_position()
        with memory_exposure_guard(
            expected_position=exposure_position,
            required=exposure_position is not None,
            index_dir=deps.memory_index_dir,
        ):
            return await deps.ask_router_llm(
                messages,
                max_tokens=180,
                timeout_seconds=min(deps.route_timeout_sec, 2.0),
                purpose="control_page_ui_tool",
                hot_path=True,
                turn_id=(
                    deps.current_turn_id(session_key)
                    if deps.current_turn_id
                    else None
                ),
                session_key=session_key,
                source="control_page",
                guild_id=guild_id,
            )
    except MemoryDeletionJournalIntegrityError:
        raise
    except Exception as exc:
        if deps.log is not None:
            deps.log(f"[CONTROL PAGE TOOL ROUTER] failed errorType={type(exc).__name__}")
        return None


async def execute_control_page_tool_from_runtime(
    guild: Any | None,
    decision: dict[str, Any],
    *,
    deps: ControlPageToolRuntimeDeps,
) -> str:
    if deps.control_page_tool_policy_error is None:
        return "그 명령은 등록만 되어 있고 실행기가 아직 없어."
    policy_error = deps.control_page_tool_policy_error(decision, guild_available=guild is not None)
    if policy_error:
        return policy_error
    tool_name = deps.clean_text(str(decision.get("tool") or ""))
    arguments = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
    if tool_name == "control_page.help" and deps.build_control_page_help_reply is not None:
        return deps.build_control_page_help_reply()

    runtime = deps.guild_getter_runtime or {}
    if deps.execute_control_page_memory_tool is not None:
        memory_reply = await deps.execute_control_page_memory_tool(
            tool_name,
            arguments,
            execute_memory_panel_action=lambda action: execute_control_page_memory_panel_action_from_runtime(action, deps=deps),
            enqueue_ui_command=deps.enqueue_control_page_ui_command,
            ensure_vault_layout=deps.ensure_vault_layout,
            open_vault_tool_reply=deps.open_vault_tool_reply,
            vault_obsidian_url=deps.vault_obsidian_url,
            open_url=deps.open_url,
            open_path=deps.open_path,
        )
        if memory_reply is not None:
            return memory_reply
    if deps.execute_control_page_runtime_tool is not None:
        runtime_reply = await deps.execute_control_page_runtime_tool(
            tool_name,
            guild=guild,
            get_runtime_services=runtime.get("get_runtime_services"),
            build_local_status_text=runtime.get("build_local_status_text"),
            build_status_reply=runtime.get("build_status_reply"),
            execute_restart_command=lambda: execute_control_page_restart_command_from_runtime(deps=deps),
            schedule_local_shutdown=runtime.get("schedule_local_shutdown"),
            schedule_stack_shutdown=runtime.get("schedule_stack_shutdown"),
            schedule_bot_shutdown=runtime.get("schedule_bot_shutdown"),
            build_autonomy_reply=runtime.get("build_autonomy_reply"),
        )
        if runtime_reply is not None:
            return runtime_reply
    if deps.execute_control_page_voice_tool is not None:
        voice_reply = await deps.execute_control_page_voice_tool(
            tool_name,
            arguments,
            guild=guild,
            build_voice_status_reply=runtime.get("build_voice_status_reply"),
            set_input_mode=runtime.get("set_input_mode"),
            input_mode_status_line=runtime.get("input_mode_status_line"),
            restore_voice_channel=runtime.get("restore_voice_channel"),
            build_voice_continuity_reply=runtime.get("build_voice_continuity_reply"),
            reset_continuity_probe=runtime.get("reset_continuity_probe"),
        )
        if voice_reply is not None:
            return voice_reply
    if deps.execute_control_page_minecraft_tool is not None:
        minecraft_reply = await deps.execute_control_page_minecraft_tool(
            tool_name,
            arguments,
            guild=guild,
            build_inventory_reply=runtime.get("build_inventory_reply"),
            build_minecraft_reply=runtime.get("build_minecraft_reply"),
            enable_mode=runtime.get("enable_mode"),
            disable_mode=runtime.get("disable_mode"),
            set_goal=runtime.get("set_minecraft_goal"),
            format_position=runtime.get("format_position"),
        )
        if minecraft_reply is not None:
            return minecraft_reply
    return "그 명령은 등록만 되어 있고 실행기가 아직 없어."


async def handle_control_page_input_from_runtime(
    guild: Any | None,
    text: str,
    *,
    deps: ControlPageInputRuntimeDeps,
) -> str:
    reset_memory_exposure_position()
    reset_conversation_memory_receipt_ref()
    guild_id = deps.control_page_effective_guild_id(guild)
    session_key = deps.control_page_session_key(guild_id)
    cheap_decision = deps.cheap_control_page_tool_decision(text)
    if cheap_decision is not None:
        reply = await deps.execute_control_page_tool(guild, cheap_decision)
        deps.remember_control_page_tool_turn(
            guild,
            text,
            reply,
            cheap_decision,
            memory_receipt_ref=not_used_memory_receipt_ref(),
        )
        capture_conversation_memory_receipt_ref(
            not_used_memory_receipt_ref()
        )
        return reply
    if deps.clean_text(text).startswith("/"):
        capture_conversation_memory_receipt_ref(
            not_used_memory_receipt_ref()
        )
        return "지원하지 않는 명령어야. /help 로 현재 페이지 명령어를 확인해줘."
    if deps.should_route_control_page_tool_candidate(text):
        tool_decision_raw = await deps.decide_control_page_tool_call(text, guild_id=guild_id, session_key=session_key)
        router_receipt_ref = (
            current_conversation_memory_receipt_ref()
            or unattributed_memory_receipt_ref()
        )
        tool_decision = deps.control_page_tool_decision_from_llm(tool_decision_raw)
        if tool_decision:
            router_policy_error = deps.control_page_tool_policy_error(tool_decision, guild_available=guild is not None)
            if router_policy_error:
                deps.remember_control_page_tool_turn(
                    guild,
                    text,
                    router_policy_error,
                    tool_decision,
                    memory_receipt_ref=router_receipt_ref,
                )
                capture_conversation_memory_receipt_ref(
                    router_receipt_ref
                )
                return router_policy_error
            execute_reply = await deps.execute_control_page_tool(guild, tool_decision)
            final_reply = deps.control_page_tool_reply_from_execution(tool_decision, execute_reply)
            deps.remember_control_page_tool_turn(
                guild,
                text,
                final_reply,
                tool_decision,
                memory_receipt_ref=router_receipt_ref,
            )
            capture_conversation_memory_receipt_ref(
                router_receipt_ref
            )
            return final_reply
        if isinstance(tool_decision_raw, dict):
            router_reply = deps.clean_text(str(tool_decision_raw.get("reply") or ""))
            if router_reply:
                capture_conversation_memory_receipt_ref(
                    router_receipt_ref
                )
                return router_reply
    if deps.should_force_search_query(text):
        return await deps.answer_control_page_search_text(guild, text)
    return await deps.answer_control_page_text(guild, text)


__all__ = [
    "ControlPageInputRuntimeDeps",
    "ControlPageToolRuntimeDeps",
    "decide_control_page_tool_call_from_runtime",
    "execute_control_page_tool_from_runtime",
    "execute_control_page_memory_panel_action_from_runtime",
    "execute_control_page_restart_command_from_runtime",
    "handle_control_page_input_from_runtime",
    "recent_control_page_history_for_router_from_runtime",
    "remember_control_page_tool_turn_from_runtime",
]
