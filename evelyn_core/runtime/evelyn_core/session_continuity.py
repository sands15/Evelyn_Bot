from __future__ import annotations

import asyncio
import hashlib
import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .runtime_artifact_io import atomic_json_write
from .runtime_error_observability import RuntimeErrorCounter
from .text import clean_text


SESSION_CONTINUITY_CHECKPOINT_SCHEMA = "conversation_continuity.checkpoint.v1"
SESSION_CONTINUITY_STATUS_SCHEMA = "conversation_continuity.status.v1"
DEFAULT_MAX_AGE_SEC = 15 * 60.0
DEFAULT_FLUSH_INTERVAL_SEC = 1.0
DEFAULT_MAX_SESSIONS = 32
DEFAULT_MAX_HISTORY_ITEMS = 12
DEFAULT_MAX_CONTENT_CHARS = 2000
DEFAULT_MAX_FILE_BYTES = 1024 * 1024
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


def _safe_history(value: Any, *, max_items: int, max_chars: int) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in _ALLOWED_HISTORY_ROLES:
            continue
        content = clean_text(str(item.get("content") or ""))[:max_chars]
        if content:
            rows.append({"role": role, "content": content})
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


class SessionContinuityCheckpoint:
    """Persists a bounded, short-lived completed-turn checkpoint across restarts."""

    def __init__(
        self,
        *,
        store: Any,
        checkpoint_path: Path,
        status_path: Path,
        system_prompt: str,
        max_age_sec: float = DEFAULT_MAX_AGE_SEC,
        flush_interval_sec: float = DEFAULT_FLUSH_INTERVAL_SEC,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        max_history_items: int = DEFAULT_MAX_HISTORY_ITEMS,
        max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        wall_time: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        log: Callable[[str], Any] | None = None,
    ) -> None:
        self.store = store
        self.checkpoint_path = Path(checkpoint_path)
        self.status_path = Path(status_path)
        self.system_prompt = str(system_prompt)
        self.max_age_sec = max(60.0, float(max_age_sec))
        self.flush_interval_sec = max(0.25, float(flush_interval_sec))
        self.max_sessions = max(1, int(max_sessions))
        self.max_history_items = max(2, int(max_history_items))
        self.max_content_chars = max(128, int(max_content_chars))
        self.max_file_bytes = max(4096, int(max_file_bytes))
        self.wall_time = wall_time
        self.monotonic = monotonic
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

    def _emit(self, message: str) -> None:
        if self.log is not None:
            self.log(message)

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

    def _selected_keys(self) -> list[str]:
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
        valid = [key for key in candidates if _valid_session_key(key)]
        return sorted(
            valid,
            key=lambda key: (
                -_finite_float(self.store.last_active_at.get(key)),
                key,
            ),
        )[: self.max_sessions]

    def _material(self) -> dict[str, Any]:
        sessions: list[dict[str, Any]] = []
        for session_key in self._selected_keys():
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
                        self.store.last_active_at.get(session_key)
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
    ) -> dict[str, Any]:
        sessions: list[dict[str, Any]] = []
        for source in material["sessions"]:
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
        return {
            "schema": SESSION_CONTINUITY_CHECKPOINT_SCHEMA,
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
            "policy": {
                "maxAgeSec": self.max_age_sec,
                "flushIntervalSec": self.flush_interval_sec,
                "rawAudio": False,
                "partialTranscript": False,
            },
            **self.runtime_errors.snapshot(),
        }

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
            if not self.checkpoint_path.exists():
                self._checkpoint_revoked_at = None
                self._state = "missing"
                self._write_status()
                return self.status()
            try:
                if (
                    self.checkpoint_path.is_symlink()
                    or not self.checkpoint_path.is_file()
                ):
                    raise ValueError("checkpoint_rejected")
                if self.checkpoint_path.stat().st_size > self.max_file_bytes:
                    self._discard_checkpoint()
                    return self._record_error(
                        "conversation_continuity_checkpoint_rejected",
                        ValueError("checkpoint_too_large"),
                    )
                payload = json.loads(
                    self.checkpoint_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._discard_checkpoint()
                return self._record_error(
                    "conversation_continuity_restore_failed",
                    exc,
                )
            if (
                not isinstance(payload, dict)
                or payload.get("schema") != SESSION_CONTINUITY_CHECKPOINT_SCHEMA
            ):
                self._discard_checkpoint()
                return self._record_error(
                    "conversation_continuity_checkpoint_rejected",
                    ValueError("invalid_schema"),
                )
            saved_at = _finite_float(payload.get("savedAt"), default=-1.0)
            now_wall = self.wall_time()
            if saved_at < 0.0 or saved_at > now_wall + 60.0:
                self._discard_checkpoint()
                return self._record_error(
                    "conversation_continuity_checkpoint_rejected",
                    ValueError("invalid_saved_at"),
                )
            if revoked_at is not None and revoked_at >= saved_at:
                self._discard_checkpoint()
                return self._record_error(
                    "conversation_continuity_checkpoint_rejected",
                    ValueError("checkpoint_revoked"),
                )
            age_sec = max(0.0, now_wall - saved_at)
            if age_sec > self.max_age_sec:
                self._discard_checkpoint()
                self._checkpoint_revoked_at = None
                self._state = "stale"
                self._write_status()
                return self.status()

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
                history = _safe_history(
                    row.get("history"),
                    max_items=self.max_history_items,
                    max_chars=self.max_content_chars,
                )
                state = row.get("state")
                state = state if isinstance(state, dict) else {}
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
                if remaining_sec > 0.0:
                    self.store.active_until[session_key] = (
                        now_mono + remaining_sec
                    )
                    self.store.last_active_at[session_key] = now_mono
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

    def flush(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            try:
                material = self._material()
                signature = self._signature(material)
            except Exception as exc:
                return self._record_error(
                    "conversation_continuity_flush_failed",
                    exc,
                )
            if not force and signature == self._last_signature:
                return self.status()
            sessions = material["sessions"]
            try:
                if not sessions:
                    self.checkpoint_path.unlink(missing_ok=True)
                    self._last_signature = signature
                    self._persisted_session_count = 0
                    self._last_persisted_at = self.wall_time()
                    self._checkpoint_revoked_at = None
                    self._state = "empty"
                    self._write_status()
                    return self.status()
                saved_at = self.wall_time()
                payload = self._payload_from_material(
                    material,
                    saved_at=saved_at,
                    now_monotonic=self.monotonic(),
                )
                encoded = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
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
                self._checkpoint_revoked_at = self.wall_time()
                self._discard_checkpoint()
                return self._record_error(
                    "conversation_continuity_flush_failed",
                    exc,
                )
            self._last_signature = signature
            self._persisted_session_count = len(sessions)
            self._last_persisted_at = saved_at
            self._checkpoint_revoked_at = None
            self._state = "ready"
            self._write_status()
            return self.status()

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
    "DEFAULT_FLUSH_INTERVAL_SEC",
    "DEFAULT_MAX_AGE_SEC",
    "SESSION_CONTINUITY_CHECKPOINT_SCHEMA",
    "SESSION_CONTINUITY_STATUS_SCHEMA",
    "SessionContinuityCheckpoint",
]
