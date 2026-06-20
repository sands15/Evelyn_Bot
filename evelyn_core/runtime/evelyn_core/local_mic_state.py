from __future__ import annotations

import time
from typing import Any

from .text import clean_text


VOICE_INPUT_MODES = {"auto", "local", "discord"}


def normalize_voice_input_mode(value: str | None) -> str:
    mode = clean_text(str(value or "")).lower().replace("-", "_")
    aliases = {
        "local_mic": "local",
        "mic": "local",
        "microphone": "local",
        "discord_voice": "discord",
        "vc": "discord",
        "voice": "discord",
    }
    mode = aliases.get(mode, mode)
    return mode if mode in VOICE_INPUT_MODES else "auto"


def build_local_mic_runtime_state(
    *,
    enabled: bool,
    input_mode: str | None,
    routed_user_ids: set[int] | list[int] | tuple[int, ...],
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "input_mode": normalize_voice_input_mode(input_mode),
        "capture_ready": False,
        "last_error": None,
        "routed_user_ids": sorted(int(user_id) for user_id in routed_user_ids),
        "segment_count": 0,
        "last_segment_at": None,
        "last_segment_duration_sec": None,
        "discord_suppression_active": False,
        "last_filter": None,
        "rejected_segment_count": 0,
    }


def set_voice_input_mode_state(state: dict[str, Any], mode: str | None) -> str:
    normalized = normalize_voice_input_mode(mode)
    state["input_mode"] = normalized
    if normalized == "discord":
        state["discord_suppression_active"] = False
    return normalized


def voice_input_mode_status_line_from_mode(mode: str | None) -> str:
    normalized = normalize_voice_input_mode(mode)
    if normalized == "local":
        return "local mic only"
    if normalized == "discord":
        return "discord voice only"
    return "auto"


def serialize_local_mic_runtime_state_payload(
    state: dict[str, Any],
    *,
    service: Any | None,
    now: float | None = None,
    max_silence_ms: int,
    vad_filter_enabled: bool,
    env_noise_filter_enabled: bool,
    waveform_filter_enabled: bool,
    discord_suppress_after_segment_sec: float,
    device: str | None,
    sample_rate: int,
    start_threshold: float,
    continue_threshold: float,
) -> dict[str, Any]:
    now_ts = time.time() if now is None else float(now)
    input_mode = normalize_voice_input_mode(str(state.get("input_mode") or "auto"))
    state["input_mode"] = input_mode
    capture_ready = bool(service and service.capture_ready)
    state["capture_ready"] = capture_ready
    last_segment_at = state.get("last_segment_at")
    last_segment_age_sec = None
    if isinstance(last_segment_at, (int, float)):
        last_segment_age_sec = round(max(0.0, now_ts - float(last_segment_at)), 3)
    last_input_age_sec = None
    if service is not None and isinstance(getattr(service, "last_input_at", None), (int, float)):
        last_input_age_sec = round(max(0.0, now_ts - float(service.last_input_at)), 3)
    return {
        "enabled": bool(state.get("enabled")),
        "inputMode": input_mode,
        "inputModeLabel": voice_input_mode_status_line_from_mode(input_mode),
        "captureReady": capture_ready,
        "lastError": state.get("last_error"),
        "routedUserIds": list(state.get("routed_user_ids") or []),
        "segmentCount": int(state.get("segment_count") or 0),
        "lastSegmentAgeSec": last_segment_age_sec,
        "lastSegmentDurationSec": state.get("last_segment_duration_sec"),
        "inputBlockCount": int(getattr(service, "input_block_count", 0) or 0),
        "lastInputAgeSec": last_input_age_sec,
        "lastInputLevel": round(float(getattr(service, "last_input_level", 0.0) or 0.0), 6),
        "maxInputLevel": round(float(getattr(service, "max_input_level", 0.0) or 0.0), 6),
        "lastInputStatus": getattr(service, "last_input_status", None),
        "effectiveMaxSilenceMs": int(getattr(service, "last_effective_max_silence_ms", max_silence_ms) or max_silence_ms),
        "rejectedSegmentCount": int(getattr(service, "rejected_segment_count", 0) or 0),
        "lastRejectedReason": getattr(service, "last_rejected_reason", None),
        "lastVoiceFilter": getattr(service, "last_segment_filter", None) or state.get("last_filter"),
        "vadFilterEnabled": bool(vad_filter_enabled),
        "envNoiseFilterEnabled": bool(env_noise_filter_enabled),
        "waveformFilterEnabled": bool(waveform_filter_enabled),
        "discordSuppressionActive": bool(state.get("discord_suppression_active")),
        "discordSuppressAfterSegmentSec": float(discord_suppress_after_segment_sec),
        "device": device or "default",
        "sampleRate": int(sample_rate),
        "captureSampleRate": int(getattr(service, "sample_rate", sample_rate) or sample_rate),
        "startThreshold": float(start_threshold),
        "continueThreshold": float(continue_threshold),
    }


def local_mic_status_line_from_payload(payload: dict[str, Any]) -> str:
    mode_text = payload.get("inputModeLabel") or payload.get("inputMode") or "auto"
    if not payload["enabled"]:
        return f"{mode_text} | disabled"
    if payload["captureReady"]:
        age = payload.get("lastSegmentAgeSec")
        segment_text = "no segments" if age is None else f"last segment {age:.1f}s ago"
        suppress_text = "discord suppress on" if payload.get("discordSuppressionActive") else "discord fallback on"
        return f"{mode_text} | ready | {segment_text} | {suppress_text}"
    error = clean_text(str(payload.get("lastError") or "capture not ready"))
    return f"{mode_text} | not ready | {error}"


__all__ = [
    "VOICE_INPUT_MODES",
    "build_local_mic_runtime_state",
    "local_mic_status_line_from_payload",
    "normalize_voice_input_mode",
    "serialize_local_mic_runtime_state_payload",
    "set_voice_input_mode_state",
    "voice_input_mode_status_line_from_mode",
]
