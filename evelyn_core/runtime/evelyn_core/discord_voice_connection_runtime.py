from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class DiscordVoiceConnectionRuntimeDeps:
    voice_client_type: type
    voice_connect_locks: dict[int, Any]
    voice_connect_timeout: float
    voice_connect_retries: int
    voice_connect_retry_delay_sec: float
    process_member_audio: Callable[..., Any]
    sleep: Callable[[float], Awaitable[Any]]
    log: Callable[[str], Any]


async def wait_for_internal_voice_reconnect_from_runtime(
    target_channel: Any,
    *,
    deps: DiscordVoiceConnectionRuntimeDeps,
) -> Any | None:
    existing_vc = target_channel.guild.voice_client
    if not isinstance(existing_vc, deps.voice_client_type):
        return None
    if not existing_vc.is_internal_voice_reconnect_active():
        return None

    resumed = await existing_vc.wait_for_internal_voice_reconnect(
        timeout=max(deps.voice_connect_timeout, 5.0)
    )
    if resumed and existing_vc.channel == target_channel:
        return existing_vc
    refreshed_vc = target_channel.guild.voice_client
    if (
        isinstance(refreshed_vc, deps.voice_client_type)
        and refreshed_vc.is_connected()
        and refreshed_vc.channel == target_channel
    ):
        return refreshed_vc
    return None


async def connect_evelyn_voice_client_from_runtime(
    target_channel: Any,
    *,
    deps: DiscordVoiceConnectionRuntimeDeps,
    arm_listener: bool = False,
) -> Any:
    guild_id = target_channel.guild.id
    lock = deps.voice_connect_locks.setdefault(guild_id, asyncio.Lock())

    async with lock:
        reused_vc = await wait_for_internal_voice_reconnect_from_runtime(target_channel, deps=deps)
        if reused_vc is not None:
            return reused_vc
        connected_vc = target_channel.guild.voice_client
        if (
            isinstance(connected_vc, deps.voice_client_type)
            and connected_vc.is_connected()
            and connected_vc.channel == target_channel
        ):
            return connected_vc
        if (
            isinstance(connected_vc, deps.voice_client_type)
            and not connected_vc.is_connected()
        ):
            try:
                await connected_vc.disconnect(force=True)
            except Exception:
                try:
                    if target_channel.guild.voice_client is connected_vc:
                        connected_vc.cleanup()
                except Exception:
                    pass
            connected_vc = target_channel.guild.voice_client
            if (
                isinstance(connected_vc, deps.voice_client_type)
                and connected_vc.is_connected()
                and connected_vc.channel == target_channel
            ):
                return connected_vc

        last_error: Exception | None = None

        for attempt in range(1, deps.voice_connect_retries + 1):
            try:
                deps.log(
                    f"[VOICE CONNECT] attempt={attempt}/{deps.voice_connect_retries} "
                    f"channel={target_channel.name} timeout={deps.voice_connect_timeout}"
                )
                vc = await target_channel.connect(
                    cls=deps.voice_client_type,
                    timeout=deps.voice_connect_timeout,
                    reconnect=False,
                )
                if not isinstance(vc, deps.voice_client_type):
                    raise RuntimeError(f"unexpected voice client type: {type(vc)!r}")
                vc.on_user_audio = deps.process_member_audio
                if arm_listener and not vc.is_listener_healthy():
                    vc.listen()
                    deps.log(f"[VOICE CONNECT ARM] guild={guild_id} channel={target_channel.name}")
                return vc
            except Exception as exc:
                last_error = exc
                deps.log(
                    f"[VOICE CONNECT FAIL] attempt={attempt}/{deps.voice_connect_retries} "
                    f"channel={target_channel.name} errorType={type(exc).__name__}"
                )

                reused_vc = await wait_for_internal_voice_reconnect_from_runtime(target_channel, deps=deps)
                if reused_vc is not None:
                    return reused_vc

                stale_vc = target_channel.guild.voice_client
                if stale_vc is not None:
                    try:
                        await stale_vc.disconnect(force=True)
                    except Exception:
                        pass

                try:
                    await target_channel.guild.change_voice_state(
                        channel=None,
                        self_deaf=False,
                        self_mute=False,
                    )
                except Exception:
                    pass

                if attempt < deps.voice_connect_retries:
                    await deps.sleep(deps.voice_connect_retry_delay_sec)

        assert last_error is not None
        raise RuntimeError("voice_connect_failed") from None


__all__ = [
    "DiscordVoiceConnectionRuntimeDeps",
    "connect_evelyn_voice_client_from_runtime",
    "wait_for_internal_voice_reconnect_from_runtime",
]
