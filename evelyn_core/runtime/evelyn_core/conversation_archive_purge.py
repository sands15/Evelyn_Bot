from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping

from .conversation_archive import (
    ARCHIVE_REQUIRED_PURGE_SINKS,
    ConversationArchive,
    DeletionPurgeWorkOrder,
)
from .memory_deletion_journal import (
    MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
    append_memory_deletion_tombstone,
    memory_deletion_journal_position,
    memory_deletion_ledger_note_id,
    read_memory_deletion_tombstones,
)


PURGE_RUN_SCHEMA = "evelyn.private-conversation-archive.purge-run.v1"
PURGE_SINK_STATUS_SCHEMA = (
    "evelyn.private-conversation-archive.purge-sink-status.v1"
)
_WORK_ORDER_DOMAIN = b"evelyn.private-conversation-archive.purge-scope.v1\n"


class ConversationArchivePurgeError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DeletionLateCommitRejected(ConversationArchivePurgeError):
    pass


@dataclass(frozen=True)
class PurgePass:
    removed_count: int = 0
    remaining_copies: int = 0
    manual_review_count: int = 0

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (
                self.removed_count,
                self.remaining_copies,
                self.manual_review_count,
            )
        ):
            raise ConversationArchivePurgeError(
                "archive_purge_owner_result_invalid"
            )


@dataclass(frozen=True)
class LocalPurgeOwner:
    sink: str
    purge: Callable[[DeletionPurgeWorkOrder], PurgePass]
    negative_recall: Callable[[DeletionPurgeWorkOrder], PurgePass]

    def __post_init__(self) -> None:
        if (
            self.sink not in ARCHIVE_REQUIRED_PURGE_SINKS
            or not callable(self.purge)
            or not callable(self.negative_recall)
        ):
            raise ConversationArchivePurgeError(
                "archive_purge_owner_invalid"
            )


@dataclass(frozen=True)
class PurgeSinkStatus:
    sink: str
    state: str
    removed_count: int
    remaining_copies: int
    manual_review_count: int
    content_free: bool = True
    schema: str = PURGE_SINK_STATUS_SCHEMA


@dataclass(frozen=True)
class PurgeRunResult:
    request_id: str
    deletion_generation: int
    state: str
    archive_completed: bool
    sinks: tuple[PurgeSinkStatus, ...]
    receipts: tuple[Mapping[str, object], ...]
    content_free: bool = True
    schema: str = PURGE_RUN_SCHEMA


@dataclass(frozen=True)
class DeletionLateCommitFence:
    target_epochs: tuple[tuple[str, int], ...]


def _work_order_payload(work_order: DeletionPurgeWorkOrder) -> dict[str, object]:
    return {
        "requestId": work_order.request_id,
        "reason": work_order.reason,
        "requestedAt": work_order.requested_at.astimezone(
            timezone.utc
        ).isoformat(),
        "deletionGeneration": work_order.deletion_generation,
        "principalId": work_order.principal_id,
        "principalIds": list(work_order.principal_ids),
        "principalLookupDigests": list(
            work_order.principal_lookup_digests
        ),
        "lineageHandles": [
            {"kind": kind, "digest": digest}
            for kind, digest in work_order.lineage_handles
        ],
        "lineageComplete": work_order.lineage_complete,
        "ownedRecordIds": list(work_order.owned_record_ids),
        "dependentRecordIds": list(work_order.dependent_record_ids),
        "intervalIds": list(work_order.interval_ids),
        "scopeAll": work_order.scope_all,
        "guildId": work_order.guild_id,
        "startedAt": (
            None
            if work_order.started_at is None
            else work_order.started_at.astimezone(timezone.utc).isoformat()
        ),
        "endedAt": (
            None
            if work_order.ended_at is None
            else work_order.ended_at.astimezone(timezone.utc).isoformat()
        ),
        "requiredSinks": list(work_order.required_sinks),
    }


def deletion_purge_scope_digest(work_order: DeletionPurgeWorkOrder) -> str:
    if not isinstance(work_order, DeletionPurgeWorkOrder):
        raise ConversationArchivePurgeError(
            "archive_purge_work_order_invalid"
        )
    encoded = json.dumps(
        _work_order_payload(work_order),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(_WORK_ORDER_DOMAIN + encoded).hexdigest()


def _target_keys(work_order: DeletionPurgeWorkOrder) -> tuple[str, ...]:
    keys = [
        *(f"record:{value}" for value in work_order.owned_record_ids),
        *(f"record:{value}" for value in work_order.dependent_record_ids),
        *(f"interval:{value}" for value in work_order.interval_ids),
    ]
    principals = set(work_order.principal_ids)
    if work_order.principal_id is not None:
        principals.add(work_order.principal_id)
    keys.extend(f"principal:{value}" for value in principals)
    keys.extend(
        f"lookup:{value}"
        for value in work_order.principal_lookup_digests
    )
    keys.extend(
        f"lineage:{kind}:{digest}"
        for kind, digest in work_order.lineage_handles
    )
    if not keys:
        raise ConversationArchivePurgeError(
            "archive_purge_work_order_invalid"
        )
    return tuple(sorted(set(keys)))


def _capture_keys(
    *,
    principal_id: str | None,
    principal_ids: Iterable[str],
    principal_lookup_digests: Iterable[str],
    record_ids: Iterable[str],
    interval_ids: Iterable[str],
    lineage_handles: Iterable[tuple[str, str]] = (),
) -> tuple[str, ...]:
    if isinstance(principal_ids, (str, bytes)) or isinstance(
        record_ids, (str, bytes)
    ) or isinstance(principal_lookup_digests, (str, bytes)) or isinstance(
        interval_ids, (str, bytes)
    ):
        raise ConversationArchivePurgeError("archive_purge_scope_invalid")
    principal_values = tuple(principal_ids)
    lookup_values = tuple(principal_lookup_digests)
    record_values = tuple(record_ids)
    interval_values = tuple(interval_ids)
    lineage_values = tuple(lineage_handles)
    values = [
        *principal_values,
        *lookup_values,
        *record_values,
        *interval_values,
    ]
    if principal_id is not None:
        values.append(principal_id)
    if any(
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
        for value in values
    ):
        raise ConversationArchivePurgeError("archive_purge_scope_invalid")
    keys = [
        *(f"record:{value}" for value in record_values),
        *(f"interval:{value}" for value in interval_values),
        *(f"lookup:{value}" for value in lookup_values),
    ]
    if principal_id is not None:
        principal_values = (*principal_values, principal_id)
    keys.extend(f"principal:{value}" for value in principal_values)
    for item in lineage_values:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or item[0] not in {
                "turn",
                "session",
                "memory_owner",
                "memory_note",
                "memory_evidence",
            }
            or len(item[1]) != 64
            or any(character not in "0123456789abcdef" for character in item[1])
        ):
            raise ConversationArchivePurgeError(
                "archive_purge_scope_invalid"
            )
        keys.append(f"lineage:{item[0]}:{item[1]}")
    if not keys:
        raise ConversationArchivePurgeError("archive_purge_scope_invalid")
    return tuple(sorted(set(keys)))


class ConversationArchivePurgeCoordinator:
    """Freeze exact targets, run real owners, and submit only verified receipts."""

    def __init__(
        self,
        *,
        owners: Iterable[LocalPurgeOwner] = (),
        memory_deletion_index_dir: Path | None = None,
    ) -> None:
        owner_items = tuple(owners)
        owner_map = {owner.sink: owner for owner in owner_items}
        if len(owner_map) != len(owner_items):
            raise ConversationArchivePurgeError(
                "archive_purge_owner_duplicate"
            )
        if "memory_deletion_journal" in owner_map:
            raise ConversationArchivePurgeError(
                "archive_purge_owner_reserved"
            )
        self._owners = owner_map
        self._memory_index_dir = (
            None
            if memory_deletion_index_dir is None
            else Path(memory_deletion_index_dir)
        )
        self._lock = threading.RLock()
        self._epochs: dict[str, int] = {}
        self._frozen_targets: dict[str, set[str]] = {}
        self._frozen_requests: dict[str, str] = {}
        self._retired_targets: set[str] = set()

    @property
    def registered_sinks(self) -> tuple[str, ...]:
        sinks = set(self._owners)
        if self._memory_index_dir is not None:
            sinks.add("memory_deletion_journal")
        return tuple(sorted(sinks))

    @staticmethod
    def _validate_work_order(work_order: DeletionPurgeWorkOrder) -> str:
        digest = deletion_purge_scope_digest(work_order)
        if (
            not work_order.request_id
            or len(work_order.request_id) > 64
            or type(work_order.deletion_generation) is not int
            or work_order.deletion_generation < 1
            or tuple(work_order.required_sinks)
            != tuple(sorted(set(work_order.required_sinks)))
            or any(
                sink not in ARCHIVE_REQUIRED_PURGE_SINKS
                for sink in work_order.required_sinks
            )
            or (
                work_order.principal_id is not None
                and work_order.principal_id not in work_order.principal_ids
            )
            or any(
                len(value) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in value
                )
                for value in work_order.principal_lookup_digests
            )
            or tuple(work_order.lineage_handles)
            != tuple(sorted(set(work_order.lineage_handles)))
            or type(work_order.lineage_complete) is not bool
            or any(
                kind not in {
                    "turn",
                    "session",
                    "memory_owner",
                    "memory_note",
                    "memory_evidence",
                }
                or len(digest) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in digest
                )
                for kind, digest in work_order.lineage_handles
            )
        ):
            raise ConversationArchivePurgeError(
                "archive_purge_work_order_invalid"
            )
        _target_keys(work_order)
        return digest

    def _append_memory_fence(
        self,
        work_order: DeletionPurgeWorkOrder,
    ) -> None:
        if "memory_deletion_journal" not in work_order.required_sinks:
            return
        if self._memory_index_dir is None:
            return
        raw_marker_id = (
            "conversation-archive-purge:"
            f"{work_order.request_id}:{work_order.deletion_generation}"
        )
        marker_id = memory_deletion_ledger_note_id(raw_marker_id)
        existing = read_memory_deletion_tombstones(self._memory_index_dir)
        if any(row.get("noteId") == marker_id for row in existing):
            return
        append_memory_deletion_tombstone(
            self._memory_index_dir,
            {
                "schema": MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                "noteId": raw_marker_id,
                "noteType": "internal",
                "sourceType": "runtime",
                "reason": (
                    "obsolete_memory"
                    if work_order.reason == "retention_expired"
                    else "privacy_request"
                ),
                "deletedAt": work_order.requested_at.astimezone(
                    timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )

    def freeze(self, work_order: DeletionPurgeWorkOrder) -> None:
        """Advance the deletion fence before the archive removes source rows."""

        digest = self._validate_work_order(work_order)
        targets = _target_keys(work_order)
        with self._lock:
            prior = self._frozen_requests.get(work_order.request_id)
            if prior is not None:
                if prior != digest:
                    raise ConversationArchivePurgeError(
                        "archive_purge_work_order_changed"
                    )
                return
            try:
                self._append_memory_fence(work_order)
            except Exception:
                raise ConversationArchivePurgeError(
                    "archive_purge_memory_fence_failed"
                ) from None
            for target in targets:
                self._epochs[target] = self._epochs.get(target, 0) + 1
                self._frozen_targets.setdefault(target, set()).add(
                    work_order.request_id
                )
            self._frozen_requests[work_order.request_id] = digest

    def restore_pending_fences(
        self,
        work_orders: Iterable[DeletionPurgeWorkOrder],
    ) -> None:
        for work_order in work_orders:
            self.freeze(work_order)

    def capture_late_commit_fence(
        self,
        *,
        principal_id: str | None = None,
        principal_ids: Iterable[str] = (),
        principal_lookup_digests: Iterable[str] = (),
        record_ids: Iterable[str] = (),
        interval_ids: Iterable[str] = (),
        lineage_handles: Iterable[tuple[str, str]] = (),
    ) -> DeletionLateCommitFence:
        targets = _capture_keys(
            principal_id=principal_id,
            principal_ids=principal_ids,
            principal_lookup_digests=principal_lookup_digests,
            record_ids=record_ids,
            interval_ids=interval_ids,
            lineage_handles=lineage_handles,
        )
        with self._lock:
            if any(
                self._frozen_targets.get(target)
                or target in self._retired_targets
                for target in targets
            ):
                raise DeletionLateCommitRejected(
                    "archive_late_commit_rejected"
                )
            return DeletionLateCommitFence(
                target_epochs=tuple(
                    (target, self._epochs.get(target, 0))
                    for target in targets
                )
            )

    def assert_late_commit_current(
        self,
        fence: DeletionLateCommitFence,
    ) -> None:
        if not isinstance(fence, DeletionLateCommitFence):
            raise DeletionLateCommitRejected(
                "archive_late_commit_rejected"
            )
        with self._lock:
            if not fence.target_epochs or any(
                self._epochs.get(target, 0) != epoch
                or bool(self._frozen_targets.get(target))
                or target in self._retired_targets
                for target, epoch in fence.target_epochs
            ):
                raise DeletionLateCommitRejected(
                    "archive_late_commit_rejected"
                )

    def purge_fence_current(
        self,
        work_order: DeletionPurgeWorkOrder,
    ) -> bool:
        """Return true only while this exact deletion scope remains frozen.

        Purge owners use this before and after rewriting a derived store.  It
        is deliberately narrower than a general health check: a changed work
        order, a released request, or even one missing target rejects the pass.
        """

        try:
            digest = deletion_purge_scope_digest(work_order)
            targets = _target_keys(work_order)
        except Exception:
            return False
        with self._lock:
            if self._frozen_requests.get(work_order.request_id) != digest:
                return False
            return all(
                work_order.request_id
                in self._frozen_targets.get(target, set())
                for target in targets
            )

    @contextmanager
    def late_commit_guard(
        self,
        fence: DeletionLateCommitFence,
    ) -> Iterator[None]:
        self.assert_late_commit_current(fence)
        yield
        self.assert_late_commit_current(fence)

    def _release(self, work_order: DeletionPurgeWorkOrder) -> None:
        targets = _target_keys(work_order)
        with self._lock:
            expected = self._frozen_requests.get(work_order.request_id)
            if expected != deletion_purge_scope_digest(work_order):
                return
            for target in targets:
                requests = self._frozen_targets.get(target)
                if requests is None:
                    continue
                requests.discard(work_order.request_id)
                if not requests:
                    self._frozen_targets.pop(target, None)
                if not target.startswith(("principal:", "lookup:")):
                    self._retired_targets.add(target)
            self._frozen_requests.pop(work_order.request_id, None)

    def _journal_status(
        self,
        work_order: DeletionPurgeWorkOrder,
    ) -> PurgeSinkStatus:
        if self._memory_index_dir is None:
            return PurgeSinkStatus(
                sink="memory_deletion_journal",
                state="manual_review",
                removed_count=0,
                remaining_copies=0,
                manual_review_count=1,
            )
        try:
            marker_id = memory_deletion_ledger_note_id(
                "conversation-archive-purge:"
                f"{work_order.request_id}:{work_order.deletion_generation}"
            )
            rows = read_memory_deletion_tombstones(self._memory_index_dir)
            memory_deletion_journal_position(self._memory_index_dir)
            present = sum(row.get("noteId") == marker_id for row in rows)
        except Exception:
            return PurgeSinkStatus(
                sink="memory_deletion_journal",
                state="manual_review",
                removed_count=0,
                remaining_copies=0,
                manual_review_count=1,
            )
        complete = present == 1
        return PurgeSinkStatus(
            sink="memory_deletion_journal",
            state="purged" if complete else "cleanup_pending",
            removed_count=0,
            remaining_copies=0 if complete else 1,
            manual_review_count=0,
        )

    @staticmethod
    def _owner_status(
        owner: LocalPurgeOwner,
        work_order: DeletionPurgeWorkOrder,
    ) -> PurgeSinkStatus:
        try:
            purged = owner.purge(work_order)
            recalled = owner.negative_recall(work_order)
            if not isinstance(purged, PurgePass) or not isinstance(
                recalled, PurgePass
            ):
                raise ConversationArchivePurgeError(
                    "archive_purge_owner_result_invalid"
                )
        except Exception:
            return PurgeSinkStatus(
                sink=owner.sink,
                state="manual_review",
                removed_count=0,
                remaining_copies=0,
                manual_review_count=1,
            )
        remaining = max(
            purged.remaining_copies,
            recalled.remaining_copies,
        )
        manual = max(
            purged.manual_review_count,
            recalled.manual_review_count,
        )
        state = (
            "purged"
            if remaining == 0 and manual == 0
            else "manual_review"
            if manual
            else "cleanup_pending"
        )
        return PurgeSinkStatus(
            sink=owner.sink,
            state=state,
            removed_count=purged.removed_count,
            remaining_copies=remaining,
            manual_review_count=manual,
        )

    def purge_work_order(
        self,
        archive: ConversationArchive,
        work_order: DeletionPurgeWorkOrder,
    ) -> PurgeRunResult:
        digest = self._validate_work_order(work_order)
        current = archive.deletion_purge_work_order(
            request_id=work_order.request_id
        )
        if current is None or deletion_purge_scope_digest(current) != digest:
            raise ConversationArchivePurgeError(
                "archive_purge_work_order_stale"
            )
        self.freeze(work_order)
        statuses: list[PurgeSinkStatus] = []
        for sink in work_order.required_sinks:
            current = archive.deletion_purge_work_order(
                request_id=work_order.request_id
            )
            if current is None or deletion_purge_scope_digest(current) != digest:
                raise ConversationArchivePurgeError(
                    "archive_purge_work_order_stale"
                )
            if sink == "memory_deletion_journal":
                statuses.append(self._journal_status(work_order))
                continue
            owner = self._owners.get(sink)
            if owner is None:
                statuses.append(
                    PurgeSinkStatus(
                        sink=sink,
                        state="manual_review",
                        removed_count=0,
                        remaining_copies=0,
                        manual_review_count=1,
                    )
                )
                continue
            statuses.append(self._owner_status(owner, work_order))
            current = archive.deletion_purge_work_order(
                request_id=work_order.request_id
            )
            if current is None or deletion_purge_scope_digest(current) != digest:
                raise ConversationArchivePurgeError(
                    "archive_purge_work_order_stale"
                )

        receipts = tuple(
            {
                "sink": status.sink,
                "deletionGeneration": work_order.deletion_generation,
                "contentFree": True,
                "complete": True,
                "remainingCopies": 0,
                "manualReviewCount": 0,
            }
            for status in statuses
            if status.state == "purged"
        )
        archive_completed = False
        if len(receipts) == len(work_order.required_sinks):
            try:
                archive_completed = archive.submit_purge_receipts(
                    request_id=work_order.request_id,
                    receipts=receipts,
                )
            except Exception:
                archive_completed = False
        if archive_completed:
            self._release(work_order)
        state = (
            "local_fully_purged"
            if archive_completed
            else "manual_review"
            if any(status.manual_review_count for status in statuses)
            else "local_cleanup_pending"
        )
        return PurgeRunResult(
            request_id=work_order.request_id,
            deletion_generation=work_order.deletion_generation,
            state=state,
            archive_completed=archive_completed,
            sinks=tuple(statuses),
            receipts=receipts,
        )

    def purge_pending(
        self,
        archive: ConversationArchive,
        *,
        limit: int = 100,
    ) -> tuple[PurgeRunResult, ...]:
        return tuple(
            self.purge_work_order(archive, work_order)
            for work_order in archive.pending_purge_work_orders(limit=limit)
        )


def voice_debug_audio_purge_owner(
    root: Path | None,
    *,
    resolve_turn_ids: Callable[
        [DeletionPurgeWorkOrder], Iterable[str] | None
    ] | None = None,
) -> LocalPurgeOwner:
    """Build the real debug-audio owner without guessing record/turn lineage."""

    resolved_root = None if root is None else Path(root)
    if resolve_turn_ids is not None and not callable(resolve_turn_ids):
        raise ConversationArchivePurgeError(
            "archive_purge_owner_invalid"
        )

    def resolve(work_order: DeletionPurgeWorkOrder) -> Iterable[str] | None:
        if resolve_turn_ids is not None:
            return resolve_turn_ids(work_order)
        if resolved_root is None:
            return None
        if resolved_root.is_symlink():
            return None
        if not resolved_root.exists():
            return ()
        if not resolved_root.is_dir():
            return None
        try:
            guild_dirs = tuple(resolved_root.iterdir())
        except OSError:
            return None
        for guild_dir in guild_dirs:
            if guild_dir.is_symlink() or not guild_dir.is_dir():
                return None
            try:
                if next(guild_dir.iterdir(), None) is not None:
                    return None
            except OSError:
                return None
        return ()

    def run(work_order: DeletionPurgeWorkOrder) -> PurgePass:
        from .voice_debug_audio import purge_voice_debug_audio_for_turns

        turn_ids = resolve(work_order)
        if (
            resolved_root is None
            or turn_ids is None
            or isinstance(turn_ids, (str, bytes))
        ):
            return PurgePass(manual_review_count=1)
        normalized = tuple(turn_ids)
        guild_id = (
            int(work_order.guild_id)
            if work_order.guild_id is not None
            and work_order.guild_id.isdecimal()
            else None
        )
        result = purge_voice_debug_audio_for_turns(
            resolved_root,
            deletion_generation=work_order.deletion_generation,
            turn_ids=normalized,
            guild_id=guild_id,
        )
        failed = int(result.get("failedCount") or 0)
        unresolved = int(result.get("unresolvedCount") or 0)
        matched = int(result.get("matchedCount") or 0)
        deleted = int(result.get("deletedCount") or 0)
        return PurgePass(
            removed_count=deleted,
            remaining_copies=max(failed, matched - deleted),
            manual_review_count=1 if unresolved else 0,
        )

    return LocalPurgeOwner(
        sink="voice_debug_audio",
        purge=run,
        negative_recall=run,
    )


__all__ = [
    "ConversationArchivePurgeCoordinator",
    "ConversationArchivePurgeError",
    "DeletionLateCommitFence",
    "DeletionLateCommitRejected",
    "LocalPurgeOwner",
    "PURGE_RUN_SCHEMA",
    "PURGE_SINK_STATUS_SCHEMA",
    "PurgePass",
    "PurgeRunResult",
    "PurgeSinkStatus",
    "deletion_purge_scope_digest",
    "voice_debug_audio_purge_owner",
]
