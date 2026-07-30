from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class DerivationNode:
    note_id: str
    title: str
    note_type: str
    source_hash: str
    derived_from: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DerivationReason:
    revoked_source_ids: tuple[str, ...]
    blocked_source_ids: tuple[str, ...]
    remaining_source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DerivationResolution:
    deleted_note_ids: frozenset[str]
    quarantined_note_ids: frozenset[str]
    reasons: Mapping[str, DerivationReason]


def _clean_ids(values: Iterable[str]) -> set[str]:
    return {
        cleaned
        for value in values
        if (cleaned := str(value or "").strip())
    }


def resolve_derivation_states(
    nodes: Mapping[str, DerivationNode],
    *,
    deleted_note_ids: Iterable[str] = (),
    seeded_quarantine_ids: Iterable[str] = (),
) -> DerivationResolution:
    """Resolve transitive deletion and quarantine without reading note content.

    A derived note is cascade-deleted only when every direct source is already
    deleted. If at least one source is still live, or one source is merely
    quarantined and may be rebuilt, the derived note is quarantined instead.
    Existing quarantine entries remain fail-closed until an explicit rewrite
    resolves them.
    """

    deleted = _clean_ids(deleted_note_ids)
    quarantined = (
        _clean_ids(seeded_quarantine_ids)
        & set(nodes)
    ) - deleted

    changed = True
    while changed:
        changed = False
        for note_id in sorted(nodes):
            if note_id in deleted:
                quarantined.discard(note_id)
                continue
            dependencies = _clean_ids(
                nodes[note_id].derived_from
            )
            if not dependencies:
                continue
            revoked = dependencies & deleted
            blocked = dependencies & quarantined
            if not revoked and not blocked:
                continue
            remaining = dependencies - deleted - quarantined
            if revoked and not remaining and not blocked:
                deleted.add(note_id)
                if note_id in quarantined:
                    quarantined.remove(note_id)
                changed = True
                continue
            if note_id not in quarantined:
                quarantined.add(note_id)
                changed = True

    reasons: dict[str, DerivationReason] = {}
    for note_id in sorted(quarantined):
        node = nodes.get(note_id)
        if node is None:
            continue
        dependencies = _clean_ids(node.derived_from)
        reasons[note_id] = DerivationReason(
            revoked_source_ids=tuple(
                sorted(dependencies & deleted)
            ),
            blocked_source_ids=tuple(
                sorted(
                    (dependencies & quarantined)
                    - {note_id}
                )
            ),
            remaining_source_ids=tuple(
                sorted(
                    dependencies
                    - deleted
                    - quarantined
                )
            ),
        )

    return DerivationResolution(
        deleted_note_ids=frozenset(deleted),
        quarantined_note_ids=frozenset(quarantined),
        reasons=reasons,
    )


def changed_quarantine_ids(
    baseline: DerivationResolution,
    candidate: DerivationResolution,
) -> frozenset[str]:
    changed: set[str] = set()
    for note_id in candidate.quarantined_note_ids:
        if note_id not in baseline.quarantined_note_ids:
            changed.add(note_id)
            continue
        if candidate.reasons.get(note_id) != baseline.reasons.get(
            note_id
        ):
            changed.add(note_id)
    return frozenset(changed)


__all__ = [
    "DerivationNode",
    "DerivationReason",
    "DerivationResolution",
    "changed_quarantine_ids",
    "resolve_derivation_states",
]
