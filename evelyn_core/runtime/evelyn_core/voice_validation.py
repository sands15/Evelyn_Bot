from __future__ import annotations

import json
import math
import os
import re
import statistics
import threading
import time
import unicodedata
import uuid
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from .paths import get_runtime_artifacts_root


SESSION_SCHEMA = "voice_validation.session.v1"
SUITE_ID = "voice-p0.v1"
SESSION_TTL_SEC = 30 * 60
MAX_ATTEMPTS = 3
REPORT_MAX_AGE_DAYS = 30
REPORT_PRESERVE_NEWEST = 20
EVENT_REORDER_GRACE_SEC = 2.0
ALLOWED_SURFACES = ("local", "discord")
TERMINAL_STATES = frozenset({"passed", "failed", "aborted"})
LATENCY_WARNING_MS = {"local": 2500.0, "discord": 3000.0}
SILENCE_LIVENESS_MAX_GAP_SEC = {"local": 2.0, "discord": 3.0}
SILENCE_LIVENESS_SAMPLE_LIMIT = 64
_SILENCE_LIVENESS_CLOCK_SKEW_SEC = 0.5
_SILENCE_LIVENESS_STEP_KEYS = (
    "silenceStartedAt",
    "silenceCompletedAt",
    "silenceLivenessFirstAt",
    "silenceLivenessLastAt",
    "silenceLivenessMaxGapSec",
    "silenceLivenessReadyCount",
    "_silenceLivenessSamples",
)
SILENCE_NON_ACCEPTED_DROP_REASONS = frozenset(
    {
        "env_ignore",
        "filler_ignore",
        "noise_text_ignore",
        "too_short_total",
        "vad_ignore",
    }
)

_PRIVATE_EVENT_KEYS = frozenset(
    {
        "audio",
        "audioBytes",
        "audio_f32_base64",
        "pcm",
        "pcmBytes",
        "rawAudio",
        "raw_audio",
        "text",
        "transcript",
        "rawTranscript",
        "raw_transcript",
        "reply",
    }
)
_PRIVATE_EVENT_KEY_TOKENS = frozenset(
    re.sub(r"[^a-z0-9]+", "", key.lower()) for key in _PRIVATE_EVENT_KEYS
)
_ATTEMPT_SOURCE_BINDINGS: dict[str, tuple[str, str]] = {}
_ATTEMPT_SOURCE_BINDINGS_LOCK = threading.Lock()
_ATTEMPT_REVISION_OMITTED = object()
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)
_ATTEMPT_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_ALLOWED_EVENT_TYPES = frozenset(
    {
        "capture",
        "stt_final",
        "turn_accepted",
        "reply_started",
        "reply_final",
        "playback_started",
        "playback_completed",
        "playback_cancelled",
        "playback_failed",
        "barge_in_accepted",
        "barge_in_rejected",
        "silence_started",
        "silence_completed",
        "voice_turn_summary",
        "voice_drop_summary",
        "tts_interrupt",
        "barge_in_continuity",
        "silence_liveness",
        "error",
    }
)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _silence_liveness_is_complete(
    step: dict[str, Any],
    *,
    boundary_at: float | None = None,
) -> bool:
    surface = str(step.get("surface") or "")
    max_gap = SILENCE_LIVENESS_MAX_GAP_SEC.get(surface)
    started_at = _finite_number(step.get("silenceStartedAt"))
    completed_at = _finite_number(
        boundary_at if boundary_at is not None else step.get("silenceCompletedAt")
    )
    first_at = _finite_number(step.get("silenceLivenessFirstAt"))
    last_at = _finite_number(step.get("silenceLivenessLastAt"))
    observed_max_gap = _finite_number(step.get("silenceLivenessMaxGapSec"))
    ready_count = step.get("silenceLivenessReadyCount")
    silence_sec = _finite_number(step.get("silenceSec"))
    if (
        max_gap is None
        or started_at is None
        or completed_at is None
        or first_at is None
        or last_at is None
        or observed_max_gap is None
        or silence_sec is None
        or isinstance(ready_count, bool)
        or not isinstance(ready_count, int)
        or ready_count < 2
    ):
        return False
    skew = _SILENCE_LIVENESS_CLOCK_SKEW_SEC
    return bool(
        completed_at - started_at >= max(1.0, silence_sec)
        and started_at - skew <= first_at <= started_at + max_gap
        and first_at <= last_at
        and observed_max_gap <= max_gap
        and completed_at - max_gap <= last_at <= completed_at + skew
    )


def _suite_steps() -> list[dict[str, Any]]:
    rows = (
        ("01-wake", "normal", "이블린", ("이블린",)),
        ("02-listening", "normal", "이블린, 지금 듣고 있어?", ("이블린", "듣고")),
        ("03-mood", "normal", "한 문장으로 오늘 기분을 말해줘", ("한문장", "오늘", "기분")),
        ("04-arithmetic", "normal", "둘 더하기 둘은 뭐야?", ("둘", "더하기", "뭐야")),
        ("05-hearing", "normal", "방금 내 말을 잘 들었는지 짧게 답해줘", ("방금", "잘", "들었")),
        ("06-final-sentence", "normal", "음성 테스트 마지막 문장이야", ("음성", "테스트", "마지막")),
        (
            "07-barge-source",
            "barge_source",
            "이블린, 음성 테스트 중이야. 세 문장으로 천천히 네 상태를 설명해줘",
            ("이블린", "음성", "테스트", "세문장", "상태"),
        ),
        (
            "08-barge-interrupt",
            "barge_interrupt",
            "잠깐, 한 문장으로 줄여줘",
            ("잠깐", "한문장", "줄여"),
        ),
        (
            "09-stop-source",
            "barge_source",
            "이번에는 세 문장으로 오늘 하고 싶은 일을 말해줘",
            ("이번", "세문장", "오늘", "하고싶"),
        ),
        (
            "10-stop-interrupt",
            "barge_interrupt",
            "잠깐, 이제 그만 말해",
            ("잠깐", "이제", "그만"),
        ),
        ("11-silence", "silence", "", ()),
    )
    return [
        {
            "id": step_id,
            "kind": kind,
            "prompt": prompt,
            "keywords": list(keywords),
            "silenceSec": 15 if kind == "silence" else 0,
        }
        for step_id, kind, prompt, keywords in rows
    ]


def normalize_validation_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    return "".join(ch for ch in normalized if ch.isalnum())


def _parse_attempt_revision(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and (
        not math.isfinite(value) or not value.is_integer()
    ):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def transcript_match(
    transcript: Any,
    expected: Any,
    *,
    keywords: Iterable[str] = (),
    threshold: float = 0.70,
) -> dict[str, Any]:
    actual_normalized = normalize_validation_text(transcript)
    expected_normalized = normalize_validation_text(expected)
    normalized_keywords = [normalize_validation_text(item) for item in keywords]
    normalized_keywords = [item for item in normalized_keywords if item]
    matched_keywords = [item for item in normalized_keywords if item in actual_normalized]
    keyword_ratio = (
        len(matched_keywords) / len(normalized_keywords) if normalized_keywords else 1.0
    )
    similarity = (
        SequenceMatcher(None, expected_normalized, actual_normalized).ratio()
        if expected_normalized and actual_normalized
        else 0.0
    )
    keywords_ok = bool(normalized_keywords and len(matched_keywords) == len(normalized_keywords))
    matched = bool(keywords_ok or similarity >= float(threshold))
    return {
        "matched": matched,
        "similarity": round(similarity, 4),
        "keywordRatio": round(keyword_ratio, 4),
        "matchedKeywordCount": len(matched_keywords),
        "requiredKeywordCount": len(normalized_keywords),
        "threshold": float(threshold),
    }


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _safe_json_read(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _session_id_is_safe(value: Any) -> bool:
    return isinstance(value, str) and bool(_SESSION_ID_PATTERN.fullmatch(value))


def _contained_validation_path(
    artifacts_root: Path,
    candidate: Path,
) -> Path | None:
    try:
        validation_boundary = artifacts_root.resolve(strict=False) / "voice_validation"
        candidate.resolve(strict=False).relative_to(validation_boundary)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _session_artifact_path(
    artifacts_root: Path,
    directory: Path,
    session_id: Any,
    suffix: str,
) -> Path | None:
    if not _session_id_is_safe(session_id):
        return None
    return _contained_validation_path(
        artifacts_root,
        directory / f"{session_id}{suffix}",
    )


def _session_expiry_state(session: dict[str, Any], *, now: float) -> str:
    try:
        expires_at = float(session.get("expiresAt"))
    except (TypeError, ValueError):
        return "invalid"
    if not math.isfinite(expires_at) or expires_at <= 0:
        return "invalid"
    return "expired" if float(now) >= expires_at else "active"


def _event_id(event: dict[str, Any]) -> str:
    explicit = str(event.get("eventId") or "").strip()
    if explicit:
        return explicit[:128]
    material = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
    return sha256(material.encode("utf-8")).hexdigest()[:24]


def sanitize_validation_event(event: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in dict(event).items():
        normalized_key = re.sub(r"[^a-z0-9]+", "", str(key).lower())
        if normalized_key in _PRIVATE_EVENT_KEY_TOKENS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            sanitized[key] = value
        elif isinstance(value, dict):
            nested = {
                str(nested_key): nested_value
                for nested_key, nested_value in value.items()
                if re.sub(r"[^a-z0-9]+", "", str(nested_key).lower())
                not in _PRIVATE_EVENT_KEY_TOKENS
                and (isinstance(nested_value, (str, int, float, bool)) or nested_value is None)
            }
            sanitized[key] = nested
    event_type = re.sub(r"[^a-z0-9_]+", "_", str(sanitized.get("event") or "").lower()).strip("_")
    sanitized["event"] = event_type if event_type in _ALLOWED_EVENT_TYPES else "error"
    sanitized["at"] = float(sanitized.get("at") or time.time())
    sanitized["eventId"] = _event_id(sanitized)
    return sanitized


def _normalize_discord_target(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    guild_id = value.get("guildId")
    channel_id = value.get("channelId")
    if (
        isinstance(guild_id, bool)
        or isinstance(channel_id, bool)
        or guild_id is None
        or channel_id is None
    ):
        return None
    normalized_guild_id = str(guild_id).strip()
    normalized_channel_id = str(channel_id).strip()
    if (
        not normalized_guild_id.isdigit()
        or not normalized_channel_id.isdigit()
        or int(normalized_guild_id) <= 0
        or int(normalized_channel_id) <= 0
    ):
        return None
    return {
        "guildId": normalized_guild_id,
        "channelId": normalized_channel_id,
    }


def resolve_discord_validation_target(
    health: dict[str, Any] | None,
) -> dict[str, Any]:
    """Select one live Discord voice target from a fresh runtime-health snapshot."""

    source = health if isinstance(health, dict) else {}
    cache = source.get("cache") if isinstance(source.get("cache"), dict) else {}
    if cache.get("stale") is True or str(cache.get("lastRefreshError") or ""):
        return {"ok": False, "error": "discord_target_unavailable"}
    service_rows = source.get("services")
    discord_service = next(
        (
            row
            for row in service_rows
            if isinstance(row, dict) and row.get("id") == "discord_bot"
        ),
        None,
    ) if isinstance(service_rows, (list, tuple)) else None
    artifact_payload: dict[str, Any] = {}
    checks = (discord_service or {}).get("checks")
    for check in checks if isinstance(checks, (list, tuple)) else ():
        if isinstance(check, dict) and check.get("kind") == "artifact_json":
            if check.get("ok") is not True:
                break
            payload = check.get("payload")
            if isinstance(payload, dict):
                artifact_payload = payload
            break
    candidates: list[dict[str, str]] = []
    voice_connections = artifact_payload.get("voiceConnections")
    for row in (
        voice_connections if isinstance(voice_connections, (list, tuple)) else ()
    ):
        if not isinstance(row, dict):
            continue
        if row.get("connected") is not True or row.get("listening") is not True:
            continue
        target = _normalize_discord_target(row)
        if target is not None:
            candidates.append(target)
    if not candidates:
        return {"ok": False, "error": "discord_target_unavailable"}
    if len(candidates) != 1:
        return {"ok": False, "error": "ambiguous_discord_target"}
    return {"ok": True, "discordTarget": candidates[0]}


def _active_validation_session_snapshot(
    *,
    surface: str,
    root: Path | None = None,
    now: Any | None = None,
) -> dict[str, Any] | None:
    base = Path(root or get_runtime_artifacts_root()) / "voice_validation"
    active_path = _contained_validation_path(base.parent, base / "active.json")
    active = _safe_json_read(active_path) if active_path is not None else None
    if (
        not active
        or active.get("schema") != SESSION_SCHEMA
        or not _session_id_is_safe(active.get("sessionId"))
        or active.get("state") != "running"
        or active.get("surface") != surface
        or not VoiceValidationManager._loaded_session_is_canonical(
            active,
            allow_missing_attempt_ids=False,
        )
    ):
        return None
    current_time = (now or time.time)()
    if _session_expiry_state(active, now=current_time) != "active":
        return None
    current = active.get("currentStep")
    if not isinstance(current, dict) or not current.get("id"):
        return None
    return active


def _validation_context_from_snapshot(
    active: dict[str, Any],
    *,
    surface: str,
    prefer_interrupt: bool,
) -> dict[str, Any] | None:
    current = active.get("currentStep")
    if not isinstance(current, dict) or not current.get("id"):
        return None
    step_id = (
        str(current.get("interruptStepId") or "")
        if prefer_interrupt and current.get("interruptStepId")
        else str(current.get("id") or "")
    )
    target_step = next(
        (
            item
            for item in active.get("_steps") or []
            if isinstance(item, dict)
            and item.get("surface") == surface
            and item.get("id") == step_id
        ),
        current if step_id == str(current.get("id") or "") else {},
    )
    attempt_id = str(target_step.get("_attemptId") or "")
    with _ATTEMPT_SOURCE_BINDINGS_LOCK:
        bound_guild_id, bound_turn_id = _ATTEMPT_SOURCE_BINDINGS.get(
            attempt_id,
            ("", ""),
        )
    discord_target = (
        _normalize_discord_target(active.get("discordTarget"))
        if surface == "discord"
        else None
    )
    return {
        "sessionId": str(active.get("sessionId") or ""),
        "stepId": step_id,
        "surface": surface,
        "kind": str(target_step.get("kind") or ""),
        "status": str(target_step.get("status") or ""),
        "attempt": int(target_step.get("attempt") or 1),
        "attemptId": attempt_id,
        "discordTarget": deepcopy(discord_target),
        "guildId": bound_guild_id or None,
        "turnId": bound_turn_id or None,
    }


def _validation_contexts_from_snapshot(
    active: dict[str, Any],
    *,
    surface: str,
) -> tuple[dict[str, Any], ...]:
    contexts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()
    for prefer_interrupt in (False, True):
        context = _validation_context_from_snapshot(
            active,
            surface=surface,
            prefer_interrupt=prefer_interrupt,
        )
        if context is None:
            continue
        key = (
            str(context.get("sessionId") or ""),
            str(context.get("stepId") or ""),
            int(context.get("attempt") or 0),
            str(context.get("attemptId") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        contexts.append(context)
    return tuple(contexts)


def _active_validation_contexts(
    *,
    surface: str,
    root: Path | None = None,
    now: Any | None = None,
) -> tuple[dict[str, Any], ...]:
    """Return normal/interrupt contexts from one canonical active.json read."""

    active = _active_validation_session_snapshot(
        surface=surface,
        root=root,
        now=now,
    )
    if active is None:
        return ()
    return _validation_contexts_from_snapshot(active, surface=surface)


def active_validation_context(
    *,
    surface: str,
    root: Path | None = None,
    prefer_interrupt: bool = False,
    now: Any | None = None,
) -> dict[str, Any] | None:
    active = _active_validation_session_snapshot(
        surface=surface,
        root=root,
        now=now,
    )
    if active is None:
        return None
    return _validation_context_from_snapshot(
        active,
        surface=surface,
        prefer_interrupt=prefer_interrupt,
    )


def validation_attempt_binding_is_current(
    metadata: dict[str, Any] | None,
    *,
    surface: str,
    root: Path | None = None,
    now: Any | None = None,
    reject_unbound_when_active: bool = False,
) -> bool:
    source = metadata if isinstance(metadata, dict) else {}
    session_id = str(
        source.get("validation_session_id")
        or source.get("validationSessionId")
        or source.get("sessionId")
        or ""
    )
    step_id = str(
        source.get("validation_step_id")
        or source.get("validationStepId")
        or source.get("stepId")
        or ""
    )
    attempt_id = str(
        source.get("validation_attempt_id")
        or source.get("validationAttemptId")
        or source.get("attemptId")
        or ""
    )
    raw_attempt = source.get("validation_attempt")
    if raw_attempt is None:
        raw_attempt = source.get("validationAttempt")
    if raw_attempt is None:
        raw_attempt = source.get("attempt")
    attempt = (
        _parse_attempt_revision(raw_attempt)
        if raw_attempt is not None
        else None
    )
    if not (session_id or step_id or attempt_id or raw_attempt is not None):
        if not reject_unbound_when_active:
            return True
        return not _active_validation_contexts(
            surface=surface,
            root=root,
            now=now,
        )
    if not (session_id and step_id and attempt_id):
        return False
    if raw_attempt is not None and attempt is None:
        return False
    for context in _active_validation_contexts(
        surface=surface,
        root=root,
        now=now,
    ):
        if (
            context
            and context.get("sessionId") == session_id
            and context.get("stepId") == step_id
            and context.get("attemptId") == attempt_id
            and (
                attempt is None
                or int(context.get("attempt") or 0) == attempt
            )
        ):
            return True
    return False


def validation_transcript_admission_status(
    surface: str,
    transcript: Any,
    metadata: dict[str, Any] | None,
    *,
    root: Path | None = None,
    now: Any | None = None,
) -> dict[str, Any]:
    """Read-only, content-free validation of a transcript-bound test attempt.

    This helper deliberately does not ingest the validation event log or persist
    the session.  It is safe for the Bot API admission boundary to call while the
    Control Page process remains the validation session writer.
    """

    result: dict[str, Any] = {
        "schema": "voice_validation.transcript-admission.v1",
        "current": False,
        "matched": False,
        "kind": "",
        "similarity": 0.0,
        "keywordRatio": 0.0,
        "matchedKeywordCount": 0,
        "requiredKeywordCount": 0,
        "threshold": 0.70,
        "reason": "validation_attempt_stale",
        "contentFree": True,
    }
    normalized_surface = str(surface or "").strip().lower()
    if normalized_surface not in ALLOWED_SURFACES:
        result["reason"] = "validation_surface_invalid"
        return result

    source = metadata if isinstance(metadata, dict) else {}
    session_id = str(
        source.get("validation_session_id")
        or source.get("validationSessionId")
        or source.get("sessionId")
        or ""
    )
    step_id = str(
        source.get("validation_step_id")
        or source.get("validationStepId")
        or source.get("stepId")
        or ""
    )
    attempt_id = str(
        source.get("validation_attempt_id")
        or source.get("validationAttemptId")
        or source.get("attemptId")
        or ""
    )
    attempt = _parse_attempt_revision(
        source.get("validation_attempt")
        or source.get("validationAttempt")
        or source.get("attempt")
    )
    if not (session_id and step_id and attempt_id and attempt is not None):
        return result

    artifacts_root = Path(root or get_runtime_artifacts_root())
    active = _active_validation_session_snapshot(
        surface=normalized_surface,
        root=artifacts_root,
        now=now,
    )
    if active is None:
        return result
    context = next(
        (
            candidate
            for candidate in _validation_contexts_from_snapshot(
                active,
                surface=normalized_surface,
            )
            if candidate
            and candidate.get("sessionId") == session_id
            and candidate.get("stepId") == step_id
            and candidate.get("attemptId") == attempt_id
            and int(candidate.get("attempt") or 0) == attempt
        ),
        None,
    )
    if context is None:
        return result

    step = next(
        (
            item
            for item in (active or {}).get("_steps") or []
            if isinstance(item, dict)
            and item.get("surface") == normalized_surface
            and item.get("id") == step_id
            and item.get("_attemptId") == attempt_id
            and _parse_attempt_revision(item.get("attempt")) == attempt
        ),
        None,
    )
    if step is None:
        return result

    match = transcript_match(
        transcript,
        step.get("prompt"),
        keywords=step.get("keywords") or (),
    )
    kind = str(step.get("kind") or "")
    result.update(
        {
            "current": True,
            "matched": bool(match.get("matched")) and kind != "silence",
            "kind": kind,
            "similarity": float(match.get("similarity") or 0.0),
            "keywordRatio": float(match.get("keywordRatio") or 0.0),
            "matchedKeywordCount": int(match.get("matchedKeywordCount") or 0),
            "requiredKeywordCount": int(match.get("requiredKeywordCount") or 0),
            "threshold": float(match.get("threshold") or 0.70),
            "reason": (
                "validation_silence_activity"
                if kind == "silence"
                else (
                    "validation_transcript_matched"
                    if bool(match.get("matched"))
                    else "validation_transcript_mismatch"
                )
            ),
        }
    )
    return result


def emit_voice_validation_event(
    surface: str,
    event: str,
    *,
    root: Path | None = None,
    session_id: str | None = None,
    step_id: str | None = None,
    attempt_id: str | None = None,
    now: Any | None = None,
    **payload: Any,
) -> dict[str, Any] | None:
    normalized_surface = str(surface or "").strip().lower()
    if normalized_surface not in ALLOWED_SURFACES:
        return None
    contexts = [
        active_validation_context(surface=normalized_surface, root=root, now=now),
        active_validation_context(
            surface=normalized_surface,
            root=root,
            prefer_interrupt=True,
            now=now,
        ),
    ]
    context = next(
        (
            candidate
            for candidate in contexts
            if candidate
            and (not session_id or str(session_id) == candidate.get("sessionId"))
            and (not step_id or str(step_id) == candidate.get("stepId"))
            and (
                not attempt_id
                or str(attempt_id) == candidate.get("attemptId")
            )
        ),
        None,
    )
    if context is None:
        return None
    resolved_session_id = str(context.get("sessionId") or "")
    resolved_step_id = str(context.get("stepId") or "")
    base = Path(root or get_runtime_artifacts_root()) / "voice_validation"
    record = sanitize_validation_event(
        {
            **payload,
            "event": event,
            "sessionId": resolved_session_id,
            "stepId": resolved_step_id,
            "surface": normalized_surface,
            "attempt": int(context.get("attempt") or 1),
            "attemptId": str(context.get("attemptId") or ""),
        }
    )
    events_path = _session_artifact_path(
        base.parent,
        base / "events",
        resolved_session_id,
        ".jsonl",
    )
    if events_path is None:
        return None
    events_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
    return record


def emit_silence_liveness_event(
    surface: str,
    *,
    heartbeat_at: Any,
    root: Path | None = None,
    now: Any | None = None,
    bridge_ready: Any = None,
    mic_enabled: Any = None,
    capture_ready: Any = None,
    gateway_connected: Any = None,
    voice_connections: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Persist content-free, attempt-bound liveness for the active silence step."""

    normalized_surface = str(surface or "").strip().lower()
    observed_at = _finite_number(heartbeat_at)
    if normalized_surface not in ALLOWED_SURFACES or observed_at is None:
        return None
    context = active_validation_context(
        surface=normalized_surface,
        root=root,
        now=now,
    )
    if (
        context is None
        or context.get("kind") != "silence"
        or context.get("status") != "pending"
    ):
        return None

    payload: dict[str, Any]
    if normalized_surface == "local":
        ready = bool(
            bridge_ready is True
            and mic_enabled is True
            and capture_ready is True
        )
        payload = {
            "ready": ready,
            "bridgeReady": bridge_ready is True,
            "micEnabled": mic_enabled is True,
            "captureReady": capture_ready is True,
        }
    else:
        target = _normalize_discord_target(context.get("discordTarget"))
        target_matched = False
        target_connected = False
        target_listening = False
        for row in voice_connections or ():
            if not isinstance(row, dict) or target is None:
                continue
            if (
                str(row.get("guildId") or "") != target["guildId"]
                or str(row.get("channelId") or "") != target["channelId"]
            ):
                continue
            target_matched = True
            target_connected = row.get("connected") is True
            target_listening = row.get("listening") is True
            break
        ready = bool(
            gateway_connected is True
            and target_matched
            and target_connected
            and target_listening
        )
        payload = {
            "ready": ready,
            "gatewayConnected": gateway_connected is True,
            "voiceConnected": target_connected,
            "listeningReady": target_listening,
            "targetMatched": target_matched,
        }

    return emit_voice_validation_event(
        normalized_surface,
        "silence_liveness",
        root=root,
        session_id=str(context.get("sessionId") or ""),
        step_id=str(context.get("stepId") or ""),
        attempt_id=str(context.get("attemptId") or ""),
        now=now,
        at=observed_at,
        **payload,
    )


def emit_transcript_validation_event(
    surface: str,
    transcript: Any,
    *,
    root: Path | None = None,
    session_id: str | None = None,
    step_id: str | None = None,
    attempt_id: str | None = None,
    prefer_interrupt: bool = False,
    **payload: Any,
) -> dict[str, Any] | None:
    base_root = Path(root or get_runtime_artifacts_root())
    normalized_surface = str(surface or "").strip().lower()
    contexts = [
        active_validation_context(
            surface=normalized_surface,
            root=base_root,
            prefer_interrupt=prefer_interrupt,
        ),
        active_validation_context(
            surface=normalized_surface,
            root=base_root,
            prefer_interrupt=not prefer_interrupt,
        ),
    ]
    context = next(
        (
            candidate
            for candidate in contexts
            if candidate
            and (not session_id or str(session_id) == candidate.get("sessionId"))
            and (not step_id or str(step_id) == candidate.get("stepId"))
            and (
                not attempt_id
                or str(attempt_id) == candidate.get("attemptId")
            )
        ),
        None,
    )
    if context is None:
        return None
    active = _safe_json_read(base_root / "voice_validation" / "active.json")
    if not active:
        return None
    resolved_session_id = str(context.get("sessionId") or "")
    resolved_step_id = str(context.get("stepId") or "")
    step = next(
        (
            item
            for item in active.get("_steps") or []
            if isinstance(item, dict)
            and item.get("surface") == normalized_surface
            and item.get("id") == resolved_step_id
        ),
        None,
    )
    if step is None:
        return None
    match = transcript_match(
        transcript,
        step.get("prompt"),
        keywords=step.get("keywords") or (),
    )
    record = emit_voice_validation_event(
        normalized_surface,
        "stt_final",
        root=base_root,
        session_id=resolved_session_id,
        step_id=resolved_step_id,
        attempt_id=str(context.get("attemptId") or ""),
        **payload,
        **match,
    )
    if record is not None and match.get("matched") is True:
        guild_id = str(payload.get("guildId") or "").strip()
        turn_id = str(payload.get("turnId") or "").strip()
        token = str(context.get("attemptId") or "")
        if normalized_surface == "discord" and token and guild_id and turn_id:
            with _ATTEMPT_SOURCE_BINDINGS_LOCK:
                _ATTEMPT_SOURCE_BINDINGS.setdefault(token, (guild_id, turn_id))
                if len(_ATTEMPT_SOURCE_BINDINGS) > 256:
                    oldest_token = next(iter(_ATTEMPT_SOURCE_BINDINGS))
                    if oldest_token != token:
                        _ATTEMPT_SOURCE_BINDINGS.pop(oldest_token, None)
    return record


@dataclass(frozen=True)
class ValidationPaths:
    root: Path

    @property
    def active(self) -> Path:
        return self.root / "active.json"

    @property
    def events(self) -> Path:
        return self.root / "events"

    @property
    def reports(self) -> Path:
        return self.root / "reports"


class VoiceValidationManager:
    def __init__(
        self,
        *,
        root: Path | None = None,
        now: Any = time.time,
        ttl_sec: float = SESSION_TTL_SEC,
    ) -> None:
        self.paths = ValidationPaths(Path(root or get_runtime_artifacts_root()) / "voice_validation")
        self.now = now
        self.ttl_sec = max(1.0, float(ttl_sec))
        self._lock = threading.RLock()
        self._session: dict[str, Any] | None = None
        self._seen_event_ids: set[str] = set()
        self._event_offset = 0
        self._load_active()

    @staticmethod
    def _new_attempt_id() -> str:
        return uuid.uuid4().hex

    def _ensure_attempt_bindings(self) -> bool:
        if not self._session:
            return False
        changed = False
        for step in self._session.get("_steps") or []:
            if not isinstance(step, dict):
                continue
            if not str(step.get("_attemptId") or ""):
                step["_attemptId"] = self._new_attempt_id()
                changed = True
        return changed

    @staticmethod
    def _persisted_step_pass_evidence_is_complete(step: dict[str, Any]) -> bool:
        events = step.get("events") or {}

        def count(event: str) -> int:
            return int(events.get(event) or 0)

        kind = str(step.get("kind") or "")
        if kind == "silence":
            voice_activity = sum(
                count(event)
                for event in (
                    "stt_final",
                    "turn_accepted",
                    "reply_started",
                    "reply_final",
                    "playback_started",
                    "playback_completed",
                    "playback_cancelled",
                    "barge_in_accepted",
                    "tts_interrupt",
                    "barge_in_continuity",
                )
            )
            return bool(
                count("silence_completed") == 1
                and voice_activity == 0
                and _silence_liveness_is_complete(step)
            )
        stt_finals = count("stt_final")
        accepted = count("turn_accepted")
        reply_started = count("reply_started")
        replies = count("reply_final")
        started = count("playback_started")
        completed = count("playback_completed")
        cancelled = count("playback_cancelled")
        interrupt = count("barge_in_accepted")
        qualified_tts_interrupt = count("tts_interrupt")
        continuity = count("barge_in_continuity")
        match_ok = bool((step.get("match") or {}).get("matched"))
        if kind == "normal":
            return bool(
                stt_finals
                == accepted
                == reply_started
                == replies
                == started
                == completed
                == 1
                and cancelled == interrupt == qualified_tts_interrupt == continuity == 0
                and match_ok
                and step.get("heard") is True
            )
        if kind == "barge_source":
            return bool(
                stt_finals == accepted == reply_started == started == cancelled == 1
                and replies in {0, 1}
                and completed == interrupt == continuity == 0
                and qualified_tts_interrupt == 1
                and step.get("acceptedTurnId")
                and step.get("qualifiedTtsInterruptTurnId")
                == step.get("acceptedTurnId")
                and match_ok
            )
        if kind == "barge_interrupt":
            return bool(
                stt_finals
                == accepted
                == reply_started
                == replies
                == started
                == completed
                == interrupt
                == continuity
                == 1
                and cancelled == qualified_tts_interrupt == 0
                and match_ok
                and step.get("heard") is True
            )
        return False

    @classmethod
    def _loaded_session_is_canonical(
        cls,
        payload: dict[str, Any],
        *,
        allow_missing_attempt_ids: bool = True,
    ) -> bool:
        if payload.get("suite") != SUITE_ID:
            return False
        state = payload.get("state")
        if state not in {"preflight", "running", *TERMINAL_STATES}:
            return False
        surfaces = payload.get("surfaces")
        if (
            not isinstance(surfaces, list)
            or not surfaces
            or len(surfaces) != len(set(surfaces))
            or any(surface not in ALLOWED_SURFACES for surface in surfaces)
        ):
            return False
        current_surface = payload.get("surface")
        if current_surface not in surfaces:
            return False
        steps = payload.get("_steps")
        definitions = _suite_steps()
        expected = [
            (surface, definition)
            for surface in surfaces
            for definition in definitions
        ]
        if not isinstance(steps, list) or len(steps) != len(expected):
            return False
        seen_step_keys: set[tuple[str, str]] = set()
        for step, (surface, definition) in zip(steps, expected):
            if not isinstance(step, dict):
                return False
            step_key = (str(step.get("surface") or ""), str(step.get("id") or ""))
            if step_key in seen_step_keys:
                return False
            seen_step_keys.add(step_key)
            if any(
                step.get(key) != expected_value
                for key, expected_value in (
                    ("surface", surface),
                    ("id", definition["id"]),
                    ("kind", definition["kind"]),
                    ("prompt", definition["prompt"]),
                    ("keywords", definition["keywords"]),
                    ("silenceSec", definition["silenceSec"]),
                )
            ):
                return False
            attempt = step.get("attempt")
            if (
                not isinstance(attempt, int)
                or isinstance(attempt, bool)
                or not 1 <= attempt <= MAX_ATTEMPTS
            ):
                return False
            attempt_id = step.get("_attemptId")
            if attempt_id in (None, ""):
                if not allow_missing_attempt_ids:
                    return False
            elif (
                not isinstance(attempt_id, str)
                or not _ATTEMPT_ID_PATTERN.fullmatch(attempt_id)
            ):
                return False
            if step.get("status") not in {"pending", "passed", "failed"}:
                return False
            events = step.get("events")
            if not isinstance(events, dict) or any(
                not isinstance(event, str)
                or event not in _ALLOWED_EVENT_TYPES
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
                for event, count in events.items()
            ):
                return False
            errors = step.get("errors")
            if not isinstance(errors, list) or any(
                not isinstance(error, str) for error in errors
            ):
                return False
            if not isinstance(step.get("heard"), bool):
                return False
            match = step.get("match")
            if match is not None and not isinstance(match, dict):
                return False
            latency = step.get("latencyMs")
            if latency is not None and (
                isinstance(latency, bool)
                or not isinstance(latency, (int, float))
                or not math.isfinite(float(latency))
                or float(latency) < 0
            ):
                return False
            if step.get("kind") == "silence":
                for key in (
                    "silenceStartedAt",
                    "silenceCompletedAt",
                    "silenceLivenessFirstAt",
                    "silenceLivenessLastAt",
                    "silenceLivenessMaxGapSec",
                ):
                    value = step.get(key)
                    if value is not None and (
                        _finite_number(value) is None or float(value) < 0
                    ):
                        return False
                ready_count = step.get("silenceLivenessReadyCount")
                if ready_count is not None and (
                    isinstance(ready_count, bool)
                    or not isinstance(ready_count, int)
                    or ready_count < 0
                ):
                    return False
                samples = step.get("_silenceLivenessSamples")
                if samples is not None:
                    if (
                        not isinstance(samples, list)
                        or len(samples) > SILENCE_LIVENESS_SAMPLE_LIMIT
                        or any(_finite_number(value) is None for value in samples)
                    ):
                        return False
                    normalized_samples = [float(value) for value in samples]
                    if normalized_samples != sorted(set(normalized_samples)):
                        return False
            if (
                step.get("status") == "passed"
                and not cls._persisted_step_pass_evidence_is_complete(step)
            ):
                return False
        if state == "passed" and any(
            step.get("status") != "passed" for step in steps
        ):
            return False
        index = payload.get("_stepIndex")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(steps)
        ):
            return False
        current = payload.get("currentStep")
        indexed_step = steps[index]
        if not isinstance(current, dict) or any(
            current.get(key) != indexed_step.get(key)
            for key in ("id", "surface", "kind", "prompt", "status", "attempt")
        ):
            return False
        if indexed_step.get("surface") != current_surface:
            return False
        surface_index = payload.get("_surfaceIndex")
        if (
            not isinstance(surface_index, int)
            or isinstance(surface_index, bool)
            or surface_index != surfaces.index(current_surface)
        ):
            return False
        if payload.get("attempt") != indexed_step.get("attempt"):
            return False
        if indexed_step.get("kind") == "barge_source":
            if index + 1 >= len(steps):
                return False
            interrupt_step = steps[index + 1]
            if (
                interrupt_step.get("surface") != current_surface
                or interrupt_step.get("kind") != "barge_interrupt"
                or current.get("interruptStepId") != interrupt_step.get("id")
                or current.get("interruptPrompt") != interrupt_step.get("prompt")
            ):
                return False
        return True

    def _invalidate_loaded_session(self, payload: dict[str, Any]) -> None:
        self._session = payload
        self._session["state"] = "failed"
        self._session["suite"] = SUITE_ID
        self._session["failureCode"] = "session_invalid"
        self._session["lastFailureCode"] = "session_invalid"
        self._session["completedAt"] = self.now()
        self._session["surface"] = None
        self._session["surfaces"] = []
        self._session["currentStep"] = {}
        self._session["attempt"] = 1
        self._session["summary"] = {
            "surfacesPassed": 0,
            "surfacesTotal": 0,
            "stepsPassed": 0,
            "stepsTotal": 0,
        }
        self._session["warnings"] = []
        self._session["_surfaceIndex"] = 0
        self._session["_stepIndex"] = 0
        self._session["_steps"] = []
        self._seen_event_ids = set()
        self._event_offset = 0
        self._persist()

    def _idle(self, *, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "schema": SESSION_SCHEMA,
            "sessionId": "",
            "suite": SUITE_ID,
            "state": "idle",
            "surface": None,
            "currentStep": {},
            "attempt": 1,
            "capabilities": deepcopy(capabilities or {}),
            "summary": {
                "surfacesPassed": 0,
                "surfacesTotal": 0,
                "stepsPassed": 0,
                "stepsTotal": 0,
            },
            "warnings": [],
        }

    def _load_active(self) -> None:
        with self._lock:
            active_path = _contained_validation_path(
                self.paths.root.parent,
                self.paths.active,
            )
            payload = _safe_json_read(active_path) if active_path is not None else None
            if (
                not payload
                or payload.get("schema") != SESSION_SCHEMA
                or not _session_id_is_safe(payload.get("sessionId"))
            ):
                self._session = None
                return
            if not self._loaded_session_is_canonical(payload):
                self._invalidate_loaded_session(payload)
                return
            self._session = payload
            self._event_offset = 0
            self._seen_event_ids = set(str(item) for item in payload.get("_seenEventIds") or [])
            current = self._session.get("currentStep") or {}
            current_step = self._step_by_id(
                str(self._session.get("surface") or ""),
                str(current.get("id") or ""),
            )
            current_binding_missing = bool(
                current_step is not None
                and not str(current_step.get("_attemptId") or "")
            )
            bindings_added = self._ensure_attempt_bindings()
            if (
                current_binding_missing
                and current_step is not None
                and self._session.get("state") == "running"
            ):
                self._fail_attempt(
                    current_step,
                    "attempt_binding_migration_required",
                )
                self._sync_current_step()
                self._update_summary()
                bindings_added = True
            self._expire_if_needed()
            self._ingest_event_log()
            if bindings_added:
                self._persist()

    def _expire_if_needed(self) -> None:
        if not self._session or self._session.get("state") in TERMINAL_STATES:
            return
        expiry_state = _session_expiry_state(self._session, now=self.now())
        if expiry_state != "active":
            self._session["state"] = "failed"
            self._session["failureCode"] = (
                "session_expired"
                if expiry_state == "expired"
                else "session_expiry_invalid"
            )
            self._session["completedAt"] = self.now()
            self._finalize_report()

    def _persist(self) -> None:
        if not self._session:
            return
        active_path = _contained_validation_path(
            self.paths.root.parent,
            self.paths.active,
        )
        if active_path is None:
            return
        self._session["updatedAt"] = self.now()
        self._session["_seenEventIds"] = list(sorted(self._seen_event_ids))[-1000:]
        _atomic_json_write(active_path, self._session)

    def _public_session(self, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._session:
            return self._idle(capabilities=capabilities)
        session = deepcopy(self._session)
        session.pop("_steps", None)
        session.pop("_seenEventIds", None)
        session.pop("_surfaceIndex", None)
        session.pop("_stepIndex", None)
        session.pop("expiresAt", None)
        if capabilities is not None:
            session["capabilities"] = deepcopy(capabilities)
        return session

    def snapshot(self, *, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._expire_if_needed()
            self._ingest_event_log()
            self._reevaluate_current_step()
            self._refresh_silence_step()
            if self._session and capabilities is not None:
                self._session["capabilities"] = deepcopy(capabilities)
                self._persist()
            return self._public_session(capabilities=capabilities)

    def start(
        self,
        *,
        suite: str,
        surfaces: Iterable[str],
        capabilities: dict[str, Any] | None = None,
        discord_target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._expire_if_needed()
            if suite != SUITE_ID:
                return {"ok": False, "error": "unsupported_suite", "suite": suite}
            normalized_surfaces: list[str] = []
            for surface in surfaces:
                normalized = str(surface or "").strip().lower()
                if normalized in ALLOWED_SURFACES and normalized not in normalized_surfaces:
                    normalized_surfaces.append(normalized)
            if not normalized_surfaces:
                return {"ok": False, "error": "surfaces_required"}
            if self._session and self._session.get("state") not in TERMINAL_STATES:
                return {
                    "ok": False,
                    "error": "validation_session_active",
                    "session": self._public_session(capabilities=capabilities),
                }

            normalized_discord_target = _normalize_discord_target(discord_target)
            if "discord" in normalized_surfaces and normalized_discord_target is None:
                return {"ok": False, "error": "discord_target_unavailable"}

            capability_map = dict(capabilities or {})
            requested_capabilities = [
                capability_map.get("voiceLocal" if surface == "local" else "voiceDiscord") or {}
                for surface in normalized_surfaces
            ]
            blockers = [
                blocker
                for capability in requested_capabilities
                for blocker in (capability.get("blockers") or [])
                if isinstance(blocker, dict)
            ]
            state = "preflight" if blockers else "running"
            session_id = f"voice-p0-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
            now = self.now()
            steps = []
            for surface in normalized_surfaces:
                for definition in _suite_steps():
                    steps.append(
                        {
                            **deepcopy(definition),
                            "surface": surface,
                            "status": "pending",
                            "attempt": 1,
                            "_attemptId": self._new_attempt_id(),
                            "events": {},
                            "errors": [],
                            "latencyMs": None,
                            "match": None,
                            "heard": False,
                        }
                    )
            self._session = {
                "schema": SESSION_SCHEMA,
                "sessionId": session_id,
                "suite": SUITE_ID,
                "state": state,
                "surface": normalized_surfaces[0],
                "surfaces": normalized_surfaces,
                "currentStep": {},
                "attempt": 1,
                "capabilities": capability_map,
                "summary": {},
                "warnings": [],
                "createdAt": now,
                "updatedAt": now,
                "expiresAt": now + self.ttl_sec,
                "_surfaceIndex": 0,
                "_stepIndex": 0,
                "_steps": steps,
                "_seenEventIds": [],
            }
            if normalized_discord_target is not None:
                self._session["discordTarget"] = normalized_discord_target
            self._seen_event_ids = set()
            self._event_offset = 0
            self._sync_current_step()
            self._update_summary()
            self._persist()
            self.prune_reports()
            return {"ok": True, "session": self._public_session()}

    def resume_after_preflight(self, *, capabilities: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._expire_if_needed()
            if not self._session or self._session.get("state") != "preflight":
                result = {"ok": False, "error": "preflight_session_required"}
                if self._session:
                    result["session"] = self._public_session(capabilities=capabilities)
                return result
            requested = self._session.get("surfaces") or []
            blockers = []
            for surface in requested:
                capability = capabilities.get(
                    "voiceLocal" if surface == "local" else "voiceDiscord"
                ) or {}
                blockers.extend(capability.get("blockers") or [])
            self._session["capabilities"] = deepcopy(capabilities)
            if blockers:
                self._persist()
                return {"ok": False, "error": "capability_blocked", "blockers": blockers}
            self._session["state"] = "running"
            self._session["expiresAt"] = self.now() + self.ttl_sec
            self._persist()
            return {"ok": True, "session": self._public_session()}

    def _events_path(self) -> Path | None:
        if not self._session:
            return None
        return _session_artifact_path(
            self.paths.root.parent,
            self.paths.events,
            self._session.get("sessionId"),
            ".jsonl",
        )

    def _ingest_event_log(self) -> None:
        path = self._events_path()
        if path is None or not path.exists() or not self._session:
            return
        try:
            size = path.stat().st_size
            if self._event_offset > size:
                self._event_offset = 0
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(self._event_offset)
                while True:
                    position = handle.tell()
                    raw = handle.readline()
                    if not raw:
                        break
                    if not raw.endswith("\n"):
                        handle.seek(position)
                        break
                    try:
                        event = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(event, dict):
                        self._apply_event(sanitize_validation_event(event))
                self._event_offset = handle.tell()
        except OSError:
            return
        self._update_summary()
        self._persist()

    def record_event(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._expire_if_needed()
            if not self._session or self._session.get("state") != "running":
                result = {"ok": False, "error": "validation_session_not_running"}
                if self._session:
                    result["session"] = self._public_session()
                return result
            surface = str(event.get("surface") or self._session.get("surface") or "")
            step_id = str(
                event.get("stepId")
                or (self._session.get("currentStep") or {}).get("id")
                or ""
            )
            if not self._event_target_is_active(surface=surface, step_id=step_id):
                return {"ok": False, "error": "validation_event_step_not_active"}
            step = self._step_by_id(surface, step_id)
            supplied_attempt_id = str(
                event.get("attemptId") or event.get("attempt_id") or ""
            )
            if (
                step is None
                or not supplied_attempt_id
                or supplied_attempt_id != str(step.get("_attemptId") or "")
            ):
                return {
                    "ok": False,
                    "error": "validation_attempt_binding_mismatch",
                    "session": self._public_session(),
                }
            if "transcript" in event:
                event = {
                    **event,
                    **transcript_match(
                        event.get("transcript"),
                        step.get("prompt"),
                        keywords=step.get("keywords") or (),
                    ),
                }
            emitted = emit_voice_validation_event(
                surface,
                str(event.get("event") or "error"),
                root=self.paths.root.parent,
                session_id=str(self._session.get("sessionId") or ""),
                step_id=step_id,
                attempt_id=str(
                    step.get("_attemptId")
                    or ""
                ),
                now=self.now,
                **{
                    key: value
                    for key, value in event.items()
                    if key
                    not in {
                        "event",
                        "surface",
                        "stepId",
                        "sessionId",
                        "attempt_id",
                        "attemptId",
                    }
                },
            )
            if emitted is None:
                return {"ok": False, "error": "validation_event_rejected"}
            self._ingest_event_log()
            public_event = deepcopy(emitted)
            public_event.pop("attemptId", None)
            return {
                "ok": True,
                "event": public_event,
                "session": self._public_session(),
            }

    def _step_by_id(self, surface: str, step_id: str) -> dict[str, Any] | None:
        if not self._session:
            return None
        return next(
            (
                step
                for step in self._session.get("_steps") or []
                if step.get("surface") == surface and step.get("id") == step_id
            ),
            None,
        )

    def _event_target_is_active(self, *, surface: str, step_id: str) -> bool:
        if not self._session or self._session.get("state") != "running":
            return False
        current = self._session.get("currentStep") or {}
        if surface != str(self._session.get("surface") or ""):
            return False
        if step_id == str(current.get("id") or ""):
            return True
        return bool(
            current.get("kind") == "barge_source"
            and step_id == str(current.get("interruptStepId") or "")
        )

    def _step_is_current(self, step: dict[str, Any]) -> bool:
        if not self._session:
            return False
        current = self._session.get("currentStep") or {}
        return bool(
            step.get("surface") == self._session.get("surface")
            and step.get("id") == current.get("id")
        )

    def _apply_event(self, event: dict[str, Any]) -> None:
        if not self._session or event.get("sessionId") != self._session.get("sessionId"):
            return
        event_id = str(event.get("eventId") or "")
        surface = str(event.get("surface") or "")
        step_id = str(event.get("stepId") or "")
        if not self._event_target_is_active(surface=surface, step_id=step_id):
            return
        step = self._step_by_id(surface, step_id)
        if step is None:
            return
        attempt_id = str(event.get("attemptId") or "")
        if not attempt_id or attempt_id != str(step.get("_attemptId") or ""):
            return
        try:
            event_attempt = int(event.get("attempt"))
        except (TypeError, ValueError):
            return
        if event_attempt != int(step.get("attempt") or 1):
            return
        dedupe_key = f"{attempt_id}:{event_id}"
        if dedupe_key in self._seen_event_ids or event_id in self._seen_event_ids:
            return
        self._seen_event_ids.add(dedupe_key)
        event_type = str(event.get("event") or "")
        events = step.setdefault("events", {})
        events[event_type] = int(events.get(event_type) or 0) + 1
        if event_type == "silence_liveness":
            event_at = _finite_number(event.get("at"))
            started_at = _finite_number(step.get("silenceStartedAt"))
            current_time = self.now()
            ready = event.get("ready") is True
            if surface == "local":
                ready = bool(
                    ready
                    and event.get("bridgeReady") is True
                    and event.get("micEnabled") is True
                    and event.get("captureReady") is True
                )
                unavailable_code = "local_silence_capture_liveness_unavailable"
            else:
                ready = bool(
                    ready
                    and event.get("gatewayConnected") is True
                    and event.get("voiceConnected") is True
                    and event.get("listeningReady") is True
                    and event.get("targetMatched") is True
                )
                unavailable_code = "discord_silence_listening_liveness_unavailable"
            if (
                step.get("kind") != "silence"
                or event_at is None
                or started_at is None
                or event_at < started_at - _SILENCE_LIVENESS_CLOCK_SKEW_SEC
                or event_at > current_time + _SILENCE_LIVENESS_CLOCK_SKEW_SEC
            ):
                self._fail_attempt(step, "silence_liveness_timestamp_invalid")
            elif not ready:
                self._fail_attempt(step, unavailable_code)
            else:
                samples = [
                    float(value)
                    for value in step.get("_silenceLivenessSamples") or []
                    if _finite_number(value) is not None
                ]
                if event_at not in samples:
                    samples.append(event_at)
                samples = sorted(set(samples))
                if len(samples) > SILENCE_LIVENESS_SAMPLE_LIMIT:
                    self._fail_attempt(
                        step,
                        "silence_liveness_sample_limit_exceeded",
                    )
                else:
                    gaps = [
                        later - earlier
                        for earlier, later in zip(samples, samples[1:])
                    ]
                    step["_silenceLivenessSamples"] = samples
                    step["silenceLivenessFirstAt"] = samples[0]
                    step["silenceLivenessLastAt"] = samples[-1]
                    step["silenceLivenessMaxGapSec"] = max(gaps, default=0.0)
                    step["silenceLivenessReadyCount"] = len(samples)
        if event_type == "stt_final":
            step["match"] = {
                "matched": bool(event.get("matched")),
                "similarity": float(event.get("similarity") or 0.0),
                "keywordRatio": float(event.get("keywordRatio") or 0.0),
                "matchedKeywordCount": int(event.get("matchedKeywordCount") or 0),
                "requiredKeywordCount": int(event.get("requiredKeywordCount") or 0),
                "threshold": float(event.get("threshold") or 0.70),
            }
        if event_type == "turn_accepted" and event.get("turnId"):
            step["acceptedTurnId"] = str(event.get("turnId"))[:128]
        if (
            event_type == "tts_interrupt"
            and event.get("qualified") is True
            and event.get("sourceTurnId")
        ):
            step["qualifiedTtsInterruptTurnId"] = str(
                event.get("sourceTurnId")
            )[:128]
        latency = event.get("latencyMs")
        if isinstance(latency, (int, float)) and latency >= 0:
            step["latencyMs"] = round(float(latency), 1)
        if event_type in {"playback_failed", "error"}:
            error_code = str(event.get("errorCode") or event.get("reason") or event_type)
            if error_code not in step["errors"]:
                step["errors"].append(error_code[:120])
        if event_type in {"playback_completed", "playback_cancelled"}:
            step.setdefault("terminalEventObservedAt", self.now())
        self._evaluate_step(step)

    @staticmethod
    def _event_count(step: dict[str, Any], event: str) -> int:
        return int((step.get("events") or {}).get(event) or 0)

    def _terminal_machine_evidence_complete(self, step: dict[str, Any]) -> bool:
        kind = str(step.get("kind") or "")
        stt_finals = self._event_count(step, "stt_final")
        accepted = self._event_count(step, "turn_accepted")
        reply_started = self._event_count(step, "reply_started")
        replies = self._event_count(step, "reply_final")
        started = self._event_count(step, "playback_started")
        completed = self._event_count(step, "playback_completed")
        cancelled = self._event_count(step, "playback_cancelled")
        interrupt = self._event_count(step, "barge_in_accepted")
        qualified_tts_interrupt = self._event_count(step, "tts_interrupt")
        continuity = self._event_count(step, "barge_in_continuity")
        match_ok = bool((step.get("match") or {}).get("matched"))
        qualified_interrupt_matches = bool(
            step.get("acceptedTurnId")
            and step.get("qualifiedTtsInterruptTurnId")
            == step.get("acceptedTurnId")
        )
        if kind == "normal":
            return bool(
                stt_finals
                == accepted
                == reply_started
                == replies
                == started
                == completed
                == 1
                and cancelled == interrupt == qualified_tts_interrupt == continuity == 0
                and match_ok
            )
        if kind == "barge_source":
            return bool(
                stt_finals == accepted == reply_started == started == cancelled == 1
                and replies in {0, 1}
                and completed == interrupt == continuity == 0
                and qualified_tts_interrupt == 1
                and qualified_interrupt_matches
                and match_ok
            )
        if kind == "barge_interrupt":
            return bool(
                stt_finals
                == accepted
                == reply_started
                == replies
                == started
                == completed
                == interrupt
                == continuity
                == 1
                and cancelled == qualified_tts_interrupt == 0
                and match_ok
            )
        return False

    def _evaluate_step(self, step: dict[str, Any]) -> None:
        if step.get("status") in {"passed", "failed"}:
            return
        kind = step.get("kind")
        accepted = self._event_count(step, "turn_accepted")
        reply_started = self._event_count(step, "reply_started")
        replies = self._event_count(step, "reply_final")
        stt_finals = self._event_count(step, "stt_final")
        started = self._event_count(step, "playback_started")
        completed = self._event_count(step, "playback_completed")
        cancelled = self._event_count(step, "playback_cancelled")
        interrupt = self._event_count(step, "barge_in_accepted")
        qualified_tts_interrupt = self._event_count(step, "tts_interrupt")
        continuity = self._event_count(step, "barge_in_continuity")
        qualified_interrupt_matches = bool(
            step.get("acceptedTurnId")
            and step.get("qualifiedTtsInterruptTurnId")
            == step.get("acceptedTurnId")
        )
        failed = self._event_count(step, "playback_failed") + self._event_count(step, "error")
        if (
            stt_finals > 1
            or accepted > 1
            or reply_started > 1
            or replies > 1
            or started > 1
            or completed > 1
            or cancelled > 1
            or interrupt > 1
            or qualified_tts_interrupt > 1
            or continuity > 1
        ):
            self._fail_attempt(step, "duplicate_turn_or_playback")
        elif failed:
            self._fail_attempt(
                step,
                str(step["errors"][-1] if step.get("errors") else "unhandled_voice_error"),
            )
        elif completed and cancelled:
            self._fail_attempt(step, "conflicting_playback_terminal_events")
        elif stt_finals == 1 and not bool((step.get("match") or {}).get("matched")):
            self._fail_attempt(step, "stt_mismatch")
        elif (
            completed + cancelled == 1
            and self.now() - float(step.get("terminalEventObservedAt") or self.now())
            >= EVENT_REORDER_GRACE_SEC
            and not self._terminal_machine_evidence_complete(step)
        ):
            self._fail_attempt(
                step,
                "orphan_or_incomplete_cancelled_playback"
                if cancelled == 1
                else "orphan_or_incomplete_playback",
            )
        elif kind == "normal":
            if self._terminal_machine_evidence_complete(step) and step.get("heard"):
                step["status"] = "passed"
        elif kind == "barge_source":
            if self._terminal_machine_evidence_complete(step):
                step["status"] = "passed"
        elif kind == "barge_interrupt":
            if self._terminal_machine_evidence_complete(step) and step.get("heard"):
                step["status"] = "passed"
        elif kind == "silence":
            silence_completed = self._event_count(step, "silence_completed")
            voice_activity = (
                stt_finals
                + accepted
                + reply_started
                + replies
                + started
                + completed
                + cancelled
                + interrupt
                + qualified_tts_interrupt
                + continuity
            )
            if voice_activity:
                self._fail_attempt(step, "silence_activity_detected")
            elif silence_completed == 1 and _silence_liveness_is_complete(step):
                step["status"] = "passed"
        if step.get("status") == "passed" and self._step_is_current(step):
            self._advance()

    def _fail_attempt(self, step: dict[str, Any], error_code: str) -> None:
        code = str(error_code or "step_failed")[:120]
        step["status"] = "failed"
        if code not in step.setdefault("errors", []):
            step["errors"].append(code)
        if self._session is not None:
            self._session["lastFailureCode"] = code
            if int(step.get("attempt") or 1) >= MAX_ATTEMPTS:
                self._session["state"] = "failed"
                self._session["failureCode"] = code
                self._session["completedAt"] = self.now()
                self._finalize_report()

    def _reevaluate_current_step(self) -> None:
        if not self._session or self._session.get("state") != "running":
            return
        current = self._session.get("currentStep") or {}
        step = self._step_by_id(
            str(self._session.get("surface") or ""),
            str(current.get("id") or ""),
        )
        if step is not None:
            before = (step.get("status"), tuple(step.get("errors") or ()))
            self._evaluate_step(step)
            after = (step.get("status"), tuple(step.get("errors") or ()))
            if after != before:
                self._update_summary()
                self._persist()

    def confirm(
        self,
        *,
        session_id: str,
        step_id: str,
        heard: bool,
        attempt: Any = _ATTEMPT_REVISION_OMITTED,
    ) -> dict[str, Any]:
        if type(heard) is not bool:
            return {"ok": False, "error": "heard_boolean_required"}
        with self._lock:
            self._expire_if_needed()
            self._ingest_event_log()
            if not self._session or session_id != self._session.get("sessionId"):
                return {"ok": False, "error": "validation_session_not_found"}
            if self._session.get("state") != "running":
                return {
                    "ok": False,
                    "error": "validation_session_not_running",
                    "session": self._public_session(),
                }
            current = self._session.get("currentStep") or {}
            if step_id != current.get("id"):
                return {"ok": False, "error": "validation_step_not_current"}
            step = self._step_by_id(str(self._session.get("surface") or ""), step_id)
            if step is None:
                return {"ok": False, "error": "validation_step_not_found"}
            current_attempt = int(step.get("attempt") or 1)
            omitted_first_attempt = (
                attempt is _ATTEMPT_REVISION_OMITTED and current_attempt == 1
            )
            if not omitted_first_attempt and (
                _parse_attempt_revision(attempt) != current_attempt
            ):
                return {
                    "ok": False,
                    "error": "validation_attempt_revision_mismatch",
                    "session": self._public_session(),
                }
            if step.get("kind") not in {"normal", "barge_interrupt"}:
                return {"ok": False, "error": "heard_confirmation_not_applicable"}
            if step.get("status") == "failed":
                return {"ok": False, "error": "validation_step_failed"}
            if (
                self._event_count(step, "playback_started") != 1
                or self._event_count(step, "playback_completed") != 1
            ):
                return {"ok": False, "error": "playback_not_completed"}
            step["heard"] = bool(heard)
            if not heard:
                self._fail_attempt(step, "user_did_not_hear_playback")
            self._evaluate_step(step)
            self._update_summary()
            self._persist()
            return {"ok": True, "session": self._public_session()}

    def retry(
        self,
        *,
        session_id: str,
        step_id: str,
        attempt: int | None,
    ) -> dict[str, Any]:
        with self._lock:
            self._expire_if_needed()
            if not self._session or session_id != self._session.get("sessionId"):
                return {"ok": False, "error": "validation_session_not_found"}
            if self._session.get("state") != "running":
                return {
                    "ok": False,
                    "error": "validation_session_not_running",
                    "session": self._public_session(),
                }
            current = self._session.get("currentStep") or {}
            if step_id != current.get("id"):
                return {"ok": False, "error": "validation_step_not_current"}
            step = self._step_by_id(str(self._session.get("surface") or ""), step_id)
            if step is None:
                return {"ok": False, "error": "validation_step_not_found"}
            current_attempt = int(step.get("attempt") or 1)
            if _parse_attempt_revision(attempt) != current_attempt:
                return {
                    "ok": False,
                    "error": "validation_attempt_revision_mismatch",
                    "session": self._public_session(),
                }
            if step.get("status") != "failed":
                return {
                    "ok": False,
                    "error": "validation_step_not_failed",
                    "session": self._public_session(),
                }
            rewind_source: dict[str, Any] | None = None
            rewind_source_index: int | None = None
            if step.get("kind") == "barge_interrupt":
                steps = self._session.get("_steps") or []
                current_index = int(self._session.get("_stepIndex") or 0)
                if current_index > 0:
                    candidate = steps[current_index - 1]
                    if (
                        candidate.get("surface") == step.get("surface")
                        and candidate.get("kind") == "barge_source"
                    ):
                        rewind_source = candidate
                        rewind_source_index = current_index - 1
            if current_attempt >= MAX_ATTEMPTS or (
                rewind_source is not None
                and int(rewind_source.get("attempt") or 1) >= MAX_ATTEMPTS
            ):
                step["status"] = "failed"
                step["errors"].append("attempt_budget_exhausted")
                self._session["state"] = "failed"
                self._session["failureCode"] = "attempt_budget_exhausted"
                self._session["completedAt"] = self.now()
                self._finalize_report()
                return {"ok": False, "error": "attempt_budget_exhausted", "session": self._public_session()}
            step.update(
                {
                    "attempt": current_attempt + 1,
                    "_attemptId": self._new_attempt_id(),
                    "status": "pending",
                    "events": {},
                    "errors": [],
                    "latencyMs": None,
                    "match": None,
                    "heard": False,
                }
            )
            step.pop("terminalEventObservedAt", None)
            step.pop("acceptedTurnId", None)
            step.pop("qualifiedTtsInterruptTurnId", None)
            if step.get("kind") == "silence":
                for key in _SILENCE_LIVENESS_STEP_KEYS:
                    step.pop(key, None)
            if rewind_source is not None and rewind_source_index is not None:
                rewind_source.update(
                    {
                        "attempt": int(rewind_source.get("attempt") or 1) + 1,
                        "_attemptId": self._new_attempt_id(),
                        "status": "pending",
                        "events": {},
                        "errors": [],
                        "latencyMs": None,
                        "match": None,
                        "heard": False,
                    }
                )
                rewind_source.pop("terminalEventObservedAt", None)
                rewind_source.pop("acceptedTurnId", None)
                rewind_source.pop("qualifiedTtsInterruptTurnId", None)
                self._session["_stepIndex"] = rewind_source_index
            if step.get("kind") == "barge_source":
                interrupt_step_id = str(current.get("interruptStepId") or "")
                paired = self._step_by_id(
                    str(self._session.get("surface") or ""),
                    interrupt_step_id,
                )
                if paired is not None:
                    paired.update(
                        {
                            "_attemptId": self._new_attempt_id(),
                            "status": "pending",
                            "events": {},
                            "errors": [],
                            "latencyMs": None,
                            "match": None,
                            "heard": False,
                        }
                    )
                    paired.pop("terminalEventObservedAt", None)
                    paired.pop("acceptedTurnId", None)
                    paired.pop("qualifiedTtsInterruptTurnId", None)
            self._session["state"] = "running"
            active_retry_step = rewind_source if rewind_source is not None else step
            self._session["attempt"] = int(active_retry_step.get("attempt") or 1)
            self._session.pop("failureCode", None)
            self._session.pop("lastFailureCode", None)
            self._sync_current_step()
            self._update_summary()
            self._persist()
            return {"ok": True, "session": self._public_session()}

    def abort(self, *, session_id: str) -> dict[str, Any]:
        with self._lock:
            self._expire_if_needed()
            if not self._session or session_id != self._session.get("sessionId"):
                return {"ok": False, "error": "validation_session_not_found"}
            if self._session.get("state") in TERMINAL_STATES:
                return {
                    "ok": False,
                    "error": "validation_session_terminal",
                    "session": self._public_session(),
                }
            self._session["state"] = "aborted"
            self._session["completedAt"] = self.now()
            self._finalize_report()
            return {"ok": True, "session": self._public_session()}

    def _advance(self) -> None:
        if not self._session:
            return
        steps = self._session.get("_steps") or []
        current_surface = str(self._session.get("surface") or "")
        current_index = next(
            (
                index
                for index, step in enumerate(steps)
                if step.get("surface") == current_surface
                and step.get("id") == (self._session.get("currentStep") or {}).get("id")
            ),
            -1,
        )
        failed_index = next(
            (
                index
                for index, step in enumerate(steps)
                if step.get("status") == "failed"
            ),
            None,
        )
        if failed_index is not None:
            self._session["_stepIndex"] = failed_index
            self._session["surface"] = steps[failed_index].get("surface")
            self._session["attempt"] = int(steps[failed_index].get("attempt") or 1)
            self._sync_current_step()
            self._update_summary()
            self._persist()
            return
        next_index = next(
            (
                index
                for index in range(current_index + 1, len(steps))
                if steps[index].get("status") != "passed"
            ),
            None,
        )
        if next_index is None:
            all_passed = bool(steps) and all(
                step.get("status") == "passed" for step in steps
            )
            self._session["state"] = "passed" if all_passed else "failed"
            if not all_passed:
                self._session["failureCode"] = "incomplete_validation_suite"
                self._session["lastFailureCode"] = "incomplete_validation_suite"
            self._session["completedAt"] = self.now()
            self._finalize_report()
            return
        self._session["_stepIndex"] = next_index
        self._session["surface"] = steps[next_index].get("surface")
        self._session["attempt"] = int(steps[next_index].get("attempt") or 1)
        self._sync_current_step()
        self._update_summary()
        self._persist()

    def _sync_current_step(self) -> None:
        if not self._session:
            return
        steps = self._session.get("_steps") or []
        index = int(self._session.get("_stepIndex") or 0)
        step = steps[index] if 0 <= index < len(steps) else {}
        surfaces = self._session.get("surfaces") or []
        if step.get("surface") in surfaces:
            self._session["_surfaceIndex"] = surfaces.index(step.get("surface"))
        if step.get("kind") == "silence" and not step.get("silenceStartedAt"):
            step["silenceStartedAt"] = self.now()
            events = step.setdefault("events", {})
            events["silence_started"] = 1
        current = {
            key: deepcopy(step.get(key))
            for key in (
                "id",
                "kind",
                "prompt",
                "silenceSec",
                "silenceStartedAt",
                "silenceCompletedAt",
                "silenceLivenessFirstAt",
                "silenceLivenessLastAt",
                "silenceLivenessMaxGapSec",
                "silenceLivenessReadyCount",
                "surface",
                "status",
                "attempt",
                "events",
                "errors",
                "latencyMs",
                "match",
                "heard",
            )
        }
        if step.get("kind") == "barge_source" and index + 1 < len(steps):
            paired = steps[index + 1]
            current["interruptStepId"] = paired.get("id")
            current["interruptPrompt"] = paired.get("prompt")
        self._session["currentStep"] = current

    def _refresh_silence_step(self) -> None:
        if not self._session or self._session.get("state") != "running":
            return
        current = self._session.get("currentStep") or {}
        if current.get("kind") != "silence":
            return
        step = self._step_by_id(
            str(self._session.get("surface") or ""),
            str(current.get("id") or ""),
        )
        if step is None or step.get("status") in {"passed", "failed"}:
            return
        started_at = float(step.get("silenceStartedAt") or self.now())
        silence_sec = max(1.0, float(step.get("silenceSec") or 15.0))
        boundary_at = self.now()
        if boundary_at - started_at < silence_sec:
            return
        if any(
            self._event_count(step, event)
            for event in (
                "stt_final",
                "turn_accepted",
                "reply_started",
                "reply_final",
                "playback_started",
                "playback_completed",
                "playback_cancelled",
                "barge_in_accepted",
                "tts_interrupt",
                "barge_in_continuity",
            )
        ):
            self._fail_attempt(step, "silence_activity_detected")
            self._update_summary()
            self._persist()
            return
        if not _silence_liveness_is_complete(step, boundary_at=boundary_at):
            self._fail_attempt(
                step,
                f"{step.get('surface')}_silence_liveness_unproven",
            )
            self._update_summary()
            self._persist()
            return
        events = step.setdefault("events", {})
        events["silence_completed"] = 1
        step["silenceCompletedAt"] = boundary_at
        self._evaluate_step(step)
        self._update_summary()
        self._persist()

    def _update_summary(self) -> None:
        if not self._session:
            return
        steps = self._session.get("_steps") or []
        voice_steps = [step for step in steps if step.get("kind") != "silence"]
        passed_steps = [step for step in steps if step.get("status") == "passed"]
        surfaces = list(self._session.get("surfaces") or [])
        surface_passed = sum(
            1
            for surface in surfaces
            if all(
                step.get("status") == "passed"
                for step in steps
                if step.get("surface") == surface
            )
        )
        latencies = {
            surface: [
                float(step["latencyMs"])
                for step in voice_steps
                if step.get("surface") == surface and isinstance(step.get("latencyMs"), (int, float))
            ]
            for surface in surfaces
        }
        latency_summary: dict[str, Any] = {}
        warnings: list[dict[str, Any]] = []
        for surface, values in latencies.items():
            ordered = sorted(values)
            if not ordered:
                latency_summary[surface] = {"count": 0, "p50Ms": None, "p95Ms": None}
                continue
            p50 = statistics.median(ordered)
            p95_index = max(0, min(len(ordered) - 1, int(round(0.95 * len(ordered) + 0.5)) - 1))
            p95 = ordered[p95_index]
            latency_summary[surface] = {
                "count": len(ordered),
                "p50Ms": round(float(p50), 1),
                "p95Ms": round(float(p95), 1),
            }
            threshold = LATENCY_WARNING_MS[surface]
            if p95 > threshold:
                warnings.append(
                    {
                        "code": f"{surface}_latency_p95_high",
                        "surface": surface,
                        "p95Ms": round(float(p95), 1),
                        "thresholdMs": threshold,
                    }
                )
        self._session["warnings"] = warnings
        self._session["summary"] = {
            "surfacesPassed": surface_passed,
            "surfacesTotal": len(surfaces),
            "stepsPassed": len(passed_steps),
            "stepsTotal": len(steps),
            "acceptedTurnsPassed": len(
                [step for step in voice_steps if step.get("status") == "passed"]
            ),
            "acceptedTurnsTotal": len(voice_steps),
            "latency": latency_summary,
        }
        self._sync_current_step()

    def _report_payload(self) -> dict[str, Any]:
        assert self._session is not None
        step_reports = []
        for step in self._session.get("_steps") or []:
            step_reports.append(
                {
                    "stepId": step.get("id"),
                    "surface": step.get("surface"),
                    "kind": step.get("kind"),
                    "attempt": step.get("attempt"),
                    "passed": step.get("status") == "passed",
                    "status": step.get("status"),
                    "errorCodes": list(step.get("errors") or []),
                    "match": deepcopy(step.get("match")),
                    "latencyMs": step.get("latencyMs"),
                    "reply": {
                        "started": self._event_count(step, "reply_started"),
                        "final": self._event_count(step, "reply_final"),
                    },
                    "interrupt": {
                        "qualifiedTts": self._event_count(step, "tts_interrupt"),
                        "sourceTurnMatched": bool(
                            step.get("acceptedTurnId")
                            and step.get("qualifiedTtsInterruptTurnId")
                            == step.get("acceptedTurnId")
                        ),
                    },
                    "playback": {
                        "started": self._event_count(step, "playback_started"),
                        "completed": self._event_count(step, "playback_completed"),
                        "cancelled": self._event_count(step, "playback_cancelled"),
                        "failed": self._event_count(step, "playback_failed"),
                    },
                    "heard": bool(step.get("heard")),
                }
            )
        return {
            "schema": "voice_validation.report.v1",
            "sessionId": self._session.get("sessionId"),
            "suite": self._session.get("suite"),
            "state": self._session.get("state"),
            "createdAt": self._session.get("createdAt"),
            "completedAt": self._session.get("completedAt"),
            "summary": deepcopy(self._session.get("summary") or {}),
            "warnings": deepcopy(self._session.get("warnings") or []),
            "steps": step_reports,
            "privacy": {"rawAudioStored": False, "transcriptStored": False},
        }

    def _finalize_report(self) -> None:
        if not self._session:
            return
        self._update_summary()
        report_path = _session_artifact_path(
            self.paths.root.parent,
            self.paths.reports,
            self._session.get("sessionId"),
            ".json",
        )
        if report_path is None:
            self._persist()
            return
        _atomic_json_write(report_path, self._report_payload())
        self._persist()
        self.prune_reports()

    def prune_reports(self) -> list[str]:
        reports_dir = _contained_validation_path(
            self.paths.root.parent,
            self.paths.reports,
        )
        if reports_dir is None:
            return []
        reports_dir.mkdir(parents=True, exist_ok=True)
        now = self.now()
        report_rows: list[tuple[Path, float]] = []
        for path in reports_dir.glob("*.json"):
            if _contained_validation_path(self.paths.root.parent, path) is None:
                continue
            try:
                report_rows.append((path, path.stat().st_mtime))
            except OSError:
                continue
        reports = [
            path
            for path, _mtime in sorted(
                report_rows,
                key=lambda item: item[1],
                reverse=True,
            )
        ]
        removed: list[str] = []
        for index, path in enumerate(reports):
            try:
                age_days = max(0.0, now - path.stat().st_mtime) / 86400.0
            except OSError:
                continue
            if index < REPORT_PRESERVE_NEWEST and age_days <= REPORT_MAX_AGE_DAYS:
                continue
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                continue
        return removed


_MANAGERS: dict[str, VoiceValidationManager] = {}
_MANAGERS_LOCK = threading.Lock()


def get_voice_validation_manager(*, root: Path | None = None) -> VoiceValidationManager:
    resolved = Path(root or get_runtime_artifacts_root()).resolve()
    key = str(resolved).lower()
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            manager = VoiceValidationManager(root=resolved)
            _MANAGERS[key] = manager
        return manager


def observe_turn_trace_for_voice_validation(event: str, payload: dict[str, Any]) -> None:
    if event not in {
        "voice_turn_summary",
        "voice_drop_summary",
        "tts_interrupt",
        "barge_in_continuity",
    }:
        return
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    session_id = str(
        payload.get("validation_session_id")
        or meta.get("validation_session_id")
        or ""
    )
    step_id = str(
        payload.get("validation_step_id")
        or meta.get("validation_step_id")
        or ""
    )
    attempt_id = str(
        payload.get("validation_attempt_id")
        or meta.get("validation_attempt_id")
        or ""
    )
    turn_id = payload.get("turn_id") or meta.get("turn_id")
    if event == "voice_turn_summary" and session_id and step_id and attempt_id:
        common = {
            "session_id": session_id,
            "step_id": step_id,
            "attempt_id": attempt_id,
            "turnId": turn_id,
        }
        summary_error = str(payload.get("error") or "").strip()
        if payload.get("playback_failed") is True:
            emit_voice_validation_event(
                "discord",
                "playback_failed",
                errorCode=summary_error or "tts_playback_failed",
                **common,
            )
            return
        active_context = active_validation_context(surface="discord")
        intentional_barge_cancel = bool(
            summary_error == "cancelled"
            and active_context
            and active_context.get("sessionId") == session_id
            and active_context.get("stepId") == step_id
            and active_context.get("attemptId") == attempt_id
            and active_context.get("kind") == "barge_source"
            and payload.get("validation_transcript_match") is True
            and payload.get("turn_accepted") is True
            and payload.get("qualified_tts_interrupt") is True
            and payload.get("reply_started") is True
            and payload.get("playback_started") is True
            and payload.get("playback_completed") is not True
            and payload.get("playback_cancelled") is True
        )
        if summary_error and not intentional_barge_cancel:
            emit_voice_validation_event(
                "discord",
                "error",
                errorCode=summary_error,
                **common,
            )
            return
        if payload.get("validation_transcript_match") is not True:
            emit_voice_validation_event(
                "discord",
                "error",
                errorCode="validation_transcript_not_matched",
                **common,
            )
            return
        if payload.get("turn_accepted") is not True:
            emit_voice_validation_event(
                "discord",
                "error",
                errorCode="voice_turn_acceptance_unproven",
                **common,
            )
            return
        if payload.get("reply_final") is not True and not intentional_barge_cancel:
            emit_voice_validation_event(
                "discord",
                "error",
                errorCode="voice_delivery_empty",
                **common,
            )
            return
        playback_started = payload.get("playback_started") is True
        playback_completed = payload.get("playback_completed") is True
        playback_cancelled = payload.get("playback_cancelled") is True
        if not playback_started or playback_completed == playback_cancelled:
            emit_voice_validation_event(
                "discord",
                "playback_failed",
                errorCode=(
                    "conflicting_playback_terminal_events"
                    if playback_completed and playback_cancelled
                    else "tts_playback_failed"
                ),
                **common,
            )
            return
        emit_voice_validation_event("discord", "turn_accepted", **common)
        if intentional_barge_cancel:
            emit_voice_validation_event(
                "discord",
                "tts_interrupt",
                qualified=True,
                sourceTurnId=turn_id,
                reason="qualified_user_audio",
                **common,
            )
        if payload.get("reply_started") is True:
            emit_voice_validation_event("discord", "reply_started", **common)
        if payload.get("reply_final") is True:
            emit_voice_validation_event("discord", "reply_final", **common)
        emit_voice_validation_event(
            "discord",
            "playback_started",
            latencyMs=payload.get("playback_first_packet_ms")
            if payload.get("playback_first_packet_ms") is not None
            else payload.get("total_ms"),
            **common,
        )
        if playback_completed:
            emit_voice_validation_event("discord", "playback_completed", **common)
        if playback_cancelled:
            emit_voice_validation_event("discord", "playback_cancelled", **common)
        return
    if event == "voice_drop_summary" and session_id and step_id and attempt_id:
        drop_reason = str(
            payload.get("drop_reason") or payload.get("error") or "voice_drop"
        ).strip().lower()
        silence_context = active_validation_context(surface="discord")
        if (
            silence_context
            and silence_context.get("kind") == "silence"
            and silence_context.get("sessionId") == session_id
            and silence_context.get("stepId") == step_id
            and silence_context.get("attemptId") == attempt_id
            and payload.get("turn_accepted") is False
            and drop_reason in SILENCE_NON_ACCEPTED_DROP_REASONS
        ):
            return
        emit_voice_validation_event(
            "discord",
            "error",
            session_id=session_id,
            step_id=step_id,
            attempt_id=attempt_id,
            turnId=turn_id,
            errorCode=drop_reason,
        )
        return
    if event == "tts_interrupt":
        source_context = active_validation_context(surface="discord")
        interrupt_context = active_validation_context(
            surface="discord",
            prefer_interrupt=True,
        )
        source_turn_id = str(
            payload.get("source_turn_id") or payload.get("turn_id") or ""
        ).strip()
        if (
            source_context
            and source_context.get("kind") == "barge_source"
            and source_context.get("sessionId") == session_id
            and source_context.get("stepId") == step_id
            and source_context.get("attemptId") == attempt_id
            and str(source_context.get("guildId") or "")
            == str(payload.get("guild_id") or "")
            and str(source_context.get("turnId") or "") == source_turn_id
            and interrupt_context
            and interrupt_context.get("kind") == "barge_interrupt"
            and interrupt_context.get("sessionId") == session_id
            and payload.get("qualified") is True
            and payload.get("reason") == "qualified_user_audio"
            and source_turn_id
        ):
            emit_voice_validation_event(
                "discord",
                "barge_in_accepted",
                session_id=interrupt_context["sessionId"],
                step_id=interrupt_context["stepId"],
                attempt_id=interrupt_context.get("attemptId"),
                turnId=source_turn_id,
                reason="qualified_user_audio",
            )
        return
    if event == "barge_in_continuity":
        context = active_validation_context(surface="discord", prefer_interrupt=True)
        if (
            not context
            or context.get("kind") != "barge_interrupt"
            or context.get("sessionId") != session_id
            or context.get("stepId") != step_id
            or context.get("attemptId") != attempt_id
        ):
            return
        status = str(payload.get("status") or "").strip().lower()
        success = status == "success" or bool(payload.get("success"))
        emit_voice_validation_event(
            "discord",
            "barge_in_continuity" if success else "error",
            session_id=context["sessionId"],
            step_id=context["stepId"],
            attempt_id=context.get("attemptId"),
            turnId=turn_id,
            errorCode=None if success else "barge_in_continuity_failed",
            reason=payload.get("reason_code") or payload.get("reason") or status,
        )


__all__ = [
    "ALLOWED_SURFACES",
    "MAX_ATTEMPTS",
    "SESSION_SCHEMA",
    "SILENCE_NON_ACCEPTED_DROP_REASONS",
    "SUITE_ID",
    "VoiceValidationManager",
    "active_validation_context",
    "emit_silence_liveness_event",
    "emit_voice_validation_event",
    "emit_transcript_validation_event",
    "get_voice_validation_manager",
    "normalize_validation_text",
    "observe_turn_trace_for_voice_validation",
    "sanitize_validation_event",
    "resolve_discord_validation_target",
    "transcript_match",
    "validation_attempt_binding_is_current",
    "validation_transcript_admission_status",
]
