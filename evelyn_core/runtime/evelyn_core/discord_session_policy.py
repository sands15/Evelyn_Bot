from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .room_session_state import (
    clear_room_owner,
    is_room_owner_active,
    room_state_snapshot,
    set_room_owner,
    set_room_reply_in_progress,
)


@dataclass(frozen=True)
class VoiceReplyGateInput:
    text: str
    wake_detected: bool
    wake_match_mode: str
    user_id: int | None
    owner_user_id: int | None
    owner_active: bool
    active_session: bool
    awaiting_user_reply: bool
    active_speaker_user_id: int | None
    last_stt_text: str
    tts_suppression: str | None
    cooldown_active: bool


@dataclass(frozen=True)
class VoiceReplyGateDecision:
    accepted: bool
    reason: str
    gate_mode: str


@dataclass(frozen=True)
class LocalMicDiscordSuppressionInput:
    member_id: int | None
    source: str | None
    input_mode: str
    capture_ready: bool
    local_mic_recent: bool
    preferred_user_ids: set[int]


@dataclass(frozen=True)
class LocalMicDiscordSuppressionDecision:
    suppress: bool
    normalized_input_mode: str


@dataclass(frozen=True)
class TtsInterruptMeta:
    active_speaker_match: bool = False
    wake_detected: bool = False
    vad_prob: float = 0.0
    audio_sec: float = 0.0
    rms_ok: bool = False
    voice_like: bool = False


@dataclass(frozen=True)
class DiscordRoomSessionPolicy:
    room_owner_user_ids: MutableMapping[str, int]
    room_owner_until: MutableMapping[str, float]
    room_reply_in_progress: MutableMapping[str, bool]
    log_event: Callable[..., Any]
    now_monotonic: Callable[[], float]
    pick_active_speaker: Callable[[str | None], int | None]

    def clear_owner(self, room_session_key: str | None) -> None:
        clear_room_owner(
            room_session_key,
            room_owner_user_ids=self.room_owner_user_ids,
            room_owner_until=self.room_owner_until,
        )

    def snapshot(self, room_session_key: str | None) -> dict[str, Any]:
        return room_state_snapshot(
            room_session_key,
            room_owner_user_ids=self.room_owner_user_ids,
            room_owner_until=self.room_owner_until,
            room_reply_in_progress=self.room_reply_in_progress,
            active_speaker_user_id=self.pick_active_speaker(room_session_key),
            now_monotonic=self.now_monotonic(),
        )

    def is_owner_active(self, room_session_key: str | None, user_id: int | None) -> bool:
        return is_room_owner_active(
            room_session_key,
            user_id,
            room_owner_user_ids=self.room_owner_user_ids,
            room_owner_until=self.room_owner_until,
            room_reply_in_progress=self.room_reply_in_progress,
            active_speaker_user_id=self.pick_active_speaker(room_session_key),
            now_monotonic=self.now_monotonic(),
        )

    def set_owner(
        self,
        room_session_key: str | None,
        user_id: int | None,
        *,
        ttl_sec: float,
        reason: str,
        session_key: str | None = None,
        turn_id: str | None = None,
        segment_id: int | None = None,
    ) -> None:
        set_room_owner(
            room_session_key,
            user_id,
            ttl_sec=ttl_sec,
            reason=reason,
            room_owner_user_ids=self.room_owner_user_ids,
            room_owner_until=self.room_owner_until,
            log_event=self.log_event,
            now_monotonic=self.now_monotonic(),
            session_key=session_key,
            turn_id=turn_id,
            segment_id=segment_id,
        )

    def set_reply_in_progress(
        self,
        room_session_key: str | None,
        value: bool,
        *,
        owner_user_id: int | None = None,
    ) -> None:
        set_room_reply_in_progress(
            room_session_key,
            value,
            room_reply_in_progress=self.room_reply_in_progress,
            room_owner_user_ids=self.room_owner_user_ids,
            log_event=self.log_event,
            owner_user_id=owner_user_id,
        )


def decide_voice_reply_gate(
    gate: VoiceReplyGateInput,
    *,
    normalize_voice_text: Callable[[str], str],
    contains_wake_word: Callable[[str], bool],
    looks_like_brief_filler_text: Callable[[str], bool],
    looks_like_repetitive_noise_text: Callable[[str], bool],
    is_similar: Callable[[str, str], bool],
    min_text_len: int,
) -> VoiceReplyGateDecision:
    text_n = normalize_voice_text(gate.text)
    awaiting_followup = bool(gate.awaiting_user_reply) and gate.owner_active
    followup_allowed = gate.owner_active and (gate.active_session or awaiting_followup)

    if gate.tts_suppression is not None:
        return VoiceReplyGateDecision(False, gate.tts_suppression, gate.tts_suppression)

    if not text_n:
        return VoiceReplyGateDecision(False, "empty", "empty")

    if looks_like_brief_filler_text(text_n):
        return VoiceReplyGateDecision(False, "reply_gate_brief_filler", "reply_gate_brief_filler")

    if looks_like_repetitive_noise_text(text_n):
        return VoiceReplyGateDecision(False, "reply_gate_noise_text", "reply_gate_noise_text")

    last_stt_text = normalize_voice_text(gate.last_stt_text)
    if gate.cooldown_active and last_stt_text and is_similar(text_n, last_stt_text):
        return VoiceReplyGateDecision(False, "duplicate", "duplicate")

    if followup_allowed:
        return VoiceReplyGateDecision(True, "ok", "owner_followup")

    if (
        gate.active_speaker_user_id is not None
        and gate.user_id is not None
        and gate.active_speaker_user_id != gate.user_id
        and not gate.wake_detected
    ):
        return VoiceReplyGateDecision(False, "not_active_speaker", "not_active_speaker")

    if gate.owner_user_id is not None and gate.user_id is not None and gate.owner_user_id != gate.user_id:
        if not gate.wake_detected:
            return VoiceReplyGateDecision(False, "owner_mismatch_needs_wake", "owner_mismatch_needs_wake")
        if gate.wake_match_mode != "exact":
            return VoiceReplyGateDecision(False, "owner_takeover_requires_exact_wake", "owner_takeover_requires_exact_wake")

    if not gate.wake_detected and not contains_wake_word(text_n):
        return VoiceReplyGateDecision(False, "no_wake_word", "no_wake_word")

    if len(text_n) < min_text_len and not gate.wake_detected:
        return VoiceReplyGateDecision(False, "too_short", "too_short")

    if gate.cooldown_active and not gate.wake_detected:
        return VoiceReplyGateDecision(False, "cooldown", "cooldown")

    if gate.wake_detected and gate.owner_user_id is not None and gate.owner_user_id != gate.user_id:
        return VoiceReplyGateDecision(True, "ok", "owner_takeover")

    return VoiceReplyGateDecision(True, "ok", "wake_entry")


def decide_local_mic_discord_suppression(
    policy: LocalMicDiscordSuppressionInput,
    *,
    normalize_voice_input_mode: Callable[[str | None], str],
    should_route_discord_user_to_local_mic: Callable[..., bool],
) -> LocalMicDiscordSuppressionDecision:
    if policy.source == "local_mic":
        return LocalMicDiscordSuppressionDecision(False, normalize_voice_input_mode(policy.input_mode))

    input_mode = normalize_voice_input_mode(policy.input_mode)
    if input_mode == "discord":
        return LocalMicDiscordSuppressionDecision(False, input_mode)

    if input_mode == "local":
        return LocalMicDiscordSuppressionDecision(
            should_route_discord_user_to_local_mic(
                policy.member_id,
                preferred_user_ids=policy.preferred_user_ids,
                capture_ready=True,
            ),
            input_mode,
        )

    return LocalMicDiscordSuppressionDecision(
        bool(
            policy.local_mic_recent
            and should_route_discord_user_to_local_mic(
                policy.member_id,
                preferred_user_ids=policy.preferred_user_ids,
                capture_ready=policy.capture_ready,
            )
        ),
        input_mode,
    )


def should_interrupt_tts(meta: TtsInterruptMeta) -> bool:
    if meta.wake_detected and meta.audio_sec >= 0.18:
        return True
    if meta.active_speaker_match and meta.voice_like and meta.audio_sec >= 0.35 and meta.vad_prob >= 0.55:
        return True
    return meta.vad_prob >= 0.6 and meta.audio_sec >= 0.35 and meta.rms_ok


def should_skip_full_stt_after_wake_probe_policy(
    *,
    wake_detected: bool,
    wake_probe: str,
    duration_sec: float,
    no_wake_max_continue_sec: float,
    clean_text: Callable[[str], str],
    looks_like_brief_filler_text: Callable[[str], bool],
    looks_like_repetitive_noise_text: Callable[[str], bool],
) -> bool:
    if wake_detected:
        return False

    probe = clean_text(wake_probe)
    if not probe and duration_sec <= no_wake_max_continue_sec:
        return True
    if looks_like_brief_filler_text(probe) and duration_sec <= no_wake_max_continue_sec:
        return True
    if looks_like_repetitive_noise_text(probe):
        return True
    return False


def should_ignore_short_transcription_policy(
    *,
    text: str,
    audio_sec: float,
    wake_detected: bool,
    normalize_voice_text: Callable[[str], str],
    normalized_wake_words: Callable[[], set[str]],
    min_audio_sec: float,
    min_transcribed_len: int,
    wake_short_text_keep_len: int,
) -> bool:
    text_n = normalize_voice_text(text)
    if not text_n:
        return True

    if text_n in normalized_wake_words():
        return False

    if wake_detected and len(text_n) >= wake_short_text_keep_len:
        return False

    return audio_sec < min_audio_sec and len(text_n) < min_transcribed_len


def is_short_followup_candidate_policy(
    *,
    text: str,
    audio_sec: float,
    wake_detected: bool,
    owner_followup_active: bool,
    normalize_voice_text: Callable[[str], str],
    min_audio_sec: float,
    min_transcribed_len: int,
) -> bool:
    text_n = normalize_voice_text(text)
    if not owner_followup_active:
        return False
    if wake_detected:
        return False
    if not text_n:
        return False
    return audio_sec < max(min_audio_sec * 1.5, 1.2) and len(text_n) < max(min_transcribed_len + 2, 8)


def estimate_voice_like_probability_policy(
    *,
    voiced_ms: float,
    audio_sec: float,
    body_rms: float,
    body_rms_min: float,
) -> float:
    audio_ms = max(audio_sec * 1000.0, 1.0)
    voiced_ratio = max(0.0, min(1.0, voiced_ms / audio_ms))
    rms_ratio = 0.0
    if body_rms_min > 0:
        rms_ratio = max(0.0, min(1.0, body_rms / body_rms_min))
    return max(voiced_ratio, rms_ratio)


def should_require_confirm_exact_for_wake_policy(debug_meta: dict[str, Any] | None) -> bool:
    if not debug_meta:
        return False
    reasons = [str(reason) for reason in (debug_meta.get("reasons") or [])]
    if any(
        marker in reason
        for reason in reasons
        for marker in ("opus_fail", "plc", "fec", "front_burst_detected", "heavy_trim_ms", "burst_trim_ms")
    ):
        return True
    if debug_meta.get("front_burst_detected"):
        return True
    if int(debug_meta.get("opus_fail") or 0) > 0:
        return True
    if int(debug_meta.get("plc_packets") or 0) > 0:
        return True
    if int(debug_meta.get("fec_packets") or 0) > 0:
        return True
    if float(debug_meta.get("trim_ms") or 0.0) >= 220.0:
        return True
    if float(debug_meta.get("burst_trim_ms") or 0.0) >= 140.0:
        return True
    return False


def is_transport_corrupted_audio_policy(debug_meta: dict[str, Any] | None) -> bool:
    if not debug_meta:
        return False
    reasons = [str(reason) for reason in (debug_meta.get("reasons") or [])]
    required_markers = ("opus_fail", "plc", "fec", "front_burst_detected", "heavy_trim_ms", "burst_trim_ms")
    reason_hits = {marker: any(marker in reason for reason in reasons) for marker in required_markers}
    return (
        (reason_hits["opus_fail"] or int(debug_meta.get("opus_fail") or 0) >= 4)
        and (reason_hits["plc"] or int(debug_meta.get("plc_packets") or 0) >= 2)
        and (reason_hits["fec"] or int(debug_meta.get("fec_packets") or 0) >= 2)
        and (reason_hits["front_burst_detected"] or bool(debug_meta.get("front_burst_detected")))
        and (reason_hits["heavy_trim_ms"] or float(debug_meta.get("trim_ms") or 0.0) >= 220.0)
        and (reason_hits["burst_trim_ms"] or float(debug_meta.get("burst_trim_ms") or 0.0) >= 140.0)
    )


def is_tail_fragment_candidate_policy(
    *,
    has_session_key: bool,
    accepted_age_sec: float | None,
    raw_seconds: float,
    voiced_ms: float,
    longest_voiced_ms: float,
    unstable: bool,
    window_sec: float,
    max_raw_sec: float,
    max_voiced_ms: float,
    max_longest_ms: float,
) -> bool:
    if not has_session_key:
        return False
    if accepted_age_sec is None or accepted_age_sec < 0:
        return False
    if accepted_age_sec > window_sec:
        return False
    if raw_seconds > max_raw_sec:
        return False
    if voiced_ms > max_voiced_ms:
        return False
    if longest_voiced_ms > max_longest_ms:
        return False
    return unstable or raw_seconds <= (max_raw_sec * 0.6)
