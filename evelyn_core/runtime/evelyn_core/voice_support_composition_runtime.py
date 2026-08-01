from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .discord_voice_connection_runtime import (
    connect_evelyn_voice_client_from_runtime,
    wait_for_internal_voice_reconnect_from_runtime,
)
from .omnivoice_source_runtime import create_omnivoice_source_from_runtime
from .stt_text_runtime import (
    build_partial_stt_window_from_runtime,
    choose_full_stt_candidate_from_runtime,
    commit_stable_transcript_from_runtime,
    detect_wake_word_sync_from_runtime,
    get_partial_transcript_from_runtime,
    longest_common_prefix_text_from_runtime,
    score_stt_candidate_from_runtime,
)
from .stt_transcription_runtime import transcribe_audio16k_from_runtime
from .startup_audio_runtime import warmup_stt_sync_from_runtime
from .tts_warmup_runtime import warmup_tts_server_from_runtime
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


TARGET_RATE = 16000
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


class VoiceSupportComposition:
    """Owns voice continuity, warmup, STT/TTS support, and connection adapters."""

    def __init__(self, deps: VoiceSupportCompositionDeps) -> None:
        self.deps = deps

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
    ) -> tuple[str, str]:
        return get_partial_transcript_from_runtime(
            session_key,
            audio16k,
            sampling_rate=sampling_rate,
            max_new_tokens=self.deps.partial_stt_max_new_tokens,
            transcribe_audio16k_sync=self.transcribe_audio16k_sync,
            deps=self.deps.stt_text(),
            validation_bound=validation_bound,
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
        )

    async def ensure_listening_voice_client(self, guild: Any, target_channel: Any) -> Any | None:
        await self.deps.ensure_startup_components_ready()
        voice_client = guild.voice_client

        if voice_client is not None and not isinstance(voice_client, self.deps.voice_client_type):
            await voice_client.disconnect(force=True)
            voice_client = None

        if voice_client is None:
            voice_client = await self.connect_evelyn_voice_client(target_channel)
        elif voice_client.is_internal_voice_reconnect_active():
            waited_voice_client = await self.wait_for_internal_voice_reconnect(target_channel)
            if waited_voice_client is not None:
                voice_client = waited_voice_client
        elif voice_client.channel != target_channel:
            await voice_client.move_to(target_channel)

        if isinstance(voice_client, self.deps.voice_client_type):
            voice_client.on_user_audio = self.deps.process_member_audio()
            if not voice_client.is_listener_healthy():
                try:
                    voice_client.stop_listening()
                except Exception:
                    pass
                voice_client.listen()
                self.deps.log(f"[VOICE LISTEN REARM] guild={guild.id} channel={target_channel.name}")
            warmup_key = f"voice:{guild.id}:{getattr(target_channel, 'id', 'unknown')}"
            try:
                await self.deps.warmup_voice_path(reason="voice_connect", key=warmup_key)
            except Exception as exc:
                self.deps.log(
                    f"[VOICE PATH WARMUP FAIL] guild={guild.id} "
                    f"channel={getattr(target_channel, 'name', None)} err={exc!r}"
                )
            self.deps.save_last_voice_channel_state(
                guild,
                target_channel,
                reason="ensure_listening",
                manual_disconnect=False,
            )
            return voice_client

        return None

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
