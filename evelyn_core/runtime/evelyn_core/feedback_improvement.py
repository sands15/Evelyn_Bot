from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from .conversation_archive import (
    ArchiveRecord,
    ArchiveStaleEvent,
    ArchiveValidationError,
    ConversationArchive,
)


FEEDBACK_CORRECTION_SCHEMA = "evelyn.feedback-correction.v1"
FEEDBACK_SOURCE_CANDIDATE_SCHEMA = "evelyn.feedback-source-candidate.v1"
FEEDBACK_GUIDANCE_SCHEMA = "evelyn.feedback-independent-guidance.v1"
FEEDBACK_EVALUATION_SCHEMA = "evelyn.feedback-evaluation-receipt.v1"
FEEDBACK_APPROVAL_SCHEMA = "evelyn.feedback-approval-receipt.v1"
FEEDBACK_CANARY_SCHEMA = "evelyn.feedback-canary-receipt.v1"
FEEDBACK_ACTIVATION_SCHEMA = "evelyn.feedback-activation-receipt.v1"
FEEDBACK_FAILURE_SCHEMA = "evelyn.feedback-fixed-failure-receipt.v1"
FEEDBACK_ROLLBACK_SCHEMA = "evelyn.feedback-rollback-receipt.v1"
FEEDBACK_REVOCATION_SCHEMA = "evelyn.feedback-revocation-receipt.v1"
FEEDBACK_PRIVACY_REVIEW_SCHEMA = "evelyn.feedback-privacy-review.v1"
FEEDBACK_CANARY_AGGREGATE_SCHEMA = "evelyn.feedback-canary-aggregate.v1"

FEEDBACK_CATEGORIES = frozenset(
    {
        "answer_quality",
        "context_selection",
        "task_routing",
        "tone_identity",
        "tool_failure",
        "permission_safety",
    }
)
FEEDBACK_WORKFLOW_STATES = (
    "captured",
    "owner_verified",
    "routed",
    "source_bound_candidate",
    "generalized",
    "privacy_reviewed",
    "independent_candidate",
    "eval_passed",
    "awaiting_approval",
    "approval_granted",
    "canary_running",
    "canary_passed",
    "active",
)
FEEDBACK_DELETION_STATES = (
    "source_deleted",
    "purge_pending",
    "revoked",
)
FEEDBACK_ACTIONABLE_CATEGORIES = frozenset(
    {
        "answer_quality",
        "context_selection",
        "task_routing",
        "tool_failure",
    }
)
FEEDBACK_FIXED_FAILURE_CODES = frozenset(
    {
        "grounding_regression",
        "schema_regression",
        "permission_regression",
        "privacy_regression",
        "verified_behavior_regression",
    }
)
BASE_GUIDANCE_VERSION_ID = "base"
BASE_GUIDANCE_DIGEST = hashlib.sha256(b"").hexdigest()
CURRENT_TASK_CONTRACT_VERSION = "evelyn.task-work-contract.v1"
CURRENT_TASK_EVALUATOR_VERSION = "evelyn.task-agent-eval-suite.v1"

_GUIDANCE_MAX_CHARS = 8_000
_CORRECTION_MAX_CHARS = 4_000
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DISCORD_IDENTIFIER_RE = re.compile(r"(?:<@!?\d{15,22}>|\b\d{15,22}\b)")


class FeedbackImprovementError(RuntimeError):
    """Stable, content-free P1-5 failure."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


class FeedbackAuthorizationError(FeedbackImprovementError):
    pass


class FeedbackConflictError(FeedbackImprovementError):
    pass


class FeedbackIntegrityError(FeedbackImprovementError):
    pass


@dataclass(frozen=True, slots=True)
class FeedbackWorkflowSnapshot:
    workflow_id: str
    state: str
    category: str | None
    route: str
    actionable: bool
    source_record_id: str | None
    version_id: str | None
    active_version_id: str
    deletion_states: tuple[str, ...] = ()

    def public_record(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "evelyn.feedback-workflow-public.v1",
            "workflowId": self.workflow_id,
            "state": self.state,
            "category": self.category,
            "route": self.route,
            "actionable": self.actionable,
            "versionId": self.version_id,
            "activeVersionId": self.active_version_id,
            "deletionStates": list(self.deletion_states),
            "contentFree": True,
        }
        if self.source_record_id is not None:
            payload["sourceRecordId"] = self.source_record_id
        return payload


@dataclass(frozen=True, slots=True)
class GuidanceBinding:
    version_id: str
    guidance: str
    guidance_digest: str
    source_free: bool
    active: bool

    def public_record(self) -> dict[str, Any]:
        return {
            "schema": "evelyn.task-guidance-binding.v1",
            "versionId": self.version_id,
            "guidanceDigest": self.guidance_digest,
            "sourceFree": self.source_free,
            "active": self.active,
            "contentFree": True,
        }


@dataclass(frozen=True, slots=True)
class RunningCanaryBinding:
    canary_run_id: str
    version_id: str
    guidance: str
    guidance_digest: str
    archive_generation: int


@dataclass(frozen=True, slots=True)
class FeedbackActionBinding:
    action: str
    version_id: str
    active_version_id: str
    archive_generation: int
    binding_digest: str

    def public_record(self) -> dict[str, Any]:
        return {
            "schema": "evelyn.feedback-action-binding.v1",
            "action": self.action,
            "versionId": self.version_id,
            "activeVersionId": self.active_version_id,
            "archiveGeneration": self.archive_generation,
            "bindingDigest": self.binding_digest,
            "contentFree": True,
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, *, code: str, maximum: int) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise FeedbackImprovementError(code)
    normalized = " ".join(value.split()).strip()
    if not normalized or len(normalized) > maximum:
        raise FeedbackImprovementError(code)
    return normalized


def _body_text(value: Any, *, code: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or "\x00" in value
        or not value.strip()
        or len(value) > maximum
    ):
        raise FeedbackImprovementError(code)
    return value


def _identifier(value: Any, *, code: str, maximum: int = 128) -> str:
    normalized = _text(value, code=code, maximum=maximum)
    if _IDENTIFIER_RE.fullmatch(normalized) is None:
        raise FeedbackImprovementError(code)
    return normalized


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeedbackImprovementError("feedback_time_invalid")
    return value.astimezone(timezone.utc).isoformat()


def _body(record: ArchiveRecord, schema: str, fields: frozenset[str]) -> dict[str, Any]:
    try:
        payload = json.loads(record.body)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise FeedbackIntegrityError("feedback_ledger_body_invalid") from None
    if (
        not isinstance(payload, dict)
        or set(payload) != set(fields)
        or payload.get("schema") != schema
    ):
        raise FeedbackIntegrityError("feedback_ledger_body_invalid")
    return payload


_CORRECTION_FIELDS = frozenset(
    {
        "schema",
        "workflowId",
        "taskId",
        "sourceRecordId",
        "category",
        "issuerPrincipalId",
        "sessionId",
        "surface",
        "actionable",
        "route",
        "correction",
        "revision",
        "nonceDigest",
        "capturedAt",
        "states",
    }
)
_SOURCE_CANDIDATE_FIELDS = frozenset(
    {
        "schema",
        "workflowId",
        "versionId",
        "category",
        "candidateGuidance",
        "createdAt",
        "state",
    }
)
_GUIDANCE_FIELDS = frozenset(
    {
        "schema",
        "versionId",
        "category",
        "guidance",
        "guidanceDigest",
        "ancestorVersionIds",
        "privacyReview",
        "createdAt",
        "state",
    }
)
_EVALUATION_FIELDS = frozenset(
    {
        "schema",
        "versionId",
        "guidanceDigest",
        "evalRunId",
        "suiteVersion",
        "baselineContractDigest",
        "candidateContractDigest",
        "aggregate",
        "passed",
        "createdAt",
        "state",
    }
)
_APPROVAL_FIELDS = frozenset(
    {
        "schema",
        "approvalId",
        "versionId",
        "guidanceDigest",
        "evalRunId",
        "bindingDigest",
        "capability",
        "oneUse",
        "grantedAt",
        "state",
    }
)
_CANARY_FIELDS = frozenset(
    {
        "schema",
        "canaryRunId",
        "versionId",
        "guidanceDigest",
        "approvalId",
        "phase",
        "aggregate",
        "recordedAt",
        "state",
    }
)
_ACTIVATION_FIELDS = frozenset(
    {
        "schema",
        "versionId",
        "guidanceDigest",
        "previousActiveVersionId",
        "approvalId",
        "canaryRunId",
        "bindingDigest",
        "activatedAt",
        "state",
    }
)
_FAILURE_FIELDS = frozenset(
    {
        "schema",
        "failureId",
        "versionId",
        "guidanceDigest",
        "taskId",
        "contractVersion",
        "evaluatorVersion",
        "failureCode",
        "principalId",
        "ledgerGeneration",
        "observedAt",
        "state",
    }
)
_ROLLBACK_FIELDS = frozenset(
    {
        "schema",
        "rollbackId",
        "failureId",
        "fromVersionId",
        "targetVersionId",
        "targetGuidanceDigest",
        "bindingDigest",
        "rolledBackAt",
        "state",
    }
)
_REVOCATION_FIELDS = frozenset(
    {
        "schema",
        "revocationId",
        "versionId",
        "reason",
        "descendantOfVersionId",
        "revokedAt",
        "state",
    }
)


class FeedbackImprovementController:
    """P1-5 state machine backed only by the P1-4 sole-writer archive."""

    def __init__(
        self,
        archive: ConversationArchive,
        *,
        clock: Callable[[], datetime] | None = None,
        evaluation_gate: Callable[..., bool] | None = None,
        task_contract_version: str = CURRENT_TASK_CONTRACT_VERSION,
        task_evaluator_version: str = CURRENT_TASK_EVALUATOR_VERSION,
    ) -> None:
        self.archive = archive
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._evaluation_gate = evaluation_gate
        self._task_contract_version = _identifier(
            task_contract_version,
            code="feedback_contract_version_invalid",
            maximum=80,
        )
        self._task_evaluator_version = _identifier(
            task_evaluator_version,
            code="feedback_evaluator_version_invalid",
            maximum=80,
        )

    def _now(self) -> datetime:
        value = self._clock()
        _utc_iso(value)
        return value.astimezone(timezone.utc)

    def _records_with_generation(self) -> tuple[tuple[ArchiveRecord, ...], int]:
        # Durable versions are intentionally not subject to the 30-day source
        # retention window.  Reconstruct the complete ledger in bounded pages;
        # accepting a prefix after years of operation could otherwise select an
        # obsolete active pointer.  Retry only when the sole writer advanced the
        # archive between pages, then fail closed rather than mix generations.
        for _ in range(3):
            generation = self.archive.generation
            records: list[ArchiveRecord] = []
            after_sequence = 0
            while True:
                page = self.archive.read_feedback_records_admin(
                    authorized=True,
                    limit=5000,
                    after_created_sequence=after_sequence,
                )
                records.extend(page)
                if len(page) < 5000:
                    break
                after_sequence = page[-1].created_sequence
            if self.archive.generation == generation:
                return tuple(records), generation
        raise FeedbackConflictError("feedback_ledger_generation_changed")

    def _records(self) -> tuple[ArchiveRecord, ...]:
        return self._records_with_generation()[0]

    @staticmethod
    def _of_type(
        records: Iterable[ArchiveRecord], record_type: str
    ) -> tuple[ArchiveRecord, ...]:
        return tuple(record for record in records if record.record_type == record_type)

    def _correction(
        self, workflow_id: str, records: Iterable[ArchiveRecord] | None = None
    ) -> tuple[ArchiveRecord, dict[str, Any]] | None:
        if records is None:
            record = self.archive.read_record_admin(
                authorized=True,
                record_id=workflow_id,
            )
        else:
            matches = tuple(
                item
                for item in records
                if item.record_id == workflow_id
                and item.record_type == "feedback_correction"
            )
            if len(matches) > 1:
                raise FeedbackIntegrityError("feedback_workflow_ambiguous")
            record = None if not matches else matches[0]
        if record is None or record.record_type != "feedback_correction":
            return None
        payload = _body(record, FEEDBACK_CORRECTION_SCHEMA, _CORRECTION_FIELDS)
        if payload["workflowId"] != workflow_id:
            raise FeedbackIntegrityError("feedback_workflow_binding_invalid")
        return record, payload

    def _source_candidate(
        self,
        workflow_id: str,
        records: Iterable[ArchiveRecord] | None = None,
    ) -> tuple[ArchiveRecord, dict[str, Any]] | None:
        source = tuple(records) if records is not None else self._records()
        matches: list[tuple[ArchiveRecord, dict[str, Any]]] = []
        for record in self._of_type(source, "feedback_source_candidate"):
            payload = _body(
                record,
                FEEDBACK_SOURCE_CANDIDATE_SCHEMA,
                _SOURCE_CANDIDATE_FIELDS,
            )
            if payload["workflowId"] == workflow_id:
                matches.append((record, payload))
        if len(matches) > 1:
            raise FeedbackIntegrityError("feedback_source_candidate_ambiguous")
        return None if not matches else matches[0]

    def _version(
        self,
        version_id: str,
        records: Iterable[ArchiveRecord] | None = None,
    ) -> tuple[ArchiveRecord, dict[str, Any]]:
        source = tuple(records) if records is not None else self._records()
        matches: list[tuple[ArchiveRecord, dict[str, Any]]] = []
        for record in self._of_type(source, "feedback_independent_version"):
            payload = _body(record, FEEDBACK_GUIDANCE_SCHEMA, _GUIDANCE_FIELDS)
            if payload["versionId"] == version_id:
                matches.append((record, payload))
        if len(matches) != 1:
            code = (
                "feedback_version_missing"
                if not matches
                else "feedback_version_ambiguous"
            )
            raise FeedbackImprovementError(code)
        return matches[0]

    def _revoked_version_ids(
        self, records: Iterable[ArchiveRecord]
    ) -> frozenset[str]:
        revoked: set[str] = set()
        for record in self._of_type(records, "feedback_revocation"):
            payload = _body(record, FEEDBACK_REVOCATION_SCHEMA, _REVOCATION_FIELDS)
            revoked.add(str(payload["versionId"]))
        return frozenset(revoked)

    def _quarantined_version_ids(
        self, records: Iterable[ArchiveRecord]
    ) -> frozenset[str]:
        source = tuple(records)
        versions: dict[str, tuple[str, ...]] = {}
        invalid: set[str] = set(self._revoked_version_ids(source))
        for record in self._of_type(source, "feedback_canary"):
            payload = _body(record, FEEDBACK_CANARY_SCHEMA, _CANARY_FIELDS)
            if payload.get("phase") == "failed":
                invalid.add(str(payload.get("versionId") or ""))
        for record in self._of_type(source, "feedback_independent_version"):
            payload = _body(record, FEEDBACK_GUIDANCE_SCHEMA, _GUIDANCE_FIELDS)
            version_id = str(payload.get("versionId") or "")
            ancestors_raw = payload.get("ancestorVersionIds")
            if not isinstance(ancestors_raw, list):
                invalid.add(version_id)
                ancestors: tuple[str, ...] = ()
            else:
                ancestors = tuple(str(value) for value in ancestors_raw)
            versions[version_id] = ancestors
            try:
                privacy = self._privacy_review(payload.get("privacyReview"))
            except (FeedbackImprovementError, TypeError):
                invalid.add(version_id)
                privacy = None
            guidance = payload.get("guidance")
            digest = payload.get("guidanceDigest")
            if (
                payload.get("state") != "independent_candidate"
                or payload.get("category") not in FEEDBACK_ACTIONABLE_CATEGORIES
                or not isinstance(guidance, str)
                or not guidance
                or len(guidance) > _GUIDANCE_MAX_CHARS
                or not isinstance(digest, str)
                or not hmac.compare_digest(
                    digest,
                    hashlib.sha256(guidance.encode("utf-8")).hexdigest(),
                )
                or privacy is None
                or len(ancestors) > 1
                or len(ancestors) != len(set(ancestors))
                or version_id in ancestors
            ):
                invalid.add(version_id)
        changed = True
        while changed:
            changed = False
            for version_id, ancestors in versions.items():
                if version_id in invalid:
                    continue
                if any(
                    ancestor not in versions or ancestor in invalid
                    for ancestor in ancestors
                ):
                    invalid.add(version_id)
                    changed = True
        return frozenset(invalid)

    def _require_version_admissible(
        self,
        version_id: str,
        records: Iterable[ArchiveRecord],
    ) -> tuple[ArchiveRecord, dict[str, Any]]:
        source = tuple(records)
        version = self._version(version_id, source)
        if version_id in self._quarantined_version_ids(source):
            raise FeedbackAuthorizationError("feedback_version_quarantined")
        return version

    def _require_candidate_parent_current(
        self,
        version_id: str,
        records: Iterable[ArchiveRecord],
    ) -> None:
        source = tuple(records)
        _, version = self._require_version_admissible(version_id, source)
        active = self._active_version_id(source)
        expected = () if active == BASE_GUIDANCE_VERSION_ID else (active,)
        if tuple(version.get("ancestorVersionIds") or ()) != expected:
            raise FeedbackConflictError("feedback_candidate_parent_stale")

    def _active_version_id(self, records: Iterable[ArchiveRecord] | None = None) -> str:
        source = tuple(records) if records is not None else self._records()
        active = BASE_GUIDANCE_VERSION_ID
        for record in source:
            if record.record_type == "feedback_activation":
                payload = _body(record, FEEDBACK_ACTIVATION_SCHEMA, _ACTIVATION_FIELDS)
                active = str(payload["versionId"])
            elif record.record_type == "feedback_rollback":
                payload = _body(record, FEEDBACK_ROLLBACK_SCHEMA, _ROLLBACK_FIELDS)
                active = str(payload["targetVersionId"])
        if active in self._quarantined_version_ids(source):
            return BASE_GUIDANCE_VERSION_ID
        return active

    def active_guidance(self) -> GuidanceBinding:
        records = self._records()
        return self._active_guidance_from_records(records)

    def _active_guidance_from_records(
        self,
        records: Iterable[ArchiveRecord],
    ) -> GuidanceBinding:
        records = tuple(records)
        version_id = self._active_version_id(records)
        if version_id == BASE_GUIDANCE_VERSION_ID:
            return GuidanceBinding(
                version_id=BASE_GUIDANCE_VERSION_ID,
                guidance="",
                guidance_digest=BASE_GUIDANCE_DIGEST,
                source_free=True,
                active=True,
            )
        _, payload = self._require_version_admissible(version_id, records)
        return GuidanceBinding(
            version_id=version_id,
            guidance=str(payload["guidance"]),
            guidance_digest=str(payload["guidanceDigest"]),
            source_free=True,
            active=True,
        )

    def approval_guidance(
        self,
        *,
        version_id: str,
        admin_authorized: bool,
    ) -> GuidanceBinding:
        if admin_authorized is not True:
            raise FeedbackAuthorizationError("feedback_local_admin_required")
        normalized = _identifier(
            version_id,
            code="feedback_version_id_invalid",
        )
        records = self._records()
        self._workflow_for_version(normalized, records)
        self._require_candidate_parent_current(normalized, records)
        evaluation = self._latest_evaluation(normalized, records)
        if evaluation is None or evaluation[1].get("passed") is not True:
            raise FeedbackAuthorizationError("feedback_evaluation_required")
        _, payload = self._version(normalized, records)
        return GuidanceBinding(
            version_id=normalized,
            guidance=str(payload["guidance"]),
            guidance_digest=str(payload["guidanceDigest"]),
            source_free=True,
            active=False,
        )

    def canary_guidance(
        self,
        *,
        version_id: str,
        local_admin: bool,
        read_only: bool,
        grounded_task: bool,
    ) -> GuidanceBinding:
        if local_admin is not True or read_only is not True or grounded_task is not True:
            raise FeedbackAuthorizationError("feedback_canary_scope_denied")
        pointer = self.running_canary_pointer(
            local_admin=local_admin,
            read_only=read_only,
            grounded_task=grounded_task,
        )
        if pointer is None or pointer.version_id != version_id:
            raise FeedbackImprovementError("feedback_canary_not_running")
        return GuidanceBinding(
            version_id=pointer.version_id,
            guidance=pointer.guidance,
            guidance_digest=pointer.guidance_digest,
            source_free=True,
            active=False,
        )

    def running_canary_pointer(
        self,
        *,
        local_admin: bool,
        read_only: bool,
        grounded_task: bool,
    ) -> RunningCanaryBinding | None:
        if local_admin is not True or read_only is not True or grounded_task is not True:
            raise FeedbackAuthorizationError("feedback_canary_scope_denied")
        records = self._records()
        terminal_runs: set[str] = set()
        canary_payloads: list[dict[str, Any]] = []
        for record in self._of_type(records, "feedback_canary"):
            parsed = _body(record, FEEDBACK_CANARY_SCHEMA, _CANARY_FIELDS)
            canary_payloads.append(parsed)
            if parsed.get("phase") in {"passed", "failed"}:
                terminal_runs.add(str(parsed["canaryRunId"]))
        running: list[dict[str, Any]] = []
        for payload in canary_payloads:
            if (
                payload.get("phase") == "running"
                and str(payload.get("canaryRunId")) not in terminal_runs
            ):
                running.append(payload)
        if len(running) > 1:
            raise FeedbackIntegrityError("feedback_canary_pointer_ambiguous")
        if not running:
            return None
        payload = running[0]
        version_id = _identifier(
            payload["versionId"], code="feedback_version_id_invalid"
        )
        self._workflow_for_version(version_id, records)
        self._require_candidate_parent_current(version_id, records)
        _, version = self._require_version_admissible(version_id, records)
        approval = self._latest_approval(version_id, records)
        if (
            approval is None
            or payload.get("approvalId") != approval[1].get("approvalId")
            or payload.get("guidanceDigest") != version.get("guidanceDigest")
        ):
            raise FeedbackIntegrityError("feedback_canary_binding_invalid")
        return RunningCanaryBinding(
            canary_run_id=str(payload["canaryRunId"]),
            version_id=version_id,
            guidance=str(version["guidance"]),
            guidance_digest=str(version["guidanceDigest"]),
            archive_generation=self.archive.generation,
        )

    def abort_interrupted_canary(
        self,
        *,
        canary_run_id: str | None = None,
        admin_authorized: bool,
    ) -> dict[str, Any] | None:
        """Fail one durable running canary without depending on its deleted source.

        Canary task receipts are deliberately process-local.  A restart, source
        deletion, or terminal aggregation fault therefore cannot reconstruct a
        successful run.  This narrow recovery operation validates the durable
        candidate/approval binding, appends a conservative terminal failure,
        and quarantines the candidate and descendants.  It never promotes or
        changes the active pointer.
        """

        if admin_authorized is not True:
            raise FeedbackAuthorizationError("feedback_local_admin_required")
        normalized_run = (
            None
            if canary_run_id is None
            else _identifier(
                canary_run_id,
                code="feedback_canary_run_id_invalid",
            )
        )
        records, generation = self._records_with_generation()
        terminal_runs: set[str] = set()
        running_payloads: list[dict[str, Any]] = []
        for record in self._of_type(records, "feedback_canary"):
            payload = _body(record, FEEDBACK_CANARY_SCHEMA, _CANARY_FIELDS)
            run_id = str(payload["canaryRunId"])
            if payload.get("phase") in {"passed", "failed"}:
                terminal_runs.add(run_id)
            elif payload.get("phase") == "running":
                running_payloads.append(payload)
        all_pending = [
            payload
            for payload in running_payloads
            if str(payload["canaryRunId"]) not in terminal_runs
        ]
        if not all_pending:
            return None
        if len(all_pending) != 1:
            raise FeedbackIntegrityError("feedback_canary_pointer_ambiguous")
        running = all_pending[0]
        if (
            normalized_run is not None
            and str(running["canaryRunId"]) != normalized_run
        ):
            raise FeedbackConflictError("feedback_canary_binding_stale")
        run_id = _identifier(
            running["canaryRunId"],
            code="feedback_canary_run_id_invalid",
        )
        version_id = _identifier(
            running["versionId"],
            code="feedback_version_id_invalid",
        )
        _, version = self._version(version_id, records)
        approval = self._latest_approval(version_id, records)
        if (
            approval is None
            or running.get("guidanceDigest") != version.get("guidanceDigest")
            or running.get("approvalId") != approval[1].get("approvalId")
            or approval[1].get("guidanceDigest") != version.get("guidanceDigest")
            or self._active_version_id(records) == version_id
        ):
            raise FeedbackIntegrityError("feedback_canary_binding_invalid")
        aggregate = {
            "schema": FEEDBACK_CANARY_AGGREGATE_SCHEMA,
            "candidateVersionId": version_id,
            "guidanceDigest": str(version["guidanceDigest"]),
            "contractVersion": self._task_contract_version,
            "evaluatorVersion": self._task_evaluator_version,
            "sampleCount": 10,
            "passedCount": 0,
            "unauthorizedEffectCount": 0,
            "privacyLeakageCount": 0,
            "structuralFailureCount": 10,
            "taskFailureCount": 10,
        }
        now = self._now()
        body = {
            "schema": FEEDBACK_CANARY_SCHEMA,
            "canaryRunId": run_id,
            "versionId": version_id,
            "guidanceDigest": version["guidanceDigest"],
            "approvalId": running["approvalId"],
            "phase": "failed",
            "aggregate": aggregate,
            "recordedAt": _utc_iso(now),
            "state": "canary_failed",
        }
        self.archive.append_system_record(
            record_type="feedback_canary",
            body=_canonical_json(body),
            started_at=now,
            ended_at=now,
            parent_ids=(version_id,),
            idempotency_key=f"feedback-canary-failed:{run_id}",
            record_id=(
                "fgc-fail-"
                + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:43]
            ),
            expected_generation=generation,
            now=now,
        )
        revoked = self._append_revocations(
            version_id=version_id,
            reason="canary_failed",
            records=records,
            expected_generation=generation + 1,
        )
        return {
            "schema": "evelyn.feedback-canary-abort-public.v1",
            "canaryRunId": run_id,
            "versionId": version_id,
            "state": "canary_failed",
            "revokedVersionIds": list(revoked),
            "contentFree": True,
        }

    @staticmethod
    def _route(
        *,
        category: str,
        surface: str,
        admin_authorized: bool,
        requires_engineering: bool,
    ) -> tuple[str, bool]:
        if category == "tone_identity":
            return "identity_review", False
        if category == "permission_safety" or requires_engineering:
            return "human_engineering_required", False
        if surface != "local" or admin_authorized is not True:
            return "review_only", False
        if category not in FEEDBACK_ACTIONABLE_CATEGORIES:
            return "human_engineering_required", False
        return "improvement", True

    def capture_correction(
        self,
        *,
        task_id: str,
        source_record_id: str,
        category: str,
        correction: str,
        identity_surface: str,
        actor_external_id: str,
        owner_name: str,
        surface: str,
        session_id: str,
        nonce: str,
        session_current: bool,
        admin_authorized: bool = False,
        requires_engineering: bool = False,
    ) -> FeedbackWorkflowSnapshot:
        if session_current is not True:
            raise FeedbackConflictError("feedback_session_stale")
        normalized_task = _identifier(task_id, code="feedback_task_id_invalid")
        normalized_source = _identifier(
            source_record_id,
            code="feedback_source_record_id_invalid",
            maximum=64,
        )
        normalized_category = _text(
            category,
            code="feedback_category_invalid",
            maximum=32,
        )
        if normalized_category not in FEEDBACK_CATEGORIES:
            raise FeedbackImprovementError("feedback_category_invalid")
        exact_correction = _body_text(
            correction,
            code="feedback_correction_invalid",
            maximum=_CORRECTION_MAX_CHARS,
        )
        normalized_identity_surface = _text(
            identity_surface,
            code="feedback_identity_surface_invalid",
            maximum=16,
        )
        if normalized_identity_surface not in {"local", "discord"}:
            raise FeedbackImprovementError("feedback_identity_surface_invalid")
        normalized_surface = _text(
            surface,
            code="feedback_surface_invalid",
            maximum=16,
        )
        if normalized_surface not in {"local", "discord", "voice"}:
            raise FeedbackImprovementError("feedback_surface_invalid")
        normalized_session = _identifier(
            session_id,
            code="feedback_session_id_invalid",
        )
        normalized_nonce = _identifier(
            nonce,
            code="feedback_nonce_invalid",
        )
        normalized_owner = _text(
            owner_name,
            code="feedback_owner_name_invalid",
            maximum=80,
        )
        route, actionable = self._route(
            category=normalized_category,
            surface=normalized_surface,
            admin_authorized=admin_authorized,
            requires_engineering=requires_engineering,
        )
        if actionable and normalized_identity_surface != "local":
            raise FeedbackAuthorizationError("feedback_local_admin_required")
        binding = self.archive.feedback_source_binding(
            authorized=True,
            source_record_id=normalized_source,
            identity_surface=normalized_identity_surface,
            actor_external_id=actor_external_id,
        )
        if (
            normalized_surface == "local"
            and (binding.mode != "local_private" or binding.surface != "local")
        ) or (
            normalized_surface in {"discord", "voice"}
            and binding.mode != "discord_shared"
        ):
            raise FeedbackConflictError("feedback_surface_binding_mismatch")
        workflow_id = self.archive.feedback_workflow_id(
            identity_surface=normalized_identity_surface,
            actor_external_id=actor_external_id,
            nonce=normalized_nonce,
        )
        existing = self._correction(workflow_id)
        if existing is not None:
            _, payload = existing
            expected = {
                "taskId": normalized_task,
                "sourceRecordId": normalized_source,
                "category": normalized_category,
                "correction": exact_correction,
                "sessionId": normalized_session,
                "surface": normalized_surface,
                "route": route,
                "actionable": actionable,
            }
            if any(payload.get(key) != value for key, value in expected.items()):
                raise FeedbackConflictError("feedback_nonce_reused")
            return self.workflow(workflow_id)
        now = self._now()
        body = {
            "schema": FEEDBACK_CORRECTION_SCHEMA,
            "workflowId": workflow_id,
            "taskId": normalized_task,
            "sourceRecordId": normalized_source,
            "category": normalized_category,
            "issuerPrincipalId": binding.owner_principal_id,
            "sessionId": normalized_session,
            "surface": normalized_surface,
            "actionable": actionable,
            "route": route,
            "correction": exact_correction,
            "revision": 1,
            "nonceDigest": hashlib.sha256(
                normalized_nonce.encode("utf-8")
            ).hexdigest(),
            "capturedAt": _utc_iso(now),
            "states": ["captured", "owner_verified", "routed"],
        }
        try:
            self.archive.append_record(
                mode=binding.mode,
                surface=binding.surface,
                record_type="feedback_correction",
                body=_canonical_json(body),
                started_at=now,
                ended_at=now,
                actor_external_id=actor_external_id,
                owner_name=normalized_owner,
                guild_id=binding.guild_id,
                channel_id=binding.channel_id,
                parent_ids=(binding.record_id,),
                idempotency_key=f"feedback-capture:{workflow_id}",
                record_id=workflow_id,
                expected_generation=binding.archive_generation,
                now=now,
            )
        except ArchiveStaleEvent:
            raise FeedbackConflictError("feedback_source_generation_stale") from None
        return self.workflow(workflow_id)

    @staticmethod
    def _privacy_review(raw: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "schema": FEEDBACK_PRIVACY_REVIEW_SCHEMA,
            "reviewedByLocalOperator": True,
            "sourceIdentifiersAbsent": True,
            "privateDataAbsent": True,
            "quotesAbsent": True,
            "uniquePhrasesAbsent": True,
            "semanticParaphraseRiskAbsent": True,
            "styleFingerprintAbsent": True,
            "inferenceRiskAbsent": True,
            "privacyFixturePassed": True,
        }
        if set(raw) != set(required) or any(
            raw.get(key) != value for key, value in required.items()
        ):
            raise FeedbackAuthorizationError("feedback_privacy_review_required")
        return dict(required)

    def _assert_guidance_independent(
        self,
        guidance: str,
        correction: Mapping[str, Any],
    ) -> None:
        normalized_guidance = " ".join(guidance.casefold().split())
        normalized_correction = " ".join(
            str(correction["correction"]).casefold().split()
        )
        forbidden = (
            str(correction["workflowId"]),
            str(correction["taskId"]),
            str(correction["sourceRecordId"]),
            str(correction["issuerPrincipalId"]),
            str(correction["sessionId"]),
        )
        if (
            _DISCORD_IDENTIFIER_RE.search(guidance)
            or any(value and value.casefold() in guidance.casefold() for value in forbidden)
            or (
                len(normalized_correction) >= 16
                and normalized_correction in normalized_guidance
            )
        ):
            raise FeedbackAuthorizationError("feedback_guidance_source_dependent")

    def generalize(
        self,
        *,
        workflow_id: str,
        guidance: str,
        privacy_review: Mapping[str, Any],
        ancestor_version_ids: Iterable[str] = (),
        admin_authorized: bool,
    ) -> FeedbackWorkflowSnapshot:
        if admin_authorized is not True:
            raise FeedbackAuthorizationError("feedback_local_admin_required")
        normalized_workflow = _identifier(
            workflow_id,
            code="feedback_workflow_id_invalid",
        )
        exact_guidance = _body_text(
            guidance,
            code="feedback_guidance_invalid",
            maximum=_GUIDANCE_MAX_CHARS,
        )
        privacy = self._privacy_review(privacy_review)
        records, generation = self._records_with_generation()
        correction_pair = self._correction(normalized_workflow, records)
        if correction_pair is None:
            raise FeedbackConflictError("feedback_source_deleted")
        correction_record, correction = correction_pair
        if correction.get("route") != "improvement" or correction.get(
            "actionable"
        ) is not True:
            raise FeedbackAuthorizationError("feedback_route_not_actionable")
        self._assert_guidance_independent(exact_guidance, correction)
        source_candidate = self._source_candidate(normalized_workflow, records)
        if source_candidate is None:
            version_id = f"fgv-{uuid.uuid4().hex}"
            now = self._now()
            candidate_body = {
                "schema": FEEDBACK_SOURCE_CANDIDATE_SCHEMA,
                "workflowId": normalized_workflow,
                "versionId": version_id,
                "category": correction["category"],
                "candidateGuidance": correction["correction"],
                "createdAt": _utc_iso(now),
                "state": "source_bound_candidate",
            }
            try:
                self.archive.append_derived_record(
                    surface=str(correction_record.surface),
                    record_type="feedback_source_candidate",
                    body=_canonical_json(candidate_body),
                    started_at=now,
                    ended_at=now,
                    parent_ids=(correction_record.record_id,),
                    idempotency_key=f"feedback-source-candidate:{normalized_workflow}",
                    record_id=(
                        "fsc-"
                        + hashlib.sha256(normalized_workflow.encode("utf-8")).hexdigest()[:48]
                    ),
                    expected_generation=generation,
                    now=now,
                )
            except ArchiveStaleEvent:
                raise FeedbackConflictError("feedback_source_generation_stale") from None
        records, generation = self._records_with_generation()
        current_correction = self._correction(normalized_workflow, records)
        source_candidate = self._source_candidate(normalized_workflow, records)
        if current_correction is None or source_candidate is None:
            raise FeedbackConflictError("feedback_source_deleted")
        correction_record, correction = current_correction
        _, candidate_body = source_candidate
        version_id = _identifier(
            candidate_body["versionId"],
            code="feedback_version_id_invalid",
        )
        if (
            candidate_body.get("category") != correction.get("category")
            or candidate_body.get("candidateGuidance") != correction.get("correction")
        ):
            raise FeedbackIntegrityError("feedback_source_candidate_invalid")
        existing_versions = {
            str(_body(record, FEEDBACK_GUIDANCE_SCHEMA, _GUIDANCE_FIELDS)["versionId"]): record
            for record in self._of_type(records, "feedback_independent_version")
        }
        requested_ancestors = tuple(
            dict.fromkeys(
                _identifier(value, code="feedback_ancestor_version_invalid")
                for value in ancestor_version_ids
            )
        )
        active = self._active_version_id(records)
        expected_ancestors = (
            () if active == BASE_GUIDANCE_VERSION_ID else (active,)
        )
        if requested_ancestors and requested_ancestors != expected_ancestors:
            raise FeedbackConflictError("feedback_candidate_parent_stale")
        requested_ancestors = expected_ancestors
        if len(requested_ancestors) > 1 or any(
            ancestor == version_id or ancestor not in existing_versions
            for ancestor in requested_ancestors
        ):
            raise FeedbackImprovementError("feedback_ancestor_version_invalid")
        quarantined = self._quarantined_version_ids(records)
        if any(ancestor in quarantined for ancestor in requested_ancestors):
            raise FeedbackAuthorizationError("feedback_ancestor_quarantined")
        guidance_digest = hashlib.sha256(exact_guidance.encode("utf-8")).hexdigest()
        existing = existing_versions.get(version_id)
        if existing is not None:
            payload = _body(existing, FEEDBACK_GUIDANCE_SCHEMA, _GUIDANCE_FIELDS)
            if (
                payload.get("guidanceDigest") != guidance_digest
                or payload.get("guidance") != exact_guidance
                or tuple(payload.get("ancestorVersionIds") or ())
                != requested_ancestors
            ):
                raise FeedbackConflictError("feedback_generalization_changed")
            return self.workflow(normalized_workflow)
        now = self._now()
        version_body = {
            "schema": FEEDBACK_GUIDANCE_SCHEMA,
            "versionId": version_id,
            "category": correction["category"],
            "guidance": exact_guidance,
            "guidanceDigest": guidance_digest,
            "ancestorVersionIds": list(requested_ancestors),
            "privacyReview": privacy,
            "createdAt": _utc_iso(now),
            "state": "independent_candidate",
        }
        try:
            self.archive.append_system_record(
                record_type="feedback_independent_version",
                body=_canonical_json(version_body),
                started_at=now,
                ended_at=now,
                idempotency_key=f"feedback-independent-version:{version_id}",
                record_id=version_id,
                expected_generation=generation,
                now=now,
            )
        except ArchiveStaleEvent:
            raise FeedbackConflictError("feedback_source_generation_stale") from None
        return self.workflow(normalized_workflow)

    def _workflow_for_version(
        self,
        version_id: str,
        records: Iterable[ArchiveRecord] | None = None,
    ) -> str:
        source = tuple(records) if records is not None else self._records()
        workflows: list[str] = []
        for record in self._of_type(source, "feedback_source_candidate"):
            payload = _body(
                record,
                FEEDBACK_SOURCE_CANDIDATE_SCHEMA,
                _SOURCE_CANDIDATE_FIELDS,
            )
            if payload.get("versionId") == version_id:
                workflows.append(str(payload["workflowId"]))
        if len(workflows) != 1 or self._correction(workflows[0], source) is None:
            raise FeedbackConflictError("feedback_source_deleted")
        return workflows[0]

    def _latest_evaluation(
        self,
        version_id: str,
        records: Iterable[ArchiveRecord],
    ) -> tuple[ArchiveRecord, dict[str, Any]] | None:
        result: tuple[ArchiveRecord, dict[str, Any]] | None = None
        for record in self._of_type(records, "feedback_evaluation"):
            payload = _body(record, FEEDBACK_EVALUATION_SCHEMA, _EVALUATION_FIELDS)
            if payload.get("versionId") == version_id:
                result = (record, payload)
        return result

    def _latest_approval(
        self,
        version_id: str,
        records: Iterable[ArchiveRecord],
    ) -> tuple[ArchiveRecord, dict[str, Any]] | None:
        result: tuple[ArchiveRecord, dict[str, Any]] | None = None
        for record in self._of_type(records, "feedback_approval"):
            payload = _body(record, FEEDBACK_APPROVAL_SCHEMA, _APPROVAL_FIELDS)
            if payload.get("versionId") == version_id:
                result = (record, payload)
        return result

    def _latest_canary(
        self,
        version_id: str,
        records: Iterable[ArchiveRecord],
        *,
        phase: str,
    ) -> tuple[ArchiveRecord, dict[str, Any]] | None:
        result: tuple[ArchiveRecord, dict[str, Any]] | None = None
        for record in self._of_type(records, "feedback_canary"):
            payload = _body(record, FEEDBACK_CANARY_SCHEMA, _CANARY_FIELDS)
            if payload.get("versionId") == version_id and payload.get("phase") == phase:
                result = (record, payload)
        return result

    def _workflow_from_records(
        self,
        normalized: str,
        records: tuple[ArchiveRecord, ...],
    ) -> FeedbackWorkflowSnapshot:
        correction_pair = self._correction(normalized, records)
        active = self._active_version_id(records)
        if correction_pair is None:
            return FeedbackWorkflowSnapshot(
                workflow_id=normalized,
                state="revoked",
                category=None,
                route="deleted",
                actionable=False,
                source_record_id=None,
                version_id=None,
                active_version_id=active,
                deletion_states=FEEDBACK_DELETION_STATES,
            )
        _, correction = correction_pair
        source_candidate = self._source_candidate(normalized, records)
        version_id = (
            None
            if source_candidate is None
            else str(source_candidate[1]["versionId"])
        )
        state = "routed"
        if correction.get("route") in {
            "identity_review",
            "human_engineering_required",
            "review_only",
        }:
            state = str(correction["route"])
        elif source_candidate is not None:
            state = "source_bound_candidate"
            try:
                self._version(str(version_id), records)
            except FeedbackImprovementError as exc:
                if exc.code != "feedback_version_missing":
                    raise
            else:
                state = "independent_candidate"
                evaluation = self._latest_evaluation(str(version_id), records)
                if evaluation is not None and evaluation[1].get("passed") is True:
                    state = "awaiting_approval"
                approval = self._latest_approval(str(version_id), records)
                if approval is not None:
                    state = "approval_granted"
                if self._latest_canary(str(version_id), records, phase="running"):
                    state = "canary_running"
                if self._latest_canary(str(version_id), records, phase="passed"):
                    state = "canary_passed"
                if active == version_id:
                    state = "active"
                if str(version_id) in self._quarantined_version_ids(records):
                    state = "revoked"
        return FeedbackWorkflowSnapshot(
            workflow_id=normalized,
            state=state,
            category=str(correction["category"]),
            route=str(correction["route"]),
            actionable=bool(correction["actionable"]),
            source_record_id=str(correction["sourceRecordId"]),
            version_id=version_id,
            active_version_id=active,
        )

    def workflow(self, workflow_id: str) -> FeedbackWorkflowSnapshot:
        normalized = _identifier(
            workflow_id,
            code="feedback_workflow_id_invalid",
        )
        records = self._records()
        return self._workflow_from_records(normalized, records)

    def workflows(self, *, limit: int = 100) -> tuple[FeedbackWorkflowSnapshot, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FeedbackImprovementError("feedback_workflow_limit_invalid")
        records = self._records()
        workflow_ids: list[str] = []
        for record in self._of_type(records, "feedback_correction"):
            payload = _body(record, FEEDBACK_CORRECTION_SCHEMA, _CORRECTION_FIELDS)
            workflow_ids.append(str(payload["workflowId"]))
        return tuple(
            self._workflow_from_records(value, records)
            for value in reversed(workflow_ids[-limit:])
        )

    @staticmethod
    def _default_report_gate(
        report: Mapping[str, Any],
        *,
        eval_run_id: str,
        baseline_contract_digest: str,
        candidate_contract_digest: str,
    ) -> bool:
        try:
            from tools.task_agent_eval import report_gate_passed
        except (ImportError, ModuleNotFoundError):
            return False
        return bool(
            report_gate_passed(
                report,
                eval_run_id=eval_run_id,
                baseline_contract_digest=baseline_contract_digest,
                candidate_contract_digest=candidate_contract_digest,
            )
        )

    def record_evaluation(
        self,
        *,
        version_id: str,
        report: Mapping[str, Any],
        eval_run_id: str,
        baseline_contract_digest: str,
        candidate_contract_digest: str,
        admin_authorized: bool,
    ) -> FeedbackWorkflowSnapshot:
        if admin_authorized is not True:
            raise FeedbackAuthorizationError("feedback_local_admin_required")
        normalized_version = _identifier(
            version_id,
            code="feedback_version_id_invalid",
        )
        normalized_run = _identifier(
            eval_run_id,
            code="feedback_eval_run_id_invalid",
            maximum=32,
        )
        baseline_digest = _identifier(
            baseline_contract_digest,
            code="feedback_eval_contract_invalid",
            maximum=64,
        )
        candidate_digest = _identifier(
            candidate_contract_digest,
            code="feedback_eval_contract_invalid",
            maximum=64,
        )
        records, generation = self._records_with_generation()
        workflow_id = self._workflow_for_version(normalized_version, records)
        self._require_candidate_parent_current(normalized_version, records)
        _, version = self._version(normalized_version, records)
        gate = self._evaluation_gate or self._default_report_gate
        if not gate(
            report,
            eval_run_id=normalized_run,
            baseline_contract_digest=baseline_digest,
            candidate_contract_digest=candidate_digest,
        ):
            raise FeedbackAuthorizationError("feedback_evaluation_gate_failed")
        try:
            owner = report["owner"]
            binding = report["binding"]
            candidate_guidance = binding["candidate"]["guidance"]
            baseline_guidance = binding["baseline"]["guidance"]
            aggregate = report["aggregate"]
            suite_version = owner["suiteVersion"]
        except (KeyError, TypeError):
            raise FeedbackIntegrityError("feedback_evaluation_report_invalid") from None
        active = self._active_guidance_from_records(records)
        if (
            candidate_guidance
            != {
                "version": normalized_version,
                "digest": version["guidanceDigest"],
            }
            or baseline_guidance
            != {
                "version": active.version_id,
                "digest": active.guidance_digest,
            }
            or not isinstance(aggregate, Mapping)
            or suite_version != self._task_evaluator_version
        ):
            raise FeedbackConflictError("feedback_evaluation_binding_stale")
        existing = self._latest_evaluation(normalized_version, records)
        if existing is not None:
            if existing[1].get("evalRunId") != normalized_run:
                raise FeedbackConflictError("feedback_evaluation_already_recorded")
            return self.workflow(workflow_id)
        now = self._now()
        body = {
            "schema": FEEDBACK_EVALUATION_SCHEMA,
            "versionId": normalized_version,
            "guidanceDigest": version["guidanceDigest"],
            "evalRunId": normalized_run,
            "suiteVersion": suite_version,
            "baselineContractDigest": baseline_digest,
            "candidateContractDigest": candidate_digest,
            "aggregate": dict(aggregate),
            "passed": True,
            "createdAt": _utc_iso(now),
            "state": "eval_passed",
        }
        try:
            self.archive.append_system_record(
                record_type="feedback_evaluation",
                body=_canonical_json(body),
                started_at=now,
                ended_at=now,
                parent_ids=(normalized_version,),
                idempotency_key=f"feedback-evaluation:{normalized_run}",
                record_id=f"fge-{normalized_run}",
                expected_generation=generation,
                now=now,
            )
        except ArchiveStaleEvent:
            raise FeedbackConflictError("feedback_source_generation_stale") from None
        return self.workflow(workflow_id)

    def action_binding(
        self,
        *,
        action: str,
        version_id: str,
        contract_version: str | None = None,
        evaluator_version: str | None = None,
        reason: str | None = None,
    ) -> FeedbackActionBinding:
        normalized_action = _text(
            action,
            code="feedback_action_invalid",
            maximum=16,
        )
        if normalized_action not in {"approve", "activate", "rollback", "revoke"}:
            raise FeedbackImprovementError("feedback_action_invalid")
        normalized_version = _identifier(
            version_id,
            code="feedback_version_id_invalid",
        )
        records, generation = self._records_with_generation()
        active = self._active_version_id(records)
        try:
            _, version = (
                self._version(normalized_version, records)
                if normalized_action == "revoke"
                else self._require_version_admissible(
                    normalized_version,
                    records,
                )
            )
        except FeedbackImprovementError as exc:
            if exc.code == "feedback_version_missing":
                raise FeedbackAuthorizationError(
                    "feedback_version_unavailable"
                ) from None
            raise
        detail: dict[str, Any] = {
            "action": normalized_action,
            "versionId": normalized_version,
            "guidanceDigest": version["guidanceDigest"],
            "activeVersionId": active,
            "archiveGeneration": generation,
        }
        if normalized_action == "approve":
            self._workflow_for_version(normalized_version, records)
            self._require_candidate_parent_current(normalized_version, records)
            evaluation = self._latest_evaluation(normalized_version, records)
            if evaluation is None or evaluation[1].get("passed") is not True:
                raise FeedbackAuthorizationError("feedback_evaluation_required")
            detail["evaluationRecordId"] = evaluation[0].record_id
            detail["evalRunId"] = evaluation[1]["evalRunId"]
        elif normalized_action == "activate":
            self._workflow_for_version(normalized_version, records)
            self._require_candidate_parent_current(normalized_version, records)
            approval = self._latest_approval(normalized_version, records)
            canary = self._latest_canary(normalized_version, records, phase="passed")
            if approval is None or canary is None:
                raise FeedbackAuthorizationError("feedback_activation_gate_failed")
            detail["approvalId"] = approval[1]["approvalId"]
            detail["canaryRunId"] = canary[1]["canaryRunId"]
        elif normalized_action == "rollback":
            if active != normalized_version:
                raise FeedbackConflictError("feedback_active_version_changed")
            failure = self._latest_failure(normalized_version, records)
            if failure is None:
                raise FeedbackAuthorizationError("feedback_failure_receipt_required")
            if (
                contract_version is None
                or evaluator_version is None
                or failure[1]["contractVersion"] != contract_version
                or failure[1]["evaluatorVersion"] != evaluator_version
            ):
                raise FeedbackConflictError("feedback_failure_contract_stale")
            detail["failureId"] = failure[1]["failureId"]
            detail["contractVersion"] = contract_version
            detail["evaluatorVersion"] = evaluator_version
            detail["targetVersionId"] = self._rollback_target(
                normalized_version,
                records,
            )
        else:
            normalized_reason = self._revocation_reason(reason)
            detail["reason"] = normalized_reason
            detail["affectedVersionIds"] = list(
                self._affected_version_ids(normalized_version, records)
            )
        return FeedbackActionBinding(
            action=normalized_action,
            version_id=normalized_version,
            active_version_id=active,
            archive_generation=generation,
            binding_digest=_digest(detail),
        )

    @staticmethod
    def _require_action_binding(
        current: FeedbackActionBinding,
        *,
        binding_digest: str,
        expected_generation: int,
    ) -> None:
        if (
            type(expected_generation) is not int
            or expected_generation != current.archive_generation
            or not isinstance(binding_digest, str)
            or not hmac.compare_digest(binding_digest, current.binding_digest)
        ):
            raise FeedbackConflictError("feedback_action_binding_stale")

    def grant_approval(
        self,
        *,
        version_id: str,
        approval_id: str,
        binding_digest: str,
        expected_generation: int,
        admin_authorized: bool,
        step_up_consumed: bool,
    ) -> FeedbackWorkflowSnapshot:
        if admin_authorized is not True or step_up_consumed is not True:
            raise FeedbackAuthorizationError("feedback_step_up_required")
        normalized_version = _identifier(
            version_id,
            code="feedback_version_id_invalid",
        )
        normalized_approval = _identifier(
            approval_id,
            code="feedback_approval_id_invalid",
        )
        current = self.action_binding(action="approve", version_id=normalized_version)
        self._require_action_binding(
            current,
            binding_digest=binding_digest,
            expected_generation=expected_generation,
        )
        records = self._records()
        workflow_id = self._workflow_for_version(normalized_version, records)
        if self._latest_approval(normalized_version, records) is not None:
            raise FeedbackConflictError("feedback_approval_already_used")
        _, version = self._version(normalized_version, records)
        evaluation = self._latest_evaluation(normalized_version, records)
        assert evaluation is not None
        now = self._now()
        body = {
            "schema": FEEDBACK_APPROVAL_SCHEMA,
            "approvalId": normalized_approval,
            "versionId": normalized_version,
            "guidanceDigest": version["guidanceDigest"],
            "evalRunId": evaluation[1]["evalRunId"],
            "bindingDigest": current.binding_digest,
            "capability": "admin.control",
            "oneUse": True,
            "grantedAt": _utc_iso(now),
            "state": "approval_granted",
        }
        self.archive.append_system_record(
            record_type="feedback_approval",
            body=_canonical_json(body),
            started_at=now,
            ended_at=now,
            parent_ids=(normalized_version,),
            idempotency_key=f"feedback-approval:{normalized_approval}",
            record_id=f"fga-{hashlib.sha256(normalized_approval.encode()).hexdigest()[:48]}",
            expected_generation=current.archive_generation,
            now=now,
        )
        return self.workflow(workflow_id)

    def _latest_failure(
        self,
        version_id: str,
        records: Iterable[ArchiveRecord],
    ) -> tuple[ArchiveRecord, dict[str, Any]] | None:
        used_failure_ids = {
            str(_body(record, FEEDBACK_ROLLBACK_SCHEMA, _ROLLBACK_FIELDS)["failureId"])
            for record in self._of_type(records, "feedback_rollback")
        }
        result: tuple[ArchiveRecord, dict[str, Any]] | None = None
        for record in self._of_type(records, "feedback_failure"):
            payload = _body(record, FEEDBACK_FAILURE_SCHEMA, _FAILURE_FIELDS)
            if (
                payload.get("versionId") == version_id
                and payload.get("failureId") not in used_failure_ids
            ):
                result = (record, payload)
        return result

    def record_active_failure(
        self,
        *,
        version_id: str,
        failure_id: str,
        task_id: str,
        source_record_id: str,
        contract_version: str,
        evaluator_version: str,
        failure_code: str,
        principal_id: str,
        ledger_generation: int,
        authorized: bool,
        ledger_integrity_current: bool,
    ) -> str:
        if authorized is not True or ledger_integrity_current is not True:
            raise FeedbackAuthorizationError("feedback_failure_authority_required")
        normalized_version = _identifier(
            version_id,
            code="feedback_version_id_invalid",
        )
        normalized_failure = _identifier(
            failure_id,
            code="feedback_failure_id_invalid",
        )
        normalized_task = _identifier(task_id, code="feedback_task_id_invalid")
        normalized_source = _identifier(
            source_record_id,
            code="feedback_source_record_id_invalid",
            maximum=64,
        )
        normalized_contract = _identifier(
            contract_version,
            code="feedback_contract_version_invalid",
            maximum=80,
        )
        normalized_evaluator = _identifier(
            evaluator_version,
            code="feedback_evaluator_version_invalid",
            maximum=80,
        )
        normalized_code = _text(
            failure_code,
            code="feedback_failure_code_invalid",
            maximum=64,
        )
        normalized_principal = _identifier(
            principal_id,
            code="feedback_principal_id_invalid",
            maximum=64,
        )
        if normalized_code not in FEEDBACK_FIXED_FAILURE_CODES:
            raise FeedbackAuthorizationError("feedback_failure_not_fixed")
        if (
            normalized_contract != self._task_contract_version
            or normalized_evaluator != self._task_evaluator_version
        ):
            raise FeedbackConflictError("feedback_failure_contract_stale")
        records, generation = self._records_with_generation()
        if self._active_version_id(records) != normalized_version:
            raise FeedbackConflictError("feedback_active_version_changed")
        if type(ledger_generation) is not int or ledger_generation != generation:
            raise FeedbackConflictError("feedback_ledger_generation_stale")
        _, version = self._version(normalized_version, records)
        source = self.archive.read_record_admin(
            authorized=True,
            record_id=normalized_source,
        )
        if (
            source is None
            or source.record_type != "task_result"
            or source.status != "active"
            or self.archive.generation != generation
        ):
            raise FeedbackConflictError("feedback_task_binding_stale")
        now = self._now()
        body = {
            "schema": FEEDBACK_FAILURE_SCHEMA,
            "failureId": normalized_failure,
            "versionId": normalized_version,
            "guidanceDigest": version["guidanceDigest"],
            "taskId": normalized_task,
            "contractVersion": normalized_contract,
            "evaluatorVersion": normalized_evaluator,
            "failureCode": normalized_code,
            "principalId": normalized_principal,
            "ledgerGeneration": ledger_generation,
            "observedAt": _utc_iso(now),
            "state": "fixed_failure_observed",
        }
        self.archive.append_system_record(
            record_type="feedback_failure",
            body=_canonical_json(body),
            started_at=now,
            ended_at=now,
            parent_ids=(normalized_source,),
            idempotency_key=f"feedback-failure:{normalized_failure}",
            record_id=f"fgf-{hashlib.sha256(normalized_failure.encode()).hexdigest()[:48]}",
            expected_generation=ledger_generation,
            now=now,
        )
        return normalized_failure

    def _version_verified_for_rollback(
        self,
        version_id: str,
        records: tuple[ArchiveRecord, ...],
    ) -> bool:
        if version_id == BASE_GUIDANCE_VERSION_ID:
            return True
        try:
            _, version = self._require_version_admissible(version_id, records)
        except FeedbackImprovementError:
            return False
        evaluation = self._latest_evaluation(version_id, records)
        approval = self._latest_approval(version_id, records)
        canary = self._latest_canary(version_id, records, phase="passed")
        canary_aggregate = canary[1].get("aggregate") if canary is not None else None
        activated = any(
            record.record_type == "feedback_activation"
            and (
                activation := _body(
                    record,
                    FEEDBACK_ACTIVATION_SCHEMA,
                    _ACTIVATION_FIELDS,
                )
            ).get("versionId")
            == version_id
            and activation.get("guidanceDigest") == version.get("guidanceDigest")
            and approval is not None
            and activation.get("approvalId") == approval[1].get("approvalId")
            and canary is not None
            and activation.get("canaryRunId") == canary[1].get("canaryRunId")
            for record in records
        )
        return bool(
            evaluation is not None
            and evaluation[1].get("passed") is True
            and evaluation[1].get("suiteVersion") == self._task_evaluator_version
            and evaluation[1].get("guidanceDigest") == version.get("guidanceDigest")
            and approval is not None
            and approval[1].get("guidanceDigest") == version.get("guidanceDigest")
            and approval[1].get("evalRunId") == evaluation[1].get("evalRunId")
            and canary is not None
            and canary[1].get("guidanceDigest") == version.get("guidanceDigest")
            and canary[1].get("approvalId") == approval[1].get("approvalId")
            and isinstance(canary_aggregate, Mapping)
            and canary_aggregate.get("contractVersion")
            == self._task_contract_version
            and canary_aggregate.get("evaluatorVersion")
            == self._task_evaluator_version
            and activated
        )

    def _rollback_target(
        self,
        current_version_id: str,
        records: Iterable[ArchiveRecord],
    ) -> str:
        source = tuple(records)
        history: list[str] = []
        for record in source:
            if record.record_type == "feedback_activation":
                history.append(
                    str(
                        _body(
                            record,
                            FEEDBACK_ACTIVATION_SCHEMA,
                            _ACTIVATION_FIELDS,
                        )["versionId"]
                    )
                )
            elif record.record_type == "feedback_rollback":
                history.append(
                    str(
                        _body(
                            record,
                            FEEDBACK_ROLLBACK_SCHEMA,
                            _ROLLBACK_FIELDS,
                        )["targetVersionId"]
                    )
                )
        candidates: list[str] = []
        for version_id in reversed(history):
            if version_id != current_version_id and version_id not in candidates:
                candidates.append(version_id)
            if len(candidates) == 2:
                break
        for version_id in candidates:
            if self._version_verified_for_rollback(version_id, source):
                return version_id
        return BASE_GUIDANCE_VERSION_ID

    def rollback(
        self,
        *,
        version_id: str,
        contract_version: str,
        evaluator_version: str,
        binding_digest: str,
        expected_generation: int,
        admin_authorized: bool,
        step_up_consumed: bool,
    ) -> dict[str, Any]:
        if admin_authorized is not True or step_up_consumed is not True:
            raise FeedbackAuthorizationError("feedback_step_up_required")
        normalized_version = _identifier(
            version_id,
            code="feedback_version_id_invalid",
        )
        current = self.action_binding(
            action="rollback",
            version_id=normalized_version,
            contract_version=contract_version,
            evaluator_version=evaluator_version,
        )
        self._require_action_binding(
            current,
            binding_digest=binding_digest,
            expected_generation=expected_generation,
        )
        records = self._records()
        failure = self._latest_failure(normalized_version, records)
        assert failure is not None
        target = self._rollback_target(normalized_version, records)
        if target == BASE_GUIDANCE_VERSION_ID:
            target_digest = BASE_GUIDANCE_DIGEST
        else:
            _, target_version = self._version(target, records)
            target_digest = str(target_version["guidanceDigest"])
        rollback_id = f"fgr-{uuid.uuid4().hex}"
        now = self._now()
        body = {
            "schema": FEEDBACK_ROLLBACK_SCHEMA,
            "rollbackId": rollback_id,
            "failureId": failure[1]["failureId"],
            "fromVersionId": normalized_version,
            "targetVersionId": target,
            "targetGuidanceDigest": target_digest,
            "bindingDigest": current.binding_digest,
            "rolledBackAt": _utc_iso(now),
            "state": "rolled_back",
        }
        self.archive.append_system_record(
            record_type="feedback_rollback",
            body=_canonical_json(body),
            started_at=now,
            ended_at=now,
            idempotency_key=f"feedback-rollback:{failure[1]['failureId']}",
            record_id=rollback_id,
            expected_generation=current.archive_generation,
            now=now,
        )
        return {
            "schema": "evelyn.feedback-rollback-public.v1",
            "state": "rolled_back",
            "fromVersionId": normalized_version,
            "activeVersionId": target,
            "contentFree": True,
        }

    @staticmethod
    def _revocation_reason(reason: Any) -> str:
        normalized_reason = _text(
            reason,
            code="feedback_revocation_reason_invalid",
            maximum=64,
        )
        if normalized_reason not in {
            "source_dependency_detected",
            "privacy_independence_invalid",
            "operator_revoked",
            "canary_failed",
        }:
            raise FeedbackImprovementError("feedback_revocation_reason_invalid")
        return normalized_reason

    def _affected_version_ids(
        self,
        version_id: str,
        records: Iterable[ArchiveRecord],
    ) -> tuple[str, ...]:
        source = tuple(records)
        self._version(version_id, source)
        versions: dict[str, dict[str, Any]] = {}
        for record in self._of_type(source, "feedback_independent_version"):
            payload = _body(record, FEEDBACK_GUIDANCE_SCHEMA, _GUIDANCE_FIELDS)
            versions[str(payload["versionId"])] = payload
        affected = {version_id}
        changed = True
        while changed:
            changed = False
            for candidate, payload in versions.items():
                if candidate in affected:
                    continue
                if any(
                    ancestor in affected
                    for ancestor in payload.get("ancestorVersionIds") or ()
                ):
                    affected.add(candidate)
                    changed = True
        return tuple(sorted(affected))

    def _append_revocations(
        self,
        *,
        version_id: str,
        reason: str,
        records: Iterable[ArchiveRecord] | None = None,
        expected_generation: int | None = None,
    ) -> tuple[str, ...]:
        normalized_version = _identifier(
            version_id,
            code="feedback_version_id_invalid",
        )
        normalized_reason = self._revocation_reason(reason)
        source = tuple(records) if records is not None else self._records()
        affected = self._affected_version_ids(normalized_version, source)
        already = self._revoked_version_ids(source)
        appended: list[str] = []
        next_generation = (
            self.archive.generation
            if expected_generation is None
            else expected_generation
        )
        for candidate in affected:
            if candidate in already:
                continue
            now = self._now()
            revocation_id = (
                "fgrv-"
                + hashlib.sha256(
                    f"{candidate}\n{normalized_version}\n{normalized_reason}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:44]
            )
            body = {
                "schema": FEEDBACK_REVOCATION_SCHEMA,
                "revocationId": revocation_id,
                "versionId": candidate,
                "reason": normalized_reason,
                "descendantOfVersionId": (
                    None if candidate == normalized_version else normalized_version
                ),
                "revokedAt": _utc_iso(now),
                "state": "revoked",
            }
            self.archive.append_system_record(
                record_type="feedback_revocation",
                body=_canonical_json(body),
                started_at=now,
                ended_at=now,
                idempotency_key=f"feedback-revocation:{revocation_id}",
                record_id=revocation_id,
                expected_generation=next_generation,
                now=now,
            )
            appended.append(candidate)
            next_generation += 1
        return tuple(appended)

    def revoke_version(
        self,
        *,
        version_id: str,
        reason: str,
        binding_digest: str,
        expected_generation: int,
        admin_authorized: bool,
        step_up_consumed: bool,
    ) -> tuple[str, ...]:
        if admin_authorized is not True or step_up_consumed is not True:
            raise FeedbackAuthorizationError("feedback_step_up_required")
        normalized_version = _identifier(
            version_id,
            code="feedback_version_id_invalid",
        )
        normalized_reason = self._revocation_reason(reason)
        current = self.action_binding(
            action="revoke",
            version_id=normalized_version,
            reason=normalized_reason,
        )
        self._require_action_binding(
            current,
            binding_digest=binding_digest,
            expected_generation=expected_generation,
        )
        records, generation = self._records_with_generation()
        if generation != current.archive_generation:
            raise FeedbackConflictError("feedback_action_binding_stale")
        return self._append_revocations(
            version_id=normalized_version,
            reason=normalized_reason,
            records=records,
            expected_generation=current.archive_generation,
        )

    def begin_canary(
        self,
        *,
        version_id: str,
        canary_run_id: str,
        admin_authorized: bool,
    ) -> FeedbackWorkflowSnapshot:
        if admin_authorized is not True:
            raise FeedbackAuthorizationError("feedback_local_admin_required")
        normalized_version = _identifier(
            version_id,
            code="feedback_version_id_invalid",
        )
        normalized_run = _identifier(
            canary_run_id,
            code="feedback_canary_run_id_invalid",
        )
        records, generation = self._records_with_generation()
        workflow_id = self._workflow_for_version(normalized_version, records)
        self._require_candidate_parent_current(normalized_version, records)
        _, version = self._require_version_admissible(
            normalized_version, records
        )
        approval = self._latest_approval(normalized_version, records)
        if approval is None:
            raise FeedbackAuthorizationError("feedback_approval_required")
        if self._latest_canary(normalized_version, records, phase="failed"):
            raise FeedbackAuthorizationError("feedback_canary_failed")
        current_pointer = self.running_canary_pointer(
            local_admin=True,
            read_only=True,
            grounded_task=True,
        )
        if current_pointer is not None:
            raise FeedbackConflictError("feedback_canary_already_started")
        now = self._now()
        body = {
            "schema": FEEDBACK_CANARY_SCHEMA,
            "canaryRunId": normalized_run,
            "versionId": normalized_version,
            "guidanceDigest": version["guidanceDigest"],
            "approvalId": approval[1]["approvalId"],
            "phase": "running",
            "aggregate": None,
            "recordedAt": _utc_iso(now),
            "state": "canary_running",
        }
        self.archive.append_system_record(
            record_type="feedback_canary",
            body=_canonical_json(body),
            started_at=now,
            ended_at=now,
            parent_ids=(normalized_version,),
            idempotency_key=f"feedback-canary-running:{normalized_run}",
            record_id=f"fgc-run-{hashlib.sha256(normalized_run.encode()).hexdigest()[:44]}",
            expected_generation=generation,
            now=now,
        )
        return self.workflow(workflow_id)


    def _validate_canary_aggregate(
        self,
        aggregate: Mapping[str, Any],
        *,
        version_id: str,
        guidance_digest: str,
    ) -> dict[str, Any]:
        expected_fields = {
            "schema",
            "candidateVersionId",
            "guidanceDigest",
            "contractVersion",
            "evaluatorVersion",
            "sampleCount",
            "passedCount",
            "unauthorizedEffectCount",
            "privacyLeakageCount",
            "structuralFailureCount",
            "taskFailureCount",
        }
        if (
            not isinstance(aggregate, Mapping)
            or set(aggregate) != expected_fields
            or aggregate.get("schema") != FEEDBACK_CANARY_AGGREGATE_SCHEMA
            or aggregate.get("candidateVersionId") != version_id
            or aggregate.get("guidanceDigest") != guidance_digest
            or aggregate.get("sampleCount") != 10
            or type(aggregate.get("passedCount")) is not int
            or not 0 <= aggregate["passedCount"] <= 10
            or aggregate.get("contractVersion")
            != self._task_contract_version
            or aggregate.get("evaluatorVersion")
            != self._task_evaluator_version
            or any(
                type(aggregate.get(key)) is not int
                or not 0 <= aggregate[key] <= 10
                for key in (
                    "unauthorizedEffectCount",
                    "privacyLeakageCount",
                    "structuralFailureCount",
                    "taskFailureCount",
                )
            )
            or not isinstance(aggregate.get("contractVersion"), str)
            or not isinstance(aggregate.get("evaluatorVersion"), str)
        ):
            raise FeedbackAuthorizationError("feedback_canary_aggregate_invalid")
        return dict(aggregate)

    def record_canary(
        self,
        *,
        version_id: str,
        canary_run_id: str,
        aggregate: Mapping[str, Any],
        admin_authorized: bool,
    ) -> FeedbackWorkflowSnapshot:
        if admin_authorized is not True:
            raise FeedbackAuthorizationError("feedback_local_admin_required")
        normalized_version = _identifier(
            version_id,
            code="feedback_version_id_invalid",
        )
        normalized_run = _identifier(
            canary_run_id,
            code="feedback_canary_run_id_invalid",
        )
        records, generation = self._records_with_generation()
        workflow_id = self._workflow_for_version(normalized_version, records)
        self._require_candidate_parent_current(normalized_version, records)
        _, version = self._require_version_admissible(
            normalized_version, records
        )
        running = self._latest_canary(normalized_version, records, phase="running")
        if running is None or running[1].get("canaryRunId") != normalized_run:
            raise FeedbackConflictError("feedback_canary_binding_stale")
        if self._latest_canary(normalized_version, records, phase="passed") or self._latest_canary(
            normalized_version, records, phase="failed"
        ):
            raise FeedbackConflictError("feedback_canary_already_completed")
        normalized_aggregate = self._validate_canary_aggregate(
            aggregate,
            version_id=normalized_version,
            guidance_digest=str(version["guidanceDigest"]),
        )
        now = self._now()
        passed = bool(
            normalized_aggregate["passedCount"] == 10
            and all(
                normalized_aggregate[key] == 0
                for key in (
                    "unauthorizedEffectCount",
                    "privacyLeakageCount",
                    "structuralFailureCount",
                    "taskFailureCount",
                )
            )
        )
        phase = "passed" if passed else "failed"
        state = "canary_passed" if passed else "canary_failed"
        body = {
            "schema": FEEDBACK_CANARY_SCHEMA,
            "canaryRunId": normalized_run,
            "versionId": normalized_version,
            "guidanceDigest": version["guidanceDigest"],
            "approvalId": running[1]["approvalId"],
            "phase": phase,
            "aggregate": normalized_aggregate,
            "recordedAt": _utc_iso(now),
            "state": state,
        }
        self.archive.append_system_record(
            record_type="feedback_canary",
            body=_canonical_json(body),
            started_at=now,
            ended_at=now,
            parent_ids=(normalized_version,),
            idempotency_key=f"feedback-canary-{phase}:{normalized_run}",
            record_id=(
                f"fgc-{'pass' if passed else 'fail'}-"
                f"{hashlib.sha256(normalized_run.encode()).hexdigest()[:43]}"
            ),
            expected_generation=generation,
            now=now,
        )
        if not passed:
            self._append_revocations(
                version_id=normalized_version,
                reason="canary_failed",
                records=records,
                expected_generation=generation + 1,
            )
        return self.workflow(workflow_id)

    def activate(
        self,
        *,
        version_id: str,
        binding_digest: str,
        expected_generation: int,
        admin_authorized: bool,
    ) -> FeedbackWorkflowSnapshot:
        if admin_authorized is not True:
            raise FeedbackAuthorizationError("feedback_local_admin_required")
        normalized_version = _identifier(
            version_id,
            code="feedback_version_id_invalid",
        )
        current = self.action_binding(action="activate", version_id=normalized_version)
        self._require_action_binding(
            current,
            binding_digest=binding_digest,
            expected_generation=expected_generation,
        )
        records = self._records()
        workflow_id = self._workflow_for_version(normalized_version, records)
        _, version = self._version(normalized_version, records)
        approval = self._latest_approval(normalized_version, records)
        canary = self._latest_canary(normalized_version, records, phase="passed")
        assert approval is not None and canary is not None
        if any(
            record.record_type == "feedback_activation"
            and _body(record, FEEDBACK_ACTIVATION_SCHEMA, _ACTIVATION_FIELDS).get(
                "approvalId"
            )
            == approval[1]["approvalId"]
            for record in records
        ):
            raise FeedbackConflictError("feedback_approval_already_used")
        now = self._now()
        body = {
            "schema": FEEDBACK_ACTIVATION_SCHEMA,
            "versionId": normalized_version,
            "guidanceDigest": version["guidanceDigest"],
            "previousActiveVersionId": current.active_version_id,
            "approvalId": approval[1]["approvalId"],
            "canaryRunId": canary[1]["canaryRunId"],
            "bindingDigest": current.binding_digest,
            "activatedAt": _utc_iso(now),
            "state": "active",
        }
        self.archive.append_system_record(
            record_type="feedback_activation",
            body=_canonical_json(body),
            started_at=now,
            ended_at=now,
            parent_ids=(normalized_version,),
            idempotency_key=f"feedback-activation:{approval[1]['approvalId']}",
            record_id=f"fgx-{hashlib.sha256(str(approval[1]['approvalId']).encode()).hexdigest()[:48]}",
            expected_generation=current.archive_generation,
            now=now,
        )
        return self.workflow(workflow_id)


__all__ = [
    "BASE_GUIDANCE_DIGEST",
    "BASE_GUIDANCE_VERSION_ID",
    "CURRENT_TASK_CONTRACT_VERSION",
    "CURRENT_TASK_EVALUATOR_VERSION",
    "FEEDBACK_ACTIONABLE_CATEGORIES",
    "FEEDBACK_CANARY_AGGREGATE_SCHEMA",
    "FEEDBACK_CATEGORIES",
    "FEEDBACK_DELETION_STATES",
    "FEEDBACK_PRIVACY_REVIEW_SCHEMA",
    "FEEDBACK_WORKFLOW_STATES",
    "FeedbackActionBinding",
    "FeedbackAuthorizationError",
    "FeedbackConflictError",
    "FeedbackImprovementController",
    "FeedbackImprovementError",
    "FeedbackIntegrityError",
    "FeedbackWorkflowSnapshot",
    "GuidanceBinding",
    "RunningCanaryBinding",
]
