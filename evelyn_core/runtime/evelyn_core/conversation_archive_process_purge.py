from __future__ import annotations

import inspect
import threading
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any


_LINEAGE_KINDS = frozenset(
    {"turn", "session", "memory_owner", "memory_note", "memory_evidence"}
)
_TARGET_FIELDS = (
    "guild_id",
    "turn_id",
    "session_key",
    "session_memory_key",
    "person_key",
)


class ConversationArchiveProcessPurgeError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def conversation_archive_process_target_values(
    target: Mapping[str, Any],
) -> dict[str, Any]:
    """Read exact task lineage, including a Discord member's owned guild."""

    if not isinstance(target, Mapping):
        return {}
    nested = target.get("target")
    values = dict(nested) if isinstance(nested, Mapping) else {}
    for key in _TARGET_FIELDS:
        if values.get(key) in (None, "") and target.get(key) not in (None, ""):
            values[key] = target.get(key)
    if values.get("guild_id") in (None, ""):
        member = target.get("member")
        guild_id = getattr(getattr(member, "guild", None), "id", None)
        if guild_id is not None:
            values["guild_id"] = guild_id
    return {key: values.get(key) for key in _TARGET_FIELDS}


def purge_exact_process_caches(
    *,
    session_caches: Iterable[MutableMapping[str, Any]],
    targeted_cache: MutableMapping[str, Any],
    target_metadata: MutableMapping[str, dict[str, Any]],
    session_matches: Callable[[str], bool],
    target_matches: Callable[[dict[str, Any]], bool],
    unattributed_session_keys: Iterable[str] = (),
) -> tuple[int, int, int]:
    """Remove exact session/target cache entries and flag unattributed copies."""

    if not callable(session_matches) or not callable(target_matches):
        return (0, 1, 1)
    caches = tuple(session_caches)
    unattributed = frozenset(unattributed_session_keys)
    if any(not isinstance(cache, MutableMapping) for cache in caches) or not isinstance(
        targeted_cache, MutableMapping
    ) or not isinstance(target_metadata, MutableMapping) or any(
        not isinstance(key, str) or not key for key in unattributed
    ):
        return (0, 1, 1)
    removed = 0
    manual = 0
    try:
        for cache in caches:
            for key in tuple(cache):
                if not isinstance(key, str) or not key:
                    manual += 1
                    continue
                if session_matches(key) is True:
                    cache.pop(key, None)
                    removed += 1
        for key in tuple(targeted_cache):
            target = target_metadata.get(key)
            if not isinstance(key, str) or not isinstance(target, dict) or not target:
                manual += 1
                continue
            if target_matches(dict(target)) is True:
                targeted_cache.pop(key, None)
                target_metadata.pop(key, None)
                removed += 1
        for key in tuple(target_metadata):
            if key not in targeted_cache:
                target_metadata.pop(key, None)
        manual += sum(key in cache for cache in caches for key in unattributed)
        remaining = sum(
            session_matches(key) is True
            for cache in caches
            for key in cache
            if isinstance(key, str) and key
        )
        remaining += sum(
            isinstance(target := target_metadata.get(key), dict)
            and bool(target)
            and target_matches(dict(target)) is True
            for key in targeted_cache
        )
    except Exception:
        return (removed, 1, manual + 1)
    return (removed, remaining, manual)


@dataclass(frozen=True, slots=True)
class ProcessPurgeFenceSnapshot:
    frozen_requests: int
    frozen_handles: int
    retired_handles: int
    content_free: bool = True


class ConversationArchiveProcessPurgeFence:
    """Block owner-local writers by the archive's opaque lineage handles.

    The archive sends no raw actor, turn, session, or text.  This process uses
    its purpose-limited lineage key to map identifiers it already owns onto
    those handles.  A successfully purged handle remains retired until this
    process exits; tasks do not survive that restart and durable retry stores
    must be empty before the purge receipt is acknowledged.
    """

    def __init__(
        self,
        *,
        lineage_handle: Callable[[str, str], str],
    ) -> None:
        if not callable(lineage_handle):
            raise ConversationArchiveProcessPurgeError(
                "archive_process_purge_fence_invalid"
            )
        self._lineage_handle = lineage_handle
        self._lock = threading.RLock()
        self._frozen: dict[
            str,
            tuple[str, frozenset[tuple[str, str]]],
        ] = {}
        self._retired: set[tuple[str, str]] = set()

    @staticmethod
    def _work_identity(work_order: Mapping[str, Any]) -> tuple[str, str]:
        if not isinstance(work_order, Mapping):
            raise ConversationArchiveProcessPurgeError(
                "archive_process_purge_work_invalid"
            )
        request_id = work_order.get("requestId")
        scope_digest = work_order.get("scopeDigest")
        if (
            not isinstance(request_id, str)
            or not request_id
            or len(request_id) > 64
            or not isinstance(scope_digest, str)
            or len(scope_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in scope_digest
            )
        ):
            raise ConversationArchiveProcessPurgeError(
                "archive_process_purge_work_invalid"
            )
        return request_id, scope_digest

    @staticmethod
    def _work_handles(
        work_order: Mapping[str, Any],
    ) -> frozenset[tuple[str, str]]:
        raw_handles = work_order.get("lineageHandles")
        if not isinstance(raw_handles, list) or len(raw_handles) > 96:
            raise ConversationArchiveProcessPurgeError(
                "archive_process_purge_work_invalid"
            )
        handles: list[tuple[str, str]] = []
        for item in raw_handles:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"kind", "digest"}
                or item.get("kind") not in _LINEAGE_KINDS
                or not isinstance(item.get("digest"), str)
                or len(item["digest"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in item["digest"]
                )
            ):
                raise ConversationArchiveProcessPurgeError(
                    "archive_process_purge_work_invalid"
                )
            handles.append((str(item["kind"]), str(item["digest"])))
        normalized = frozenset(handles)
        if len(normalized) != len(handles):
            raise ConversationArchiveProcessPurgeError(
                "archive_process_purge_work_invalid"
            )
        return normalized

    def freeze(self, work_order: Mapping[str, Any]) -> None:
        request_id, scope_digest = self._work_identity(work_order)
        handles = self._work_handles(work_order)
        with self._lock:
            existing = self._frozen.get(request_id)
            if existing is not None and existing != (scope_digest, handles):
                raise ConversationArchiveProcessPurgeError(
                    "archive_process_purge_work_changed"
                )
            self._frozen[request_id] = (scope_digest, handles)

    def retire(self, work_order: Mapping[str, Any]) -> None:
        request_id, scope_digest = self._work_identity(work_order)
        handles = self._work_handles(work_order)
        with self._lock:
            existing = self._frozen.get(request_id)
            if existing != (scope_digest, handles):
                raise ConversationArchiveProcessPurgeError(
                    "archive_process_purge_work_stale"
                )
            self._retired.update(handles)
            self._frozen.pop(request_id, None)

    def release_completed(self, work_order: Mapping[str, Any]) -> None:
        """Retire one exact fence after the archive confirms completion.

        Completion removes the in-flight request fence but must never make the
        deleted lineage current again during this process lifetime.
        """

        self.retire(work_order)

    def raw_handles(
        self,
        lineage: Mapping[str, Iterable[str]],
    ) -> frozenset[tuple[str, str]]:
        if not isinstance(lineage, Mapping):
            raise ConversationArchiveProcessPurgeError(
                "archive_process_purge_lineage_invalid"
            )
        handles: set[tuple[str, str]] = set()
        for kind, values in lineage.items():
            if kind not in _LINEAGE_KINDS or isinstance(values, (str, bytes)):
                raise ConversationArchiveProcessPurgeError(
                    "archive_process_purge_lineage_invalid"
                )
            try:
                raw_values = tuple(values)
            except TypeError:
                raise ConversationArchiveProcessPurgeError(
                    "archive_process_purge_lineage_invalid"
                ) from None
            if len(raw_values) > 32:
                raise ConversationArchiveProcessPurgeError(
                    "archive_process_purge_lineage_invalid"
                )
            for raw_value in raw_values:
                if not isinstance(raw_value, str) or not raw_value:
                    raise ConversationArchiveProcessPurgeError(
                        "archive_process_purge_lineage_invalid"
                    )
                try:
                    digest = self._lineage_handle(kind, raw_value)
                except Exception:
                    raise ConversationArchiveProcessPurgeError(
                        "archive_process_purge_lineage_invalid"
                    ) from None
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in digest
                    )
                ):
                    raise ConversationArchiveProcessPurgeError(
                        "archive_process_purge_lineage_invalid"
                    )
                handles.add((kind, digest))
        if not handles or len(handles) > 96:
            raise ConversationArchiveProcessPurgeError(
                "archive_process_purge_lineage_invalid"
            )
        return frozenset(handles)

    def matches(
        self,
        work_order: Mapping[str, Any],
        lineage: Mapping[str, Iterable[str]],
    ) -> bool:
        work_handles = self._work_handles(work_order)
        try:
            candidate_handles = self.raw_handles(lineage)
        except ConversationArchiveProcessPurgeError:
            return False
        return bool(work_handles.intersection(candidate_handles))

    def target_is_current(
        self,
        lineage: Mapping[str, Iterable[str]],
    ) -> bool:
        try:
            candidate_handles = self.raw_handles(lineage)
        except ConversationArchiveProcessPurgeError:
            return False
        with self._lock:
            blocked = set(self._retired)
            for _, frozen_handles in self._frozen.values():
                blocked.update(frozen_handles)
            return blocked.isdisjoint(candidate_handles)

    def work_is_exact(self, work_order: Mapping[str, Any]) -> bool:
        try:
            handles = self._work_handles(work_order)
        except ConversationArchiveProcessPurgeError:
            return False
        return bool(handles) and work_order.get("lineageComplete") is True

    def snapshot(self) -> ProcessPurgeFenceSnapshot:
        with self._lock:
            frozen_handles = {
                handle
                for _, handles in self._frozen.values()
                for handle in handles
            }
            return ProcessPurgeFenceSnapshot(
                frozen_requests=len(self._frozen),
                frozen_handles=len(frozen_handles),
                retired_handles=len(self._retired),
            )


ProcessPurgeOwner = Callable[
    [Mapping[str, Any]],
    tuple[int, int, int] | Awaitable[tuple[int, int, int]],
]


class ConversationArchiveProcessPurgeRunner:
    """Run every requested process owner while one exact fence stays frozen."""

    def __init__(
        self,
        *,
        fence: ConversationArchiveProcessPurgeFence,
        owners: Mapping[str, ProcessPurgeOwner],
    ) -> None:
        if not isinstance(fence, ConversationArchiveProcessPurgeFence):
            raise ConversationArchiveProcessPurgeError(
                "archive_process_purge_runner_invalid"
            )
        normalized = dict(owners)
        if not normalized or any(
            not isinstance(sink, str) or not sink or not callable(owner)
            for sink, owner in normalized.items()
        ):
            raise ConversationArchiveProcessPurgeError(
                "archive_process_purge_runner_invalid"
            )
        self.fence = fence
        self.owners = normalized

    @staticmethod
    def _remaining_sinks(
        work_order: Mapping[str, Any],
    ) -> tuple[str, ...]:
        raw = work_order.get("remainingSinks")
        if (
            not isinstance(raw, list)
            or not raw
            or len(raw) > 32
            or any(not isinstance(item, str) or not item for item in raw)
        ):
            raise ConversationArchiveProcessPurgeError(
                "archive_process_purge_work_invalid"
            )
        sinks = tuple(raw)
        if len(set(sinks)) != len(sinks):
            raise ConversationArchiveProcessPurgeError(
                "archive_process_purge_work_invalid"
            )
        return sinks

    @staticmethod
    def _result_is_complete(value: Any) -> bool:
        return bool(
            isinstance(value, tuple)
            and len(value) == 3
            and all(type(item) is int and item >= 0 for item in value)
            and value[1] == 0
            and value[2] == 0
        )

    async def purge(
        self,
        work_order: Mapping[str, Any],
    ) -> tuple[str, ...]:
        """Return ackable sinks only after every local owner proves zero recall."""

        self.fence.freeze(work_order)
        if not self.fence.work_is_exact(work_order):
            return ()
        try:
            sinks = self._remaining_sinks(work_order)
        except ConversationArchiveProcessPurgeError:
            return ()
        if any(sink not in self.owners for sink in sinks):
            return ()
        completed: list[str] = []
        for sink in sinks:
            if not self.fence.work_is_exact(work_order):
                return ()
            try:
                result = self.owners[sink](work_order)
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                return ()
            if not self._result_is_complete(result):
                return ()
            completed.append(sink)
        return tuple(completed)

    def release_completed(self, work_order: Mapping[str, Any]) -> None:
        # Completion is the privacy boundary: exact lineage must stay blocked
        # for the rest of this process so a late producer cannot recreate it.
        self.fence.retire(work_order)


__all__ = [
    "ConversationArchiveProcessPurgeError",
    "ConversationArchiveProcessPurgeFence",
    "ConversationArchiveProcessPurgeRunner",
    "ProcessPurgeOwner",
    "ProcessPurgeFenceSnapshot",
    "conversation_archive_process_target_values",
    "purge_exact_process_caches",
]
