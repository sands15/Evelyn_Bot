from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from .discord_voice_connection_runtime import (
    connect_evelyn_voice_client_from_runtime,
    wait_for_internal_voice_reconnect_from_runtime,
)
from .omnivoice_source_runtime import create_omnivoice_source_from_runtime
from .observability_metrics import VoiceLatencyTrace
from .stt_text_runtime import (
    DeferredPartialTranscript,
    build_partial_stt_window_from_runtime,
    choose_full_stt_candidate_from_runtime,
    commit_deferred_partial_transcript_from_runtime,
    commit_stable_transcript_from_runtime,
    detect_wake_word_sync_from_runtime,
    get_partial_transcript_from_runtime,
    longest_common_prefix_text_from_runtime,
    score_stt_candidate_from_runtime,
)
from .stt_transcription_runtime import transcribe_audio16k_from_runtime
from .startup_audio_runtime import warmup_stt_sync_from_runtime
from .tts_warmup_runtime import warmup_tts_server_from_runtime
from .voice_ingress_runtime import set_voice_transition_pending
from .voice_barge_in_continuity import (
    VOICE_BARGE_IN_EVENT_FINISH,
    build_voice_barge_in_continuity_snapshot_from_runtime,
    format_voice_barge_in_continuity_detail_lines_from_runtime,
    format_voice_barge_in_continuity_summary_from_runtime,
    mark_voice_barge_in_continuity_probe_from_runtime,
    parse_barge_in_reason_label_from_runtime,
    reset_voice_barge_in_continuity_probe_from_runtime,
    start_voice_barge_in_continuity_probe_from_runtime,
)
from .voice_timing_runtime import (
    log_voice_bottleneck_summary_from_runtime,
    log_voice_latency_from_runtime,
    log_voice_stage_from_runtime,
    should_log_voice_timing_from_runtime,
)
from .voice_input_lease import (
    acquire_discord_voice_input_lease,
    release_discord_voice_input_lease,
)


TARGET_RATE = 16000
VOICE_LISTENER_REARM_ATTEMPTS = 3
VOICE_LISTENER_REARM_DELAY_SEC = 0.5
DepsFactory = Callable[[], Any]


@dataclass(frozen=True)
class VoiceSupportCompositionDeps:
    continuity: DepsFactory
    stt_warmup: DepsFactory
    tts_warmup: DepsFactory
    timing: DepsFactory
    omnivoice_source: DepsFactory
    stt_transcription: DepsFactory
    stt_text: DepsFactory
    voice_connection: DepsFactory
    set_tts_warmup_started: Callable[[bool], None]
    partial_stt_max_new_tokens: int
    clean_text: Callable[[str], str]
    wake_audio_sec: float
    wake_confirm_audio_sec: float
    wake_max_tokens: int
    wake_confirm_max_tokens: int
    apply_stt_post_corrections: Callable[[str], str]
    strip_leading_voice_fillers: Callable[[str], str]
    extract_leading_wake_alias: Callable[[str], Any]
    fuzzy_leading_wake_alias: Callable[[str], Any]
    looks_like_gibberish_probe: Callable[[str], bool]
    slice_audio_window: Callable[..., Any]
    ensure_startup_components_ready: Callable[..., Any]
    voice_client_type: type
    process_member_audio: Callable[[], Callable[..., Any]]
    cancel_voice_turns_for_guild: Callable[[int], int]
    stop_active_tts_playback: Callable[..., Any]
    is_tts_playback_active: Callable[[int], bool]
    warmup_voice_path: Callable[..., Any]
    save_last_voice_channel_state: Callable[..., None]
    load_last_voice_channel_state: Callable[[], dict[str, Any]]
    increment_voice_pipeline_counter: Callable[..., None]
    voice_pipeline_state: dict[str, Any]
    voice_rejoin_on_ready: bool
    get_guild: Callable[[int], Any]
    voice_channel_type: type
    now: Callable[[], float]
    log: Callable[..., Any]
    acquire_voice_input_lease: Callable[[], Any] | None = None
    release_voice_input_lease: Callable[[str], Any] | None = None


class VoiceSupportComposition:
    """Owns voice continuity, warmup, STT/TTS support, and connection adapters."""

    def __init__(self, deps: VoiceSupportCompositionDeps) -> None:
        self.deps = deps
        self._guild_voice_locks: dict[int, asyncio.Lock] = {}
        self._listener_rearm_tasks: dict[
            tuple[int, int, int], asyncio.Task[Any]
        ] = {}
        self._listener_rearm_generations: dict[tuple[int, int, int], int] = {}
        self._listener_rearm_attempts: dict[tuple[int, int, int], int] = {}

    def _schedule_listener_rearm(
        self,
        guild: Any,
        target_channel: Any,
        voice_client: Any,
        listener_generation: int,
    ) -> None:
        target_channel_id = int(getattr(target_channel, "id", 0) or 0)
        rearm_key = (int(guild.id), target_channel_id, id(voice_client))
        if (
            guild.voice_client is not voice_client
            or getattr(getattr(voice_client, "channel", None), "id", None)
            != target_channel_id
            or getattr(voice_client, "_listener_generation", None)
            != listener_generation
        ):
            return
        self._listener_rearm_generations[rearm_key] = listener_generation
        current = self._listener_rearm_tasks.get(rearm_key)
        if current is not None and not current.done():
            return
        if (
            self._listener_rearm_attempts.get(rearm_key, 0)
            >= VOICE_LISTENER_REARM_ATTEMPTS
        ):
            self.deps.log(
                "[VOICE LISTENER REARM FAIL] "
                f"guild={guild.id} channel={target_channel_id} "
                "errorType=RuntimeError"
            )
            return
        task = asyncio.create_task(
            self._rearm_listener_after_failure(
                guild,
                target_channel,
                voice_client,
                rearm_key,
            ),
            name="discord-voice-listener-rearm",
        )
        self._listener_rearm_tasks[rearm_key] = task

        def consume_result(done: asyncio.Task[Any]) -> None:
            if self._listener_rearm_tasks.get(rearm_key) is done:
                self._listener_rearm_tasks.pop(rearm_key, None)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.deps.log(
                    "[VOICE LISTENER REARM FAIL] "
                    f"errorType={type(exc).__name__}"
                )

        task.add_done_callback(consume_result)

    async def _rearm_listener_after_failure(
        self,
        guild: Any,
        target_channel: Any,
        voice_client: Any,
        rearm_key: tuple[int, int, int],
    ) -> None:
        last_error: Exception | None = None
        target_channel_id = getattr(target_channel, "id", None)
        while (
            self._listener_rearm_attempts.get(rearm_key, 0)
            < VOICE_LISTENER_REARM_ATTEMPTS
        ):
            listener_generation = self._listener_rearm_generations.get(rearm_key)
            if (
                guild.voice_client is not voice_client
                or getattr(getattr(voice_client, "channel", None), "id", None)
                != target_channel_id
                or getattr(voice_client, "_listener_generation", None)
                != listener_generation
                or not voice_client.is_connected()
                or voice_client.is_listener_healthy()
            ):
                return
            self._listener_rearm_attempts[rearm_key] = (
                self._listener_rearm_attempts.get(rearm_key, 0) + 1
            )
            try:
                rearmed = await self.ensure_listening_voice_client(
                    guild,
                    target_channel,
                    force_listener_reset=True,
                    expected_voice_client=voice_client,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
            else:
                # Let freshly-created listener tasks and their done callbacks run
                # before treating the new generation as stable.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                if rearmed is voice_client and voice_client.is_listener_healthy():
                    self._listener_rearm_attempts.pop(rearm_key, None)
                    self._listener_rearm_generations.pop(rearm_key, None)
                    self.deps.log(
                        "[VOICE LISTENER REARM OK] "
                        f"guild={guild.id} channel={target_channel_id}"
                    )
                    return
                latest_generation = self._listener_rearm_generations.get(
                    rearm_key
                )
                if (
                    latest_generation
                    == getattr(voice_client, "_listener_generation", None)
                    and not voice_client.is_listener_healthy()
                ):
                    continue
                return
            latest_generation = self._listener_rearm_generations.get(rearm_key)
            if (
                latest_generation
                != getattr(voice_client, "_listener_generation", None)
            ):
                return
            if (
                self._listener_rearm_attempts.get(rearm_key, 0)
                >= VOICE_LISTENER_REARM_ATTEMPTS
            ):
                break
            await asyncio.sleep(VOICE_LISTENER_REARM_DELAY_SEC)
        self.deps.log(
            "[VOICE LISTENER REARM FAIL] "
            f"guild={guild.id} channel={target_channel_id} "
            f"errorType={type(last_error).__name__ if last_error is not None else 'RuntimeError'}"
        )

    async def _acquire_voice_input_lease(self) -> str:
        acquire = (
            self.deps.acquire_voice_input_lease
            or acquire_discord_voice_input_lease
        )
        token = str(await acquire() or "").strip()
        if not token:
            raise RuntimeError("voice_input_lease_unavailable")
        return token

    async def _release_voice_input_lease(self, token: str) -> None:
        release = (
            self.deps.release_voice_input_lease
            or release_discord_voice_input_lease
        )
        await release(token)

    def parse_barge_in_reason_label(self, raw_reason_code: str) -> str:
        return parse_barge_in_reason_label_from_runtime(raw_reason_code, deps=self.deps.continuity())

    def format_voice_barge_in_continuity_summary(self, continuity: dict[str, Any]) -> str:
        return format_voice_barge_in_continuity_summary_from_runtime(
            continuity,
            deps=self.deps.continuity(),
        )

    def format_voice_barge_in_continuity_detail_lines(self, continuity: dict[str, Any]) -> list[str]:
        return format_voice_barge_in_continuity_detail_lines_from_runtime(
            continuity,
            deps=self.deps.continuity(),
        )

    def start_voice_barge_in_continuity_probe(self, metrics: dict, *, source: str) -> None:
        start_voice_barge_in_continuity_probe_from_runtime(
            metrics,
            source=source,
            deps=self.deps.continuity(),
        )

    def build_voice_barge_in_continuity_snapshot(self) -> dict[str, Any]:
        return build_voice_barge_in_continuity_snapshot_from_runtime(deps=self.deps.continuity())

    def reset_voice_barge_in_continuity_probe(self, *, reason: str = "") -> None:
        reset_voice_barge_in_continuity_probe_from_runtime(
            reason=reason,
            deps=self.deps.continuity(),
        )

    def mark_voice_barge_in_continuity_probe(
        self,
        metrics: dict,
        *,
        success: bool,
        reason: str,
        queued_sentence_count: int = 0,
        reason_code: str | None = None,
        reason_label: str | None = None,
        event: str = VOICE_BARGE_IN_EVENT_FINISH,
    ) -> None:
        mark_voice_barge_in_continuity_probe_from_runtime(
            metrics,
            success=success,
            reason=reason,
            queued_sentence_count=queued_sentence_count,
            reason_code=reason_code,
            reason_label=reason_label,
            event=event,
            deps=self.deps.continuity(),
        )

    def warmup_stt_sync(self) -> None:
        warmup_stt_sync_from_runtime(deps=self.deps.stt_warmup())

    async def warmup_tts_server(self) -> None:
        self.deps.set_tts_warmup_started(True)
        await warmup_tts_server_from_runtime(deps=self.deps.tts_warmup())

    def should_log_voice_timing(self, elapsed_ms: float) -> bool:
        return should_log_voice_timing_from_runtime(elapsed_ms, deps=self.deps.timing())

    def log_voice_latency(self, metrics: dict | None, key: str, label: str) -> None:
        log_voice_latency_from_runtime(metrics, key, label, deps=self.deps.timing())

    def log_voice_stage(
        self,
        metrics: dict | None,
        label: str,
        *,
        extra: str = "",
        key: str | None = None,
    ) -> None:
        log_voice_stage_from_runtime(
            metrics,
            label,
            deps=self.deps.timing(),
            extra=extra,
            key=key,
        )

    def log_voice_bottleneck_summary(
        self,
        metrics: dict | None,
        *,
        label: str,
        extra: str = "",
        event_name: str = "turn_summary",
    ) -> None:
        log_voice_bottleneck_summary_from_runtime(
            metrics,
            deps=self.deps.timing(),
            label=label,
            extra=extra,
            event_name=event_name,
        )

    async def create_omnivoice_source(
        self,
        text: str,
        *,
        on_task_started: Callable[[], None] | None = None,
        on_request_start: Callable[[], None] | None = None,
        on_response_headers: Callable[[], None] | None = None,
        on_first_byte: Callable[[], None] | None = None,
        on_first_frame: Callable[[], None] | None = None,
        on_first_packet_sent: Callable[[], None] | None = None,
        turn_id: str | None = None,
        chunk_index: int | None = None,
        session_key: str | None = None,
        turn_scope: Any | None = None,
        trace_payload: dict[str, Any] | None = None,
        latency_trace: VoiceLatencyTrace | None = None,
    ) -> Any:
        return await create_omnivoice_source_from_runtime(
            text,
            deps=self.deps.omnivoice_source(),
            on_task_started=on_task_started,
            on_request_start=on_request_start,
            on_response_headers=on_response_headers,
            on_first_byte=on_first_byte,
            on_first_frame=on_first_frame,
            on_first_packet_sent=on_first_packet_sent,
            turn_id=turn_id,
            chunk_index=chunk_index,
            session_key=session_key,
            turn_scope=turn_scope,
            trace_payload=trace_payload,
            latency_trace=latency_trace,
        )

    def transcribe_audio16k_sync(
        self,
        audio16k: Any,
        max_new_tokens: int = 256,
        *,
        sampling_rate: int = TARGET_RATE,
        stage: str = "full",
        validation_bound: bool = False,
    ) -> str:
        runtime_kwargs: dict[str, Any] = {
            "deps": self.deps.stt_transcription(),
            "sampling_rate": sampling_rate,
            "stage": stage,
        }
        if validation_bound:
            runtime_kwargs["validation_bound"] = True
        return transcribe_audio16k_from_runtime(
            audio16k,
            max_new_tokens,
            **runtime_kwargs,
        )

    def build_partial_stt_window(self, audio16k: Any, *, sampling_rate: int = TARGET_RATE) -> Any:
        return build_partial_stt_window_from_runtime(audio16k, sampling_rate=sampling_rate)

    def longest_common_prefix_text(self, a: str, b: str) -> str:
        return longest_common_prefix_text_from_runtime(a, b, clean_text=self.deps.clean_text)

    def commit_stable_transcript(self, session_key: str | None, *, new_partial_text: str) -> str:
        return commit_stable_transcript_from_runtime(
            session_key,
            new_partial_text=new_partial_text,
            deps=self.deps.stt_text(),
        )

    def get_partial_transcript(
        self,
        session_key: str | None,
        audio16k: Any,
        *,
        sampling_rate: int = TARGET_RATE,
        validation_bound: bool = False,
        defer_state_writes: bool = False,
    ) -> tuple[str, str] | DeferredPartialTranscript:
        return get_partial_transcript_from_runtime(
            session_key,
            audio16k,
            sampling_rate=sampling_rate,
            max_new_tokens=self.deps.partial_stt_max_new_tokens,
            transcribe_audio16k_sync=self.transcribe_audio16k_sync,
            deps=self.deps.stt_text(),
            validation_bound=validation_bound,
            defer_state_writes=defer_state_writes,
        )

    def commit_deferred_partial_transcript(
        self,
        session_key: str | None,
        candidate: DeferredPartialTranscript,
    ) -> tuple[str, str]:
        return commit_deferred_partial_transcript_from_runtime(
            session_key,
            candidate,
            deps=self.deps.stt_text(),
        )

    def score_stt_candidate(self, text: str, *, wake_probe: str = "") -> float:
        return score_stt_candidate_from_runtime(
            text,
            wake_probe=wake_probe,
            deps=self.deps.stt_text(),
        )

    def choose_full_stt_candidate(
        self,
        primary_text: str,
        rescore_text: str,
        *,
        wake_probe: str = "",
    ) -> tuple[str, dict]:
        return choose_full_stt_candidate_from_runtime(
            primary_text,
            rescore_text,
            wake_probe=wake_probe,
            deps=self.deps.stt_text(),
        )

    def detect_wake_word_sync(
        self,
        audio: Any,
        *,
        sampling_rate: int = TARGET_RATE,
        validation_bound: bool = False,
    ) -> dict[str, str | bool | None]:
        return detect_wake_word_sync_from_runtime(
            audio,
            sampling_rate=sampling_rate,
            wake_audio_sec=self.deps.wake_audio_sec,
            wake_confirm_audio_sec=self.deps.wake_confirm_audio_sec,
            wake_max_tokens=self.deps.wake_max_tokens,
            wake_confirm_max_tokens=self.deps.wake_confirm_max_tokens,
            transcribe_audio16k_sync=self.transcribe_audio16k_sync,
            apply_stt_post_corrections=self.deps.apply_stt_post_corrections,
            strip_leading_voice_fillers=self.deps.strip_leading_voice_fillers,
            extract_leading_wake_alias=self.deps.extract_leading_wake_alias,
            fuzzy_leading_wake_alias=self.deps.fuzzy_leading_wake_alias,
            looks_like_gibberish_probe=self.deps.looks_like_gibberish_probe,
            slice_audio_window=self.deps.slice_audio_window,
            validation_bound=validation_bound,
        )

    async def wait_for_internal_voice_reconnect(self, target_channel: Any) -> Any | None:
        return await wait_for_internal_voice_reconnect_from_runtime(
            target_channel,
            deps=self.deps.voice_connection(),
        )

    async def connect_evelyn_voice_client(self, target_channel: Any) -> Any:
        return await connect_evelyn_voice_client_from_runtime(
            target_channel,
            deps=self.deps.voice_connection(),
            arm_listener=False,
        )

    async def ensure_listening_voice_client(
        self,
        guild: Any,
        target_channel: Any,
        *,
        force_listener_reset: bool = False,
        expected_voice_client: Any | None = None,
    ) -> Any | None:
        lock = self._guild_voice_locks.setdefault(int(guild.id), asyncio.Lock())
        async with lock:
            set_voice_transition_pending(int(guild.id), True)
            try:
                voice_client = await self._ensure_listening_voice_client_locked(
                    guild,
                    target_channel,
                    force_listener_reset=force_listener_reset,
                    expected_voice_client=expected_voice_client,
                )
            finally:
                set_voice_transition_pending(int(guild.id), False)
        if voice_client is None:
            return None
        warmup_key = f"voice:{guild.id}:{getattr(target_channel, 'id', 'unknown')}"
        try:
            await self.deps.warmup_voice_path(reason="voice_connect", key=warmup_key)
        except Exception as exc:
            self.deps.log(
                f"[VOICE PATH WARMUP FAIL] guild={guild.id} "
                f"channel={getattr(target_channel, 'name', None)} err={exc!r}"
            )
        if (
            not voice_client.is_connected()
            or not voice_client.is_listener_healthy()
            or bool(getattr(voice_client, "_evelyn_voice_move_pending", False))
            or guild.voice_client is not voice_client
            or getattr(getattr(voice_client, "channel", None), "id", None)
            != getattr(target_channel, "id", None)
        ):
            return None
        if not force_listener_reset:
            rearm_key = (
                int(guild.id),
                int(getattr(target_channel, "id", 0) or 0),
                id(voice_client),
            )
            self._listener_rearm_attempts.pop(rearm_key, None)
            self._listener_rearm_generations.pop(rearm_key, None)
        return voice_client

    async def _ensure_listening_voice_client_locked(
        self,
        guild: Any,
        target_channel: Any,
        *,
        force_listener_reset: bool,
        expected_voice_client: Any | None,
    ) -> Any | None:
        await self.deps.ensure_startup_components_ready()
        voice_client = guild.voice_client
        target_channel_id = getattr(target_channel, "id", None)

        if expected_voice_client is not None and (
            voice_client is not expected_voice_client
            or getattr(getattr(voice_client, "channel", None), "id", None)
            != target_channel_id
        ):
            return None

        if force_listener_reset and (
            not isinstance(voice_client, self.deps.voice_client_type)
            or getattr(getattr(voice_client, "channel", None), "id", None)
            != target_channel_id
        ):
            return None

        if voice_client is not None and not isinstance(voice_client, self.deps.voice_client_type):
            await voice_client.disconnect(force=True)
            voice_client = None

        if (
            isinstance(voice_client, self.deps.voice_client_type)
            and voice_client.is_internal_voice_reconnect_active()
        ):
            waited_voice_client = await self.wait_for_internal_voice_reconnect(target_channel)
            if waited_voice_client is not None:
                voice_client = waited_voice_client

        if (
            isinstance(voice_client, self.deps.voice_client_type)
            and not voice_client.is_connected()
        ):
            voice_client = None

        if force_listener_reset and (
            voice_client is None
            or guild.voice_client is not voice_client
            or getattr(getattr(voice_client, "channel", None), "id", None)
            != target_channel_id
        ):
            return None

        if expected_voice_client is not None and (
            voice_client is not expected_voice_client
            or guild.voice_client is not voice_client
            or getattr(getattr(voice_client, "channel", None), "id", None)
            != target_channel_id
        ):
            return None

        lease_token = await self._acquire_voice_input_lease()
        pending_client = None
        transitioning = False
        try:
            if voice_client is None:
                voice_client = await self.connect_evelyn_voice_client(target_channel)
                pending_client = voice_client
                transitioning = True
                setattr(voice_client, "_evelyn_voice_move_pending", True)
                if voice_client.is_listener_healthy():
                    voice_client.stop_listening()
            elif force_listener_reset or voice_client.channel != target_channel:
                pending_client = voice_client
                transitioning = True
                setattr(voice_client, "_evelyn_voice_move_pending", True)
                voice_client.stop_listening()
                self.deps.cancel_voice_turns_for_guild(int(guild.id))
                await self.deps.stop_active_tts_playback(
                    int(guild.id),
                    reason="voice_channel_move",
                )
                if self.deps.is_tts_playback_active(int(guild.id)):
                    try:
                        await voice_client.disconnect(force=True)
                    except Exception:
                        pass
                    raise RuntimeError("voice_channel_move_playback_stop_failed") from None
                if force_listener_reset and (
                    guild.voice_client is not voice_client
                    or getattr(getattr(voice_client, "channel", None), "id", None)
                    != target_channel_id
                ):
                    return None
                if not force_listener_reset and voice_client.channel != target_channel:
                    await voice_client.move_to(target_channel)
                    if (
                        getattr(getattr(voice_client, "channel", None), "id", None)
                        != target_channel_id
                    ):
                        try:
                            await voice_client.disconnect(force=True)
                        except Exception:
                            pass
                        raise RuntimeError("voice_channel_move_failed") from None

            if (
                isinstance(voice_client, self.deps.voice_client_type)
                and voice_client.is_connected()
            ):
                if pending_client is None:
                    pending_client = voice_client
                    setattr(voice_client, "_evelyn_voice_move_pending", True)
                voice_client.on_user_audio = self.deps.process_member_audio()
                set_listener_failure_callback = getattr(
                    voice_client,
                    "set_listener_failure_callback",
                    None,
                )
                if not callable(set_listener_failure_callback):
                    raise RuntimeError(
                        "voice_listener_failure_binding_unavailable"
                    )
                set_listener_failure_callback(
                    lambda failed_client, generation: self._schedule_listener_rearm(
                        guild,
                        target_channel,
                        failed_client,
                        generation,
                    )
                )
                if (
                    not voice_client.is_connected()
                    or guild.voice_client is not voice_client
                    or getattr(getattr(voice_client, "channel", None), "id", None)
                    != target_channel_id
                ):
                    return None
                has_lease = getattr(
                    voice_client,
                    "has_voice_input_lease",
                    None,
                )
                if not callable(has_lease):
                    raise RuntimeError("voice_input_lease_binding_unavailable")
                if voice_client.is_listener_healthy() and not has_lease():
                    voice_client.stop_listening()
                if not voice_client.is_listener_healthy():
                    if not transitioning:
                        try:
                            voice_client.stop_listening()
                        except Exception:
                            pass
                    refresh_udp_transport = getattr(
                        voice_client,
                        "refresh_udp_transport_from_base",
                        None,
                    )
                    if callable(refresh_udp_transport):
                        await refresh_udp_transport()
                    bind_lease = getattr(
                        voice_client,
                        "bind_voice_input_lease",
                        None,
                    )
                    if not callable(bind_lease):
                        raise RuntimeError(
                            "voice_input_lease_binding_unavailable"
                        )
                    bind_lease(
                        lease_token,
                        self._release_voice_input_lease,
                    )
                    lease_token = ""
                    try:
                        voice_client.listen()
                    except Exception:
                        voice_client.stop_listening()
                        raise
                    self.deps.log(
                        f"[VOICE LISTEN REARM] guild={guild.id} channel={target_channel.name}"
                    )
                self.deps.save_last_voice_channel_state(
                    guild,
                    target_channel,
                    reason="ensure_listening",
                    manual_disconnect=False,
                )
                return voice_client

            return None
        finally:
            if pending_client is not None:
                try:
                    delattr(pending_client, "_evelyn_voice_move_pending")
                except AttributeError:
                    pass
            if lease_token:
                await self._release_voice_input_lease(lease_token)

    async def ensure_voice_client(self, message: Any) -> Any | None:
        if not message.guild:
            return None

        voice_state = getattr(message.author, "voice", None)
        if not voice_state or not voice_state.channel:
            return None

        return await self.ensure_listening_voice_client(message.guild, voice_state.channel)

    async def restore_last_voice_channel(self, guild: Any | None = None, *, force: bool = False) -> tuple[bool, str]:
        if not self.deps.voice_rejoin_on_ready and not force:
            return False, "rejoin_disabled"
        state = self.deps.load_last_voice_channel_state()
        if not state:
            return False, "no_saved_voice_channel"
        if state.get("manual_disconnect") and not force:
            return False, "manual_disconnect"

        guild_id = int(state.get("guild_id") or 0)
        channel_id = int(state.get("channel_id") or 0)
        if not guild_id or not channel_id:
            return False, "invalid_saved_voice_channel"

        target_guild = guild or self.deps.get_guild(guild_id)
        if target_guild is None or int(target_guild.id) != guild_id:
            return False, "saved_guild_not_available"
        channel = target_guild.get_channel(channel_id)
        if not isinstance(channel, self.deps.voice_channel_type):
            return False, "saved_channel_not_available"

        self.deps.increment_voice_pipeline_counter("voice_rejoin_attempts")
        self.deps.voice_pipeline_state["last_voice_rejoin_at"] = self.deps.now()
        self.deps.voice_pipeline_state["last_voice_rejoin_error"] = None
        self.deps.voice_pipeline_state[
            "last_voice_rejoin_error_type"
        ] = ""
        try:
            voice_client = await self.ensure_listening_voice_client(target_guild, channel)
        except Exception as exc:
            self.deps.increment_voice_pipeline_counter("voice_rejoin_fail")
            self.deps.voice_pipeline_state[
                "last_voice_rejoin_error"
            ] = "voice_rearm_failed"
            self.deps.voice_pipeline_state[
                "last_voice_rejoin_error_type"
            ] = type(exc).__name__
            self.deps.log(
                "[VOICE REJOIN FAIL] "
                f"guild={guild_id} channel={channel_id} "
                f"errorType={type(exc).__name__}"
            )
            return False, "voice_rearm_failed"
        if voice_client is None:
            self.deps.increment_voice_pipeline_counter("voice_rejoin_fail")
            self.deps.voice_pipeline_state["last_voice_rejoin_error"] = "voice_client_none"
            self.deps.voice_pipeline_state[
                "last_voice_rejoin_error_type"
            ] = ""
            return False, "voice_client_none"
        self.deps.increment_voice_pipeline_counter("voice_rejoin_success")
        self.deps.save_last_voice_channel_state(
            target_guild,
            channel,
            reason="restore_last_voice_channel",
            manual_disconnect=False,
        )
        self.deps.log(f"[VOICE REJOIN OK] guild={guild_id} channel={getattr(channel, 'name', None)}")
        return True, getattr(channel, "name", str(channel_id))
