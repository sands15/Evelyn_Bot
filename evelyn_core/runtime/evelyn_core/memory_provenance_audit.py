from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


DIRECT_SOURCE_TYPES = frozenset({"conversation", "system", "user"})


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _reference_key(value: object) -> str:
    cleaned = _clean(value).replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.rstrip("/").casefold()


def _hash_key(value: object) -> str:
    return _clean(value).lower()


@dataclass(frozen=True, slots=True)
class ProvenanceAuditNode:
    note_id: str
    note_type: str
    source_type: str
    source_refs: tuple[str, ...] = ()
    derived_from: tuple[str, ...] = ()
    origin_derived_from: tuple[str, ...] = ()
    evidence_hashes: tuple[str, ...] = ()
    reference_aliases: tuple[str, ...] = ()
    evidence_aliases: tuple[str, ...] = ()
    explicitly_detached: bool = False


@dataclass(frozen=True, slots=True)
class ProvenanceCandidateSignal:
    source_note_id: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceBackfillCandidate:
    target_note_id: str
    state: str
    signals: tuple[ProvenanceCandidateSignal, ...]
    reason_codes: tuple[str, ...]

    @property
    def candidate_source_ids(self) -> tuple[str, ...]:
        return tuple(signal.source_note_id for signal in self.signals)


@dataclass(frozen=True, slots=True)
class ProvenanceAuditResult:
    audited_note_count: int
    declared_derivation_count: int
    explicitly_detached_count: int
    auditable_missing_count: int
    unmatched_target_count: int
    cycle_rejected_signal_count: int
    candidates: tuple[ProvenanceBackfillCandidate, ...]

    @property
    def verified_count(self) -> int:
        return sum(
            1 for candidate in self.candidates
            if candidate.state == "verified"
        )

    @property
    def review_count(self) -> int:
        return sum(
            1 for candidate in self.candidates
            if candidate.state == "review"
        )

    @property
    def ambiguous_count(self) -> int:
        return sum(
            1 for candidate in self.candidates
            if candidate.state == "ambiguous"
        )


def _index_aliases(
    nodes: dict[str, ProvenanceAuditNode],
    *,
    attribute: str,
    normalizer: Callable[[object], str],
) -> dict[str, set[str]]:
    output: dict[str, set[str]] = {}
    for note_id, node in nodes.items():
        for raw_value in getattr(node, attribute):
            value = normalizer(raw_value)
            if value:
                output.setdefault(value, set()).add(note_id)
    return output


def _matching_note_ids(
    values: Iterable[str],
    *,
    index: dict[str, set[str]],
    normalizer: Callable[[object], str],
) -> set[str]:
    matches: set[str] = set()
    for raw_value in values:
        value = normalizer(raw_value)
        if value:
            matches.update(index.get(value, set()))
    return matches


def _depends_on(
    nodes: dict[str, ProvenanceAuditNode],
    start_note_id: str,
    target_note_id: str,
) -> bool:
    pending = [start_note_id]
    visited: set[str] = set()
    while pending:
        note_id = pending.pop()
        if note_id in visited:
            continue
        visited.add(note_id)
        node = nodes.get(note_id)
        if node is None:
            continue
        for source_id in node.derived_from:
            if source_id == target_note_id:
                return True
            if source_id not in visited:
                pending.append(source_id)
    return False


def audit_missing_derivations(
    audit_nodes: Iterable[ProvenanceAuditNode],
) -> ProvenanceAuditResult:
    nodes = {
        node.note_id: node
        for node in audit_nodes
        if _clean(node.note_id)
    }
    ref_index = _index_aliases(
        nodes,
        attribute="reference_aliases",
        normalizer=_reference_key,
    )
    hash_index = _index_aliases(
        nodes,
        attribute="evidence_aliases",
        normalizer=_hash_key,
    )

    declared_count = 0
    detached_count = 0
    missing_count = 0
    unmatched_count = 0
    cycle_rejected_count = 0
    candidates: list[ProvenanceBackfillCandidate] = []

    for target_id in sorted(nodes):
        target = nodes[target_id]
        if target.derived_from:
            declared_count += 1
            continue
        if target.explicitly_detached or target.origin_derived_from:
            detached_count += 1
            continue
        has_audit_signal = bool(
            target.source_refs or target.evidence_hashes
        )
        if (
            target.source_type in DIRECT_SOURCE_TYPES
            or not has_audit_signal
        ):
            continue
        missing_count += 1

        ref_matches = _matching_note_ids(
            target.source_refs,
            index=ref_index,
            normalizer=_reference_key,
        )
        hash_matches = _matching_note_ids(
            target.evidence_hashes,
            index=hash_index,
            normalizer=_hash_key,
        )
        ref_matches.discard(target_id)
        hash_matches.discard(target_id)

        all_matches = ref_matches | hash_matches
        cyclic_matches = {
            source_id
            for source_id in all_matches
            if _depends_on(nodes, source_id, target_id)
        }
        cycle_rejected_count += len(cyclic_matches)
        ref_matches.difference_update(cyclic_matches)
        hash_matches.difference_update(cyclic_matches)
        all_matches = ref_matches | hash_matches
        if not all_matches:
            unmatched_count += 1
            continue

        conflicting = bool(
            ref_matches
            and hash_matches
            and ref_matches != hash_matches
        )
        fully_correlated = bool(
            ref_matches
            and hash_matches
            and ref_matches == hash_matches
        )
        if conflicting or (
            len(all_matches) > 1 and not fully_correlated
        ):
            state = "ambiguous"
        elif fully_correlated:
            state = "verified"
        else:
            state = "review"

        signals: list[ProvenanceCandidateSignal] = []
        for source_id in sorted(all_matches):
            reason_codes: list[str] = []
            if source_id in ref_matches:
                reason_codes.append("exact_source_ref")
            if source_id in hash_matches:
                reason_codes.append("exact_evidence_hash")
            signals.append(
                ProvenanceCandidateSignal(
                    source_note_id=source_id,
                    reason_codes=tuple(reason_codes),
                )
            )
        entry_reasons = (
            ("conflicting_exact_signals",)
            if conflicting
            else (
                ("multiple_exact_candidates",)
                if state == "ambiguous"
                else ("exact_metadata_match",)
            )
        )
        candidates.append(
            ProvenanceBackfillCandidate(
                target_note_id=target_id,
                state=state,
                signals=tuple(signals),
                reason_codes=entry_reasons,
            )
        )

    return ProvenanceAuditResult(
        audited_note_count=len(nodes),
        declared_derivation_count=declared_count,
        explicitly_detached_count=detached_count,
        auditable_missing_count=missing_count,
        unmatched_target_count=unmatched_count,
        cycle_rejected_signal_count=cycle_rejected_count,
        candidates=tuple(candidates),
    )


__all__ = [
    "ProvenanceAuditNode",
    "ProvenanceAuditResult",
    "ProvenanceBackfillCandidate",
    "ProvenanceCandidateSignal",
    "audit_missing_derivations",
]
