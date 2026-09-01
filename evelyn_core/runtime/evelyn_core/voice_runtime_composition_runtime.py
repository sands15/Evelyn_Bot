from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import discord
import numpy as np

from .local_control_voice_runtime import (
    LocalControlVoiceMember,
    build_local_control_voice_member_from_runtime,
    is_local_speaker_voice_client_from_runtime,
)
from .local_mic_segment_runtime import (
    LocalMicDiscordSuppressionRuntimeDeps,
    LocalMicSegmentRuntimeDeps,
    LocalMicServiceRuntimeDeps,
    ensure_local_mic_service_started_from_runtime,
    handle_local_mic_segment_from_runtime,
    local_mic_effective_max_silence_ms_from_runtime,
    should_drop_discord_audio_for_local_mic_from_runtime,
    stop_local_mic_service_from_runtime,
)
from .local_mic_state import (
    build_local_mic_runtime_state,
    local_mic_status_line_from_payload,
    serialize_local_mic_runtime_state_payload,
    set_voice_input_mode_state,
    voice_input_mode_status_line_from_mode,
)
from .stt_task_runtime import run_blocking_stt_task_from_runtime
from .voice_debug_audio import (
    debug_write_worker_from_runtime,
    enqueue_voice_debug_audio_from_runtime,
    ensure_debug_write_worker_started_from_runtime,
    save_voice_debug_audio_now,
)
from .voice_pipeline_state import (
    build_voice_pipeline_snapshot_payload,
    default_voice_pipeline_counters,
    default_voice_pipeline_state,
    increment_voice_counter,
    load_last_voice_channel_state,
    mark_last_voice_manual_disconnect,
    record_voice_pipeline_failure_from_runtime,
    save_last_voice_channel_state_from_runtime,
    voice_last_channel_state_path,
)


@dataclass(frozen=True)
class VoicePipelineCompositionDeps:
    project_root: Path
    last_channel_state_file: str
    summarize_p95_metrics: Callable[[], dict[str, float | int]]
    merge_log_event_payload: Callable[..., dict[str, Any]]
    log_turn_event: Callable[..., Any]
    local_only_mode: bool
    local_tts_enabled: Callable[[], bool]
    local_tts_snapshot: Callable[[], dict[str, Any]]
    voice_ingress_queue_depth: Callable[[], int]
    voice_ingress_queue_max: int
    live_recent_sec: float
    utterance_assembly_enabled: Callable[[], bool]
    utterance_pending_count: Callable[[], int]
    utterance_commit_wait_sec: Callable[[], float]
    barge_in_continuity: Callable[[], dict[str, Any]]
    summarize_turn_path_metrics: Callable[[], Any]
    stt_cooldown_after_timeout_sec: float
    monotonic: Callable[[], float]
    time: Callable[[], float]
    log: Callable[..., Any]


@dataclass(frozen=True)
class VoiceDebugCompositionDeps:
    project_root: Path
    configured_dir: str
    max_files_per_guild: int
    max_age_days: float
    max_total_bytes_per_guild: int
    preserve_newest: int
    raw_channels: int
    raw_rate: int
    stt_rate: int
    enabled: bool
    queue_max: int
    create_task: Callable[[Awaitable[Any]], Any]
    to_thread: Callable[..., Awaitable[Any]]
    log: Callable[..., Any]


@dataclass(frozen=True)
class LocalMicCompositionDeps:
    enabled: bool
    input_mode: str
    discord_user_ids: Callable[[], set[int]]
    local_control_guild_id: int
    local_control_guild_name: str
    local_mic_user_name: str
    normalize_voice_input_mode: Callable[[str | None], str]
    resolve_local_mic_target: Callable[..., Any]
    should_route_discord_user_to_local_mic: Callable[..., bool]
    guilds: Callable[[], list[Any]]
    process_member_audio: Callable[[], Callable[[Any, bytes, dict[str, Any]], Awaitable[Any]]]
    local_only_mode: bool
    service_factory: Callable[..., Any]
    get_running_loop: Callable[[], Any]
    create_task: Callable[[Awaitable[Any]], Any]
    local_tts_playback_snapshot: Callable[[], dict[str, Any]]
    tts_active_max_silence_ms: int
    max_silence_ms: int
    discord_suppress_after_segment_sec: float
    sample_rate: int
    block_ms: int
    start_threshold: float
    continue_threshold: float
    start_consecutive: int
    min_voiced_ms: int
    preroll_ms: int
    max_segment_sec: float
    device: str | int | None
    queue_max: int
    vad_filter_enabled: bool
    env_noise_filter_enabled: bool
    waveform_filter_enabled: bool
    time: Callable[[], float]
    log: Callable[..., Any]
    conversation_archive_enabled: bool = False


@dataclass(frozen=True)
class VoiceRuntimeCompositionDeps:
    pipeline: VoicePipelineCompositionDeps
    debug: VoiceDebugCompositionDeps
    local_mic: LocalMicCompositionDeps


class VoiceRuntimeComposition:
    """Owns mutable voice pipeline, debug writer, and local microphone runtime state."""

    def __init__(self, deps: VoiceRuntimeCompositionDeps) -> None:
        self.deps = deps
        self.voice_pipeline_counters = default_voice_pipeline_counters()
        self.voice_pipeline_state = default_voice_pipeline_state()
        self.stt_inference_lock: asyncio.Lock | None = None
        self.stt_cooldown_until = 0.0

        self.voice_debug_counts: dict[int, int] = {}
        self.voice_debug_stems: dict[tuple[int, str, str, str], str] = {}
        self.debug_write_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=max(8, deps.debug.queue_max)
        )
        self.debug_write_task: asyncio.Task | None = None

        self.local_mic_service: Any = None
        self.local_mic_runtime_state: dict[str, Any] = build_local_mic_runtime_state(
            enabled=deps.local_mic.enabled,
            input_mode=deps.local_mic.input_mode,
            routed_user_ids=deps.local_mic.discord_user_ids(),
        )

    def increment_voice_pipeline_counter(self, name: str, amount: int = 1) -> None:
        increment_voice_counter(self.voice_pipeline_counters, name, amount)

    def get_stt_inference_lock(self) -> asyncio.Lock:
        if self.stt_inference_lock is None:
            self.stt_inference_lock = asyncio.Lock()
        return self.stt_inference_lock

    def voice_last_channel_state_path(self) -> Path:
        deps = self.deps.pipeline
        return voice_last_channel_state_path(deps.project_root, deps.last_channel_state_file)

    def load_last_voice_channel_state(self) -> dict[str, Any]:
        deps = self.deps.pipeline
        return load_last_voice_channel_state(deps.project_root, deps.last_channel_state_file)

    def save_last_voice_channel_state(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel,
        *,
        reason: str,
        manual_disconnect: bool = False,
    ) -> None:
        deps = self.deps.pipeline
        save_last_voice_channel_state_from_runtime(
            deps.project_root,
            deps.last_channel_state_file,
            self.voice_pipeline_state,
            guild,
            channel,
            reason=reason,
            manual_disconnect=manual_disconnect,
            log=deps.log,
        )

    def mark_voice_manual_disconnect(self, guild: discord.Guild | None, *, reason: str) -> None:
        deps = self.deps.pipeline
        try:
            mark_last_voice_manual_disconnect(
                deps.project_root,
                deps.last_channel_state_file,
                self.voice_pipeline_state,
                guild,
                reason=reason,
            )
        except Exception as exc:
            deps.log(f"[VOICE STATE SAVE FAIL] err={exc!r}")

    def record_voice_pipeline_failure(
        self,
        kind: str,
        err: BaseException | str,
        metrics: dict | None = None,
        **extra: Any,
    ) -> None:
        deps = self.deps.pipeline
        record_voice_pipeline_failure_from_runtime(
            self.voice_pipeline_counters,
            self.voice_pipeline_state,
            kind,
            err,
            merge_log_event_payload=deps.merge_log_event_payload,
            log_turn_event=deps.log_turn_event,
            metrics=metrics,
            **extra,
        )

    def build_voice_pipeline_snapshot(self, guild: discord.Guild | None = None) -> dict[str, Any]:
        _ = guild
        deps = self.deps.pipeline
        lock = self.stt_inference_lock
        output_mode = (
            "local_speaker"
            if deps.local_only_mode and deps.local_tts_enabled()
            else "discord_voice"
        )
        return build_voice_pipeline_snapshot_payload(
            counters=self.voice_pipeline_counters,
            state=self.voice_pipeline_state,
            p95=deps.summarize_p95_metrics(),
            now_time=deps.time(),
            now_mono=deps.monotonic(),
            stt_lock_locked=bool(lock and lock.locked()),
            stt_cooldown_until=self.stt_cooldown_until,
            last_channel_state=self.load_last_voice_channel_state(),
            output_mode=output_mode,
            local_tts_output=deps.local_tts_snapshot(),
            queue_depth=deps.voice_ingress_queue_depth(),
            queue_max=deps.voice_ingress_queue_max,
            live_recent_sec=deps.live_recent_sec,
            utterance_assembly_enabled=deps.utterance_assembly_enabled(),
            utterance_pending_count=deps.utterance_pending_count(),
            utterance_commit_wait_sec=deps.utterance_commit_wait_sec(),
            barge_in_continuity=deps.barge_in_continuity(),
            turn_path_metrics=deps.summarize_turn_path_metrics(),
        )

    async def run_blocking_stt_task(
        self,
        func: Callable[[], Any],
        *,
        stage: str,
        timeout_sec: float,
        metrics: dict | None = None,
    ) -> Any:
        deps = self.deps.pipeline
        return await run_blocking_stt_task_from_runtime(
            func,
            stage=stage,
            timeout_sec=timeout_sec,
            metrics=metrics,
            get_stt_cooldown_until=lambda: self.stt_cooldown_until,
            set_stt_cooldown_until=self._set_stt_cooldown_until,
            stt_cooldown_after_timeout_sec=deps.stt_cooldown_after_timeout_sec,
            monotonic=deps.monotonic,
            get_stt_inference_lock=self.get_stt_inference_lock,
            increment_voice_pipeline_counter=self.increment_voice_pipeline_counter,
            record_voice_pipeline_failure=self.record_voice_pipeline_failure,
        )

    def _set_stt_cooldown_until(self, value: float) -> None:
        self.stt_cooldown_until = value

    def _save_voice_debug_audio_now(
        self,
        guild_id: int,
        speaker: str,
        pcm_bytes: bytes,
        audio16k: np.ndarray,
        *,
        wake_probe: str | None = None,
        final_text: str | None = None,
        debug_meta: dict | None = None,
        save_stt_audio: bool = True,
        stt_meta: dict | None = None,
        session_key: str | None = None,
        stage_label: str | None = None,
    ) -> None:
        deps = self.deps.debug
        save_voice_debug_audio_now(
            project_root=deps.project_root,
            configured_dir=deps.configured_dir,
            max_files_per_guild=deps.max_files_per_guild,
            max_age_days=deps.max_age_days,
            max_total_bytes_per_guild=deps.max_total_bytes_per_guild,
            preserve_newest=deps.preserve_newest,
            raw_channels=deps.raw_channels,
            raw_rate=deps.raw_rate,
            stt_rate=deps.stt_rate,
            counts=self.voice_debug_counts,
            stems=self.voice_debug_stems,
            log=deps.log,
            guild_id=guild_id,
            speaker=speaker,
            pcm_bytes=pcm_bytes,
            audio16k=audio16k,
            wake_probe=wake_probe,
            final_text=final_text,
            debug_meta=debug_meta,
            save_stt_audio=save_stt_audio,
            stt_meta=stt_meta,
            session_key=session_key,
            stage_label=stage_label,
        )

    async def debug_write_worker(self) -> None:
        deps = self.deps.debug
        await debug_write_worker_from_runtime(
            queue=self.debug_write_queue,
            save_now=self._save_voice_debug_audio_now,
            to_thread=deps.to_thread,
            log=deps.log,
        )

    def ensure_debug_write_worker_started(self) -> None:
        deps = self.deps.debug
        self.debug_write_task = ensure_debug_write_worker_started_from_runtime(
            current_task=self.debug_write_task,
            create_task=deps.create_task,
            worker_coro_factory=self.debug_write_worker,
        )

    def save_voice_debug_audio(
        self,
        guild_id: int,
        speaker: str,
        pcm_bytes: bytes,
        audio16k: np.ndarray,
        *,
        wake_probe: str | None = None,
        final_text: str | None = None,
        debug_meta: dict | None = None,
        save_stt_audio: bool = True,
        stt_meta: dict | None = None,
        session_key: str | None = None,
        stage_label: str | None = None,
    ) -> None:
        if isinstance(debug_meta, dict) and any(
            debug_meta.get(key) not in (None, "")
            for key in (
                "validation_session_id",
                "validation_step_id",
                "validation_attempt",
                "validation_attempt_id",
            )
        ):
            return
        deps = self.deps.debug
        enqueue_voice_debug_audio_from_runtime(
            enabled=deps.enabled,
            ensure_worker_started=self.ensure_debug_write_worker_started,
            queue=self.debug_write_queue,
            log=deps.log,
            guild_id=guild_id,
            speaker=speaker,
            pcm_bytes=pcm_bytes,
            audio16k=audio16k,
            wake_probe=wake_probe,
            final_text=final_text,
            debug_meta=debug_meta,
            save_stt_audio=save_stt_audio,
            stt_meta=stt_meta,
            session_key=session_key,
            stage_label=stage_label,
        )

    def local_control_voice_member(self) -> LocalControlVoiceMember:
        deps = self.deps.local_mic
        return build_local_control_voice_member_from_runtime(
            local_control_guild_id=deps.local_control_guild_id,
            local_control_guild_name=deps.local_control_guild_name,
            local_mic_discord_user_ids=deps.discord_user_ids(),
            local_mic_user_name=deps.local_mic_user_name,
        )

    def is_local_speaker_voice_client(self, vc: Any) -> bool:
        return is_local_speaker_voice_client_from_runtime(vc)

    def stop_local_mic_service(self) -> None:
        self.local_mic_service = stop_local_mic_service_from_runtime(
            current_service=self.local_mic_service,
            local_mic_runtime_state=self.local_mic_runtime_state,
        )

    def build_local_mic_discord_suppression_runtime_deps(
        self,
    ) -> LocalMicDiscordSuppressionRuntimeDeps:
        deps = self.deps.local_mic
        return LocalMicDiscordSuppressionRuntimeDeps(
            local_mic_runtime_state=self.local_mic_runtime_state,
            local_mic_capture_ready=lambda: bool(
                self.local_mic_service and self.local_mic_service.capture_ready
            ),
            preferred_user_ids=deps.discord_user_ids,
            normalize_voice_input_mode=deps.normalize_voice_input_mode,
            should_route_discord_user_to_local_mic=deps.should_route_discord_user_to_local_mic,
            suppress_after_segment_sec=deps.discord_suppress_after_segment_sec,
            time=deps.time,
        )

    def should_drop_discord_audio_for_local_mic(
        self,
        member_id: int | None,
        *,
        source: str | None = None,
    ) -> bool:
        return should_drop_discord_audio_for_local_mic_from_runtime(
            member_id,
            source=source,
            deps=self.build_local_mic_discord_suppression_runtime_deps(),
        )

    def set_voice_input_mode(self, mode: str | None) -> str:
        return set_voice_input_mode_state(self.local_mic_runtime_state, mode)

    def voice_input_mode_status_line(self) -> str:
        return voice_input_mode_status_line_from_mode(
            str(self.local_mic_runtime_state.get("input_mode") or "auto")
        )

    def serialize_local_mic_runtime_state(self) -> dict[str, Any]:
        deps = self.deps.local_mic
        return serialize_local_mic_runtime_state_payload(
            self.local_mic_runtime_state,
            service=self.local_mic_service,
            max_silence_ms=deps.max_silence_ms,
            vad_filter_enabled=deps.vad_filter_enabled,
            env_noise_filter_enabled=deps.env_noise_filter_enabled,
            waveform_filter_enabled=deps.waveform_filter_enabled,
            discord_suppress_after_segment_sec=deps.discord_suppress_after_segment_sec,
            device=deps.device,
            sample_rate=deps.sample_rate,
            start_threshold=deps.start_threshold,
            continue_threshold=deps.continue_threshold,
        )

    def local_mic_status_line(self) -> str:
        return local_mic_status_line_from_payload(self.serialize_local_mic_runtime_state())

    def build_local_mic_segment_runtime_deps(self) -> LocalMicSegmentRuntimeDeps:
        deps = self.deps.local_mic
        return LocalMicSegmentRuntimeDeps(
            local_mic_runtime_state=self.local_mic_runtime_state,
            normalize_voice_input_mode=deps.normalize_voice_input_mode,
            resolve_local_mic_target=deps.resolve_local_mic_target,
            guilds=deps.guilds,
            preferred_user_ids=deps.discord_user_ids,
            local_only_mode=deps.local_only_mode,
            local_control_voice_member=self.local_control_voice_member,
            process_member_audio=deps.process_member_audio(),
            conversation_archive_enabled=deps.conversation_archive_enabled,
            log=deps.log,
            time=deps.time,
        )

    async def handle_local_mic_segment(
        self,
        pcm_bytes: bytes,
        debug_meta: dict[str, Any] | None = None,
    ) -> None:
        await handle_local_mic_segment_from_runtime(
            pcm_bytes,
            debug_meta,
            deps=self.build_local_mic_segment_runtime_deps(),
        )

    def build_local_mic_service_runtime_deps(self) -> LocalMicServiceRuntimeDeps:
        deps = self.deps.local_mic
        return LocalMicServiceRuntimeDeps(
            local_mic_runtime_state=self.local_mic_runtime_state,
            local_mic_enabled=deps.enabled,
            local_only_mode=deps.local_only_mode,
            discord_user_ids=deps.discord_user_ids,
            service_factory=deps.service_factory,
            get_running_loop=deps.get_running_loop,
            create_task=deps.create_task,
            handle_local_mic_segment=self.handle_local_mic_segment,
            max_silence_ms_provider=lambda: local_mic_effective_max_silence_ms_from_runtime(
                local_tts_playback_snapshot=deps.local_tts_playback_snapshot,
                tts_active_max_silence_ms=deps.tts_active_max_silence_ms,
                default_max_silence_ms=deps.max_silence_ms,
            ),
            sample_rate=deps.sample_rate,
            block_ms=deps.block_ms,
            start_threshold=deps.start_threshold,
            continue_threshold=deps.continue_threshold,
            start_consecutive=deps.start_consecutive,
            min_voiced_ms=deps.min_voiced_ms,
            max_silence_ms=deps.max_silence_ms,
            preroll_ms=deps.preroll_ms,
            max_segment_sec=deps.max_segment_sec,
            device=deps.device,
            queue_max=deps.queue_max,
            vad_filter_enabled=deps.vad_filter_enabled,
            env_noise_filter_enabled=deps.env_noise_filter_enabled,
            waveform_filter_enabled=deps.waveform_filter_enabled,
            conversation_archive_enabled=deps.conversation_archive_enabled,
            log=deps.log,
        )

    async def ensure_local_mic_service_started(self) -> None:
        self.local_mic_service = await ensure_local_mic_service_started_from_runtime(
            current_service=self.local_mic_service,
            deps=self.build_local_mic_service_runtime_deps(),
        )
