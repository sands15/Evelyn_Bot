from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web

from .control_page_http import (
    add_control_page_no_store_headers,
    build_control_page_health_payload,
    control_page_file_response,
    control_page_json_response,
    control_page_session_handler,
    resolve_control_page_asset_path,
)
from .control_page_state import (
    build_control_page_boot_progress_payload,
    control_page_open_memory_vault_result,
    control_page_result_status,
    handle_control_page_chat_request,
    handle_control_page_memory_note_action_request,
    handle_control_page_shutdown_request,
    parse_control_page_guild_id,
    parse_control_page_memory_graph_query,
    parse_control_page_memory_note_query,
    parse_control_page_memory_snapshot_query,
)
from .startup_component_state import (
    StartupComponentRuntimeDeps,
    mark_startup_component_from_runtime,
    startup_component_done_from_runtime,
)

from .control_page_guild_runtime import (
    current_tts_target_name_from_runtime,
    resolve_guild_member_name_from_runtime,
    select_control_page_guild_from_runtime,
)
from .minecraft_live_state_runtime import get_control_page_minecraft_snapshot_from_runtime
from .control_page_minecraft_snapshot_runtime import (
    ensure_control_page_background_tasks_started_from_runtime,
    ensure_control_page_minecraft_snapshot_from_runtime,
    get_control_page_minecraft_snapshot_cache_copy_from_runtime,
    safe_get_control_page_minecraft_snapshot_from_runtime,
    stop_control_page_background_tasks_from_runtime,
)
from .control_page_runtime_services_runtime import get_control_page_runtime_services_from_runtime
from .control_page_search_runtime import answer_control_page_search_text_from_runtime
from .control_page_server_start_runtime import (
    ControlPageServerStartRuntimeDeps,
    start_control_page_server_from_runtime,
)
from .control_page_status_runtime import (
    build_control_page_autonomy_reply_from_runtime,
    build_control_page_inventory_reply_from_runtime,
    build_control_page_local_status_text_from_runtime,
    build_control_page_minecraft_reply_from_runtime,
    build_control_page_status_reply_from_runtime,
    build_control_page_status_text_from_runtime,
    build_control_page_voice_continuity_reply_from_runtime,
    build_control_page_voice_status_reply_from_runtime,
)
from .control_page_text_runtime import answer_control_page_text_from_runtime
from .control_page_tool_runtime import (
    decide_control_page_tool_call_from_runtime,
    execute_control_page_memory_panel_action_from_runtime,
    execute_control_page_restart_command_from_runtime,
    execute_control_page_tool_from_runtime,
    handle_control_page_input_from_runtime,
    recent_control_page_history_for_router_from_runtime,
    remember_control_page_tool_turn_from_runtime,
)
from .control_page_ui_runtime import (
    append_control_page_chat_log_from_runtime,
    build_control_page_panel_state_from_runtime,
    control_page_effective_guild_id_from_runtime,
    control_page_effective_guild_name_from_runtime,
    control_page_local_url_from_runtime,
    control_page_session_key_from_runtime,
    enqueue_control_page_ui_command_from_runtime,
    generate_control_page_welcome_text_from_runtime,
    get_control_page_chat_log_from_runtime,
    sanitize_control_page_welcome_text_from_runtime,
)
from .control_page_memory_http import (
    control_page_memory_guarded_json_response,
)
from .conversation_memory_receipt import not_used_memory_receipt_ref
from .memory_deletion_journal import (
    MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    MemoryDeletionJournalIntegrityError,
)
from .memory_exposure import (
    current_memory_exposure_position,
    reset_memory_exposure_position,
)


DepsFactory = Callable[[], Any]


@dataclass(frozen=True)
class ControlPageCompositionDeps:
    ui: DepsFactory
    guild_selection: DepsFactory
    welcome: DepsFactory
    minecraft_live_snapshot: DepsFactory
    minecraft_snapshot: DepsFactory
    background_tasks: DepsFactory
    runtime_services: DepsFactory
    status: DepsFactory
    tool: DepsFactory
    search: DepsFactory
    text: DepsFactory
    input: DepsFactory
    server_start: DepsFactory
    build_voice_continuity_snapshot: Callable[[], dict[str, Any]]
    cheap_tool_decision: Callable[[str], dict[str, Any] | None]
    welcome_locks: dict[int, Any]
    startup_component_state: dict[str, dict[str, Any]]
    startup_steps: Any
    startup_components_ready: Callable[[], bool]
    discord_enabled: bool
    discord_ready: Callable[[], bool]
    control_api_available: Callable[[], bool]
    now: Callable[[], float]


@dataclass(frozen=True)
class ControlPageHttpCompositionDeps:
    memory_index_dir: Path
    docs_dir: Any
    assets_dir: Any
    minecraft_item_icon_loader: Any
    normalize_minecraft_item_name: Callable[[str], str]
    select_guild: Callable[[int | None], Any | None]
    build_state: Callable[[Any | None], Awaitable[dict[str, Any]]]
    discord_enabled: bool
    effective_guild_id: Callable[[Any | None], int]
    append_chat_log: Callable[..., None]
    handle_input: Callable[[Any | None, str], Awaitable[str]]
    ensure_minecraft_snapshot: Callable[..., Awaitable[dict[str, Any]]]
    refresh_runtime_services: Callable[..., Awaitable[dict[str, Any]]]
    export_memory_graph: Callable[..., dict[str, Any]]
    memory_vault_user_snapshot: Callable[..., dict[str, Any]]
    memory_vault_user_note: Callable[..., dict[str, Any]]
    update_memory_vault_user_note: Callable[..., dict[str, Any]]
    local_only_mode: bool
    port: int
    ensure_memory_vault_layout: Callable[[], Any]
    memory_vault_obsidian_url: Callable[[Any], str]
    open_url: Callable[[str], bool]
    open_path: Callable[[Any], bool]
    enabled: bool
    host: str
    minecraft_icon_route: str
    middleware: Any
    get_runner: Callable[[], Any | None]
    set_runner: Callable[[Any], None]
    set_site: Callable[[Any], None]
    get_start_lock: Callable[[], Any | None]
    set_start_lock: Callable[[Any], None]
    lock_factory: Callable[[], Any]
    application_factory: Callable[..., Any]
    app_runner_factory: Callable[..., Any]
    tcp_site_factory: Callable[..., Any]
    mark_startup_component: Callable[[str, str, str], Any]
    local_url: Callable[[], str]
    log: Callable[[str], Any]


class ControlPageComposition:
    """Owns the thin Control Page adapters that used to dominate main.py."""

    def __init__(self, deps: ControlPageCompositionDeps) -> None:
        self.deps = deps

    def enqueue_ui_command(self, action: str, *, panel_id: str | None = None) -> dict[str, Any]:
        return enqueue_control_page_ui_command_from_runtime(action, panel_id=panel_id, deps=self.deps.ui())

    def build_panel_state(self) -> dict[str, Any]:
        return build_control_page_panel_state_from_runtime(deps=self.deps.ui())

    def local_url(self) -> str:
        return control_page_local_url_from_runtime(deps=self.deps.ui())

    def session_key(self, guild_id: int | None) -> str:
        return control_page_session_key_from_runtime(guild_id, deps=self.deps.ui())

    def effective_guild_id(self, guild: Any | None) -> int:
        return control_page_effective_guild_id_from_runtime(guild, deps=self.deps.ui())

    def effective_guild_name(self, guild: Any | None) -> str:
        return control_page_effective_guild_name_from_runtime(guild, deps=self.deps.ui())

    def append_chat_log(
        self,
        guild_id: int,
        role: str,
        author: str,
        text: str,
        memory_receipt_ref: Any = None,
    ) -> None:
        append_control_page_chat_log_from_runtime(
            guild_id,
            role,
            author,
            text,
            deps=self.deps.ui(),
            memory_receipt_ref=memory_receipt_ref,
        )

    def get_chat_log(self, guild_id: int) -> list[dict[str, Any]]:
        return get_control_page_chat_log_from_runtime(guild_id, deps=self.deps.ui())

    def sanitize_welcome_text(self, text: str) -> str:
        return sanitize_control_page_welcome_text_from_runtime(text, deps=self.deps.ui())

    async def generate_welcome_text(self, guild: Any | None) -> str:
        return await generate_control_page_welcome_text_from_runtime(guild, deps=self.deps.welcome())

    async def ensure_welcome_message(
        self,
        guild: Any | None,
        *,
        runtime_services: dict[str, Any] | None = None,
    ) -> None:
        services = runtime_services or {}
        if not bool(services.get("mainReady")):
            return
        guild_id = self.effective_guild_id(guild)
        if self.get_chat_log(guild_id):
            return
        lock = self.deps.welcome_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            if self.get_chat_log(guild_id):
                return
            welcome = await self.generate_welcome_text(guild)
            self.append_chat_log(
                guild_id,
                "assistant",
                "Evelyn",
                welcome,
                not_used_memory_receipt_ref(),
            )

    def select_guild(self, requested_guild_id: int | None = None) -> Any | None:
        return select_control_page_guild_from_runtime(requested_guild_id, deps=self.deps.guild_selection())

    def resolve_guild_member_name(self, guild: Any | None, user_id: int | None) -> str:
        return resolve_guild_member_name_from_runtime(guild, user_id, deps=self.deps.guild_selection())

    def current_tts_target_name(self, guild: Any | None) -> str:
        return current_tts_target_name_from_runtime(guild, deps=self.deps.guild_selection())

    async def get_minecraft_snapshot(self, guild_id: int | None) -> dict[str, Any]:
        return await get_control_page_minecraft_snapshot_from_runtime(
            guild_id,
            deps=self.deps.minecraft_live_snapshot(),
        )

    async def safe_get_minecraft_snapshot(
        self,
        guild_id: int | None,
        *,
        timeout_seconds: float = 0.75,
    ) -> dict[str, Any]:
        return await safe_get_control_page_minecraft_snapshot_from_runtime(
            guild_id,
            timeout_seconds=timeout_seconds,
            deps=self.deps.minecraft_snapshot(),
        )

    async def get_runtime_services(self, *, force: bool = False) -> dict[str, Any]:
        return await get_control_page_runtime_services_from_runtime(deps=self.deps.runtime_services(), force=force)

    def get_minecraft_snapshot_cache_copy(self) -> dict[str, Any]:
        return get_control_page_minecraft_snapshot_cache_copy_from_runtime(deps=self.deps.minecraft_snapshot())

    async def ensure_minecraft_snapshot(
        self,
        guild_id: int | None,
        *,
        force: bool = False,
        wait: bool = False,
    ) -> dict[str, Any]:
        return await ensure_control_page_minecraft_snapshot_from_runtime(
            guild_id,
            deps=self.deps.minecraft_snapshot(),
            force=force,
            wait=wait,
        )

    async def ensure_background_tasks_started(self) -> None:
        await ensure_control_page_background_tasks_started_from_runtime(deps=self.deps.background_tasks())

    def stop_background_tasks(self) -> None:
        stop_control_page_background_tasks_from_runtime(deps=self.deps.background_tasks())

    def build_status_text(self, guild: Any, minecraft: dict[str, Any]) -> str:
        return build_control_page_status_text_from_runtime(guild, minecraft, deps=self.deps.status())

    def build_local_status_text(self, runtime_services: dict[str, Any] | None = None) -> str:
        return build_control_page_local_status_text_from_runtime(runtime_services, deps=self.deps.status())

    async def build_status_reply(self, guild: Any) -> str:
        return await build_control_page_status_reply_from_runtime(guild, deps=self.deps.status())

    def build_voice_status_reply(self, guild: Any | None) -> str:
        return build_control_page_voice_status_reply_from_runtime(guild, deps=self.deps.status())

    def build_voice_continuity_reply(self, guild: Any | None) -> str:
        _ = guild
        return build_control_page_voice_continuity_reply_from_runtime(
            self.deps.build_voice_continuity_snapshot(),
            deps=self.deps.status(),
        )

    async def build_inventory_reply(self, guild: Any) -> str:
        return await build_control_page_inventory_reply_from_runtime(guild, deps=self.deps.status())

    async def build_minecraft_reply(self, guild: Any) -> str:
        return await build_control_page_minecraft_reply_from_runtime(guild, deps=self.deps.status())

    def build_autonomy_reply(self, guild: Any) -> str:
        return build_control_page_autonomy_reply_from_runtime(guild, deps=self.deps.status())

    def execute_memory_panel_action(self, action: str) -> str:
        return execute_control_page_memory_panel_action_from_runtime(action, deps=self.deps.tool())

    def execute_restart_command(self) -> str:
        return execute_control_page_restart_command_from_runtime(deps=self.deps.tool())

    def recent_history_for_router(self, *, session_key: str, guild_id: int | None, limit: int = 6) -> str:
        return recent_control_page_history_for_router_from_runtime(
            session_key=session_key,
            guild_id=guild_id,
            limit=limit,
            deps=self.deps.tool(),
        )

    def remember_tool_turn(
        self,
        guild: Any | None,
        user_text: str,
        reply_text: str,
        decision: dict[str, Any],
        *,
        memory_receipt_ref: Any = None,
    ) -> None:
        remember_control_page_tool_turn_from_runtime(
            guild,
            user_text,
            reply_text,
            decision,
            deps=self.deps.tool(),
            memory_receipt_ref=memory_receipt_ref,
        )

    async def decide_tool_call(self, text: str, *, guild_id: int | None, session_key: str) -> dict[str, Any] | None:
        return await decide_control_page_tool_call_from_runtime(
            text,
            guild_id=guild_id,
            session_key=session_key,
            deps=self.deps.tool(),
        )

    async def execute_tool(self, guild: Any | None, decision: dict[str, Any]) -> str:
        return await execute_control_page_tool_from_runtime(guild, decision, deps=self.deps.tool())

    async def execute_command(self, guild: Any | None, text: str) -> str:
        decision = self.deps.cheap_tool_decision(text)
        if decision is not None:
            return await self.execute_tool(guild, decision)
        return "지원하지 않는 명령어야. /help 로 현재 페이지 명령어를 확인해줘."

    async def answer_search_text(self, guild: Any | None, user_text: str) -> str:
        return await answer_control_page_search_text_from_runtime(guild, user_text, deps=self.deps.search())

    async def answer_text(self, guild: Any | None, user_text: str) -> str:
        return await answer_control_page_text_from_runtime(guild, user_text, deps=self.deps.text())

    async def handle_input(self, guild: Any | None, text: str) -> str:
        return await handle_control_page_input_from_runtime(guild, text, deps=self.deps.input())

    def mark_startup_component(self, key: str, status: str, detail: str = "") -> None:
        mark_startup_component_from_runtime(
            key,
            status,
            detail,
            deps=StartupComponentRuntimeDeps(
                startup_component_state=self.deps.startup_component_state,
                now=self.deps.now,
            ),
        )

    def startup_component_done(self, key: str) -> bool:
        return startup_component_done_from_runtime(
            key,
            deps=StartupComponentRuntimeDeps(
                startup_component_state=self.deps.startup_component_state,
                now=self.deps.now,
            ),
        )

    def build_boot_progress(
        self,
        runtime_services: dict[str, Any] | None,
        *,
        guild_available: bool,
        listening: bool = False,
    ) -> dict[str, Any]:
        return build_control_page_boot_progress_payload(
            runtime_services,
            startup_steps=self.deps.startup_steps,
            startup_component_state=self.deps.startup_component_state,
            startup_components_ready=self.deps.startup_components_ready(),
            discord_enabled=self.deps.discord_enabled,
            discord_ready=self.deps.discord_ready(),
            guild_available=guild_available,
            control_api_available=self.deps.control_api_available(),
            listening=listening,
        )

    async def start_server(self) -> None:
        await start_control_page_server_from_runtime(deps=self.deps.server_start())


class ControlPageHttpComposition:
    """Owns aiohttp handlers while receiving application state through typed hooks."""

    def __init__(self, deps: ControlPageHttpCompositionDeps) -> None:
        self.deps = deps

    async def index(self, _: web.Request) -> web.StreamResponse:
        return control_page_file_response(
            self.deps.docs_dir / "index.html",
            not_found_text="control page index not found",
        )

    async def asset(self, request: web.Request) -> web.StreamResponse:
        return control_page_file_response(
            resolve_control_page_asset_path(self.deps.assets_dir, request.match_info.get("asset_path", "")),
            not_found_text="asset not found",
        )

    async def minecraft_item_icon(self, request: web.Request) -> web.StreamResponse:
        item_name = self.deps.normalize_minecraft_item_name(request.match_info.get("item_name", ""))
        if not item_name:
            raise web.HTTPNotFound(text="item icon not found")
        icon_bytes = self.deps.minecraft_item_icon_loader.load_icon(item_name)
        if not icon_bytes:
            raise web.HTTPNotFound(text="item icon not found")
        return add_control_page_no_store_headers(web.Response(body=icon_bytes, content_type="image/png"))

    async def state(self, request: web.Request) -> web.StreamResponse:
        reset_memory_exposure_position()
        guild = self.deps.select_guild(parse_control_page_guild_id(request.query.get("guildId")))
        try:
            payload = await self.deps.build_state(guild)
        except MemoryDeletionJournalIntegrityError:
            payload = {
                "ok": False,
                "error": MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
            }
            return control_page_memory_guarded_json_response(
                payload,
                expected_position=None,
                memory_index_dir=self.deps.memory_index_dir,
                status=503,
            )
        return control_page_memory_guarded_json_response(
            payload,
            expected_position=current_memory_exposure_position(),
            memory_index_dir=self.deps.memory_index_dir,
        )

    async def chat(self, request: web.Request) -> web.StreamResponse:
        reset_memory_exposure_position()
        try:
            payload = await request.json()
        except Exception:
            return control_page_memory_guarded_json_response(
                {"ok": False, "error": "invalid_json"},
                expected_position=None,
                memory_index_dir=self.deps.memory_index_dir,
                status=400,
            )
        try:
            response_payload, status = await handle_control_page_chat_request(
                payload,
                discord_enabled=self.deps.discord_enabled,
                select_guild=self.deps.select_guild,
                effective_guild_id=self.deps.effective_guild_id,
                append_chat_log=self.deps.append_chat_log,
                handle_input=self.deps.handle_input,
                ensure_minecraft_snapshot=self.deps.ensure_minecraft_snapshot,
                refresh_runtime_services=self.deps.refresh_runtime_services,
                build_state=self.deps.build_state,
            )
        except MemoryDeletionJournalIntegrityError:
            response_payload = {
                "ok": False,
                "error": MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
            }
            status = 503
            reset_memory_exposure_position()
        return control_page_memory_guarded_json_response(
            response_payload,
            expected_position=current_memory_exposure_position(),
            memory_index_dir=self.deps.memory_index_dir,
            status=status,
        )

    async def memory_graph(self, request: web.Request) -> web.StreamResponse:
        params = parse_control_page_memory_graph_query(request.query)
        return control_page_json_response(self.deps.export_memory_graph(**params))

    async def memory_snapshot(self, request: web.Request) -> web.StreamResponse:
        params = parse_control_page_memory_snapshot_query(request.query)
        return control_page_json_response(self.deps.memory_vault_user_snapshot(**params))

    async def memory_note(self, request: web.Request) -> web.StreamResponse:
        note_id = request.match_info.get("note_id", "")
        result = self.deps.memory_vault_user_note(
            note_id,
            **parse_control_page_memory_note_query(request.query),
        )
        return control_page_json_response(result, status=control_page_result_status(result))

    async def memory_note_action(self, request: web.Request) -> web.StreamResponse:
        note_id = request.match_info.get("note_id", "")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        result, status = handle_control_page_memory_note_action_request(
            note_id,
            payload,
            update_note=self.deps.update_memory_vault_user_note,
        )
        return control_page_json_response(result, status=status)

    async def shutdown(self, request: web.Request) -> web.StreamResponse:
        reset_memory_exposure_position()
        response_payload, status = await handle_control_page_shutdown_request(
            request.query.get("guildId"),
            select_guild=self.deps.select_guild,
            handle_input=self.deps.handle_input,
            build_state=self.deps.build_state,
        )
        return control_page_memory_guarded_json_response(
            response_payload,
            expected_position=current_memory_exposure_position(),
            memory_index_dir=self.deps.memory_index_dir,
            status=status,
        )

    async def health(self, _: web.Request) -> web.StreamResponse:
        return control_page_json_response(
            build_control_page_health_payload(
                local_only_mode=self.deps.local_only_mode,
                discord_enabled=self.deps.discord_enabled,
                port=self.deps.port,
            )
        )

    async def open_memory_vault(self, _: web.Request) -> web.StreamResponse:
        vault = self.deps.ensure_memory_vault_layout()
        obsidian_url = self.deps.memory_vault_obsidian_url(vault)
        payload, status = control_page_open_memory_vault_result(
            vault_path=vault,
            obsidian_url=obsidian_url,
            open_url=self.deps.open_url,
            open_path=self.deps.open_path,
        )
        return control_page_json_response(payload, status=status)

    async def open_memory_vault_options(self, _: web.Request) -> web.StreamResponse:
        return control_page_json_response({"ok": True, "methods": ["POST", "OPTIONS"]})

    def build_server_start_deps(self) -> ControlPageServerStartRuntimeDeps:
        return ControlPageServerStartRuntimeDeps(
            enabled=self.deps.enabled,
            docs_dir=self.deps.docs_dir,
            host=self.deps.host,
            port=self.deps.port,
            routes=(
                ("GET", "/health", self.health),
                ("GET", "/", self.index),
                ("GET", "/assets/{asset_path:.*}", self.asset),
                ("GET", self.deps.minecraft_icon_route + "/{item_name}", self.minecraft_item_icon),
                ("GET", "/api/control-page/state", self.state),
                ("GET", "/api/control-page/session", control_page_session_handler),
                ("GET", "/api/control-page/memory", self.memory_snapshot),
                ("GET", "/api/control-page/memory-graph", self.memory_graph),
                ("GET", "/api/control-page/memory/{note_id}", self.memory_note),
                ("POST", "/api/control-page/open-memory-vault", self.open_memory_vault),
                ("POST", "/api/control-page/chat", self.chat),
                ("POST", "/api/control-page/memory/{note_id}", self.memory_note_action),
                ("POST", "/api/control-page/shutdown", self.shutdown),
                ("OPTIONS", "/api/control-page/state", self.state),
                ("OPTIONS", "/api/control-page/memory", self.memory_snapshot),
                ("OPTIONS", "/api/control-page/memory-graph", self.memory_graph),
                ("OPTIONS", "/api/control-page/memory/{note_id}", self.memory_note),
                ("OPTIONS", "/api/control-page/open-memory-vault", self.open_memory_vault_options),
                ("OPTIONS", "/api/control-page/chat", self.chat),
                ("OPTIONS", "/api/control-page/shutdown", self.shutdown),
            ),
            middleware=self.deps.middleware,
            get_runner=self.deps.get_runner,
            set_runner=self.deps.set_runner,
            set_site=self.deps.set_site,
            get_start_lock=self.deps.get_start_lock,
            set_start_lock=self.deps.set_start_lock,
            lock_factory=self.deps.lock_factory,
            application_factory=self.deps.application_factory,
            app_runner_factory=self.deps.app_runner_factory,
            tcp_site_factory=self.deps.tcp_site_factory,
            mark_startup_component=self.deps.mark_startup_component,
            local_url=self.deps.local_url,
            log=self.deps.log,
        )


__all__ = [
    "ControlPageComposition",
    "ControlPageCompositionDeps",
    "ControlPageHttpComposition",
    "ControlPageHttpCompositionDeps",
]
