from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable


DIRECT_SOURCE_TYPES = frozenset({"conversation", "system", "user"})
PROVENANCE_COVERAGE_SCHEMA = "memory.provenance.coverage.v1"


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _reference_key(value: object) -> str:
    cleaned = _clean(value).replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.rstrip("/").casefold()


def _hash_key(value: object) -> str:
    return _clean(value).lower()


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


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
    updated_at: str = ""


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
    missing_signal_target_ids: tuple[str, ...]
    unmatched_target_ids: tuple[str, ...]
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
    missing_signal_target_ids: list[str] = []
    unmatched_target_ids: list[str] = []
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
        if target.source_type in DIRECT_SOURCE_TYPES:
            continue
        if not has_audit_signal:
            missing_signal_target_ids.append(target_id)
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
            unmatched_target_ids.append(target_id)
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
        missing_signal_target_ids=tuple(
            missing_signal_target_ids
        ),
        unmatched_target_ids=tuple(unmatched_target_ids),
        candidates=tuple(candidates),
    )


def _coverage_age_bucket(
    value: object,
    *,
    now: datetime,
) -> str:
    cleaned = _clean(value)
    if not cleaned:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(
            cleaned.replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return "unknown"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_days = max(
        0.0,
        (
            now.astimezone(timezone.utc)
            - parsed.astimezone(timezone.utc)
        ).total_seconds()
        / 86_400.0,
    )
    if age_days <= 7:
        return "0_7d"
    if age_days <= 30:
        return "8_30d"
    if age_days <= 180:
        return "31_180d"
    return "over_180d"


def _coverage_bucket_rows(
    buckets: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "totalNoteCount": values["total"],
            "groundedNoteCount": values["grounded"],
            "needsReviewCount": values["needs_review"],
        }
        for key, values in sorted(buckets.items())
    ]


def summarize_provenance_coverage(
    audit_nodes: Iterable[ProvenanceAuditNode],
    *,
    audit: ProvenanceAuditResult | None = None,
    forward_write_rejections: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    nodes = tuple(
        node
        for node in audit_nodes
        if _clean(node.note_id)
    )
    current_audit = audit or audit_missing_derivations(nodes)
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    checked_at = checked_at.astimezone(timezone.utc)

    candidate_states = {
        candidate.target_note_id: candidate.state
        for candidate in current_audit.candidates
    }
    missing_signal_ids = set(
        current_audit.missing_signal_target_ids
    )
    unmatched_ids = set(current_audit.unmatched_target_ids)
    state_counts: dict[str, int] = {}
    source_buckets: dict[str, dict[str, int]] = {}
    type_buckets: dict[str, dict[str, int]] = {}
    age_buckets: dict[str, dict[str, int]] = {}
    grounded_count = 0

    for node in nodes:
        if node.derived_from:
            state = "declared_derivation"
        elif (
            node.explicitly_detached
            or node.origin_derived_from
        ):
            state = "user_detached"
        elif node.source_type in DIRECT_SOURCE_TYPES:
            state = "direct_source"
        elif node.note_id in candidate_states:
            candidate_state = candidate_states[node.note_id]
            state = (
                "ambiguous"
                if candidate_state == "ambiguous"
                else "exact_candidate"
            )
        elif node.note_id in unmatched_ids:
            state = "unmatched_metadata"
        elif node.note_id in missing_signal_ids:
            state = "missing_signal"
        else:
            state = "unclassified"

        grounded = state in {
            "declared_derivation",
            "direct_source",
            "user_detached",
        }
        grounded_count += int(grounded)
        state_counts[state] = state_counts.get(state, 0) + 1
        dimensions = (
            (
                source_buckets,
                _clean(node.source_type).lower()
                or "unknown",
            ),
            (
                type_buckets,
                _clean(node.note_type).lower()
                or "unknown",
            ),
            (
                age_buckets,
                _coverage_age_bucket(
                    node.updated_at,
                    now=checked_at,
                ),
            ),
        )
        for buckets, key in dimensions:
            values = buckets.setdefault(
                key,
                {
                    "total": 0,
                    "grounded": 0,
                    "needs_review": 0,
                },
            )
            values["total"] += 1
            values["grounded"] += int(grounded)
            values["needs_review"] += int(not grounded)

    rejection_payload = (
        forward_write_rejections
        if isinstance(forward_write_rejections, dict)
        else {}
    )
    rejection_by_type = (
        rejection_payload.get("byNoteType")
        if isinstance(
            rejection_payload.get("byNoteType"),
            dict,
        )
        else {}
    )
    total_count = len(nodes)
    needs_review_count = total_count - grounded_count
    return {
        "schema": PROVENANCE_COVERAGE_SCHEMA,
        "contentFree": True,
        "totalNoteCount": total_count,
        "groundedNoteCount": grounded_count,
        "needsReviewCount": needs_review_count,
        "coverageRatio": (
            round(grounded_count / total_count, 4)
            if total_count
            else 1.0
        ),
        "stateCounts": {
            key: state_counts[key]
            for key in sorted(state_counts)
        },
        "bySourceType": _coverage_bucket_rows(
            source_buckets
        ),
        "byNoteType": _coverage_bucket_rows(type_buckets),
        "byAgeBucket": _coverage_bucket_rows(age_buckets),
        "forwardWriteRejections": {
            "count": _non_negative_int(
                rejection_payload.get("count")
            ),
            "byNoteType": {
                _clean(key).lower() or "unknown": (
                    _non_negative_int(value)
                )
                for key, value in sorted(
                    rejection_by_type.items()
                )
            },
        },
        "checkedAt": checked_at.isoformat().replace(
            "+00:00",
            "Z",
        ),
    }


__all__ = [
    "ProvenanceAuditNode",
    "ProvenanceAuditResult",
    "ProvenanceBackfillCandidate",
    "ProvenanceCandidateSignal",
    "PROVENANCE_COVERAGE_SCHEMA",
    "audit_missing_derivations",
    "summarize_provenance_coverage",
]
