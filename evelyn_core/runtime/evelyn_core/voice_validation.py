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
_ALLOWED_EVENT_TYPES = frozenset(
    {
        "capture",
        "stt_final",
        "turn_accepted",
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
        "error",
    }
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


def active_validation_context(
    *,
    surface: str,
    root: Path | None = None,
    prefer_interrupt: bool = False,
    now: Any | None = None,
) -> dict[str, str] | None:
    base = Path(root or get_runtime_artifacts_root()) / "voice_validation"
    active = _safe_json_read(base / "active.json")
    if not active or active.get("state") != "running" or active.get("surface") != surface:
        return None
    current_time = (now or time.time)()
    if _session_expiry_state(active, now=current_time) != "active":
        return None
    current = active.get("currentStep")
    if not isinstance(current, dict) or not current.get("id"):
        return None
    step_id = (
        str(current.get("interruptStepId") or "")
        if prefer_interrupt and current.get("interruptStepId")
        else str(current.get("id") or "")
    )
    return {
        "sessionId": str(active.get("sessionId") or ""),
        "stepId": step_id,
        "surface": surface,
    }


def emit_voice_validation_event(
    surface: str,
    event: str,
    *,
    root: Path | None = None,
    session_id: str | None = None,
    step_id: str | None = None,
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
        }
    )
    events_path = base / "events" / f"{resolved_session_id}.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
    return record


def emit_transcript_validation_event(
    surface: str,
    transcript: Any,
    *,
    root: Path | None = None,
    session_id: str | None = None,
    step_id: str | None = None,
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
    return emit_voice_validation_event(
        normalized_surface,
        "stt_final",
        root=base_root,
        session_id=resolved_session_id,
        step_id=resolved_step_id,
        **payload,
        **match,
    )


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
            payload = _safe_json_read(self.paths.active)
            if not payload or payload.get("schema") != SESSION_SCHEMA:
                self._session = None
                return
            self._session = payload
            self._event_offset = 0
            self._seen_event_ids = set(str(item) for item in payload.get("_seenEventIds") or [])
            self._expire_if_needed()
            self._ingest_event_log()

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
        self._session["updatedAt"] = self.now()
        self._session["_seenEventIds"] = list(sorted(self._seen_event_ids))[-1000:]
        _atomic_json_write(self.paths.active, self._session)

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
        return self.paths.events / f"{self._session.get('sessionId')}.jsonl"

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
            if "transcript" in event:
                step = self._step_by_id(surface, step_id)
                if step is not None:
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
                now=self.now,
                **{
                    key: value
                    for key, value in event.items()
                    if key not in {"event", "surface", "stepId", "sessionId"}
                },
            )
            if emitted is None:
                return {"ok": False, "error": "validation_event_rejected"}
            self._ingest_event_log()
            return {"ok": True, "event": emitted, "session": self._public_session()}

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
        if event_id in self._seen_event_ids:
            return
        self._seen_event_ids.add(event_id)
        surface = str(event.get("surface") or "")
        step_id = str(event.get("stepId") or "")
        if not self._event_target_is_active(surface=surface, step_id=step_id):
            return
        step = self._step_by_id(surface, step_id)
        if step is None:
            return
        event_type = str(event.get("event") or "")
        events = step.setdefault("events", {})
        events[event_type] = int(events.get(event_type) or 0) + 1
        if event_type == "stt_final":
            step["match"] = {
                "matched": bool(event.get("matched")),
                "similarity": float(event.get("similarity") or 0.0),
                "keywordRatio": float(event.get("keywordRatio") or 0.0),
                "matchedKeywordCount": int(event.get("matchedKeywordCount") or 0),
                "requiredKeywordCount": int(event.get("requiredKeywordCount") or 0),
                "threshold": float(event.get("threshold") or 0.70),
            }
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

    def _evaluate_step(self, step: dict[str, Any]) -> None:
        if step.get("status") in {"passed", "failed"}:
            return
        kind = step.get("kind")
        accepted = self._event_count(step, "turn_accepted")
        replies = self._event_count(step, "reply_final")
        stt_finals = self._event_count(step, "stt_final")
        started = self._event_count(step, "playback_started")
        completed = self._event_count(step, "playback_completed")
        cancelled = self._event_count(step, "playback_cancelled")
        interrupt = self._event_count(step, "barge_in_accepted")
        continuity = self._event_count(step, "barge_in_continuity")
        failed = self._event_count(step, "playback_failed") + self._event_count(step, "error")
        if (
            stt_finals > 1
            or accepted > 1
            or replies > 1
            or started > 1
            or completed > 1
            or cancelled > 1
            or interrupt > 1
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
            completed == 1
            and (accepted != 1 or replies != 1 or started != 1)
            and self.now() - float(step.get("terminalEventObservedAt") or self.now())
            >= EVENT_REORDER_GRACE_SEC
        ):
            self._fail_attempt(step, "orphan_or_incomplete_playback")
        elif (
            cancelled == 1
            and (accepted != 1 or replies != 1 or started != 1)
            and self.now() - float(step.get("terminalEventObservedAt") or self.now())
            >= EVENT_REORDER_GRACE_SEC
        ):
            self._fail_attempt(step, "orphan_or_incomplete_cancelled_playback")
        elif kind == "normal":
            match_ok = bool((step.get("match") or {}).get("matched"))
            if (
                accepted == replies == started == completed == 1
                and cancelled == interrupt == continuity == 0
                and match_ok
                and step.get("heard")
            ):
                step["status"] = "passed"
        elif kind == "barge_source":
            if (
                accepted == replies == started == cancelled == 1
                and completed == interrupt == continuity == 0
                and bool((step.get("match") or {}).get("matched"))
            ):
                step["status"] = "passed"
        elif kind == "barge_interrupt":
            if (
                accepted == replies == started == completed == interrupt == continuity == 1
                and cancelled == 0
                and bool((step.get("match") or {}).get("matched"))
                and step.get("heard")
            ):
                step["status"] = "passed"
        elif kind == "silence":
            silence_completed = self._event_count(step, "silence_completed")
            voice_activity = (
                stt_finals
                + accepted
                + replies
                + started
                + completed
                + cancelled
                + interrupt
                + continuity
            )
            if voice_activity:
                self._fail_attempt(step, "silence_activity_detected")
            elif silence_completed == 1:
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

    def confirm(self, *, session_id: str, step_id: str, heard: bool) -> dict[str, Any]:
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

    def retry(self, *, session_id: str, step_id: str) -> dict[str, Any]:
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
            attempt = int(step.get("attempt") or 1)
            if attempt >= MAX_ATTEMPTS:
                step["status"] = "failed"
                step["errors"].append("attempt_budget_exhausted")
                self._session["state"] = "failed"
                self._session["failureCode"] = "attempt_budget_exhausted"
                self._session["completedAt"] = self.now()
                self._finalize_report()
                return {"ok": False, "error": "attempt_budget_exhausted", "session": self._public_session()}
            step.update(
                {
                    "attempt": attempt + 1,
                    "status": "pending",
                    "events": {},
                    "errors": [],
                    "latencyMs": None,
                    "match": None,
                    "heard": False,
                }
            )
            step.pop("terminalEventObservedAt", None)
            self._session["state"] = "running"
            self._session["attempt"] = attempt + 1
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
        next_index = next(
            (
                index
                for index in range(current_index + 1, len(steps))
                if steps[index].get("status") not in {"passed", "failed"}
            ),
            None,
        )
        if next_index is None:
            self._session["state"] = "passed"
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
        if self.now() - started_at < silence_sec:
            return
        events = step.setdefault("events", {})
        events["silence_completed"] = 1
        if self._event_count(step, "turn_accepted") or self._event_count(step, "playback_started"):
            self._fail_attempt(step, "silence_activity_detected")
            self._update_summary()
            self._persist()
            return
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
        report_path = self.paths.reports / f"{self._session.get('sessionId')}.json"
        _atomic_json_write(report_path, self._report_payload())
        self._persist()
        self.prune_reports()

    def prune_reports(self) -> list[str]:
        self.paths.reports.mkdir(parents=True, exist_ok=True)
        now = self.now()
        reports = sorted(
            self.paths.reports.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        removed: list[str] = []
        for index, path in enumerate(reports):
            age_days = max(0.0, now - path.stat().st_mtime) / 86400.0
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
    turn_id = payload.get("turn_id") or meta.get("turn_id")
    if event == "voice_turn_summary" and session_id and step_id:
        common = {
            "session_id": session_id,
            "step_id": step_id,
            "turnId": turn_id,
        }
        emit_voice_validation_event("discord", "turn_accepted", **common)
        emit_voice_validation_event("discord", "reply_final", **common)
        if payload.get("playback_started"):
            emit_voice_validation_event(
                "discord",
                "playback_started",
                latencyMs=payload.get("playback_first_packet_ms")
                if payload.get("playback_first_packet_ms") is not None
                else payload.get("total_ms"),
                **common,
            )
        if payload.get("playback_completed"):
            emit_voice_validation_event("discord", "playback_completed", **common)
        if payload.get("playback_cancelled"):
            emit_voice_validation_event("discord", "playback_cancelled", **common)
        if payload.get("error"):
            emit_voice_validation_event(
                "discord",
                "error",
                errorCode=payload.get("error"),
                **common,
            )
        return
    if event == "voice_drop_summary" and session_id and step_id:
        emit_voice_validation_event(
            "discord",
            "error",
            session_id=session_id,
            step_id=step_id,
            turnId=turn_id,
            errorCode=payload.get("drop_reason") or payload.get("error") or "voice_drop",
        )
        return
    if event == "tts_interrupt":
        context = active_validation_context(surface="discord", prefer_interrupt=True)
        if context:
            emit_voice_validation_event(
                "discord",
                "barge_in_accepted",
                session_id=context["sessionId"],
                step_id=context["stepId"],
                turnId=turn_id,
                reason=payload.get("reason") or "tts_interrupt",
            )
        return
    if event == "barge_in_continuity":
        context = (
            {"sessionId": session_id, "stepId": step_id}
            if session_id and step_id
            else active_validation_context(surface="discord", prefer_interrupt=True)
        )
        if not context:
            return
        status = str(payload.get("status") or "").strip().lower()
        success = status == "success" or bool(payload.get("success"))
        emit_voice_validation_event(
            "discord",
            "barge_in_continuity" if success else "error",
            session_id=context["sessionId"],
            step_id=context["stepId"],
            turnId=turn_id,
            errorCode=None if success else "barge_in_continuity_failed",
            reason=payload.get("reason_code") or payload.get("reason") or status,
        )


__all__ = [
    "ALLOWED_SURFACES",
    "MAX_ATTEMPTS",
    "SESSION_SCHEMA",
    "SUITE_ID",
    "VoiceValidationManager",
    "active_validation_context",
    "emit_voice_validation_event",
    "emit_transcript_validation_event",
    "get_voice_validation_manager",
    "normalize_validation_text",
    "observe_turn_trace_for_voice_validation",
    "sanitize_validation_event",
    "transcript_match",
]
