from __future__ import annotations

import asyncio
import hashlib
import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .continuity_commit_contract import (
    CONTINUITY_COMMIT_METRICS_SCHEMA,
    CONTINUITY_STATUS_SCHEMA,
)
from .continuity_authenticity import (
    CONTINUITY_AUTH_ANCHOR_SLOT_GUILD_REVOCATIONS,
    CONTINUITY_AUTH_ARTIFACT_GUILD_REVOCATIONS,
    CONTINUITY_AUTH_SCOPE_MAIN,
    CONTINUITY_HEAD_SCHEMA_V1,
    CONTINUITY_HEAD_SCHEMA_V2,
    ContinuityAuthenticity,
    ContinuityAuthenticityError,
    build_continuity_head,
    validate_continuity_head,
)
from .conversation_memory_receipt import (
    sanitize_memory_receipt_ref,
)
from .runtime_artifact_io import atomic_json_write
from .runtime_error_observability import RuntimeErrorCounter
from .text import clean_text


SESSION_CONTINUITY_CHECKPOINT_SCHEMA = "conversation_continuity.checkpoint.v2"
SESSION_CONTINUITY_LEGACY_CHECKPOINT_SCHEMA = (
    "conversation_continuity.checkpoint.v1"
)
SESSION_CONTINUITY_HEAD_SCHEMA = (
    CONTINUITY_HEAD_SCHEMA_V1
)
SESSION_CONTINUITY_AUTHENTICATED_HEAD_SCHEMA = (
    CONTINUITY_HEAD_SCHEMA_V2
)
SESSION_CONTINUITY_STATUS_SCHEMA = CONTINUITY_STATUS_SCHEMA
SESSION_CONTINUITY_COMMIT_METRICS_SCHEMA = (
    CONTINUITY_COMMIT_METRICS_SCHEMA
)
SESSION_CONTINUITY_REVOCATIONS_SCHEMA = (
    "conversation_continuity.guild_revocations.v1"
)
SESSION_CONTINUITY_AUTHENTICATED_REVOCATIONS_SCHEMA = (
    "conversation_continuity.guild_revocations.v2"
)
SESSION_CONTINUITY_ANCHORED_REVOCATIONS_SCHEMA = (
    "conversation_continuity.guild_revocations.v3"
)
DEFAULT_MAX_AGE_SEC = 15 * 60.0
DEFAULT_FLUSH_INTERVAL_SEC = 1.0
DEFAULT_MAX_SESSIONS = 32
DEFAULT_MAX_HISTORY_ITEMS = 12
DEFAULT_MAX_CONTENT_CHARS = 2000
DEFAULT_MAX_FILE_BYTES = 1024 * 1024
DEFAULT_MAX_GUILD_REVOCATIONS = 256
DEFAULT_COMMIT_LATENCY_WARNING_MS = 100.0
DEFAULT_COMMIT_LATENCY_WARNING_MIN_SAMPLES = 20
DEFAULT_COMMIT_LATENCY_SAMPLE_LIMIT = 256
SESSION_CONTINUITY_CHAIN_GENESIS = "0" * 64
_ALLOWED_HISTORY_ROLES = frozenset({"user", "assistant"})
_ALLOWED_SPEAKERS = frozenset({"user", "assistant"})


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _valid_session_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > 256:
        return ""
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_-" for character in text):
        return ""
    return text


def _valid_turn_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > 80:
        return ""
    return text


def _safe_history(
    value: Any,
    *,
    max_items: int,
    max_chars: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in _ALLOWED_HISTORY_ROLES:
            continue
        content = clean_text(str(item.get("content") or ""))[:max_chars]
        if not content:
            continue
        receipt_present = "memoryReceiptRef" in item
        if role == "user" and receipt_present:
            continue
        row: dict[str, Any] = {
            "role": role,
            "content": content,
        }
        if role == "assistant" and receipt_present:
            receipt_ref = sanitize_memory_receipt_ref(
                item.get("memoryReceiptRef")
            )
            if receipt_ref is None:
                continue
            row["memoryReceiptRef"] = receipt_ref
        rows.append(row)
    return rows[-max_items:]


def _safe_followup_target(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    target: dict[str, int] = {}
    for source_name, output_name in (
        ("channel_id", "channelId"),
        ("message_id", "messageId"),
    ):
        parsed = _safe_int(value.get(source_name))
        if parsed is not None and parsed >= 0:
            target[output_name] = parsed
    return target


def _session_guild_id(session_key: str) -> int | None:
    parts = str(session_key or "").split(":", 2)
    if len(parts) < 3 or parts[0] != "guild":
        return None
    guild_id = _safe_int(parts[1])
    return guild_id if guild_id is not None and guild_id >= 0 else None


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _checkpoint_hash(payload: dict[str, Any]) -> str:
    unsigned = {
        key: value
        for key, value in payload.items()
        if key != "checkpointHash"
    }
    return hashlib.sha256(
        _canonical_json(unsigned).encode("utf-8")
    ).hexdigest()


def _revocations_hash(payload: dict[str, Any]) -> str:
    unsigned = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "ledgerHash",
            "authAlgorithm",
            "authScope",
            "authKeyId",
            "authTag",
        }
    }
    return hashlib.sha256(
        _canonical_json(unsigned).encode("utf-8")
    ).hexdigest()


def _legacy_checkpoint_hash(raw_text: str) -> str:
    digest = hashlib.sha256(
        b"conversation_continuity.legacy-checkpoint.v1\n"
    )
    digest.update(raw_text.encode("utf-8"))
    return digest.hexdigest()


def _valid_sha256(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64:
        return ""
    lowered = value.lower()
    return (
        lowered
        if all(character in "0123456789abcdef" for character in lowered)
        else ""
    )


class SessionContinuityCheckpoint:
    """Persists a bounded, short-lived completed-turn checkpoint across restarts."""

    def __init__(
        self,
        *,
        store: Any,
        checkpoint_path: Path,
        status_path: Path,
        head_path: Path | None = None,
        revocations_path: Path | None = None,
        system_prompt: str,
        max_age_sec: float = DEFAULT_MAX_AGE_SEC,
        flush_interval_sec: float = DEFAULT_FLUSH_INTERVAL_SEC,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        max_history_items: int = DEFAULT_MAX_HISTORY_ITEMS,
        max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        wall_time: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        commit_latency_clock: Callable[[], float] = time.perf_counter,
        commit_latency_warning_ms: float = (
            DEFAULT_COMMIT_LATENCY_WARNING_MS
        ),
        commit_latency_warning_min_samples: int = (
            DEFAULT_COMMIT_LATENCY_WARNING_MIN_SAMPLES
        ),
        commit_latency_sample_limit: int = (
            DEFAULT_COMMIT_LATENCY_SAMPLE_LIMIT
        ),
        authenticity: ContinuityAuthenticity | None = None,
        authenticity_scope: str = CONTINUITY_AUTH_SCOPE_MAIN,
        log: Callable[[str], Any] | None = None,
    ) -> None:
        self.store = store
        self.checkpoint_path = Path(checkpoint_path)
        self.status_path = Path(status_path)
        self.head_path = Path(
            head_path
            or self.checkpoint_path.with_name(
                "checkpoint_head.json"
            )
        )
        self.revocations_path = Path(
            revocations_path
            or self.checkpoint_path.with_name("guild_revocations.json")
        )
        self.system_prompt = str(system_prompt)
        self.max_age_sec = max(60.0, float(max_age_sec))
        self.flush_interval_sec = max(0.25, float(flush_interval_sec))
        self.max_sessions = max(1, int(max_sessions))
        self.max_history_items = max(2, int(max_history_items))
        self.max_content_chars = max(128, int(max_content_chars))
        self.max_file_bytes = max(4096, int(max_file_bytes))
        self.wall_time = wall_time
        self.monotonic = monotonic
        self.commit_latency_clock = commit_latency_clock
        self.commit_latency_warning_ms = max(
            1.0,
            _finite_float(
                commit_latency_warning_ms,
                DEFAULT_COMMIT_LATENCY_WARNING_MS,
            ),
        )
        self.commit_latency_warning_min_samples = max(
            1,
            int(commit_latency_warning_min_samples),
        )
        self.commit_latency_sample_limit = max(
            self.commit_latency_warning_min_samples,
            int(commit_latency_sample_limit),
        )
        self.authenticity = (
            authenticity or ContinuityAuthenticity()
        )
        self.authenticity_scope = str(authenticity_scope)
        self.log = log
        self.runtime_errors = RuntimeErrorCounter(now=wall_time)
        self._lock = threading.RLock()
        self._task: asyncio.Task[None] | None = None
        self._last_signature = ""
        self._state = "not_initialized"
        self._last_restored_at: float | None = None
        self._last_persisted_at: float | None = None
        self._checkpoint_revoked_at: float | None = None
        self._restored_session_count = 0
        self._persisted_session_count = 0
        self._guild_revocations: dict[int, float] = {}
        self._guild_revocations_authenticity = "missing"
        self._guild_revocations_generation = 0
        self._guild_revocations_hash = (
            SESSION_CONTINUITY_CHAIN_GENESIS
        )
        self._guild_revocations_anchor_state = (
            "missing"
            if (
                self.authenticity.external_anchor_configured
                and self.authenticity_scope
                == CONTINUITY_AUTH_SCOPE_MAIN
            )
            else "unconfigured"
        )
        self._checkpoint_integrity = "unknown"
        self._checkpoint_generation = 0
        self._checkpoint_head_state = "missing"
        self._checkpoint_head_authenticity = "missing"
        self._checkpoint_anchor_state = (
            "missing"
            if self.authenticity.external_anchor_configured
            else "unconfigured"
        )
        self._commit_attempt_count = 0
        self._commit_success_count = 0
        self._commit_failure_count = 0
        self._commit_latency_samples_ms: list[float] = []
        self._commit_last_ms: float | None = None
        self._commit_last_at: float | None = None
        self._commit_last_succeeded: bool | None = None
        self._commit_last_target_verified: bool | None = None

    def _emit(self, message: str) -> None:
        if self.log is not None:
            self.log(message)

    def _guild_revocations_anchor_configured(self) -> bool:
        return bool(
            self.authenticity.external_anchor_configured
            and self.authenticity_scope
            == CONTINUITY_AUTH_SCOPE_MAIN
        )

    @staticmethod
    def _mapping_keys(mapping: Any) -> list[Any]:
        # The store is owned by the event loop while flush runs in a worker
        # thread. Retry a bounded snapshot if a dictionary changes size.
        for _attempt in range(3):
            try:
                return list(mapping.keys())
            except RuntimeError:
                continue
        raise RuntimeError("session_store_busy")

    def _selected_keys(
        self,
        *,
        required_session_key: str = "",
    ) -> list[str]:
        candidates: set[Any] = set()
        for mapping in (
            self.store.histories,
            self.store.followup_targets,
            self.store.active_until,
            self.store.active_user_ids,
            self.store.awaiting_user_reply,
            self.store.last_speaker,
            self.store.topic_ids,
            self.store.turn_ids,
        ):
            candidates.update(self._mapping_keys(mapping))
        required = _valid_session_key(required_session_key)
        if required_session_key and not required:
            raise ValueError("continuity_commit_target_invalid")
        valid = [key for key in candidates if _valid_session_key(key)]
        if required and required not in valid:
            raise ValueError("continuity_commit_target_missing")
        ranked = sorted(
            valid,
            key=lambda key: (
                -_finite_float(self.store.last_active_at.get(key)),
                key,
            ),
        )
        selected = ranked[: self.max_sessions]
        if required and required not in selected:
            selected[-1] = required
            selected.sort(key=ranked.index)
        return selected

    def _material(
        self,
        *,
        required_session_key: str = "",
        required_turn_id: str = "",
    ) -> dict[str, Any]:
        required = _valid_session_key(required_session_key)
        expected_turn = _valid_turn_id(required_turn_id)
        if required_turn_id and not expected_turn:
            raise ValueError("continuity_commit_target_invalid")
        if expected_turn and not required:
            raise ValueError("continuity_commit_target_invalid")
        if expected_turn:
            current_turn = _valid_turn_id(
                self.store.turn_ids.get(required)
            )
            if current_turn != expected_turn:
                raise ValueError("continuity_commit_target_mismatch")
        sessions: list[dict[str, Any]] = []
        for session_key in self._selected_keys(
            required_session_key=required,
        ):
            history = _safe_history(
                self.store.histories.get(session_key),
                max_items=self.max_history_items,
                max_chars=self.max_content_chars,
            )
            speaker = str(self.store.last_speaker.get(session_key) or "").strip().lower()
            followup_target = _safe_followup_target(
                self.store.followup_targets.get(session_key)
            )
            sessions.append(
                {
                    "sessionKey": session_key,
                    "history": history,
                    "activeUntilMonotonic": _finite_float(
                        self.store.active_until.get(session_key)
                    ),
                    "lastActiveMonotonic": _finite_float(
                        self.store.last_active_at.get(session_key),
                        default=-1.0,
                    ),
                    "awaitingUserReply": bool(
                        self.store.awaiting_user_reply.get(session_key)
                    ),
                    "userId": _safe_int(
                        self.store.active_user_ids.get(session_key)
                    ),
                    "lastSpeaker": speaker if speaker in _ALLOWED_SPEAKERS else "",
                    "topicId": str(
                        self.store.topic_ids.get(session_key) or ""
                    )[:80],
                    "turnId": str(
                        self.store.turn_ids.get(session_key) or ""
                    )[:80],
                    "followupTarget": followup_target,
                }
            )
        return {"sessions": sessions}

    @staticmethod
    def _signature(material: dict[str, Any]) -> str:
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _payload_from_material(
        self,
        material: dict[str, Any],
        *,
        saved_at: float,
        now_monotonic: float,
        generation: int,
        previous_hash: str,
    ) -> dict[str, Any]:
        sessions: list[dict[str, Any]] = []
        for source in material["sessions"]:
            last_active_monotonic = _finite_float(
                source.get("lastActiveMonotonic"),
                default=-1.0,
            )
            last_active_ago_sec = (
                max(0.0, now_monotonic - last_active_monotonic)
                if last_active_monotonic >= 0.0
                else self.max_age_sec + 1.0
            )
            remaining_sec = max(
                0.0,
                min(
                    self.max_age_sec,
                    _finite_float(source.get("activeUntilMonotonic"))
                    - now_monotonic,
                ),
            )
            sessions.append(
                {
                    "sessionKey": source["sessionKey"],
                    "history": source["history"],
                    "state": {
                        "activeRemainingSec": round(remaining_sec, 3),
                        "lastActiveAgoSec": last_active_ago_sec,
                        "awaitingUserReply": bool(
                            source.get("awaitingUserReply")
                            and remaining_sec > 0.0
                        ),
                        "userId": source.get("userId"),
                        "lastSpeaker": source.get("lastSpeaker") or "",
                        "topicId": source.get("topicId") or "",
                        "turnId": source.get("turnId") or "",
                        "followupTarget": (
                            source.get("followupTarget")
                            if remaining_sec > 0.0
                            else {}
                        ),
                    },
                }
            )
        payload = {
            "schema": SESSION_CONTINUITY_CHECKPOINT_SCHEMA,
            "generation": int(generation),
            "previousHash": str(previous_hash),
            "savedAt": saved_at,
            "expiresAt": saved_at + self.max_age_sec,
            "policy": {
                "maxAgeSec": self.max_age_sec,
                "maxSessions": self.max_sessions,
                "maxHistoryItems": self.max_history_items,
                "maxContentChars": self.max_content_chars,
                "completedTurnText": True,
                "rawAudio": False,
                "partialTranscript": False,
                "systemPrompt": False,
            },
            "sessions": sessions,
        }
        payload["checkpointHash"] = _checkpoint_hash(payload)
        return payload

    def status(self) -> dict[str, Any]:
        updated_at = self.wall_time()
        return {
            "schema": SESSION_CONTINUITY_STATUS_SCHEMA,
            "state": self._state,
            "updatedAt": updated_at,
            "heartbeatAt": updated_at,
            "lastRestoredAt": self._last_restored_at,
            "lastPersistedAt": self._last_persisted_at,
            "checkpointRevokedAt": self._checkpoint_revoked_at,
            "restoredSessionCount": self._restored_session_count,
            "persistedSessionCount": self._persisted_session_count,
            "guildRevocationCount": len(self._guild_revocations),
            "guildRevocationsAuthenticity": (
                self._guild_revocations_authenticity
            ),
            "guildRevocationsGeneration": (
                self._guild_revocations_generation
            ),
            "guildRevocationsAnchorState": (
                self._guild_revocations_anchor_state
            ),
            "guildRevocationsTamperEvident": bool(
                self.authenticity.configured
                and self._guild_revocations_authenticity
                == "verified"
            ),
            "guildRevocationsReplayProtected": bool(
                self._guild_revocations_anchor_configured()
                and self._guild_revocations_anchor_state
                == "verified"
            ),
            "checkpointIntegrity": self._checkpoint_integrity,
            "checkpointGeneration": self._checkpoint_generation,
            "checkpointHeadState": self._checkpoint_head_state,
            "checkpointHeadAuthenticity": (
                self._checkpoint_head_authenticity
            ),
            "checkpointAnchorState": (
                self._checkpoint_anchor_state
            ),
            "keyedAuthenticity": (
                self.authenticity.configured
            ),
            "externalAnchorConfigured": (
                self.authenticity.external_anchor_configured
            ),
            "externalReplayProtected": bool(
                self.authenticity.external_anchor_configured
                and self._checkpoint_anchor_state == "verified"
                and self._checkpoint_head_state
                in {"current", "empty", "missing"}
            ),
            "tamperEvident": bool(
                self.authenticity.configured
                and self._checkpoint_head_authenticity
                == "verified"
                and self._checkpoint_head_state
                in {"current", "empty"}
                and (
                    not self.authenticity.external_anchor_configured
                    or self._checkpoint_anchor_state == "verified"
                )
            ),
            "rollbackProtected": bool(
                self._checkpoint_head_state
                in {"current", "empty"}
                and self._checkpoint_integrity
                in {
                    "empty",
                    "legacy_anchored",
                    "verified",
                }
            ),
            "policy": {
                "maxAgeSec": self.max_age_sec,
                "flushIntervalSec": self.flush_interval_sec,
                "rawAudio": False,
                "partialTranscript": False,
            },
            "completedTurnCommit": self._commit_metrics(),
            **self.runtime_errors.snapshot(),
        }

    @staticmethod
    def _percentile(
        samples: list[float],
        percentile: float,
    ) -> float | None:
        if not samples:
            return None
        ordered = sorted(samples)
        rank = max(
            0,
            min(
                len(ordered) - 1,
                math.ceil(float(percentile) * len(ordered)) - 1,
            ),
        )
        return round(ordered[rank], 3)

    def _commit_metrics(self) -> dict[str, Any]:
        samples = self._commit_latency_samples_ms
        p50_ms = self._percentile(samples, 0.50)
        p95_ms = self._percentile(samples, 0.95)
        if self._commit_last_succeeded is False:
            state = "error"
            warning_code = "conversation_continuity_commit_failed"
        elif len(samples) < self.commit_latency_warning_min_samples:
            state = "warming" if samples else "idle"
            warning_code = ""
        elif (
            p95_ms is not None
            and p95_ms > self.commit_latency_warning_ms
        ):
            state = "warning"
            warning_code = (
                "conversation_continuity_commit_latency_high"
            )
        else:
            state = "ready"
            warning_code = ""
        return {
            "schema": SESSION_CONTINUITY_COMMIT_METRICS_SCHEMA,
            "state": state,
            "attemptCount": self._commit_attempt_count,
            "successCount": self._commit_success_count,
            "failureCount": self._commit_failure_count,
            "sampleCount": len(samples),
            "lastMs": self._commit_last_ms,
            "p50Ms": p50_ms,
            "p95Ms": p95_ms,
            "maxMs": (
                round(max(samples), 3)
                if samples
                else None
            ),
            "lastAt": self._commit_last_at,
            "lastSucceeded": self._commit_last_succeeded,
            "lastTargetVerified": (
                self._commit_last_target_verified
            ),
            "warningThresholdMs": (
                self.commit_latency_warning_ms
            ),
            "warningCode": warning_code,
        }

    def _record_commit_attempt(
        self,
        *,
        started_at: float,
        succeeded: bool,
        target_verified: bool,
    ) -> None:
        try:
            elapsed_ms = max(
                0.0,
                (float(self.commit_latency_clock()) - started_at)
                * 1000.0,
            )
        except (OverflowError, TypeError, ValueError):
            elapsed_ms = 0.0
        self._commit_attempt_count += 1
        self._commit_last_ms = round(elapsed_ms, 3)
        self._commit_last_at = self.wall_time()
        self._commit_last_succeeded = bool(succeeded)
        self._commit_last_target_verified = bool(
            target_verified
        )
        if succeeded:
            self._commit_success_count += 1
            self._commit_latency_samples_ms.append(
                self._commit_last_ms
            )
            del self._commit_latency_samples_ms[
                : -self.commit_latency_sample_limit
            ]
        else:
            self._commit_failure_count += 1

    def _load_checkpoint_head(self) -> dict[str, Any] | None:
        path = self.head_path
        if not path.exists() and not path.is_symlink():
            return None
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 128 * 1024
        ):
            raise ValueError("checkpoint_head_rejected")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("checkpoint_head_rejected")
        validated, auth_state = validate_continuity_head(
            payload,
            authenticity=self.authenticity,
            auth_scope=self.authenticity_scope,
            permit_unsigned_bootstrap=True,
        )
        return {
            **validated,
            "_authenticity": auth_state,
        }

    def _write_checkpoint_head(
        self,
        *,
        state: str,
        generation: int,
        checkpoint_hash: str,
    ) -> None:
        head = build_continuity_head(
            state=state,
            generation=generation,
            checkpoint_hash=checkpoint_hash,
            updated_at=self.wall_time(),
            authenticity=self.authenticity,
            auth_scope=self.authenticity_scope,
        )
        atomic_json_write(
            self.head_path,
            head,
            durable=True,
        )
        self._checkpoint_head_authenticity = (
            "verified"
            if self.authenticity.configured
            else "unconfigured"
        )

    def _discard_checkpoint_head(self) -> None:
        try:
            if (
                self.head_path.exists()
                and not self.head_path.is_symlink()
                and self.head_path.is_file()
            ):
                self.head_path.unlink()
        except OSError:
            return

    def _checkpoint_snapshot(self) -> dict[str, Any]:
        head = self._load_checkpoint_head()
        head_authenticity = (
            str(head.get("_authenticity") or "")
            if head is not None
            else "missing"
        )
        path = self.checkpoint_path
        if not path.exists() and not path.is_symlink():
            if head is not None and head["state"] == "active":
                raise ValueError("checkpoint_missing_after_head")
            return {
                "payload": None,
                "rawText": "",
                "schema": "",
                "generation": (
                    int(head["generation"])
                    if head is not None
                    else 0
                ),
                "checkpointHash": (
                    str(head["checkpointHash"])
                    if head is not None
                    else SESSION_CONTINUITY_CHAIN_GENESIS
                ),
                "previousHash": SESSION_CONTINUITY_CHAIN_GENESIS,
                "discardCheckpoint": False,
                "integrity": "empty",
                "headState": (
                    "empty"
                    if head is not None
                    else "missing"
                ),
                "headAuthenticity": head_authenticity,
            }
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > self.max_file_bytes
        ):
            raise ValueError("checkpoint_rejected")
        raw_text = path.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
        if not isinstance(payload, dict):
            raise ValueError("checkpoint_rejected")
        schema = payload.get("schema")
        if schema == SESSION_CONTINUITY_LEGACY_CHECKPOINT_SCHEMA:
            checkpoint_hash = _legacy_checkpoint_hash(raw_text)
            if head is None:
                if (
                    self.authenticity.configured
                    and not self.authenticity.allow_unsigned_bootstrap
                ):
                    raise ContinuityAuthenticityError(
                        "continuity_auth_bootstrap_required"
                    )
                head_state = "missing"
                head_authenticity = (
                    "bootstrap_required"
                    if self.authenticity.configured
                    else "missing"
                )
            elif (
                head["state"] == "active"
                and int(head["generation"]) == 0
                and head["checkpointHash"] == checkpoint_hash
            ):
                head_state = "current"
            else:
                raise ValueError("legacy_checkpoint_head_mismatch")
            return {
                "payload": payload,
                "rawText": raw_text,
                "schema": schema,
                "generation": 0,
                "checkpointHash": checkpoint_hash,
                "previousHash": SESSION_CONTINUITY_CHAIN_GENESIS,
                "discardCheckpoint": False,
                "integrity": "legacy",
                "headState": head_state,
                "headAuthenticity": head_authenticity,
            }
        if schema != SESSION_CONTINUITY_CHECKPOINT_SCHEMA:
            raise ValueError("invalid_schema")
        generation = payload.get("generation")
        previous_hash = _valid_sha256(
            payload.get("previousHash")
        )
        checkpoint_hash = _valid_sha256(
            payload.get("checkpointHash")
        )
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
            or not previous_hash
            or not checkpoint_hash
            or checkpoint_hash != _checkpoint_hash(payload)
        ):
            raise ValueError("checkpoint_integrity_failed")
        if head is None:
            if (
                self.authenticity.configured
                and not self.authenticity.allow_unsigned_bootstrap
            ):
                raise ContinuityAuthenticityError(
                    "continuity_auth_bootstrap_required"
                )
            if (
                generation != 1
                or previous_hash
                != SESSION_CONTINUITY_CHAIN_GENESIS
            ):
                raise ValueError("checkpoint_head_missing")
            head_state = "lagging"
            head_authenticity = (
                "bootstrap_required"
                if self.authenticity.configured
                else "missing"
            )
        elif (
            head["state"] == "active"
            and generation == int(head["generation"])
            and checkpoint_hash == head["checkpointHash"]
        ):
            head_state = "current"
        elif (
            head["state"] == "active"
            and generation == int(head["generation"]) + 1
            and previous_hash == head["checkpointHash"]
        ):
            head_state = "lagging"
        elif (
            head["state"] == "empty"
            and generation == int(head["generation"]) + 1
            and previous_hash
            == SESSION_CONTINUITY_CHAIN_GENESIS
        ):
            head_state = "lagging"
        elif (
            head["state"] == "empty"
            and int(head["generation"]) == generation + 1
            and head["checkpointHash"]
            == SESSION_CONTINUITY_CHAIN_GENESIS
            and head.get("_authenticity") == "verified"
        ):
            return {
                "payload": None,
                "rawText": raw_text,
                "schema": schema,
                "generation": int(head["generation"]),
                "checkpointHash": SESSION_CONTINUITY_CHAIN_GENESIS,
                "previousHash": checkpoint_hash,
                "discardCheckpoint": True,
                "integrity": "empty",
                "headState": "empty",
                "headAuthenticity": head_authenticity,
            }
        else:
            raise ValueError("checkpoint_rollback_or_head_mismatch")
        return {
            "payload": payload,
            "rawText": raw_text,
            "schema": schema,
            "generation": generation,
            "checkpointHash": checkpoint_hash,
            "previousHash": previous_hash,
            "discardCheckpoint": False,
            "integrity": "verified",
            "headState": head_state,
            "headAuthenticity": head_authenticity,
        }

    def _anchor_checkpoint_snapshot(
        self,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        payload = snapshot.get("payload")
        anchor_slot = self.authenticity.checkpoint_anchor_slot(
            self.authenticity_scope
        )
        if payload is None:
            if (
                snapshot.get("headState") == "empty"
                and snapshot.get("headAuthenticity")
                == "bootstrap_required"
            ):
                self._write_checkpoint_head(
                    state="empty",
                    generation=int(snapshot["generation"]),
                    checkpoint_hash=str(
                        snapshot["checkpointHash"]
                    ),
                )
                snapshot = {
                    **snapshot,
                    "headAuthenticity": "verified",
                }
            self.authenticity.reconcile_external_anchor(
                anchor_slot,
                generation=int(snapshot["generation"]),
                artifact_hash=str(snapshot["checkpointHash"]),
                previous_hash=str(
                    snapshot.get("previousHash") or ""
                ),
                allow_unlinked_one_step=bool(
                    snapshot.get("headState") == "empty"
                    and int(snapshot["generation"]) > 0
                ),
                updated_at=self.wall_time(),
            )
            self._checkpoint_anchor_state = (
                "verified"
                if self.authenticity.external_anchor_configured
                else "unconfigured"
            )
            if snapshot.get("discardCheckpoint") is True:
                self._discard_checkpoint()
            self._checkpoint_generation = int(
                snapshot["generation"]
            )
            self._checkpoint_integrity = "empty"
            self._checkpoint_head_state = str(
                snapshot["headState"]
            )
            self._checkpoint_head_authenticity = str(
                snapshot["headAuthenticity"]
            )
            return snapshot
        if (
            snapshot["headState"] != "current"
            or snapshot.get("headAuthenticity")
            == "bootstrap_required"
        ):
            self._write_checkpoint_head(
                state="active",
                generation=int(snapshot["generation"]),
                checkpoint_hash=str(
                    snapshot["checkpointHash"]
                ),
            )
            snapshot = {
                **snapshot,
                "headState": "current",
                "headAuthenticity": (
                    "verified"
                    if self.authenticity.configured
                    else "unconfigured"
                ),
            }
        self.authenticity.reconcile_external_anchor(
            anchor_slot,
            generation=int(snapshot["generation"]),
            artifact_hash=str(snapshot["checkpointHash"]),
            previous_hash=str(
                snapshot.get("previousHash") or ""
            ),
            updated_at=self.wall_time(),
        )
        self._checkpoint_anchor_state = (
            "verified"
            if self.authenticity.external_anchor_configured
            else "unconfigured"
        )
        self._checkpoint_generation = int(
            snapshot["generation"]
        )
        self._checkpoint_integrity = (
            "legacy_anchored"
            if snapshot["integrity"] == "legacy"
            else "verified"
        )
        self._checkpoint_head_state = "current"
        self._checkpoint_head_authenticity = str(
            snapshot["headAuthenticity"]
        )
        return snapshot

    def _revoke_checkpoint_chain(self) -> None:
        self._checkpoint_revoked_at = self.wall_time()
        generation = self._checkpoint_generation
        checkpoint_hash = SESSION_CONTINUITY_CHAIN_GENESIS
        anchor_slot = self.authenticity.checkpoint_anchor_slot(
            self.authenticity_scope
        )
        if self.authenticity.external_anchor_configured:
            try:
                anchored = self.authenticity.external_anchor_position(
                    anchor_slot
                )
            except ContinuityAuthenticityError as exc:
                self._checkpoint_anchor_state = "failed"
                self._checkpoint_head_state = "failed"
                self._checkpoint_integrity = "failed"
                return
            if anchored is None:
                self._checkpoint_anchor_state = (
                    "bootstrap_required"
                )
                self._checkpoint_head_state = "failed"
                self._checkpoint_integrity = "failed"
                return
            generation, checkpoint_hash = anchored
        try:
            head = self._load_checkpoint_head()
            if (
                head is not None
                and not self.authenticity.external_anchor_configured
            ):
                generation = max(
                    generation,
                    int(head["generation"]),
                )
                checkpoint_hash = str(head["checkpointHash"])
        except Exception:
            if not self.authenticity.external_anchor_configured:
                self._discard_checkpoint_head()
        try:
            self._write_checkpoint_head(
                state="empty",
                generation=generation + 1,
                checkpoint_hash=(
                    SESSION_CONTINUITY_CHAIN_GENESIS
                ),
            )
            self.authenticity.commit_external_anchor(
                anchor_slot,
                previous_generation=generation,
                previous_hash=checkpoint_hash,
                generation=generation + 1,
                artifact_hash=SESSION_CONTINUITY_CHAIN_GENESIS,
                updated_at=self.wall_time(),
            )
            self._checkpoint_generation = generation + 1
            self._checkpoint_head_state = "empty"
            self._checkpoint_anchor_state = (
                "verified"
                if self.authenticity.external_anchor_configured
                else "unconfigured"
            )
        except Exception:
            if not self.authenticity.external_anchor_configured:
                self._discard_checkpoint_head()
            else:
                self._checkpoint_head_state = "failed"
                self._checkpoint_anchor_state = "failed"
                self._checkpoint_integrity = "failed"
                self._last_signature = ""
                return
            self._checkpoint_head_state = "failed"
            self._checkpoint_anchor_state = "unconfigured"
        self._checkpoint_integrity = "failed"
        self._last_signature = ""
        self._discard_checkpoint()

    def _load_guild_revocations(self) -> dict[int, float]:
        path = self.revocations_path
        if not path.exists() and not path.is_symlink():
            self._guild_revocations_authenticity = "missing"
            if self._guild_revocations_anchor_configured():
                try:
                    self.authenticity.reconcile_external_anchor(
                        CONTINUITY_AUTH_ANCHOR_SLOT_GUILD_REVOCATIONS,
                        generation=0,
                        artifact_hash=SESSION_CONTINUITY_CHAIN_GENESIS,
                        updated_at=self.wall_time(),
                    )
                except ContinuityAuthenticityError as exc:
                    self._guild_revocations_anchor_state = {
                        "continuity_anchor_bootstrap_required": (
                            "bootstrap_required"
                        ),
                        "continuity_anchor_replay_detected": (
                            "replay_detected"
                        ),
                    }.get(exc.code, "failed")
                    raise
            self._guild_revocations_generation = 0
            self._guild_revocations_hash = (
                SESSION_CONTINUITY_CHAIN_GENESIS
            )
            self._guild_revocations_anchor_state = (
                "verified"
                if self._guild_revocations_anchor_configured()
                else "unconfigured"
            )
            return {}
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 128 * 1024
        ):
            raise ValueError("guild_revocations_rejected")
        payload = json.loads(path.read_text(encoding="utf-8"))
        schema = (
            str(payload.get("schema") or "")
            if isinstance(payload, dict)
            else ""
        )
        base_keys = {
            "schema",
            "updatedAt",
            "guilds",
            "policy",
        }
        expected_keys = set(base_keys)
        if schema in {
            SESSION_CONTINUITY_AUTHENTICATED_REVOCATIONS_SCHEMA,
            SESSION_CONTINUITY_ANCHORED_REVOCATIONS_SCHEMA,
        }:
            expected_keys.update(
                {
                    "authAlgorithm",
                    "authScope",
                    "authKeyId",
                    "authTag",
                }
            )
        if schema == SESSION_CONTINUITY_ANCHORED_REVOCATIONS_SCHEMA:
            expected_keys.update(
                {"generation", "previousHash", "ledgerHash"}
            )
        if (
            not isinstance(payload, dict)
            or set(payload) != expected_keys
            or schema
            not in {
                SESSION_CONTINUITY_REVOCATIONS_SCHEMA,
                SESSION_CONTINUITY_AUTHENTICATED_REVOCATIONS_SCHEMA,
                SESSION_CONTINUITY_ANCHORED_REVOCATIONS_SCHEMA,
            }
            or not isinstance(payload.get("guilds"), dict)
            or not isinstance(payload.get("policy"), dict)
            or set(payload["policy"])
            != {"contentFree", "maxGuilds"}
            or payload["policy"].get("contentFree") is not True
            or payload["policy"].get("maxGuilds")
            != DEFAULT_MAX_GUILD_REVOCATIONS
            or _finite_float(
                payload.get("updatedAt"),
                default=-1.0,
            )
            < 0.0
        ):
            raise ValueError("guild_revocations_rejected")
        try:
            if schema in {
                SESSION_CONTINUITY_AUTHENTICATED_REVOCATIONS_SCHEMA,
                SESSION_CONTINUITY_ANCHORED_REVOCATIONS_SCHEMA,
            }:
                self.authenticity.verify_scoped_artifact(
                    payload,
                    artifact_scope=(
                        CONTINUITY_AUTH_ARTIFACT_GUILD_REVOCATIONS
                    ),
                )
                auth_state = "verified"
            elif self.authenticity.configured:
                if not self.authenticity.allow_unsigned_bootstrap:
                    raise ContinuityAuthenticityError(
                        "continuity_auth_bootstrap_required"
                    )
                auth_state = "bootstrap_required"
            else:
                auth_state = "unconfigured"
        except ContinuityAuthenticityError as exc:
            self._guild_revocations_authenticity = {
                "continuity_auth_bootstrap_required": (
                    "bootstrap_required"
                ),
                "continuity_auth_key_required": "key_required",
            }.get(exc.code, "failed")
            raise
        revocations: dict[int, float] = {}
        for raw_guild_id, raw_timestamp in payload["guilds"].items():
            guild_id = _safe_int(raw_guild_id)
            timestamp = _finite_float(raw_timestamp, default=-1.0)
            if (
                guild_id is None
                or guild_id < 0
                or str(guild_id) != str(raw_guild_id)
                or timestamp < 0.0
            ):
                raise ValueError("guild_revocations_rejected")
            revocations[guild_id] = timestamp
        bounded = dict(
            sorted(
                revocations.items(),
                key=lambda item: (-item[1], item[0]),
            )[:DEFAULT_MAX_GUILD_REVOCATIONS]
        )
        if schema == SESSION_CONTINUITY_ANCHORED_REVOCATIONS_SCHEMA:
            generation = payload.get("generation")
            previous_hash = _valid_sha256(
                payload.get("previousHash")
            )
            ledger_hash = _valid_sha256(payload.get("ledgerHash"))
            if (
                isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation < 1
                or not previous_hash
                or not ledger_hash
                or ledger_hash != _revocations_hash(payload)
            ):
                raise ValueError("guild_revocations_rejected")
            if self._guild_revocations_anchor_configured():
                try:
                    self.authenticity.reconcile_external_anchor(
                        CONTINUITY_AUTH_ANCHOR_SLOT_GUILD_REVOCATIONS,
                        generation=generation,
                        artifact_hash=ledger_hash,
                        previous_hash=previous_hash,
                        updated_at=self.wall_time(),
                    )
                except ContinuityAuthenticityError as exc:
                    self._guild_revocations_anchor_state = {
                        "continuity_anchor_bootstrap_required": (
                            "bootstrap_required"
                        ),
                        "continuity_anchor_replay_detected": (
                            "replay_detected"
                        ),
                    }.get(exc.code, "failed")
                    raise
            self._guild_revocations_generation = generation
            self._guild_revocations_hash = ledger_hash
            self._guild_revocations_anchor_state = (
                "verified"
                if self._guild_revocations_anchor_configured()
                else "unconfigured"
            )
            self._guild_revocations_authenticity = "verified"
        elif self._guild_revocations_anchor_configured():
            if not self.authenticity.allow_unsigned_bootstrap:
                self._guild_revocations_anchor_state = (
                    "bootstrap_required"
                )
                raise ContinuityAuthenticityError(
                    "continuity_anchor_bootstrap_required"
                )
            anchored = self.authenticity.external_anchor_position(
                CONTINUITY_AUTH_ANCHOR_SLOT_GUILD_REVOCATIONS
            )
            if anchored not in {
                None,
                (0, SESSION_CONTINUITY_CHAIN_GENESIS),
            }:
                self._guild_revocations_anchor_state = (
                    "replay_detected"
                )
                raise ContinuityAuthenticityError(
                    "continuity_anchor_replay_detected"
                )
            self._guild_revocations_generation = 0
            self._guild_revocations_hash = (
                SESSION_CONTINUITY_CHAIN_GENESIS
            )
            self._write_guild_revocations(bounded)
        elif auth_state == "bootstrap_required":
            self._write_guild_revocations(bounded)
        else:
            self._guild_revocations_generation = 0
            self._guild_revocations_hash = (
                SESSION_CONTINUITY_CHAIN_GENESIS
            )
            self._guild_revocations_anchor_state = "unconfigured"
            self._guild_revocations_authenticity = auth_state
        return bounded

    def _write_guild_revocations(
        self,
        revocations: dict[int, float],
    ) -> None:
        bounded = dict(
            sorted(
                revocations.items(),
                key=lambda item: (-item[1], item[0]),
            )[:DEFAULT_MAX_GUILD_REVOCATIONS]
        )
        anchored_schema = bool(
            self._guild_revocations_anchor_configured()
            or self._guild_revocations_generation > 0
        )
        previous_generation = self._guild_revocations_generation
        previous_hash = self._guild_revocations_hash
        generation = previous_generation + 1
        payload = {
            "schema": (
                SESSION_CONTINUITY_ANCHORED_REVOCATIONS_SCHEMA
                if anchored_schema
                else (
                    SESSION_CONTINUITY_AUTHENTICATED_REVOCATIONS_SCHEMA
                    if self.authenticity.configured
                    else SESSION_CONTINUITY_REVOCATIONS_SCHEMA
                )
            ),
            "updatedAt": self.wall_time(),
            "guilds": {
                str(guild_id): timestamp
                for guild_id, timestamp in sorted(bounded.items())
            },
            "policy": {
                "contentFree": True,
                "maxGuilds": DEFAULT_MAX_GUILD_REVOCATIONS,
            },
        }
        if anchored_schema:
            payload.update(
                {
                    "generation": generation,
                    "previousHash": previous_hash,
                    "ledgerHash": "",
                }
            )
            payload["ledgerHash"] = _revocations_hash(payload)
        payload = self.authenticity.sign_scoped_artifact(
            payload,
            artifact_scope=(
                CONTINUITY_AUTH_ARTIFACT_GUILD_REVOCATIONS
            ),
        )
        atomic_json_write(
            self.revocations_path,
            payload,
            durable=True,
        )
        if anchored_schema:
            if self._guild_revocations_anchor_configured():
                try:
                    self.authenticity.commit_external_anchor(
                        CONTINUITY_AUTH_ANCHOR_SLOT_GUILD_REVOCATIONS,
                        previous_generation=previous_generation,
                        previous_hash=previous_hash,
                        generation=generation,
                        artifact_hash=str(payload["ledgerHash"]),
                        updated_at=self.wall_time(),
                    )
                except ContinuityAuthenticityError as exc:
                    self._guild_revocations_anchor_state = {
                        "continuity_anchor_bootstrap_required": (
                            "bootstrap_required"
                        ),
                        "continuity_anchor_replay_detected": (
                            "replay_detected"
                        ),
                    }.get(exc.code, "failed")
                    raise
            self._guild_revocations_generation = generation
            self._guild_revocations_hash = str(
                payload["ledgerHash"]
            )
            self._guild_revocations_anchor_state = (
                "verified"
                if self._guild_revocations_anchor_configured()
                else "unconfigured"
            )
        self._guild_revocations = bounded
        self._guild_revocations_authenticity = (
            "verified"
            if self.authenticity.configured
            else "unconfigured"
        )

    def _write_status(self, *, durable: bool = False) -> None:
        try:
            atomic_json_write(
                self.status_path,
                self.status(),
                durable=durable,
            )
        except Exception:
            return

    def _record_error(self, code: str, exc: BaseException) -> dict[str, Any]:
        self.runtime_errors.record(code, exc)
        self._state = "error"
        self._write_status(durable=True)
        self._emit(
            f"[SESSION CONTINUITY] {code} type={type(exc).__name__}"
        )
        return self.status()

    def _record_authenticity_error(
        self,
        exc: ContinuityAuthenticityError,
    ) -> dict[str, Any]:
        if exc.code.startswith("continuity_anchor_"):
            self._checkpoint_anchor_state = {
                "continuity_anchor_bootstrap_required": (
                    "bootstrap_required"
                ),
                "continuity_anchor_replay_detected": (
                    "replay_detected"
                ),
            }.get(exc.code, "failed")
            return self._record_error(exc.code, exc)
        self._checkpoint_head_authenticity = {
            "continuity_auth_bootstrap_required": (
                "bootstrap_required"
            ),
            "continuity_auth_key_required": "key_required",
        }.get(exc.code, "failed")
        return self._record_error(exc.code, exc)

    def _discard_checkpoint(self) -> None:
        try:
            if (
                self.checkpoint_path.exists()
                and not self.checkpoint_path.is_symlink()
                and self.checkpoint_path.is_file()
            ):
                self.checkpoint_path.unlink()
        except OSError:
            return

    def _load_checkpoint_revoked_at(self) -> float | None:
        try:
            if (
                not self.status_path.is_file()
                or self.status_path.is_symlink()
                or self.status_path.stat().st_size > 128 * 1024
            ):
                return None
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("schema") != SESSION_CONTINUITY_STATUS_SCHEMA
            ):
                return None
            revoked_at = _finite_float(
                payload.get("checkpointRevokedAt"),
                default=-1.0,
            )
            return revoked_at if revoked_at >= 0.0 else None
        except (OSError, TypeError, ValueError):
            return None

    def restore(self) -> dict[str, Any]:
        with self._lock:
            revoked_at = self._load_checkpoint_revoked_at()
            self._checkpoint_revoked_at = revoked_at
            material = self._material()
            self._last_signature = self._signature(material)
            try:
                snapshot = self._checkpoint_snapshot()
            except ContinuityAuthenticityError as exc:
                return self._record_authenticity_error(exc)
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
            ) as exc:
                self._revoke_checkpoint_chain()
                return self._record_error(
                    "conversation_continuity_restore_failed",
                    exc,
                )
            except ValueError as exc:
                self._revoke_checkpoint_chain()
                return self._record_error(
                    "conversation_continuity_checkpoint_rejected",
                    exc,
                )
            try:
                snapshot = self._anchor_checkpoint_snapshot(
                    snapshot
                )
            except ContinuityAuthenticityError as exc:
                return self._record_authenticity_error(exc)
            except Exception as exc:
                self._checkpoint_head_state = "failed"
                return self._record_error(
                    "conversation_continuity_restore_failed",
                    exc,
                )
            if snapshot["payload"] is None:
                if self._guild_revocations_anchor_configured():
                    try:
                        self._guild_revocations = (
                            self._load_guild_revocations()
                        )
                    except ContinuityAuthenticityError as exc:
                        return self._record_error(exc.code, exc)
                    except (
                        OSError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                    ) as exc:
                        return self._record_error(
                            "conversation_continuity_checkpoint_rejected",
                            exc,
                        )
                self._checkpoint_revoked_at = None
                self._state = "missing"
                self._write_status()
                return self.status()
            payload = snapshot["payload"]
            saved_at = _finite_float(payload.get("savedAt"), default=-1.0)
            now_wall = self.wall_time()
            if saved_at < 0.0 or saved_at > now_wall + 60.0:
                self._revoke_checkpoint_chain()
                return self._record_error(
                    "conversation_continuity_checkpoint_rejected",
                    ValueError("invalid_saved_at"),
                )
            if revoked_at is not None and revoked_at >= saved_at:
                self._revoke_checkpoint_chain()
                return self._record_error(
                    "conversation_continuity_checkpoint_rejected",
                    ValueError("checkpoint_revoked"),
                )
            age_sec = max(0.0, now_wall - saved_at)
            if age_sec > self.max_age_sec:
                self._revoke_checkpoint_chain()
                self._checkpoint_revoked_at = None
                self._checkpoint_integrity = "empty"
                self._state = "stale"
                self._write_status()
                return self.status()
            try:
                guild_revocations = self._load_guild_revocations()
            except ContinuityAuthenticityError as exc:
                return self._record_error(exc.code, exc)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._revoke_checkpoint_chain()
                return self._record_error(
                    "conversation_continuity_checkpoint_rejected",
                    exc,
                )
            self._guild_revocations = guild_revocations

            restored = 0
            now_mono = self.monotonic()
            sessions = payload.get("sessions")
            if not isinstance(sessions, list):
                sessions = []
            for row in sessions[: self.max_sessions]:
                if not isinstance(row, dict):
                    continue
                session_key = _valid_session_key(row.get("sessionKey"))
                if not session_key:
                    continue
                state = row.get("state")
                state = state if isinstance(state, dict) else {}
                raw_last_active_ago_sec = state.get(
                    "lastActiveAgoSec"
                )
                parsed_last_active_ago_sec = _finite_float(
                    raw_last_active_ago_sec,
                    default=-1.0,
                )
                activity_known = (
                    not isinstance(raw_last_active_ago_sec, bool)
                    and parsed_last_active_ago_sec >= 0.0
                )
                last_active_ago_sec = (
                    parsed_last_active_ago_sec
                    if activity_known
                    else self.max_age_sec + 1.0
                )
                selected_activity_at = max(
                    0.0,
                    saved_at - last_active_ago_sec,
                )
                guild_id = _session_guild_id(session_key)
                if guild_id is not None:
                    revoked_at = _finite_float(
                        guild_revocations.get(guild_id),
                        default=-1.0,
                    )
                    if revoked_at >= 0.0 and (
                        not activity_known
                        or revoked_at >= selected_activity_at
                    ):
                        continue
                history = _safe_history(
                    row.get("history"),
                    max_items=self.max_history_items,
                    max_chars=self.max_content_chars,
                )
                remaining_sec = max(
                    0.0,
                    min(
                        self.max_age_sec,
                        _finite_float(state.get("activeRemainingSec"))
                        - age_sec,
                    ),
                )
                self.store.histories[session_key] = [
                    {"role": "system", "content": self.system_prompt},
                    *history,
                ]
                self.store.last_active_at[session_key] = (
                    now_mono - age_sec - last_active_ago_sec
                )
                if remaining_sec > 0.0:
                    self.store.active_until[session_key] = (
                        now_mono + remaining_sec
                    )
                    user_id = _safe_int(state.get("userId"))
                    if user_id is not None:
                        self.store.active_user_ids[session_key] = user_id
                    self.store.awaiting_user_reply[session_key] = bool(
                        state.get("awaitingUserReply")
                    )
                    target = state.get("followupTarget")
                    if isinstance(target, dict):
                        restored_target: dict[str, int] = {}
                        channel_id = _safe_int(target.get("channelId"))
                        message_id = _safe_int(target.get("messageId"))
                        if channel_id is not None and channel_id >= 0:
                            restored_target["channel_id"] = channel_id
                        if message_id is not None and message_id >= 0:
                            restored_target["message_id"] = message_id
                        if restored_target:
                            self.store.followup_targets[session_key] = (
                                restored_target
                            )
                speaker = str(state.get("lastSpeaker") or "").strip().lower()
                if speaker in _ALLOWED_SPEAKERS:
                    self.store.last_speaker[session_key] = speaker
                topic_id = str(state.get("topicId") or "")[:80]
                turn_id = str(state.get("turnId") or "")[:80]
                if topic_id:
                    self.store.topic_ids[session_key] = topic_id
                if turn_id:
                    self.store.turn_ids[session_key] = turn_id
                restored += 1

            self._restored_session_count = restored
            self._last_restored_at = now_wall
            self._state = "restored"
            self._checkpoint_revoked_at = None
            self._last_signature = self._signature(self._material())
            self._write_status()
            self._emit(
                f"[SESSION CONTINUITY] restored sessions={restored}"
            )
            return self.status()

    def reset_guild(
        self,
        guild_id: int,
        reset_runtime_state: Callable[[], Any],
    ) -> dict[str, Any]:
        """Durably revoke a guild checkpoint before clearing its live state."""
        normalized_guild_id = _safe_int(guild_id)
        if normalized_guild_id is None or normalized_guild_id < 0:
            raise ValueError("invalid_guild_id")
        with self._lock:
            try:
                revocations = self._load_guild_revocations()
                revocations[normalized_guild_id] = self.wall_time()
                self._write_guild_revocations(revocations)
                self._state = "guild_reset_revoked"
                self._write_status(durable=True)
            except Exception as exc:
                self._record_error(
                    "conversation_continuity_guild_reset_revoke_failed",
                    exc,
                )
                raise RuntimeError(
                    "conversation_continuity_guild_reset_revoke_failed"
                ) from exc
            try:
                reset_runtime_state()
            except Exception as exc:
                self._record_error(
                    "conversation_continuity_guild_reset_failed",
                    exc,
                )
                raise
            result = self.flush(force=True)
            if result.get("state") == "error":
                return result
            try:
                revocations.pop(normalized_guild_id, None)
                self._write_guild_revocations(revocations)
                self._write_status(durable=True)
            except Exception as exc:
                return self._record_error(
                    "conversation_continuity_guild_reset_finalize_failed",
                    exc,
                )
            return self.status()

    def flush(
        self,
        *,
        force: bool = False,
        required_session_key: str = "",
        required_turn_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            try:
                snapshot = self._checkpoint_snapshot()
            except ContinuityAuthenticityError as exc:
                return self._record_authenticity_error(exc)
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                self._revoke_checkpoint_chain()
                return self._record_error(
                    "conversation_continuity_checkpoint_rejected",
                    exc,
                )
            try:
                snapshot = self._anchor_checkpoint_snapshot(
                    snapshot
                )
            except ContinuityAuthenticityError as exc:
                return self._record_authenticity_error(exc)
            except Exception as exc:
                self._checkpoint_head_state = "failed"
                return self._record_error(
                    "conversation_continuity_flush_failed",
                    exc,
                )
            if self._guild_revocations_anchor_configured():
                try:
                    self._guild_revocations = (
                        self._load_guild_revocations()
                    )
                except ContinuityAuthenticityError as exc:
                    return self._record_error(exc.code, exc)
                except (
                    OSError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ) as exc:
                    return self._record_error(
                        "conversation_continuity_checkpoint_rejected",
                        exc,
                    )
            try:
                material = self._material(
                    required_session_key=required_session_key,
                    required_turn_id=required_turn_id,
                )
                signature = self._signature(material)
            except Exception as exc:
                return self._record_error(
                    "conversation_continuity_flush_failed",
                    exc,
                )
            if not force and signature == self._last_signature:
                if (
                    snapshot["payload"] is not None
                    and self._state == "error"
                ):
                    self._state = "ready"
                    self._write_status()
                return self.status()
            sessions = material["sessions"]
            if not sessions:
                previous_generation = int(snapshot["generation"])
                previous_hash = str(snapshot["checkpointHash"])
                generation = previous_generation + 1
                try:
                    self._write_checkpoint_head(
                        state="empty",
                        generation=generation,
                        checkpoint_hash=(
                            SESSION_CONTINUITY_CHAIN_GENESIS
                        ),
                    )
                except Exception as exc:
                    self._revoke_checkpoint_chain()
                    return self._record_error(
                        "conversation_continuity_flush_failed",
                        exc,
                    )
                try:
                    self.authenticity.commit_external_anchor(
                        self.authenticity.checkpoint_anchor_slot(
                            self.authenticity_scope
                        ),
                        previous_generation=previous_generation,
                        previous_hash=previous_hash,
                        generation=generation,
                        artifact_hash=(
                            SESSION_CONTINUITY_CHAIN_GENESIS
                        ),
                        updated_at=self.wall_time(),
                    )
                    self._checkpoint_anchor_state = (
                        "verified"
                        if self.authenticity.external_anchor_configured
                        else "unconfigured"
                    )
                except ContinuityAuthenticityError as exc:
                    return self._record_authenticity_error(exc)
                self._discard_checkpoint()
                self._checkpoint_generation = generation
                self._checkpoint_integrity = "empty"
                self._checkpoint_head_state = "empty"
                self._last_signature = signature
                self._persisted_session_count = 0
                self._last_persisted_at = self.wall_time()
                self._checkpoint_revoked_at = None
                self._state = "empty"
                self._write_status()
                return self.status()
            saved_at = self.wall_time()
            generation = int(snapshot["generation"]) + 1
            previous_hash = (
                str(snapshot["checkpointHash"])
                if snapshot["payload"] is not None
                else SESSION_CONTINUITY_CHAIN_GENESIS
            )
            payload = self._payload_from_material(
                material,
                saved_at=saved_at,
                now_monotonic=self.monotonic(),
                generation=generation,
                previous_hash=previous_hash,
            )
            try:
                encoded = _canonical_json(payload).encode("utf-8")
                if len(encoded) > self.max_file_bytes:
                    raise ValueError("checkpoint_too_large")
                atomic_json_write(
                    self.checkpoint_path,
                    payload,
                    durable=True,
                )
            except Exception as exc:
                # A stale pre-reset checkpoint is more dangerous than losing
                # short-lived continuity. Fail closed and never resurrect it.
                self._revoke_checkpoint_chain()
                return self._record_error(
                    "conversation_continuity_flush_failed",
                    exc,
                )
            self._last_signature = signature
            self._persisted_session_count = len(sessions)
            self._last_persisted_at = saved_at
            self._checkpoint_revoked_at = None
            self._checkpoint_generation = generation
            self._checkpoint_integrity = "verified"
            try:
                self._write_checkpoint_head(
                    state="active",
                    generation=generation,
                    checkpoint_hash=str(
                        payload["checkpointHash"]
                    ),
                )
            except Exception as exc:
                self._checkpoint_head_state = "lagging"
                return self._record_error(
                    "conversation_continuity_flush_failed",
                    exc,
                )
            try:
                self.authenticity.commit_external_anchor(
                    self.authenticity.checkpoint_anchor_slot(
                        self.authenticity_scope
                    ),
                    previous_generation=int(snapshot["generation"]),
                    previous_hash=str(snapshot["checkpointHash"]),
                    generation=generation,
                    artifact_hash=str(payload["checkpointHash"]),
                    updated_at=self.wall_time(),
                )
                self._checkpoint_anchor_state = (
                    "verified"
                    if self.authenticity.external_anchor_configured
                    else "unconfigured"
                )
            except ContinuityAuthenticityError as exc:
                return self._record_authenticity_error(exc)
            self._checkpoint_head_state = "current"
            self._state = "ready"
            self._write_status()
            return self.status()

    @staticmethod
    def _snapshot_contains_commit_target(
        snapshot: dict[str, Any],
        *,
        session_key: str,
        turn_id: str,
    ) -> bool:
        payload = snapshot.get("payload")
        if not isinstance(payload, dict):
            return False
        sessions = payload.get("sessions")
        if not isinstance(sessions, list):
            return False
        for row in sessions:
            if (
                not isinstance(row, dict)
                or row.get("sessionKey") != session_key
            ):
                continue
            if not turn_id:
                return True
            state = row.get("state")
            return bool(
                isinstance(state, dict)
                and state.get("turnId") == turn_id
            )
        return False

    def commit_completed_turn(
        self,
        session_key: str,
        turn_id: str = "",
        *,
        before_commit: Callable[[int], Any] | None = None,
    ) -> dict[str, Any]:
        """Durably anchor the named completed turn before returning."""
        started_at = float(self.commit_latency_clock())
        try:
            required_session = _valid_session_key(session_key)
            required_turn = _valid_turn_id(turn_id)
            if (
                not required_session
                or (turn_id and not required_turn)
            ):
                raise ValueError("continuity_commit_target_invalid")
            with self._lock:
                if before_commit is not None:
                    snapshot = self._anchor_checkpoint_snapshot(
                        self._checkpoint_snapshot()
                    )
                    before_commit(int(snapshot["generation"]) + 1)
                status = self.flush(
                    force=True,
                    required_session_key=required_session,
                    required_turn_id=required_turn,
                )
                snapshot = self._checkpoint_snapshot()
                target_verified = bool(
                    snapshot.get("headState") == "current"
                    and (
                        not self.authenticity.configured
                        or snapshot.get("headAuthenticity")
                        == "verified"
                    )
                    and self._snapshot_contains_commit_target(
                        snapshot,
                        session_key=required_session,
                        turn_id=required_turn,
                    )
                )
                if (
                    status.get("state") == "error"
                    or status.get("rollbackProtected") is not True
                    or int(
                        status.get("persistedSessionCount") or 0
                    )
                    < 1
                    or not target_verified
                ):
                    raise RuntimeError(
                        "conversation_continuity_commit_failed"
                    )
                self._record_commit_attempt(
                    started_at=started_at,
                    succeeded=True,
                    target_verified=True,
                )
                self._write_status()
                return self.status()
        except Exception as exc:
            with self._lock:
                self._record_commit_attempt(
                    started_at=started_at,
                    succeeded=False,
                    target_verified=False,
                )
                if self._state != "error":
                    self.runtime_errors.record(
                        "conversation_continuity_commit_failed",
                        exc,
                    )
                    self._state = "error"
                self._write_status()
            raise RuntimeError(
                "conversation_continuity_commit_failed"
            ) from None

    async def commit_completed_turn_async(
        self,
        session_key: str,
        turn_id: str = "",
        *,
        before_commit: Callable[[int], Any] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.commit_completed_turn,
            session_key,
            turn_id,
            before_commit=before_commit,
        )

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval_sec)
            await asyncio.to_thread(self.flush)
            await asyncio.to_thread(self._write_status)

    def ensure_started(self) -> asyncio.Task[None]:
        task = self._task
        if task is not None and not task.done():
            return task
        self._task = asyncio.create_task(
            self._run(),
            name="evelyn-session-continuity",
        )
        return self._task


__all__ = [
    "DEFAULT_COMMIT_LATENCY_SAMPLE_LIMIT",
    "DEFAULT_COMMIT_LATENCY_WARNING_MIN_SAMPLES",
    "DEFAULT_COMMIT_LATENCY_WARNING_MS",
    "DEFAULT_FLUSH_INTERVAL_SEC",
    "DEFAULT_MAX_AGE_SEC",
    "SESSION_CONTINUITY_CHAIN_GENESIS",
    "SESSION_CONTINUITY_CHECKPOINT_SCHEMA",
    "SESSION_CONTINUITY_COMMIT_METRICS_SCHEMA",
    "SESSION_CONTINUITY_AUTHENTICATED_HEAD_SCHEMA",
    "SESSION_CONTINUITY_AUTHENTICATED_REVOCATIONS_SCHEMA",
    "SESSION_CONTINUITY_ANCHORED_REVOCATIONS_SCHEMA",
    "SESSION_CONTINUITY_HEAD_SCHEMA",
    "SESSION_CONTINUITY_LEGACY_CHECKPOINT_SCHEMA",
    "SESSION_CONTINUITY_REVOCATIONS_SCHEMA",
    "SESSION_CONTINUITY_STATUS_SCHEMA",
    "SessionContinuityCheckpoint",
]
