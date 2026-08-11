from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .voice_pipeline import (
    ActionResult,
    AnswerPayload,
    DeliveryPlan,
    RouteDecision,
    TranscriptResult,
    VoiceReplyRequest,
    VoiceSegment,
)


@dataclass(frozen=True, slots=True)
class AcceptedVoiceTurn:
    accepted_turn_id: str
    segment: VoiceSegment
    transcript: TranscriptResult | None
    gate_mode: str
    ingress_source: str
    queue_wait_ms: float
    accepted_at_unix: float
    reply_scope_key: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RejectedVoiceTurn:
    segment: VoiceSegment
    ingress_source: str
    drop_reason: str
    drop_detail: str | None
    queue_wait_ms: float
    rejected_at_unix: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TtsSynthRequest:
    request_id: str
    turn_id: str
    text: str
    voice: str
    voice_profile: str | None
    response_format: str
    sample_rate_hz: int
    stream: bool
    chunk_index: int
    is_prefetch: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TtsSynthResult:
    request_id: str
    turn_id: str
    backend: str
    ok: bool
    response_format: str
    sample_rate_hz: int
    audio_bytes: bytes = b""
    profile_resolved: str | None = None
    status_code: int | None = None
    latency_ms: float | None = None
    first_audio_ms: float | None = None
    error_code: str | None = None
    error_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryRecallRequest:
    turn_id: str
    session_key: str | None
    guild_id: int | None
    user_text: str
    topic_id: str | None
    source: str
    owner_scope: str | None = None
    max_items: int = 6
    timeout_ms: int = 1200
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryRecallResult:
    turn_id: str
    ok: bool
    context_text: str
    facts: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    latency_ms: float | None = None
    truncated: bool = False
    error_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryWritebackRequest:
    turn_id: str
    session_key: str | None
    guild_id: int | None
    user_text: str
    answer_text: str
    topic_id: str | None
    source: str
    should_update_long_term: bool = True
    should_append_history: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    created_at_unix: float
    voice_pipeline: dict[str, Any] = field(default_factory=dict)
    runtime_services: dict[str, Any] = field(default_factory=dict)
    tts: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    minecraft: dict[str, Any] = field(default_factory=dict)
    presentation: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "AcceptedVoiceTurn",
    "ActionResult",
    "AnswerPayload",
    "DeliveryPlan",
    "MemoryRecallRequest",
    "MemoryRecallResult",
    "MemoryWritebackRequest",
    "RejectedVoiceTurn",
    "RouteDecision",
    "RuntimeSnapshot",
    "TranscriptResult",
    "TtsSynthRequest",
    "TtsSynthResult",
    "VoiceReplyRequest",
    "VoiceSegment",
]
