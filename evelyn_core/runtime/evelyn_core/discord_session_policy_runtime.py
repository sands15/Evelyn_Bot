from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from evelyn_core.discord_session_policy import (
    is_tail_fragment_candidate_policy,
    is_transport_corrupted_audio_policy,
    is_short_followup_candidate_policy,
    should_ignore_short_transcription_policy,
    should_skip_full_stt_after_wake_probe_policy,
    should_require_confirm_exact_for_wake_policy,
)


@dataclass(frozen=True)
class DiscordSessionPolicyRuntimeDeps:
    session_last_turn_accepted_at_get: Callable[[str], float]
    monotonic_fn: Callable[[], float]
    should_require_confirm_exact_for_wake_payload: Callable[[dict[str, Any] | None], bool]
    is_transport_corrupted_audio_payload: Callable[[dict[str, Any] | None], bool]
    no_wake_max_continue_sec: float
    clean_text: Callable[[str], str]
    looks_like_brief_filler_text: Callable[[str], bool]
    looks_like_repetitive_noise_text: Callable[[str], bool]
    tail_fragment_window_sec: float
    tail_fragment_max_raw_sec: float
    tail_fragment_max_voiced_ms: float
    tail_fragment_max_longest_ms: float
    normalize_voice_text: Callable[[str], str]
    normalized_wake_words: Callable[[], set[str]]
    min_audio_sec: float
    min_transcribed_len: int
    wake_short_text_keep_len: int
    audio_duration_fn: Callable[[bytes], float] = lambda pcm_bytes: len(pcm_bytes or b"") / 2


def should_require_confirm_exact_for_wake_from_runtime(
    *,
    debug_meta: dict[str, Any] | None,
    deps: DiscordSessionPolicyRuntimeDeps,
) -> bool:
    return deps.should_require_confirm_exact_for_wake_payload(debug_meta)


def is_transport_corrupted_audio_from_runtime(
    *,
    debug_meta: dict[str, Any] | None,
    deps: DiscordSessionPolicyRuntimeDeps,
) -> bool:
    return deps.is_transport_corrupted_audio_payload(debug_meta)


def is_tail_fragment_candidate_from_runtime(
    *,
    session_key: str | None,
    raw_seconds: float,
    voiced_ms: float,
    longest_voiced_ms: float,
    unstable: bool,
    deps: DiscordSessionPolicyRuntimeDeps,
) -> bool:
    if not session_key:
        return False

    accepted_at = deps.session_last_turn_accepted_at_get(session_key)
    if accepted_at <= 0.0:
        return False

    return is_tail_fragment_candidate_policy(
        has_session_key=True,
        accepted_age_sec=deps.monotonic_fn() - accepted_at,
        raw_seconds=raw_seconds,
        voiced_ms=voiced_ms,
        longest_voiced_ms=longest_voiced_ms,
        unstable=unstable,
        window_sec=deps.tail_fragment_window_sec,
        max_raw_sec=deps.tail_fragment_max_raw_sec,
        max_voiced_ms=deps.tail_fragment_max_voiced_ms,
        max_longest_ms=deps.tail_fragment_max_longest_ms,
    )


def should_skip_full_stt_after_wake_probe_from_runtime(
    *,
    wake_detected: bool,
    wake_probe: str,
    duration_sec: float,
    deps: DiscordSessionPolicyRuntimeDeps,
) -> bool:
    return should_skip_full_stt_after_wake_probe_policy(
        wake_detected=wake_detected,
        wake_probe=wake_probe,
        duration_sec=duration_sec,
        no_wake_max_continue_sec=deps.no_wake_max_continue_sec,
        clean_text=deps.clean_text,
        looks_like_brief_filler_text=deps.looks_like_brief_filler_text,
        looks_like_repetitive_noise_text=deps.looks_like_repetitive_noise_text,
    )


def should_ignore_short_transcription_from_runtime(
    *,
    text: str,
    audio_sec: float | None = None,
    wake_detected: bool,
    pcm_bytes: bytes | None = None,
    deps: DiscordSessionPolicyRuntimeDeps,
) -> bool:
    if audio_sec is None:
        resolver = deps.audio_duration_fn
        if resolver is None:
            raise ValueError("audio_duration_fn is required when audio_sec is omitted")
        audio_sec = resolver(pcm_bytes or b"")
    return should_ignore_short_transcription_policy(
        text=text,
        audio_sec=audio_sec,
        wake_detected=wake_detected,
        normalize_voice_text=deps.normalize_voice_text,
        normalized_wake_words=deps.normalized_wake_words,
        min_audio_sec=deps.min_audio_sec,
        min_transcribed_len=deps.min_transcribed_len,
        wake_short_text_keep_len=deps.wake_short_text_keep_len,
    )


def is_short_followup_candidate_from_runtime(
    *,
    text: str,
    audio_sec: float | None = None,
    wake_detected: bool,
    owner_followup_active: bool,
    pcm_bytes: bytes | None = None,
    deps: DiscordSessionPolicyRuntimeDeps,
) -> bool:
    if audio_sec is None:
        resolver = deps.audio_duration_fn
        if resolver is None:
            raise ValueError("audio_duration_fn is required when audio_sec is omitted")
        audio_sec = resolver(pcm_bytes or b"")
    return is_short_followup_candidate_policy(
        text=text,
        audio_sec=audio_sec,
        wake_detected=wake_detected,
        owner_followup_active=owner_followup_active,
        normalize_voice_text=deps.normalize_voice_text,
        min_audio_sec=deps.min_audio_sec,
        min_transcribed_len=deps.min_transcribed_len,
    )


__all__ = [
    "DiscordSessionPolicyRuntimeDeps",
    "should_require_confirm_exact_for_wake_from_runtime",
    "is_transport_corrupted_audio_from_runtime",
    "is_tail_fragment_candidate_from_runtime",
    "should_skip_full_stt_after_wake_probe_from_runtime",
    "should_ignore_short_transcription_from_runtime",
    "is_short_followup_candidate_from_runtime",
]
