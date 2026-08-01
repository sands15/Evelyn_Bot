from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .autonomy_authorization import (
    ASSISTANT_AUTONOMY_ACTIONS,
    AUTONOMY_AUTHORIZATION_EVENT_SCHEMA,
    AUTONOMY_AUTHORIZATION_STATUS_SCHEMA,
    SUPPORTED_AUTONOMY_ACTIONS,
)
from .autonomy_outcome_evidence import (
    AUTONOMY_SUCCESS_STATUSES,
    expected_autonomy_evidence_codes,
)
from .minecraft_world_lease import MINECRAFT_WORLD_LEASE_EVENT_SCHEMA
from .minecraft_world_lease_contract import MINECRAFT_WORLD_LEASE_STATUS_SCHEMA
from .minecraft_action_contract import (
    MINECRAFT_ACTION_SPECS,
    MINECRAFT_ROUTE_ACTIONS,
)
from .mindcraft_world_effect import (
    MINDCRAFT_WORLD_EFFECT_EVENT_SCHEMA,
    MINDCRAFT_WORLD_EFFECT_STATUS_SCHEMA,
    MINDCRAFT_WORLD_EFFECT_EVIDENCE_CODE,
    validate_mindcraft_world_effect_status,
)
from .paths import get_runtime_artifacts_root
from .runtime_artifact_io import atomic_json_write


SESSION_SCHEMA = "autonomy_validation.session.v1"
EVENT_SCHEMA = "autonomy_validation.event.v1"
REPORT_SCHEMA = "autonomy_validation.report.v1"
SUITE_ID = "autonomy-p0.v1"
SESSION_TTL_SEC = 30 * 60
MAX_ATTEMPTS = 3
STATUS_MAX_AGE_SEC = 120.0
REPORT_MAX_AGE_DAYS = 30
REPORT_PRESERVE_NEWEST = 20
TERMINAL_STATES = frozenset({"passed", "failed", "aborted"})

MINECRAFT_ROUTE_BLOCKER = "minecraft_autonomy_route_unwired"
MINECRAFT_POSTCONDITION_BLOCKER = (
    "minecraft_postcondition_observer_unavailable"
)
MINECRAFT_PRODUCTION_BLOCKERS = (
    MINECRAFT_ROUTE_BLOCKER,
    MINECRAFT_POSTCONDITION_BLOCKER,
)

_SESSION_ID_RE = re.compile(r"autonomy-p0-[A-Za-z0-9_-]{8,96}\Z", re.ASCII)
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_\-.]{0,127}\Z", re.ASCII)
_HEX_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_EVENT_FILE_BYTES = 8 * 1024 * 1024
_MAX_EVENT_LINES = 20_000
_CLOCK_SKEW_SEC = 5.0
_PARTIAL_WRITE_GRACE_SEC = 2.0

_AUTH_EVENTS = frozenset(
    {
        "process_started",
        "grant_issued",
        "grant_revoked",
        "grant_expired",
        "action_authorized",
        "action_denied",
        "action_outcome",
    }
)
_LEASE_EVENTS = frozenset(
    {
        "process_started",
        "lease_issued",
        "lease_revoked",
        "runtime_start_verified",
        "runtime_stop_attempted",
        "runtime_stop_verified",
        "runtime_stop_failed",
        "goal_attempted",
        "goal_failed",
        "goal_verified",
        "action_dispatch_attempted",
        "action_dispatch_verified",
        "action_completed",
        "action_failed",
        "action_cancel_attempted",
        "action_cancel_verified",
        "action_cancel_failed",
    }
)
_WORLD_EFFECT_EVENTS = frozenset(
    {
        "process_started",
        "binding_armed",
        "telemetry_rejected",
        "effect_verified",
        "binding_disarmed",
        "audit_failed",
        "status_failed",
    }
)
_WORLD_EFFECT_EVENT_FIELDS = frozenset(
    {
        "schema",
        "eventId",
        "at",
        "event",
        "processNonce",
        "goalRunId",
        "actionRunId",
        "actionKey",
        "contractCode",
        "leaseId",
        "leaseProcessNonce",
        "producerNonce",
        "candidateSequence",
        "executionSequence",
        "errorCode",
        "evidenceCode",
        "postconditionCode",
        "autonomous",
        "relevant",
        "succeeded",
        "worldChanged",
        "goalProgress",
        "contentFree",
    }
)
_OWN_EVENTS = frozenset(
    {
        "session_started",
        "instruction_acknowledged",
        "machine_evidence_observed",
        "step_passed",
        "step_failed",
        "step_retry_started",
        "session_aborted",
        "session_expired",
        "session_passed",
    }
)

_STEP_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "01-explicit-grant",
        "kind": "authorization_grant",
        "instructionCode": "issue_explicit_autonomy_grant",
        "manualAcknowledgementRequired": True,
    },
    {
        "id": "02-assistant-action-authorized",
        "kind": "assistant_authorization",
        "instructionCode": "wait_for_assistant_action_authorization",
        "manualAcknowledgementRequired": False,
    },
    {
        "id": "03-assistant-outcome-verified",
        "kind": "assistant_outcome",
        "instructionCode": "wait_for_exact_assistant_outcome",
        "manualAcknowledgementRequired": False,
    },
    {
        "id": "04-world-lease-lifecycle",
        "kind": "world_lease",
        "instructionCode": "issue_explicit_minecraft_connect",
        "manualAcknowledgementRequired": True,
    },
    {
        "id": "05-world-postcondition",
        "kind": "world_postcondition",
        "instructionCode": "minecraft_route_and_postcondition_required",
        "manualAcknowledgementRequired": False,
    },
    {
        "id": "06-revoke-and-stop",
        "kind": "cleanup",
        "instructionCode": "revoke_authorization_and_stop_minecraft",
        "manualAcknowledgementRequired": True,
    },
)


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _safe_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if _SAFE_ID_RE.fullmatch(text) else ""


def _fingerprint(session_id: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(f"{session_id}:{text}".encode("utf-8")).hexdigest()


def _safe_json_object(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (json.JSONDecodeError, OSError, UnicodeError):
        return None, "corrupt"
    if not isinstance(payload, dict):
        return None, "corrupt"
    return payload, ""


@dataclass(frozen=True)
class AutonomyValidationPaths:
    artifacts_root: Path

    @property
    def root(self) -> Path:
        return self.artifacts_root / "autonomy_validation"

    @property
    def active(self) -> Path:
        return self.root / "active.json"

    @property
    def events(self) -> Path:
        return self.root / "events"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def authorization_status(self) -> Path:
        return self.artifacts_root / "autonomy_authorization" / "status.json"

    @property
    def authorization_events(self) -> Path:
        return self.artifacts_root / "autonomy_authorization" / "events"

    @property
    def world_lease_status(self) -> Path:
        return self.artifacts_root / "minecraft_world_lease" / "status.json"

    @property
    def world_lease_events(self) -> Path:
        return self.artifacts_root / "minecraft_world_lease" / "events"

    @property
    def world_effect_status(self) -> Path:
        return self.artifacts_root / "mindcraft_world_effect" / "status.json"

    @property
    def world_effect_events(self) -> Path:
        return self.artifacts_root / "mindcraft_world_effect" / "events"


def _new_step(definition: dict[str, Any], *, now: float) -> dict[str, Any]:
    return {
        **deepcopy(definition),
        "status": "pending",
        "attempt": 1,
        "attemptStartedAt": now,
        "acknowledged": False,
        "machineEvidenceObserved": False,
        "requirements": {},
        "errors": [],
    }


class AutonomyValidationManager:
    """Read-only observer for the autonomy P0 evidence chain.

    The manager only reads the authorization and Minecraft world-lease audit
    artifacts.  No method grants authority, starts a service, submits a goal,
    writes a host queue, or calls an effect API.  Its own artifacts contain
    fixed codes and booleans only; external identifiers are represented by
    session-salted fingerprints in the private recovery state.
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        now: Callable[[], float] = time.time,
        ttl_sec: float = SESSION_TTL_SEC,
        status_max_age_sec: float = STATUS_MAX_AGE_SEC,
    ) -> None:
        self.paths = AutonomyValidationPaths(
            Path(root or get_runtime_artifacts_root()).resolve()
        )
        self.now = now
        self.ttl_sec = max(1.0, float(ttl_sec))
        self.status_max_age_sec = max(1.0, float(status_max_age_sec))
        self._lock = threading.RLock()
        self._session: dict[str, Any] | None = None
        self._load_active()

    def _idle(self) -> dict[str, Any]:
        return {
            "schema": SESSION_SCHEMA,
            "sessionId": "",
            "suite": SUITE_ID,
            "state": "idle",
            "currentStep": {},
            "attempt": 1,
            "capabilities": self._public_capabilities(),
            "summary": self._empty_summary(),
            "warnings": [],
            "dryRun": True,
        }

    @staticmethod
    def _empty_summary() -> dict[str, Any]:
        return {
            "stepsPassed": 0,
            "stepsTotal": len(_STEP_DEFINITIONS),
            "assistantTrack": "pending",
            "minecraftTrack": "pending",
            "cleanupTrack": "pending",
            "cleanupRequired": False,
            "cleanupStateUnknown": True,
            "eligibleToPass": False,
        }

    def _canonical_loaded_session(self, payload: dict[str, Any]) -> bool:
        if (
            payload.get("schema") != SESSION_SCHEMA
            or payload.get("suite") != SUITE_ID
            or not _SESSION_ID_RE.fullmatch(str(payload.get("sessionId") or ""))
            or payload.get("state")
            not in {"preflight", "running", *TERMINAL_STATES}
            or payload.get("dryRun") is not True
            or not _HEX_DIGEST_RE.fullmatch(
                str(payload.get("_guildFingerprint") or "")
            )
        ):
            return False
        created = _finite_float(payload.get("createdAt"))
        expires = _finite_float(payload.get("expiresAt"))
        index = payload.get("_stepIndex")
        steps = payload.get("_steps")
        if (
            created is None
            or expires is None
            or expires <= created
            or not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(_STEP_DEFINITIONS)
            or not isinstance(steps, list)
            or len(steps) != len(_STEP_DEFINITIONS)
        ):
            return False
        if payload.get("state") == "passed" and not all(
            isinstance(step, dict)
            and step.get("status") == "passed"
            and step.get("machineEvidenceObserved") is True
            for step in steps
        ):
            return False
        for step, definition in zip(steps, _STEP_DEFINITIONS):
            if not isinstance(step, dict) or any(
                step.get(key) != definition[key]
                for key in (
                    "id",
                    "kind",
                    "instructionCode",
                    "manualAcknowledgementRequired",
                )
            ):
                return False
            attempt = step.get("attempt")
            if (
                not isinstance(attempt, int)
                or isinstance(attempt, bool)
                or not 1 <= attempt <= MAX_ATTEMPTS
                or step.get("status")
                not in {"pending", "passed", "failed", "blocked"}
                or not isinstance(step.get("acknowledged"), bool)
                or not isinstance(step.get("machineEvidenceObserved"), bool)
                or not isinstance(step.get("requirements"), dict)
                or not isinstance(step.get("errors"), list)
                or any(not isinstance(code, str) for code in step.get("errors"))
                or _finite_float(step.get("attemptStartedAt")) is None
            ):
                return False
        return True

    def _load_active(self) -> None:
        payload, error = _safe_json_object(self.paths.active)
        if error == "missing":
            return
        if payload is None or not self._canonical_loaded_session(payload):
            self._session = self._invalid_recovery_session()
            self._persist()
            self._finalize_report()
            return
        self._session = payload
        self._expire_if_needed()

    def _invalid_recovery_session(self) -> dict[str, Any]:
        now = self.now()
        session_id = f"autonomy-p0-invalid-{uuid.uuid4().hex[:12]}"
        steps = [_new_step(item, now=now) for item in _STEP_DEFINITIONS]
        steps[0]["status"] = "failed"
        steps[0]["errors"] = ["session_recovery_invalid"]
        return {
            "schema": SESSION_SCHEMA,
            "sessionId": session_id,
            "suite": SUITE_ID,
            "state": "failed",
            "dryRun": True,
            "createdAt": now,
            "updatedAt": now,
            "expiresAt": now + self.ttl_sec,
            "completedAt": now,
            "failureCode": "session_recovery_invalid",
            "_guildFingerprint": "0" * 64,
            "_stepIndex": 0,
            "_steps": steps,
            "warnings": [],
        }

    def _persist(self) -> None:
        if self._session is None:
            return
        self._session["updatedAt"] = self.now()
        atomic_json_write(self.paths.active, self._session, durable=True)

    def _status_probe(
        self,
        *,
        path: Path,
        expected_schema: str,
        observer: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        payload, error = _safe_json_object(path)
        blocker_prefix = (
            "authorization" if observer == "authorization" else "world_lease"
        )
        public: dict[str, Any] = {
            "state": "unavailable",
            "ready": False,
            "blockers": [],
            "warnings": [],
        }
        if payload is None:
            public["blockers"] = [f"{blocker_prefix}_status_{error}"]
            return public, None
        if payload.get("schema") != expected_schema:
            public["blockers"] = [f"{blocker_prefix}_status_schema_mismatch"]
            return public, None
        updated = _finite_float(payload.get("updatedAt"))
        current = self.now()
        if updated is None:
            public["blockers"] = [f"{blocker_prefix}_status_invalid"]
            return public, None
        age = current - updated
        if age < -_CLOCK_SKEW_SEC:
            public["blockers"] = [f"{blocker_prefix}_status_clock_invalid"]
            return public, None
        if age > self.status_max_age_sec:
            public["state"] = "degraded"
            public["blockers"] = [f"{blocker_prefix}_status_stale"]
            return public, payload
        if payload.get("auditReady") is not True:
            public["blockers"] = [f"{blocker_prefix}_audit_unavailable"]
            return public, payload
        policy = payload.get("policy")
        if not isinstance(policy, dict):
            public["blockers"] = [f"{blocker_prefix}_privacy_contract_invalid"]
            return public, payload
        privacy_ok = bool(
            policy.get("issuerRefPublic") is False
            and policy.get("rawArguments") is False
            and policy.get("transcript") is False
        )
        if observer == "world_lease":
            privacy_ok = bool(
                privacy_ok
                and policy.get("rawGoal") is False
                and payload.get("statusReady") is True
                and payload.get("ownerClaimOwned") is True
                and payload.get("ownerLockHeld") is True
            )
        else:
            privacy_ok = bool(
                privacy_ok
                and policy.get("strictActionEvidenceMatch") is True
                and policy.get("retryExhaustionIsEvidence") is False
            )
        if not privacy_ok:
            public["blockers"] = [f"{blocker_prefix}_privacy_contract_invalid"]
            return public, payload
        public.update(
            {
                "state": "ready",
                "ready": True,
                "blockers": [],
                "ageSec": round(max(0.0, age), 1),
            }
        )
        return public, payload

    def _probes(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        authorization, authorization_raw = self._status_probe(
            path=self.paths.authorization_status,
            expected_schema=AUTONOMY_AUTHORIZATION_STATUS_SCHEMA,
            observer="authorization",
        )
        lease, lease_raw = self._status_probe(
            path=self.paths.world_lease_status,
            expected_schema=MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
            observer="world_lease",
        )
        effect_raw, effect_error = _safe_json_object(
            self.paths.world_effect_status
        )
        effect_status = None
        if not effect_error and effect_raw is not None:
            effect_status, effect_error = (
                validate_mindcraft_world_effect_status(
                    effect_raw,
                    now=self.now(),
                    max_age_sec=self.status_max_age_sec,
                )
            )
        effect_blocker = (
            MINECRAFT_POSTCONDITION_BLOCKER
            if effect_error
            else ""
        )
        capabilities = {
            "authorizationObserver": authorization,
            "worldLeaseObserver": lease,
            "minecraftWorldEffect": {
                "state": (
                    str((effect_status or {}).get("state") or "ready")
                    if effect_status is not None
                    else "unavailable"
                ),
                "ready": effect_status is not None,
                "blockers": [effect_blocker] if effect_blocker else [],
                "warnings": [],
            },
        }
        return capabilities, authorization_raw, lease_raw

    def _public_capabilities(self) -> dict[str, Any]:
        capabilities, _, _ = self._probes()
        return capabilities

    def _preflight_blockers(self) -> list[str]:
        capabilities, authorization, lease = self._probes()
        blockers = {
            str(code)
            for name in ("authorizationObserver", "worldLeaseObserver")
            for code in (capabilities.get(name) or {}).get("blockers") or []
        }
        if isinstance(authorization, dict):
            active_grants = authorization.get("activeGrants")
            if not isinstance(active_grants, list):
                blockers.add("authorization_active_grants_invalid")
            elif any(
                isinstance(grant, dict)
                and self._target_matches(grant.get("guildId"))
                for grant in active_grants
            ):
                blockers.add("active_authorization_present")
        if isinstance(lease, dict):
            active_lease = lease.get("lease")
            if (
                lease.get("active") is True
                and isinstance(active_lease, dict)
                and self._target_matches(active_lease.get("guildId"))
            ):
                blockers.add("active_world_lease_present")
        return sorted(blockers)

    def _target_matches(self, value: Any) -> bool:
        if self._session is None:
            return False
        return _fingerprint(
            str(self._session.get("sessionId") or ""), value
        ) == self._session.get("_guildFingerprint")

    def _cleanup_state(
        self,
        *,
        authorization_status: dict[str, Any] | None = None,
        lease_status: dict[str, Any] | None = None,
    ) -> tuple[bool, bool]:
        """Return ``(required, unknown)`` without exposing target identity."""

        if self._session is None:
            return False, True
        steps = self._session.get("_steps") or []
        authority_grant_step = (
            steps[4]
            if len(steps) > 4 and steps[4].get("_grantFingerprint")
            else steps[0]
            if steps
            else {}
        )
        grant_was_observed = bool(
            authority_grant_step.get("_grantFingerprint")
        )
        lease_was_observed = bool(
            len(steps) > 3 and steps[3].get("_leaseFingerprint")
        )
        if authorization_status is None or lease_status is None:
            authorization, auth_error = _safe_json_object(
                self.paths.authorization_status
            )
            lease, lease_error = _safe_json_object(
                self.paths.world_lease_status
            )
        else:
            authorization = authorization_status
            lease = lease_status
            auth_error = "" if isinstance(authorization, dict) else "invalid"
            lease_error = "" if isinstance(lease, dict) else "invalid"
        if (
            auth_error
            or lease_error
            or authorization is None
            or lease is None
            or authorization.get("schema")
            != AUTONOMY_AUTHORIZATION_STATUS_SCHEMA
            or lease.get("schema") != MINECRAFT_WORLD_LEASE_STATUS_SCHEMA
            or authorization.get("auditReady") is not True
            or lease.get("auditReady") is not True
            or lease.get("statusReady") is not True
        ):
            # This observer never creates an external effect. A cold preflight
            # can therefore be aborted even when the external status producers
            # are absent. Once a target grant or lease has actually been
            # observed, unknown cleanup state remains fail-closed.
            return bool(grant_was_observed or lease_was_observed), True
        active_grants = authorization.get("activeGrants")
        if not isinstance(active_grants, list):
            return True, True
        target_grant_active = any(
            isinstance(grant, dict)
            and self._target_matches(grant.get("guildId"))
            for grant in active_grants
        )
        active_lease = lease.get("lease")
        target_lease_active = bool(
            lease.get("active") is True
            and isinstance(active_lease, dict)
            and self._target_matches(active_lease.get("guildId"))
        )
        session_id = str(self._session.get("sessionId") or "")
        auth_rows, auth_events_error = self._scan_events(
            directory=self.paths.authorization_events,
            schema=AUTONOMY_AUTHORIZATION_EVENT_SCHEMA,
            allowed_events=_AUTH_EVENTS,
            source_code="authorization",
        )
        auth_process = _fingerprint(
            session_id,
            authorization.get("processNonce"),
        )
        grant_fingerprint = str(
            authority_grant_step.get("_grantFingerprint") or ""
        )
        grant_process = str(
            authority_grant_step.get("_processFingerprint") or ""
        )
        if not grant_process and grant_fingerprint:
            issued = next(
                (
                    row
                    for row in auth_rows
                    if row.get("event") == "grant_issued"
                    and row.get("grantFingerprint") == grant_fingerprint
                ),
                None,
            )
            grant_process = str((issued or {}).get("processFingerprint") or "")
        grant_observed_at = float(
            authority_grant_step.get("_grantObservedAt")
            or authority_grant_step.get("_observedAt")
            or 0.0
        )
        grant_explicit_cleanup = bool(
            not auth_events_error
            and grant_fingerprint
            and any(
                row.get("event") in {"grant_revoked", "grant_expired"}
                and row.get("grantFingerprint") == grant_fingerprint
                and float(row.get("at") or 0.0) >= grant_observed_at
                for row in auth_rows
            )
        )
        authorization_policy = authorization.get("policy")
        grant_epoch_cleanup = bool(
            not auth_events_error
            and grant_process
            and auth_process
            and auth_process != grant_process
            and not target_grant_active
            and isinstance(authorization_policy, dict)
            and authorization_policy.get("restoredAfterRestart") is False
            and any(
                row.get("event") == "process_started"
                and row.get("processFingerprint") == auth_process
                and float(row.get("at") or 0.0) >= grant_observed_at
                for row in auth_rows
            )
        )
        grant_cleanup_observed = bool(
            grant_explicit_cleanup or grant_epoch_cleanup
        )

        lease_rows, lease_events_error = self._scan_events(
            directory=self.paths.world_lease_events,
            schema=MINECRAFT_WORLD_LEASE_EVENT_SCHEMA,
            allowed_events=_LEASE_EVENTS,
            source_code="world_lease",
        )
        lease_process = _fingerprint(
            session_id,
            lease.get("processNonce"),
        )
        lease_fingerprint = str(
            (steps[3] if len(steps) > 3 else {}).get("_leaseFingerprint")
            or ""
        )
        issued_process = str(
            (steps[3] if len(steps) > 3 else {}).get("_processFingerprint")
            or ""
        )
        if not issued_process and lease_fingerprint:
            issued = next(
                (
                    row
                    for row in lease_rows
                    if row.get("event") == "lease_issued"
                    and row.get("leaseFingerprint") == lease_fingerprint
                ),
                None,
            )
            issued_process = str((issued or {}).get("processFingerprint") or "")
        lease_observed_at = float(
            (steps[3] if len(steps) > 3 else {}).get("_issuedAt") or 0.0
        )
        lease_revocations = [
            row
            for row in lease_rows
            if row.get("event") == "lease_revoked"
            and row.get("leaseFingerprint") == lease_fingerprint
            and float(row.get("at") or 0.0) >= lease_observed_at
        ]
        lease_revoked_at = (
            float(lease_revocations[0].get("at") or 0.0)
            if lease_revocations
            else 0.0
        )
        lease_explicit_cleanup = bool(
            not lease_events_error
            and lease_fingerprint
            and lease_revocations
            and any(
                row.get("event") == "runtime_stop_verified"
                and row.get("verified") is True
                and row.get("outcomeCode") == "minecraft_stopped"
                and row.get("leaseFingerprint") == lease_fingerprint
                and float(row.get("at") or 0.0) >= lease_revoked_at
                for row in lease_rows
            )
        )
        lease_policy = lease.get("policy")
        current_process_started = [
            row
            for row in lease_rows
            if row.get("event") == "process_started"
            and row.get("processFingerprint") == lease_process
            and float(row.get("at") or 0.0) >= lease_observed_at
        ]
        process_started_at = (
            float(current_process_started[0].get("at") or 0.0)
            if current_process_started
            else 0.0
        )
        lease_epoch_cleanup = bool(
            not lease_events_error
            and issued_process
            and lease_process
            and lease_process != issued_process
            and not target_lease_active
            and isinstance(lease_policy, dict)
            and lease_policy.get("restoredAfterRestart") is False
            and lease_policy.get("singleWorldOwner") is True
            and lease_policy.get("effectHandoffLock") is True
            and lease.get("ownerClaimOwned") is True
            and lease.get("ownerLockHeld") is True
            and lease.get("lastStopOutcome") == "minecraft_stopped"
            and current_process_started
            and any(
                row.get("event") == "runtime_stop_verified"
                and row.get("processFingerprint") == lease_process
                and not row.get("leaseFingerprint")
                and row.get("verified") is True
                and row.get("outcomeCode") == "minecraft_stopped"
                and float(row.get("at") or 0.0) >= process_started_at
                for row in lease_rows
            )
        )
        lease_cleanup_observed = bool(
            lease_explicit_cleanup or lease_epoch_cleanup
        )
        grant_cleanup_missing = bool(
            grant_was_observed
            and not grant_cleanup_observed
        )
        lease_cleanup_missing = bool(
            lease_was_observed
            and not lease_cleanup_observed
        )
        return (
            bool(
                target_grant_active
                or target_lease_active
                or grant_cleanup_missing
                or lease_cleanup_missing
            ),
            False,
        )

    def _current_target_authority_inactive(
        self,
        *,
        authorization_status: dict[str, Any] | None,
        lease_status: dict[str, Any] | None,
    ) -> tuple[bool, bool, bool]:
        """Project the same-refresh target authority state fail-closed.

        Return ``(authorization_inactive, lease_inactive, unknown)``.  The
        validation cursor must never infer cleanup from historical revoke/stop
        events alone: a replacement grant or lease can be issued immediately
        after those events.
        """

        if (
            not isinstance(authorization_status, dict)
            or authorization_status.get("schema")
            != AUTONOMY_AUTHORIZATION_STATUS_SCHEMA
            or authorization_status.get("auditReady") is not True
            or not isinstance(lease_status, dict)
            or lease_status.get("schema")
            != MINECRAFT_WORLD_LEASE_STATUS_SCHEMA
            or lease_status.get("auditReady") is not True
            or lease_status.get("statusReady") is not True
        ):
            return False, False, True

        active_grants = authorization_status.get("activeGrants")
        active_grant_count = authorization_status.get("activeGrantCount")
        if (
            not isinstance(active_grants, list)
            or type(active_grant_count) is not int
            or active_grant_count != len(active_grants)
            or any(
                not isinstance(grant, dict)
                or _positive_int(grant.get("guildId")) is None
                for grant in active_grants
            )
        ):
            return False, False, True
        target_grant_active = any(
            self._target_matches(grant.get("guildId"))
            for grant in active_grants
        )

        active = lease_status.get("active")
        active_lease = lease_status.get("lease")
        if type(active) is not bool:
            return False, False, True
        if active:
            if (
                not isinstance(active_lease, dict)
                or _positive_int(active_lease.get("guildId")) is None
                or not _safe_id(active_lease.get("leaseId"))
            ):
                return False, False, True
            target_lease_active = self._target_matches(
                active_lease.get("guildId")
            )
        else:
            if active_lease is not None:
                return False, False, True
            target_lease_active = False
        return (
            not target_grant_active,
            not target_lease_active,
            False,
        )

    def _id_matches(self, value: Any, fingerprint: str) -> bool:
        if self._session is None or not fingerprint:
            return False
        return _fingerprint(
            str(self._session.get("sessionId") or ""), value
        ) == fingerprint

    def _scan_events(
        self,
        *,
        directory: Path,
        schema: str,
        allowed_events: frozenset[str],
        source_code: str,
    ) -> tuple[list[dict[str, Any]], str]:
        if self._session is None:
            return [], ""
        if not directory.exists():
            return [], f"{source_code}_events_missing"
        try:
            paths = sorted(directory.glob("*.jsonl"))
        except OSError:
            return [], f"{source_code}_events_unavailable"
        if not paths:
            return [], f"{source_code}_events_missing"
        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        created_at = float(self._session.get("createdAt") or 0.0)
        current = self.now()
        line_count = 0
        for path in paths:
            try:
                stat = path.stat()
                if stat.st_size > _MAX_EVENT_FILE_BYTES:
                    return [], f"{source_code}_events_oversized"
                raw_lines = path.read_text(encoding="utf-8").splitlines(
                    keepends=True
                )
            except (OSError, UnicodeError):
                return [], f"{source_code}_events_unavailable"
            for index, raw in enumerate(raw_lines):
                line_count += 1
                if line_count > _MAX_EVENT_LINES:
                    return [], f"{source_code}_events_oversized"
                if not raw.strip():
                    continue
                if (
                    path == paths[-1]
                    and index == len(raw_lines) - 1
                    and not raw.endswith(("\n", "\r"))
                ):
                    # Only a freshly modified current journal gets a short
                    # concurrent-writer grace window. Crash leftovers and
                    # truncated historical journals fail closed.
                    age = current - stat.st_mtime
                    if -_CLOCK_SKEW_SEC <= age <= _PARTIAL_WRITE_GRACE_SEC:
                        continue
                    return [], f"{source_code}_events_corrupt"
                try:
                    payload = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    return [], f"{source_code}_events_corrupt"
                if not isinstance(payload, dict) or payload.get("schema") != schema:
                    return [], f"{source_code}_event_schema_mismatch"
                event_id = _safe_id(payload.get("eventId"))
                event = str(payload.get("event") or "")
                at = _finite_float(payload.get("at"))
                if (
                    not event_id
                    or event_id in seen_ids
                    or event not in allowed_events
                    or at is None
                    or at > current + _CLOCK_SKEW_SEC
                ):
                    return [], f"{source_code}_event_invalid"
                seen_ids.add(event_id)
                target_matched = (
                    True
                    if source_code == "world_effect"
                    else self._target_matches(payload.get("guildId"))
                )
                global_epoch_event = bool(
                    event == "process_started"
                    or (
                        source_code == "world_lease"
                        and event == "runtime_stop_verified"
                        and not str(payload.get("leaseId") or "").strip()
                        and payload.get("guildId") in (0, None)
                    )
                )
                if at < created_at or not (target_matched or global_epoch_event):
                    continue
                descriptor: dict[str, Any] = {
                    "at": at,
                    "event": event,
                    "sourceSequence": line_count,
                    "targetMatched": target_matched,
                    "eventFingerprint": _fingerprint(
                        str(self._session.get("sessionId") or ""), event_id
                    ),
                    "processFingerprint": _fingerprint(
                        str(self._session.get("sessionId") or ""),
                        payload.get("processNonce"),
                    ),
                }
                if source_code == "authorization":
                    action = str(payload.get("action") or "")
                    if action and action not in SUPPORTED_AUTONOMY_ACTIONS:
                        return [], "authorization_event_invalid"
                    scopes = payload.get("scopes")
                    if not isinstance(scopes, list):
                        return [], "authorization_event_invalid"
                    safe_scopes = sorted(
                        {
                            str(item)
                            for item in scopes
                            if str(item) in SUPPORTED_AUTONOMY_ACTIONS
                        }
                    )
                    if len(safe_scopes) != len(set(str(item) for item in scopes)):
                        return [], "authorization_event_invalid"
                    descriptor.update(
                        {
                            "grantFingerprint": _fingerprint(
                                str(self._session.get("sessionId") or ""),
                                payload.get("grantId"),
                            ),
                            "actionRunFingerprint": _fingerprint(
                                str(self._session.get("sessionId") or ""),
                                payload.get("actionRunId"),
                            ),
                            "actionCode": action,
                            "scopes": safe_scopes,
                            "expiresAt": _finite_float(payload.get("expiresAt")),
                            "outcomeStatus": str(
                                payload.get("outcomeStatus") or ""
                            ).lower(),
                            "verified": payload.get("verified") is True,
                            "evidenceCode": _safe_id(
                                payload.get("evidenceCode")
                            ),
                            "authorizationCurrent": (
                                payload.get("authorizationCurrent") is True
                            ),
                        }
                    )
                elif source_code == "world_lease":
                    action_code = _safe_id(payload.get("actionKey"))
                    action_event = event.startswith("action_")
                    if action_event and (
                        action_code not in MINECRAFT_ROUTE_ACTIONS
                        or not _safe_id(payload.get("actionRunId"))
                        or not _safe_id(payload.get("goalRunId"))
                        or not _safe_id(
                            payload.get("authorizationGrantId")
                        )
                        or not _safe_id(payload.get("contractCode"))
                    ):
                        return [], "world_lease_event_invalid"
                    descriptor.update(
                        {
                            "leaseFingerprint": _fingerprint(
                                str(self._session.get("sessionId") or ""),
                                payload.get("leaseId"),
                            ),
                            "reasonCode": _safe_id(payload.get("reasonCode")),
                            "outcomeCode": _safe_id(payload.get("outcomeCode")),
                            "verified": payload.get("verified") is True,
                            "grantFingerprint": _fingerprint(
                                str(self._session.get("sessionId") or ""),
                                payload.get("authorizationGrantId"),
                            ),
                            "actionRunFingerprint": _fingerprint(
                                str(self._session.get("sessionId") or ""),
                                payload.get("actionRunId"),
                            ),
                            "goalRunFingerprint": _fingerprint(
                                str(self._session.get("sessionId") or ""),
                                payload.get("goalRunId"),
                            ),
                            "actionCode": action_code,
                            "contractCode": _safe_id(
                                payload.get("contractCode")
                            ),
                        }
                    )
                else:
                    if (
                        set(payload) != _WORLD_EFFECT_EVENT_FIELDS
                        or payload.get("contentFree") is not True
                    ):
                        return [], "world_effect_event_invalid"
                    action_code = _safe_id(payload.get("actionKey"))
                    identity_required = event in {
                        "binding_armed",
                        "telemetry_rejected",
                        "effect_verified",
                        "binding_disarmed",
                    }
                    raw_identity_fields = (
                        "leaseId",
                        "leaseProcessNonce",
                        "actionRunId",
                        "goalRunId",
                        "producerNonce",
                    )
                    if identity_required and any(
                        not _safe_id(payload.get(field))
                        for field in raw_identity_fields
                    ):
                        return [], "world_effect_event_invalid"
                    identity_values = {
                        "leaseFingerprint": _fingerprint(
                            str(self._session.get("sessionId") or ""),
                            payload.get("leaseId"),
                        ),
                        "leaseProcessFingerprint": _fingerprint(
                            str(self._session.get("sessionId") or ""),
                            payload.get("leaseProcessNonce"),
                        ),
                        "actionRunFingerprint": _fingerprint(
                            str(self._session.get("sessionId") or ""),
                            payload.get("actionRunId"),
                        ),
                        "goalRunFingerprint": _fingerprint(
                            str(self._session.get("sessionId") or ""),
                            payload.get("goalRunId"),
                        ),
                        "producerFingerprint": _fingerprint(
                            str(self._session.get("sessionId") or ""),
                            payload.get("producerNonce"),
                        ),
                    }
                    if identity_required and (
                        action_code not in MINECRAFT_ROUTE_ACTIONS
                        or not all(identity_values.values())
                        or not _safe_id(payload.get("contractCode"))
                    ):
                        return [], "world_effect_event_invalid"
                    boolean_fields = {
                        "autonomous": payload.get("autonomous") is True,
                        "relevant": payload.get("relevant") is True,
                        "succeeded": payload.get("succeeded") is True,
                        "worldChanged": payload.get("worldChanged") is True,
                        "goalProgress": payload.get("goalProgress") is True,
                    }
                    descriptor.update(
                        {
                            **identity_values,
                            "actionCode": action_code,
                            "contractCode": _safe_id(
                                payload.get("contractCode")
                            ),
                            "evidenceCode": _safe_id(
                                payload.get("evidenceCode")
                            ),
                            "postconditionCode": _safe_id(
                                payload.get("postconditionCode")
                            ),
                            "candidateSequence": _positive_int(
                                payload.get("candidateSequence")
                            ),
                            "executionSequence": _positive_int(
                                payload.get("executionSequence")
                            ),
                            **boolean_fields,
                        }
                    )
                rows.append(descriptor)
        return rows, ""

    def _source_events(
        self,
        *,
        authorization_raw: dict[str, Any] | None,
        lease_raw: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        authorization, error = self._scan_events(
            directory=self.paths.authorization_events,
            schema=AUTONOMY_AUTHORIZATION_EVENT_SCHEMA,
            allowed_events=_AUTH_EVENTS,
            source_code="authorization",
        )
        if error:
            return [], [], error
        lease, error = self._scan_events(
            directory=self.paths.world_lease_events,
            schema=MINECRAFT_WORLD_LEASE_EVENT_SCHEMA,
            allowed_events=_LEASE_EVENTS,
            source_code="world_lease",
        )
        if error:
            return [], [], error
        session_id = str(self._session.get("sessionId") or "") if self._session else ""
        auth_process = _fingerprint(
            session_id,
            (authorization_raw or {}).get("processNonce"),
        )
        lease_process = _fingerprint(
            session_id,
            (lease_raw or {}).get("processNonce"),
        )
        if not auth_process or not lease_process:
            return [], [], "observer_process_identity_invalid"
        authorization = [
            row
            for row in authorization
            if row.get("processFingerprint") == auth_process
        ]
        lease = [
            row for row in lease if row.get("processFingerprint") == lease_process
        ]
        return authorization, lease, ""

    def _world_effect_source_events(
        self,
    ) -> tuple[list[dict[str, Any]], str]:
        raw, error = _safe_json_object(self.paths.world_effect_status)
        if error or raw is None:
            return [], MINECRAFT_POSTCONDITION_BLOCKER
        status, status_error = validate_mindcraft_world_effect_status(
            raw,
            now=self.now(),
            max_age_sec=self.status_max_age_sec,
        )
        if status_error or status is None:
            return [], MINECRAFT_POSTCONDITION_BLOCKER
        rows, error = self._scan_events(
            directory=self.paths.world_effect_events,
            schema=MINDCRAFT_WORLD_EFFECT_EVENT_SCHEMA,
            allowed_events=_WORLD_EFFECT_EVENTS,
            source_code="world_effect",
        )
        if error:
            return [], error
        process_fingerprint = _fingerprint(
            str(self._session.get("sessionId") or "")
            if self._session
            else "",
            status.get("processNonce"),
        )
        if not process_fingerprint:
            return [], "world_effect_process_identity_invalid"
        return (
            [
                row
                for row in rows
                if row.get("processFingerprint")
                == process_fingerprint
            ],
            "",
        )

    def _current_step(self) -> dict[str, Any] | None:
        if self._session is None:
            return None
        steps = self._session.get("_steps") or []
        index = int(self._session.get("_stepIndex") or 0)
        return steps[index] if 0 <= index < len(steps) else None

    def _append_own_event(
        self,
        event: str,
        *,
        step: dict[str, Any] | None = None,
        error_code: str = "",
        action_code: str = "",
        evidence_code: str = "",
    ) -> bool:
        if self._session is None or event not in _OWN_EVENTS:
            return False
        session_id = str(self._session.get("sessionId") or "")
        record = {
            "schema": EVENT_SCHEMA,
            "eventId": uuid.uuid4().hex,
            "sessionId": session_id,
            "at": self.now(),
            "event": event,
            "stepId": str((step or {}).get("id") or ""),
            "attempt": int((step or {}).get("attempt") or 1),
            "errorCode": _safe_id(error_code),
            "actionCode": (
                action_code if action_code in SUPPORTED_AUTONOMY_ACTIONS else ""
            ),
            "evidenceCode": _safe_id(evidence_code),
        }
        path = self.paths.events / f"{session_id}.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return True
        except OSError:
            failed_step = step or self._current_step()
            if failed_step is not None:
                failed_step["status"] = "failed"
                if "validation_audit_unavailable" not in failed_step.setdefault(
                    "errors", []
                ):
                    failed_step["errors"].append(
                        "validation_audit_unavailable"
                    )
            self._session["state"] = "failed"
            self._session["failureCode"] = "validation_audit_unavailable"
            self._session["completedAt"] = self.now()
            # Preserve a terminal, content-free report whenever the event
            # journal alone is unavailable. Reporting is best effort here:
            # the original audit failure remains the authoritative error.
            try:
                self._update_summary()
                session_id = str(self._session.get("sessionId") or "")
                if _SESSION_ID_RE.fullmatch(session_id):
                    atomic_json_write(
                        self.paths.reports / f"{session_id}.json",
                        self._report_payload(),
                        durable=True,
                    )
                self._persist()
            except OSError:
                pass
            return False

    @staticmethod
    def _events_after(
        rows: Iterable[dict[str, Any]],
        boundary: float,
        *,
        event: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in rows
            if float(row.get("at") or 0.0) >= boundary
            and (event is None or row.get("event") == event)
        ]

    def _mark_machine_evidence(
        self,
        step: dict[str, Any],
        *,
        requirements: dict[str, bool],
        action_code: str = "",
        evidence_code: str = "",
    ) -> None:
        before = bool(step.get("machineEvidenceObserved"))
        step["requirements"] = {
            str(key): bool(value) for key, value in sorted(requirements.items())
        }
        step["machineEvidenceObserved"] = bool(
            requirements and all(requirements.values())
        )
        if step["machineEvidenceObserved"] and not before:
            self._append_own_event(
                "machine_evidence_observed",
                step=step,
                action_code=action_code,
                evidence_code=evidence_code,
            )

    def _pass_step(self, step: dict[str, Any]) -> None:
        if step.get("status") in {"passed", "failed", "blocked"}:
            return
        if not step.get("machineEvidenceObserved"):
            return
        if step.get("manualAcknowledgementRequired") and not step.get(
            "acknowledged"
        ):
            return
        step["status"] = "passed"
        if not self._append_own_event("step_passed", step=step):
            return
        if self._session is None:
            return
        index = int(self._session.get("_stepIndex") or 0)
        if index + 1 < len(self._session.get("_steps") or []):
            self._session["_stepIndex"] = index + 1

    def _fail_step(self, step: dict[str, Any] | None, code: str) -> None:
        if self._session is None or step is None or step.get("status") == "failed":
            return
        safe_code = _safe_id(code) or "validation_step_failed"
        step["status"] = "failed"
        if safe_code not in step.setdefault("errors", []):
            step["errors"].append(safe_code)
        self._session["lastFailureCode"] = safe_code
        if not self._append_own_event(
            "step_failed", step=step, error_code=safe_code
        ):
            return
        if int(step.get("attempt") or 1) >= MAX_ATTEMPTS:
            self._session["state"] = "failed"
            self._session["failureCode"] = safe_code
            self._session["completedAt"] = self.now()
            self._finalize_report()

    def _fail_evidence_integrity(
        self,
        step: dict[str, Any],
        code: str,
    ) -> None:
        """Make late conflicts terminal instead of invalidating a past cursor."""

        if self._session is None:
            return
        if step.get("status") != "passed":
            self._fail_step(step, code)
            return
        current = self._current_step() or step
        safe_code = _safe_id(code) or "validation_evidence_conflict"
        current["status"] = "failed"
        if safe_code not in current.setdefault("errors", []):
            current["errors"].append(safe_code)
        self._session["state"] = "failed"
        self._session["failureCode"] = safe_code
        self._session["completedAt"] = self.now()
        if self._append_own_event(
            "step_failed",
            step=current,
            error_code=safe_code,
        ):
            self._finalize_report()

    def _advance_cleanup_evidence(
        self,
        *,
        steps: list[dict[str, Any]],
        authorization: list[dict[str, Any]],
        lease: list[dict[str, Any]],
        authorization_status: dict[str, Any] | None,
        lease_status: dict[str, Any] | None,
        lease_fingerprint: str,
        default_grant_fingerprint: str,
        world_boundary: float,
    ) -> None:
        if self._session is None:
            return
        world_step = steps[4]
        cleanup_step = steps[5]
        cleanup_grant_fingerprint = str(
            world_step.get("_grantFingerprint")
            or default_grant_fingerprint
        )
        cleanup_boundary = max(
            float(world_step.get("_observedAt") or world_boundary),
            float(cleanup_step.get("attemptStartedAt") or 0.0),
        )
        auth_cleanup = [
            row
            for row in self._events_after(authorization, cleanup_boundary)
            if row.get("event") in {"grant_revoked", "grant_expired"}
            and row.get("grantFingerprint")
            == cleanup_grant_fingerprint
        ]
        lease_revocations = [
            row
            for row in self._events_after(lease, cleanup_boundary)
            if row.get("event") == "lease_revoked"
            and row.get("leaseFingerprint") == lease_fingerprint
        ]
        stop_boundary = (
            float(lease_revocations[0]["at"])
            if lease_revocations
            else cleanup_boundary
        )
        stops = [
            row
            for row in self._events_after(lease, stop_boundary)
            if row.get("event") == "runtime_stop_verified"
            and row.get("verified") is True
            and row.get("outcomeCode") == "minecraft_stopped"
            and row.get("leaseFingerprint") == lease_fingerprint
        ]
        (
            target_authorization_inactive,
            target_lease_inactive,
            authority_state_unknown,
        ) = self._current_target_authority_inactive(
            authorization_status=authorization_status,
            lease_status=lease_status,
        )
        cleanup_required, cleanup_unknown = self._cleanup_state(
            authorization_status=authorization_status,
            lease_status=lease_status,
        )
        self._mark_machine_evidence(
            cleanup_step,
            requirements={
                "grantRevokedOrExpired": bool(auth_cleanup),
                "leaseRevoked": bool(lease_revocations),
                "runtimeStopVerified": bool(stops),
                "targetAuthorizationInactive": bool(
                    target_authorization_inactive
                ),
                "targetWorldLeaseInactive": bool(target_lease_inactive),
                "cleanupStateKnown": not bool(
                    authority_state_unknown or cleanup_unknown
                ),
                "cleanupNotRequired": not bool(cleanup_required),
            },
        )
        self._pass_step(cleanup_step)
        if (
            all(step.get("status") == "passed" for step in steps)
            and not cleanup_required
            and not cleanup_unknown
            and not authority_state_unknown
        ):
            self._session["state"] = "passed"
            self._session["completedAt"] = self.now()
            if self._append_own_event(
                "session_passed",
                step=cleanup_step,
            ):
                self._finalize_report()

    def _advance_evidence(
        self,
        authorization: list[dict[str, Any]],
        lease: list[dict[str, Any]],
        world_effect: list[dict[str, Any]] | None = None,
        *,
        authorization_status: dict[str, Any] | None = None,
        lease_status: dict[str, Any] | None = None,
    ) -> None:
        if self._session is None or self._session.get("state") != "running":
            return
        steps = self._session["_steps"]

        grant_step = steps[0]
        boundary = float(grant_step.get("attemptStartedAt") or 0.0)
        grant_rows = self._events_after(
            authorization, boundary, event="grant_issued"
        )
        if not grant_step.get("_grantFingerprint"):
            for row in grant_rows:
                assistant_scopes = [
                    scope
                    for scope in row.get("scopes") or []
                    if scope in ASSISTANT_AUTONOMY_ACTIONS
                ]
                expires_at = row.get("expiresAt")
                if (
                    row.get("grantFingerprint")
                    and assistant_scopes
                    and expires_at is not None
                    and float(expires_at) > float(row["at"])
                ):
                    grant_step["_grantFingerprint"] = row["grantFingerprint"]
                    grant_step["_processFingerprint"] = row[
                        "processFingerprint"
                    ]
                    grant_step["_observedAt"] = row["at"]
                    grant_step["_assistantScopes"] = assistant_scopes
                    break
        self._mark_machine_evidence(
            grant_step,
            requirements={
                "grantIssued": bool(grant_step.get("_grantFingerprint")),
                "assistantScopePresent": bool(
                    grant_step.get("_assistantScopes")
                ),
            },
        )
        self._pass_step(grant_step)
        if grant_step.get("status") != "passed":
            return

        grant_fingerprint = str(grant_step.get("_grantFingerprint") or "")
        grant_at = float(grant_step.get("_observedAt") or 0.0)
        allowed_scopes = set(grant_step.get("_assistantScopes") or [])
        authorization_step = steps[1]
        authorization_boundary = max(
            grant_at,
            float(authorization_step.get("attemptStartedAt") or 0.0),
        )
        matching_decisions = [
            row
            for row in self._events_after(authorization, authorization_boundary)
            if row.get("grantFingerprint") == grant_fingerprint
            and row.get("actionCode") in allowed_scopes
            and row.get("actionRunFingerprint")
            and row.get("event") in {"action_authorized", "action_denied"}
        ]
        if (
            matching_decisions
            and matching_decisions[0]["event"] == "action_denied"
        ):
            self._fail_evidence_integrity(
                authorization_step,
                "assistant_action_denied",
            )
            return
        if matching_decisions and not authorization_step.get("_actionCode"):
            row = next(
                (
                    item
                    for item in matching_decisions
                    if item.get("event") == "action_authorized"
                ),
                None,
            )
            if row is None:
                return
            authorization_step["_actionCode"] = row["actionCode"]
            authorization_step["_actionRunFingerprint"] = row[
                "actionRunFingerprint"
            ]
            authorization_step["_preAuthorizedSequence"] = row[
                "sourceSequence"
            ]
        action_code = str(authorization_step.get("_actionCode") or "")
        action_run_fingerprint = str(
            authorization_step.get("_actionRunFingerprint") or ""
        )
        run_rows = [
            row
            for row in self._events_after(authorization, authorization_boundary)
            if action_code
            and action_run_fingerprint
            and row.get("grantFingerprint") == grant_fingerprint
            and row.get("actionCode") == action_code
            and row.get("actionRunFingerprint") == action_run_fingerprint
            and row.get("event")
            in {"action_authorized", "action_denied", "action_outcome"}
        ]
        if any(row.get("event") == "action_denied" for row in run_rows):
            self._fail_evidence_integrity(
                authorization_step,
                "assistant_action_denied",
            )
            return
        authorizations = [
            row for row in run_rows if row.get("event") == "action_authorized"
        ]
        if len(authorizations) > 2:
            self._fail_evidence_integrity(
                authorization_step,
                "assistant_authorization_duplicate",
            )
            return
        if len(authorizations) >= 2:
            post_authorization = authorizations[1]
            authorization_step["_postAuthorizedSequence"] = (
                post_authorization["sourceSequence"]
            )
            authorization_step["_observedAt"] = post_authorization["at"]
        outcomes_before_recheck = [
            row
            for row in run_rows
            if row.get("event") == "action_outcome"
            and (
                not authorization_step.get("_postAuthorizedSequence")
                or int(row.get("sourceSequence") or 0)
                <= int(
                    authorization_step.get("_postAuthorizedSequence") or 0
                )
            )
        ]
        if outcomes_before_recheck:
            self._fail_evidence_integrity(
                authorization_step,
                "assistant_evidence_order_invalid",
            )
            return
        self._mark_machine_evidence(
            authorization_step,
            requirements={
                "preAuthorizationObserved": bool(authorizations),
                "postAuthorizationObserved": bool(
                    authorization_step.get("_postAuthorizedSequence")
                ),
                "sameGrant": bool(authorization_step.get("_actionCode")),
                "executionCorrelated": bool(
                    authorization_step.get("_actionRunFingerprint")
                ),
            },
            action_code=str(authorization_step.get("_actionCode") or ""),
        )
        self._pass_step(authorization_step)
        if authorization_step.get("status") != "passed":
            return

        outcome_step = steps[2]
        authorized_at = float(authorization_step.get("_observedAt") or 0.0)
        post_authorized_sequence = int(
            authorization_step.get("_postAuthorizedSequence") or 0
        )
        outcome_boundary = max(
            authorized_at,
            float(outcome_step.get("attemptStartedAt") or 0.0),
        )
        outcomes = [
            row
            for row in self._events_after(authorization, outcome_boundary)
            if row.get("event") == "action_outcome"
            and row.get("grantFingerprint") == grant_fingerprint
            and row.get("actionCode") == action_code
            and row.get("actionRunFingerprint") == action_run_fingerprint
            and int(row.get("sourceSequence") or 0) > post_authorized_sequence
        ]
        if len(outcomes) > 1:
            self._fail_evidence_integrity(
                outcome_step,
                "assistant_outcome_duplicate",
            )
            return
        if outcomes:
            row = outcomes[0]
            exact = bool(
                row.get("outcomeStatus") in AUTONOMY_SUCCESS_STATUSES
                and row.get("verified") is True
                and row.get("authorizationCurrent") is True
                and row.get("evidenceCode")
                in expected_autonomy_evidence_codes(action_code)
            )
            if not exact:
                self._fail_step(outcome_step, "assistant_outcome_evidence_invalid")
                return
            outcome_step["_evidenceCode"] = row["evidenceCode"]
            outcome_step["_observedAt"] = row["at"]
        self._mark_machine_evidence(
            outcome_step,
            requirements={
                "outcomeSucceeded": bool(outcome_step.get("_evidenceCode")),
                "outcomeVerified": bool(outcome_step.get("_evidenceCode")),
                "exactEvidenceCode": bool(outcome_step.get("_evidenceCode")),
                "authorizationCurrent": bool(outcome_step.get("_evidenceCode")),
                "sameExecution": bool(outcome_step.get("_evidenceCode")),
            },
            action_code=action_code,
            evidence_code=str(outcome_step.get("_evidenceCode") or ""),
        )
        self._pass_step(outcome_step)
        if outcome_step.get("status") != "passed":
            return

        lease_step = steps[3]
        outcome_at = float(outcome_step.get("_observedAt") or 0.0)
        lease_boundary = max(
            outcome_at,
            float(lease_step.get("attemptStartedAt") or 0.0),
        )
        issued = self._events_after(lease, lease_boundary, event="lease_issued")
        if issued and not lease_step.get("_leaseFingerprint"):
            row = next(
                (item for item in issued if item.get("leaseFingerprint")), None
            )
            if row is not None:
                lease_step["_leaseFingerprint"] = row["leaseFingerprint"]
                lease_step["_processFingerprint"] = row[
                    "processFingerprint"
                ]
                lease_step["_issuedAt"] = row["at"]
        lease_fingerprint = str(lease_step.get("_leaseFingerprint") or "")
        starts = [
            row
            for row in self._events_after(
                lease, float(lease_step.get("_issuedAt") or lease_boundary)
            )
            if row.get("event") == "runtime_start_verified"
            and row.get("leaseFingerprint") == lease_fingerprint
            and row.get("verified") is True
            and row.get("outcomeCode") == "minecraft_connected"
        ]
        if starts and not lease_step.get("_startedAt"):
            lease_step["_startedAt"] = starts[0]["at"]
        self._mark_machine_evidence(
            lease_step,
            requirements={
                "leaseIssued": bool(lease_fingerprint),
                "runtimeStartVerified": bool(lease_step.get("_startedAt")),
            },
        )
        self._pass_step(lease_step)
        if lease_step.get("status") != "passed":
            return

        world_step = steps[4]
        world_boundary = max(
            float(lease_step.get("_startedAt") or 0.0),
            float(world_step.get("attemptStartedAt") or 0.0),
        )
        if world_step.get("status") == "passed":
            self._advance_cleanup_evidence(
                steps=steps,
                authorization=authorization,
                lease=lease,
                authorization_status=authorization_status,
                lease_status=lease_status,
                lease_fingerprint=lease_fingerprint,
                default_grant_fingerprint=grant_fingerprint,
                world_boundary=world_boundary,
            )
            return
        effect_rows = list(world_effect or [])
        route_action = MINECRAFT_ROUTE_ACTIONS[0]
        spec = MINECRAFT_ACTION_SPECS[route_action]
        minecraft_grants = [
            row
            for row in self._events_after(
                authorization,
                world_boundary,
                event="grant_issued",
            )
            if route_action in (row.get("scopes") or [])
            and row.get("grantFingerprint")
        ]
        minecraft_grant = minecraft_grants[-1] if minecraft_grants else None
        minecraft_grant_fingerprint = str(
            (minecraft_grant or {}).get("grantFingerprint") or ""
        )
        minecraft_grant_at = float(
            (minecraft_grant or {}).get("at") or world_boundary
        )
        action_rows = [
            row
            for row in self._events_after(lease, minecraft_grant_at)
            if row.get("leaseFingerprint") == lease_fingerprint
            and row.get("grantFingerprint")
            == minecraft_grant_fingerprint
            and row.get("actionCode") == route_action
            and row.get("contractCode") == spec.contract_code
        ]
        if any(
            row.get("event")
            in {"action_failed", "action_cancel_failed"}
            for row in action_rows
        ):
            self._fail_step(world_step, "minecraft_action_failed")
            return
        dispatches = [
            row
            for row in action_rows
            if row.get("event") == "action_dispatch_verified"
            and row.get("verified") is True
            and row.get("outcomeCode") == "minecraft_action_dispatched"
            and row.get("actionRunFingerprint")
            and row.get("goalRunFingerprint")
        ]
        if len(dispatches) > 1:
            self._fail_evidence_integrity(
                world_step,
                "minecraft_action_dispatch_duplicate",
            )
            return
        dispatch = dispatches[0] if dispatches else None
        action_run_fingerprint = str(
            (dispatch or {}).get("actionRunFingerprint") or ""
        )
        goal_run_fingerprint = str(
            (dispatch or {}).get("goalRunFingerprint") or ""
        )
        attempted = [
            row
            for row in action_rows
            if row.get("event") == "action_dispatch_attempted"
            and row.get("actionRunFingerprint") == action_run_fingerprint
            and row.get("goalRunFingerprint") == goal_run_fingerprint
        ]
        if len(attempted) > 1:
            self._fail_evidence_integrity(
                world_step,
                "minecraft_action_dispatch_attempt_duplicate",
            )
            return
        attempt = attempted[0] if attempted else None
        if dispatch is not None and attempt is None:
            self._fail_evidence_integrity(
                world_step,
                "minecraft_action_dispatch_attempt_missing",
            )
            return
        if (
            dispatch is not None
            and attempt is not None
            and int(attempt.get("sourceSequence") or 0)
            >= int(dispatch.get("sourceSequence") or 0)
        ):
            self._fail_evidence_integrity(
                world_step,
                "minecraft_action_dispatch_order_invalid",
            )
            return
        candidate_effects = [
            row
            for row in self._events_after(effect_rows, minecraft_grant_at)
            if row.get("event") == "effect_verified"
            and row.get("leaseFingerprint") == lease_fingerprint
            and row.get("leaseProcessFingerprint")
            == lease_step.get("_processFingerprint")
            and row.get("actionRunFingerprint") == action_run_fingerprint
            and row.get("goalRunFingerprint") == goal_run_fingerprint
            and row.get("actionCode") == route_action
            and row.get("contractCode") == spec.contract_code
            and row.get("evidenceCode")
            == MINDCRAFT_WORLD_EFFECT_EVIDENCE_CODE
            and row.get("postconditionCode") == spec.postcondition_code
            and row.get("candidateSequence") == 1
            and row.get("executionSequence") is not None
            and row.get("autonomous") is True
            and row.get("relevant") is True
            and row.get("succeeded") is True
            and row.get("worldChanged") is True
            and row.get("goalProgress") is True
        ]
        if len(candidate_effects) > 1:
            self._fail_evidence_integrity(
                world_step,
                "minecraft_postcondition_duplicate",
            )
            return
        effect = candidate_effects[0] if candidate_effects else None
        if (
            effect is not None
            and attempt is not None
            and float(effect.get("at") or 0.0)
            <= float(attempt.get("at") or 0.0)
        ):
            self._fail_evidence_integrity(
                world_step,
                "minecraft_postcondition_order_invalid",
            )
            return
        completed = [
            row
            for row in action_rows
            if row.get("event") == "action_completed"
            and row.get("verified") is True
            and row.get("outcomeCode") == "minecraft_action_completed"
            and row.get("actionRunFingerprint") == action_run_fingerprint
            and row.get("goalRunFingerprint") == goal_run_fingerprint
            and (
                dispatch is None
                or int(row.get("sourceSequence") or 0)
                > int(dispatch.get("sourceSequence") or 0)
            )
            and (
                effect is None
                or float(row.get("at") or 0.0)
                >= float(effect.get("at") or 0.0)
            )
        ]
        if len(completed) > 1:
            self._fail_evidence_integrity(
                world_step,
                "minecraft_action_completion_duplicate",
            )
            return
        completion = completed[0] if completed else None
        auth_run_rows = [
            row
            for row in self._events_after(
                authorization,
                minecraft_grant_at,
            )
            if row.get("grantFingerprint")
            == minecraft_grant_fingerprint
            and row.get("actionCode") == route_action
            and row.get("actionRunFingerprint") == action_run_fingerprint
            and row.get("event")
            in {"action_authorized", "action_denied", "action_outcome"}
        ]
        if any(row.get("event") == "action_denied" for row in auth_run_rows):
            self._fail_evidence_integrity(
                world_step,
                "minecraft_action_denied",
            )
            return
        auth_decisions = [
            row
            for row in auth_run_rows
            if row.get("event") == "action_authorized"
        ]
        outcomes = [
            row
            for row in auth_run_rows
            if row.get("event") == "action_outcome"
        ]
        if len(auth_decisions) > 2 or len(outcomes) > 1:
            self._fail_evidence_integrity(
                world_step,
                "minecraft_authorization_evidence_duplicate",
            )
            return
        outcome = outcomes[0] if outcomes else None
        authorization_ordered = bool(
            len(auth_decisions) == 2
            and dispatch is not None
            and completion is not None
            and float(auth_decisions[0].get("at") or 0.0)
            <= float(dispatch.get("at") or 0.0)
            and float(auth_decisions[1].get("at") or 0.0)
            >= float(completion.get("at") or 0.0)
            and outcome is not None
            and int(outcome.get("sourceSequence") or 0)
            > int(auth_decisions[1].get("sourceSequence") or 0)
        )
        outcome_exact = bool(
            outcome is not None
            and outcome.get("outcomeStatus") in AUTONOMY_SUCCESS_STATUSES
            and outcome.get("verified") is True
            and outcome.get("authorizationCurrent") is True
            and outcome.get("evidenceCode") == spec.evidence_code
        )
        if outcome is not None and not outcome_exact:
            self._fail_step(
                world_step,
                "minecraft_outcome_evidence_invalid",
            )
            return
        requirements = {
            "minecraftScopeGranted": bool(minecraft_grant_fingerprint),
            "preAndPostAuthorizationObserved": authorization_ordered,
            "dispatchAttempted": bool(attempted),
            "autonomyRouteWired": bool(dispatch),
            "trustedPostconditionObserved": bool(effect),
            "ownerCompletionVerified": bool(completion),
            "outcomeVerified": outcome_exact,
            "sameActionRun": bool(action_run_fingerprint),
            "sameGoalRun": bool(goal_run_fingerprint),
            "sameGrant": bool(minecraft_grant_fingerprint),
            "sameLease": bool(lease_fingerprint),
            "exactEvidenceCode": outcome_exact,
        }
        if minecraft_grant is not None:
            world_step["_grantFingerprint"] = (
                minecraft_grant_fingerprint
            )
            world_step["_processFingerprint"] = (
                minecraft_grant.get("processFingerprint")
            )
            world_step["_grantObservedAt"] = minecraft_grant_at
        if action_run_fingerprint:
            world_step["_actionRunFingerprint"] = (
                action_run_fingerprint
            )
            world_step["_goalRunFingerprint"] = goal_run_fingerprint
        if outcome_exact:
            world_step["_observedAt"] = float(
                outcome.get("at") or 0.0
            )
        self._mark_machine_evidence(
            world_step,
            requirements=requirements,
            action_code=route_action,
            evidence_code=(
                str(outcome.get("evidenceCode") or "")
                if outcome_exact
                else ""
            ),
        )
        self._pass_step(world_step)
        if world_step.get("status") != "passed":
            return

        self._advance_cleanup_evidence(
            steps=steps,
            authorization=authorization,
            lease=lease,
            authorization_status=authorization_status,
            lease_status=lease_status,
            lease_fingerprint=lease_fingerprint,
            default_grant_fingerprint=grant_fingerprint,
            world_boundary=world_boundary,
        )

    def _refresh(self) -> None:
        if self._session is None or self._session.get("state") in TERMINAL_STATES:
            return
        self._expire_if_needed()
        if self._session is None or self._session.get("state") in TERMINAL_STATES:
            return
        capabilities, authorization_raw, lease_raw = self._probes()
        self._session["capabilities"] = capabilities
        if self._session.get("state") == "preflight":
            self._update_summary()
            self._persist()
            return
        current = self._current_step()
        current_kind = str((current or {}).get("kind") or "")
        if current is not None and current.get("status") == "failed":
            self._update_summary()
            self._persist()
            return
        relevant = [capabilities["authorizationObserver"]]
        if current_kind in {"world_lease", "world_postcondition", "cleanup"}:
            relevant.append(capabilities["worldLeaseObserver"])
        if current_kind == "world_postcondition":
            relevant.append(capabilities["minecraftWorldEffect"])
        blocker = next(
            (
                str(code)
                for capability in relevant
                for code in capability.get("blockers") or []
            ),
            "",
        )
        if blocker:
            self._fail_step(current, blocker)
            self._update_summary()
            self._persist()
            return
        if current_kind in {"world_lease", "world_postcondition", "cleanup"}:
            authorization, lease, error = self._source_events(
                authorization_raw=authorization_raw,
                lease_raw=lease_raw,
            )
        else:
            authorization, error = self._scan_events(
                directory=self.paths.authorization_events,
                schema=AUTONOMY_AUTHORIZATION_EVENT_SCHEMA,
                allowed_events=_AUTH_EVENTS,
                source_code="authorization",
            )
            lease = []
            auth_process = _fingerprint(
                str(self._session.get("sessionId") or ""),
                (authorization_raw or {}).get("processNonce"),
            )
            if not auth_process:
                error = "observer_process_identity_invalid"
            else:
                authorization = [
                    row
                    for row in authorization
                    if row.get("processFingerprint") == auth_process
                ]
        world_effect: list[dict[str, Any]] = []
        if not error and current_kind == "world_postcondition":
            world_effect, error = self._world_effect_source_events()
        if error:
            steps = self._session.get("_steps") or []
            source_not_yet_observed = bool(
                error == "authorization_events_missing"
                and not (steps and steps[0].get("_grantFingerprint"))
            ) or bool(
                error == "world_lease_events_missing"
                and not (
                    len(steps) > 3
                    and steps[3].get("_leaseFingerprint")
                )
            )
            if not source_not_yet_observed:
                self._fail_step(current, error)
                self._update_summary()
                self._persist()
                return
            authorization = authorization if isinstance(authorization, list) else []
            lease = lease if isinstance(lease, list) else []
        self._advance_evidence(
            authorization,
            lease,
            world_effect,
            authorization_status=authorization_raw,
            lease_status=lease_raw,
        )
        self._update_summary()
        self._persist()

    def _update_summary(self) -> None:
        if self._session is None:
            return
        steps = self._session.get("_steps") or []
        assistant_passed = all(
            step.get("status") == "passed" for step in steps[:3]
        )
        minecraft_passed = bool(
            len(steps) > 4
            and all(
                step.get("status") == "passed"
                for step in steps[3:5]
            )
        )
        cleanup_required, cleanup_unknown = self._cleanup_state()
        effect_observed = bool(
            (steps and steps[0].get("_grantFingerprint"))
            or (len(steps) > 4 and steps[4].get("_grantFingerprint"))
            or (len(steps) > 3 and steps[3].get("_leaseFingerprint"))
        )
        cleanup_observed = bool(
            len(steps) > 5
            and (
                steps[5].get("machineEvidenceObserved")
                or (
                    effect_observed
                    and not cleanup_required
                    and not cleanup_unknown
                )
            )
        )
        if cleanup_observed and len(steps) > 5:
            steps[5]["machineEvidenceObserved"] = True
            steps[5].setdefault("requirements", {})[
                "externalAuthorityInactive"
            ] = True
        self._session["summary"] = {
            "stepsPassed": sum(
                1 for step in steps if step.get("status") == "passed"
            ),
            "stepsTotal": len(steps),
            "assistantTrack": "passed" if assistant_passed else "pending",
            "minecraftTrack": "passed" if minecraft_passed else "pending",
            "cleanupTrack": (
                "passed"
                if len(steps) > 5 and steps[5].get("status") == "passed"
                else "observed"
                if cleanup_observed
                else "pending"
            ),
            "cleanupRequired": cleanup_required,
            "cleanupStateUnknown": cleanup_unknown,
            "eligibleToPass": bool(
                assistant_passed
                and minecraft_passed
                and cleanup_observed
                and not cleanup_required
                and not cleanup_unknown
            ),
        }
        self._session["warnings"] = []

    def _production_blockers(self) -> list[str]:
        if self._session is None:
            return list(MINECRAFT_PRODUCTION_BLOCKERS)
        steps = self._session.get("_steps") or []
        world_step = steps[4] if len(steps) > 4 else {}
        if world_step.get("status") == "passed":
            return []
        requirements = world_step.get("requirements") or {}
        blockers: list[str] = []
        if not requirements.get("autonomyRouteWired"):
            blockers.append(MINECRAFT_ROUTE_BLOCKER)
        if not requirements.get("trustedPostconditionObserved"):
            blockers.append(MINECRAFT_POSTCONDITION_BLOCKER)
        return blockers

    def _public_step(self, step: dict[str, Any] | None) -> dict[str, Any]:
        if not step:
            return {}
        return {
            "id": step.get("id"),
            "kind": step.get("kind"),
            "instructionCode": step.get("instructionCode"),
            "status": step.get("status"),
            "attempt": step.get("attempt"),
            "acknowledged": bool(step.get("acknowledged")),
            "manualAcknowledgementRequired": bool(
                step.get("manualAcknowledgementRequired")
            ),
            "machineEvidenceObserved": bool(
                step.get("machineEvidenceObserved")
            ),
            "requirements": deepcopy(step.get("requirements") or {}),
            "errors": list(step.get("errors") or []),
        }

    def _public_session(self) -> dict[str, Any]:
        if self._session is None:
            return self._idle()
        current = self._current_step()
        public = {
            "schema": SESSION_SCHEMA,
            "sessionId": self._session.get("sessionId"),
            "suite": SUITE_ID,
            "state": self._session.get("state"),
            "currentStep": self._public_step(current),
            "attempt": int((current or {}).get("attempt") or 1),
            "capabilities": deepcopy(self._session.get("capabilities") or {}),
            "summary": deepcopy(
                self._session.get("summary") or self._empty_summary()
            ),
            "warnings": deepcopy(self._session.get("warnings") or []),
            "dryRun": True,
            "preflightBlockers": (
                self._preflight_blockers()
                if self._session.get("state") == "preflight"
                else []
            ),
            "productionBlockers": self._production_blockers(),
            "blockers": sorted(
                {
                    str(code)
                    for capability in (
                        self._session.get("capabilities") or {}
                    ).values()
                    if isinstance(capability, dict)
                    for code in capability.get("blockers") or []
                }
            ),
            **(
                {"failureCode": self._session.get("failureCode")}
                if self._session.get("failureCode")
                else {}
            ),
        }
        steps = self._session.get("_steps") or []
        if current and current.get("kind") == "world_postcondition" and len(steps) > 5:
            public["cleanupStep"] = self._public_step(steps[5])
        return public

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._refresh()
            return self._public_session()

    def start(
        self,
        *,
        suite: str,
        guild_id: Any,
        dry_run: Any,
    ) -> dict[str, Any]:
        if suite != SUITE_ID:
            return {"ok": False, "error": "unsupported_suite"}
        if dry_run is not True:
            return {"ok": False, "error": "dry_run_required"}
        resolved_guild = _positive_int(guild_id)
        if resolved_guild is None:
            return {"ok": False, "error": "guild_id_positive_required"}
        with self._lock:
            self._expire_if_needed()
            if self._session and self._session.get("state") not in TERMINAL_STATES:
                return {
                    "ok": False,
                    "error": "validation_session_active",
                    "session": self._public_session(),
                }
            now = self.now()
            session_id = (
                f"autonomy-p0-{time.strftime('%Y%m%d-%H%M%S')}-"
                f"{uuid.uuid4().hex[:10]}"
            )
            self._session = {
                "schema": SESSION_SCHEMA,
                "sessionId": session_id,
                "suite": SUITE_ID,
                "state": "preflight",
                "dryRun": True,
                "createdAt": now,
                "updatedAt": now,
                "expiresAt": now + self.ttl_sec,
                "_guildFingerprint": _fingerprint(session_id, resolved_guild),
                "_stepIndex": 0,
                "_steps": [
                    _new_step(definition, now=now)
                    for definition in _STEP_DEFINITIONS
                ],
                "summary": self._empty_summary(),
                "warnings": [],
            }
            capabilities, _, _ = self._probes()
            self._session["capabilities"] = capabilities
            audit_recorded = self._append_own_event(
                "session_started", step=self._current_step()
            )
            self._update_summary()
            self._persist()
            if not audit_recorded:
                return {
                    "ok": False,
                    "error": "validation_audit_unavailable",
                    "session": self._public_session(),
                }
            self.prune_reports()
            return {"ok": True, "session": self._public_session()}

    def confirm(
        self,
        *,
        session_id: str,
        step_id: str,
        acknowledged: bool,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        if type(acknowledged) is not bool:
            return {"ok": False, "error": "acknowledged_boolean_required"}
        with self._lock:
            self._expire_if_needed()
            if not self._session or session_id != self._session.get("sessionId"):
                return {"ok": False, "error": "validation_session_not_found"}
            if self._session.get("state") in TERMINAL_STATES:
                return {
                    "ok": False,
                    "error": "validation_session_terminal",
                    "session": self._public_session(),
                }
            step = self._current_step()
            if step is None or step_id != step.get("id"):
                return {"ok": False, "error": "validation_step_not_current"}
            if attempt is not None and (
                isinstance(attempt, bool)
                or not isinstance(attempt, int)
                or attempt != step.get("attempt")
            ):
                return {
                    "ok": False,
                    "error": "validation_attempt_revision_mismatch",
                    "session": self._public_session(),
                }
            if not step.get("manualAcknowledgementRequired"):
                return {
                    "ok": False,
                    "error": "acknowledgement_not_applicable",
                    "session": self._public_session(),
                }
            if self._session.get("state") == "preflight":
                preflight_blockers = self._preflight_blockers()
                if preflight_blockers:
                    self._session["capabilities"] = self._probes()[0]
                    self._update_summary()
                    self._persist()
                    return {
                        "ok": False,
                        "error": "preflight_blocked",
                        "blockers": preflight_blockers,
                        "session": self._public_session(),
                    }
            if step.get("status") == "failed":
                return {
                    "ok": False,
                    "error": "validation_step_failed",
                    "session": self._public_session(),
                }
            step["acknowledged"] = acknowledged
            if not acknowledged:
                self._fail_step(step, "manual_instruction_not_acknowledged")
            else:
                audit_recorded = self._append_own_event(
                    "instruction_acknowledged",
                    step=step,
                )
                if audit_recorded and self._session.get("state") == "preflight":
                    self._session["state"] = "running"
            # Confirmation records intent only.  A later passive refresh is
            # responsible for machine evidence and step completion.
            self._update_summary()
            self._persist()
            if self._session.get("failureCode") == "validation_audit_unavailable":
                return {
                    "ok": False,
                    "error": "validation_audit_unavailable",
                    "session": self._public_session(),
                }
            return {"ok": True, "session": self._public_session()}

    def retry(
        self,
        *,
        session_id: str,
        step_id: str,
        attempt: int | None,
    ) -> dict[str, Any]:
        with self._lock:
            self._expire_if_needed()
            if not self._session or session_id != self._session.get("sessionId"):
                return {"ok": False, "error": "validation_session_not_found"}
            if self._session.get("state") in TERMINAL_STATES:
                return {
                    "ok": False,
                    "error": "validation_session_terminal",
                    "session": self._public_session(),
                }
            step = self._current_step()
            if step is None or step_id != step.get("id"):
                return {"ok": False, "error": "validation_step_not_current"}
            current_attempt = int(step.get("attempt") or 1)
            if attempt != current_attempt:
                return {
                    "ok": False,
                    "error": "validation_attempt_revision_mismatch",
                    "session": self._public_session(),
                }
            if step.get("status") != "failed":
                return {
                    "ok": False,
                    "error": "validation_step_not_failed",
                    "session": self._public_session(),
                }
            if current_attempt >= MAX_ATTEMPTS:
                return {
                    "ok": False,
                    "error": "attempt_budget_exhausted",
                    "session": self._public_session(),
                }
            step_index = int(self._session.get("_stepIndex") or 0)
            definition = _STEP_DEFINITIONS[step_index]
            replacement = _new_step(definition, now=self.now())
            replacement["attempt"] = current_attempt + 1
            self._session["_steps"][step_index] = replacement
            if step_index == 2:
                # An outcome is immutable evidence for one execution. Retrying
                # it must observe a new pre-execution authorization and a new
                # actionRunId instead of attaching a later outcome to the old
                # authorization decision.
                authorization = _new_step(
                    _STEP_DEFINITIONS[1],
                    now=self.now(),
                )
                authorization["attempt"] = current_attempt + 1
                self._session["_steps"][1] = authorization
                self._session["_stepIndex"] = 1
            self._session["state"] = (
                "preflight" if int(self._session["_stepIndex"]) == 0 else "running"
            )
            self._session.pop("failureCode", None)
            self._session.pop("lastFailureCode", None)
            self._session.pop("completedAt", None)
            retry_audited = self._append_own_event(
                "step_retry_started",
                step=replacement,
            )
            self._update_summary()
            self._persist()
            if not retry_audited:
                return {
                    "ok": False,
                    "error": "validation_audit_unavailable",
                    "session": self._public_session(),
                }
            return {"ok": True, "session": self._public_session()}

    def abort(self, *, session_id: str) -> dict[str, Any]:
        with self._lock:
            self._expire_if_needed()
            if not self._session or session_id != self._session.get("sessionId"):
                return {"ok": False, "error": "validation_session_not_found"}
            if self._session.get("state") in TERMINAL_STATES:
                return {
                    "ok": False,
                    "error": "validation_session_terminal",
                    "session": self._public_session(),
                }
            cleanup_required, cleanup_unknown = self._cleanup_state()
            if cleanup_required:
                self._update_summary()
                self._persist()
                return {
                    "ok": False,
                    "error": "cleanup_required",
                    "cleanupStateUnknown": cleanup_unknown,
                    "session": self._public_session(),
                }
            self._session["state"] = "aborted"
            self._session["completedAt"] = self.now()
            if not self._append_own_event(
                "session_aborted", step=self._current_step()
            ):
                return {
                    "ok": False,
                    "error": "validation_audit_unavailable",
                    "session": self._public_session(),
                }
            self._finalize_report()
            return {"ok": True, "session": self._public_session()}

    def _expire_if_needed(self) -> None:
        if self._session is None or self._session.get("state") in TERMINAL_STATES:
            return
        expires_at = _finite_float(self._session.get("expiresAt"))
        if expires_at is None or self.now() >= expires_at:
            self._session["state"] = "failed"
            self._session["failureCode"] = "session_expired"
            self._session["completedAt"] = self.now()
            if not self._append_own_event(
                "session_expired", step=self._current_step()
            ):
                return
            self._finalize_report()

    def _report_payload(self) -> dict[str, Any]:
        assert self._session is not None
        return {
            "schema": REPORT_SCHEMA,
            "sessionId": self._session.get("sessionId"),
            "suite": SUITE_ID,
            "state": self._session.get("state"),
            "createdAt": self._session.get("createdAt"),
            "completedAt": self._session.get("completedAt"),
            "summary": deepcopy(
                self._session.get("summary") or self._empty_summary()
            ),
            "warnings": deepcopy(self._session.get("warnings") or []),
            "productionBlockers": self._production_blockers(),
            "steps": [self._public_step(step) for step in self._session.get("_steps") or []],
            "privacy": {
                "contentFree": True,
                "guildIdStored": False,
                "grantIdStored": False,
                "leaseIdStored": False,
                "processNonceStored": False,
                "issuerStored": False,
                "sourceStored": False,
                "rawGoalStored": False,
                "rawArgumentsStored": False,
                "chatStored": False,
                "transcriptStored": False,
                "coordinatesStored": False,
                "inventoryStored": False,
                "pathsStored": False,
            },
        }

    def _finalize_report(self) -> None:
        if self._session is None:
            return
        self._update_summary()
        session_id = str(self._session.get("sessionId") or "")
        if not _SESSION_ID_RE.fullmatch(session_id):
            return
        atomic_json_write(
            self.paths.reports / f"{session_id}.json",
            self._report_payload(),
            durable=True,
        )
        self._persist()
        self.prune_reports()

    def prune_reports(self) -> list[str]:
        self.paths.reports.mkdir(parents=True, exist_ok=True)
        self.paths.events.mkdir(parents=True, exist_ok=True)
        current = self.now()
        rows: list[tuple[Path, float]] = []
        for path in self.paths.reports.glob("*.json"):
            try:
                rows.append((path, path.stat().st_mtime))
            except OSError:
                continue
        rows.sort(key=lambda item: item[1], reverse=True)
        removed: list[str] = []
        for index, (report, modified) in enumerate(rows):
            age_days = max(0.0, current - modified) / 86400.0
            if index < REPORT_PRESERVE_NEWEST and age_days <= REPORT_MAX_AGE_DAYS:
                continue
            session_id = report.stem
            try:
                report.unlink()
                removed.append(report.name)
            except OSError:
                continue
            event_path = self.paths.events / f"{session_id}.jsonl"
            try:
                event_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                continue
        return removed


_MANAGERS: dict[str, AutonomyValidationManager] = {}
_MANAGERS_LOCK = threading.Lock()


def get_autonomy_validation_manager(
    *, root: Path | None = None
) -> AutonomyValidationManager:
    resolved = Path(root or get_runtime_artifacts_root()).resolve()
    key = str(resolved).casefold()
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            manager = AutonomyValidationManager(root=resolved)
            _MANAGERS[key] = manager
        return manager


__all__ = [
    "AutonomyValidationManager",
    "EVENT_SCHEMA",
    "MAX_ATTEMPTS",
    "MINECRAFT_POSTCONDITION_BLOCKER",
    "MINECRAFT_PRODUCTION_BLOCKERS",
    "MINECRAFT_ROUTE_BLOCKER",
    "REPORT_SCHEMA",
    "SESSION_SCHEMA",
    "SESSION_TTL_SEC",
    "SUITE_ID",
    "get_autonomy_validation_manager",
]
