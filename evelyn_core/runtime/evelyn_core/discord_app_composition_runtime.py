from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import discord

if TYPE_CHECKING:
    from .discord_runtime_status import DiscordRuntimeStatus

from .discord_command_handlers import (
    handle_autonomy_start_command,
    handle_autonomy_status_command,
    handle_autonomy_stop_command,
    handle_channel_setting_command,
    handle_control_command_error,
    handle_evelyn_page_command,
    handle_join_voice_command,
    handle_leave_voice_command,
    handle_minecraft_connect_command,
    handle_minecraft_disconnect_command,
    handle_minecraft_goal_command,
    handle_minecraft_status_command,
    handle_prefix_command,
    handle_rejoin_voice_command,
    handle_reset_guild_memory_command,
    handle_restart_bot_command,
    handle_shutdown_bot_command,
    handle_status_command,
)


def build_discord_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.voice_states = True
    intents.members = True
    return intents
from .discord_command_session_runtime import (
    ContinuityRecordingCommandContext,
    mark_text_session_from_command_runtime,
)
from .discord_text_turn import handle_discord_text_message


DepsFactory = Callable[[], Any]


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
    memory_root: Any
    reset_guild_runtime_state: Callable[..., Any]
    remove_tree: Callable[..., Any]
    build_reset_guild_memory_reply: Callable[..., str]
    log: Callable[..., Any]


@dataclass(frozen=True)
class DiscordAppCompositionDeps:
    events: DiscordEventCompositionDeps
    commands: DiscordCommandCompositionDeps


@dataclass(frozen=True)
class DiscordAppBindings:
    on_ready: Any
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


class DiscordAppComposition:
    """Owns Discord gateway events, command callbacks, and explicit registration."""

    def __init__(self, deps: DiscordAppCompositionDeps) -> None:
        self.deps = deps

    def _record_runtime_error(self, code: str, exc: BaseException) -> None:
        runtime_status = self.deps.events.runtime_status
        if runtime_status is not None:
            try:
                runtime_status.record_error(code, exc)
            except Exception:
                pass

    async def _recover_search_followups(self) -> None:
        deps = self.deps.events
        if deps.recover_search_followups is None:
            return
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
        except Exception as exc:
            self._record_runtime_error(
                "search_followup_recovery_failed",
                exc,
            )
            deps.log(
                "[SEARCH] recovery_start_failed "
                f"errorType={type(exc).__name__}"
            )

    def _command_context(self, ctx: Any) -> Any:
        if isinstance(ctx, ContinuityRecordingCommandContext):
            return ctx
        return ContinuityRecordingCommandContext(
            ctx,
            record_reply=self.mark_text_session_from_command,
            log=self.deps.commands.log,
        )

    async def on_ready(self) -> None:
        deps = self.deps.events
        if deps.runtime_status is not None:
            deps.runtime_status.start()
            deps.runtime_status.write_once()
        user = deps.bot_user()
        deps.log(f"로그인 완료: {user}")
        deps.mark_startup_component("discord_gateway", "done", deps.clean_text(str(user or "")))
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
            await deps.ensure_startup_components_ready()
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
                ok, detail = await deps.restore_last_voice_channel(guild)
                if ok:
                    deps.log(f"[VOICE READY REJOIN] guild={guild.id} channel={detail}")
                elif detail not in {"no_saved_voice_channel", "manual_disconnect"}:
                    deps.log(f"[VOICE READY REJOIN SKIP] guild={guild.id} reason={detail}")
            if deps.autonomy_enabled:
                deps.log(
                    f"[AUTONOMY] guild={guild.id} "
                    "available approval_required=true"
                )
        await self._recover_search_followups()

    async def on_voice_state_update(self, member: Any, before: Any, after: Any) -> None:
        deps = self.deps.events
        user = deps.bot_user()
        if user is None or member.id != user.id:
            return
        if deps.runtime_status is not None:
            deps.runtime_status.write_once()
        guild = getattr(member, "guild", None)
        if guild is None:
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
        try:
            rearmed_client = await deps.ensure_listening_voice_client(
                guild,
                target_channel,
                force_listener_reset=channel_changed,
                expected_voice_client=voice_client,
            )
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
            await self._recover_search_followups()
        except Exception as exc:
            self._record_runtime_error("voice_state_rearm_failed", exc)
            deps.log(f"[VOICE STATE REARM FAIL] guild={guild.id} err={exc!r}")
        finally:
            if deps.runtime_status is not None:
                deps.runtime_status.write_once()

    async def on_message(self, message: discord.Message) -> None:
        await handle_discord_text_message(message, self.deps.events.text_message_handler())

    async def join_voice(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        await handle_join_voice_command(
            ctx,
            ensure_listening_voice_client=deps.ensure_listening_voice_client,
            log=deps.log,
        )

    async def rejoin_voice(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        await handle_rejoin_voice_command(
            ctx,
            ensure_listening_voice_client=deps.ensure_listening_voice_client,
            log=deps.log,
        )

    async def leave_voice(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
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
            record_runtime_error=self._record_runtime_error,
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
    ) -> None:
        mark_text_session_from_command_runtime(
            ctx,
            user_text,
            answer_text,
            awaiting_user_reply=awaiting_user_reply,
            deps=self.deps.commands.command_session(),
        )

    async def minecraft_connect_command(self, ctx: Any) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
        await handle_minecraft_connect_command(
            ctx,
            enable_minecraft_mode=deps.enable_minecraft_mode,
            enable_minecraft_autonomy_route=(
                deps.enable_minecraft_autonomy_route
            ),
            build_reply=deps.build_minecraft_connect_reply,
            guild_only_message=deps.guild_only_message,
            record_runtime_error=self._record_runtime_error,
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
        await handle_minecraft_goal_command(
            ctx,
            goal=goal,
            set_minecraft_goal=deps.set_minecraft_goal,
            build_missing_reply=deps.build_minecraft_goal_missing_reply,
            build_updated_reply=deps.build_minecraft_goal_updated_reply,
            guild_only_message=deps.guild_only_message,
        )

    async def observe_channel_command(
        self,
        ctx: Any,
        action: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        ctx = self._command_context(ctx)
        deps = self.deps.commands
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
            memory_root=deps.memory_root,
            reset_guild_runtime_state=deps.reset_guild_runtime_state,
            remove_tree=deps.remove_tree,
            get_guild_command_prefix=deps.get_guild_command_prefix,
            build_reply=deps.build_reset_guild_memory_reply,
            guild_only_message=deps.guild_only_message,
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

        on_ready = bot.event(self.on_ready)
        on_voice_state_update = bot.event(self.on_voice_state_update)
        on_message = bot.event(self.on_message)

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
        )(minecraft_connect_command_callback)
        minecraft_connect_command.add_check(commands.is_control_command_authorized)
        minecraft_connect_command.error(self.control_command_error)
        minecraft_disconnect_command = bot.command(
            name="마크종료",
            aliases=["mc-disconnect", "minecraft-disconnect"],
        )(minecraft_disconnect_command_callback)
        minecraft_disconnect_command.add_check(commands.is_control_command_authorized)
        minecraft_disconnect_command.error(self.control_command_error)
        minecraft_status_command = bot.command(
            name="마크상태",
            aliases=["mc-status", "minecraft-status"],
        )(minecraft_status_command_callback)
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

        return DiscordAppBindings(
            on_ready=on_ready,
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
        )
