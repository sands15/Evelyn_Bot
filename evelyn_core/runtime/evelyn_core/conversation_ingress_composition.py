from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable

from .conversation_ingress_recovery import (
    CONVERSATION_INGRESS_RECOVERY_SCHEMA,
    ConversationIngressRecoveryError,
    ConversationIngressRecoveryJournal,
)
from .discord_ingress import DiscordTextIngressContext
from .conversation_ingress_restart_runtime import (
    ConversationIngressRestartDeps,
    reconcile_recovered_delivery_succeeded,
    verify_recovered_terminal_commit,
)


CONVERSATION_INGRESS_CONTEXT_SCHEMA = (
    "conversation.ingress-recovery-context.v1"
)


@dataclass(frozen=True)
class ConversationIngressCompositionDeps:
    journal_factory: Callable[[], ConversationIngressRecoveryJournal]
    log: Callable[..., Any]
    reconcile_delivery_succeeded: (
        Callable[[dict[str, Any]], int | None] | None
    ) = None
    verify_terminal_commit: (
        Callable[[dict[str, Any]], bool] | None
    ) = None


class ConversationIngressComposition:
    """Single-owner adapter between Main and the durable ingress journal."""

    def __init__(self, deps: ConversationIngressCompositionDeps) -> None:
        self.deps = deps
        self._lock = threading.RLock()
        self._journal: ConversationIngressRecoveryJournal | None = None
        self._owner_ready = False
        self._last_error_code = "conversation_ingress_owner_not_restored"
        self._reconciled_recovery_count = 0
        self._reconciliation_failure_count = 0

    def activate_after_continuity_restore(self) -> dict[str, Any]:
        """Create the only writer after Main owns the process and is restored."""

        with self._lock:
            try:
                if self._journal is None:
                    self._journal = self.deps.journal_factory()
            except Exception as exc:
                self._owner_ready = False
                self._last_error_code = (
                    "conversation_ingress_recovery_unavailable"
                )
                self.deps.log(
                    "[CONVERSATION INGRESS] owner_restore_failed errorType=",
                    type(exc).__name__,
                )
                return self.public_status()
            assert self._journal is not None
            status = self._journal.public_status()
            self._owner_ready = bool(
                status.get("enabled") is True
                and status.get("state") == "ready"
            )
            self._last_error_code = (
                ""
                if self._owner_ready
                else str(status.get("lastErrorCode") or "")
                or "conversation_ingress_recovery_unavailable"
            )
            if self._owner_ready:
                self._reconcile_recovered_records()
            return self.public_status()

    def _reconcile_recovered_records(self) -> None:
        assert self._journal is not None
        critical_unresolved = False
        for record in self._journal.recovery_records():
            phase = str(record.get("phase") or "")
            try:
                if phase == "delivery_succeeded":
                    if self.deps.reconcile_delivery_succeeded is None:
                        critical_unresolved = True
                        continue
                    generation = (
                        self.deps.reconcile_delivery_succeeded(record)
                    )
                    if generation is None:
                        critical_unresolved = True
                        continue
                    self._journal.begin_terminal_commit(
                        str(record["entryId"]),
                        continuity_generation=int(generation),
                        assistant_text=str(record["assistantText"]),
                        memory_receipt_ref=record["memoryReceiptRef"],
                    )
                    self._journal.complete(
                        str(record["entryId"]),
                        continuity_generation=int(generation),
                        assistant_text=str(record["assistantText"]),
                        memory_receipt_ref=record["memoryReceiptRef"],
                    )
                    self._reconciled_recovery_count += 1
                elif phase == "terminal_committing":
                    if (
                        self.deps.verify_terminal_commit is None
                        or self.deps.verify_terminal_commit(record)
                        is not True
                    ):
                        critical_unresolved = True
                        continue
                    self._journal.complete(
                        str(record["entryId"]),
                        continuity_generation=int(
                            record["continuityGeneration"]
                        ),
                        assistant_text=str(record["assistantText"]),
                        memory_receipt_ref=record["memoryReceiptRef"],
                    )
                    self._reconciled_recovery_count += 1
            except Exception as exc:
                critical_unresolved = critical_unresolved or phase in {
                    "delivery_succeeded",
                    "terminal_committing",
                }
                self._reconciliation_failure_count += 1
                self._last_error_code = (
                    "conversation_ingress_reconciliation_failed"
                )
                self.deps.log(
                    "[CONVERSATION INGRESS] reconciliation_failed errorType=",
                    type(exc).__name__,
                )
        if critical_unresolved:
            self._owner_ready = False
            self._reconciliation_failure_count += 1
            self._last_error_code = (
                "conversation_ingress_reconciliation_required"
            )

    def _ready_journal(self) -> ConversationIngressRecoveryJournal:
        with self._lock:
            if not self._owner_ready or self._journal is None:
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_recovery_unavailable"
                )
            return self._journal

    def claim_discord_text(
        self,
        ingress: DiscordTextIngressContext,
        accepted_text: str,
    ) -> dict[str, Any]:
        if ingress.message_id is None or int(ingress.message_id) <= 0:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_source_delivery_id_invalid"
            )
        return self.claim(
            surface="discord_text",
            scope=ingress.session_key,
            source_delivery_id=str(int(ingress.message_id)),
            accepted_text=accepted_text,
        )

    def claim(
        self,
        *,
        surface: str,
        scope: str,
        source_delivery_id: str,
        accepted_text: str,
    ) -> dict[str, Any]:
        """Surface-neutral claim hook for later Discord voice wiring."""
        with self._lock:
            journal = self._ready_journal()
            if any(
                record.get("surface") == surface
                and record.get("scope") == scope
                and record.get("phase")
                in {"delivery_succeeded", "terminal_committing"}
                for record in journal.recovery_records()
            ):
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_reconciliation_required"
                )
            return journal.claim(
                surface=surface,
                scope=scope,
                source_delivery_id=source_delivery_id,
                accepted_text=accepted_text,
            )

    def mark_response_ready(
        self,
        entry_id: str,
        *,
        assistant_text: str,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        return self._ready_journal().mark_response_ready(
            entry_id,
            assistant_text=assistant_text,
            memory_receipt_ref=memory_receipt_ref,
        )

    def bind_response(
        self,
        entry_id: str,
        *,
        assistant_text: str,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        return self._ready_journal().bind_response(
            entry_id,
            assistant_text=assistant_text,
            memory_receipt_ref=memory_receipt_ref,
        )

    def mark_stream_delivery_inflight(
        self,
        entry_id: str,
        *,
        delivery_ref: str,
    ) -> dict[str, Any]:
        return self._ready_journal().mark_stream_delivery_inflight(
            entry_id,
            delivery_ref=delivery_ref,
        )

    def mark_delivery_inflight(
        self,
        entry_id: str,
        *,
        delivery_ref: str,
    ) -> dict[str, Any]:
        return self._ready_journal().mark_delivery_inflight(
            entry_id,
            delivery_ref=delivery_ref,
        )

    def mark_delivery_succeeded(
        self,
        entry_id: str,
        *,
        delivery_ref: str,
    ) -> dict[str, Any]:
        return self._ready_journal().mark_delivery_succeeded(
            entry_id,
            delivery_ref=delivery_ref,
        )

    def mark_delivery_ambiguous(
        self,
        entry_id: str,
    ) -> dict[str, Any]:
        return self._ready_journal().mark_delivery_ambiguous(
            entry_id,
            error_code="conversation_ingress_delivery_ambiguous",
        )

    def begin_terminal_commit(
        self,
        entry_id: str,
        *,
        continuity_generation: int,
        assistant_text: str,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        return self._ready_journal().begin_terminal_commit(
            entry_id,
            continuity_generation=continuity_generation,
            assistant_text=assistant_text,
            memory_receipt_ref=memory_receipt_ref,
        )

    def complete(
        self,
        entry_id: str,
        *,
        continuity_generation: int,
        assistant_text: str,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        return self._ready_journal().complete(
            entry_id,
            continuity_generation=continuity_generation,
            assistant_text=assistant_text,
            memory_receipt_ref=memory_receipt_ref,
        )

    def recovery_context_for_scope(
        self,
        scope: str,
        *,
        surface: str = "discord_text",
        exclude_entry_id: str = "",
    ) -> dict[str, Any]:
        """Private bounded hook for a later context owner; never auto-replay."""

        records = self._ready_journal().recovery_records()
        rows: list[dict[str, Any]] = []
        for record in records:
            if (
                record.get("surface") != surface
                or record.get("scope") != scope
                or record.get("entryId") == exclude_entry_id
            ):
                continue
            phase = str(record["phase"])
            row: dict[str, Any] = {
                "entryId": str(record["entryId"]),
                "turnId": str(record["turnId"]),
                "phase": phase,
                "acceptedText": str(record["acceptedText"]),
                "deliveryAmbiguous": bool(
                    phase == "delivery_ambiguous"
                ),
                "automaticReplay": False,
            }
            rows.append(row)
        return {
            "schema": CONVERSATION_INGRESS_CONTEXT_SCHEMA,
            "surface": surface,
            "scope": scope,
            "pendingCount": len(rows),
            "records": rows,
            "automaticReplay": False,
        }

    def record_for(self, entry_id: str) -> dict[str, Any] | None:
        """Private owner inspection used for exact restart reconciliation."""

        return self._ready_journal().record_for(entry_id)

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            if self._journal is None:
                return {
                    "schema": CONVERSATION_INGRESS_RECOVERY_SCHEMA,
                    "state": "not_restored",
                    "enabled": True,
                    "ownerReady": False,
                    "entryCount": 0,
                    "unansweredRecoveryCount": 0,
                    "ambiguousRecoveryCount": 0,
                    "lastErrorCode": self._last_error_code,
                }
            journal_status = dict(self._journal.public_status())
            phases = journal_status.get("phases")
            phase_counts = phases if isinstance(phases, dict) else {}
            public_phases = {
                phase: int(phase_counts.get(phase, 0) or 0)
                for phase in (
                    "accepted",
                    "response_ready",
                    "delivery_inflight",
                    "delivery_succeeded",
                    "delivery_ambiguous",
                    "terminal_committing",
                    "completed",
                )
            }
            return {
                "schema": CONVERSATION_INGRESS_RECOVERY_SCHEMA,
                "state": str(journal_status.get("state") or "unknown"),
                "enabled": journal_status.get("enabled") is True,
                "ownerReady": self._owner_ready,
                "entryCount": sum(public_phases.values()),
                "phases": public_phases,
                "unansweredRecoveryCount": sum(
                    public_phases[phase]
                    for phase in ("accepted", "response_ready")
                ),
                "ambiguousRecoveryCount": public_phases[
                    "delivery_ambiguous"
                ],
                "reconciledRecoveryCount": (
                    self._reconciled_recovery_count
                ),
                "reconciliationFailureCount": (
                    self._reconciliation_failure_count
                ),
                "lastErrorCode": (
                    str(journal_status.get("lastErrorCode") or "")
                    or self._last_error_code
                ),
            }


def build_main_conversation_ingress_composition(
    artifacts_root: Path,
    enabled: bool,
    session_continuity_checkpoint: Any,
    normal_ttl_sec: float,
    question_ttl_sec: float,
    log: Callable[..., Any],
) -> ConversationIngressComposition:
    restart_deps = ConversationIngressRestartDeps(
        session_state_store=session_continuity_checkpoint.store,
        session_continuity_checkpoint=session_continuity_checkpoint,
        system_prompt=session_continuity_checkpoint.system_prompt,
        max_history_items=session_continuity_checkpoint.max_history_items,
        normal_ttl_sec=normal_ttl_sec,
        question_ttl_sec=question_ttl_sec,
        log=log,
    )
    root = Path(artifacts_root) / "conversation_ingress"
    return ConversationIngressComposition(
        ConversationIngressCompositionDeps(
            journal_factory=lambda: ConversationIngressRecoveryJournal(
                path=root / "main.json",
                head_path=root / "main.head.json",
                enabled=enabled,
            ),
            log=log,
            reconcile_delivery_succeeded=partial(
                reconcile_recovered_delivery_succeeded,
                deps=restart_deps,
            ),
            verify_terminal_commit=partial(
                verify_recovered_terminal_commit,
                deps=restart_deps,
            ),
        )
    )


__all__ = [
    "CONVERSATION_INGRESS_CONTEXT_SCHEMA",
    "ConversationIngressComposition",
    "ConversationIngressCompositionDeps",
    "build_main_conversation_ingress_composition",
]
