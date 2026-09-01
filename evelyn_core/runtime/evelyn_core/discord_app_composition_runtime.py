from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable

import discord
from discord import app_commands
from discord.http import Route

if TYPE_CHECKING:
    from .discord_runtime_status import DiscordRuntimeStatus

from .discord_command_session_runtime import (
    ContinuityRecordingCommandContext,
    mark_text_session_from_command_runtime,
)
from .discord_command_ownership import (
    read_command_ownership_ledger,
    validate_command_ownership_config,
    write_command_ownership_ledger,
)
from .discord_command_handlers import (
    AUTONOMY_START_STALE_REPLY,
    handle_autonomy_start_command,
    handle_autonomy_status_command,
    handle_autonomy_stop_command,
    handle_channel_setting_command,
    handle_control_command_error,
    handle_discord_command_error,
    handle_evelyn_page_command,
    handle_feedback_application_command,
    handle_join_voice_command,
    handle_leave_voice_command,
    handle_minecraft_connect_command,
    handle_minecraft_disconnect_command,
    handle_minecraft_goal_command,
    handle_minecraft_status_command,
    handle_prefix_command,
    handle_rejoin_voice_command,
    handle_record_consent_application_command,
    handle_record_delete_application_command,
    handle_record_view_application_command,
    handle_reset_guild_memory_command,
    handle_restart_bot_command,
    handle_shutdown_bot_command,
    handle_status_command,
    RecordDeletionConfirmationGuard,
)
from .discord_conversation_archive_runtime import (
    DiscordParticipationTracker,
    DiscordSharedSession,
    DiscordSharedSessionRegistry,
    voice_state_snapshot_from_discord,
)
from .discord_text_turn import handle_discord_text_message


def build_discord_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.voice_states = True
    intents.members = True
    return intents


DepsFactory = Callable[[], Any]
_VOICE_REARM_ATTEMPTS = 3
_VOICE_REARM_RETRY_DELAY_SEC = 0.5
_VOICE_READY_RESTORE_RETRY_REASON = "voice_rearm_failed"
_SEARCH_RECOVERY_PENDING = "pending"
_SEARCH_RECOVERY_RUNNING = "running"
_SEARCH_RECOVERY_COMPLETE = "complete"
_SEARCH_RECOVERY_FAILED = "failed"
_CONVERSATION_ARCHIVE_APPLICATION_COMMAND_NAMES = frozenset(
    {"기록열람", "기록삭제", "기록동의", "기록철회", "피드백제출"}
)


def _temporary_application_command_name(run_id: str, final_name: str) -> str:
    return hashlib.sha256(f"{run_id}:{final_name}".encode("utf-8")).hexdigest()[:32]


def _normalized_application_command_choice(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("archive_command_shape_invalid")
    return {
        "name": str(payload.get("name") or ""),
        "value": payload.get("value"),
        "name_localizations": dict(payload.get("name_localizations") or {}),
    }


def _normalized_application_command_option(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("archive_command_shape_invalid")
    return {
        "type": int(payload.get("type") or 0),
        "name": str(payload.get("name") or ""),
        "description": str(payload.get("description") or ""),
        "required": payload.get("required") is True,
        "choices": [
            _normalized_application_command_choice(choice)
            for choice in payload.get("choices") or ()
        ],
        "options": [
            _normalized_application_command_option(option)
            for option in payload.get("options") or ()
        ],
        "channel_types": sorted(int(value) for value in payload.get("channel_types") or ()),
        "min_value": payload.get("min_value"),
        "max_value": payload.get("max_value"),
        "min_length": payload.get("min_length"),
        "max_length": payload.get("max_length"),
        "autocomplete": payload.get("autocomplete") is True,
        "name_localizations": dict(payload.get("name_localizations") or {}),
        "description_localizations": dict(
            payload.get("description_localizations") or {}
        ),
    }


def _normalized_application_command_shape(
    payload: Any,
    *,
    command: Any = None,
    guild_registry: bool = False,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("archive_command_shape_invalid")
    permissions = payload.get(
        "default_member_permissions",
        getattr(command, "default_member_permissions", None),
    )
    if permissions is not None:
        permissions = str(getattr(permissions, "value", permissions))
    return {
        "type": int(payload.get("type") or 0),
        "name": str(payload.get("name") or ""),
        "description": str(payload.get("description") or ""),
        "options": [
            _normalized_application_command_option(option)
            for option in payload.get("options") or ()
        ],
        "contexts": sorted(
            int(value)
            for value in (
                payload.get("contexts")
                if payload.get("contexts") is not None
                else ([0] if guild_registry else [])
            )
        ),
        "integration_types": sorted(
            int(value)
            for value in (
                payload.get("integration_types")
                if payload.get("integration_types") is not None
                else ([0] if guild_registry else [])
            )
        ),
        "default_member_permissions": permissions,
        "dm_permission": payload.get(
            "dm_permission",
            getattr(command, "dm_permission", True),
        )
        is True,
        "nsfw": payload.get("nsfw", getattr(command, "nsfw", False)) is True,
        "name_localizations": dict(payload.get("name_localizations") or {}),
        "description_localizations": dict(
            payload.get("description_localizations") or {}
        ),
    }


def _remote_application_command_shape(
    command: Any,
    *,
    guild_registry: bool = False,
) -> dict[str, Any]:
    if isinstance(command, dict):
        return _normalized_application_command_shape(
            command,
            guild_registry=guild_registry,
        )
    serializer = getattr(command, "to_dict", None)
    if callable(serializer):
        return _normalized_application_command_shape(
            serializer(),
            command=command,
            guild_registry=guild_registry,
        )
    raise RuntimeError("archive_command_shape_invalid")


def _remote_application_command_name(command: Any) -> str:
    if isinstance(command, dict):
        return str(command.get("name") or "")
    return str(getattr(command, "name", ""))


def _remote_application_command_id(command: Any) -> int:
    command_id = (
        command.get("id") if isinstance(command, dict) else getattr(command, "id", None)
    )
    if isinstance(command_id, bool):
        raise RuntimeError("archive_command_identity_invalid")
    try:
        normalized = int(command_id)
    except (TypeError, ValueError):
        raise RuntimeError("archive_command_identity_invalid") from None
    if normalized <= 0:
        raise RuntimeError("archive_command_identity_invalid")
    return normalized


def _remote_application_command_snapshot(
    commands: Any,
    *,
    guild_registry: bool = False,
) -> tuple[Any, ...]:
    snapshot = [
        (
            _remote_application_command_id(command),
            _remote_application_command_shape(
                command,
                guild_registry=guild_registry,
            ),
        )
        for command in commands
    ]
    snapshot.sort(key=lambda item: (item[1]["type"], item[1]["name"], item[0]))
    return tuple(snapshot)


@dataclass(frozen=True)
class DiscordEventCompositionDeps:
    bot_user: Callable[[], Any]
    bot_guilds: Callable[[], list[Any]]
    mark_startup_component: Callable[..., None]
    clean_text: Callable[[str], str]
    ensure_voice_worker_started: Callable[[], None]
    start_control_page_server: Callable[..., Any]
    ensure_startup_components_ready: Callable[..., Any]
    ensure_local_mic_service_started: Callable[..., Any]
    ensure_vision_watch_started: Callable[[], None]
    ensure_control_page_background_tasks_started: Callable[..., Any]
    voice_client_type: type
    ensure_listening_voice_client: Callable[..., Any]
    voice_rejoin_on_ready: bool
    restore_last_voice_channel: Callable[..., Any]
    autonomy_enabled: bool
    text_message_handler: DepsFactory
    log: Callable[..., Any]
    runtime_status: "DiscordRuntimeStatus | None" = None
    recover_search_followups: Callable[..., Any] | None = None
    conversation_archive_enabled: bool = False
    conversation_participation_tracker: Any = None
    conversation_participation_observer: Callable[..., Any] | None = None
    conversation_consent_current: Callable[..., Any] | None = None
    conversation_archive_ready: Callable[..., Any] | None = None
    conversation_archive_otp_delivery_worker: Callable[..., Any] | None = None
    conversation_shared_session_registry: DiscordSharedSessionRegistry | None = None
    conversation_shared_session_open: Callable[..., Any] | None = None
    conversation_shared_session_close: Callable[..., Any] | None = None
    conversation_archive_command_guild_id: int = 0
    conversation_archive_command_ownership: tuple[str, str] = ("", "")


@dataclass(frozen=True)
class DiscordCommandCompositionDeps:
    ensure_listening_voice_client: Callable[..., Any]
    mark_voice_manual_disconnect: Callable[..., None]
    create_task: Callable[..., Any]
    restart_bot_process: Callable[..., Any]
    schedule_evelyn_stack_shutdown: Callable[..., bool]
    shutdown_bot_process: Callable[..., Any]
    build_status_reply: Callable[..., str]
    model_name: str
    router_model_name: str
    summary_model_name: str
    stt_model_name: str
    voice_debug_save_audio: bool
    vad_enabled: bool
    vad_provider: str
    resolve_evelyn_page_url: Callable[[], str | None]
    default_command_prefix: str
    get_guild_command_prefix: Callable[..., str]
    save_guild_command_prefix: Callable[..., Any]
    build_prefix_current_reply: Callable[..., str]
    build_prefix_reset_reply: Callable[..., str]
    build_prefix_saved_reply: Callable[..., str]
    guild_only_message: Callable[[], str]
    autonomy_enabled: bool
    get_or_create_autonomy_engine: Callable[[int], Any]
    autonomy_engines: dict[Any, Any]
    get_routed_autonomy_executor: Callable[..., Any]
    build_autonomy_status_reply: Callable[..., str]
    grant_autonomy_authorization: Callable[..., dict[str, Any]]
    revoke_autonomy_authorization: Callable[..., dict[str, Any]]
    get_autonomy_authorization_status: Callable[[], dict[str, Any]]
    command_session: DepsFactory
    enable_minecraft_mode: Callable[..., Any]
    enable_minecraft_autonomy_route: Callable[..., Any]
    disable_minecraft_mode: Callable[..., Any]
    disable_minecraft_autonomy_route: Callable[..., Any]
    is_minecraft_autonomy_route_enabled: Callable[[int], bool]
    get_minecraft_client: Callable[[], Any]
    get_minecraft_world_lease_status: Callable[
        [],
        dict[str, Any],
    ]
    set_minecraft_goal: Callable[..., Any]
    build_minecraft_connect_reply: Callable[..., str]
    build_minecraft_goal_missing_reply: Callable[..., str]
    build_minecraft_goal_updated_reply: Callable[..., str]
    build_minecraft_status_reply: Callable[..., str]
    normalize_channel_setting_action: Callable[..., Any]
    get_guild_observe_channel_ids: Callable[..., Any]
    get_guild_command_only_channel_ids: Callable[..., Any]
    add_guild_channel_setting: Callable[..., Any]
    remove_guild_channel_setting: Callable[..., Any]
    build_channel_setting_list_reply: Callable[..., str]
    build_observe_channel_usage: Callable[..., str]
    build_command_channel_usage: Callable[..., str]
    build_help_command_text: Callable[..., str]
    is_control_command_authorized: Callable[[Any], bool]
    guild_is_open: Callable[[int], bool]
    guild_epoch: Callable[[int], int]
    reset_guild_runtime_state: Callable[..., Any]
    build_reset_guild_memory_reply: Callable[..., str]
    log: Callable[..., Any]
    conversation_archive_enabled: bool = False
    conversation_archive_read_self: Callable[..., Any] | None = None
    conversation_archive_preview_delete: Callable[..., Any] | None = None
    conversation_archive_apply_delete: Callable[..., Any] | None = None
    conversation_archive_set_consent: Callable[..., Any] | None = None
    conversation_archive_capture_feedback: Callable[..., Any] | None = None
    conversation_archive_archive_autonomy_grant: Callable[..., Any] | None = None
    conversation_archive_archive_minecraft_command: Callable[..., Any] | None = None
    conversation_archive_sleep: Callable[..., Any] | None = None
    conversation_archive_operator_authorized: Callable[[Any], bool] | None = None


@dataclass(frozen=True)
class DiscordAppCompositionDeps:
    events: DiscordEventCompositionDeps
    commands: DiscordCommandCompositionDeps


@dataclass(frozen=True)
class DiscordAppBindings:
    on_ready: Any
    on_disconnect: Any
    on_voice_state_update: Any
    on_message: Any
    join_voice: Any
    rejoin_voice: Any
    leave_voice: Any
    restart_bot_command: Any
    shutdown_bot_command: Any
    status_command: Any
    evelyn_page_command: Any
    set_guild_prefix: Any
    autonomy_start_command: Any
    autonomy_stop_command: Any
    autonomy_status_command: Any
    minecraft_connect_command: Any
    minecraft_disconnect_command: Any
    minecraft_status_command: Any
    minecraft_goal_command: Any
    observe_channel_command: Any
    command_channel_command: Any
    help_command: Any
    reset_guild_memory: Any
    record_view_application_command: Any
    record_delete_application_command: Any
    record_consent_application_command: Any
    record_withdraw_application_command: Any
    feedback_application_command: Any


class DiscordAppComposition:
    """Owns Discord gateway events, command callbacks, and explicit registration."""

    def __init__(self, deps: DiscordAppCompositionDeps) -> None:
        self.deps = deps
        self._ready_generation = 0
        self._search_recovery_lock = asyncio.Lock()
        self._search_recovery_state = (
            _SEARCH_RECOVERY_COMPLETE
            if deps.events.recover_search_followups is None
            else _SEARCH_RECOVERY_PENDING
        )
        configured_tracker = deps.events.conversation_participation_tracker
        self._conversation_participation_tracker = (
            configured_tracker
            if isinstance(configured_tracker, DiscordParticipationTracker)
            else DiscordParticipationTracker()
        )
        self._record_delete_confirmations = RecordDeletionConfirmationGuard()
        self._last_conversation_archive_observed_at = 0.0
        self._conversation_archive_otp_task: asyncio.Task[Any] | None = None
        self._conversation_archive_command_bot: Any = None
        self._conversation_archive_application_commands: tuple[Any, ...] = ()
        self._conversation_archive_commands_published = False
        self._conversation_archive_owned_commands: dict[
            int, tuple[dict[str, Any], ...]
        ] = {}
        self._conversation_archive_command_recovery_required = False
        self._conversation_archive_command_ownership_loaded = False
        self._conversation_archive_command_publish_lock = asyncio.Lock()
        self._conversation_shared_session_expiry_tasks: dict[
            int, asyncio.Task[Any]
        ] = {}

    @property
    def _conversation_archive_enabled(self) -> bool:
        return self.deps.events.conversation_archive_enabled is True

    def _shared_sessions(self) -> DiscordSharedSessionRegistry:
        sessions = self.deps.events.conversation_shared_session_registry
        if not isinstance(sessions, DiscordSharedSessionRegistry):
            raise RuntimeError("conversation_shared_session_registry_missing")
        return sessions

    def _conversation_archive_command_context(
        self,
    ) -> tuple[Any, int, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        guild_id = self.deps.events.conversation_archive_command_guild_id
        bot = self._conversation_archive_command_bot
        application_id = getattr(bot, "application_id", None)
        if (
            isinstance(guild_id, bool)
            or not isinstance(guild_id, int)
            or guild_id <= 0
            or bot is None
            or not isinstance(application_id, int)
            or application_id <= 0
        ):
            raise RuntimeError("archive_command_publish_configuration_invalid")
        commands = self._conversation_archive_application_commands
        if (
            {str(getattr(command, "name", "")) for command in commands}
            != _CONVERSATION_ARCHIVE_APPLICATION_COMMAND_NAMES
        ):
            raise RuntimeError("archive_command_publish_contract_invalid")
        payloads = {
            str(command.name): command.to_dict(bot.tree) for command in commands
        }
        shapes = {
            name: _normalized_application_command_shape(payload)
            for name, payload in payloads.items()
        }
        return bot, guild_id, payloads, shapes

    def _persist_conversation_archive_command_ownership(
        self,
        bot: Any,
        guild_id: int,
    ) -> None:
        try:
            write_command_ownership_ledger(
                self.deps.events.conversation_archive_command_ownership,
                application_id=bot.application_id,
                guild_id=guild_id,
                commands=self._conversation_archive_owned_commands,
                recovery_required=self._conversation_archive_command_recovery_required,
            )
        except Exception:
            self._conversation_archive_command_recovery_required = True
            raise RuntimeError("archive_command_ownership_ledger_write_failed") from None

    @staticmethod
    def _owned_application_command_shape_matches(
        command: Any,
        allowed_shapes: tuple[dict[str, Any], ...],
    ) -> bool:
        return _remote_application_command_shape(
            command,
            guild_registry=True,
        ) in allowed_shapes

    @staticmethod
    def _temporary_conversation_archive_command_payloads(
        payloads: dict[str, dict[str, Any]],
        desired_shapes: dict[str, dict[str, Any]],
        run_id: str,
    ) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
        temporary: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for final_name, payload in payloads.items():
            temporary_payload = dict(payload)
            temporary_payload["name"] = _temporary_application_command_name(
                run_id,
                final_name,
            )
            temporary[final_name] = (
                temporary_payload,
                _normalized_application_command_shape(temporary_payload),
            )
            if desired_shapes[final_name]["name"] != final_name:
                raise RuntimeError("archive_command_publish_contract_invalid")
        return temporary

    @staticmethod
    def _validate_conversation_archive_owned_shape_contract(
        allowed_shapes: tuple[dict[str, Any], ...],
        desired_shapes: dict[str, dict[str, Any]],
    ) -> str:
        final_names = [
            name for name, shape in desired_shapes.items() if shape in allowed_shapes
        ]
        if len(final_names) != 1:
            raise RuntimeError("archive_command_ownership_ledger_invalid")
        final_name = final_names[0]
        if len(allowed_shapes) == 1:
            return final_name
        if any(
            shape["name"] in desired_shapes and shape != desired_shapes[final_name]
            for shape in allowed_shapes
        ):
            raise RuntimeError("archive_command_ownership_ledger_invalid")
        return final_name

    def _load_conversation_archive_command_ownership(
        self,
        bot: Any,
        guild_id: int,
        remote_guild: list[Any],
        desired_shapes: dict[str, dict[str, Any]],
        *,
        adopt_stale_temporary: bool = False,
    ) -> None:
        if self._conversation_archive_command_ownership_loaded:
            return
        config = self.deps.events.conversation_archive_command_ownership
        try:
            loaded = read_command_ownership_ledger(
                config,
                application_id=bot.application_id,
                guild_id=guild_id,
                normalize_shape=lambda shape: _normalized_application_command_shape(
                    shape,
                    guild_registry=True,
                ),
            )
        except Exception:
            self._conversation_archive_command_recovery_required = True
            raise RuntimeError("archive_command_ownership_ledger_read_failed") from None
        self._conversation_archive_command_ownership_loaded = True
        if loaded is None:
            return
        loaded_commands, recovery_required = loaded
        validated = validate_command_ownership_config(config)
        if validated is None:
            raise RuntimeError("archive_command_ownership_configuration_invalid")
        remote_by_id = {
            _remote_application_command_id(command): command for command in remote_guild
        }
        reconciled: dict[int, tuple[dict[str, Any], ...]] = {}
        final_names: set[str] = set()
        drift = False
        for command_id, allowed_shapes in loaded_commands.items():
            try:
                final_name = self._validate_conversation_archive_owned_shape_contract(
                    allowed_shapes,
                    desired_shapes,
                )
                if final_name in final_names:
                    raise RuntimeError("archive_command_ownership_ledger_invalid")
            except Exception:
                self._conversation_archive_command_recovery_required = True
                raise RuntimeError("archive_command_ownership_ledger_read_failed") from None
            final_names.add(final_name)
            command = remote_by_id.get(command_id)
            if command is None:
                continue
            reconciled[command_id] = allowed_shapes
            if not self._owned_application_command_shape_matches(command, allowed_shapes):
                drift = True
        stale_temporary = False
        if adopt_stale_temporary:
            temporary_shapes = self._temporary_conversation_archive_command_payloads(
                {
                    name: dict(shape)
                    for name, shape in desired_shapes.items()
                },
                desired_shapes,
                validated[1],
            )
            for final_name, (_payload, temporary_shape) in temporary_shapes.items():
                if final_name in final_names:
                    continue
                matches = [
                    command
                    for command in remote_guild
                    if _remote_application_command_shape(
                        command,
                        guild_registry=True,
                    )
                    == temporary_shape
                ]
                if len(matches) > 1:
                    self._conversation_archive_command_recovery_required = True
                    raise RuntimeError("archive_command_ownership_stale_ambiguous")
                if not matches:
                    continue
                command_id = _remote_application_command_id(matches[0])
                if command_id in loaded_commands or command_id in reconciled:
                    self._conversation_archive_command_recovery_required = True
                    raise RuntimeError("archive_command_ownership_stale_ambiguous")
                reconciled[command_id] = (
                    temporary_shape,
                    desired_shapes[final_name],
                )
                final_names.add(final_name)
                stale_temporary = True
        self._conversation_archive_owned_commands = reconciled
        self._conversation_archive_command_recovery_required = bool(
            recovery_required or drift or stale_temporary
        )
        if reconciled != loaded_commands or drift or stale_temporary:
            self._persist_conversation_archive_command_ownership(bot, guild_id)
        if drift:
            raise RuntimeError("archive_command_ownership_remote_drift")

    @staticmethod
    async def _post_temporary_conversation_archive_command(
        bot: Any,
        guild_id: int,
        payload: dict[str, Any],
        statuses: list[int],
    ) -> Any:
        async def observe_status(response: Any) -> None:
            status = getattr(response, "status", None)
            statuses.append(status if isinstance(status, int) else 0)

        route = Route(
            "POST",
            "/applications/{application_id}/guilds/{guild_id}/commands",
            application_id=bot.application_id,
            guild_id=guild_id,
        )
        return await bot.http.request(
            route,
            json=payload,
            raise_for_status=observe_status,
        )

    def _record_created_conversation_archive_command(
        self,
        *,
        bot: Any,
        guild_id: int,
        created: Any,
        expected_temporary_shape: dict[str, Any],
        desired_shape: dict[str, Any],
        baseline_ids: set[int],
    ) -> tuple[int, dict[str, Any]]:
        command_id = _remote_application_command_id(created)
        created_shape = _remote_application_command_shape(
            created,
            guild_registry=True,
        )
        if (
            command_id in baseline_ids
            or command_id in self._conversation_archive_owned_commands
        ):
            raise RuntimeError("archive_command_publish_response_invalid")
        self._conversation_archive_owned_commands[command_id] = (
            created_shape,
            desired_shape,
        )
        self._persist_conversation_archive_command_ownership(bot, guild_id)
        return command_id, created_shape

    async def _recover_created_conversation_archive_command(
        self,
        *,
        bot: Any,
        guild_id: int,
        expected_temporary_shape: dict[str, Any],
        desired_shape: dict[str, Any],
        baseline_ids: set[int],
    ) -> bool:
        remote = list(
            await bot.http.get_guild_commands(
                bot.application_id,
                guild_id,
            )
        )
        matches = [
            command
            for command in remote
            if _remote_application_command_shape(
                command,
                guild_registry=True,
            )
            == expected_temporary_shape
            and _remote_application_command_id(command) not in baseline_ids
        ]
        if len(matches) != 1:
            return False
        self._record_created_conversation_archive_command(
            bot=bot,
            guild_id=guild_id,
            created=matches[0],
            expected_temporary_shape=expected_temporary_shape,
            desired_shape=desired_shape,
            baseline_ids=baseline_ids,
        )
        return True

    @staticmethod
    def _classify_conversation_archive_application_commands(
        remote: Any,
        desired_shapes: dict[str, dict[str, Any]],
        *,
        error_code: str,
    ) -> tuple[dict[str, Any], set[str]]:
        grouped = {name: [] for name in desired_shapes}
        for command in remote:
            name = _remote_application_command_name(command)
            if name in grouped:
                grouped[name].append(command)
        exact: dict[str, Any] = {}
        missing: set[str] = set()
        for name, matches in grouped.items():
            if not matches:
                missing.add(name)
                continue
            try:
                valid = (
                    len(matches) == 1
                    and _remote_application_command_shape(
                        matches[0],
                        guild_registry=True,
                    )
                    == desired_shapes[name]
                )
            except Exception:
                valid = False
            if not valid:
                raise RuntimeError(error_code)
            exact[name] = matches[0]
        return exact, missing

    @staticmethod
    def _foreign_conversation_archive_command_snapshot(remote: Any) -> tuple[Any, ...]:
        return _remote_application_command_snapshot(
            (
                command
                for command in remote
                if _remote_application_command_name(command)
                not in _CONVERSATION_ARCHIVE_APPLICATION_COMMAND_NAMES
            ),
            guild_registry=True,
        )

    async def _fetch_conversation_archive_command_registries(
        self,
        bot: Any,
        guild_id: int,
    ) -> tuple[list[Any], list[Any]]:
        guild_commands = await bot.http.get_guild_commands(
            bot.application_id,
            guild_id,
        )
        global_commands = await bot.http.get_global_commands(bot.application_id)
        return list(guild_commands), list(global_commands)

    async def _rollback_new_conversation_archive_application_commands(
        self,
        *,
        bot: Any,
        guild_id: int,
        baseline_guild: tuple[Any, ...],
        baseline_global: tuple[Any, ...],
    ) -> None:
        try:
            current = list(
                await bot.http.get_guild_commands(
                    bot.application_id,
                    guild_id,
                )
            )
            current_by_id = {
                _remote_application_command_id(command): command
                for command in current
            }
            owned_current: dict[int, Any] = {}
            for command_id, allowed_shapes in tuple(
                self._conversation_archive_owned_commands.items()
            ):
                command = current_by_id.get(command_id)
                if command is None:
                    self._conversation_archive_owned_commands.pop(command_id, None)
                    continue
                if not self._owned_application_command_shape_matches(
                    command,
                    allowed_shapes,
                ):
                    raise RuntimeError("archive_command_publish_rollback_ambiguous")
                owned_current[command_id] = command
            for command_id in owned_current:
                fresh = await bot.http.get_guild_commands(
                    bot.application_id,
                    guild_id,
                )
                command = next(
                    (
                        item
                        for item in fresh
                        if _remote_application_command_id(item) == command_id
                    ),
                    None,
                )
                if command is None:
                    continue
                if not self._owned_application_command_shape_matches(
                    command,
                    self._conversation_archive_owned_commands[command_id],
                ):
                    raise RuntimeError("archive_command_publish_rollback_ambiguous")
                try:
                    await bot.http.delete_guild_command(
                        bot.application_id,
                        guild_id,
                        command_id,
                    )
                except Exception:
                    remaining = await bot.http.get_guild_commands(
                        bot.application_id,
                        guild_id,
                    )
                    if any(
                        _remote_application_command_id(item) == command_id
                        for item in remaining
                    ):
                        raise
            restored_guild, restored_global = (
                await self._fetch_conversation_archive_command_registries(
                    bot,
                    guild_id,
                )
            )
            restored_ids = {
                _remote_application_command_id(command)
                for command in restored_guild
            }
            for command_id in tuple(self._conversation_archive_owned_commands):
                if command_id not in restored_ids:
                    self._conversation_archive_owned_commands.pop(command_id, None)
            self._persist_conversation_archive_command_ownership(bot, guild_id)
            if self._conversation_archive_owned_commands:
                raise RuntimeError("archive_command_publish_rollback_incomplete")
            if (
                _remote_application_command_snapshot(
                    restored_guild,
                    guild_registry=True,
                )
                != baseline_guild
                or _remote_application_command_snapshot(restored_global)
                != baseline_global
            ):
                self._conversation_archive_command_recovery_required = True
                self._persist_conversation_archive_command_ownership(bot, guild_id)
                raise RuntimeError("archive_command_publish_rollback_invariant_failed")
        except Exception:
            self._conversation_archive_command_recovery_required = True
            try:
                self._persist_conversation_archive_command_ownership(bot, guild_id)
            except Exception:
                pass
            raise RuntimeError("archive_command_publish_rollback_failed") from None

    async def _publish_conversation_archive_application_commands(self) -> None:
        async with self._conversation_archive_command_publish_lock:
            if self._conversation_archive_commands_published:
                return
            if self._conversation_archive_command_recovery_required:
                raise RuntimeError("archive_command_publish_recovery_required")
            await self._publish_conversation_archive_application_commands_locked()

    async def _publish_conversation_archive_application_commands_locked(self) -> None:
        bot, guild_id, payloads, desired_shapes = (
            self._conversation_archive_command_context()
        )
        try:
            baseline_guild, baseline_globals = (
                await self._fetch_conversation_archive_command_registries(
                    bot,
                    guild_id,
                )
            )
        except Exception:
            raise RuntimeError("archive_command_publish_failed") from None
        self._load_conversation_archive_command_ownership(
            bot,
            guild_id,
            baseline_guild,
            desired_shapes,
        )
        if self._conversation_archive_command_recovery_required:
            raise RuntimeError("archive_command_publish_recovery_required")
        _existing, missing = self._classify_conversation_archive_application_commands(
            baseline_guild,
            desired_shapes,
            error_code="archive_command_publish_collision",
        )
        baseline_foreign = self._foreign_conversation_archive_command_snapshot(
            baseline_guild
        )
        baseline_guild_snapshot = _remote_application_command_snapshot(
            baseline_guild,
            guild_registry=True,
        )
        baseline_ids = {
            _remote_application_command_id(command) for command in baseline_guild
        }
        baseline_global = _remote_application_command_snapshot(baseline_globals)
        if self._conversation_archive_owned_commands:
            owned_remote = {
                command_id: next(
                    (
                        command
                        for command in baseline_guild
                        if _remote_application_command_id(command) == command_id
                    ),
                    None,
                )
                for command_id in self._conversation_archive_owned_commands
            }
            owned_final_names = {
                _remote_application_command_name(command)
                for command in owned_remote.values()
                if command is not None
                and _remote_application_command_shape(
                    command,
                    guild_registry=True,
                )
                in desired_shapes.values()
            }
            if (
                len(self._conversation_archive_owned_commands)
                != len(desired_shapes)
                or owned_final_names != set(desired_shapes)
                or missing
            ):
                self._conversation_archive_command_recovery_required = True
                self._persist_conversation_archive_command_ownership(bot, guild_id)
                raise RuntimeError("archive_command_publish_restart_incomplete")
        ownership_config = validate_command_ownership_config(
            self.deps.events.conversation_archive_command_ownership
        )
        if missing and ownership_config is None:
            raise RuntimeError("archive_command_ownership_configuration_required")
        temporary_payloads = (
            self._temporary_conversation_archive_command_payloads(
                payloads,
                desired_shapes,
                ownership_config[1],
            )
            if ownership_config is not None
            else {}
        )
        self._persist_conversation_archive_command_ownership(bot, guild_id)
        failure_code = ""
        awaiting_owned_response = False
        pending_base_exception: BaseException | None = None
        try:
            for name, payload in payloads.items():
                if name not in missing:
                    continue
                temporary_payload, expected_temporary_shape = temporary_payloads[name]
                statuses: list[int] = []
                awaiting_owned_response = True
                try:
                    created = await self._post_temporary_conversation_archive_command(
                        bot,
                        guild_id,
                        temporary_payload,
                        statuses,
                    )
                except BaseException:
                    if statuses and statuses[-1] == 201:
                        try:
                            recovered = await (
                                self._recover_created_conversation_archive_command(
                                    bot=bot,
                                    guild_id=guild_id,
                                    expected_temporary_shape=expected_temporary_shape,
                                    desired_shape=desired_shapes[name],
                                    baseline_ids=baseline_ids,
                                )
                            )
                        except Exception:
                            recovered = False
                        awaiting_owned_response = not recovered
                    raise
                if not statuses or statuses[-1] != 201:
                    self._conversation_archive_command_recovery_required = True
                    awaiting_owned_response = False
                    self._persist_conversation_archive_command_ownership(bot, guild_id)
                    raise RuntimeError("archive_command_publish_newness_unproven")
                owned_before_record = set(self._conversation_archive_owned_commands)
                try:
                    command_id, created_shape = (
                        self._record_created_conversation_archive_command(
                            bot=bot,
                            guild_id=guild_id,
                            created=created,
                            expected_temporary_shape=expected_temporary_shape,
                            desired_shape=desired_shapes[name],
                            baseline_ids=baseline_ids,
                        )
                    )
                except BaseException:
                    recovered = bool(
                        set(self._conversation_archive_owned_commands)
                        - owned_before_record
                    )
                    if not recovered:
                        try:
                            recovered = await (
                                self._recover_created_conversation_archive_command(
                                    bot=bot,
                                    guild_id=guild_id,
                                    expected_temporary_shape=expected_temporary_shape,
                                    desired_shape=desired_shapes[name],
                                    baseline_ids=baseline_ids,
                                )
                            )
                        except Exception:
                            recovered = False
                    awaiting_owned_response = not recovered
                    raise
                awaiting_owned_response = False
                if created_shape != expected_temporary_shape:
                    raise RuntimeError("archive_command_publish_response_invalid")
                edited = await bot.http.edit_guild_command(
                    bot.application_id,
                    guild_id,
                    command_id,
                    payload,
                )
                if (
                    _remote_application_command_id(edited) != command_id
                    or _remote_application_command_shape(
                        edited,
                        guild_registry=True,
                    )
                    != desired_shapes[name]
                ):
                    raise RuntimeError("archive_command_publish_response_invalid")
                self._conversation_archive_owned_commands[command_id] = (
                    desired_shapes[name],
                )
                self._persist_conversation_archive_command_ownership(bot, guild_id)
            remote_guild, remote_globals = (
                await self._fetch_conversation_archive_command_registries(
                    bot,
                    guild_id,
                )
            )
        except BaseException as exc:
            pending_base_exception = exc
            if awaiting_owned_response:
                self._conversation_archive_command_recovery_required = True
                try:
                    self._persist_conversation_archive_command_ownership(bot, guild_id)
                except Exception:
                    pass
            failure_code = "archive_command_publish_failed"
        if not failure_code:
            try:
                _exact, remote_missing = (
                    self._classify_conversation_archive_application_commands(
                        remote_guild,
                        desired_shapes,
                        error_code="archive_command_publish_verification_failed",
                    )
                )
                if (
                    remote_missing
                    or self._foreign_conversation_archive_command_snapshot(remote_guild)
                    != baseline_foreign
                    or _remote_application_command_snapshot(remote_globals)
                    != baseline_global
                ):
                    raise RuntimeError("archive_command_publish_verification_failed")
            except Exception:
                failure_code = "archive_command_publish_verification_failed"
        if failure_code:
            await self._rollback_new_conversation_archive_application_commands(
                bot=bot,
                guild_id=guild_id,
                baseline_guild=baseline_guild_snapshot,
                baseline_global=baseline_global,
            )
            if pending_base_exception is not None and not isinstance(
                pending_base_exception,
                Exception,
            ):
                raise pending_base_exception
            raise RuntimeError(failure_code)
        self._conversation_archive_commands_published = True

    async def _clear_conversation_archive_application_commands(self) -> None:
        async with self._conversation_archive_command_publish_lock:
            await self._clear_conversation_archive_application_commands_locked()

    async def _clear_conversation_archive_application_commands_locked(self) -> None:
        bot, guild_id, _payloads, _desired_shapes = (
            self._conversation_archive_command_context()
        )
        try:
            baseline_guild, baseline_globals = (
                await self._fetch_conversation_archive_command_registries(
                    bot,
                    guild_id,
                )
            )
        except Exception:
            raise RuntimeError("archive_command_clear_failed") from None
        baseline_global = _remote_application_command_snapshot(baseline_globals)
        current_by_id = {
            _remote_application_command_id(command): command
            for command in baseline_guild
        }
        owned_current: dict[int, Any] = {}
        for command_id, allowed_shapes in tuple(
            self._conversation_archive_owned_commands.items()
        ):
            command = current_by_id.get(command_id)
            if command is None:
                self._conversation_archive_owned_commands.pop(command_id, None)
                continue
            if not self._owned_application_command_shape_matches(
                command,
                allowed_shapes,
            ):
                self._conversation_archive_command_recovery_required = True
                self._persist_conversation_archive_command_ownership(bot, guild_id)
                raise RuntimeError("archive_command_clear_drift")
            owned_current[command_id] = command
        expected_guild = _remote_application_command_snapshot(
            (
                command
                for command_id, command in current_by_id.items()
                if command_id not in owned_current
            ),
            guild_registry=True,
        )
        try:
            for command_id in owned_current:
                fresh = await bot.http.get_guild_commands(
                    bot.application_id,
                    guild_id,
                )
                command = next(
                    (
                        item
                        for item in fresh
                        if _remote_application_command_id(item) == command_id
                    ),
                    None,
                )
                if command is None:
                    continue
                if not self._owned_application_command_shape_matches(
                    command,
                    self._conversation_archive_owned_commands[command_id],
                ):
                    raise RuntimeError("archive_command_clear_drift")
                try:
                    await bot.http.delete_guild_command(
                        bot.application_id,
                        guild_id,
                        command_id,
                    )
                except Exception:
                    remaining = await bot.http.get_guild_commands(
                        bot.application_id,
                        guild_id,
                    )
                    if any(
                        _remote_application_command_id(item) == command_id
                        for item in remaining
                    ):
                        raise
            remote_guild, remote_globals = (
                await self._fetch_conversation_archive_command_registries(
                    bot,
                    guild_id,
                )
            )
        except Exception:
            raise RuntimeError("archive_command_clear_failed") from None
        remote_ids = {
            _remote_application_command_id(command) for command in remote_guild
        }
        for command_id in tuple(self._conversation_archive_owned_commands):
            if command_id not in remote_ids:
                self._conversation_archive_owned_commands.pop(command_id, None)
        self._persist_conversation_archive_command_ownership(bot, guild_id)
        if (
            self._conversation_archive_owned_commands
            or _remote_application_command_snapshot(
                remote_guild,
                guild_registry=True,
            )
            != expected_guild
            or _remote_application_command_snapshot(remote_globals) != baseline_global
        ):
            raise RuntimeError("archive_command_clear_verification_failed")
        self._conversation_archive_commands_published = False

    def _archive_operator_is_authorized(self, ctx: Any) -> bool:
        predicate = self.deps.commands.conversation_archive_operator_authorized
        if not callable(predicate):
            return False
        try:
            return predicate(ctx) is True
        except Exception as exc:
            self._record_runtime_error(
                "conversation_archive_operator_authorization_failed",
                exc,
            )
            return False

    def _cancel_shared_session_expiry_task(self, guild_id: int) -> None:
        task = self._conversation_shared_session_expiry_tasks.pop(
            int(guild_id),
            None,
        )
        if (
            task is not None
            and task is not asyncio.current_task()
            and not task.done()
        ):
            task.cancel()

    def _cancel_all_shared_session_expiry_tasks(self) -> None:
        for guild_id in tuple(self._conversation_shared_session_expiry_tasks):
            self._cancel_shared_session_expiry_task(guild_id)

    async def _expire_shared_session(
        self,
        session: DiscordSharedSession,
    ) -> None:
        try:
            await asyncio.sleep(self._shared_sessions().seconds_until_expiry(session))
            await self._close_shared_session(
                session.guild_id,
                expected=session,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_runtime_error(
                "conversation_shared_session_expiry_failed",
                exc,
            )
            self._shared_sessions().close(
                guild_id=session.guild_id,
                expected=session,
            )
        finally:
            current = self._conversation_shared_session_expiry_tasks.get(
                session.guild_id
            )
            if current is asyncio.current_task():
                self._conversation_shared_session_expiry_tasks.pop(
                    session.guild_id,
                    None,
                )

    async def _close_shared_session(
        self,
        guild_id: int,
        *,
        expected: DiscordSharedSession | None = None,
    ) -> bool:
        sessions = self._shared_sessions()
        session = sessions.peek(guild_id=int(guild_id))
        if session is None or (expected is not None and session is not expected):
            return False
        self._cancel_shared_session_expiry_task(session.guild_id)
        try:
            if callable(self.deps.events.conversation_participation_observer):
                updates = self._conversation_participation_tracker.mark_gateway_unknown(
                    observed_at=self._conversation_archive_observed_at(),
                    guild_id=session.guild_id,
                )
                for update in updates:
                    if update.closed:
                        await self._emit_participation_update(update)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_runtime_error(
                "conversation_shared_session_close_failed",
                exc,
            )
        try:
            closer = self.deps.events.conversation_shared_session_close
            if callable(closer):
                result = closer(session)
                if inspect.isawaitable(result):
                    await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_runtime_error(
                "conversation_shared_session_lease_close_failed",
                exc,
            )
        finally:
            sessions.close(guild_id=session.guild_id, expected=session)
        return True

    async def _open_shared_session(self, ctx: Any, voice_channel: Any) -> None:
        guild = getattr(ctx, "guild", None)
        text_channel = getattr(ctx, "channel", None)
        author = getattr(ctx, "author", None)
        if guild is None or text_channel is None or author is None:
            raise RuntimeError("conversation_shared_session_context_missing")
        session = self._shared_sessions().open(
            operator_user_id=int(author.id),
            guild_id=int(guild.id),
            text_channel_id=int(text_channel.id),
            voice_channel_id=int(voice_channel.id),
        )
        try:
            opener = self.deps.events.conversation_shared_session_open
            if not callable(opener):
                raise RuntimeError("conversation_shared_session_open_missing")
            result = opener(session)
            if inspect.isawaitable(result):
                await result
            if self._shared_sessions().peek(guild_id=session.guild_id) is not session:
                raise RuntimeError("conversation_shared_session_open_superseded")
            self._cancel_shared_session_expiry_task(session.guild_id)
            self._conversation_shared_session_expiry_tasks[session.guild_id] = (
                asyncio.create_task(self._expire_shared_session(session))
            )
            for member in tuple(getattr(voice_channel, "members", ()) or ()):
                state = getattr(member, "voice", None)
                if state is not None:
                    await self._observe_human_voice_state(member, state)
        except BaseException:
            await self._close_shared_session(
                session.guild_id,
                expected=session,
            )
            raise

    async def _emit_participation_update(self, update: Any) -> None:
        observer = self.deps.events.conversation_participation_observer
        if not callable(observer):
            raise RuntimeError("conversation_participation_observer_missing")
        result = observer(update)
        if inspect.isawaitable(result):
            await result

    def _conversation_archive_observed_at(self) -> float:
        observed_at = max(time.time(), self._last_conversation_archive_observed_at)
        self._last_conversation_archive_observed_at = observed_at
        return observed_at

    def _conversation_consent_is_current(
        self,
        *,
        guild_id: int,
        channel_id: int | None,
        user_id: int,
    ) -> bool:
        resolver = self.deps.events.conversation_consent_current
        if channel_id is None or not callable(resolver):
            return False
        try:
            return resolver(
                guild_id=int(guild_id),
                channel_id=int(channel_id),
                user_id=int(user_id),
            ) is True
        except Exception as exc:
            self._record_runtime_error("conversation_consent_resolve_failed", exc)
            return False

    async def _observe_human_voice_state(
        self,
        member: Any,
        state: Any,
        *,
        gate_channel_id: int | None = None,
    ) -> None:
        if not self._conversation_archive_enabled:
            return
        user = self.deps.events.bot_user()
        if user is None or getattr(member, "id", None) == getattr(user, "id", None):
            return
        if getattr(member, "bot", False) is True:
            return
        guild = getattr(member, "guild", None)
        if guild is None:
            return
        raw_channel_id = getattr(getattr(state, "channel", None), "id", None)
        actual_channel_id = (
            None if raw_channel_id is None else int(raw_channel_id)
        )
        bound_channel_id = (
            actual_channel_id if gate_channel_id is None else gate_channel_id
        )
        if bound_channel_id is None:
            return
        try:
            shared_session = self._shared_sessions().current(
                guild_id=int(guild.id),
                voice_channel_id=int(bound_channel_id),
            )
        except Exception as exc:
            self._record_runtime_error(
                "conversation_shared_session_gate_failed",
                exc,
            )
            return
        if shared_session is None:
            return
        if not callable(self.deps.events.conversation_participation_observer):
            exc = RuntimeError("conversation_participation_observer_missing")
            self._record_runtime_error("conversation_participation_update_failed", exc)
            self.deps.events.log(
                "[ARCHIVE VOICE] update_fail errorType=RuntimeError"
            )
            return
        snapshot = voice_state_snapshot_from_discord(
            state,
            consent_current=self._conversation_consent_is_current(
                guild_id=guild.id,
                channel_id=(
                    actual_channel_id
                    if actual_channel_id == shared_session.voice_channel_id
                    else None
                ),
                user_id=member.id,
            ),
        )
        if actual_channel_id != shared_session.voice_channel_id:
            snapshot = replace(snapshot, channel_id=None)
        try:
            update = self._conversation_participation_tracker.observe(
                guild_id=guild.id,
                user_id=member.id,
                observed_at=self._conversation_archive_observed_at(),
                snapshot=snapshot,
                owner_name=str(
                    getattr(member, "display_name", None)
                    or getattr(member, "global_name", None)
                    or getattr(member, "name", None)
                    or member.id
                ),
            )
            await self._emit_participation_update(update)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_runtime_error("conversation_participation_update_failed", exc)
            self.deps.events.log(
                "[ARCHIVE VOICE] update_fail "
                f"errorType={type(exc).__name__}"
            )

    async def _rebuild_human_voice_state_from_ready(self) -> None:
        if not self._conversation_archive_enabled:
            return
        for guild in self.deps.events.bot_guilds():
            channels = (
                *tuple(getattr(guild, "voice_channels", ()) or ()),
                *tuple(getattr(guild, "stage_channels", ()) or ()),
            )
            seen_channels: set[int] = set()
            for channel in channels:
                channel_id = getattr(channel, "id", None)
                if channel_id is not None and int(channel_id) in seen_channels:
                    continue
                if channel_id is not None:
                    seen_channels.add(int(channel_id))
                for member in tuple(getattr(channel, "members", ()) or ()):
                    state = getattr(member, "voice", None)
                    if state is None:
                        continue
                    await self._observe_human_voice_state(member, state)

    def _record_runtime_error(self, code: str, exc: BaseException) -> None:
        runtime_status = self.deps.events.runtime_status
        if runtime_status is not None:
            try:
                runtime_status.record_error(code, exc)
            except Exception:
                pass

    async def _recover_search_followups_once(self) -> bool:
        deps = self.deps.events
        async with self._search_recovery_lock:
            if self._search_recovery_state == _SEARCH_RECOVERY_COMPLETE:
                return True
            if self._search_recovery_state == _SEARCH_RECOVERY_FAILED:
                return False
            self._search_recovery_state = _SEARCH_RECOVERY_RUNNING
            try:
                recovery = await deps.recover_search_followups()
                if int(recovery.get("pending", 0)):
                    deps.log(
                        "[SEARCH] recovery_complete "
                        f"pending={int(recovery.get('pending', 0))} "
                        f"resumed={int(recovery.get('resumed', 0))} "
                        f"verified={int(recovery.get('verified', 0))} "
                        f"redelivered={int(recovery.get('redelivered', 0))} "
                        f"uncertain={int(recovery.get('uncertain', 0))}"
                    )
            except asyncio.CancelledError as exc:
                self._search_recovery_state = _SEARCH_RECOVERY_FAILED
                self._record_runtime_error(
                    "search_followup_recovery_failed",
                    exc,
                )
                raise
            except Exception as exc:
                self._search_recovery_state = _SEARCH_RECOVERY_FAILED
                self._record_runtime_error(
                    "search_followup_recovery_failed",
                    exc,
                )
                return False
            self._search_recovery_state = _SEARCH_RECOVERY_COMPLETE
            return True

    async def admit_search_followup_ingress(self) -> bool:
        state = self._search_recovery_state
        if state == _SEARCH_RECOVERY_COMPLETE:
            return True
        if state != _SEARCH_RECOVERY_RUNNING:
            return False
        async with self._search_recovery_lock:
            return self._search_recovery_state == _SEARCH_RECOVERY_COMPLETE

    def _command_context(self, ctx: Any) -> Any:
        if isinstance(ctx, ContinuityRecordingCommandContext):
            return ctx
        runtime_deps = self.deps.commands.command_session()
        return ContinuityRecordingCommandContext(
            ctx,
            record_reply=self.mark_text_session_from_command,
            log=self.deps.commands.log,
            runtime_deps=runtime_deps,
        )

    async def _admit_guild_mutation(self, ctx: Any) -> bool:
        guild = getattr(ctx, "guild", None)
        if guild is None:
            return True
        try:
            allowed = self.deps.commands.guild_is_open(guild.id)
        except Exception:
            allowed = False
        if allowed:
            return True
        await ctx.send(
            "길드 초기화를 마무리하는 중이야. 초기화를 다시 시도해줘."
        )
        return False

    async def on_ready(self) -> None:
        deps = self.deps.events
        self._ready_generation += 1
        ready_generation = self._ready_generation
        if deps.runtime_status is not None:
            deps.runtime_status.start()
            deps.runtime_status.write_once()
        user = deps.bot_user()
        deps.log(f"로그인 완료: {user}")
        deps.mark_startup_component("discord_gateway", "done", deps.clean_text(str(user or "")))
        try:
            await deps.ensure_startup_components_ready()
        except Exception as exc:
            self._record_runtime_error("startup_initialization_failed", exc)
            deps.log(f"[STARTUP] init_fail err={exc!r}")
            raise
        if self._conversation_archive_enabled:
            sessions = self._shared_sessions()
            self._cancel_all_shared_session_expiry_tasks()
            for session in sessions.snapshot():
                await self._close_shared_session(
                    session.guild_id,
                    expected=session,
                )
            if not callable(deps.conversation_archive_ready):
                raise RuntimeError("conversation_archive_ready_missing")
            ready_result = deps.conversation_archive_ready()
            if inspect.isawaitable(ready_result):
                ready_result = await ready_result
            if not isinstance(ready_result, str) or not ready_result:
                raise RuntimeError("conversation_archive_generation_invalid")
            sessions.begin_generation(ready_result)
            await self._publish_conversation_archive_application_commands()
            worker = deps.conversation_archive_otp_delivery_worker
            if callable(worker) and (
                self._conversation_archive_otp_task is None
                or self._conversation_archive_otp_task.done()
            ):
                self._conversation_archive_otp_task = asyncio.create_task(worker())
        await self._rebuild_human_voice_state_from_ready()
        if not await self._recover_search_followups_once():
            return
        deps.ensure_voice_worker_started()
        try:
            await deps.start_control_page_server()
        except Exception as exc:
            error_type = type(exc).__name__
            self._record_runtime_error("control_page_start_failed", exc)
            deps.mark_startup_component(
                "control_api", "failed", f"control_page_start_failed:{error_type}"
            )
            deps.log(
                "[CONTROL PAGE] start_fail "
                f"errorCode=control_page_start_failed errorType={error_type}"
            )
        try:
            await deps.ensure_local_mic_service_started()
            deps.ensure_vision_watch_started()
        except Exception as exc:
            self._record_runtime_error("startup_initialization_failed", exc)
            deps.log(f"[STARTUP] init_fail err={exc!r}")
            raise
        try:
            await deps.ensure_control_page_background_tasks_started()
        except Exception as exc:
            self._record_runtime_error("control_page_background_tasks_failed", exc)
            deps.log(f"[CONTROL PAGE] bg_tasks_fail err={exc!r}")
        for guild in deps.bot_guilds():
            voice_client = guild.voice_client
            if isinstance(voice_client, deps.voice_client_type):
                deps.log(
                    f"[VOICE READY] guild={guild.id} "
                    f"channel={getattr(getattr(voice_client, 'channel', None), 'name', None)} "
                    f"listening={voice_client.is_listening()}"
                )
                try:
                    if voice_client.channel is not None:
                        await deps.ensure_listening_voice_client(guild, voice_client.channel)
                except Exception as exc:
                    self._record_runtime_error("voice_rearm_failed", exc)
                    deps.log(f"[VOICE READY REARM FAIL] guild={guild.id} err={exc!r}")
            elif voice_client is not None:
                deps.log(f"[VOICE READY] guild={guild.id} unexpected_voice_client={type(voice_client)!r}")
            elif deps.voice_rejoin_on_ready:
                ok = False
                detail = "voice_rearm_failed"
                superseded = False
                for attempt in range(_VOICE_REARM_ATTEMPTS):
                    if ready_generation != self._ready_generation:
                        superseded = True
                        break
                    ok, detail = await deps.restore_last_voice_channel(guild)
                    if ready_generation != self._ready_generation:
                        superseded = True
                        break
                    if (
                        ok
                        or detail != _VOICE_READY_RESTORE_RETRY_REASON
                        or attempt + 1 >= _VOICE_REARM_ATTEMPTS
                    ):
                        break
                    await asyncio.sleep(_VOICE_REARM_RETRY_DELAY_SEC)
                if superseded:
                    continue
                if ok:
                    deps.log(f"[VOICE READY REJOIN] guild={guild.id} channel={detail}")
                elif detail not in {"no_saved_voice_channel", "manual_disconnect"}:
                    deps.log(f"[VOICE READY REJOIN SKIP] guild={guild.id} reason={detail}")
            if deps.autonomy_enabled:
                deps.log(
                    f"[AUTONOMY] guild={guild.id} "
                    "available approval_required=true"
                )

    async def on_disconnect(self) -> None:
        if not self._conversation_archive_enabled:
            return
        otp_task = self._conversation_archive_otp_task
        self._conversation_archive_otp_task = None
        if otp_task is not None and not otp_task.done():
            otp_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await otp_task
        try:
            sessions = self._shared_sessions()
            for session in sessions.snapshot():
                await self._close_shared_session(
                    session.guild_id,
                    expected=session,
                )
            self._cancel_all_shared_session_expiry_tasks()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_runtime_error("conversation_gateway_unknown_failed", exc)
            self.deps.events.log(
                "[ARCHIVE VOICE] gateway_unknown_fail "
                f"errorType={type(exc).__name__}"
            )
            with contextlib.suppress(Exception):
                self._shared_sessions().close_all()
            self._cancel_all_shared_session_expiry_tasks()

    async def on_voice_state_update(self, member: Any, before: Any, after: Any) -> None:
        deps = self.deps.events
        user = deps.bot_user()
        if (
            self._conversation_archive_enabled
            and user is not None
            and getattr(member, "id", None) != getattr(user, "id", None)
        ):
            guild = getattr(member, "guild", None)
            after_channel_id = getattr(getattr(after, "channel", None), "id", None)
            before_channel_id = getattr(getattr(before, "channel", None), "id", None)
            gate_channel_id = None
            if guild is not None:
                with contextlib.suppress(Exception):
                    sessions = self._shared_sessions()
                    if after_channel_id is not None and sessions.current(
                        guild_id=int(guild.id),
                        voice_channel_id=int(after_channel_id),
                    ) is not None:
                        gate_channel_id = int(after_channel_id)
                    elif before_channel_id is not None and sessions.current(
                        guild_id=int(guild.id),
                        voice_channel_id=int(before_channel_id),
                    ) is not None:
                        gate_channel_id = int(before_channel_id)
            if gate_channel_id is not None:
                await self._observe_human_voice_state(
                    member,
                    after,
                    gate_channel_id=gate_channel_id,
                )
            return
        if user is None or member.id != user.id:
            return
        if deps.runtime_status is not None:
            deps.runtime_status.write_once()
        guild = getattr(member, "guild", None)
        if guild is None:
            return
        if self._conversation_archive_enabled:
            try:
                session = self._shared_sessions().peek(guild_id=int(guild.id))
                after_channel_id = getattr(
                    getattr(after, "channel", None),
                    "id",
                    None,
                )
                if session is not None and (
                    after_channel_id is None
                    or int(after_channel_id) != session.voice_channel_id
                ):
                    await self._close_shared_session(
                        session.guild_id,
                        expected=session,
                    )
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_runtime_error(
                    "conversation_shared_session_bot_leave_failed",
                    exc,
                )
                with contextlib.suppress(Exception):
                    self._shared_sessions().close(guild_id=int(guild.id))
        if not await self.admit_search_followup_ingress():
            return
        voice_client = guild.voice_client
        if not isinstance(voice_client, deps.voice_client_type):
            return
        target_channel = after.channel or voice_client.channel
        if target_channel is None:
            return
        before_channel = getattr(before, "channel", None)
        after_channel = getattr(after, "channel", None)
        channel_changed = bool(
            after_channel is not None
            and getattr(before_channel, "id", None)
            != getattr(after_channel, "id", None)
        )
        target_channel_id = getattr(target_channel, "id", None)
        rearmed_client = None
        last_error: Exception | None = None
        try:
            for attempt in range(_VOICE_REARM_ATTEMPTS):
                if (
                    guild.voice_client is not voice_client
                    or getattr(
                        getattr(voice_client, "channel", None),
                        "id",
                        None,
                    )
                    != target_channel_id
                ):
                    return
                try:
                    rearmed_client = await deps.ensure_listening_voice_client(
                        guild,
                        target_channel,
                        force_listener_reset=channel_changed,
                        expected_voice_client=voice_client,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt + 1 >= _VOICE_REARM_ATTEMPTS:
                        break
                    await asyncio.sleep(_VOICE_REARM_RETRY_DELAY_SEC)
                    continue
                break
            if last_error is not None and rearmed_client is None:
                self._record_runtime_error(
                    "voice_state_rearm_failed",
                    last_error,
                )
                deps.log(
                    f"[VOICE STATE REARM FAIL] guild={guild.id} "
                    f"errorType={type(last_error).__name__}"
                )
                return
            if not (
                isinstance(rearmed_client, deps.voice_client_type)
                and rearmed_client.is_connected()
            ):
                return
            deps.log(
                f"[VOICE STATE REARM] guild={guild.id} "
                f"channel={getattr(target_channel, 'name', None)} "
                f"listening={rearmed_client.is_listening()}"
            )
        except Exception as exc:
            self._record_runtime_error("voice_state_rearm_failed", exc)
            deps.log(
                f"[VOICE STATE REARM FAIL] guild={guild.id} "
                f"errorType={type(exc).__name__}"
            )
        finally:
            if deps.runtime_status is not None:
                deps.runtime_status.write_once()

    async def on_message(self, message: discord.Message) -> None:
        if not await self.admit_search_followup_ingress():
            return
        await handle_discord_text_message(message, self.deps.events.text_message_handler())

    async def join_voice(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        if not await self._admit_guild_mutation(ctx):
            return
        archive_session_open = None
        if self._conversation_archive_enabled:
            if not self._archive_operator_is_authorized(ctx):
                await ctx.send(
                    "이 기록 세션은 등록된 로컬 운영자만 시작할 수 있어."
                )
                return
            try:
                guild = getattr(ctx, "guild", None)
                if guild is None:
                    raise RuntimeError("conversation_shared_session_guild_missing")
                voice_channel = getattr(
                    getattr(getattr(ctx, "author", None), "voice", None),
                    "channel",
                    None,
                )
                if voice_channel is not None:
                    await self._close_shared_session(int(guild.id))
                archive_session_open = self._open_shared_session
            except Exception as exc:
                self._record_runtime_error(
                    "conversation_shared_session_prepare_failed",
                    exc,
                )
                await ctx.send("기록 세션을 안전하게 준비하지 못해 음성 참여를 열지 않았어.")
                return
        await handle_join_voice_command(
            ctx,
            ensure_listening_voice_client=deps.ensure_listening_voice_client,
            archive_session_open=archive_session_open,
            log=deps.log,
        )

    async def rejoin_voice(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        if not await self._admit_guild_mutation(ctx):
            return
        archive_session_open = None
        if self._conversation_archive_enabled:
            if not self._archive_operator_is_authorized(ctx):
                await ctx.send(
                    "이 기록 세션은 등록된 로컬 운영자만 시작할 수 있어."
                )
                return
            try:
                guild = getattr(ctx, "guild", None)
                if guild is None:
                    raise RuntimeError("conversation_shared_session_guild_missing")
                voice_channel = getattr(
                    getattr(getattr(ctx, "author", None), "voice", None),
                    "channel",
                    None,
                )
                if voice_channel is not None:
                    await self._close_shared_session(int(guild.id))
                archive_session_open = self._open_shared_session
            except Exception as exc:
                self._record_runtime_error(
                    "conversation_shared_session_prepare_failed",
                    exc,
                )
                await ctx.send("기록 세션을 안전하게 준비하지 못해 음성 참여를 열지 않았어.")
                return
        await handle_rejoin_voice_command(
            ctx,
            ensure_listening_voice_client=deps.ensure_listening_voice_client,
            archive_session_open=archive_session_open,
            log=deps.log,
        )

    async def leave_voice(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        if self._conversation_archive_enabled:
            guild = getattr(ctx, "guild", None)
            if guild is not None:
                with contextlib.suppress(Exception):
                    await self._close_shared_session(int(guild.id))
        await handle_leave_voice_command(
            ctx,
            mark_manual_disconnect=self.deps.commands.mark_voice_manual_disconnect,
        )

    async def restart_bot_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        await handle_restart_bot_command(
            ctx,
            create_task=deps.create_task,
            restart_bot_process=deps.restart_bot_process,
        )

    async def shutdown_bot_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        await handle_shutdown_bot_command(
            ctx,
            schedule_stack_shutdown=deps.schedule_evelyn_stack_shutdown,
            create_task=deps.create_task,
            shutdown_bot_process=deps.shutdown_bot_process,
        )

    async def control_command_error(self, ctx: Any, error: Any) -> None:
        ctx = self._command_context(ctx)
        await handle_control_command_error(ctx, error)

    async def on_command_error(self, ctx: Any, error: Any) -> None:
        ctx = self._command_context(ctx)
        await handle_discord_command_error(ctx, error)

    async def status_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        await handle_status_command(
            ctx,
            build_reply=deps.build_status_reply,
            model_name=deps.model_name,
            router_model_name=deps.router_model_name,
            summary_model_name=deps.summary_model_name,
            stt_model_name=deps.stt_model_name,
            voice_debug_save_audio=deps.voice_debug_save_audio,
            vad_enabled=deps.vad_enabled,
            vad_provider=deps.vad_provider,
        )

    async def evelyn_page_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        await handle_evelyn_page_command(ctx, resolve_page_url=self.deps.commands.resolve_evelyn_page_url)

    async def set_guild_prefix(self, ctx: Any, new_prefix: str | None = None) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        if new_prefix is not None and not await self._admit_guild_mutation(ctx):
            return
        await handle_prefix_command(
            ctx,
            new_prefix,
            default_prefix=deps.default_command_prefix,
            get_guild_command_prefix=deps.get_guild_command_prefix,
            save_guild_command_prefix=deps.save_guild_command_prefix,
            build_current_reply=deps.build_prefix_current_reply,
            build_reset_reply=deps.build_prefix_reset_reply,
            build_saved_reply=deps.build_prefix_saved_reply,
            guild_only_message=deps.guild_only_message,
        )

    async def autonomy_start_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        if not await self._admit_guild_mutation(ctx):
            return
        guild = getattr(ctx, "guild", None)
        guild_mutation_is_current = None
        if guild is not None:
            guild_id = guild.id
            try:
                admitted_epoch = deps.guild_epoch(guild_id)
            except Exception:
                await ctx.send(AUTONOMY_START_STALE_REPLY)
                return

            def guild_mutation_is_current() -> bool:
                try:
                    return (
                        deps.guild_is_open(guild_id)
                        and deps.guild_epoch(guild_id) == admitted_epoch
                    )
                except Exception:
                    return False

        await handle_autonomy_start_command(
            ctx,
            autonomy_enabled=deps.autonomy_enabled,
            get_or_create_autonomy_engine=deps.get_or_create_autonomy_engine,
            is_minecraft_autonomy_route_enabled=(
                deps.is_minecraft_autonomy_route_enabled
            ),
            enable_minecraft_autonomy_route=(
                deps.enable_minecraft_autonomy_route
            ),
            grant_autonomy_authorization=deps.grant_autonomy_authorization,
            revoke_autonomy_authorization=deps.revoke_autonomy_authorization,
            guild_only_message=deps.guild_only_message,
            guild_mutation_is_current=guild_mutation_is_current,
            record_runtime_error=self._record_runtime_error,
            archive_autonomy_grant=(
                deps.conversation_archive_archive_autonomy_grant
            ),
        )

    async def autonomy_stop_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        await handle_autonomy_stop_command(
            ctx,
            autonomy_engines=deps.autonomy_engines,
            revoke_autonomy_authorization=deps.revoke_autonomy_authorization,
            guild_only_message=deps.guild_only_message,
            record_runtime_error=self._record_runtime_error,
        )

    async def autonomy_status_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        await handle_autonomy_status_command(
            ctx,
            autonomy_engines=deps.autonomy_engines,
            get_routed_autonomy_executor=deps.get_routed_autonomy_executor,
            get_autonomy_authorization_status=(
                deps.get_autonomy_authorization_status
            ),
            build_reply=deps.build_autonomy_status_reply,
            guild_only_message=deps.guild_only_message,
        )

    def mark_text_session_from_command(
        self,
        ctx: Any,
        user_text: str,
        answer_text: str,
        *,
        awaiting_user_reply: bool = False,
        turn_id: str | None = None,
        before_commit: Callable[[int], Any] | None = None,
    ) -> dict[str, Any] | None:
        guild = getattr(ctx, "guild", None)
        if guild is not None:
            try:
                if not self.deps.commands.guild_is_open(guild.id):
                    return None
            except Exception:
                return None
        return mark_text_session_from_command_runtime(
            ctx,
            user_text,
            answer_text,
            awaiting_user_reply=awaiting_user_reply,
            turn_id=turn_id,
            before_commit=before_commit,
            deps=self.deps.commands.command_session(),
        )

    async def minecraft_connect_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        if not await self._admit_guild_mutation(ctx):
            return
        await handle_minecraft_connect_command(
            ctx,
            enable_minecraft_mode=deps.enable_minecraft_mode,
            enable_minecraft_autonomy_route=(
                deps.enable_minecraft_autonomy_route
            ),
            build_reply=deps.build_minecraft_connect_reply,
            guild_only_message=deps.guild_only_message,
            record_runtime_error=self._record_runtime_error,
            archive_minecraft_command=(
                deps.conversation_archive_archive_minecraft_command
            ),
            archive_required=self._conversation_archive_enabled,
            log=deps.log,
        )

    async def minecraft_disconnect_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        await handle_minecraft_disconnect_command(
            ctx,
            disable_minecraft_mode=deps.disable_minecraft_mode,
            disable_minecraft_autonomy_route=(
                deps.disable_minecraft_autonomy_route
            ),
            guild_only_message=deps.guild_only_message,
            record_runtime_error=self._record_runtime_error,
            archive_minecraft_command=(
                deps.conversation_archive_archive_minecraft_command
            ),
            archive_required=self._conversation_archive_enabled,
            log=deps.log,
        )

    async def minecraft_status_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        await handle_minecraft_status_command(
            ctx,
            get_minecraft_client=deps.get_minecraft_client,
            get_minecraft_world_lease_status=(
                deps.get_minecraft_world_lease_status
            ),
            build_reply=deps.build_minecraft_status_reply,
            guild_only_message=deps.guild_only_message,
        )

    async def minecraft_goal_command(self, ctx: Any, *, goal: str | None = None) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        if str(goal or "").strip() and not await self._admit_guild_mutation(ctx):
            return
        await handle_minecraft_goal_command(
            ctx,
            goal=goal,
            set_minecraft_goal=deps.set_minecraft_goal,
            build_missing_reply=deps.build_minecraft_goal_missing_reply,
            build_updated_reply=deps.build_minecraft_goal_updated_reply,
            guild_only_message=deps.guild_only_message,
            record_runtime_error=self._record_runtime_error,
            archive_minecraft_command=(
                deps.conversation_archive_archive_minecraft_command
            ),
            archive_required=self._conversation_archive_enabled,
        )

    async def observe_channel_command(
        self,
        ctx: Any,
        action: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        normalized_action = deps.normalize_channel_setting_action(action)
        if (
            normalized_action not in {"목록", "list"}
            and not await self._admit_guild_mutation(ctx)
        ):
            return
        await handle_channel_setting_command(
            ctx,
            action,
            channel,
            setting_key="observe_channel_ids",
            label="👀 관찰채널",
            add_success="✅ 관찰채널에 {channel.mention} 추가했어. (총 {count}개)",
            remove_success="🗑️ 관찰채널에서 {channel.mention} 뺐어. (총 {count}개)",
            normalize_action=deps.normalize_channel_setting_action,
            get_channel_ids=deps.get_guild_observe_channel_ids,
            add_channel_setting=deps.add_guild_channel_setting,
            remove_channel_setting=deps.remove_guild_channel_setting,
            get_guild_command_prefix=deps.get_guild_command_prefix,
            build_list_reply=deps.build_channel_setting_list_reply,
            build_usage_reply=deps.build_observe_channel_usage,
            guild_only_message=deps.guild_only_message,
        )

    async def command_channel_command(
        self,
        ctx: Any,
        action: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        normalized_action = deps.normalize_channel_setting_action(action)
        if (
            normalized_action not in {"목록", "list"}
            and not await self._admit_guild_mutation(ctx)
        ):
            return
        await handle_channel_setting_command(
            ctx,
            action,
            channel,
            setting_key="command_only_channel_ids",
            label="🧭 명령채널",
            add_success="✅ 명령채널에 {channel.mention} 추가했어. 이제 여기선 명령어만 읽어.",
            remove_success="🗑️ 명령채널에서 {channel.mention} 뺐어. (총 {count}개)",
            normalize_action=deps.normalize_channel_setting_action,
            get_channel_ids=deps.get_guild_command_only_channel_ids,
            add_channel_setting=deps.add_guild_channel_setting,
            remove_channel_setting=deps.remove_guild_channel_setting,
            get_guild_command_prefix=deps.get_guild_command_prefix,
            build_list_reply=deps.build_channel_setting_list_reply,
            build_usage_reply=deps.build_command_channel_usage,
            guild_only_message=deps.guild_only_message,
        )

    async def help_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        prefix = deps.get_guild_command_prefix(ctx.guild.id if ctx.guild else None)
        await ctx.send(
            deps.build_help_command_text(
                prefix=prefix,
                control_authorized=deps.is_control_command_authorized(ctx),
            )
        )

    async def reset_guild_memory(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        await handle_reset_guild_memory_command(
            ctx,
            reset_guild_runtime_state=deps.reset_guild_runtime_state,
            get_guild_command_prefix=deps.get_guild_command_prefix,
            build_reply=deps.build_reset_guild_memory_reply,
            guild_only_message=deps.guild_only_message,
        )

    async def record_view_application_command(
        self,
        interaction: Any,
        *,
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> None:
        deps = self.deps.commands
        await handle_record_view_application_command(
            interaction,
            feature_enabled=deps.conversation_archive_enabled,
            read_self=deps.conversation_archive_read_self,
            create_task=deps.create_task,
            sleep_fn=deps.conversation_archive_sleep,
            started_at=started_at,
            ended_at=ended_at,
        )

    async def record_delete_application_command(
        self,
        interaction: Any,
        *,
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> None:
        deps = self.deps.commands
        await handle_record_delete_application_command(
            interaction,
            feature_enabled=deps.conversation_archive_enabled,
            preview_delete=deps.conversation_archive_preview_delete,
            apply_delete=deps.conversation_archive_apply_delete,
            confirmation_guard=self._record_delete_confirmations,
            create_task=deps.create_task,
            sleep_fn=deps.conversation_archive_sleep,
            started_at=started_at,
            ended_at=ended_at,
        )

    async def record_consent_application_command(
        self,
        interaction: Any,
        *,
        consented: bool,
    ) -> None:
        deps = self.deps.commands
        await handle_record_consent_application_command(
            interaction,
            feature_enabled=deps.conversation_archive_enabled,
            set_consent=deps.conversation_archive_set_consent,
            consented=consented,
            create_task=deps.create_task,
            sleep_fn=deps.conversation_archive_sleep,
        )

    async def feedback_application_command(
        self,
        interaction: Any,
        *,
        source_surface: str,
        category: str,
        correction: str,
        requested_change_scope: str,
    ) -> None:
        deps = self.deps.commands
        await handle_feedback_application_command(
            interaction,
            feature_enabled=deps.conversation_archive_enabled,
            capture_feedback=deps.conversation_archive_capture_feedback,
            source_surface=source_surface,
            category=category,
            correction=correction,
            requested_change_scope=requested_change_scope,
            create_task=deps.create_task,
            sleep_fn=deps.conversation_archive_sleep,
        )

    def register(self, bot: Any) -> DiscordAppBindings:
        commands = self.deps.commands

        # discord.py treats a callable whose qualified name belongs to a class as
        # an unbound Cog method and skips both ``self`` and ``ctx``.  These local
        # adapters keep the original command signatures while the composition
        # continues to own the implementation.
        async def join_voice_callback(ctx: Any) -> None:
            await self.join_voice(ctx)

        async def rejoin_voice_callback(ctx: Any) -> None:
            await self.rejoin_voice(ctx)

        async def leave_voice_callback(ctx: Any) -> None:
            await self.leave_voice(ctx)

        async def restart_bot_command_callback(ctx: Any) -> None:
            await self.restart_bot_command(ctx)

        async def shutdown_bot_command_callback(ctx: Any) -> None:
            await self.shutdown_bot_command(ctx)

        async def status_command_callback(ctx: Any) -> None:
            await self.status_command(ctx)

        async def evelyn_page_command_callback(ctx: Any) -> None:
            await self.evelyn_page_command(ctx)

        async def set_guild_prefix_callback(ctx: Any, new_prefix: str | None = None) -> None:
            await self.set_guild_prefix(ctx, new_prefix)

        async def autonomy_start_command_callback(ctx: Any) -> None:
            await self.autonomy_start_command(ctx)

        async def autonomy_stop_command_callback(ctx: Any) -> None:
            await self.autonomy_stop_command(ctx)

        async def autonomy_status_command_callback(ctx: Any) -> None:
            await self.autonomy_status_command(ctx)

        async def minecraft_connect_command_callback(ctx: Any) -> None:
            await self.minecraft_connect_command(ctx)

        async def minecraft_disconnect_command_callback(ctx: Any) -> None:
            await self.minecraft_disconnect_command(ctx)

        async def minecraft_status_command_callback(ctx: Any) -> None:
            await self.minecraft_status_command(ctx)

        async def minecraft_goal_command_callback(ctx: Any, *, goal: str | None = None) -> None:
            await self.minecraft_goal_command(ctx, goal=goal)

        async def observe_channel_command_callback(
            ctx: Any,
            action: str | None = None,
            channel: discord.TextChannel | None = None,
        ) -> None:
            await self.observe_channel_command(ctx, action, channel)

        async def command_channel_command_callback(
            ctx: Any,
            action: str | None = None,
            channel: discord.TextChannel | None = None,
        ) -> None:
            await self.command_channel_command(ctx, action, channel)

        async def help_command_callback(ctx: Any) -> None:
            await self.help_command(ctx)

        async def reset_guild_memory_callback(ctx: Any) -> None:
            await self.reset_guild_memory(ctx)

        @app_commands.describe(
            시작="조회 시작 시각(ISO, 생략 가능)",
            끝="조회 끝 시각(ISO, 생략 가능)",
        )
        async def record_view_application_callback(
            interaction: discord.Interaction,
            시작: str | None = None,
            끝: str | None = None,
        ) -> None:
            await self.record_view_application_command(
                interaction,
                started_at=시작,
                ended_at=끝,
            )

        @app_commands.describe(
            시작="삭제 시작 시각(ISO, 기간 전체면 생략)",
            끝="삭제 끝 시각(ISO, 기간 전체면 생략)",
        )
        async def record_delete_application_callback(
            interaction: discord.Interaction,
            시작: str | None = None,
            끝: str | None = None,
        ) -> None:
            await self.record_delete_application_command(
                interaction,
                started_at=시작,
                ended_at=끝,
            )

        async def record_consent_application_callback(
            interaction: discord.Interaction,
        ) -> None:
            await self.record_consent_application_command(
                interaction,
                consented=True,
            )

        async def record_withdraw_application_callback(
            interaction: discord.Interaction,
        ) -> None:
            await self.record_consent_application_command(
                interaction,
                consented=False,
            )

        @app_commands.describe(
            출처="교정할 최신 본인 답변의 출처",
            분류="사용자가 직접 고르는 피드백 분류",
            교정="검토할 교정 내용(최대 4,000자)",
            변경범위="도구·권한 같은 설계 변경 요구가 있으면 선택",
        )
        @app_commands.choices(
            출처=[
                app_commands.Choice(name="채팅 답변", value="discord"),
                app_commands.Choice(name="음성 답변", value="voice"),
            ],
            분류=[
                app_commands.Choice(name="답변 품질", value="answer_quality"),
                app_commands.Choice(name="문맥 선택", value="context_selection"),
                app_commands.Choice(name="작업 라우팅", value="task_routing"),
                app_commands.Choice(name="말투·정체성", value="tone_identity"),
                app_commands.Choice(name="도구 실패", value="tool_failure"),
                app_commands.Choice(name="권한·안전", value="permission_safety"),
            ],
            변경범위=[
                app_commands.Choice(name="없음", value="none"),
                app_commands.Choice(name="평가기", value="evaluator"),
                app_commands.Choice(name="도구", value="tool"),
                app_commands.Choice(name="승인 정책", value="approval"),
                app_commands.Choice(name="프로덕션 소스", value="source"),
            ],
        )
        async def feedback_application_callback(
            interaction: discord.Interaction,
            출처: app_commands.Choice[str],
            분류: app_commands.Choice[str],
            교정: str,
            변경범위: app_commands.Choice[str] | None = None,
        ) -> None:
            await self.feedback_application_command(
                interaction,
                source_surface=출처.value,
                category=분류.value,
                correction=교정,
                requested_change_scope=(
                    "none" if 변경범위 is None else 변경범위.value
                ),
            )

        on_ready = bot.event(self.on_ready)
        on_disconnect = bot.event(self.on_disconnect)
        on_voice_state_update = bot.event(self.on_voice_state_update)
        on_message = bot.event(self.on_message)
        bot.event(self.on_command_error)

        join_voice = bot.command(name="들어와", aliases=["join"])(join_voice_callback)
        rejoin_voice = bot.command(name="다시들어와", aliases=["rejoin"])(rejoin_voice_callback)
        leave_voice = bot.command(name="나가", aliases=["leave"])(leave_voice_callback)

        restart_bot_command = bot.command(name="재시작", aliases=["restart"])(restart_bot_command_callback)
        restart_bot_command.add_check(commands.is_control_command_authorized)
        restart_bot_command.error(self.control_command_error)

        shutdown_bot_command = bot.command(name="종료", aliases=["shutdown", "quit", "exit"])(
            shutdown_bot_command_callback
        )
        shutdown_bot_command.add_check(commands.is_control_command_authorized)
        shutdown_bot_command.error(self.control_command_error)

        status_command = bot.command(name="상태", aliases=["status"])(status_command_callback)
        evelyn_page_command = bot.command(
            name="이블린페이지",
            aliases=["page", "homepage", "website", "landing"],
        )(evelyn_page_command_callback)

        set_guild_prefix = bot.command(name="접두사", aliases=["prefix"])(set_guild_prefix_callback)
        set_guild_prefix.add_check(commands.is_control_command_authorized)
        set_guild_prefix.error(self.control_command_error)

        autonomy_start_command = bot.command(name="자율시작", aliases=["autonomy-on"])(
            autonomy_start_command_callback
        )
        autonomy_start_command.add_check(commands.is_control_command_authorized)
        autonomy_start_command.error(self.control_command_error)
        autonomy_stop_command = bot.command(name="자율정지", aliases=["autonomy-off"])(
            autonomy_stop_command_callback
        )
        autonomy_stop_command.add_check(commands.is_control_command_authorized)
        autonomy_stop_command.error(self.control_command_error)
        autonomy_status_command = bot.command(name="자율상태", aliases=["autonomy-status"])(
            autonomy_status_command_callback
        )

        minecraft_connect_command = bot.command(
            name="마크접속",
            aliases=["mc-connect", "minecraft-connect"],
            ignore_extra=False,
        )(minecraft_connect_command_callback)
        minecraft_connect_command.add_check(commands.is_control_command_authorized)
        minecraft_connect_command.error(self.control_command_error)
        minecraft_disconnect_command = bot.command(
            name="마크종료",
            aliases=["mc-disconnect", "minecraft-disconnect"],
            ignore_extra=False,
        )(minecraft_disconnect_command_callback)
        minecraft_disconnect_command.add_check(commands.is_control_command_authorized)
        minecraft_disconnect_command.error(self.control_command_error)
        minecraft_status_command = bot.command(
            name="마크상태",
            aliases=["mc-status", "minecraft-status"],
            ignore_extra=False,
        )(minecraft_status_command_callback)
        minecraft_status_command.error(self.control_command_error)
        minecraft_goal_command = bot.command(
            name="마크목표",
            aliases=["mc-goal", "minecraft-goal"],
        )(minecraft_goal_command_callback)
        minecraft_goal_command.add_check(commands.is_control_command_authorized)
        minecraft_goal_command.error(self.control_command_error)

        observe_channel_command = bot.command(name="관찰채널", aliases=["observe-channel"])(
            observe_channel_command_callback
        )
        observe_channel_command.add_check(commands.is_control_command_authorized)
        observe_channel_command.error(self.control_command_error)

        command_channel_command = bot.command(name="명령채널", aliases=["command-channel"])(
            command_channel_command_callback
        )
        command_channel_command.add_check(commands.is_control_command_authorized)
        command_channel_command.error(self.control_command_error)

        help_command = bot.command(name="도움말", aliases=["help"])(help_command_callback)
        reset_guild_memory = bot.command(name="초기화", aliases=["reset"])(reset_guild_memory_callback)
        reset_guild_memory.add_check(commands.is_control_command_authorized)
        reset_guild_memory.error(self.control_command_error)

        record_view_application_command = None
        record_delete_application_command = None
        record_consent_application_command = None
        record_withdraw_application_command = None
        feedback_application_command = None
        if commands.conversation_archive_enabled is True:
            guild_context = app_commands.AppCommandContext(
                guild=True,
                dm_channel=False,
                private_channel=False,
            )
            guild_install = app_commands.AppInstallationType(
                guild=True,
                user=False,
            )
            record_view_application_command = app_commands.Command(
                name="기록열람",
                description="내가 작성하거나 말한 이블린 기록만 열람합니다.",
                callback=record_view_application_callback,
                allowed_contexts=guild_context,
                allowed_installs=guild_install,
            )
            record_delete_application_command = app_commands.Command(
                name="기록삭제",
                description="내 기록 삭제를 미리 보거나 단회 확인합니다.",
                callback=record_delete_application_callback,
                allowed_contexts=guild_context,
                allowed_installs=guild_install,
            )
            record_consent_application_command = app_commands.Command(
                name="기록동의",
                description="현재 음성 채널의 기록 안내에 동의합니다.",
                callback=record_consent_application_callback,
                allowed_contexts=guild_context,
                allowed_installs=guild_install,
            )
            record_withdraw_application_command = app_commands.Command(
                name="기록철회",
                description="현재 음성 채널의 기록 동의를 철회합니다.",
                callback=record_withdraw_application_callback,
                allowed_contexts=guild_context,
                allowed_installs=guild_install,
            )
            feedback_application_command = app_commands.Command(
                name="피드백제출",
                description="내 최신 채팅·음성 답변에 검토 전용 피드백을 남깁니다.",
                callback=feedback_application_callback,
                allowed_contexts=guild_context,
                allowed_installs=guild_install,
            )
            for application_command in (
                record_view_application_command,
                record_delete_application_command,
                record_consent_application_command,
                record_withdraw_application_command,
                feedback_application_command,
            ):
                bot.tree.add_command(application_command)
            self._conversation_archive_command_bot = bot
            self._conversation_archive_application_commands = (
                record_view_application_command,
                record_delete_application_command,
                record_consent_application_command,
                record_withdraw_application_command,
                feedback_application_command,
            )

        return DiscordAppBindings(
            on_ready=on_ready,
            on_disconnect=on_disconnect,
            on_voice_state_update=on_voice_state_update,
            on_message=on_message,
            join_voice=join_voice,
            rejoin_voice=rejoin_voice,
            leave_voice=leave_voice,
            restart_bot_command=restart_bot_command,
            shutdown_bot_command=shutdown_bot_command,
            status_command=status_command,
            evelyn_page_command=evelyn_page_command,
            set_guild_prefix=set_guild_prefix,
            autonomy_start_command=autonomy_start_command,
            autonomy_stop_command=autonomy_stop_command,
            autonomy_status_command=autonomy_status_command,
            minecraft_connect_command=minecraft_connect_command,
            minecraft_disconnect_command=minecraft_disconnect_command,
            minecraft_status_command=minecraft_status_command,
            minecraft_goal_command=minecraft_goal_command,
            observe_channel_command=observe_channel_command,
            command_channel_command=command_channel_command,
            help_command=help_command,
            reset_guild_memory=reset_guild_memory,
            record_view_application_command=record_view_application_command,
            record_delete_application_command=record_delete_application_command,
            record_consent_application_command=record_consent_application_command,
            record_withdraw_application_command=record_withdraw_application_command,
            feedback_application_command=feedback_application_command,
        )
