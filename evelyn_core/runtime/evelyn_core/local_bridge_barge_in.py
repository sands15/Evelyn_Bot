from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .discord_session_policy import TtsInterruptMeta, should_interrupt_tts


@dataclass(frozen=True)
class LocalBargeInDecision:
    accepted: bool
    reason: str
    interrupt_meta: TtsInterruptMeta
    speaker_verification: dict[str, Any] | None = None


class SingleOwnerPlaybackController:
    """Keep one playback owner until its cancelled turn has fully unwound."""

    def __init__(self) -> None:
        self._owner_id = ""
        self._cancel: Callable[[], Any] | None = None
        self._cancel_requested = False

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    def claim(self, owner_id: str, cancel: Callable[[], Any]) -> bool:
        normalized = str(owner_id or "").strip()
        if not normalized:
            raise ValueError("playback owner id is required")
        if self._owner_id and self._owner_id != normalized:
            return False
        self._owner_id = normalized
        self._cancel = cancel
        return True

    def request_cancel(self) -> bool:
        if not self._owner_id or self._cancel is None or self._cancel_requested:
            return False
        self._cancel_requested = True
        self._cancel()
        return True

    def release(self, owner_id: str) -> bool:
        if not self._owner_id or self._owner_id != str(owner_id or "").strip():
            return False
        self._owner_id = ""
        self._cancel = None
        self._cancel_requested = False
        return True


def build_local_tts_interrupt_meta(
    segment_meta: dict[str, Any] | None,
    *,
    body_rms_min: float,
) -> TtsInterruptMeta:
    meta = dict(segment_meta or {})
    voice_filter = (
        dict(meta.get("voice_filter") or {})
        if isinstance(meta.get("voice_filter"), dict)
        else {}
    )
    duration_sec = max(0.0, float(meta.get("duration_sec") or 0.0))
    body_rms = max(
        float(voice_filter.get("bodyRms") or 0.0),
        float(voice_filter.get("rms") or 0.0),
    )
    vad_silent = bool(voice_filter.get("vadSilent"))
    environment_noise = bool(voice_filter.get("environmentNoise"))
    weak_waveform = bool(voice_filter.get("weakWaveform"))
    vad_prob_raw = meta.get("vad_prob", voice_filter.get("vadProb"))
    if isinstance(vad_prob_raw, (int, float)):
        vad_prob = max(0.0, min(1.0, float(vad_prob_raw)))
    else:
        vad_prob = 0.0 if vad_silent else 0.75 if not environment_noise and not weak_waveform else 0.35
    voice_like = not vad_silent and not environment_noise and not weak_waveform
    return TtsInterruptMeta(
        active_speaker_match=True,
        wake_detected=bool(meta.get("wake_detected")),
        vad_prob=vad_prob,
        audio_sec=duration_sec,
        rms_ok=body_rms >= max(0.0, float(body_rms_min)),
        voice_like=voice_like,
    )


def evaluate_local_barge_in(
    segment_meta: dict[str, Any] | None,
    *,
    body_rms_min: float,
    speaker_verification: Any = None,
) -> LocalBargeInDecision:
    interrupt_meta = build_local_tts_interrupt_meta(
        segment_meta,
        body_rms_min=body_rms_min,
    )
    speaker_payload: dict[str, Any] | None = None
    if speaker_verification is not None:
        to_dict = getattr(speaker_verification, "to_dict", None)
        if callable(to_dict):
            speaker_payload = dict(to_dict())
        elif isinstance(speaker_verification, dict):
            speaker_payload = dict(speaker_verification)
        matched = getattr(speaker_verification, "matched", None)
        if matched is None and isinstance(speaker_verification, dict):
            status = str(speaker_verification.get("status") or "")
            matched = True if status == "verified" else False if status == "rejected" else None
        if matched is False:
            return LocalBargeInDecision(
                accepted=False,
                reason="speaker_verification_rejected",
                interrupt_meta=interrupt_meta,
                speaker_verification=speaker_payload,
            )
    if not should_interrupt_tts(interrupt_meta):
        return LocalBargeInDecision(
            accepted=False,
            reason="weak_or_echo_input",
            interrupt_meta=interrupt_meta,
            speaker_verification=speaker_payload,
        )
    return LocalBargeInDecision(
        accepted=True,
        reason="qualified_user_audio",
        interrupt_meta=interrupt_meta,
        speaker_verification=speaker_payload,
    )


__all__ = [
    "LocalBargeInDecision",
    "SingleOwnerPlaybackController",
    "build_local_tts_interrupt_meta",
    "evaluate_local_barge_in",
]
