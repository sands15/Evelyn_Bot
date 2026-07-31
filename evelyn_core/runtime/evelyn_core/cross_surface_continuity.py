from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .session_continuity import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_GUILD_REVOCATIONS,
    DEFAULT_MAX_SESSIONS,
    SESSION_CONTINUITY_CHECKPOINT_SCHEMA,
    SESSION_CONTINUITY_HEAD_SCHEMA,
    SESSION_CONTINUITY_REVOCATIONS_SCHEMA,
)
from .text import clean_text


CROSS_SURFACE_CONTINUITY_STATUS_SCHEMA = (
    "cross_surface_continuity.status.v1"
)
DEFAULT_CROSS_SURFACE_MAX_AGE_SEC = 30 * 60.0
DEFAULT_CROSS_SURFACE_MAX_MESSAGES = 8
DEFAULT_CROSS_SURFACE_MAX_SESSIONS = 8
DEFAULT_CROSS_SURFACE_MAX_CONTENT_CHARS = 2000
DEFAULT_CROSS_SURFACE_FUTURE_SKEW_SEC = 60.0
_HEAD_MAX_BYTES = 128 * 1024
_REVOCATIONS_MAX_BYTES = 128 * 1024
_ALLOWED_ROLES = frozenset({"user", "assistant"})


def _finite_float(value: Any, *, default: float = -1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _valid_sha256(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64:
        return ""
    lowered = value.lower()
    if not all(character in "0123456789abcdef" for character in lowered):
        return ""
    return lowered


def _canonical_checkpoint_hash(payload: dict[str, Any]) -> str:
    unsigned = {
        key: value
        for key, value in payload.items()
        if key != "checkpointHash"
    }
    encoded = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_regular_json(
    path: Path,
    *,
    max_bytes: int,
    missing_ok: bool,
) -> dict[str, Any] | None:
    if not path.exists() and not path.is_symlink():
        if missing_ok:
            return None
        raise ValueError("artifact_missing")
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > max_bytes
    ):
        raise ValueError("artifact_rejected")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("artifact_rejected")
    return payload


def _parse_session_scope(
    session_key: str,
) -> tuple[int | None, int | None]:
    parts = session_key.split(":")
    guild_id: int | None = None
    user_id: int | None = None
    if len(parts) >= 2 and parts[0] == "guild":
        guild_id = _positive_int(parts[1])
    for index, part in enumerate(parts[:-1]):
        if part == "user":
            user_id = _positive_int(parts[index + 1])
    return guild_id, user_id


def session_scope_matches(
    session_key: str | None,
    *,
    guild_id: int | None,
    user_id: int | None,
) -> bool:
    if guild_id is None or user_id is None:
        return False
    parsed_guild_id, parsed_user_id = _parse_session_scope(
        clean_text(session_key)
    )
    return (
        parsed_guild_id == guild_id
        and parsed_user_id == user_id
    )


def _safe_history(
    value: Any,
    *,
    max_items: int,
    max_content_chars: int,
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("history_rejected")
    messages: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("history_rejected")
        role = clean_text(item.get("role")).lower()
        content = clean_text(item.get("content"))
        if role not in _ALLOWED_ROLES or not content:
            raise ValueError("history_rejected")
        if len(content) > max_content_chars:
            raise ValueError("history_rejected")
        messages.append(
            {
                "role": role,
                "content": content,
            }
        )
    return messages[-max_items:]


def _read_revocations(
    path: Path,
) -> dict[int, float]:
    payload = _read_regular_json(
        path,
        max_bytes=_REVOCATIONS_MAX_BYTES,
        missing_ok=True,
    )
    if payload is None:
        return {}
    policy = payload.get("policy")
    guilds = payload.get("guilds")
    if (
        set(payload)
        != {"schema", "updatedAt", "guilds", "policy"}
        or payload.get("schema")
        != SESSION_CONTINUITY_REVOCATIONS_SCHEMA
        or not isinstance(guilds, dict)
        or len(guilds) > DEFAULT_MAX_GUILD_REVOCATIONS
        or not isinstance(policy, dict)
        or set(policy) != {"contentFree", "maxGuilds"}
        or policy.get("contentFree") is not True
        or policy.get("maxGuilds")
        != DEFAULT_MAX_GUILD_REVOCATIONS
        or _finite_float(payload.get("updatedAt")) < 0.0
    ):
        raise ValueError("revocations_rejected")
    revocations: dict[int, float] = {}
    for raw_guild_id, raw_timestamp in guilds.items():
        guild_id = _positive_int(raw_guild_id)
        timestamp = _finite_float(raw_timestamp)
        if (
            guild_id is None
            or str(guild_id) != str(raw_guild_id)
            or timestamp < 0.0
        ):
            raise ValueError("revocations_rejected")
        revocations[guild_id] = timestamp
    return revocations


def _validated_head(
    payload: dict[str, Any],
) -> dict[str, Any]:
    expected_keys = {
        "schema",
        "state",
        "generation",
        "checkpointHash",
        "updatedAt",
        "contentFree",
    }
    generation = payload.get("generation")
    checkpoint_hash = _valid_sha256(
        payload.get("checkpointHash")
    )
    updated_at = _finite_float(payload.get("updatedAt"))
    state = clean_text(payload.get("state"))
    if (
        set(payload) != expected_keys
        or payload.get("schema")
        != SESSION_CONTINUITY_HEAD_SCHEMA
        or state not in {"active", "empty"}
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
        or not checkpoint_hash
        or updated_at < 0.0
        or payload.get("contentFree") is not True
        or (
            state == "active"
            and generation < 1
        )
        or (
            state == "empty"
            and checkpoint_hash != "0" * 64
        )
    ):
        raise ValueError("continuity_head_rejected")
    return {
        **payload,
        "state": state,
        "generation": generation,
        "checkpointHash": checkpoint_hash,
        "updatedAt": updated_at,
    }


@dataclass(frozen=True)
class CrossSurfaceContinuityConfig:
    enabled: bool
    guild_id: int | None
    user_id: int | None
    max_age_sec: float = DEFAULT_CROSS_SURFACE_MAX_AGE_SEC
    max_messages: int = DEFAULT_CROSS_SURFACE_MAX_MESSAGES

    @property
    def scope_ready(self) -> bool:
        return bool(
            self.enabled
            and self.guild_id is not None
            and self.user_id is not None
        )

    @classmethod
    def from_env(
        cls,
        environ: dict[str, str] | None = None,
    ) -> "CrossSurfaceContinuityConfig":
        values = os.environ if environ is None else environ
        enabled = clean_text(
            values.get("CROSS_SURFACE_CONTINUITY_ENABLED")
        ).lower() in {"1", "true", "yes", "on"}
        guild_id = _positive_int(
            values.get("CROSS_SURFACE_CONTINUITY_GUILD_ID")
        )
        user_id = _positive_int(
            values.get("CROSS_SURFACE_CONTINUITY_USER_ID")
        )
        max_age_sec = _finite_float(
            values.get("CROSS_SURFACE_CONTINUITY_MAX_AGE_SEC"),
            default=DEFAULT_CROSS_SURFACE_MAX_AGE_SEC,
        )
        max_messages = _positive_int(
            values.get("CROSS_SURFACE_CONTINUITY_MAX_MESSAGES")
        )
        return cls(
            enabled=enabled,
            guild_id=guild_id,
            user_id=user_id,
            max_age_sec=max(
                60.0,
                min(
                    DEFAULT_CROSS_SURFACE_MAX_AGE_SEC,
                    max_age_sec,
                ),
            ),
            max_messages=max(
                2,
                min(
                    20,
                    max_messages
                    or DEFAULT_CROSS_SURFACE_MAX_MESSAGES,
                ),
            ),
        )

    def public_status(self) -> dict[str, Any]:
        if not self.enabled:
            state = "disabled"
            error_code = ""
        elif not self.scope_ready:
            state = "unavailable"
            error_code = "cross_surface_scope_not_configured"
        else:
            state = "configured"
            error_code = ""
        return {
            "schema": CROSS_SURFACE_CONTINUITY_STATUS_SCHEMA,
            "state": state,
            "enabled": self.enabled,
            "scopeReady": self.scope_ready,
            "errorCode": error_code,
            "policy": {
                "contentFree": True,
                "readOnly": True,
                "maxAgeSec": self.max_age_sec,
                "maxMessages": self.max_messages,
            },
        }


@dataclass(frozen=True)
class VerifiedContinuitySnapshot:
    source: str
    state: str
    saved_at: float | None = None
    generation: int = 0
    messages: tuple[dict[str, str], ...] = ()
    session_count: int = 0
    error_code: str = ""

    @property
    def verified(self) -> bool:
        return self.state == "verified"

    def public_status(self) -> dict[str, Any]:
        return {
            "schema": CROSS_SURFACE_CONTINUITY_STATUS_SCHEMA,
            "source": self.source,
            "state": self.state,
            "generation": self.generation,
            "messageCount": len(self.messages),
            "sessionCount": self.session_count,
            "errorCode": self.error_code,
            "policy": {
                "contentFree": True,
                "readOnly": True,
            },
        }


class CrossSurfaceContinuityBridge:
    """Read and merge two independently owned continuity checkpoints."""

    def __init__(
        self,
        *,
        artifacts_root: Path,
        config: CrossSurfaceContinuityConfig,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.artifacts_root = Path(artifacts_root)
        self.config = config
        self.wall_time = wall_time

    def _read_main(self) -> VerifiedContinuitySnapshot:
        return read_verified_continuity_snapshot(
            self.artifacts_root / "conversation_continuity",
            source="main",
            wall_time=self.wall_time,
            max_age_sec=self.config.max_age_sec,
            max_sessions=1,
            max_messages=self.config.max_messages,
            guild_id=self.config.guild_id,
            user_id=self.config.user_id,
        )

    def _read_fast(self) -> VerifiedContinuitySnapshot:
        return read_verified_continuity_snapshot(
            self.artifacts_root / "fast_control_continuity",
            source="fast_control",
            wall_time=self.wall_time,
            max_age_sec=self.config.max_age_sec,
            max_messages=self.config.max_messages,
        )

    def merge_for_main(
        self,
        local_messages: Iterable[dict[str, Any]],
        *,
        session_key: str | None,
        current_user_text: str,
    ) -> list[dict[str, Any]]:
        source_messages = [
            dict(message)
            for message in local_messages
            if isinstance(message, dict)
        ]
        if (
            not self.config.scope_ready
            or not session_scope_matches(
                session_key,
                guild_id=self.config.guild_id,
                user_id=self.config.user_id,
            )
        ):
            return source_messages
        local_snapshot = self._read_main()
        cross_snapshot = self._read_fast()
        if _cross_snapshot_precedes_empty_local_boundary(
            local_snapshot,
            cross_snapshot,
        ):
            return source_messages
        return merge_verified_recent_context(
            source_messages,
            cross_snapshot,
            local_saved_at=local_snapshot.saved_at,
            current_user_text=current_user_text,
            limit=self.config.max_messages,
        )

    def merge_for_fast(
        self,
        local_messages: Iterable[dict[str, Any]],
        *,
        current_user_text: str,
    ) -> list[dict[str, Any]]:
        source_messages = [
            dict(message)
            for message in local_messages
            if isinstance(message, dict)
        ]
        if not self.config.scope_ready:
            return source_messages
        local_snapshot = self._read_fast()
        cross_snapshot = self._read_main()
        if _cross_snapshot_precedes_empty_local_boundary(
            local_snapshot,
            cross_snapshot,
        ):
            return source_messages
        return merge_verified_recent_context(
            source_messages,
            cross_snapshot,
            local_saved_at=local_snapshot.saved_at,
            current_user_text=current_user_text,
            limit=self.config.max_messages,
        )

    def public_status(self) -> dict[str, Any]:
        config_status = self.config.public_status()
        if not self.config.scope_ready:
            return config_status
        main_snapshot = self._read_main()
        fast_snapshot = self._read_fast()
        states = {
            main_snapshot.state,
            fast_snapshot.state,
        }
        state = (
            "rejected"
            if "rejected" in states
            else (
                "ready"
                if "verified" in states
                else "waiting"
            )
        )
        return {
            **config_status,
            "state": state,
            "owners": {
                "main": main_snapshot.public_status(),
                "fastControl": fast_snapshot.public_status(),
            },
        }


def read_verified_continuity_snapshot(
    owner_root: Path,
    *,
    source: str,
    wall_time: Callable[[], float] = time.time,
    max_age_sec: float = DEFAULT_CROSS_SURFACE_MAX_AGE_SEC,
    max_sessions: int = DEFAULT_CROSS_SURFACE_MAX_SESSIONS,
    max_messages: int = DEFAULT_CROSS_SURFACE_MAX_MESSAGES,
    max_content_chars: int = (
        DEFAULT_CROSS_SURFACE_MAX_CONTENT_CHARS
    ),
    guild_id: int | None = None,
    user_id: int | None = None,
) -> VerifiedContinuitySnapshot:
    root = Path(owner_root)
    checkpoint_path = root / "active.json"
    if (
        not checkpoint_path.exists()
        and not checkpoint_path.is_symlink()
    ):
        try:
            empty_head = _read_regular_json(
                root / "checkpoint_head.json",
                max_bytes=_HEAD_MAX_BYTES,
                missing_ok=True,
            )
            if empty_head is None:
                return VerifiedContinuitySnapshot(
                    source=source,
                    state="missing",
                )
            validated_empty_head = _validated_head(
                empty_head
            )
            if validated_empty_head["state"] != "empty":
                raise ValueError(
                    "checkpoint_missing_after_active_head"
                )
            return VerifiedContinuitySnapshot(
                source=source,
                state="empty",
                saved_at=validated_empty_head["updatedAt"],
                generation=validated_empty_head[
                    "generation"
                ],
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            return VerifiedContinuitySnapshot(
                source=source,
                state="rejected",
                error_code="cross_surface_continuity_rejected",
            )
    try:
        head = _read_regular_json(
            root / "checkpoint_head.json",
            max_bytes=_HEAD_MAX_BYTES,
            missing_ok=False,
        )
        checkpoint = _read_regular_json(
            checkpoint_path,
            max_bytes=DEFAULT_MAX_FILE_BYTES,
            missing_ok=False,
        )
        if head is None or checkpoint is None:
            raise ValueError("continuity_artifact_missing")
        head = _validated_head(head)
        head_generation = head["generation"]
        head_hash = head["checkpointHash"]
        if head["state"] != "active":
            raise ValueError("continuity_head_rejected")
        generation = checkpoint.get("generation")
        checkpoint_hash = _valid_sha256(
            checkpoint.get("checkpointHash")
        )
        previous_hash = _valid_sha256(
            checkpoint.get("previousHash")
        )
        if (
            checkpoint.get("schema")
            != SESSION_CONTINUITY_CHECKPOINT_SCHEMA
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
            or not checkpoint_hash
            or not previous_hash
            or checkpoint_hash
            != _canonical_checkpoint_hash(checkpoint)
            or generation != head_generation
            or checkpoint_hash != head_hash
        ):
            raise ValueError("continuity_integrity_rejected")
        policy = checkpoint.get("policy")
        if (
            not isinstance(policy, dict)
            or policy.get("completedTurnText") is not True
            or policy.get("rawAudio") is not False
            or policy.get("partialTranscript") is not False
            or policy.get("systemPrompt") is not False
        ):
            raise ValueError("continuity_policy_rejected")
        saved_at = _finite_float(checkpoint.get("savedAt"))
        expires_at = _finite_float(
            checkpoint.get("expiresAt")
        )
        now = _finite_float(wall_time(), default=0.0)
        bounded_max_age = max(
            60.0,
            min(
                DEFAULT_CROSS_SURFACE_MAX_AGE_SEC,
                _finite_float(
                    max_age_sec,
                    default=DEFAULT_CROSS_SURFACE_MAX_AGE_SEC,
                ),
            ),
        )
        if (
            saved_at < 0.0
            or saved_at
            > now + DEFAULT_CROSS_SURFACE_FUTURE_SKEW_SEC
            or expires_at < saved_at
        ):
            raise ValueError("continuity_timestamp_rejected")
        if now > expires_at or now - saved_at > bounded_max_age:
            return VerifiedContinuitySnapshot(
                source=source,
                state="stale",
                saved_at=saved_at,
                generation=generation,
                error_code="cross_surface_continuity_stale",
            )
        revocations = _read_revocations(
            root / "guild_revocations.json"
        )
        sessions = checkpoint.get("sessions")
        if not isinstance(sessions, list):
            raise ValueError("continuity_sessions_rejected")
        selected: list[tuple[str, list[dict[str, str]]]] = []
        selection_limit = max(1, int(max_sessions))
        for row in sessions[:DEFAULT_MAX_SESSIONS]:
            if not isinstance(row, dict):
                raise ValueError("continuity_session_rejected")
            session_key = clean_text(row.get("sessionKey"))
            if not session_key or len(session_key) > 256:
                raise ValueError("continuity_session_rejected")
            row_guild_id, row_user_id = _parse_session_scope(
                session_key
            )
            if (
                row_guild_id is not None
                and revocations.get(row_guild_id, -1.0)
                >= saved_at
            ):
                continue
            if guild_id is not None or user_id is not None:
                if (
                    row_guild_id != guild_id
                    or row_user_id != user_id
                ):
                    continue
            selected.append(
                (
                    session_key,
                    _safe_history(
                        row.get("history"),
                        max_items=max(
                            2,
                            int(max_messages),
                        ),
                        max_content_chars=max(
                            128,
                            int(max_content_chars),
                        ),
                    ),
                )
            )
            if len(selected) >= selection_limit:
                break
        messages: list[dict[str, str]] = []
        # The writer stores newest sessions first. Reverse session order so
        # the newest selected session is closest to the current user turn.
        for _session_key, history in reversed(selected):
            messages.extend(history)
        messages = _dedupe_adjacent(messages)[
            -max(2, int(max_messages)) :
        ]
        return VerifiedContinuitySnapshot(
            source=source,
            state="verified",
            saved_at=saved_at,
            generation=generation,
            messages=tuple(messages),
            session_count=len(selected),
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        return VerifiedContinuitySnapshot(
            source=source,
            state="rejected",
            error_code="cross_surface_continuity_rejected",
        )


def _normalized_messages(
    messages: Iterable[dict[str, Any]],
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = clean_text(message.get("role")).lower()
        content = clean_text(message.get("content"))
        if role in _ALLOWED_ROLES and content:
            normalized.append(
                {
                    "role": role,
                    "content": content,
                }
            )
    return normalized


def _dedupe_adjacent(
    messages: Iterable[dict[str, Any]],
) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    for message in _normalized_messages(messages):
        if (
            deduped
            and deduped[-1]["role"] == message["role"]
            and deduped[-1]["content"] == message["content"]
        ):
            continue
        deduped.append(message)
    return deduped


def _cross_snapshot_precedes_empty_local_boundary(
    local_snapshot: VerifiedContinuitySnapshot,
    cross_snapshot: VerifiedContinuitySnapshot,
) -> bool:
    if local_snapshot.state not in {"verified", "empty"}:
        return False
    if local_snapshot.messages:
        return False
    local_saved_at = _finite_float(
        local_snapshot.saved_at,
        default=-1.0,
    )
    cross_saved_at = _finite_float(
        cross_snapshot.saved_at,
        default=-1.0,
    )
    return (
        local_saved_at >= 0.0
        and cross_saved_at >= 0.0
        and cross_saved_at <= local_saved_at
    )


def merge_verified_recent_context(
    local_messages: Iterable[dict[str, Any]],
    cross_snapshot: VerifiedContinuitySnapshot,
    *,
    local_saved_at: float | None = None,
    current_user_text: str = "",
    limit: int = DEFAULT_CROSS_SURFACE_MAX_MESSAGES,
) -> list[dict[str, Any]]:
    source_messages = [
        dict(message)
        for message in local_messages
        if isinstance(message, dict)
    ]
    trusted_prefix = [
        message
        for message in source_messages
        if clean_text(message.get("role")).lower()
        not in _ALLOWED_ROLES
    ]
    local_recent = _normalized_messages(source_messages)
    current = clean_text(current_user_text)
    if (
        current
        and local_recent
        and local_recent[-1]["role"] == "user"
        and local_recent[-1]["content"] == current
    ):
        local_recent.pop()
    if not cross_snapshot.verified or not cross_snapshot.messages:
        return source_messages
    cross_recent = _normalized_messages(
        cross_snapshot.messages
    )
    if (
        current
        and cross_recent
        and cross_recent[-1]["role"] == "user"
        and cross_recent[-1]["content"] == current
    ):
        cross_recent.pop()
    local_timestamp = _finite_float(
        local_saved_at,
        default=-1.0,
    )
    cross_timestamp = _finite_float(
        cross_snapshot.saved_at,
        default=-1.0,
    )
    if (
        local_timestamp >= 0.0
        and cross_timestamp >= 0.0
        and cross_timestamp < local_timestamp
    ):
        combined = [*cross_recent, *local_recent]
    else:
        combined = [*local_recent, *cross_recent]
    return [
        *trusted_prefix,
        *_dedupe_adjacent(combined)[
            -max(2, int(limit)) :
        ],
    ]


__all__ = [
    "CROSS_SURFACE_CONTINUITY_STATUS_SCHEMA",
    "CrossSurfaceContinuityBridge",
    "CrossSurfaceContinuityConfig",
    "VerifiedContinuitySnapshot",
    "merge_verified_recent_context",
    "read_verified_continuity_snapshot",
    "session_scope_matches",
]
