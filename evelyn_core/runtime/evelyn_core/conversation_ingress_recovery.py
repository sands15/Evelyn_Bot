from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable

from .conversation_memory_receipt import (
    sanitize_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from .runtime_artifact_io import atomic_json_write


CONVERSATION_INGRESS_RECOVERY_SCHEMA = (
    "conversation.ingress-recovery.v1"
)
CONVERSATION_INGRESS_RECOVERY_HEAD_SCHEMA = (
    "conversation.ingress-recovery-head.v1"
)
CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA = (
    "conversation.ingress-recovery-receipt.v1"
)
CONVERSATION_INGRESS_RESERVATION_REVOCATION_RECEIPT_SCHEMA = (
    "conversation.ingress-reservation-revocation-receipt.v1"
)
CONVERSATION_INGRESS_RECOVERY_RECORD_SCHEMA = (
    "conversation.ingress-recovery-record.v1"
)
CONVERSATION_INGRESS_RECOVERY_CHAIN_GENESIS = "0" * 64

DEFAULT_INGRESS_MAX_AGE_SEC = 15 * 60.0
DEFAULT_INGRESS_MAX_ENTRIES = 128
DEFAULT_INGRESS_MAX_CONTENT_CHARS = 2_000
DEFAULT_INGRESS_MAX_BYTES = 1024 * 1024

_HARD_MAX_AGE_SEC = 30 * 60.0
_HARD_MAX_ENTRIES = 1024
_HARD_MAX_CONTENT_CHARS = 2_000
_HARD_MAX_BYTES = 4 * 1024 * 1024
_ENTRY_ID = re.compile(r"ingress-[0-9a-f]{64}\Z")
_TURN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_SURFACE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_PHASES = frozenset(
    {
        "reserved",
        "accepted",
        "response_ready",
        "delivery_inflight",
        "delivery_succeeded",
        "delivery_ambiguous",
        "terminal_committing",
        "completed",
    }
)
_PENDING_PHASES = _PHASES - {"completed", "reserved"}
_ERROR_CODES = frozenset(
    {
        "",
        "conversation_ingress_delivery_ambiguous",
        "conversation_ingress_delivery_ambiguous_after_restart",
        "conversation_ingress_delivery_disconnected",
        "conversation_ingress_delivery_failed",
        "conversation_ingress_delivery_timeout",
        "conversation_ingress_process_interrupted",
    }
)
_ENTRY_KEYS = frozenset(
    {
        "entryId",
        "surface",
        "scope",
        "sourceDeliveryId",
        "turnId",
        "phase",
        "textHash",
        "assistantHash",
        "assistantBindingHash",
        "acceptedText",
        "assistantText",
        "memoryReceiptRef",
        "deliveryRef",
        "continuityGeneration",
        "createdAt",
        "updatedAt",
        "expiresAt",
        "recoveredAt",
        "lastErrorCode",
    }
)
_POLICY_KEYS = frozenset(
    {
        "automaticReplay",
        "rawAudio",
        "partialTranscript",
        "finalAcceptedText",
        "finalAssistantText",
        "assistantMemoryReceiptRef",
        "maxAgeSec",
        "maxEntries",
        "maxContentChars",
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
    }
)


class ConversationIngressRecoveryError(RuntimeError):
    """Stable, public-code exception raised by the ingress journal."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


class ConversationIngressBindingMismatch(
    ConversationIngressRecoveryError
):
    pass


def normalize_final_conversation_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", normalized).strip()


def final_text_sha256(value: Any) -> str:
    return hashlib.sha256(
        normalize_final_conversation_text(value).encode("utf-8")
    ).hexdigest()


def _assistant_binding_hash(
    assistant_hash: str,
    memory_receipt_ref: dict[str, Any],
) -> str:
    encoded = json.dumps(
        {
            "assistantHash": assistant_hash,
            "memoryReceiptRef": memory_receipt_ref,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _memory_receipt_ref(value: Any) -> dict[str, Any]:
    sanitized = sanitize_memory_receipt_ref(value)
    return (
        sanitized
        if sanitized is not None
        else unattributed_memory_receipt_ref()
    )


def _error_code(value: Any, *, allow_empty: bool = False) -> str:
    candidate = normalize_final_conversation_text(value).lower()
    if not candidate and allow_empty:
        return ""
    if candidate not in _ERROR_CODES or not candidate:
        raise ConversationIngressRecoveryError(
            "conversation_ingress_error_code_invalid"
        )
    return candidate


def conversation_ingress_entry_id(
    *,
    surface: Any,
    scope: Any,
    source_delivery_id: Any,
) -> str:
    normalized_surface = _surface(surface)
    normalized_scope = _bounded_identifier(
        scope,
        code="conversation_ingress_scope_invalid",
        max_chars=512,
    )
    normalized_delivery_id = _bounded_identifier(
        source_delivery_id,
        code="conversation_ingress_source_delivery_id_invalid",
        max_chars=512,
    )
    material = json.dumps(
        [
            normalized_surface,
            normalized_scope,
            normalized_delivery_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"ingress-{hashlib.sha256(material).hexdigest()}"


def _surface(value: Any) -> str:
    candidate = normalize_final_conversation_text(value).lower()
    if not _SURFACE.fullmatch(candidate):
        raise ConversationIngressRecoveryError(
            "conversation_ingress_surface_invalid"
        )
    return candidate


def _bounded_identifier(
    value: Any,
    *,
    code: str,
    max_chars: int,
    allow_empty: bool = False,
) -> str:
    candidate = normalize_final_conversation_text(value)
    if not candidate and allow_empty:
        return ""
    if (
        not candidate
        or len(candidate) > max_chars
        or any(ord(char) < 32 for char in candidate)
    ):
        raise ConversationIngressRecoveryError(code)
    return candidate


def _turn_id(value: Any, *, allow_empty: bool = False) -> str:
    candidate = normalize_final_conversation_text(value)
    if not candidate and allow_empty:
        return ""
    if not _TURN_ID.fullmatch(candidate):
        raise ConversationIngressRecoveryError(
            "conversation_ingress_turn_id_invalid"
        )
    return candidate


def _entry_id(value: Any) -> str:
    candidate = normalize_final_conversation_text(value).lower()
    if not _ENTRY_ID.fullmatch(candidate):
        raise ConversationIngressRecoveryError(
            "conversation_ingress_entry_id_invalid"
        )
    return candidate


def _sha256(value: Any, *, allow_empty: bool = False) -> str:
    candidate = normalize_final_conversation_text(value).lower()
    if not candidate and allow_empty:
        return ""
    if not _SHA256.fullmatch(candidate):
        raise ConversationIngressRecoveryError(
            "conversation_ingress_hash_invalid"
        )
    return candidate


def _nonnegative_int(value: Any, *, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConversationIngressRecoveryError(code)
    return value


def _positive_int(value: Any, *, code: str) -> int:
    parsed = _nonnegative_int(value, code=code)
    if parsed < 1:
        raise ConversationIngressRecoveryError(code)
    return parsed


def _finite_nonnegative(value: Any, *, code: str) -> float:
    if isinstance(value, bool):
        raise ConversationIngressRecoveryError(code)
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConversationIngressRecoveryError(code) from exc
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ConversationIngressRecoveryError(code)
    return parsed


def _journal_hash(payload: dict[str, Any]) -> str:
    material = {
        key: value
        for key, value in payload.items()
        if key != "journalHash"
    }
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
                raise ValueError("conversation_ingress_duplicate_json_key")
            result[key] = value
        return result

    return json.loads(raw_text, object_pairs_hook=reject_duplicate_keys)


def _clone_entries(
    entries: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in entries.items()}


class ConversationIngressRecoveryJournal:
    """Durable at-most-once boundary for accepted conversation ingress.

    The journal stores only bounded final accepted/assistant text. It never
    stores raw audio, partial transcripts, prompts, tool evidence, or arbitrary
    response payloads. A repeated source delivery can inspect prior state, but
    only the first durable claim is allowed to start processing.
    """

    def __init__(
        self,
        *,
        path: Path,
        head_path: Path | None = None,
        enabled: bool = True,
        max_age_sec: float = DEFAULT_INGRESS_MAX_AGE_SEC,
        max_entries: int = DEFAULT_INGRESS_MAX_ENTRIES,
        max_content_chars: int = DEFAULT_INGRESS_MAX_CONTENT_CHARS,
        max_bytes: int = DEFAULT_INGRESS_MAX_BYTES,
        wall_time: Callable[[], float] = time.time,
        turn_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.head_path = Path(
            head_path
            if head_path is not None
            else self.path.with_name(f"{self.path.stem}.head.json")
        )
        self.enabled = bool(enabled)
        self.max_age_sec = min(
            _HARD_MAX_AGE_SEC,
            max(
                1.0,
                _finite_nonnegative(
                    max_age_sec,
                    code="conversation_ingress_max_age_invalid",
                ),
            ),
        )
        self.max_entries = min(
            _HARD_MAX_ENTRIES,
            _positive_int(
                max_entries,
                code="conversation_ingress_max_entries_invalid",
            ),
        )
        self.max_content_chars = min(
            _HARD_MAX_CONTENT_CHARS,
            _positive_int(
                max_content_chars,
                code="conversation_ingress_max_content_invalid",
            ),
        )
        self.max_bytes = min(
            _HARD_MAX_BYTES,
            max(
                4_096,
                _positive_int(
                    max_bytes,
                    code="conversation_ingress_max_bytes_invalid",
                ),
            ),
        )
        self.wall_time = wall_time
        self.turn_id_factory = turn_id_factory or (
            lambda: f"ingress-{uuid.uuid4().hex}"
        )
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._generation = 0
        self._journal_hash = CONVERSATION_INGRESS_RECOVERY_CHAIN_GENESIS
        self._state = "disabled" if not self.enabled else "ready"
        self._integrity = "disabled" if not self.enabled else "missing"
        self._head_state = "disabled" if not self.enabled else "missing"
        self._last_error_code = ""
        if self.enabled:
            self._load()

    def _now(self) -> float:
        return _finite_nonnegative(
            self.wall_time(),
            code="conversation_ingress_timestamp_invalid",
        )

    def _policy(self) -> dict[str, Any]:
        return {
            "automaticReplay": False,
            "rawAudio": False,
            "partialTranscript": False,
            "finalAcceptedText": True,
            "finalAssistantText": True,
            "assistantMemoryReceiptRef": True,
            "maxAgeSec": self.max_age_sec,
            "maxEntries": self.max_entries,
            "maxContentChars": self.max_content_chars,
        }

    def _payload(
        self,
        *,
        generation: int,
        previous_hash: str,
    ) -> dict[str, Any]:
        payload = {
            "schema": CONVERSATION_INGRESS_RECOVERY_SCHEMA,
            "generation": generation,
            "previousHash": previous_hash,
            "journalHash": "",
            "updatedAt": self._now(),
            "entries": [
                dict(entry)
                for entry in sorted(
                    self._entries.values(),
                    key=lambda item: (
                        float(item["createdAt"]),
                        str(item["entryId"]),
                    ),
                )
            ],
            "policy": self._policy(),
        }
        payload["journalHash"] = _journal_hash(payload)
        return payload

    def _validated_policy(self, raw: Any) -> None:
        if not isinstance(raw, dict) or frozenset(raw) != _POLICY_KEYS:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_policy_invalid"
            )
        expected = self._policy()
        if raw != expected:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_policy_mismatch"
            )

    def _validated_entry(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or frozenset(raw) != _ENTRY_KEYS:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_entry_schema_invalid"
            )
        entry_id = _entry_id(raw.get("entryId"))
        surface = _surface(raw.get("surface"))
        scope = _bounded_identifier(
            raw.get("scope"),
            code="conversation_ingress_scope_invalid",
            max_chars=512,
        )
        source_delivery_id = _bounded_identifier(
            raw.get("sourceDeliveryId"),
            code="conversation_ingress_source_delivery_id_invalid",
            max_chars=512,
        )
        if entry_id != conversation_ingress_entry_id(
            surface=surface,
            scope=scope,
            source_delivery_id=source_delivery_id,
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_entry_binding_invalid"
            )
        turn_id = _turn_id(raw.get("turnId"))
        phase = normalize_final_conversation_text(raw.get("phase")).lower()
        if phase not in _PHASES:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_phase_invalid"
            )
        accepted_text = normalize_final_conversation_text(
            raw.get("acceptedText")
        )
        assistant_text = normalize_final_conversation_text(
            raw.get("assistantText")
        )
        if (
            (phase == "reserved" and bool(accepted_text))
            or (phase != "reserved" and not accepted_text)
            or len(accepted_text) > self.max_content_chars
            or len(assistant_text) > self.max_content_chars
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_content_invalid"
            )
        text_hash = _sha256(raw.get("textHash"))
        assistant_hash = _sha256(
            raw.get("assistantHash"),
            allow_empty=True,
        )
        assistant_binding_hash = _sha256(
            raw.get("assistantBindingHash"),
            allow_empty=True,
        )
        memory_receipt_ref = sanitize_memory_receipt_ref(
            raw.get("memoryReceiptRef")
        )
        if memory_receipt_ref is None:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_memory_receipt_invalid"
            )
        if phase != "reserved" and text_hash != final_text_sha256(
            accepted_text
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_text_hash_mismatch"
            )
        if (
            bool(assistant_text) != bool(assistant_hash)
            or bool(assistant_text) != bool(assistant_binding_hash)
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_assistant_binding_invalid"
            )
        if assistant_text and assistant_hash != final_text_sha256(
            assistant_text
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_assistant_hash_mismatch"
            )
        if assistant_text and assistant_binding_hash != (
            _assistant_binding_hash(
                assistant_hash,
                memory_receipt_ref,
            )
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_assistant_binding_invalid"
            )
        phases_without_required_response = {
            "reserved",
            "accepted",
            "delivery_inflight",
            "delivery_ambiguous",
        }
        if not assistant_text and (
            phase not in phases_without_required_response
            or assistant_binding_hash
            or memory_receipt_ref.get("state") != "unattributed"
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_phase_content_invalid"
            )
        if phase in {"reserved", "accepted"} and assistant_text:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_phase_content_invalid"
            )
        continuity_generation = _nonnegative_int(
            raw.get("continuityGeneration"),
            code="conversation_ingress_continuity_generation_invalid",
        )
        if phase in {"terminal_committing", "completed"}:
            if continuity_generation < 1:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_continuity_generation_invalid"
                )
        elif continuity_generation != 0:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_continuity_generation_invalid"
            )
        created_at = _finite_nonnegative(
            raw.get("createdAt"),
            code="conversation_ingress_timestamp_invalid",
        )
        updated_at = _finite_nonnegative(
            raw.get("updatedAt"),
            code="conversation_ingress_timestamp_invalid",
        )
        expires_at = _finite_nonnegative(
            raw.get("expiresAt"),
            code="conversation_ingress_timestamp_invalid",
        )
        recovered_at = _finite_nonnegative(
            raw.get("recoveredAt"),
            code="conversation_ingress_timestamp_invalid",
        )
        if (
            updated_at < created_at
            or expires_at <= created_at
            or expires_at > created_at + self.max_age_sec + 0.001
            or updated_at > expires_at
            or (recovered_at and recovered_at < created_at)
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_timestamp_invalid"
            )
        delivery_ref = _bounded_identifier(
            raw.get("deliveryRef"),
            code="conversation_ingress_delivery_ref_invalid",
            max_chars=512,
            allow_empty=True,
        )
        if phase == "reserved" and delivery_ref != _sha256(delivery_ref):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_reservation_ref_invalid"
            )
        last_error_code = _error_code(
            raw.get("lastErrorCode"),
            allow_empty=True,
        )
        return {
            "entryId": entry_id,
            "surface": surface,
            "scope": scope,
            "sourceDeliveryId": source_delivery_id,
            "turnId": turn_id,
            "phase": phase,
            "textHash": text_hash,
            "assistantHash": assistant_hash,
            "assistantBindingHash": assistant_binding_hash,
            "acceptedText": accepted_text,
            "assistantText": assistant_text,
            "memoryReceiptRef": memory_receipt_ref,
            "deliveryRef": delivery_ref,
            "continuityGeneration": continuity_generation,
            "createdAt": created_at,
            "updatedAt": updated_at,
            "expiresAt": expires_at,
            "recoveredAt": recovered_at,
            "lastErrorCode": last_error_code,
        }

    def _validated_payload(
        self,
        payload: Any,
    ) -> tuple[dict[str, dict[str, Any]], int, str, str]:
        if not isinstance(payload, dict) or frozenset(payload) != _PAYLOAD_KEYS:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_payload_schema_invalid"
            )
        if payload.get("schema") != CONVERSATION_INGRESS_RECOVERY_SCHEMA:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_schema_invalid"
            )
        generation = _positive_int(
            payload.get("generation"),
            code="conversation_ingress_generation_invalid",
        )
        previous_hash = _sha256(payload.get("previousHash"))
        journal_hash = _sha256(payload.get("journalHash"))
        _finite_nonnegative(
            payload.get("updatedAt"),
            code="conversation_ingress_timestamp_invalid",
        )
        self._validated_policy(payload.get("policy"))
        if journal_hash != _journal_hash(payload):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_self_hash_mismatch"
            )
        raw_entries = payload.get("entries")
        if (
            not isinstance(raw_entries, list)
            or len(raw_entries) > self.max_entries
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_entries_invalid"
            )
        entries: dict[str, dict[str, Any]] = {}
        for raw_entry in raw_entries:
            entry = self._validated_entry(raw_entry)
            entry_id = str(entry["entryId"])
            if entry_id in entries:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_duplicate_entry"
                )
            entries[entry_id] = entry
        return entries, generation, previous_hash, journal_hash

    def _load_head(self) -> dict[str, Any] | None:
        if not self.head_path.exists() and not self.head_path.is_symlink():
            return None
        if self.head_path.is_symlink() or not self.head_path.is_file():
            raise ConversationIngressRecoveryError(
                "conversation_ingress_head_invalid"
            )
        raw_text = self.head_path.read_text(encoding="utf-8")
        payload = _strict_json_loads(raw_text)
        if not isinstance(payload, dict) or frozenset(payload) != _HEAD_KEYS:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_head_invalid"
            )
        if (
            payload.get("schema")
            != CONVERSATION_INGRESS_RECOVERY_HEAD_SCHEMA
            or payload.get("contentFree") is not True
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_head_invalid"
            )
        return {
            "generation": _positive_int(
                payload.get("generation"),
                code="conversation_ingress_head_invalid",
            ),
            "journalHash": _sha256(payload.get("journalHash")),
            "updatedAt": _finite_nonnegative(
                payload.get("updatedAt"),
                code="conversation_ingress_head_invalid",
            ),
        }

    def _write_head(self, *, generation: int, journal_hash: str) -> None:
        atomic_json_write(
            self.head_path,
            {
                "schema": CONVERSATION_INGRESS_RECOVERY_HEAD_SCHEMA,
                "generation": generation,
                "journalHash": journal_hash,
                "updatedAt": self._now(),
                "contentFree": True,
            },
            durable=True,
        )

    @staticmethod
    def _target_allowed(path: Path) -> bool:
        return not path.is_symlink() and (
            not path.exists() or path.is_file()
        )

    def _write(self) -> None:
        self._require_ready()
        if not self._target_allowed(self.path) or not self._target_allowed(
            self.head_path
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_target_rejected"
            )
        generation = self._generation + 1
        payload = self._payload(
            generation=generation,
            previous_hash=self._journal_hash,
        )
        encoded_size = len(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
        )
        if encoded_size > self.max_bytes:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_capacity_exhausted"
            )
        journal_hash = str(payload["journalHash"])
        try:
            atomic_json_write(self.path, payload, durable=True)
            try:
                self._write_head(
                    generation=generation,
                    journal_hash=journal_hash,
                )
            except Exception:
                self._write_head(
                    generation=generation,
                    journal_hash=journal_hash,
                )
        except Exception:
            self._state = "error"
            self._integrity = "failed"
            self._head_state = "write_failed"
            self._last_error_code = (
                "conversation_ingress_recovery_write_failed"
            )
            raise
        self._generation = generation
        self._journal_hash = journal_hash
        self._integrity = "verified"
        self._head_state = "current"
        self._last_error_code = ""

    def _load(self) -> None:
        head: dict[str, Any] | None = None
        try:
            head = self._load_head()
            missing = not self.path.exists() and not self.path.is_symlink()
            if missing:
                if head is not None:
                    raise ConversationIngressRecoveryError(
                        "conversation_ingress_journal_missing_after_head"
                    )
                try:
                    self._write()
                except Exception:
                    # `_write()` has already moved the journal to a closed
                    # error state.  Keep the object inspectable, but never
                    # expose a fresh owner as durable-ready when bootstrap
                    # persistence did not complete.
                    return
                return
            if (
                self.path.is_symlink()
                or not self.path.is_file()
                or self.path.stat().st_size > self.max_bytes
            ):
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_journal_invalid"
                )
            raw_text = self.path.read_text(encoding="utf-8")
            payload = _strict_json_loads(raw_text)
            (
                entries,
                generation,
                previous_hash,
                journal_hash,
            ) = self._validated_payload(payload)
            if head is None:
                if (
                    generation != 1
                    or previous_hash
                    != CONVERSATION_INGRESS_RECOVERY_CHAIN_GENESIS
                ):
                    raise ConversationIngressRecoveryError(
                        "conversation_ingress_head_missing"
                    )
                self._write_head(
                    generation=generation,
                    journal_hash=journal_hash,
                )
            elif (
                generation == int(head["generation"])
                and journal_hash == str(head["journalHash"])
            ):
                pass
            elif (
                generation == int(head["generation"]) + 1
                and previous_hash == str(head["journalHash"])
            ):
                self._write_head(
                    generation=generation,
                    journal_hash=journal_hash,
                )
            else:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_rollback_or_head_mismatch"
                )
            self._entries = entries
            self._generation = generation
            self._journal_hash = journal_hash
            self._integrity = "verified"
            self._head_state = "current"
            self._state = "ready"
            self._recover_after_restart()
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            ConversationIngressRecoveryError,
        ):
            self._entries = {}
            if head is not None:
                self._generation = int(head["generation"])
                self._journal_hash = str(head["journalHash"])
                self._head_state = "orphaned"
            self._state = "corrupt"
            self._integrity = "failed"
            self._last_error_code = (
                "conversation_ingress_recovery_journal_corrupt"
            )

    def _recover_after_restart(self) -> None:
        now = self._now()
        before = _clone_entries(self._entries)
        self._entries = {
            entry_id: entry
            for entry_id, entry in self._entries.items()
            if float(entry["expiresAt"]) > now
        }
        for entry in self._entries.values():
            if entry["phase"] == "completed":
                continue
            if entry["phase"] == "delivery_inflight":
                entry["phase"] = "delivery_ambiguous"
                entry["lastErrorCode"] = (
                    "conversation_ingress_delivery_ambiguous_after_restart"
                )
            if not float(entry["recoveredAt"]):
                entry["recoveredAt"] = now
            entry["updatedAt"] = min(
                max(float(entry["updatedAt"]), now),
                float(entry["expiresAt"]),
            )
        if self._entries != before:
            self._write()

    def _require_ready(self) -> None:
        if not self.enabled or self._state != "ready":
            raise ConversationIngressRecoveryError(
                "conversation_ingress_recovery_unavailable"
            )

    def _prune_expired(self) -> None:
        now = self._now()
        expired = {
            entry_id
            for entry_id, entry in self._entries.items()
            if float(entry["expiresAt"]) <= now
        }
        if not expired:
            return
        before = _clone_entries(self._entries)
        for entry_id in expired:
            self._entries.pop(entry_id, None)
        try:
            self._write()
        except Exception:
            self._entries = before
            raise

    def _content(
        self,
        value: Any,
        *,
        code: str,
        allow_empty: bool = False,
    ) -> str:
        candidate = normalize_final_conversation_text(value)
        if not candidate and allow_empty:
            return ""
        if not candidate or len(candidate) > self.max_content_chars:
            raise ConversationIngressRecoveryError(code)
        return candidate

    def _entry(self, entry_id: Any) -> dict[str, Any]:
        normalized = _entry_id(entry_id)
        entry = self._entries.get(normalized)
        if entry is None:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_entry_not_found"
            )
        return entry

    def _mutate_and_write(
        self,
        mutation: Callable[[], None],
    ) -> None:
        before = _clone_entries(self._entries)
        mutation()
        try:
            self._write()
        except Exception:
            self._entries = before
            raise

    def _receipt(
        self,
        entry: dict[str, Any],
        *,
        disposition: str | None = None,
        should_process: bool = False,
    ) -> dict[str, Any]:
        phase = str(entry["phase"])
        if disposition is None:
            disposition = {
                "reserved": "reserved",
                "accepted": "pending",
                "response_ready": "pending",
                "delivery_inflight": "delivery_inflight",
                "delivery_succeeded": "delivered_pending_commit",
                "delivery_ambiguous": "delivery_ambiguous",
                "terminal_committing": "terminal_pending",
                "completed": "completed",
            }[phase]
        return {
            "schema": CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA,
            "entryId": str(entry["entryId"]),
            "phase": phase,
            "disposition": disposition,
            "durable": True,
            "shouldProcess": bool(should_process),
            "automaticReplay": False,
            "turnId": str(entry["turnId"]),
            "textHash": str(entry["textHash"]),
            "assistantHash": str(entry["assistantHash"]),
            "assistantBindingHash": str(
                entry["assistantBindingHash"]
            ),
            "memoryReceiptRef": _memory_receipt_ref(
                entry["memoryReceiptRef"]
            ),
            "replayable": bool(
                entry["phase"] == "completed"
                and entry["memoryReceiptRef"].get("state")
                in {"bound", "not_used"}
            ),
            "continuityGeneration": int(
                entry["continuityGeneration"]
            ),
            "deliveryAmbiguous": phase == "delivery_ambiguous",
            "recovered": bool(float(entry["recoveredAt"])),
            "expiresAt": float(entry["expiresAt"]),
            "journalGeneration": int(self._generation),
        }

    def reserve_ingress(
        self,
        *,
        surface: Any,
        scope: Any,
        source_delivery_id: Any,
        text_hash: Any,
        turn_id: Any,
        reservation_ref: Any,
        ttl_sec: Any,
    ) -> dict[str, Any]:
        """Durably reserve an ingress key without storing conversation text."""

        normalized_surface = _surface(surface)
        normalized_scope = _bounded_identifier(
            scope,
            code="conversation_ingress_scope_invalid",
            max_chars=512,
        )
        normalized_delivery_id = _bounded_identifier(
            source_delivery_id,
            code="conversation_ingress_source_delivery_id_invalid",
            max_chars=512,
        )
        normalized_text_hash = _sha256(text_hash)
        normalized_turn_id = _turn_id(turn_id)
        normalized_reservation_ref = _sha256(reservation_ref)
        ttl = _finite_nonnegative(
            ttl_sec,
            code="conversation_ingress_reservation_ttl_invalid",
        )
        if ttl <= 0.0 or ttl > self.max_age_sec:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_reservation_ttl_invalid"
            )
        entry_id = conversation_ingress_entry_id(
            surface=normalized_surface,
            scope=normalized_scope,
            source_delivery_id=normalized_delivery_id,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            existing = self._entries.get(entry_id)
            if existing is not None and (
                existing["phase"] != "reserved"
                or existing["textHash"] != normalized_text_hash
                or existing["turnId"] != normalized_turn_id
                or existing["surface"] != normalized_surface
                or existing["scope"] != normalized_scope
                or existing["sourceDeliveryId"]
                != normalized_delivery_id
            ):
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_binding_mismatch"
                )
            before = _clone_entries(self._entries)
            if existing is None and len(self._entries) >= self.max_entries:
                completed = sorted(
                    (
                        entry
                        for entry in self._entries.values()
                        if entry["phase"] == "completed"
                    ),
                    key=lambda item: (
                        float(item["updatedAt"]),
                        str(item["entryId"]),
                    ),
                )
                if not completed:
                    raise ConversationIngressRecoveryError(
                        "conversation_ingress_capacity_exhausted"
                    )
                self._entries.pop(str(completed[0]["entryId"]), None)
            now = self._now()
            reserved = {
                "entryId": entry_id,
                "surface": normalized_surface,
                "scope": normalized_scope,
                "sourceDeliveryId": normalized_delivery_id,
                "turnId": normalized_turn_id,
                "phase": "reserved",
                "textHash": normalized_text_hash,
                "assistantHash": "",
                "assistantBindingHash": "",
                "acceptedText": "",
                "assistantText": "",
                "memoryReceiptRef": unattributed_memory_receipt_ref(),
                "deliveryRef": normalized_reservation_ref,
                "continuityGeneration": 0,
                "createdAt": now,
                "updatedAt": now,
                "expiresAt": now + ttl,
                "recoveredAt": 0.0,
                "lastErrorCode": "",
            }
            self._entries[entry_id] = reserved
            try:
                self._write()
            except Exception:
                self._entries = before
                raise
            return self._receipt(reserved, disposition="reserved")

    def claim_reserved_ingress(
        self,
        *,
        surface: Any,
        scope: Any,
        source_delivery_id: Any,
        accepted_text: Any,
        turn_id: Any,
        reservation_ref: Any,
    ) -> dict[str, Any]:
        """Promote an exact durable reservation to accepted ingress."""

        normalized_surface = _surface(surface)
        normalized_scope = _bounded_identifier(
            scope,
            code="conversation_ingress_scope_invalid",
            max_chars=512,
        )
        normalized_delivery_id = _bounded_identifier(
            source_delivery_id,
            code="conversation_ingress_source_delivery_id_invalid",
            max_chars=512,
        )
        normalized_text = self._content(
            accepted_text,
            code="conversation_ingress_accepted_text_invalid",
        )
        text_hash = final_text_sha256(normalized_text)
        normalized_turn_id = _turn_id(turn_id)
        normalized_reservation_ref = _sha256(reservation_ref)
        entry_id = conversation_ingress_entry_id(
            surface=normalized_surface,
            scope=normalized_scope,
            source_delivery_id=normalized_delivery_id,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            entry = self._entry(entry_id)
            exact_binding = bool(
                entry["textHash"] == text_hash
                and entry["turnId"] == normalized_turn_id
                and entry["deliveryRef"] == normalized_reservation_ref
                and entry["surface"] == normalized_surface
                and entry["scope"] == normalized_scope
                and entry["sourceDeliveryId"] == normalized_delivery_id
            )
            if entry["phase"] == "accepted" and exact_binding:
                if entry["acceptedText"] != normalized_text:
                    raise ConversationIngressBindingMismatch(
                        "conversation_ingress_binding_mismatch"
                    )
                return self._receipt(entry)
            if entry["phase"] != "reserved" or not exact_binding:
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_binding_mismatch"
                )
            now = self._now()

            def mutate() -> None:
                entry.update(
                    {
                        "phase": "accepted",
                        "acceptedText": normalized_text,
                        "createdAt": now,
                        "updatedAt": now,
                        "expiresAt": now + self.max_age_sec,
                        "recoveredAt": 0.0,
                    }
                )

            self._mutate_and_write(mutate)
            return self._receipt(
                entry,
                disposition="claimed",
                should_process=True,
            )

    def revoke_reserved_ingress_batch(
        self,
        reservations: Any,
    ) -> dict[str, Any]:
        """Atomically delete only an exact set of unconsumed reservations."""

        if not isinstance(reservations, (list, tuple)) or not reservations:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_reservation_revocation_invalid"
            )
        if len(reservations) > 256:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_reservation_revocation_invalid"
            )
        normalized: list[dict[str, str]] = []
        for raw in reservations:
            if not isinstance(raw, dict):
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_reservation_revocation_invalid"
                )
            surface = _surface(raw.get("surface"))
            scope = _bounded_identifier(
                raw.get("scope"),
                code="conversation_ingress_scope_invalid",
                max_chars=512,
            )
            source_delivery_id = _bounded_identifier(
                raw.get("source_delivery_id"),
                code="conversation_ingress_source_delivery_id_invalid",
                max_chars=512,
            )
            normalized.append(
                {
                    "entryId": conversation_ingress_entry_id(
                        surface=surface,
                        scope=scope,
                        source_delivery_id=source_delivery_id,
                    ),
                    "surface": surface,
                    "scope": scope,
                    "sourceDeliveryId": source_delivery_id,
                    "turnId": _turn_id(raw.get("turn_id")),
                    "textHash": _sha256(raw.get("text_hash")),
                    "reservationRef": _sha256(
                        raw.get("reservation_ref")
                    ),
                }
            )
        normalized.sort(key=lambda item: item["entryId"])
        if len({item["entryId"] for item in normalized}) != len(normalized):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_reservation_revocation_invalid"
            )

        with self._lock:
            self._require_ready()
            self._prune_expired()
            for item in normalized:
                entry = self._entries.get(item["entryId"])
                if not entry or not (
                    entry["phase"] == "reserved"
                    and entry["surface"] == item["surface"]
                    and entry["scope"] == item["scope"]
                    and entry["sourceDeliveryId"]
                    == item["sourceDeliveryId"]
                    and entry["turnId"] == item["turnId"]
                    and entry["textHash"] == item["textHash"]
                    and entry["deliveryRef"] == item["reservationRef"]
                ):
                    raise ConversationIngressBindingMismatch(
                        "conversation_ingress_binding_mismatch"
                    )
            before = _clone_entries(self._entries)
            for item in normalized:
                self._entries.pop(item["entryId"], None)
            try:
                self._write()
            except Exception:
                self._entries = before
                raise
            return {
                "schema": (
                    CONVERSATION_INGRESS_RESERVATION_REVOCATION_RECEIPT_SCHEMA
                ),
                "durable": True,
                "revokedCount": len(normalized),
                "bindings": [
                    {
                        "entryId": item["entryId"],
                        "turnId": item["turnId"],
                        "textHash": item["textHash"],
                        "reservationRef": item["reservationRef"],
                    }
                    for item in normalized
                ],
                "journalGeneration": int(self._generation),
            }

    def revoke_reserved_ingress_scope(
        self,
        *,
        surface: Any,
        scope: Any,
    ) -> dict[str, Any]:
        """Atomically delete every reservation in one exact owner scope."""

        normalized_surface = _surface(surface)
        normalized_scope = _bounded_identifier(
            scope,
            code="conversation_ingress_scope_invalid",
            max_chars=512,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            bindings = [
                {
                    "entryId": str(entry["entryId"]),
                    "turnId": str(entry["turnId"]),
                    "textHash": str(entry["textHash"]),
                    "reservationRef": str(entry["deliveryRef"]),
                }
                for entry in sorted(
                    self._entries.values(),
                    key=lambda item: str(item["entryId"]),
                )
                if entry["phase"] == "reserved"
                and entry["surface"] == normalized_surface
                and entry["scope"] == normalized_scope
            ]
            if bindings:
                before = _clone_entries(self._entries)
                for binding in bindings:
                    self._entries.pop(binding["entryId"], None)
                try:
                    self._write()
                except Exception:
                    self._entries = before
                    raise
            return {
                "schema": (
                    CONVERSATION_INGRESS_RESERVATION_REVOCATION_RECEIPT_SCHEMA
                ),
                "durable": True,
                "revokedCount": len(bindings),
                "bindings": bindings,
                "journalGeneration": int(self._generation),
            }

    def claim(
        self,
        *,
        surface: Any,
        scope: Any,
        source_delivery_id: Any,
        accepted_text: Any,
        turn_id: Any = "",
    ) -> dict[str, Any]:
        normalized_surface = _surface(surface)
        normalized_scope = _bounded_identifier(
            scope,
            code="conversation_ingress_scope_invalid",
            max_chars=512,
        )
        normalized_delivery_id = _bounded_identifier(
            source_delivery_id,
            code="conversation_ingress_source_delivery_id_invalid",
            max_chars=512,
        )
        normalized_text = self._content(
            accepted_text,
            code="conversation_ingress_accepted_text_invalid",
        )
        text_hash = final_text_sha256(normalized_text)
        entry_id = conversation_ingress_entry_id(
            surface=normalized_surface,
            scope=normalized_scope,
            source_delivery_id=normalized_delivery_id,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            existing = self._entries.get(entry_id)
            if existing is not None:
                if (
                    existing["textHash"] != text_hash
                    or existing["acceptedText"] != normalized_text
                    or existing["surface"] != normalized_surface
                    or existing["scope"] != normalized_scope
                    or existing["sourceDeliveryId"]
                    != normalized_delivery_id
                ):
                    raise ConversationIngressBindingMismatch(
                        "conversation_ingress_binding_mismatch"
                    )
                return self._receipt(existing)

            normalized_turn_id = (
                _turn_id(turn_id)
                if normalize_final_conversation_text(turn_id)
                else _turn_id(self.turn_id_factory())
            )
            before = _clone_entries(self._entries)
            if len(self._entries) >= self.max_entries:
                completed = sorted(
                    (
                        entry
                        for entry in self._entries.values()
                        if entry["phase"] == "completed"
                    ),
                    key=lambda item: (
                        float(item["updatedAt"]),
                        str(item["entryId"]),
                    ),
                )
                if not completed:
                    raise ConversationIngressRecoveryError(
                        "conversation_ingress_capacity_exhausted"
                    )
                self._entries.pop(str(completed[0]["entryId"]), None)
            now = self._now()
            self._entries[entry_id] = {
                "entryId": entry_id,
                "surface": normalized_surface,
                "scope": normalized_scope,
                "sourceDeliveryId": normalized_delivery_id,
                "turnId": normalized_turn_id,
                "phase": "accepted",
                "textHash": text_hash,
                "assistantHash": "",
                "assistantBindingHash": "",
                "acceptedText": normalized_text,
                "assistantText": "",
                "memoryReceiptRef": unattributed_memory_receipt_ref(),
                "deliveryRef": "",
                "continuityGeneration": 0,
                "createdAt": now,
                "updatedAt": now,
                "expiresAt": now + self.max_age_sec,
                "recoveredAt": 0.0,
                "lastErrorCode": "",
            }
            try:
                self._write()
            except Exception:
                self._entries = before
                raise
            return self._receipt(
                self._entries[entry_id],
                disposition="claimed",
                should_process=True,
            )

    def mark_response_ready(
        self,
        entry_id: Any,
        *,
        assistant_text: Any,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        normalized_assistant = self._content(
            assistant_text,
            code="conversation_ingress_assistant_text_invalid",
        )
        assistant_hash = final_text_sha256(normalized_assistant)
        normalized_receipt_ref = _memory_receipt_ref(
            memory_receipt_ref
        )
        assistant_binding_hash = _assistant_binding_hash(
            assistant_hash,
            normalized_receipt_ref,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            entry = self._entry(entry_id)
            if entry["phase"] != "accepted":
                if (
                    entry["assistantBindingHash"]
                    == assistant_binding_hash
                    and entry["assistantText"]
                    == normalized_assistant
                ):
                    return self._receipt(entry)
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_assistant_binding_mismatch"
                )

            def mutate() -> None:
                entry.update(
                    {
                        "phase": "response_ready",
                        "assistantText": normalized_assistant,
                        "assistantHash": assistant_hash,
                        "assistantBindingHash": (
                            assistant_binding_hash
                        ),
                        "memoryReceiptRef": normalized_receipt_ref,
                        "updatedAt": self._now(),
                        "lastErrorCode": "",
                    }
                )

            self._mutate_and_write(mutate)
            return self._receipt(entry, disposition="response_ready")

    def bind_response(
        self,
        entry_id: Any,
        *,
        assistant_text: Any,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        """Bind a final streamed response without erasing delivery state."""

        normalized_assistant = self._content(
            assistant_text,
            code="conversation_ingress_assistant_text_invalid",
        )
        assistant_hash = final_text_sha256(normalized_assistant)
        normalized_receipt_ref = _memory_receipt_ref(
            memory_receipt_ref
        )
        assistant_binding_hash = _assistant_binding_hash(
            assistant_hash,
            normalized_receipt_ref,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            entry = self._entry(entry_id)
            if entry["assistantText"]:
                if (
                    entry["assistantBindingHash"]
                    == assistant_binding_hash
                    and entry["assistantText"]
                    == normalized_assistant
                ):
                    return self._receipt(entry)
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_assistant_binding_mismatch"
                )
            if entry["phase"] not in {
                "accepted",
                "delivery_inflight",
                "delivery_ambiguous",
            }:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_transition_invalid"
                )

            def mutate() -> None:
                if entry["phase"] == "accepted":
                    entry["phase"] = "response_ready"
                entry.update(
                    {
                        "assistantText": normalized_assistant,
                        "assistantHash": assistant_hash,
                        "assistantBindingHash": (
                            assistant_binding_hash
                        ),
                        "memoryReceiptRef": normalized_receipt_ref,
                        "updatedAt": self._now(),
                    }
                )

            self._mutate_and_write(mutate)
            return self._receipt(entry)

    def mark_stream_delivery_inflight(
        self,
        entry_id: Any,
        *,
        delivery_ref: Any = "",
    ) -> dict[str, Any]:
        """Mark the first externally visible stream delta before it is sent."""

        normalized_ref = _bounded_identifier(
            delivery_ref,
            code="conversation_ingress_delivery_ref_invalid",
            max_chars=512,
            allow_empty=True,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            entry = self._entry(entry_id)
            if entry["phase"] not in {"accepted", "response_ready"}:
                if entry["phase"] in {
                    "delivery_inflight",
                    "delivery_succeeded",
                    "delivery_ambiguous",
                    "terminal_committing",
                    "completed",
                } and (
                    not normalized_ref
                    or entry["deliveryRef"] == normalized_ref
                ):
                    return self._receipt(entry)
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_transition_invalid"
                )

            def mutate() -> None:
                entry.update(
                    {
                        "phase": "delivery_inflight",
                        "deliveryRef": normalized_ref,
                        "updatedAt": self._now(),
                        "lastErrorCode": "",
                    }
                )

            self._mutate_and_write(mutate)
            return self._receipt(
                entry,
                disposition="delivery_inflight",
            )

    def mark_delivery_inflight(
        self,
        entry_id: Any,
        *,
        delivery_ref: Any = "",
    ) -> dict[str, Any]:
        normalized_ref = _bounded_identifier(
            delivery_ref,
            code="conversation_ingress_delivery_ref_invalid",
            max_chars=512,
            allow_empty=True,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            entry = self._entry(entry_id)
            if entry["phase"] != "response_ready":
                if entry["phase"] in {
                    "delivery_inflight",
                    "delivery_succeeded",
                    "delivery_ambiguous",
                    "terminal_committing",
                    "completed",
                } and (
                    not normalized_ref
                    or entry["deliveryRef"] == normalized_ref
                ):
                    return self._receipt(entry)
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_transition_invalid"
                )

            def mutate() -> None:
                entry.update(
                    {
                        "phase": "delivery_inflight",
                        "deliveryRef": normalized_ref,
                        "updatedAt": self._now(),
                        "lastErrorCode": "",
                    }
                )

            self._mutate_and_write(mutate)
            return self._receipt(
                entry,
                disposition="delivery_inflight",
            )

    def mark_delivery_succeeded(
        self,
        entry_id: Any,
        *,
        delivery_ref: Any = "",
    ) -> dict[str, Any]:
        normalized_ref = _bounded_identifier(
            delivery_ref,
            code="conversation_ingress_delivery_ref_invalid",
            max_chars=512,
            allow_empty=True,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            entry = self._entry(entry_id)
            if entry["phase"] not in {
                "delivery_inflight",
                "delivery_ambiguous",
            }:
                if entry["phase"] in {
                    "delivery_succeeded",
                    "terminal_committing",
                    "completed",
                } and (
                    not normalized_ref
                    or entry["deliveryRef"] == normalized_ref
                ):
                    return self._receipt(entry)
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_transition_invalid"
                )
            if not entry["assistantText"]:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_response_not_bound"
                )
            if (
                normalized_ref
                and entry["deliveryRef"]
                and entry["deliveryRef"] != normalized_ref
            ):
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_delivery_binding_mismatch"
                )

            def mutate() -> None:
                entry.update(
                    {
                        "phase": "delivery_succeeded",
                        "deliveryRef": (
                            normalized_ref or entry["deliveryRef"]
                        ),
                        "updatedAt": self._now(),
                        "lastErrorCode": "",
                    }
                )

            self._mutate_and_write(mutate)
            return self._receipt(
                entry,
                disposition="delivery_succeeded",
            )

    def mark_delivery_ambiguous(
        self,
        entry_id: Any,
        *,
        error_code: Any,
    ) -> dict[str, Any]:
        normalized_error = _error_code(
            error_code,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            entry = self._entry(entry_id)
            if entry["phase"] == "delivery_ambiguous":
                if entry["lastErrorCode"] == normalized_error:
                    return self._receipt(entry)
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_error_binding_mismatch"
                )
            if entry["phase"] in {"terminal_committing", "completed"}:
                return self._receipt(entry)
            if entry["phase"] != "delivery_inflight":
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_transition_invalid"
                )

            def mutate() -> None:
                entry.update(
                    {
                        "phase": "delivery_ambiguous",
                        "updatedAt": self._now(),
                        "lastErrorCode": normalized_error,
                    }
                )

            self._mutate_and_write(mutate)
            return self._receipt(
                entry,
                disposition="delivery_ambiguous",
            )

    def begin_terminal_commit(
        self,
        entry_id: Any,
        *,
        continuity_generation: Any,
        assistant_text: Any,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        generation = _positive_int(
            continuity_generation,
            code="conversation_ingress_continuity_generation_invalid",
        )
        normalized_assistant = self._content(
            assistant_text,
            code="conversation_ingress_assistant_text_invalid",
        )
        assistant_hash = final_text_sha256(normalized_assistant)
        normalized_receipt_ref = _memory_receipt_ref(
            memory_receipt_ref
        )
        assistant_binding_hash = _assistant_binding_hash(
            assistant_hash,
            normalized_receipt_ref,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            entry = self._entry(entry_id)
            if (
                entry["assistantBindingHash"]
                != assistant_binding_hash
                or entry["assistantText"] != normalized_assistant
            ):
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_assistant_binding_mismatch"
                )
            if entry["phase"] in {"terminal_committing", "completed"}:
                if int(entry["continuityGeneration"]) != generation:
                    raise ConversationIngressBindingMismatch(
                        "conversation_ingress_generation_binding_mismatch"
                    )
                return self._receipt(entry)
            if entry["phase"] != "delivery_succeeded":
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_transition_invalid"
                )

            def mutate() -> None:
                entry.update(
                    {
                        "phase": "terminal_committing",
                        "continuityGeneration": generation,
                        "updatedAt": self._now(),
                    }
                )

            self._mutate_and_write(mutate)
            return self._receipt(
                entry,
                disposition="terminal_committing",
            )

    def complete(
        self,
        entry_id: Any,
        *,
        continuity_generation: Any,
        assistant_text: Any,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        generation = _positive_int(
            continuity_generation,
            code="conversation_ingress_continuity_generation_invalid",
        )
        normalized_assistant = self._content(
            assistant_text,
            code="conversation_ingress_assistant_text_invalid",
        )
        assistant_hash = final_text_sha256(normalized_assistant)
        normalized_receipt_ref = _memory_receipt_ref(
            memory_receipt_ref
        )
        assistant_binding_hash = _assistant_binding_hash(
            assistant_hash,
            normalized_receipt_ref,
        )
        with self._lock:
            self._require_ready()
            self._prune_expired()
            entry = self._entry(entry_id)
            if (
                entry["assistantBindingHash"]
                != assistant_binding_hash
                or entry["assistantText"] != normalized_assistant
                or int(entry["continuityGeneration"]) != generation
            ):
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_terminal_binding_mismatch"
                )
            if entry["phase"] == "completed":
                return self._receipt(entry)
            if entry["phase"] != "terminal_committing":
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_transition_invalid"
                )

            def mutate() -> None:
                entry.update(
                    {
                        "phase": "completed",
                        "updatedAt": self._now(),
                        "lastErrorCode": "",
                    }
                )

            self._mutate_and_write(mutate)
            return self._receipt(entry, disposition="completed")

    def receipt_for(self, entry_id: Any) -> dict[str, Any] | None:
        normalized = _entry_id(entry_id)
        with self._lock:
            self._require_ready()
            self._prune_expired()
            entry = self._entries.get(normalized)
            return self._receipt(entry) if entry is not None else None

    @staticmethod
    def _record(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": CONVERSATION_INGRESS_RECOVERY_RECORD_SCHEMA,
            "entryId": str(entry["entryId"]),
            "surface": str(entry["surface"]),
            "scope": str(entry["scope"]),
            "sourceDeliveryId": str(entry["sourceDeliveryId"]),
            "turnId": str(entry["turnId"]),
            "phase": str(entry["phase"]),
            "acceptedText": str(entry["acceptedText"]),
            "assistantText": str(entry["assistantText"]),
            "assistantBindingHash": str(
                entry["assistantBindingHash"]
            ),
            "memoryReceiptRef": _memory_receipt_ref(
                entry["memoryReceiptRef"]
            ),
            "deliveryRef": str(entry["deliveryRef"]),
            "continuityGeneration": int(
                entry["continuityGeneration"]
            ),
            "lastErrorCode": str(entry["lastErrorCode"]),
            "automaticReplay": False,
            "replayable": bool(
                entry["phase"] == "completed"
                and entry["memoryReceiptRef"].get("state")
                in {"bound", "not_used"}
            ),
            "recovered": bool(float(entry["recoveredAt"])),
            "expiresAt": float(entry["expiresAt"]),
        }

    def record_for(self, entry_id: Any) -> dict[str, Any] | None:
        normalized = _entry_id(entry_id)
        with self._lock:
            self._require_ready()
            self._prune_expired()
            entry = self._entries.get(normalized)
            return self._record(entry) if entry is not None else None

    def replay_record_for(
        self,
        entry_id: Any,
    ) -> dict[str, Any] | None:
        """Return a completed reply only when its memory binding is attributable.

        A bound receipt still has to pass the existing deletion/exposure guard
        at the integration boundary immediately before user-visible replay.
        """

        record = self.record_for(entry_id)
        if record is None:
            return None
        if record["phase"] != "completed":
            raise ConversationIngressRecoveryError(
                "conversation_ingress_replay_not_terminal"
            )
        if record["replayable"] is not True:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_replay_unattributed"
            )
        return record

    def recovery_records(self) -> list[dict[str, Any]]:
        with self._lock:
            self._require_ready()
            self._prune_expired()
            return [
                self._record(entry)
                for entry in sorted(
                    self._entries.values(),
                    key=lambda item: (
                        float(item["createdAt"]),
                        str(item["entryId"]),
                    ),
                )
                if entry["phase"] in _PENDING_PHASES
            ]

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            counts = {phase: 0 for phase in sorted(_PHASES)}
            for entry in self._entries.values():
                counts[str(entry["phase"])] += 1
            return {
                "schema": CONVERSATION_INGRESS_RECOVERY_SCHEMA,
                "state": self._state,
                "enabled": self.enabled,
                "generation": int(self._generation),
                "entryCount": len(self._entries),
                "phases": counts,
                "integrity": self._integrity,
                "headState": self._head_state,
                "rollbackProtected": bool(
                    self._integrity == "verified"
                    and self._head_state == "current"
                ),
                "lastErrorCode": self._last_error_code,
                "policy": self._policy(),
            }


__all__ = [
    "CONVERSATION_INGRESS_RECOVERY_CHAIN_GENESIS",
    "CONVERSATION_INGRESS_RECOVERY_HEAD_SCHEMA",
    "CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA",
    "CONVERSATION_INGRESS_RECOVERY_RECORD_SCHEMA",
    "CONVERSATION_INGRESS_RECOVERY_SCHEMA",
    "CONVERSATION_INGRESS_RESERVATION_REVOCATION_RECEIPT_SCHEMA",
    "ConversationIngressBindingMismatch",
    "ConversationIngressRecoveryError",
    "ConversationIngressRecoveryJournal",
    "DEFAULT_INGRESS_MAX_AGE_SEC",
    "DEFAULT_INGRESS_MAX_BYTES",
    "DEFAULT_INGRESS_MAX_CONTENT_CHARS",
    "DEFAULT_INGRESS_MAX_ENTRIES",
    "conversation_ingress_entry_id",
    "final_text_sha256",
    "normalize_final_conversation_text",
]
