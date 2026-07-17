from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .discord_voice_connection_runtime import DiscordVoiceConnectionRuntimeDeps
from .stt_text_runtime import build_stt_text_runtime_deps
from .stt_transcription_runtime import SttTranscriptionRuntimeDeps


@dataclass(frozen=True)
class VoiceInputSupportDependencyCompositionDeps:
    clean_text: Callable[[str], str]
    normalize_voice_text: Callable[[str], str]
    contains_wake_word: Callable[..., bool]
    looks_like_brief_filler_text: Callable[..., bool]
    looks_like_repetitive_noise_text: Callable[..., bool]
    is_similar: Callable[..., bool]
    session_partial_stt_text: MutableMapping[str, str]
    session_committed_stt_text: MutableMapping[str, str]
    partial_stt_cache: MutableMapping[str, Any]
    stt_service_url: str
    stt_service_timeout_sec: float
    stt_service_fallback_local: bool
    stt_language: str
    stt_force_language: bool
    target_rate: int
    normalize_stt_language: Callable[..., str]
    transcribe_via_service: Callable[..., Any]
    get_stt_model: Callable[..., Any]
    as_float32_array: Callable[..., Any]
    resample_audio_float: Callable[..., Any]
    voice_client_type: type[Any]
    voice_connect_locks: MutableMapping[int, Any]
    voice_connect_timeout: float
    voice_connect_retries: int
    voice_connect_retry_delay_sec: float
    process_member_audio: Callable[..., Any]
    sleep: Callable[..., Any]
    log: Callable[..., Any]


class VoiceInputSupportDependencyComposition:
    """Builds STT text/transcription and Discord voice-connection contracts."""

    def __init__(self, deps: VoiceInputSupportDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_stt_text_runtime_deps(self) -> Any:
        deps = self.deps
        return build_stt_text_runtime_deps(
            clean_text=deps.clean_text,
            normalize_voice_text=deps.normalize_voice_text,
            contains_wake_word=deps.contains_wake_word,
            looks_like_brief_filler_text=deps.looks_like_brief_filler_text,
            looks_like_repetitive_noise_text=deps.looks_like_repetitive_noise_text,
            is_similar=deps.is_similar,
            session_partial_stt_text=deps.session_partial_stt_text,
            session_committed_stt_text=deps.session_committed_stt_text,
            partial_stt_cache=deps.partial_stt_cache,
        )

    def build_stt_transcription_runtime_deps(self) -> SttTranscriptionRuntimeDeps:
        deps = self.deps
        return SttTranscriptionRuntimeDeps(
            stt_service_url=deps.stt_service_url,
            stt_service_timeout_sec=deps.stt_service_timeout_sec,
            stt_service_fallback_local=deps.stt_service_fallback_local,
            stt_language=deps.stt_language,
            stt_force_language=deps.stt_force_language,
            target_rate=deps.target_rate,
            normalize_stt_language=deps.normalize_stt_language,
            transcribe_via_service=deps.transcribe_via_service,
            get_stt_model=deps.get_stt_model,
            as_float32_array=deps.as_float32_array,
            resample_audio_float=deps.resample_audio_float,
            clean_text=deps.clean_text,
            log=deps.log,
        )

    def build_discord_voice_connection_runtime_deps(
        self,
    ) -> DiscordVoiceConnectionRuntimeDeps:
        deps = self.deps
        return DiscordVoiceConnectionRuntimeDeps(
            voice_client_type=deps.voice_client_type,
            voice_connect_locks=deps.voice_connect_locks,
            voice_connect_timeout=deps.voice_connect_timeout,
            voice_connect_retries=deps.voice_connect_retries,
            voice_connect_retry_delay_sec=deps.voice_connect_retry_delay_sec,
            process_member_audio=deps.process_member_audio,
            sleep=deps.sleep,
            log=deps.log,
        )


__all__ = [
    "VoiceInputSupportDependencyComposition",
    "VoiceInputSupportDependencyCompositionDeps",
]
