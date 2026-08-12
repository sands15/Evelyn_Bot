from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .runtime_artifact_io import atomic_json_write
from .text import clean_text


SEARCH_FOLLOWUP_RECOVERY_SCHEMA = "search_followup.recovery.v1"
SEARCH_FOLLOWUP_RECOVERY_HEAD_SCHEMA = "search_followup.recovery-head.v1"
SEARCH_FOLLOWUP_RECOVERY_CHAIN_GENESIS = "0" * 64
SEARCH_FOLLOWUP_RECOVERY_MAX_BYTES = 256 * 1024
SEARCH_FOLLOWUP_RECOVERY_MAX_ENTRIES = 40
_INTENT_ID = re.compile(r"^search-followup-[0-9a-f]{24}$")
_PHASES = frozenset(
    {
        "running",
        "delivery_preparing",
        "delivery_ready",
        "delivery_attempted",
        "delivery_uncertain",
        "request_unrecoverable",
    }
)


def content_sha256(
    value: Any,
    *,
    normalized: bool = True,
) -> str:
    material = (
        clean_text(value)
        if normalized
        else str(value or "")
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _valid_sha256(value: Any) -> str:
    candidate = clean_text(value).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", candidate):
        raise ValueError("search_followup_hash_invalid")
    return candidate


def _finite_nonnegative(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("search_followup_timestamp_invalid")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("search_followup_timestamp_invalid") from exc
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError("search_followup_timestamp_invalid")
    return parsed


def _nonnegative_int(value: Any, *, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(code)
    return value


def _optional_positive_int(value: Any, *, code: str) -> int | None:
    if value is None:
        return None
    parsed = _nonnegative_int(value, code=code)
    if parsed < 1:
        raise ValueError(code)
    return parsed


def _bounded_key(value: Any, *, required: bool = False) -> str | None:
    candidate = clean_text(value)
    if not candidate:
        if required:
            raise ValueError("search_followup_key_invalid")
        return None
    if len(candidate) > 512 or any(ord(char) < 32 for char in candidate):
        raise ValueError("search_followup_key_invalid")
    return candidate


def _journal_hash(payload: dict[str, Any]) -> str:
    material = {key: value for key, value in payload.items() if key != "journalHash"}
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SearchFollowupRecoveryJournal:
    """Durable, content-free state for promised search follow-ups."""

    def __init__(
        self,
        *,
        path: Path,
        head_path: Path | None = None,
        enabled: bool = True,
        wall_time: Callable[[], float] = time.time,
        intent_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.head_path = Path(
            head_path
            if head_path is not None
            else self.path.with_name(f"{self.path.stem}.head.json")
        )
        self.enabled = bool(enabled)
        self.wall_time = wall_time
        self.intent_id_factory = intent_id_factory or (
            lambda: f"search-followup-{uuid.uuid4().hex[:24]}"
        )
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._recovery_claims: set[str] = set()
        self._generation = 0
        self._journal_hash = SEARCH_FOLLOWUP_RECOVERY_CHAIN_GENESIS
        self._integrity = "disabled" if not self.enabled else "uninitialized"
        self._head_state = "disabled" if not self.enabled else "missing"
        self._load_state = "disabled" if not self.enabled else "ready"
        self._last_error_code = ""
        if self.enabled:
            self._load()

    def _now(self) -> float:
        return _finite_nonnegative(self.wall_time())

    def _payload(self, *, generation: int, previous_hash: str) -> dict[str, Any]:
        payload = {
            "schema": SEARCH_FOLLOWUP_RECOVERY_SCHEMA,
            "generation": generation,
            "previousHash": previous_hash,
            "journalHash": "",
            "updatedAt": self._now(),
            "entries": [dict(entry) for entry in self._entries.values()],
            "lastErrorCode": self._last_error_code,
            "policy": {
                "contentFree": True,
                "rawQuery": False,
                "rawTranscript": False,
                "duplicateDeliveryPolicy": "verify_text_or_fail_closed",
                "maxEntries": SEARCH_FOLLOWUP_RECOVERY_MAX_ENTRIES,
            },
        }
        payload["journalHash"] = _journal_hash(payload)
        return payload

    def _validated_entry(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("search_followup_entry_invalid")
        intent_id = clean_text(raw.get("intentId"))
        if not _INTENT_ID.fullmatch(intent_id):
            raise ValueError("search_followup_intent_id_invalid")
        phase = clean_text(raw.get("phase"))
        if phase not in _PHASES:
            raise ValueError("search_followup_phase_invalid")
        source = clean_text(raw.get("source"))
        if source not in {"text", "voice"}:
            raise ValueError("search_followup_source_invalid")
        entry = {
            "intentId": intent_id,
            "phase": phase,
            "source": source,
            "guildId": _nonnegative_int(raw.get("guildId"), code="search_followup_guild_invalid"),
            "sessionKey": _bounded_key(raw.get("sessionKey"), required=True),
            "turnId": _bounded_key(raw.get("turnId")),
            "deliveryTurnId": _bounded_key(raw.get("deliveryTurnId")),
            "roomKey": _bounded_key(raw.get("roomKey")),
            "personKey": _bounded_key(raw.get("personKey")),
            "sessionMemoryKey": _bounded_key(raw.get("sessionMemoryKey")),
            "channelId": _optional_positive_int(raw.get("channelId"), code="search_followup_channel_invalid"),
            "replyToMessageId": _optional_positive_int(raw.get("replyToMessageId"), code="search_followup_message_invalid"),
            "requestUserHash": _valid_sha256(raw.get("requestUserHash")),
            "requestAnswerHash": _valid_sha256(raw.get("requestAnswerHash")),
            "queryHash": _valid_sha256(raw.get("queryHash")),
            "answerHash": clean_text(raw.get("answerHash")),
            "displayHash": clean_text(raw.get("displayHash")),
            "continuityGeneration": _nonnegative_int(raw.get("continuityGeneration"), code="search_followup_continuity_generation_invalid"),
            "deliveryGeneration": _nonnegative_int(raw.get("deliveryGeneration"), code="search_followup_delivery_generation_invalid"),
            "attemptCount": _nonnegative_int(raw.get("attemptCount"), code="search_followup_attempt_invalid"),
            "createdAt": _finite_nonnegative(raw.get("createdAt")),
            "updatedAt": _finite_nonnegative(raw.get("updatedAt")),
            "lastErrorCode": clean_text(raw.get("lastErrorCode"))[:120],
        }
        if entry["guildId"] < 1:
            raise ValueError("search_followup_guild_invalid")
        for key in ("answerHash", "displayHash"):
            value = entry[key]
            if value:
                entry[key] = _valid_sha256(value)
        if phase in {
            "delivery_preparing",
            "delivery_ready",
            "delivery_attempted",
            "delivery_uncertain",
        } and not entry["answerHash"]:
            raise ValueError("search_followup_delivery_anchor_invalid")
        if phase in {
            "delivery_ready",
            "delivery_attempted",
            "delivery_uncertain",
        } and entry["deliveryGeneration"] < 1:
            raise ValueError("search_followup_delivery_anchor_invalid")
        return entry

    def _validated_payload(self, payload: Any) -> tuple[dict[str, dict[str, Any]], int, str, str, str]:
        if not isinstance(payload, dict) or clean_text(payload.get("schema")) != SEARCH_FOLLOWUP_RECOVERY_SCHEMA:
            raise ValueError("search_followup_schema_invalid")
        generation = _nonnegative_int(payload.get("generation"), code="search_followup_generation_invalid")
        if generation < 1:
            raise ValueError("search_followup_generation_invalid")
        previous_hash = _valid_sha256(payload.get("previousHash"))
        journal_hash = _valid_sha256(payload.get("journalHash"))
        if journal_hash != _journal_hash(payload):
            raise ValueError("search_followup_self_hash_mismatch")
        entries_raw = payload.get("entries")
        if not isinstance(entries_raw, list) or len(entries_raw) > SEARCH_FOLLOWUP_RECOVERY_MAX_ENTRIES:
            raise ValueError("search_followup_entries_invalid")
        entries: dict[str, dict[str, Any]] = {}
        sessions: set[str] = set()
        for raw in entries_raw:
            entry = self._validated_entry(raw)
            if entry["intentId"] in entries or entry["sessionKey"] in sessions:
                raise ValueError("search_followup_duplicate_entry")
            entries[entry["intentId"]] = entry
            sessions.add(entry["sessionKey"])
        return entries, generation, previous_hash, journal_hash, clean_text(payload.get("lastErrorCode"))[:120]

    def _load_head(self) -> dict[str, Any] | None:
        if not self.head_path.exists() and not self.head_path.is_symlink():
            return None
        if self.head_path.is_symlink() or not self.head_path.is_file():
            raise ValueError("search_followup_head_invalid")
        payload = json.loads(self.head_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or clean_text(payload.get("schema")) != SEARCH_FOLLOWUP_RECOVERY_HEAD_SCHEMA:
            raise ValueError("search_followup_head_invalid")
        return {
            "generation": _nonnegative_int(payload.get("generation"), code="search_followup_head_generation_invalid"),
            "journalHash": _valid_sha256(payload.get("journalHash")),
        }

    def _write_head(self, *, generation: int, journal_hash: str) -> None:
        atomic_json_write(
            self.head_path,
            {
                "schema": SEARCH_FOLLOWUP_RECOVERY_HEAD_SCHEMA,
                "generation": generation,
                "journalHash": journal_hash,
                "updatedAt": self._now(),
                "contentFree": True,
            },
            durable=True,
        )

    def _load(self) -> None:
        head: dict[str, Any] | None = None
        try:
            head = self._load_head()
            missing = not self.path.exists() and not self.path.is_symlink()
            if missing:
                if head is not None:
                    raise ValueError("search_followup_journal_missing_after_head")
                self._integrity = "missing"
                return
            if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > SEARCH_FOLLOWUP_RECOVERY_MAX_BYTES:
                raise ValueError("search_followup_journal_invalid")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            entries, generation, previous_hash, journal_hash, error_code = self._validated_payload(payload)
            if head is None:
                if generation != 1 or previous_hash != SEARCH_FOLLOWUP_RECOVERY_CHAIN_GENESIS:
                    raise ValueError("search_followup_head_missing")
                self._write_head(generation=generation, journal_hash=journal_hash)
            elif generation == head["generation"] and journal_hash == head["journalHash"]:
                pass
            elif generation == head["generation"] + 1 and previous_hash == head["journalHash"]:
                self._write_head(generation=generation, journal_hash=journal_hash)
            else:
                raise ValueError("search_followup_rollback_or_head_mismatch")
            self._entries = entries
            self._generation = generation
            self._journal_hash = journal_hash
            self._last_error_code = error_code
            self._integrity = "verified"
            self._head_state = "current"
            self._load_state = "ready"
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            self._entries = {}
            if head is not None:
                self._generation = int(head["generation"])
                self._journal_hash = str(head["journalHash"])
                self._head_state = "orphaned"
            self._load_state = "corrupt"
            self._integrity = "failed"
            self._last_error_code = "search_followup_recovery_journal_corrupt"

    @staticmethod
    def _target_allowed(path: Path) -> bool:
        return not path.is_symlink() and (not path.exists() or path.is_file())

    def _write(self) -> None:
        if not self.enabled:
            return
        if self._load_state not in {"ready"}:
            raise RuntimeError("search_followup_recovery_unavailable")
        if not self._target_allowed(self.path) or not self._target_allowed(self.head_path):
            raise OSError("search_followup_recovery_target_rejected")
        generation = self._generation + 1
        payload = self._payload(generation=generation, previous_hash=self._journal_hash)
        journal_hash = str(payload["journalHash"])
        try:
            atomic_json_write(self.path, payload, durable=True)
            self._write_head(generation=generation, journal_hash=journal_hash)
        except Exception:
            self._load_state = "error"
            self._integrity = "failed"
            self._head_state = "write_failed"
            self._last_error_code = "search_followup_recovery_write_failed"
            raise
        self._generation = generation
        self._journal_hash = journal_hash
        self._integrity = "verified"
        self._head_state = "current"

    def begin(
        self,
        *,
        guild_id: int,
        session_key: str,
        source: str,
        turn_id: str | None,
        room_key: str | None,
        person_key: str | None,
        session_memory_key: str | None,
        channel_id: int | None,
        reply_to_message_id: int | None,
        request_user_text: str,
        request_answer_text: str,
        query: str,
        continuity_generation: int,
    ) -> str | None:
        if not self.enabled:
            return None
        with self._lock:
            continuity_generation = _nonnegative_int(
                continuity_generation,
                code="search_followup_continuity_generation_invalid",
            )
            if continuity_generation < 1:
                raise ValueError(
                    "search_followup_continuity_generation_invalid"
                )
            intent_id = clean_text(self.intent_id_factory())
            if not _INTENT_ID.fullmatch(intent_id):
                raise ValueError("search_followup_intent_id_invalid")
            now = self._now()
            normalized_session = _bounded_key(session_key, required=True)
            self._entries = {
                key: value
                for key, value in self._entries.items()
                if value["sessionKey"] != normalized_session
            }
            self._recovery_claims.intersection_update(
                self._entries
            )
            entry = self._validated_entry(
                {
                    "intentId": intent_id,
                    "phase": "running",
                    "source": source,
                    "guildId": guild_id,
                    "sessionKey": normalized_session,
                    "turnId": turn_id,
                    "deliveryTurnId": None,
                    "roomKey": room_key,
                    "personKey": person_key,
                    "sessionMemoryKey": session_memory_key,
                    "channelId": channel_id,
                    "replyToMessageId": reply_to_message_id,
                    "requestUserHash": content_sha256(request_user_text),
                    "requestAnswerHash": content_sha256(request_answer_text),
                    "queryHash": content_sha256(query),
                    "answerHash": "",
                    "displayHash": "",
                    "continuityGeneration": continuity_generation,
                    "deliveryGeneration": 0,
                    "attemptCount": 0,
                    "createdAt": now,
                    "updatedAt": now,
                    "lastErrorCode": "",
                }
            )
            self._entries[intent_id] = entry
            if len(self._entries) > SEARCH_FOLLOWUP_RECOVERY_MAX_ENTRIES:
                oldest = min(self._entries.values(), key=lambda item: item["createdAt"])
                self._entries.pop(oldest["intentId"], None)
            self._write()
            return intent_id

    def is_active(self, intent_id: str | None) -> bool:
        if not intent_id:
            return True
        with self._lock:
            return intent_id in self._entries

    def attempt_count(self, intent_id: str | None) -> int:
        if not intent_id:
            return 0
        with self._lock:
            entry = self._entries.get(intent_id)
            return int(entry["attemptCount"]) if entry else 0

    def claim_recovery(self, intent_id: str) -> bool:
        with self._lock:
            if (
                intent_id not in self._entries
                or intent_id in self._recovery_claims
            ):
                return False
            self._recovery_claims.add(intent_id)
            return True

    def release_recovery_claim(self, intent_id: str) -> None:
        with self._lock:
            self._recovery_claims.discard(intent_id)

    def mark_delivery_ready(
        self,
        intent_id: str | None,
        *,
        answer: str,
        display_text: str,
        continuity_generation: int,
    ) -> None:
        if not intent_id:
            return
        with self._lock:
            continuity_generation = _nonnegative_int(
                continuity_generation,
                code="search_followup_delivery_generation_invalid",
            )
            if continuity_generation < 1:
                raise ValueError(
                    "search_followup_delivery_generation_invalid"
                )
            entry = self._entries.get(intent_id)
            if entry is None:
                raise RuntimeError("search_followup_recovery_intent_inactive")
            if entry["phase"] not in {
                "delivery_preparing",
                "delivery_ready",
            }:
                raise RuntimeError(
                    "search_followup_delivery_not_prepared"
                )
            entry.update(
                {
                    "phase": "delivery_ready",
                    "answerHash": content_sha256(answer),
                    "displayHash": content_sha256(
                        display_text,
                        normalized=False,
                    ),
                    "deliveryGeneration": int(continuity_generation),
                    "updatedAt": self._now(),
                    "lastErrorCode": "",
                }
            )
            self._write()

    def begin_delivery_prepare(
        self,
        intent_id: str | None,
        *,
        answer: str,
        display_text: str,
        delivery_turn_id: str | None = None,
    ) -> None:
        if not intent_id:
            return
        with self._lock:
            entry = self._entries.get(intent_id)
            if entry is None:
                raise RuntimeError(
                    "search_followup_recovery_intent_inactive"
                )
            if entry["phase"] not in {
                "running",
                "delivery_preparing",
            }:
                raise RuntimeError(
                    "search_followup_delivery_prepare_invalid"
                )
            update = {
                "phase": "delivery_preparing",
                "answerHash": content_sha256(answer),
                "displayHash": content_sha256(
                    display_text,
                    normalized=False,
                ),
                "deliveryGeneration": 0,
                "updatedAt": self._now(),
                "lastErrorCode": "",
            }
            if delivery_turn_id is not None:
                update["deliveryTurnId"] = _bounded_key(
                    delivery_turn_id,
                    required=True,
                )
            entry.update(update)
            self._write()

    def mark_delivery_attempted(self, intent_id: str | None) -> None:
        if not intent_id:
            return
        with self._lock:
            entry = self._entries.get(intent_id)
            if entry is None:
                raise RuntimeError("search_followup_recovery_intent_inactive")
            if entry["phase"] not in {"delivery_ready", "delivery_attempted"}:
                raise RuntimeError("search_followup_delivery_not_ready")
            entry["phase"] = "delivery_attempted"
            entry["updatedAt"] = self._now()
            self._write()

    def mark_delivery_uncertain(self, intent_id: str | None, *, error_code: str) -> None:
        if not intent_id:
            return
        with self._lock:
            entry = self._entries.get(intent_id)
            if entry is None:
                return
            entry["phase"] = (
                "delivery_uncertain"
                if entry.get("answerHash")
                and int(entry.get("deliveryGeneration") or 0) >= 1
                else "request_unrecoverable"
            )
            entry["lastErrorCode"] = clean_text(error_code)[:120]
            entry["updatedAt"] = self._now()
            self._write()

    def record_attempt_failure(self, intent_id: str | None, *, error_code: str) -> int:
        if not intent_id:
            return 0
        with self._lock:
            entry = self._entries.get(intent_id)
            if entry is None:
                return 0
            entry["attemptCount"] = int(entry["attemptCount"]) + 1
            entry["lastErrorCode"] = clean_text(error_code)[:120]
            entry["updatedAt"] = self._now()
            self._write()
            return int(entry["attemptCount"])

    def complete(self, intent_id: str | None) -> None:
        if not intent_id:
            return
        with self._lock:
            if self._entries.pop(intent_id, None) is not None:
                self._recovery_claims.discard(intent_id)
                self._write()

    def reset_guild(self, guild_id: int) -> int:
        normalized_guild_id = _nonnegative_int(
            guild_id,
            code="search_followup_guild_invalid",
        )
        if normalized_guild_id < 1:
            raise ValueError("search_followup_guild_invalid")
        with self._lock:
            removed_ids = {
                intent_id
                for intent_id, entry in self._entries.items()
                if entry["guildId"] == normalized_guild_id
            }
            if not removed_ids:
                return 0
            for intent_id in removed_ids:
                self._entries.pop(intent_id, None)
                self._recovery_claims.discard(intent_id)
            self._write()
            return len(removed_ids)

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._entries.values()]

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            counts = {phase: 0 for phase in sorted(_PHASES)}
            for entry in self._entries.values():
                counts[entry["phase"]] += 1
            return {
                "schema": SEARCH_FOLLOWUP_RECOVERY_SCHEMA,
                "state": self._load_state,
                "enabled": self.enabled,
                "pendingCount": len(self._entries),
                "phases": counts,
                "generation": self._generation,
                "integrity": self._integrity,
                "headState": self._head_state,
                "rollbackProtected": self._integrity == "verified" and self._head_state == "current",
                "lastErrorCode": self._last_error_code,
                "policy": {
                    "contentFree": True,
                    "rawQuery": False,
                    "rawTranscript": False,
                    "duplicateDeliveryPolicy": "verify_text_or_fail_closed",
                },
            }


__all__ = [
    "SEARCH_FOLLOWUP_RECOVERY_SCHEMA",
    "SearchFollowupRecoveryJournal",
    "content_sha256",
]
