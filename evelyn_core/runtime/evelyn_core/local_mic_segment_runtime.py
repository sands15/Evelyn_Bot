from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from .discord_session_policy import LocalMicDiscordSuppressionInput, decide_local_mic_discord_suppression


@dataclass(frozen=True)
class LocalMicSegmentRuntimeDeps:
    local_mic_runtime_state: MutableMapping[str, Any]
    normalize_voice_input_mode: Callable[[str | None], str]
    resolve_local_mic_target: Callable[..., Any]
    guilds: Callable[[], list[Any]]
    preferred_user_ids: Callable[[], set[int]]
    local_only_mode: bool
    local_control_voice_member: Callable[[], Any]
    process_member_audio: Callable[[Any, bytes, dict[str, Any]], Awaitable[Any]]
    log: Callable[..., Any] = print
    time: Callable[[], float] = time.time


@dataclass(frozen=True)
class LocalMicDiscordSuppressionRuntimeDeps:
    local_mic_runtime_state: MutableMapping[str, Any]
    local_mic_capture_ready: Callable[[], bool]
    preferred_user_ids: Callable[[], set[int]]
    normalize_voice_input_mode: Callable[[str | None], str]
    should_route_discord_user_to_local_mic: Callable[..., bool]
    suppress_after_segment_sec: float
    time: Callable[[], float] = time.time


@dataclass(frozen=True)
class LocalMicServiceRuntimeDeps:
    local_mic_runtime_state: MutableMapping[str, Any]
    local_mic_enabled: bool
    local_only_mode: bool
    discord_user_ids: Callable[[], set[int]]
    service_factory: Callable[..., Any]
    get_running_loop: Callable[[], Any]
    create_task: Callable[[Awaitable[Any]], Any]
    handle_local_mic_segment: Callable[[bytes, dict[str, Any]], Awaitable[Any]]
    max_silence_ms_provider: Callable[[], int]
    sample_rate: int
    block_ms: int
    start_threshold: float
    continue_threshold: float
    start_consecutive: int
    min_voiced_ms: int
    max_silence_ms: int
    preroll_ms: int
    max_segment_sec: float
    device: str | int | None
    queue_max: int
    vad_filter_enabled: bool
    env_noise_filter_enabled: bool
    waveform_filter_enabled: bool
    log: Callable[..., Any] = print


def local_mic_effective_max_silence_ms_from_runtime(
    *,
    local_tts_playback_snapshot: Callable[[], dict[str, Any]],
    tts_active_max_silence_ms: int,
    default_max_silence_ms: int,
) -> int:
    if local_tts_playback_snapshot().get("active"):
        return tts_active_max_silence_ms
    return default_max_silence_ms


def stop_local_mic_service_from_runtime(
    *,
    current_service: Any,
    local_mic_runtime_state: MutableMapping[str, Any],
) -> Any:
    if current_service is None:
        local_mic_runtime_state["capture_ready"] = False
        return current_service
    try:
        current_service.stop()
    finally:
        local_mic_runtime_state["capture_ready"] = False
    return None


async def ensure_local_mic_service_started_from_runtime(
    *,
    current_service: Any,
    deps: LocalMicServiceRuntimeDeps,
) -> Any:
    if not deps.local_mic_enabled:
        deps.local_mic_runtime_state["capture_ready"] = False
        return current_service
    user_ids = deps.discord_user_ids()
    if not deps.local_only_mode and not user_ids:
        deps.local_mic_runtime_state["capture_ready"] = False
        deps.local_mic_runtime_state["last_error"] = "no_local_mic_user_ids"
        return current_service
    if current_service is not None and getattr(current_service, "capture_ready", False):
        deps.local_mic_runtime_state["capture_ready"] = True
        return current_service

    loop = deps.get_running_loop()

    def _dispatch_local_segment(pcm_bytes: bytes, meta: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(deps.create_task, deps.handle_local_mic_segment(pcm_bytes, meta))

    service = deps.service_factory(
        on_segment=_dispatch_local_segment,
        sample_rate=deps.sample_rate,
        block_ms=deps.block_ms,
        start_threshold=deps.start_threshold,
        continue_threshold=deps.continue_threshold,
        start_consecutive=deps.start_consecutive,
        min_voiced_ms=deps.min_voiced_ms,
        max_silence_ms=deps.max_silence_ms,
        max_silence_ms_provider=deps.max_silence_ms_provider,
        preroll_ms=deps.preroll_ms,
        max_segment_sec=deps.max_segment_sec,
        device=deps.device,
        queue_max=deps.queue_max,
        vad_filter_enabled=deps.vad_filter_enabled,
        env_noise_filter_enabled=deps.env_noise_filter_enabled,
        waveform_filter_enabled=deps.waveform_filter_enabled,
    )
    started = service.start()
    deps.local_mic_runtime_state["capture_ready"] = bool(started and getattr(service, "capture_ready", False))
    deps.local_mic_runtime_state["last_error"] = getattr(service, "last_error", None)
    if deps.local_mic_runtime_state["capture_ready"]:
        deps.log(
            f"[LOCAL MIC] ready user_ids={sorted(user_ids)} sample_rate={deps.sample_rate} device={deps.device or 'default'}"
        )
        return service
    deps.log(f"[LOCAL MIC] unavailable err={getattr(service, 'last_error', None) or 'capture_not_ready'}")
    return current_service


def should_drop_discord_audio_for_local_mic_from_runtime(
    member_id: int | None,
    *,
    source: str | None = None,
    deps: LocalMicDiscordSuppressionRuntimeDeps,
) -> bool:
    input_mode = deps.normalize_voice_input_mode(str(deps.local_mic_runtime_state.get("input_mode") or "auto"))
    deps.local_mic_runtime_state["input_mode"] = input_mode
    capture_ready = deps.local_mic_capture_ready()
    deps.local_mic_runtime_state["capture_ready"] = capture_ready
    local_mic_recent = False
    last_segment_at = deps.local_mic_runtime_state.get("last_segment_at")
    if isinstance(last_segment_at, (int, float)):
        local_mic_recent = (deps.time() - float(last_segment_at)) <= deps.suppress_after_segment_sec
    decision = decide_local_mic_discord_suppression(
        LocalMicDiscordSuppressionInput(
            member_id=member_id,
            source=source,
            input_mode=input_mode,
            capture_ready=capture_ready,
            local_mic_recent=local_mic_recent,
            preferred_user_ids=deps.preferred_user_ids(),
        ),
        normalize_voice_input_mode=deps.normalize_voice_input_mode,
        should_route_discord_user_to_local_mic=deps.should_route_discord_user_to_local_mic,
    )
    deps.local_mic_runtime_state["input_mode"] = decision.normalized_input_mode
    deps.local_mic_runtime_state["discord_suppression_active"] = decision.suppress
    return decision.suppress


async def handle_local_mic_segment_from_runtime(
    pcm_bytes: bytes,
    debug_meta: dict[str, Any] | None = None,
    *,
    deps: LocalMicSegmentRuntimeDeps,
) -> None:
    if not pcm_bytes:
        return
    if deps.normalize_voice_input_mode(str(deps.local_mic_runtime_state.get("input_mode") or "auto")) == "discord":
        return
    deps.local_mic_runtime_state["segment_count"] = int(deps.local_mic_runtime_state.get("segment_count") or 0) + 1
    deps.local_mic_runtime_state["last_segment_at"] = deps.time()
    if isinstance(debug_meta, dict):
        deps.local_mic_runtime_state["last_segment_duration_sec"] = debug_meta.get("duration_sec")
        deps.local_mic_runtime_state["last_filter"] = debug_meta.get("voice_filter")
    target = deps.resolve_local_mic_target(
        guilds=deps.guilds(),
        preferred_user_ids=deps.preferred_user_ids(),
    )
    routed_meta = dict(debug_meta or {})
    routed_meta["source"] = "local_mic"
    if target is None and deps.local_only_mode:
        member = deps.local_control_voice_member()
        deps.local_mic_runtime_state["last_error"] = None
        routed_meta["routed_local_control"] = True
        routed_meta["routed_discord_user_id"] = int(getattr(member, "id", 0) or 0)
        deps.log(
            f"[LOCAL MIC] segment routed=local_control user_id={member.id} "
            f"duration={routed_meta.get('duration_sec')}"
        )
        await deps.process_member_audio(member, pcm_bytes, routed_meta)
        return
    if target is None:
        deps.local_mic_runtime_state["last_error"] = "no_active_discord_target_for_local_mic"
        return
    deps.local_mic_runtime_state["last_error"] = None
    routed_meta["routed_discord_user_id"] = int(getattr(target.member, "id", 0) or 0)
    voice_client = getattr(getattr(target.member, "guild", None), "voice_client", None)
    listener_binding = getattr(voice_client, "listener_binding", None)
    if callable(listener_binding):
        routed_meta["_voice_listener_binding"] = listener_binding()
    await deps.process_member_audio(target.member, pcm_bytes, routed_meta)
