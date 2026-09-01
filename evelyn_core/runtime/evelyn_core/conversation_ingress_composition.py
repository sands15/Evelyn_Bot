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
from .memory_vault import reset_guild_memory_vault
from .conversation_ingress_restart_runtime import (
    ConversationIngressRestartDeps,
    reconcile_recovered_delivery_succeeded,
    reconcile_recovered_terminal_commit,
)


CONVERSATION_INGRESS_CONTEXT_SCHEMA = (
    "conversation.ingress-recovery-context.v1"
)


def _manual_exact_lineage_counts() -> dict[str, Any]:
    return {
        "removedCount": 0,
        "remainingCopies": 0,
        "manualReviewCount": 1,
        "contentFree": True,
    }


@dataclass(frozen=True)
class ConversationIngressCompositionDeps:
    journal_factory: Callable[[], ConversationIngressRecoveryJournal]
    log: Callable[..., Any]
    active_guild_revocation_ids: Callable[[], tuple[int, ...]]
    reset_session_continuity_guild: (
        Callable[[int, Callable[[], Any]], Any]
    )
    reset_guild_persistent_memory: Callable[[int], Any]
    reset_guild_recovery_metadata: Callable[[int], Any] | None = None
    reconcile_delivery_succeeded: (
        Callable[..., int | None] | None
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
        self._guild_epochs: dict[int, int] = {}
        self._blocked_guild_ids: set[int] = set()

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
            journal_ready = bool(
                status.get("enabled") is True
                and status.get("state") == "ready"
            )
            self._owner_ready = False
            self._last_error_code = (
                ""
                if journal_ready
                else str(status.get("lastErrorCode") or "")
                or "conversation_ingress_recovery_unavailable"
            )
            if not journal_ready and status.get("enabled") is not True:
                return self.public_status()
        try:
            revoked_guild_ids = self.deps.active_guild_revocation_ids()
            if not journal_ready:
                if revoked_guild_ids:
                    raise ConversationIngressRecoveryError(
                        "conversation_ingress_guild_reset_recovery_failed"
                    )
                return self.public_status()
            for guild_id in revoked_guild_ids:
                result = self.deps.reset_session_continuity_guild(
                    guild_id,
                    partial(
                        self._purge_guild_before_owner_ready,
                        guild_id,
                    ),
                )
                if (
                    isinstance(result, dict)
                    and result.get("state") == "error"
                ):
                    raise ConversationIngressRecoveryError(
                        "conversation_ingress_guild_reset_recovery_failed"
                    )
        except Exception as exc:
            with self._lock:
                self._owner_ready = False
                self._last_error_code = (
                    "conversation_ingress_guild_reset_recovery_failed"
                )
            self.deps.log(
                "[CONVERSATION INGRESS] guild_reset_recovery_failed errorType=",
                type(exc).__name__,
            )
            raise ConversationIngressRecoveryError(
                "conversation_ingress_guild_reset_recovery_failed"
            ) from exc
        with self._lock:
            self._owner_ready = True
            self._last_error_code = ""
            self._reconcile_recovered_records()
            return self.public_status()

    def _guild_epoch_locked(self, guild_id: int) -> int:
        return int(self._guild_epochs.get(int(guild_id), 0))

    def guild_epoch(self, guild_id: int) -> int:
        with self._lock:
            self._require_guild_open_locked(guild_id)
            return self._guild_epoch_locked(guild_id)

    def guild_is_open(self, guild_id: int) -> bool:
        with self._lock:
            return (
                self._owner_ready
                and int(guild_id) not in self._blocked_guild_ids
            )

    def _require_guild_open_locked(self, guild_id: int) -> None:
        if int(guild_id) in self._blocked_guild_ids:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_guild_reset_inflight"
            )

    def _require_guild_epoch_locked(
        self,
        guild_id: int,
        expected_guild_epoch: int,
    ) -> None:
        if (
            isinstance(expected_guild_epoch, bool)
            or not isinstance(expected_guild_epoch, int)
            or self._guild_epoch_locked(guild_id)
            != expected_guild_epoch
        ):
            raise ConversationIngressRecoveryError(
                "conversation_ingress_epoch_not_current"
            )

    def _purge_guild_locked(self, guild_id: int) -> dict[str, Any]:
        assert self._journal is not None
        normalized_guild_id = int(guild_id)
        self._guild_epochs[normalized_guild_id] = (
            self._guild_epoch_locked(normalized_guild_id) + 1
        )
        try:
            receipt = self._journal.reset_guild(normalized_guild_id)
        except Exception as exc:
            raise RuntimeError(
                "conversation_ingress_guild_reset_failed"
            ) from exc
        self.deps.reset_guild_persistent_memory(
            normalized_guild_id
        )
        if self.deps.reset_guild_recovery_metadata is not None:
            try:
                self.deps.reset_guild_recovery_metadata(
                    normalized_guild_id
                )
            except Exception as exc:
                raise RuntimeError(
                    "search_followup_guild_reset_failed"
                ) from exc
        return receipt

    def _purge_guild_before_owner_ready(
        self,
        guild_id: int,
    ) -> dict[str, Any]:
        with self._lock:
            return self._purge_guild_locked(guild_id)

    def reset_guild(
        self,
        guild_id: int,
        reset_runtime_state: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            normalized_guild_id = int(guild_id)
            self._blocked_guild_ids.add(normalized_guild_id)
            self._ready_journal()
            if reset_runtime_state is not None:
                reset_runtime_state()
            return self._purge_guild_locked(normalized_guild_id)

    def complete_guild_reset(self, guild_id: int) -> None:
        with self._lock:
            self._blocked_guild_ids.discard(int(guild_id))

    def activate_guild_turn(
        self,
        guild_id: int,
        expected_guild_epoch: int,
        activation: Callable[[], Any],
    ) -> Any:
        with self._lock:
            self._ready_journal()
            self._require_guild_open_locked(guild_id)
            self._require_guild_epoch_locked(
                guild_id,
                expected_guild_epoch,
            )
            return activation()

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
                        self.deps.reconcile_delivery_succeeded(
                            record,
                            before_commit=lambda generation: (
                                self._journal.begin_terminal_commit(
                                    str(record["entryId"]),
                                    continuity_generation=int(generation),
                                    assistant_text=str(
                                        record["assistantText"]
                                    ),
                                    memory_receipt_ref=record[
                                        "memoryReceiptRef"
                                    ],
                                )
                            ),
                        )
                    )
                    if generation is None:
                        critical_unresolved = True
                        continue
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
        *,
        expected_guild_epoch: int | None = None,
    ) -> dict[str, Any]:
        if ingress.message_id is None or int(ingress.message_id) <= 0:
            raise ConversationIngressRecoveryError(
                "conversation_ingress_source_delivery_id_invalid"
            )
        with self._lock:
            self._require_guild_open_locked(ingress.guild_id)
            if expected_guild_epoch is not None:
                self._require_guild_epoch_locked(
                    ingress.guild_id,
                    expected_guild_epoch,
                )
            guild_epoch = self._guild_epoch_locked(ingress.guild_id)
            receipt = self.claim(
                surface="discord_text",
                scope=ingress.session_key,
                source_delivery_id=str(int(ingress.message_id)),
                accepted_text=accepted_text,
            )
            return {**receipt, "guildEpoch": guild_epoch}

    def claim_discord_command(
        self,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        scope: str,
        source_delivery_id: str,
        accepted_text: str,
    ) -> dict[str, Any]:
        return self._claim_discord_projection(
            guild_id=guild_id,
            expected_guild_epoch=expected_guild_epoch,
            scope=scope,
            source_delivery_id=source_delivery_id,
            accepted_text=accepted_text,
            block_other_ambiguous=False,
        )

    def claim_discord_autonomy(
        self,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        scope: str,
        source_delivery_id: str,
        accepted_text: str,
    ) -> dict[str, Any]:
        return self._claim_discord_projection(
            guild_id=guild_id,
            expected_guild_epoch=expected_guild_epoch,
            scope=scope,
            source_delivery_id=source_delivery_id,
            accepted_text=accepted_text,
            block_other_ambiguous=True,
        )

    def _claim_discord_projection(
        self,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        scope: str,
        source_delivery_id: str,
        accepted_text: str,
        block_other_ambiguous: bool,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_guild_open_locked(guild_id)
            self._require_guild_epoch_locked(
                guild_id,
                expected_guild_epoch,
            )
            if block_other_ambiguous and any(
                record.get("surface") == "discord_text"
                and record.get("scope") == scope
                and record.get("phase")
                in {"delivery_inflight", "delivery_ambiguous"}
                and record.get("sourceDeliveryId")
                != source_delivery_id
                for record in self._ready_journal().recovery_records()
            ):
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_reconciliation_required"
                )
            receipt = self.claim(
                surface="discord_text",
                scope=scope,
                source_delivery_id=source_delivery_id,
                accepted_text=accepted_text,
            )
            return {
                **receipt,
                "guildEpoch": self._guild_epoch_locked(guild_id),
            }

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

    def _mutate_claimed_guild_entry(
        self,
        guild_id: int,
        expected_guild_epoch: int,
        operation: Callable[
            [ConversationIngressRecoveryJournal],
            Any,
        ],
    ) -> Any:
        with self._lock:
            self._require_guild_open_locked(guild_id)
            self._require_guild_epoch_locked(
                guild_id,
                expected_guild_epoch,
            )
            return operation(self._ready_journal())

    def mark_response_ready(
        self,
        entry_id: str,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        assistant_text: str,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        return self._mutate_claimed_guild_entry(
            guild_id,
            expected_guild_epoch,
            lambda journal: journal.mark_response_ready(
                entry_id,
                assistant_text=assistant_text,
                memory_receipt_ref=memory_receipt_ref,
            ),
        )

    def bind_response(
        self,
        entry_id: str,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        assistant_text: str,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        return self._mutate_claimed_guild_entry(
            guild_id,
            expected_guild_epoch,
            lambda journal: journal.bind_response(
                entry_id,
                assistant_text=assistant_text,
                memory_receipt_ref=memory_receipt_ref,
            ),
        )

    def mark_stream_delivery_inflight(
        self,
        entry_id: str,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        delivery_ref: str,
    ) -> dict[str, Any]:
        return self._mutate_claimed_guild_entry(
            guild_id,
            expected_guild_epoch,
            lambda journal: journal.mark_stream_delivery_inflight(
                entry_id,
                delivery_ref=delivery_ref,
            ),
        )

    def mark_delivery_inflight(
        self,
        entry_id: str,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        delivery_ref: str,
    ) -> dict[str, Any]:
        return self._mutate_claimed_guild_entry(
            guild_id,
            expected_guild_epoch,
            lambda journal: journal.mark_delivery_inflight(
                entry_id,
                delivery_ref=delivery_ref,
            ),
        )

    def mark_delivery_succeeded(
        self,
        entry_id: str,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        delivery_ref: str,
    ) -> dict[str, Any]:
        return self._mutate_claimed_guild_entry(
            guild_id,
            expected_guild_epoch,
            lambda journal: journal.mark_delivery_succeeded(
                entry_id,
                delivery_ref=delivery_ref,
            ),
        )

    def mark_delivery_ambiguous(
        self,
        entry_id: str,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        error_code: str = (
            "conversation_ingress_delivery_ambiguous"
        ),
    ) -> dict[str, Any]:
        return self._mutate_claimed_guild_entry(
            guild_id,
            expected_guild_epoch,
            lambda journal: journal.mark_delivery_ambiguous(
                entry_id,
                error_code=error_code,
            ),
        )

    def discard_ambiguous(
        self,
        entry_id: str,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        assistant_hash: str,
        delivery_ref: str,
        error_code: str,
    ) -> None:
        self._mutate_claimed_guild_entry(
            guild_id,
            expected_guild_epoch,
            lambda journal: journal.discard_ambiguous(
                entry_id,
                assistant_hash=assistant_hash,
                delivery_ref=delivery_ref,
                error_code=error_code,
            ),
        )

    def begin_terminal_commit(
        self,
        entry_id: str,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        continuity_generation: int,
        assistant_text: str,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        return self._mutate_claimed_guild_entry(
            guild_id,
            expected_guild_epoch,
            lambda journal: journal.begin_terminal_commit(
                entry_id,
                continuity_generation=continuity_generation,
                assistant_text=assistant_text,
                memory_receipt_ref=memory_receipt_ref,
            ),
        )

    def complete(
        self,
        entry_id: str,
        *,
        guild_id: int,
        expected_guild_epoch: int,
        continuity_generation: int,
        assistant_text: str,
        memory_receipt_ref: Any,
    ) -> dict[str, Any]:
        return self._mutate_claimed_guild_entry(
            guild_id,
            expected_guild_epoch,
            lambda journal: journal.complete(
                entry_id,
                continuity_generation=continuity_generation,
                assistant_text=assistant_text,
                memory_receipt_ref=memory_receipt_ref,
            ),
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

    def _exact_lineage_operation(
        self,
        operation_name: str,
        *,
        match_turn: Callable[[str], bool],
        match_session: Callable[[str], bool],
        full_user_delete: bool,
    ) -> dict[str, Any]:
        with self._lock:
            if not self._owner_ready or self._journal is None:
                return _manual_exact_lineage_counts()
            try:
                status = self._journal.public_status()
                if (
                    not isinstance(status, dict)
                    or status.get("enabled") is not True
                    or status.get("state") != "ready"
                    or status.get("rollbackProtected") is not True
                ):
                    return _manual_exact_lineage_counts()
                operation = getattr(self._journal, operation_name)
                if not callable(operation):
                    return _manual_exact_lineage_counts()
                result = operation(
                    match_turn=match_turn,
                    match_session=match_session,
                    full_user_delete=full_user_delete,
                )
                if (
                    not isinstance(result, dict)
                    or frozenset(result)
                    != {
                        "removedCount",
                        "remainingCopies",
                        "manualReviewCount",
                        "contentFree",
                    }
                    or result.get("contentFree") is not True
                    or any(
                        type(result.get(key)) is not int
                        or int(result[key]) < 0
                        for key in (
                            "removedCount",
                            "remainingCopies",
                            "manualReviewCount",
                        )
                    )
                ):
                    return _manual_exact_lineage_counts()
                return dict(result)
            except Exception:
                return _manual_exact_lineage_counts()

    def purge_exact_lineage(
        self,
        *,
        match_turn: Callable[[str], bool],
        match_session: Callable[[str], bool],
        full_user_delete: bool,
    ) -> dict[str, Any]:
        return self._exact_lineage_operation(
            "purge_exact_lineage",
            match_turn=match_turn,
            match_session=match_session,
            full_user_delete=full_user_delete,
        )

    def negative_recall_exact_lineage(
        self,
        *,
        match_turn: Callable[[str], bool],
        match_session: Callable[[str], bool],
        full_user_delete: bool,
    ) -> dict[str, Any]:
        return self._exact_lineage_operation(
            "negative_recall_exact_lineage",
            match_turn=match_turn,
            match_session=match_session,
            full_user_delete=full_user_delete,
        )

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
    reset_guild_recovery_metadata: Callable[[int], Any],
    mutation_target_is_current: Callable[..., bool] | None = None,
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
                artifact_process=getattr(
                    session_continuity_checkpoint,
                    "artifact_process",
                    None,
                ),
                artifact_deadline_sec=getattr(
                    session_continuity_checkpoint,
                    "commit_artifact_deadline_sec",
                    5.0,
                ),
                mutation_target_is_current=mutation_target_is_current,
            ),
            log=log,
            active_guild_revocation_ids=(
                session_continuity_checkpoint.active_guild_revocation_ids
            ),
            reset_session_continuity_guild=(
                session_continuity_checkpoint.reset_guild
            ),
            reset_guild_persistent_memory=(
                reset_guild_memory_vault
            ),
            reset_guild_recovery_metadata=(
                reset_guild_recovery_metadata
            ),
            reconcile_delivery_succeeded=partial(
                reconcile_recovered_delivery_succeeded,
                deps=restart_deps,
            ),
            verify_terminal_commit=partial(
                reconcile_recovered_terminal_commit,
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
