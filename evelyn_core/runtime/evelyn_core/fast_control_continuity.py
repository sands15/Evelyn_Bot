from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from .continuity_authenticity import (
    CONTINUITY_AUTH_SCOPE_FAST_CONTROL,
    ContinuityAuthenticity,
)
from .conversation_memory_receipt import (
    memory_receipt_ref_from_receipt,
    sanitize_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from .conversation_ingress_recovery import (
    ConversationIngressBindingMismatch,
    ConversationIngressRecoveryError,
    ConversationIngressRecoveryJournal,
    final_text_sha256,
    normalize_final_conversation_text,
)
from .session_continuity import SessionContinuityCheckpoint
from .session_memory_state import (
    SessionStateStore,
    build_topic_id,
)
from .text import clean_text


FAST_CONTROL_CONTINUITY_STATUS_SCHEMA = (
    "fast_control.continuity-status.v1"
)
FAST_CONTROL_INGRESS_RECOVERY_STATUS_SCHEMA = (
    "fast_control.ingress-recovery-status.v1"
)
FAST_CONTROL_SESSION_KEY = "fast-control:control-page:owner"
FAST_CONTROL_INGRESS_SURFACE = "fast_control"
FAST_CONTROL_SYSTEM_PROMPT = (
    "Evelyn fast-control short-lived conversation continuity"
)
DEFAULT_FAST_CONTROL_MAX_AGE_SEC = 30 * 60.0
DEFAULT_FAST_CONTROL_MAX_HISTORY_ITEMS = 40
FAST_CONTROL_EPHEMERAL_VALIDATION_DELIVERY_REF = (
    "fast-control:voice-validation-ephemeral"
)
FAST_CONTROL_LOCAL_PLAYBACK_DELIVERY_REF = "fast-control:local-playback"
_EPHEMERAL_VALIDATION_RESPONSE_PREFIX = (
    "validation-response-sha256:"
)
_EPHEMERAL_VALIDATION_RECOVERY_PREFIX = (
    "validation-recovery-sha256:"
)


class FastControlContinuityOwner:
    """Own a bounded checkpoint for the standalone Bot API conversation."""

    def __init__(
        self,
        *,
        artifacts_root: Path,
        enabled: bool,
        max_age_sec: float = DEFAULT_FAST_CONTROL_MAX_AGE_SEC,
        max_history_items: int = (
            DEFAULT_FAST_CONTROL_MAX_HISTORY_ITEMS
        ),
        wall_time: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        authenticity: ContinuityAuthenticity | None = None,
        log: Callable[..., Any] = print,
    ) -> None:
        self.enabled = bool(enabled)
        self.artifacts_root = Path(artifacts_root)
        self.max_age_sec = max(1.0, float(max_age_sec))
        self.max_history_items = max(
            2,
            int(max_history_items),
        )
        self.wall_time = wall_time
        self.monotonic = monotonic
        self.authenticity = (
            authenticity or ContinuityAuthenticity()
        )
        self.log = log
        self._lock = threading.RLock()
        self.store = SessionStateStore.create_empty()
        self.checkpoint: SessionContinuityCheckpoint | None = None
        self.ingress: ConversationIngressRecoveryJournal | None = None
        self.restore_status: dict[str, Any] = {
            "state": "disabled",
        }
        self.ingress_recovery_status: dict[str, Any] = {
            "schema": FAST_CONTROL_INGRESS_RECOVERY_STATUS_SCHEMA,
            "state": "disabled",
            "pendingCount": 0,
            "reconciledCount": 0,
            "blockedCount": 0,
            "contentFree": True,
        }
        if not self.enabled:
            return
        root = self.artifacts_root / "fast_control_continuity"
        self.checkpoint = SessionContinuityCheckpoint(
            store=self.store,
            checkpoint_path=root / "active.json",
            status_path=root / "status.json",
            system_prompt=FAST_CONTROL_SYSTEM_PROMPT,
            max_age_sec=self.max_age_sec,
            max_sessions=1,
            max_history_items=self.max_history_items,
            wall_time=self.wall_time,
            monotonic=self.monotonic,
            authenticity=self.authenticity,
            authenticity_scope=(
                CONTINUITY_AUTH_SCOPE_FAST_CONTROL
            ),
            log=self.log,
        )
        self.restore_status = self.checkpoint.restore()
        if self.restore_status.get("state") == "missing":
            # Establish an exact, content-free generation-1 checkpoint head.
            # This is the only accepted fresh bootstrap; corrupt or partially
            # missing chains remain fail-closed in restore().
            self.restore_status = self.checkpoint.flush(force=True)
        self.ingress = ConversationIngressRecoveryJournal(
            path=root / "ingress.json",
            head_path=root / "ingress.head.json",
            enabled=True,
            wall_time=self.wall_time,
        )
        self.ingress_recovery_status = (
            self._reconcile_ingress_after_restore()
        )

    def restored_chat_messages(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        history = self.store.histories.get(
            FAST_CONTROL_SESSION_KEY,
            [],
        )
        restored_at = float(
            self.restore_status.get("lastRestoredAt")
            or self.wall_time()
        )
        messages: list[dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = clean_text(item.get("role")).lower()
            content = clean_text(item.get("content"))
            if role not in {"user", "assistant"} or not content:
                continue
            receipt_present = "memoryReceiptRef" in item
            if role == "user" and receipt_present:
                continue
            message: dict[str, Any] = {
                "role": role,
                "author": (
                    "정훈"
                    if role == "user"
                    else "Evelyn"
                ),
                "text": content,
                "at": restored_at,
                "source": (
                    "fast_control_continuity_restore"
                ),
            }
            if role == "assistant" and receipt_present:
                receipt_ref = sanitize_memory_receipt_ref(
                    item.get("memoryReceiptRef")
                )
                if receipt_ref is None:
                    continue
                message["memoryReceiptRef"] = receipt_ref
            messages.append(message)
        return messages[-self.max_history_items :]

    def _require_checkpoint(
        self,
    ) -> SessionContinuityCheckpoint:
        if not self.enabled or self.checkpoint is None:
            raise RuntimeError("fast_control_continuity_disabled")
        return self.checkpoint

    def _require_ingress(
        self,
    ) -> ConversationIngressRecoveryJournal:
        if not self.enabled or self.ingress is None:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_recovery_unavailable"
            )
        checkpoint = self._require_checkpoint()
        checkpoint_status = checkpoint.status()
        ingress_status = self.ingress.public_status()
        checkpoint_current = bool(
            checkpoint_status.get("rollbackProtected") is True
            and (
                (
                    checkpoint_status.get("checkpointIntegrity")
                    == "verified"
                    and checkpoint_status.get("checkpointHeadState")
                    == "current"
                )
                or (
                    checkpoint_status.get("checkpointIntegrity")
                    == "empty"
                    and checkpoint_status.get("checkpointHeadState")
                    == "empty"
                    and int(
                        checkpoint_status.get("persistedSessionCount")
                        or 0
                    )
                    == 0
                )
            )
        )
        if (
            not checkpoint_current
            or ingress_status.get("rollbackProtected") is not True
            or ingress_status.get("state") != "ready"
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_recovery_unavailable"
            )
        return self.ingress

    def claim_ingress(
        self,
        *,
        request_id: Any,
        accepted_text: Any,
    ) -> dict[str, Any]:
        """Durably claim one client request before any turn side effect."""

        with self._lock:
            ingress = self._require_ingress()
            normalized_request_id = normalize_final_conversation_text(
                request_id
            )
            self._reject_unrelated_pending(
                ingress,
                normalized_request_id,
            )
            return ingress.claim(
                surface=FAST_CONTROL_INGRESS_SURFACE,
                scope=FAST_CONTROL_SESSION_KEY,
                source_delivery_id=normalized_request_id,
                accepted_text=accepted_text,
            )

    @staticmethod
    def _reject_unrelated_pending(
        ingress: ConversationIngressRecoveryJournal,
        request_id: str,
    ) -> None:
        pending = ingress.recovery_records()
        if pending and not any(
            record.get("surface") == FAST_CONTROL_INGRESS_SURFACE
            and record.get("scope") == FAST_CONTROL_SESSION_KEY
            and normalize_final_conversation_text(
                record.get("sourceDeliveryId")
            )
            == request_id
            for record in pending
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_recovery_pending"
            )

    def reserve_ingress(
        self,
        *,
        request_id: Any,
        text_hash: Any,
        turn_id: Any,
        reservation_ref: Any,
        ttl_sec: Any,
    ) -> dict[str, Any]:
        """Reserve a content-free request binding before capability return."""

        with self._lock:
            return self._require_ingress().reserve_ingress(
                surface=FAST_CONTROL_INGRESS_SURFACE,
                scope=FAST_CONTROL_SESSION_KEY,
                source_delivery_id=normalize_final_conversation_text(
                    request_id
                ),
                text_hash=text_hash,
                turn_id=turn_id,
                reservation_ref=reservation_ref,
                ttl_sec=ttl_sec,
            )

    def claim_reserved_ingress(
        self,
        *,
        request_id: Any,
        accepted_text: Any,
        turn_id: Any,
        reservation_ref: Any,
    ) -> dict[str, Any]:
        """Atomically promote an exact request reservation to accepted."""

        with self._lock:
            ingress = self._require_ingress()
            normalized_request_id = normalize_final_conversation_text(
                request_id
            )
            self._reject_unrelated_pending(
                ingress,
                normalized_request_id,
            )
            return ingress.claim_reserved_ingress(
                surface=FAST_CONTROL_INGRESS_SURFACE,
                scope=FAST_CONTROL_SESSION_KEY,
                source_delivery_id=normalized_request_id,
                accepted_text=accepted_text,
                turn_id=turn_id,
                reservation_ref=reservation_ref,
            )

    def revoke_reserved_ingress_batch(
        self,
        reservations: Any,
    ) -> dict[str, Any]:
        """Atomically revoke exact unconsumed local-voice reservations."""

        if (
            not isinstance(reservations, (list, tuple))
            or not reservations
            or any(not isinstance(item, dict) for item in reservations)
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_reservation_revocation_invalid"
            )
        with self._lock:
            ingress = self._require_ingress()
            return ingress.revoke_reserved_ingress_batch(
                [
                    {
                        "surface": FAST_CONTROL_INGRESS_SURFACE,
                        "scope": FAST_CONTROL_SESSION_KEY,
                        "source_delivery_id": (
                            normalize_final_conversation_text(
                                item.get("request_id")
                            )
                        ),
                        "text_hash": item.get("text_hash"),
                        "turn_id": item.get("turn_id"),
                        "reservation_ref": item.get("reservation_ref"),
                    }
                    for item in reservations
                ]
            )

    def revoke_reserved_local_voice_ingress(self) -> dict[str, Any]:
        """Revoke every outstanding local-voice issuance reservation."""

        with self._lock:
            return self._require_ingress().revoke_reserved_ingress_scope(
                surface=FAST_CONTROL_INGRESS_SURFACE,
                scope=FAST_CONTROL_SESSION_KEY,
            )

    def bind_ingress_response(
        self,
        entry_id: Any,
        *,
        assistant_text: Any,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        with self._lock:
            return self._require_ingress().bind_response(
                entry_id,
                assistant_text=assistant_text,
                memory_receipt_ref=memory_receipt_ref,
            )

    def mark_ingress_delivery_inflight(
        self,
        entry_id: Any,
        *,
        delivery_ref: Any = "",
        streaming: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            ingress = self._require_ingress()
            if streaming:
                return ingress.mark_stream_delivery_inflight(
                    entry_id,
                    delivery_ref=delivery_ref,
                )
            return ingress.mark_delivery_inflight(
                entry_id,
                delivery_ref=delivery_ref,
            )

    def mark_ingress_delivery_succeeded(
        self,
        entry_id: Any,
        *,
        delivery_ref: Any = "",
    ) -> dict[str, Any]:
        with self._lock:
            return self._require_ingress().mark_delivery_succeeded(
                entry_id,
                delivery_ref=delivery_ref,
            )

    def mark_ingress_delivery_ambiguous(
        self,
        entry_id: Any,
        *,
        error_code: Any,
    ) -> dict[str, Any]:
        with self._lock:
            return self._require_ingress().mark_delivery_ambiguous(
                entry_id,
                error_code=error_code,
            )

    def discard_failed_ingress(
        self,
        entry_id: Any,
        *,
        assistant_hash: Any,
    ) -> None:
        with self._lock:
            ingress = self._require_ingress()
            checkpoint = self._require_checkpoint()
            record = ingress.record_for(entry_id)
            error_code = clean_text(
                (record or {}).get("lastErrorCode")
            )
            if (
                (record or {}).get("phase") != "delivery_ambiguous"
                or error_code not in {
                    "conversation_ingress_delivery_ambiguous",
                    "conversation_ingress_delivery_ambiguous_after_restart",
                    "conversation_ingress_delivery_disconnected",
                    "conversation_ingress_delivery_failed",
                    "conversation_ingress_process_interrupted",
                }
            ):
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_error_binding_mismatch"
                )
            normalized_assistant_hash = normalize_final_conversation_text(
                assistant_hash
            ).lower()
            if (
                normalized_assistant_hash
                and normalized_assistant_hash
                != final_text_sha256((record or {}).get("assistantText"))
            ):
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_delivery_binding_mismatch"
                )
            accepted_text = normalize_final_conversation_text(
                (record or {}).get("acceptedText")
            )
            turn_id = clean_text((record or {}).get("turnId"))
            if not accepted_text or not turn_id:
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_binding_mismatch"
                )
            user_turn_persisted = (
                self._checkpoint_contains_unanswered_ingress_record(
                    record
                )
            )
            if (
                self.store.current_turn_id(FAST_CONTROL_SESSION_KEY)
                == turn_id
                and not user_turn_persisted
            ):
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_binding_mismatch"
                )
            if not user_turn_persisted:
                self.store.start_new_turn(
                    FAST_CONTROL_SESSION_KEY,
                    turn_id=turn_id,
                    now_monotonic=self.monotonic(),
                )
                self.store.append_history(
                    FAST_CONTROL_SESSION_KEY,
                    accepted_text,
                    None,
                    system_prompt=FAST_CONTROL_SYSTEM_PROMPT,
                    max_history_items=self.max_history_items,
                )
            self.store.mark_active(
                FAST_CONTROL_SESSION_KEY,
                ttl_sec=self.max_age_sec,
                speaker="user",
                awaiting_user_reply=False,
                topic_id=build_topic_id(accepted_text),
                user_text=accepted_text,
                active_conversation_awaiting_reply_sec=(
                    self.max_age_sec
                ),
                now_monotonic=self.monotonic(),
            )
            checkpoint.commit_completed_turn(
                FAST_CONTROL_SESSION_KEY,
                turn_id,
            )
            ingress.discard_ambiguous(
                entry_id,
                assistant_hash=assistant_hash,
                delivery_ref=clean_text(
                    (record or {}).get("deliveryRef")
                ),
                error_code=error_code,
            )

    @staticmethod
    def _ephemeral_validation_response_marker(
        assistant_text: Any,
    ) -> str:
        normalized = normalize_final_conversation_text(assistant_text)
        if not normalized:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_assistant_text_invalid"
            )
        return (
            _EPHEMERAL_VALIDATION_RESPONSE_PREFIX
            + final_text_sha256(normalized)
        )

    @staticmethod
    def _is_ephemeral_validation_record(
        record: dict[str, Any],
    ) -> bool:
        marker = normalize_final_conversation_text(
            record.get("assistantText")
        )
        prefix = next(
            (
                candidate
                for candidate in (
                    _EPHEMERAL_VALIDATION_RESPONSE_PREFIX,
                    _EPHEMERAL_VALIDATION_RECOVERY_PREFIX,
                )
                if marker.startswith(candidate)
            ),
            "",
        )
        digest = marker.removeprefix(prefix) if prefix else ""
        receipt_ref = sanitize_memory_receipt_ref(
            record.get("memoryReceiptRef")
        )
        return bool(
            prefix
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
            and receipt_ref is not None
            and receipt_ref.get("state") == "unattributed"
            and receipt_ref.get("suppliedNoteCount") == 0
            and record.get("deliveryRef")
            == FAST_CONTROL_EPHEMERAL_VALIDATION_DELIVERY_REF
        )

    @staticmethod
    def _ephemeral_validation_recovery_marker(
        record: dict[str, Any],
    ) -> str:
        entry_id = clean_text(record.get("entryId"))
        if not entry_id:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_entry_not_found"
            )
        return (
            _EPHEMERAL_VALIDATION_RECOVERY_PREFIX
            + final_text_sha256(
                f"{entry_id}:{FAST_CONTROL_EPHEMERAL_VALIDATION_DELIVERY_REF}"
            )
        )

    def _finish_ephemeral_ingress(
        self,
        entry_id: Any,
    ) -> dict[str, Any]:
        """Finish a marked ingress by advancing only checkpoint metadata."""

        with self._lock:
            ingress = self._require_ingress()
            checkpoint = self._require_checkpoint()
            record = ingress.record_for(entry_id)
            if record is None:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_entry_not_found"
                )
            if not self._is_ephemeral_validation_record(record):
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_assistant_binding_mismatch"
                )
            phase = clean_text(record.get("phase"))
            marker = str(record["assistantText"])
            receipt_ref = record["memoryReceiptRef"]
            if phase == "completed":
                ingress_receipt = ingress.complete(
                    record["entryId"],
                    continuity_generation=record["continuityGeneration"],
                    assistant_text=marker,
                    memory_receipt_ref=receipt_ref,
                )
                result = dict(checkpoint.status())
                result["ingressReceipt"] = ingress_receipt
                result["ephemeral"] = True
                return result

            current_generation = self._checkpoint_generation()
            if phase == "delivery_succeeded":
                expected_generation = current_generation + 1
                ingress.begin_terminal_commit(
                    record["entryId"],
                    continuity_generation=expected_generation,
                    assistant_text=marker,
                    memory_receipt_ref=receipt_ref,
                )
            elif phase == "terminal_committing":
                expected_generation = int(
                    record["continuityGeneration"]
                )
            else:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_transition_invalid"
                )

            if current_generation + 1 == expected_generation:
                raw_status = checkpoint.flush(force=True)
            elif current_generation == expected_generation:
                raw_status = checkpoint.status()
            else:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_recovery_unavailable"
                )
            if (
                raw_status.get("state") == "error"
                or raw_status.get("rollbackProtected") is not True
                or self._checkpoint_generation() != expected_generation
            ):
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_recovery_unavailable"
                )
            self._require_ingress()
            ingress_receipt = ingress.complete(
                record["entryId"],
                continuity_generation=expected_generation,
                assistant_text=marker,
                memory_receipt_ref=receipt_ref,
            )
            result = dict(raw_status)
            result["ingressReceipt"] = ingress_receipt
            result["ephemeral"] = True
            return result

    def _complete_ephemeral_ingress_marker(
        self,
        entry_id: Any,
        *,
        marker: str,
    ) -> dict[str, Any]:
        receipt_ref = unattributed_memory_receipt_ref()
        with self._lock:
            ingress = self._require_ingress()
            record = ingress.record_for(entry_id)
            if record is None:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_entry_not_found"
                )
            if (
                record.get("deliveryRef")
                != FAST_CONTROL_EPHEMERAL_VALIDATION_DELIVERY_REF
            ):
                raise ConversationIngressBindingMismatch(
                    "conversation_ingress_delivery_binding_mismatch"
                )
            ingress.bind_response(
                entry_id,
                assistant_text=marker,
                memory_receipt_ref=receipt_ref,
            )
            record = ingress.record_for(entry_id)
            if record is None:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_entry_not_found"
                )
            ingress.mark_delivery_succeeded(
                entry_id,
                delivery_ref=record["deliveryRef"],
            )
            return self._finish_ephemeral_ingress(entry_id)

    def complete_ephemeral_ingress(
        self,
        entry_id: Any,
        *,
        assistant_text: Any,
        memory_receipt_ref: Any = None,
    ) -> dict[str, Any]:
        """Complete delivered validation ingress without session history."""

        marker = self._ephemeral_validation_response_marker(
            assistant_text
        )
        source_receipt_ref = self._receipt_ref(memory_receipt_ref)
        if source_receipt_ref.get("state") == "bound":
            raise ConversationIngressRecoveryError(
                "conversation_ingress_memory_receipt_invalid"
            )
        # Validation replies are intentionally non-replayable. The fixed
        # content-free receipt also makes retries independent of memory state.
        return self._complete_ephemeral_ingress_marker(
            entry_id,
            marker=marker,
        )

    def ingress_record(
        self,
        entry_id: Any,
        *,
        replay: bool = False,
    ) -> dict[str, Any] | None:
        with self._lock:
            ingress = self._require_ingress()
            if replay:
                return ingress.replay_record_for(entry_id)
            return ingress.record_for(entry_id)

    def recovered_ingress_context_messages(
        self,
        *,
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        """Privately project bounded recovered user turns without replay."""

        if not self.enabled or self.ingress is None:
            return []
        bounded_limit = max(1, min(8, int(limit)))
        with self._lock:
            ingress = self._require_ingress()
            records = ingress.recovery_records()
        messages: list[dict[str, Any]] = []
        seen_entry_ids: set[str] = set()
        for record in records[-bounded_limit:]:
            entry_id = str(record["entryId"])
            if (
                not bool(record.get("recovered"))
                or entry_id in seen_entry_ids
            ):
                continue
            seen_entry_ids.add(entry_id)
            accepted_text = normalize_final_conversation_text(
                record.get("acceptedText")
            )
            if not accepted_text:
                continue
            messages.append(
                {
                    "role": "user",
                    "content": accepted_text,
                    "_ingressRecoveryEntryId": entry_id,
                    "_ingressRecoveryUnanswered": True,
                }
            )
        return messages

    def ingress_recovery_projection(self) -> dict[str, Any]:
        """Expose restart truth without accepted or assistant content."""

        if not self.enabled or self.ingress is None:
            return {
                "schema": FAST_CONTROL_INGRESS_RECOVERY_STATUS_SCHEMA,
                "state": "disabled",
                "pendingCount": 0,
                "contentFree": True,
            }
        try:
            records = self.ingress.recovery_records()
        except ConversationIngressRecoveryError as exc:
            return {
                "schema": FAST_CONTROL_INGRESS_RECOVERY_STATUS_SCHEMA,
                "state": "unavailable",
                "pendingCount": 0,
                "lastErrorCode": exc.code,
                "contentFree": True,
            }
        phases: dict[str, int] = {}
        fixed_error_codes: set[str] = set()
        recovered_count = 0
        ambiguous_count = 0
        for record in records:
            phase = str(record["phase"])
            phases[phase] = phases.get(phase, 0) + 1
            recovered_count += int(bool(record["recovered"]))
            ambiguous_count += int(phase == "delivery_ambiguous")
            error_code = str(record["lastErrorCode"])
            if error_code:
                fixed_error_codes.add(error_code)
        return {
            "schema": FAST_CONTROL_INGRESS_RECOVERY_STATUS_SCHEMA,
            "state": "ready",
            "pendingCount": len(records),
            "phases": dict(sorted(phases.items())),
            "recoveredCount": recovered_count,
            "deliveryAmbiguousCount": ambiguous_count,
            "lastErrorCodes": sorted(fixed_error_codes),
            "automaticReplay": False,
            "contentFree": True,
        }

    @staticmethod
    def _receipt_ref(memory_receipt: Any) -> dict[str, Any]:
        return memory_receipt_ref_from_receipt(memory_receipt)

    def _checkpoint_generation(self) -> int:
        checkpoint = self._require_checkpoint()
        return max(
            0,
            int(
                checkpoint.status().get(
                    "checkpointGeneration"
                )
                or 0
            ),
        )

    def _checkpoint_contains_ingress_record(
        self,
        record: dict[str, Any],
    ) -> bool:
        if self.store.current_turn_id(FAST_CONTROL_SESSION_KEY) != clean_text(
            record.get("turnId")
        ):
            return False
        history = self.store.histories.get(
            FAST_CONTROL_SESSION_KEY,
            [],
        )
        if len(history) < 2:
            return False
        user = history[-2]
        assistant = history[-1]
        if not isinstance(user, dict) or not isinstance(assistant, dict):
            return False
        if (
            user.get("role") != "user"
            or assistant.get("role") != "assistant"
            or normalize_final_conversation_text(user.get("content"))
            != normalize_final_conversation_text(
                record.get("acceptedText")
            )
            or normalize_final_conversation_text(
                assistant.get("content")
            )
            != normalize_final_conversation_text(
                record.get("assistantText")
            )
        ):
            return False
        return sanitize_memory_receipt_ref(
            assistant.get("memoryReceiptRef")
        ) == sanitize_memory_receipt_ref(
            record.get("memoryReceiptRef")
        )

    def _checkpoint_contains_unanswered_ingress_record(
        self,
        record: dict[str, Any],
    ) -> bool:
        if self.store.current_turn_id(FAST_CONTROL_SESSION_KEY) != clean_text(
            record.get("turnId")
        ):
            return False
        history = self.store.histories.get(
            FAST_CONTROL_SESSION_KEY,
            [],
        )
        if not history or not isinstance(history[-1], dict):
            return False
        user = history[-1]
        return bool(
            user.get("role") == "user"
            and "memoryReceiptRef" not in user
            and normalize_final_conversation_text(user.get("content"))
            == normalize_final_conversation_text(
                record.get("acceptedText")
            )
        )

    def _reconcile_ingress_after_restore(
        self,
    ) -> dict[str, Any]:
        projection: dict[str, Any] = {
            "schema": FAST_CONTROL_INGRESS_RECOVERY_STATUS_SCHEMA,
            "state": "ready",
            "pendingCount": 0,
            "reconciledCount": 0,
            "blockedCount": 0,
            "contentFree": True,
        }
        try:
            ingress = self._require_ingress()
            records = ingress.recovery_records()
        except (ConversationIngressRecoveryError, RuntimeError) as exc:
            projection.update(
                {
                    "state": "unavailable",
                    "lastErrorCode": getattr(
                        exc,
                        "code",
                        "conversation_ingress_recovery_unavailable",
                    ),
                }
            )
            return projection

        for record in records:
            phase = clean_text(record.get("phase"))
            try:
                if (
                    phase == "delivery_ambiguous"
                    and self._checkpoint_contains_unanswered_ingress_record(
                        record
                    )
                ):
                    assistant_text = clean_text(
                        record.get("assistantText")
                    )
                    ingress.discard_ambiguous(
                        record["entryId"],
                        assistant_hash=(
                            final_text_sha256(assistant_text)
                            if assistant_text
                            else ""
                        ),
                        delivery_ref=record["deliveryRef"],
                        error_code=record["lastErrorCode"],
                    )
                    projection["reconciledCount"] += 1
                    continue
                if (
                    phase in {"delivery_inflight", "delivery_ambiguous"}
                    and record.get("deliveryRef")
                    == FAST_CONTROL_EPHEMERAL_VALIDATION_DELIVERY_REF
                ):
                    marker = clean_text(record.get("assistantText"))
                    if marker and not self._is_ephemeral_validation_record(
                        record
                    ):
                        raise ConversationIngressBindingMismatch(
                            "conversation_ingress_assistant_binding_mismatch"
                        )
                    self._complete_ephemeral_ingress_marker(
                        record["entryId"],
                        marker=(
                            marker
                            or self._ephemeral_validation_recovery_marker(
                                record
                            )
                        ),
                    )
                    projection["reconciledCount"] += 1
                    continue
                if (
                    phase in {"delivery_succeeded", "terminal_committing"}
                    and self._is_ephemeral_validation_record(record)
                ):
                    self._finish_ephemeral_ingress(record["entryId"])
                    projection["reconciledCount"] += 1
                    continue
                if phase == "delivery_succeeded":
                    self.record_completed_turn(
                        str(record["acceptedText"]),
                        str(record["assistantText"]),
                        memory_receipt=record["memoryReceiptRef"],
                        ingress_entry_id=str(record["entryId"]),
                    )
                    projection["reconciledCount"] += 1
                    continue
                if phase != "terminal_committing":
                    projection["blockedCount"] += 1
                    continue
                expected_generation = int(
                    record["continuityGeneration"]
                )
                current_generation = self._checkpoint_generation()
                if (
                    current_generation == expected_generation
                    and self._checkpoint_contains_ingress_record(record)
                ):
                    ingress.complete(
                        record["entryId"],
                        continuity_generation=expected_generation,
                        assistant_text=record["assistantText"],
                        memory_receipt_ref=record["memoryReceiptRef"],
                    )
                    projection["reconciledCount"] += 1
                    continue
                if current_generation + 1 == expected_generation:
                    self.record_completed_turn(
                        str(record["acceptedText"]),
                        str(record["assistantText"]),
                        memory_receipt=record["memoryReceiptRef"],
                        ingress_entry_id=str(record["entryId"]),
                    )
                    projection["reconciledCount"] += 1
                    continue
                projection["blockedCount"] += 1
            except Exception as exc:
                projection["blockedCount"] += 1
                self.log(
                    "[FAST CONTROL] ingress_reconcile_failed "
                    f"phase={phase or 'unknown'} "
                    f"errorType={type(exc).__name__}"
                )
        projection["pendingCount"] = len(
            self.ingress.recovery_records()
        )
        if projection["blockedCount"]:
            projection["state"] = "degraded"
        return projection

    def record_completed_turn(
        self,
        user_text: str,
        assistant_text: str,
        *,
        before_commit: Callable[[int], Any] | None = None,
        memory_receipt: Any = None,
        ingress_entry_id: str = "",
    ) -> dict[str, Any]:
        cleaned_user = clean_text(user_text)
        cleaned_assistant = clean_text(assistant_text)
        if not cleaned_user or not cleaned_assistant:
            raise ValueError("fast_control_turn_empty")
        with self._lock:
            checkpoint = self._require_checkpoint()
            ingress = (
                self._require_ingress()
                if clean_text(ingress_entry_id)
                else None
            )
            ingress_record = (
                ingress.record_for(ingress_entry_id)
                if ingress is not None
                else None
            )
            if ingress is not None and ingress_record is None:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_entry_not_found"
                )
            if ingress_record is not None and (
                normalize_final_conversation_text(
                    ingress_record.get("acceptedText")
                )
                != normalize_final_conversation_text(cleaned_user)
            ):
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_binding_mismatch"
                )
            receipt_ref = self._receipt_ref(memory_receipt)
            if ingress is not None:
                ingress.bind_response(
                    ingress_entry_id,
                    assistant_text=cleaned_assistant,
                    memory_receipt_ref=receipt_ref,
                )
            current_generation = self._checkpoint_generation()
            expected_generation = current_generation + 1
            if ingress_record is not None and (
                ingress_record.get("phase") == "terminal_committing"
            ):
                expected_generation = int(
                    ingress_record["continuityGeneration"]
                )
                if expected_generation != current_generation + 1:
                    raise ConversationIngressRecoveryError(
                        "conversation_ingress_recovery_unavailable"
                    )
            if before_commit is not None:
                before_commit(expected_generation)
            if ingress is not None:
                ingress.begin_terminal_commit(
                    ingress_entry_id,
                    continuity_generation=expected_generation,
                    assistant_text=cleaned_assistant,
                    memory_receipt_ref=receipt_ref,
                )
            turn_id = self.store.start_new_turn(
                FAST_CONTROL_SESSION_KEY,
                turn_id=(
                    str(ingress_record["turnId"])
                    if ingress_record is not None
                    else None
                ),
                now_monotonic=self.monotonic(),
            )
            self.store.finish_assistant_text_turn(
                FAST_CONTROL_SESSION_KEY,
                cleaned_user,
                cleaned_assistant,
                system_prompt=FAST_CONTROL_SYSTEM_PROMPT,
                max_history_items=self.max_history_items,
                awaiting_user_reply=False,
                normal_ttl_sec=self.max_age_sec,
                question_ttl_sec=self.max_age_sec,
                topic_id=build_topic_id(
                    cleaned_user,
                    cleaned_assistant,
                ),
                now_monotonic=self.monotonic(),
                memory_receipt=memory_receipt,
            )
            raw_status = checkpoint.commit_completed_turn(
                FAST_CONTROL_SESSION_KEY,
                turn_id,
            )
            if ingress is None:
                return raw_status
            ingress_receipt = ingress.complete(
                ingress_entry_id,
                continuity_generation=expected_generation,
                assistant_text=cleaned_assistant,
                memory_receipt_ref=receipt_ref,
            )
            result = dict(raw_status)
            result["ingressReceipt"] = ingress_receipt
            return result

    def record_assistant_followup(
        self,
        assistant_text: str,
        *,
        before_commit: Callable[[int], Any] | None = None,
        memory_receipt: Any = None,
    ) -> dict[str, Any]:
        cleaned_assistant = clean_text(assistant_text)
        if not cleaned_assistant:
            raise ValueError("fast_control_followup_empty")
        with self._lock:
            checkpoint = self._require_checkpoint()
            current_generation = max(
                0,
                int(
                    checkpoint.status().get(
                        "checkpointGeneration"
                    )
                    or 0
                ),
            )
            if before_commit is not None:
                before_commit(current_generation + 1)
            turn_id = self.store.start_new_turn(
                FAST_CONTROL_SESSION_KEY,
                now_monotonic=self.monotonic(),
            )
            history = self.store.get_conversation_history(
                system_prompt=FAST_CONTROL_SYSTEM_PROMPT,
                session_key=FAST_CONTROL_SESSION_KEY,
            )
            history.append(
                {
                    "role": "assistant",
                    "content": cleaned_assistant,
                    "memoryReceiptRef": (
                        unattributed_memory_receipt_ref()
                        if memory_receipt is None
                        else memory_receipt_ref_from_receipt(
                            memory_receipt
                        )
                    ),
                }
            )
            self.store.trim_history(
                system_prompt=FAST_CONTROL_SYSTEM_PROMPT,
                max_history_items=self.max_history_items,
                session_key=FAST_CONTROL_SESSION_KEY,
            )
            self.store.mark_active(
                FAST_CONTROL_SESSION_KEY,
                ttl_sec=self.max_age_sec,
                speaker="assistant",
                awaiting_user_reply=False,
                topic_id=build_topic_id(cleaned_assistant),
                answer_text=cleaned_assistant,
                active_conversation_awaiting_reply_sec=(
                    self.max_age_sec
                ),
                now_monotonic=self.monotonic(),
            )
            return checkpoint.commit_completed_turn(
                FAST_CONTROL_SESSION_KEY,
                turn_id,
            )

    def status(self) -> dict[str, Any]:
        if not self.enabled or self.checkpoint is None:
            return {
                "schema": FAST_CONTROL_CONTINUITY_STATUS_SCHEMA,
                "enabled": False,
                "state": "disabled",
                "durableReady": False,
                "generation": 0,
                "persistedSessionCount": 0,
                "messageCount": 0,
                "lastErrorCode": "",
                "keyedAuthenticity": False,
                "tamperEvident": False,
                "ingress": self.ingress_recovery_projection(),
                "policy": {"contentFree": True},
            }
        raw = self.checkpoint.status()
        ingress_status = (
            self.ingress.public_status()
            if self.ingress is not None
            else {
                "state": "unavailable",
                "rollbackProtected": False,
            }
        )
        ingress_journal_projection = {
            "state": clean_text(ingress_status.get("state"))
            or "unknown",
            "entryCount": max(
                0,
                int(ingress_status.get("entryCount") or 0),
            ),
            "phases": {
                clean_text(phase): max(0, int(count or 0))
                for phase, count in (
                    ingress_status.get("phases") or {}
                ).items()
                if clean_text(phase)
            },
            "integrity": clean_text(
                ingress_status.get("integrity")
            ),
            "headState": clean_text(
                ingress_status.get("headState")
            ),
            "rollbackProtected": bool(
                ingress_status.get("rollbackProtected")
            ),
            "lastErrorCode": clean_text(
                ingress_status.get("lastErrorCode")
            ),
            "contentFree": True,
        }
        return {
            "schema": FAST_CONTROL_CONTINUITY_STATUS_SCHEMA,
            "enabled": True,
            "state": clean_text(raw.get("state")) or "unknown",
            "durableReady": bool(
                raw.get("rollbackProtected") is True
                and (
                    (
                        raw.get("checkpointIntegrity") == "verified"
                        and raw.get("checkpointHeadState") == "current"
                    )
                    or (
                        raw.get("checkpointIntegrity") == "empty"
                        and raw.get("checkpointHeadState") == "empty"
                        and int(
                            raw.get("persistedSessionCount") or 0
                        )
                        == 0
                    )
                )
                and ingress_status.get("state") == "ready"
                and ingress_status.get("rollbackProtected") is True
                and (
                    raw.get("keyedAuthenticity") is not True
                    or raw.get("tamperEvident") is True
                )
            ),
            "generation": max(
                0,
                int(raw.get("checkpointGeneration") or 0),
            ),
            "persistedSessionCount": max(
                0,
                int(raw.get("persistedSessionCount") or 0),
            ),
            "messageCount": len(
                self.restored_chat_messages()
            ),
            "lastErrorCode": clean_text(
                raw.get("lastErrorCode")
            ),
            "keyedAuthenticity": bool(
                raw.get("keyedAuthenticity")
            ),
            "tamperEvident": bool(
                raw.get("tamperEvident")
            ),
            "ingress": {
                **self.ingress_recovery_projection(),
                "journal": ingress_journal_projection,
                "startup": dict(self.ingress_recovery_status),
            },
            "policy": {"contentFree": True},
        }


__all__ = [
    "DEFAULT_FAST_CONTROL_MAX_AGE_SEC",
    "DEFAULT_FAST_CONTROL_MAX_HISTORY_ITEMS",
    "FAST_CONTROL_CONTINUITY_STATUS_SCHEMA",
    "FAST_CONTROL_EPHEMERAL_VALIDATION_DELIVERY_REF",
    "FAST_CONTROL_LOCAL_PLAYBACK_DELIVERY_REF",
    "FAST_CONTROL_INGRESS_RECOVERY_STATUS_SCHEMA",
    "FAST_CONTROL_INGRESS_SURFACE",
    "FAST_CONTROL_SESSION_KEY",
    "FastControlContinuityOwner",
]
