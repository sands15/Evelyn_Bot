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
SEARCH_FOLLOWUP_PREPARED_ANSWER_MAX_CHARS = 16 * 1024
_INTENT_ID = re.compile(r"^search-followup-[0-9a-f]{24}$")
_PHASES = frozenset(
    {
        "running",
        "delivery_preparing",
        "delivery_ready",
        "delivery_attempted",
        "delivery_uncertain",
        "delivery_succeeded",
        "canonical_committed",
        "request_unrecoverable",
    }
)
_ENTRY_KEYS = frozenset(
    {
        "intentId",
        "phase",
        "source",
        "guildId",
        "sessionKey",
        "turnId",
        "deliveryTurnId",
        "roomKey",
        "personKey",
        "sessionMemoryKey",
        "channelId",
        "replyToMessageId",
        "requestUserHash",
        "requestAnswerHash",
        "queryHash",
        "answerHash",
        "displayHash",
        "preparedAnswer",
        "deliveryMessageId",
        "continuityGeneration",
        "deliveryGeneration",
        "attemptCount",
        "createdAt",
        "updatedAt",
        "lastErrorCode",
    }
)
_PAYLOAD_KEYS = frozenset(
    {
        "schema",
        "generation",
        "previousHash",
        "journalHash",
        "updatedAt",
        "entries",
        "lastErrorCode",
        "policy",
    }
)
_HEAD_KEYS = frozenset(
    {
        "schema",
        "generation",
        "journalHash",
        "updatedAt",
        "contentFree",
        "privatePreparedAnswer",
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


def _prepared_answer(value: Any, *, required: bool = False) -> str:
    if value is None:
        candidate = ""
    elif isinstance(value, str):
        candidate = value
    else:
        raise ValueError("search_followup_prepared_answer_invalid")
    if (
        len(candidate) > SEARCH_FOLLOWUP_PREPARED_ANSWER_MAX_CHARS
        or (required and not clean_text(candidate))
    ):
        raise ValueError("search_followup_prepared_answer_invalid")
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


def _strict_json_loads(raw_text: str) -> Any:
    def reject_duplicate_keys(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("search_followup_duplicate_json_key")
            result[key] = value
        return result

    return json.loads(raw_text, object_pairs_hook=reject_duplicate_keys)


def _exact_lineage_counts(
    *,
    removed: int = 0,
    remaining: int = 0,
    manual: int = 0,
) -> dict[str, Any]:
    return {
        "removedCount": max(0, int(removed)),
        "remainingCopies": max(0, int(remaining)),
        "manualReviewCount": max(0, int(manual)),
        "contentFree": True,
    }


def _exact_selector_matches(
    selector: Callable[[str], bool],
    value: str,
) -> bool:
    matched = selector(value)
    if type(matched) is not bool:
        raise TypeError("search_followup_lineage_selector_invalid")
    return matched


class SearchFollowupRecoveryJournal:
    """Durable private state for promised search follow-ups."""

    def __init__(
        self,
        *,
        path: Path,
        head_path: Path | None = None,
        enabled: bool = True,
        wall_time: Callable[[], float] = time.time,
        intent_id_factory: Callable[[], str] | None = None,
        mutation_target_is_current: Callable[..., bool] | None = None,
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
        self.mutation_target_is_current = mutation_target_is_current
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
                "contentFree": False,
                "privatePreparedAnswer": True,
                "rawQuery": False,
                "rawTranscript": False,
                "duplicateDeliveryPolicy": "verify_text_or_fail_closed",
                "maxEntries": SEARCH_FOLLOWUP_RECOVERY_MAX_ENTRIES,
            },
        }
        payload["journalHash"] = _journal_hash(payload)
        return payload

    def _validated_entry(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or frozenset(raw) != _ENTRY_KEYS:
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
            "preparedAnswer": _prepared_answer(
                raw.get("preparedAnswer")
            ),
            "deliveryMessageId": _optional_positive_int(
                raw.get("deliveryMessageId"),
                code="search_followup_delivery_message_invalid",
            ),
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
            "delivery_succeeded",
            "canonical_committed",
        } and not entry["answerHash"]:
            raise ValueError("search_followup_delivery_anchor_invalid")
        if phase in {
            "delivery_ready",
            "canonical_committed",
        } and entry["deliveryGeneration"] < 1:
            raise ValueError("search_followup_delivery_anchor_invalid")
        if phase in {
            "delivery_succeeded",
            "canonical_committed",
        }:
            if (
                not entry["preparedAnswer"]
                or entry["deliveryMessageId"] is None
            ):
                raise ValueError(
                    "search_followup_delivery_receipt_invalid"
                )
        if (
            phase in {
                "delivery_attempted",
                "delivery_uncertain",
            }
            and entry["deliveryGeneration"] < 1
            and not entry["preparedAnswer"]
        ):
            raise ValueError("search_followup_delivery_anchor_invalid")
        return entry

    def _validated_payload(self, payload: Any) -> tuple[dict[str, dict[str, Any]], int, str, str, str]:
        if (
            not isinstance(payload, dict)
            or frozenset(payload) != _PAYLOAD_KEYS
            or clean_text(payload.get("schema"))
            != SEARCH_FOLLOWUP_RECOVERY_SCHEMA
        ):
            raise ValueError("search_followup_schema_invalid")
        generation = _nonnegative_int(payload.get("generation"), code="search_followup_generation_invalid")
        if generation < 1:
            raise ValueError("search_followup_generation_invalid")
        previous_hash = _valid_sha256(payload.get("previousHash"))
        journal_hash = _valid_sha256(payload.get("journalHash"))
        _finite_nonnegative(payload.get("updatedAt"))
        if payload.get("policy") != {
            "contentFree": False,
            "privatePreparedAnswer": True,
            "rawQuery": False,
            "rawTranscript": False,
            "duplicateDeliveryPolicy": "verify_text_or_fail_closed",
            "maxEntries": SEARCH_FOLLOWUP_RECOVERY_MAX_ENTRIES,
        }:
            raise ValueError("search_followup_policy_invalid")
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
        payload = _strict_json_loads(
            self.head_path.read_text(encoding="utf-8")
        )
        if (
            not isinstance(payload, dict)
            or frozenset(payload) != _HEAD_KEYS
            or clean_text(payload.get("schema"))
            != SEARCH_FOLLOWUP_RECOVERY_HEAD_SCHEMA
            or payload.get("contentFree") is not False
            or payload.get("privatePreparedAnswer") is not True
        ):
            raise ValueError("search_followup_head_invalid")
        generation = _nonnegative_int(
            payload.get("generation"),
            code="search_followup_head_generation_invalid",
        )
        if generation < 1:
            raise ValueError("search_followup_head_generation_invalid")
        return {
            "generation": generation,
            "journalHash": _valid_sha256(payload.get("journalHash")),
            "updatedAt": _finite_nonnegative(payload.get("updatedAt")),
        }

    def _write_head(self, *, generation: int, journal_hash: str) -> None:
        atomic_json_write(
            self.head_path,
            {
                "schema": SEARCH_FOLLOWUP_RECOVERY_HEAD_SCHEMA,
                "generation": generation,
                "journalHash": journal_hash,
                "updatedAt": self._now(),
                "contentFree": False,
                "privatePreparedAnswer": True,
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
            payload = _strict_json_loads(
                self.path.read_text(encoding="utf-8")
            )
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
        except OSError:
            self._entries = {}
            if head is not None:
                self._generation = int(head["generation"])
                self._journal_hash = str(head["journalHash"])
            self._load_state = "error"
            self._integrity = "failed"
            self._head_state = "write_failed"
            self._last_error_code = (
                "search_followup_recovery_write_failed"
            )
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
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

    def _require_ready(self) -> None:
        if self._load_state != "ready":
            raise RuntimeError(
                "search_followup_recovery_unavailable"
            )

    def _require_mutation_targets_current(
        self,
        entries: Any,
    ) -> None:
        callback = self.mutation_target_is_current
        if callback is None:
            return
        seen: set[tuple[Any, Any, Any, Any]] = set()
        for entry in entries:
            lineage = (
                entry.get("turnId"),
                entry.get("deliveryTurnId"),
                entry.get("sessionKey"),
                entry.get("sessionMemoryKey"),
            )
            if lineage in seen:
                continue
            seen.add(lineage)
            try:
                current = callback(
                    turn_id=lineage[0],
                    delivery_turn_id=lineage[1],
                    session_key=lineage[2],
                    session_memory_key=lineage[3],
                ) is True
            except Exception:
                current = False
            if not current:
                raise RuntimeError("search_followup_target_retired")

    def _commit_entry_update(
        self,
        entry: dict[str, Any],
        update: dict[str, Any],
    ) -> None:
        candidate = dict(entry)
        candidate.update(update)
        entries = dict(self._entries)
        entries[str(entry["intentId"])] = candidate
        self._require_mutation_targets_current(entries.values())
        self._entries = entries
        self._write()

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
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
            if len(encoded) > SEARCH_FOLLOWUP_RECOVERY_MAX_BYTES:
                raise ValueError(
                    "search_followup_recovery_size_invalid"
                )
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

    def _fresh_exact_lineage_entries(
        self,
    ) -> dict[str, dict[str, Any]]:
        """Read the journal and head without startup repair."""

        head = self._load_head()
        missing = not self.path.exists() and not self.path.is_symlink()
        if missing:
            if head is not None or self._generation != 0 or self._entries:
                raise ValueError(
                    "search_followup_exact_lineage_state_mismatch"
                )
            return {}
        if (
            self.path.is_symlink()
            or not self.path.is_file()
            or self.path.stat().st_size
            > SEARCH_FOLLOWUP_RECOVERY_MAX_BYTES
        ):
            raise ValueError("search_followup_journal_invalid")
        payload = _strict_json_loads(
            self.path.read_text(encoding="utf-8")
        )
        entries, generation, _previous_hash, journal_hash, _error = (
            self._validated_payload(payload)
        )
        if (
            head is None
            or generation != int(head["generation"])
            or journal_hash != str(head["journalHash"])
            or generation != self._generation
            or journal_hash != self._journal_hash
            or entries != self._entries
        ):
            raise ValueError(
                "search_followup_exact_lineage_state_mismatch"
            )
        return entries

    @staticmethod
    def _exact_lineage_targets(
        entries: dict[str, dict[str, Any]],
        *,
        match_turn: Callable[[str], bool],
        match_session: Callable[[str], bool],
        full_user_delete: bool,
    ) -> tuple[set[str], int]:
        targets: set[str] = set()
        incomplete = 0
        for intent_id, entry in entries.items():
            sessions = tuple(
                value
                for value in (
                    entry.get("sessionKey"),
                    entry.get("sessionMemoryKey"),
                )
                if isinstance(value, str) and value
            )
            turns = tuple(
                value
                for value in (
                    entry.get("turnId"),
                    entry.get("deliveryTurnId"),
                )
                if isinstance(value, str) and value
            )
            if full_user_delete:
                if any(
                    _exact_selector_matches(match_session, value)
                    for value in sessions
                ):
                    targets.add(intent_id)
                continue
            if any(
                _exact_selector_matches(match_turn, value)
                for value in turns
            ):
                targets.add(intent_id)
            elif not turns and any(
                _exact_selector_matches(match_session, value)
                for value in sessions
            ):
                incomplete += 1
        return targets, incomplete

    def negative_recall_exact_lineage(
        self,
        *,
        match_turn: Callable[[str], bool],
        match_session: Callable[[str], bool],
        full_user_delete: bool,
    ) -> dict[str, Any]:
        """Freshly count exact target rows without returning their content."""

        if (
            not callable(match_turn)
            or not callable(match_session)
            or type(full_user_delete) is not bool
        ):
            raise TypeError("search_followup_lineage_selector_invalid")
        with self._lock:
            try:
                entries = self._fresh_exact_lineage_entries()
                targets, incomplete = self._exact_lineage_targets(
                    entries,
                    match_turn=match_turn,
                    match_session=match_session,
                    full_user_delete=full_user_delete,
                )
            except Exception:
                return _exact_lineage_counts(manual=1)
            return _exact_lineage_counts(
                remaining=len(targets) + incomplete,
                manual=1 if incomplete else 0,
            )

    def purge_exact_lineage(
        self,
        *,
        match_turn: Callable[[str], bool],
        match_session: Callable[[str], bool],
        full_user_delete: bool,
    ) -> dict[str, Any]:
        """Rewrite one exact target set and clear its recovery claims."""

        if (
            not callable(match_turn)
            or not callable(match_session)
            or type(full_user_delete) is not bool
        ):
            raise TypeError("search_followup_lineage_selector_invalid")
        with self._lock:
            try:
                entries = self._fresh_exact_lineage_entries()
                targets, incomplete = self._exact_lineage_targets(
                    entries,
                    match_turn=match_turn,
                    match_session=match_session,
                    full_user_delete=full_user_delete,
                )
            except Exception:
                return _exact_lineage_counts(manual=1)
            if incomplete:
                return _exact_lineage_counts(
                    remaining=len(targets) + incomplete,
                    manual=1,
                )
            if not targets:
                return _exact_lineage_counts()
            before_entries = {
                key: dict(value)
                for key, value in self._entries.items()
            }
            before_claims = set(self._recovery_claims)
            self._entries = {
                intent_id: entry
                for intent_id, entry in self._entries.items()
                if intent_id not in targets
            }
            self._recovery_claims.difference_update(targets)
            try:
                self._write()
                fresh = self._fresh_exact_lineage_entries()
                remaining, incomplete = self._exact_lineage_targets(
                    fresh,
                    match_turn=match_turn,
                    match_session=match_session,
                    full_user_delete=full_user_delete,
                )
            except Exception:
                self._entries = before_entries
                self._recovery_claims = before_claims
                return _exact_lineage_counts(manual=1)
            return _exact_lineage_counts(
                removed=len(targets),
                remaining=len(remaining) + incomplete,
                manual=1 if incomplete else 0,
            )

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
            self._require_ready()
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
            existing = [
                value
                for value in self._entries.values()
                if value["sessionKey"] == normalized_session
            ]
            if any(
                value["phase"]
                in {
                    "delivery_attempted",
                    "delivery_uncertain",
                    "delivery_succeeded",
                    "canonical_committed",
                }
                for value in existing
            ):
                raise RuntimeError(
                    "search_followup_prior_delivery_unresolved"
                )
            retained_entries = {
                key: value
                for key, value in self._entries.items()
                if value["sessionKey"] != normalized_session
            }
            if (
                len(retained_entries)
                >= SEARCH_FOLLOWUP_RECOVERY_MAX_ENTRIES
            ):
                raise RuntimeError(
                    "search_followup_recovery_capacity_exhausted"
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
                    "preparedAnswer": "",
                    "deliveryMessageId": None,
                    "continuityGeneration": continuity_generation,
                    "deliveryGeneration": 0,
                    "attemptCount": 0,
                    "createdAt": now,
                    "updatedAt": now,
                    "lastErrorCode": "",
                }
            )
            retained_entries[intent_id] = entry
            self._require_mutation_targets_current(
                retained_entries.values()
            )
            self._entries = retained_entries
            self._recovery_claims.intersection_update(self._entries)
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
            self._require_ready()
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
            self._require_ready()
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
            self._commit_entry_update(
                entry,
                {
                    "phase": "delivery_ready",
                    "answerHash": content_sha256(answer),
                    "displayHash": content_sha256(
                        display_text,
                        normalized=False,
                    ),
                    "preparedAnswer": "",
                    "deliveryMessageId": None,
                    "deliveryGeneration": int(continuity_generation),
                    "updatedAt": self._now(),
                    "lastErrorCode": "",
                },
            )

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
            self._require_ready()
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
                "preparedAnswer": _prepared_answer(
                    answer,
                    required=True,
                ),
                "deliveryMessageId": None,
                "deliveryGeneration": 0,
                "updatedAt": self._now(),
                "lastErrorCode": "",
            }
            if delivery_turn_id is not None:
                update["deliveryTurnId"] = _bounded_key(
                    delivery_turn_id,
                    required=True,
                )
            self._commit_entry_update(entry, update)

    def mark_delivery_baseline(
        self,
        intent_id: str | None,
        *,
        continuity_generation: int,
    ) -> None:
        if not intent_id:
            return
        with self._lock:
            self._require_ready()
            generation = _nonnegative_int(
                continuity_generation,
                code=(
                    "search_followup_delivery_generation_invalid"
                ),
            )
            entry = self._entries.get(intent_id)
            if entry is None:
                raise RuntimeError(
                    "search_followup_recovery_intent_inactive"
                )
            if (
                generation < 1
                or generation < int(entry["continuityGeneration"])
            ):
                raise ValueError(
                    "search_followup_delivery_generation_invalid"
                )
            if entry["phase"] not in {
                "delivery_preparing",
                "delivery_attempted",
                "delivery_uncertain",
            }:
                raise RuntimeError(
                    "search_followup_delivery_baseline_invalid"
                )
            self._commit_entry_update(
                entry,
                {
                    "deliveryGeneration": generation,
                    "updatedAt": self._now(),
                    "lastErrorCode": "",
                },
            )

    def mark_delivery_attempted(self, intent_id: str | None) -> None:
        if not intent_id:
            return
        with self._lock:
            self._require_ready()
            entry = self._entries.get(intent_id)
            if entry is None:
                raise RuntimeError("search_followup_recovery_intent_inactive")
            if entry["phase"] not in {
                "delivery_preparing",
                "delivery_ready",
                "delivery_attempted",
                "delivery_uncertain",
            }:
                raise RuntimeError("search_followup_delivery_not_ready")
            self._commit_entry_update(
                entry,
                {
                    "phase": "delivery_attempted",
                    "updatedAt": self._now(),
                },
            )

    def mark_delivery_succeeded(
        self,
        intent_id: str | None,
        *,
        delivery_message_id: int,
    ) -> None:
        if not intent_id:
            return
        with self._lock:
            self._require_ready()
            entry = self._entries.get(intent_id)
            if entry is None:
                raise RuntimeError(
                    "search_followup_recovery_intent_inactive"
                )
            if entry["phase"] not in {
                "delivery_attempted",
                "delivery_uncertain",
                "delivery_succeeded",
            }:
                raise RuntimeError(
                    "search_followup_delivery_not_attempted"
                )
            if int(entry.get("deliveryGeneration") or 0) < max(
                1,
                int(entry["continuityGeneration"]),
            ):
                raise RuntimeError(
                    "search_followup_delivery_baseline_invalid"
                )
            self._commit_entry_update(
                entry,
                {
                    "phase": "delivery_succeeded",
                    "deliveryMessageId": _optional_positive_int(
                        delivery_message_id,
                        code=(
                            "search_followup_delivery_message_invalid"
                        ),
                    ),
                    "updatedAt": self._now(),
                    "lastErrorCode": "",
                },
            )

    def mark_canonical_committed(
        self,
        intent_id: str | None,
        *,
        continuity_generation: int,
    ) -> None:
        if not intent_id:
            return
        with self._lock:
            self._require_ready()
            generation = _nonnegative_int(
                continuity_generation,
                code=(
                    "search_followup_delivery_generation_invalid"
                ),
            )
            if generation < 1:
                raise ValueError(
                    "search_followup_delivery_generation_invalid"
                )
            entry = self._entries.get(intent_id)
            if entry is None:
                raise RuntimeError(
                    "search_followup_recovery_intent_inactive"
                )
            if entry["phase"] not in {
                "delivery_succeeded",
                "canonical_committed",
            }:
                raise RuntimeError(
                    "search_followup_delivery_not_succeeded"
                )
            prior_generation = int(
                entry.get("deliveryGeneration") or 0
            )
            if (
                generation < int(entry["continuityGeneration"])
                or (
                    entry["phase"] == "delivery_succeeded"
                    and generation <= prior_generation
                )
                or (
                    entry["phase"] == "canonical_committed"
                    and generation < prior_generation
                )
            ):
                raise ValueError(
                    "search_followup_delivery_generation_invalid"
                )
            self._commit_entry_update(
                entry,
                {
                    "phase": "canonical_committed",
                    "deliveryGeneration": generation,
                    "updatedAt": self._now(),
                    "lastErrorCode": "",
                },
            )

    def mark_delivery_uncertain(self, intent_id: str | None, *, error_code: str) -> None:
        if not intent_id:
            return
        with self._lock:
            self._require_ready()
            entry = self._entries.get(intent_id)
            if entry is None:
                return
            update: dict[str, Any] = {}
            if entry["phase"] not in {
                "delivery_succeeded",
                "canonical_committed",
            }:
                update["phase"] = (
                    "delivery_uncertain"
                    if entry.get("answerHash")
                    and (
                        entry.get("preparedAnswer")
                        or int(entry.get("deliveryGeneration") or 0) >= 1
                    )
                    else "request_unrecoverable"
                )
            update["lastErrorCode"] = clean_text(error_code)[:120]
            update["updatedAt"] = self._now()
            self._commit_entry_update(entry, update)

    def record_attempt_failure(self, intent_id: str | None, *, error_code: str) -> int:
        if not intent_id:
            return 0
        with self._lock:
            self._require_ready()
            entry = self._entries.get(intent_id)
            if entry is None:
                return 0
            attempt_count = int(entry["attemptCount"]) + 1
            self._commit_entry_update(
                entry,
                {
                    "attemptCount": attempt_count,
                    "lastErrorCode": clean_text(error_code)[:120],
                    "updatedAt": self._now(),
                },
            )
            return attempt_count

    def complete(self, intent_id: str | None) -> None:
        if not intent_id:
            return
        with self._lock:
            self._require_ready()
            entry = self._entries.get(intent_id)
            if entry is None:
                return
            entries = {
                key: value
                for key, value in self._entries.items()
                if key != intent_id
            }
            self._require_mutation_targets_current(
                (*entries.values(), entry)
            )
            self._entries = entries
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
            if not self.enabled:
                return 0
            if self._load_state != "ready":
                if (
                    self._load_state == "error"
                    and self._last_error_code
                    == "search_followup_recovery_write_failed"
                ):
                    self._load()
                    self._recovery_claims.intersection_update(
                        self._entries
                    )
                self._require_ready()
            removed_ids = {
                intent_id
                for intent_id, entry in self._entries.items()
                if entry["guildId"] == normalized_guild_id
            }
            if not removed_ids:
                return 0
            self._require_mutation_targets_current(
                self._entries.values()
            )
            self._entries = {
                intent_id: entry
                for intent_id, entry in self._entries.items()
                if intent_id not in removed_ids
            }
            self._recovery_claims = (
                self._recovery_claims - removed_ids
            )
            try:
                self._write()
            except BaseException:
                self._load_state = "error"
                self._integrity = "failed"
                self._head_state = "write_failed"
                self._last_error_code = (
                    "search_followup_recovery_write_failed"
                )
                raise
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
                    "contentFree": False,
                    "privatePreparedAnswer": True,
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
