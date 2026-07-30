from __future__ import annotations

import os
from typing import Any

try:
    from discord.ext import commands
except Exception:  # pragma: no cover
    class _FallbackCheckFailure(Exception):
        """discord.py이 없는 환경에서 체크 실패 예외 대체."""

    class _FallbackCommands:
        CheckFailure = _FallbackCheckFailure

    commands = _FallbackCommands()

from .text import clean_text
from .discord_commands import (
    control_command_check_failure_message,
    is_control_command_authorized_payload,
)
from .minecraft_mode_composition import (
    MINECRAFT_STOPPED_OUTCOME,
    minecraft_stop_confirmed,
)


async def handle_join_voice_command(
    ctx: Any,
    *,
    ensure_listening_voice_client: Any,
    log: Any = print,
) -> None:
    voice_state = getattr(ctx.author, "voice", None)
    if not voice_state or not getattr(voice_state, "channel", None):
        await ctx.send("먼저 음성 채널에 들어가줘.")
        return

    try:
        vc = await ensure_listening_voice_client(ctx.guild, voice_state.channel)
        if vc is None:
            await ctx.send("❌ 음성 연결에 실패했어.")
            return
        await ctx.send(f"🔊 {voice_state.channel.name}에 들어왔어. 이제 듣고 말할게.")
    except Exception as exc:
        log("음성 연결 오류:", repr(exc))
        await ctx.send(f"❌ 음성 연결 실패: {exc}")


async def handle_rejoin_voice_command(
    ctx: Any,
    *,
    ensure_listening_voice_client: Any,
    log: Any = print,
) -> None:
    channel = ctx.author.voice.channel if getattr(ctx.author, "voice", None) else None
    if channel is None:
        await ctx.send("먼저 음성 채널에 들어가줘.")
        return

    vc = getattr(ctx.guild, "voice_client", None)
    if vc is not None:
        try:
            if hasattr(vc, "stop_listening"):
                vc.stop_listening()
        except Exception:
            pass
        await vc.disconnect(force=True)

    try:
        new_vc = await ensure_listening_voice_client(ctx.guild, channel)
        if new_vc is None:
            await ctx.send("❌ 재연결 실패")
            return
        await ctx.send("🔄 다시 붙었어. 이제 계속 들을게.")
    except Exception as exc:
        log("재연결 오류:", repr(exc))
        await ctx.send(f"❌ 재연결 실패: {exc}")


async def handle_leave_voice_command(
    ctx: Any,
    *,
    mark_manual_disconnect: Any,
) -> None:
    vc = getattr(ctx.guild, "voice_client", None)
    if vc is None:
        await ctx.send("이미 나와 있어.")
        return

    try:
        if hasattr(vc, "stop_listening"):
            vc.stop_listening()
    except Exception:
        pass

    mark_manual_disconnect(ctx.guild, reason="leave_command")
    await vc.disconnect()
    await ctx.send("👋 나갔어.")


async def handle_prefix_command(
    ctx: Any,
    new_prefix: str | None,
    *,
    default_prefix: str,
    get_guild_command_prefix: Any,
    save_guild_command_prefix: Any,
    build_current_reply: Any,
    build_reset_reply: Any,
    build_saved_reply: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return

    guild_id = ctx.guild.id
    current_prefix = get_guild_command_prefix(guild_id)

    if not new_prefix:
        await ctx.send(build_current_reply(current_prefix))
        return

    if new_prefix.lower() in {"기본", "default", "reset"}:
        saved_prefix = save_guild_command_prefix(guild_id, default_prefix)
        await ctx.send(build_reset_reply(saved_prefix))
        return

    try:
        saved_prefix = save_guild_command_prefix(guild_id, new_prefix)
    except ValueError as exc:
        await ctx.send(f"❌ {exc}")
        return

    await ctx.send(build_saved_reply(saved_prefix))


async def handle_autonomy_start_command(
    ctx: Any,
    *,
    autonomy_enabled: bool,
    get_or_create_autonomy_engine: Any,
    grant_autonomy_authorization: Any,
    revoke_autonomy_authorization: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    if not autonomy_enabled:
        await ctx.send("자율 행동 기능이 설정에서 비활성화되어 있어.")
        return
    grant = grant_autonomy_authorization(
        ctx.guild.id,
        f"discord_user:{getattr(ctx.author, 'id', '')}",
    )
    if not isinstance(grant, dict) or not grant.get("ok"):
        await ctx.send("❌ 자율 행동 승인을 발급하지 못했어.")
        return
    try:
        await get_or_create_autonomy_engine(ctx.guild.id).start()
        await ctx.send("🤖 자율 행동 루프를 시작했어.")
    except Exception:
        revoke_autonomy_authorization(
            ctx.guild.id,
            reason_code="start_failed",
        )
        await ctx.send("❌ 자율 행동 시작에 실패했고 승인은 폐기했어.")


async def handle_autonomy_stop_command(
    ctx: Any,
    *,
    autonomy_engines: dict[int, Any],
    revoke_autonomy_authorization: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    revoke_autonomy_authorization(
        ctx.guild.id,
        reason_code="explicit_autonomy_stop",
    )
    engine = autonomy_engines.get(ctx.guild.id)
    if engine is None:
        await ctx.send("이미 자율 행동이 꺼져 있어.")
        return
    try:
        await engine.stop()
        await ctx.send("🛑 자율 행동 루프를 멈췄어.")
    except Exception as exc:
        await ctx.send(f"❌ 자율 행동 정지 실패: {exc}")


async def handle_autonomy_status_command(
    ctx: Any,
    *,
    autonomy_engines: dict[int, Any],
    get_routed_autonomy_executor: Any,
    get_autonomy_authorization_status: Any,
    build_reply: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    engine = autonomy_engines.get(ctx.guild.id)
    router = get_routed_autonomy_executor(ctx.guild.id)
    minecraft_enabled = bool(router and router.is_domain_enabled("minecraft"))
    try:
        authorization = get_autonomy_authorization_status()
    except Exception:
        authorization = {
            "state": "unknown",
            "auditReady": None,
        }
    await ctx.send(
        build_reply(
            engine.state if engine is not None else None,
            minecraft_enabled=minecraft_enabled,
            authorization=authorization,
            guild_id=ctx.guild.id,
        )
    )


async def handle_channel_setting_command(
    ctx: Any,
    action: str | None,
    channel: Any,
    *,
    setting_key: str,
    label: str,
    add_success: str,
    remove_success: str,
    normalize_action: Any,
    get_channel_ids: Any,
    add_channel_setting: Any,
    remove_channel_setting: Any,
    get_guild_command_prefix: Any,
    build_list_reply: Any,
    build_usage_reply: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    normalized_action = normalize_action(action)
    current = get_channel_ids(ctx.guild.id)
    if normalized_action in {"목록", "list"}:
        await ctx.send(build_list_reply(label=label, channel_ids=current, resolve_channel=ctx.guild.get_channel))
        return
    if channel is None:
        await ctx.send(build_usage_reply(get_guild_command_prefix(ctx.guild.id)))
        return
    if normalized_action in {"추가", "add"}:
        updated = add_channel_setting(ctx.guild.id, setting_key, channel.id)
        await ctx.send(add_success.format(channel=channel, count=len(updated)))
        return
    if normalized_action in {"제거", "remove", "삭제"}:
        updated = remove_channel_setting(ctx.guild.id, setting_key, channel.id)
        await ctx.send(remove_success.format(channel=channel, count=len(updated)))
        return
    await ctx.send(build_usage_reply(get_guild_command_prefix(ctx.guild.id)))


async def handle_restart_bot_command(
    ctx: Any,
    *,
    create_task: Any,
    restart_bot_process: Any,
) -> None:
    await ctx.send("🔄 봇을 재시작할게. 잠깐만 기다려줘.")
    create_task(restart_bot_process())


async def handle_shutdown_bot_command(
    ctx: Any,
    *,
    schedule_stack_shutdown: Any,
    create_task: Any,
    shutdown_bot_process: Any,
) -> None:
    if schedule_stack_shutdown():
        await ctx.send("Full Evelyn stack shutdown started. Supervisors, bot, LLM, TTS, Voyager, and Evelyn-owned WSL services will stop.")
        return
    await ctx.send("Full-stack shutdown helper failed, so only the bot process is stopping.")
    create_task(shutdown_bot_process())


def resolve_opus_runtime_value() -> Any:
    try:
        from evelyn_voice.client import OPUS_ERROR_TO_SILENCE as opus_runtime_value
    except Exception:
        opus_runtime_value = None
    return opus_runtime_value


async def handle_status_command(
    ctx: Any,
    *,
    build_reply: Any,
    model_name: str,
    router_model_name: str,
    summary_model_name: str,
    stt_model_name: str,
    voice_debug_save_audio: bool,
    vad_enabled: bool,
    vad_provider: str,
    opus_runtime_value: Any = None,
) -> None:
    guild = ctx.guild
    vc = guild.voice_client if guild else None
    voice_channel_name = getattr(getattr(vc, "channel", None), "name", None) or "없음"
    listening = bool(vc and hasattr(vc, "is_listening") and vc.is_listening())
    await ctx.send(
        build_reply(
            model_name=model_name,
            router_model_name=router_model_name,
            summary_model_name=summary_model_name,
            stt_model_name=stt_model_name,
            voice_channel_name=voice_channel_name,
            listening=listening,
            voice_debug_save_audio=voice_debug_save_audio,
            opus_env_state=os.getenv("OPUS_ERROR_TO_SILENCE"),
            opus_runtime_value=resolve_opus_runtime_value() if opus_runtime_value is None else opus_runtime_value,
            vad_enabled=vad_enabled,
            vad_provider=vad_provider,
        )
    )


async def handle_evelyn_page_command(
    ctx: Any,
    *,
    resolve_page_url: Any,
) -> None:
    page_url = resolve_page_url()
    if not page_url:
        await ctx.send("아직 공개 이블린 페이지 URL을 못 찾았어. EVELYN_PAGE_URL을 설정하거나 GitHub Pages 배포를 먼저 붙여줘.")
        return
    await ctx.send(f"이블린 페이지: {page_url}")


async def handle_reset_guild_memory_command(
    ctx: Any,
    *,
    memory_root: Any,
    reset_guild_runtime_state: Any,
    remove_tree: Any,
    get_guild_command_prefix: Any,
    build_reply: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return

    guild_id = ctx.guild.id
    memory_dir = memory_root / f"guild_{guild_id}"
    current_prefix = get_guild_command_prefix(guild_id)

    reset_guild_runtime_state(guild_id)
    if memory_dir.exists():
        remove_tree(memory_dir)

    await ctx.send(build_reply(guild_name=ctx.guild.name, current_prefix=current_prefix))


async def handle_minecraft_connect_command(
    ctx: Any,
    *,
    enable_minecraft_mode: Any,
    build_reply: Any,
    mark_text_session_from_command: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    try:
        observed = await enable_minecraft_mode(
            ctx.guild.id,
            issuer_ref=(
                f"discord_user:{getattr(ctx.author, 'id', '')}"
            ),
            source="discord_command",
        )
        reply_text = build_reply(observed)
        await ctx.send(reply_text)
    except Exception as exc:
        reply_text = f"❌ 마인크래프트 접속 실패: {exc}"
        await ctx.send(reply_text)
    mark_text_session_from_command(ctx, getattr(ctx.message, "content", None) or "마크접속", reply_text)


async def handle_minecraft_disconnect_command(
    ctx: Any,
    *,
    disable_minecraft_mode: Any,
    mark_text_session_from_command: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    try:
        stopped = await disable_minecraft_mode(ctx.guild.id)
        if not (
            isinstance(stopped, dict)
            and stopped.get("outcome_verified") is True
            and stopped.get("outcome_code") == MINECRAFT_STOPPED_OUTCOME
            and minecraft_stop_confirmed(stopped)
        ):
            raise RuntimeError("minecraft_stop_unverified")
        reply_text = "🛑 Voyager 기반 마인크래프트 자율 모드를 중지했어."
        await ctx.send(reply_text)
    except Exception as exc:
        reply_text = f"❌ 마인크래프트 연결 종료 실패: {exc}"
        await ctx.send(reply_text)
    mark_text_session_from_command(ctx, getattr(ctx.message, "content", None) or "마크종료", reply_text)


async def handle_minecraft_status_command(
    ctx: Any,
    *,
    get_minecraft_client: Any,
    get_minecraft_world_lease_status: Any,
    build_reply: Any,
    mark_text_session_from_command: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    client = get_minecraft_client()
    try:
        status = await client.status()
        payload = dict(status) if isinstance(status, dict) else {}
        payload["world_lease"] = (
            get_minecraft_world_lease_status()
        )
        reply_text = build_reply(payload)
        await ctx.send(reply_text)
    except Exception as exc:
        reply_text = f"❌ 마인크래프트 상태 확인 실패: {exc}"
        await ctx.send(reply_text)
    mark_text_session_from_command(ctx, getattr(ctx.message, "content", None) or "마크상태", reply_text)


async def handle_minecraft_goal_command(
    ctx: Any,
    *,
    goal: str | None,
    set_minecraft_goal: Any,
    build_missing_reply: Any,
    build_updated_reply: Any,
    mark_text_session_from_command: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    goal_text = clean_text(str(goal or ""))
    if not goal_text:
        reply_text = build_missing_reply()
        await ctx.send(reply_text)
        mark_text_session_from_command(ctx, getattr(ctx.message, "content", None) or "마크목표", reply_text)
        return
    try:
        status = await set_minecraft_goal(
            ctx.guild.id,
            goal_text,
        )
        reply_text = build_updated_reply(goal_text, status)
        await ctx.send(reply_text)
    except Exception as exc:
        reply_text = f"❌ 마인크래프트 목표 변경 실패: {exc}"
        await ctx.send(reply_text)
    mark_text_session_from_command(ctx, getattr(ctx.message, "content", None) or "마크목표", reply_text)


def make_control_command_authorized_checker(*, allowed_user_ids: set[int] | frozenset[int]) -> Any:
    allowed = set(allowed_user_ids)

    def is_authorized(ctx: Any) -> bool:
        perms = getattr(ctx.author, "guild_permissions", None)
        return is_control_command_authorized_payload(
            author_id=getattr(ctx.author, "id", None),
            is_administrator=bool(perms and getattr(perms, "administrator", False)),
            allowed_user_ids=allowed,
        )

    return is_authorized


async def handle_control_command_error(ctx: Any, error: BaseException) -> None:
    if isinstance(error, commands.CheckFailure):
        await ctx.send(control_command_check_failure_message())
        return
    raise error


__all__ = [
    "handle_autonomy_start_command",
    "handle_autonomy_status_command",
    "handle_autonomy_stop_command",
    "handle_channel_setting_command",
    "handle_evelyn_page_command",
    "handle_join_voice_command",
    "handle_leave_voice_command",
    "handle_minecraft_connect_command",
    "handle_minecraft_disconnect_command",
    "handle_minecraft_goal_command",
    "handle_minecraft_status_command",
    "handle_prefix_command",
    "handle_rejoin_voice_command",
    "handle_control_command_error",
    "handle_reset_guild_memory_command",
    "handle_restart_bot_command",
    "handle_shutdown_bot_command",
    "handle_status_command",
    "make_control_command_authorized_checker",
    "resolve_opus_runtime_value",
]
