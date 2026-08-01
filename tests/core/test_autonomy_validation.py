from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.autonomy_authorization import (  # noqa: E402
    AUTONOMY_AUTHORIZATION_EVENT_SCHEMA,
    AUTONOMY_AUTHORIZATION_STATUS_SCHEMA,
)
from evelyn_core.autonomy_validation import (  # noqa: E402
    MAX_ATTEMPTS,
    MINECRAFT_POSTCONDITION_BLOCKER,
    MINECRAFT_ROUTE_BLOCKER,
    REPORT_SCHEMA,
    SESSION_SCHEMA,
    SUITE_ID,
    AutonomyValidationManager,
)
from evelyn_core.minecraft_world_lease import (  # noqa: E402
    MINECRAFT_WORLD_LEASE_EVENT_SCHEMA,
)
from evelyn_core.minecraft_world_lease_contract import (  # noqa: E402
    MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
)


class Clock:
    def __init__(self, value: float | None = None) -> None:
        self.value = time.time() if value is None else value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float = 1.0) -> float:
        self.value += seconds
        return self.value


def _walk_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_keys(nested)


class AutonomyValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clock = Clock()
        self.manager = AutonomyValidationManager(
            root=self.root,
            now=self.clock,
            status_max_age_sec=30.0,
        )
        self.auth_event_index = 0
        self.lease_event_index = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def auth_status_path(self) -> Path:
        return self.root / "autonomy_authorization" / "status.json"

    @property
    def lease_status_path(self) -> Path:
        return self.root / "minecraft_world_lease" / "status.json"

    @property
    def auth_events_path(self) -> Path:
        return self.root / "autonomy_authorization" / "events" / "events.jsonl"

    @property
    def lease_events_path(self) -> Path:
        return self.root / "minecraft_world_lease" / "events" / "events.jsonl"

    def write_statuses(
        self,
        *,
        grant_active: bool = False,
        lease_active: bool = False,
        guild_id: int = 7,
        auth_process_nonce: str = "auth-process-1",
        lease_process_nonce: str = "lease-process-1",
        last_stop_outcome: str = "",
    ) -> None:
        grant = {
            "grantId": "grant-1",
            "guildId": guild_id,
            "scopes": ["assistant:check_status"],
            "issuedAt": self.clock.value - 1,
            "expiresAt": self.clock.value + 600,
        }
        authorization = {
            "schema": AUTONOMY_AUTHORIZATION_STATUS_SCHEMA,
            "state": "ready" if grant_active else "authorization_required",
            "updatedAt": self.clock.value,
            "processNonce": auth_process_nonce,
            "activeGrantCount": 1 if grant_active else 0,
            "activeGrants": [grant] if grant_active else [],
            "auditReady": True,
            "policy": {
                "restoredAfterRestart": False,
                "issuerRefPublic": False,
                "rawArguments": False,
                "transcript": False,
                "strictActionEvidenceMatch": True,
                "retryExhaustionIsEvidence": False,
            },
        }
        lease = {
            "leaseId": "lease-1",
            "guildId": guild_id,
            "expiresAt": self.clock.value + 600,
        }
        world = {
            "schema": MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
            "state": "authorized" if lease_active else "authorization_required",
            "updatedAt": self.clock.value,
            "processNonce": lease_process_nonce,
            "active": lease_active,
            "lease": lease if lease_active else None,
            "auditReady": True,
            "statusReady": True,
            "ownerClaimOwned": True,
            "ownerLockHeld": True,
            "lastStopOutcome": last_stop_outcome,
            "policy": {
                "restoredAfterRestart": False,
                "singleWorldOwner": True,
                "effectHandoffLock": True,
                "issuerRefPublic": False,
                "rawGoal": False,
                "rawArguments": False,
                "transcript": False,
            },
        }
        for path, payload in (
            (self.auth_status_path, authorization),
            (self.lease_status_path, world),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

    def append_auth_event(
        self,
        event: str,
        *,
        action: str = "",
        outcome_status: str = "",
        verified=None,
        evidence_code: str = "",
        authorization_current=None,
        scopes=None,
        action_run_id: str = "action-run-1",
        process_nonce: str = "auth-process-1",
        guild_id: int | None = 7,
        grant_id: str = "grant-1",
    ) -> dict:
        self.auth_event_index += 1
        payload = {
            "schema": AUTONOMY_AUTHORIZATION_EVENT_SCHEMA,
            "eventId": f"auth-event-{self.auth_event_index}",
            "at": self.clock.value,
            "event": event,
            "processNonce": process_nonce,
            "guildId": guild_id,
            "grantId": grant_id,
            "issuerRef": "",
            "source": "",
            "scopes": list(
                ["assistant:check_status"] if scopes is None else scopes
            ),
            "expiresAt": self.clock.value + 600,
            "action": action,
            "reasonCode": event,
            "outcomeStatus": outcome_status,
            "verified": verified,
            "evidenceCode": evidence_code,
            "authorizationCurrent": authorization_current,
            "actionRunId": action_run_id,
        }
        self.auth_events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.auth_events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def append_lease_event(
        self,
        event: str,
        *,
        outcome_code: str = "",
        verified=None,
        lease_id: str = "lease-1",
        process_nonce: str = "lease-process-1",
        guild_id: int | None = 7,
    ) -> dict:
        self.lease_event_index += 1
        payload = {
            "schema": MINECRAFT_WORLD_LEASE_EVENT_SCHEMA,
            "eventId": f"lease-event-{self.lease_event_index}",
            "at": self.clock.value,
            "event": event,
            "processNonce": process_nonce,
            "leaseId": lease_id,
            "guildId": guild_id,
            "issuerRef": "",
            "source": "",
            "expiresAt": self.clock.value + 600,
            "reasonCode": "explicit_connect",
            "outcomeCode": outcome_code,
            "verified": verified,
        }
        self.lease_events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lease_events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def start(self) -> dict:
        result = self.manager.start(
            suite=SUITE_ID,
            guild_id=7,
            dry_run=True,
        )
        self.assertTrue(result["ok"], result)
        return result["session"]

    def break_validation_audit(self) -> None:
        audit_directory = self.root / "autonomy_validation" / "events"
        for path in audit_directory.iterdir():
            path.unlink()
        audit_directory.rmdir()
        audit_directory.write_text("not-a-directory", encoding="utf-8")

    def confirm_current(self) -> dict:
        session = self.manager.snapshot()
        step = session["currentStep"]
        result = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=step["attempt"],
            acknowledged=True,
        )
        self.assertTrue(result["ok"], result)
        return result["session"]

    def advance_assistant_track(self, *, evidence_code="status_snapshot_built") -> dict:
        self.clock.advance()
        self.append_auth_event("grant_issued")
        self.write_statuses(grant_active=True)
        self.assertEqual(
            self.manager.snapshot()["currentStep"]["id"],
            "02-assistant-action-authorized",
        )

        self.clock.advance()
        self.append_auth_event(
            "action_authorized",
            action="assistant:check_status",
        )
        self.clock.advance()
        self.append_auth_event(
            "action_authorized",
            action="assistant:check_status",
        )
        self.write_statuses(grant_active=True)
        self.assertEqual(
            self.manager.snapshot()["currentStep"]["id"],
            "03-assistant-outcome-verified",
        )

        self.clock.advance()
        self.append_auth_event(
            "action_outcome",
            action="assistant:check_status",
            outcome_status="ok",
            verified=True,
            evidence_code=evidence_code,
            authorization_current=True,
        )
        self.write_statuses(grant_active=True)
        return self.manager.snapshot()

    def test_start_is_dry_only_and_public_contract_is_content_free(self):
        self.assertEqual(self.manager.snapshot()["state"], "idle")
        self.assertEqual(
            self.manager.start(suite=SUITE_ID, guild_id=7, dry_run=False)["error"],
            "dry_run_required",
        )
        session = self.start()

        self.assertEqual(session["schema"], SESSION_SCHEMA)
        self.assertEqual(session["state"], "preflight")
        self.assertTrue(session["dryRun"])
        forbidden = {
            "guildId",
            "grantId",
            "leaseId",
            "processNonce",
            "issuerRef",
            "goal",
            "chat",
            "transcript",
            "coordinates",
            "inventory",
        }
        self.assertTrue(forbidden.isdisjoint(set(_walk_keys(session))))
        active_text = (
            self.root / "autonomy_validation" / "active.json"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"guildId"', active_text)
        self.assertNotIn('"grantId"', active_text)

    def test_fresh_cold_preflight_can_abort_without_external_cleanup(self):
        session = self.start()

        result = self.manager.abort(session_id=session["sessionId"])

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["session"]["state"], "aborted")
        self.assertTrue(result["session"]["summary"]["cleanupStateUnknown"])

    def test_dirty_target_baseline_blocks_observation_confirmation(self):
        self.write_statuses(grant_active=True, lease_active=True)
        session = self.start()
        step = session["currentStep"]

        result = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=step["attempt"],
            acknowledged=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "preflight_blocked")
        self.assertIn("active_authorization_present", result["blockers"])
        self.assertIn("active_world_lease_present", result["blockers"])
        self.assertEqual(result["session"]["state"], "preflight")

    def test_exact_assistant_grant_authorization_and_outcome_pass_track(self):
        self.write_statuses()
        self.start()
        self.confirm_current()

        session = self.advance_assistant_track()

        self.assertEqual(session["currentStep"]["id"], "04-world-lease-lifecycle")
        self.assertEqual(session["summary"]["assistantTrack"], "passed")
        self.assertEqual(session["summary"]["stepsPassed"], 3)
        self.assertFalse(session["summary"]["eligibleToPass"])

    def test_wrong_assistant_evidence_fails_without_progress(self):
        self.write_statuses()
        self.start()
        self.confirm_current()

        session = self.advance_assistant_track(evidence_code="discord_send_completed")

        self.assertEqual(session["currentStep"]["id"], "03-assistant-outcome-verified")
        self.assertEqual(session["currentStep"]["status"], "failed")
        self.assertIn(
            "assistant_outcome_evidence_invalid",
            session["currentStep"]["errors"],
        )
        self.assertEqual(session["summary"]["assistantTrack"], "pending")

    def test_outcome_retry_requires_fresh_correlated_authorization(self):
        self.write_statuses()
        started = self.start()
        self.confirm_current()
        failed = self.advance_assistant_track(
            evidence_code="discord_send_completed"
        )

        retried = self.manager.retry(
            session_id=started["sessionId"],
            step_id=failed["currentStep"]["id"],
            attempt=failed["currentStep"]["attempt"],
        )

        self.assertTrue(retried["ok"], retried)
        self.assertEqual(
            retried["session"]["currentStep"]["id"],
            "02-assistant-action-authorized",
        )
        self.clock.advance()
        self.append_auth_event(
            "action_authorized",
            action="assistant:check_status",
            action_run_id="action-run-2",
        )
        self.clock.advance()
        self.append_auth_event(
            "action_authorized",
            action="assistant:check_status",
            action_run_id="action-run-2",
        )
        self.write_statuses(grant_active=True)
        self.assertEqual(
            self.manager.snapshot()["currentStep"]["id"],
            "03-assistant-outcome-verified",
        )
        self.clock.advance()
        self.append_auth_event(
            "action_outcome",
            action="assistant:check_status",
            outcome_status="ok",
            verified=True,
            evidence_code="status_snapshot_built",
            authorization_current=True,
            action_run_id="action-run-2",
        )
        self.write_statuses(grant_active=True)

        passed = self.manager.snapshot()

        self.assertEqual(passed["summary"]["assistantTrack"], "passed")
        self.assertEqual(passed["currentStep"]["id"], "04-world-lease-lifecycle")

    def test_goal_echo_never_satisfies_world_postcondition(self):
        self.write_statuses()
        self.start()
        self.confirm_current()
        self.advance_assistant_track()
        self.confirm_current()

        self.clock.advance()
        self.append_lease_event("lease_issued")
        self.clock.advance()
        self.append_lease_event(
            "runtime_start_verified",
            outcome_code="minecraft_connected",
            verified=True,
        )
        self.clock.advance()
        self.append_lease_event(
            "goal_verified",
            outcome_code="minecraft_goal_confirmed",
            verified=True,
        )
        self.write_statuses(grant_active=True, lease_active=True)

        session = self.manager.snapshot()

        self.assertEqual(session["currentStep"]["id"], "05-world-postcondition")
        self.assertEqual(session["currentStep"]["status"], "blocked")
        self.assertIn(MINECRAFT_ROUTE_BLOCKER, session["currentStep"]["errors"])
        self.assertIn(
            MINECRAFT_POSTCONDITION_BLOCKER,
            session["currentStep"]["errors"],
        )
        self.assertEqual(session["summary"]["minecraftTrack"], "blocked")
        self.assertFalse(session["summary"]["eligibleToPass"])
        self.assertNotEqual(session["state"], "passed")

    def test_abort_requires_observed_grant_cleanup_then_accepts_revoke(self):
        self.write_statuses()
        session = self.start()
        self.confirm_current()
        self.clock.advance()
        self.append_auth_event("grant_issued")
        self.write_statuses(grant_active=True)
        self.manager.snapshot()

        blocked = self.manager.abort(session_id=session["sessionId"])
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"], "cleanup_required")

        self.clock.advance()
        self.append_auth_event("grant_revoked")
        self.write_statuses(grant_active=False)
        cleaned = self.manager.abort(session_id=session["sessionId"])

        self.assertTrue(cleaned["ok"], cleaned)
        self.assertEqual(cleaned["session"]["state"], "aborted")

    def test_world_cleanup_requires_revoke_and_verified_stop(self):
        self.write_statuses()
        session = self.start()
        self.confirm_current()
        self.advance_assistant_track()
        self.confirm_current()
        self.clock.advance()
        self.append_lease_event("lease_issued")
        self.clock.advance()
        self.append_lease_event(
            "runtime_start_verified",
            outcome_code="minecraft_connected",
            verified=True,
        )
        self.write_statuses(grant_active=True, lease_active=True)
        self.manager.snapshot()

        self.clock.advance()
        self.append_auth_event("grant_revoked")
        self.clock.advance()
        self.append_lease_event("lease_revoked")
        self.write_statuses(grant_active=False, lease_active=False)
        still_blocked = self.manager.abort(session_id=session["sessionId"])
        self.assertEqual(still_blocked["error"], "cleanup_required")

        self.clock.advance()
        self.append_lease_event(
            "runtime_stop_verified",
            outcome_code="minecraft_stopped",
            verified=True,
            lease_id="lease-2",
        )
        self.write_statuses(grant_active=False, lease_active=False)
        wrong_lease = self.manager.abort(session_id=session["sessionId"])
        self.assertFalse(wrong_lease["ok"])
        self.assertEqual(wrong_lease["error"], "cleanup_required")

        self.clock.advance()
        self.append_lease_event(
            "runtime_stop_verified",
            outcome_code="minecraft_stopped",
            verified=True,
            lease_id="lease-1",
        )
        self.write_statuses(grant_active=False, lease_active=False)
        cleaned = self.manager.abort(session_id=session["sessionId"])

        self.assertTrue(cleaned["ok"], cleaned)

    def test_process_rollover_can_prove_non_restored_cleanup(self):
        self.write_statuses()
        session = self.start()
        self.confirm_current()
        self.advance_assistant_track()
        self.confirm_current()
        self.clock.advance()
        self.append_lease_event("lease_issued")
        self.clock.advance()
        self.append_lease_event(
            "runtime_start_verified",
            outcome_code="minecraft_connected",
            verified=True,
        )
        self.write_statuses(grant_active=True, lease_active=True)
        self.manager.snapshot()

        self.clock.advance()
        self.append_auth_event(
            "process_started",
            process_nonce="auth-process-2",
            guild_id=None,
            grant_id="",
            scopes=[],
        )
        self.append_lease_event(
            "process_started",
            process_nonce="lease-process-2",
            guild_id=None,
            lease_id="",
        )
        self.clock.advance()
        self.append_lease_event(
            "runtime_stop_verified",
            outcome_code="minecraft_stopped",
            verified=True,
            lease_id="",
            process_nonce="lease-process-2",
            guild_id=0,
        )
        self.write_statuses(
            grant_active=False,
            lease_active=False,
            auth_process_nonce="auth-process-2",
            lease_process_nonce="lease-process-2",
            last_stop_outcome="minecraft_stopped",
        )

        cleaned = self.manager.abort(session_id=session["sessionId"])

        self.assertTrue(cleaned["ok"], cleaned)
        self.assertEqual(cleaned["session"]["state"], "aborted")
        self.assertEqual(
            cleaned["session"]["summary"]["cleanupTrack"],
            "observed",
        )

    def test_partial_final_jsonl_line_is_ignored(self):
        self.write_statuses()
        self.start()
        self.confirm_current()
        self.clock.advance()
        event = self.append_auth_event("grant_issued")
        with self.auth_events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write('{"partial"')
        self.write_statuses(grant_active=True)

        session = self.manager.snapshot()

        self.assertEqual(event["event"], "grant_issued")
        self.assertEqual(session["currentStep"]["id"], "02-assistant-action-authorized")

    def test_stale_partial_final_jsonl_line_fails_closed(self):
        self.write_statuses()
        self.start()
        self.confirm_current()
        self.clock.advance()
        self.append_auth_event("grant_issued")
        with self.auth_events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write('{"partial"')
        stale = self.clock.value - 10
        os.utime(self.auth_events_path, (stale, stale))
        self.write_statuses(grant_active=True)

        session = self.manager.snapshot()

        self.assertEqual(session["currentStep"]["status"], "failed")
        self.assertIn(
            "authorization_events_corrupt",
            session["currentStep"]["errors"],
        )

    def test_partial_historical_jsonl_line_fails_closed(self):
        self.write_statuses()
        self.start()
        self.confirm_current()
        self.clock.advance()
        self.append_auth_event("grant_issued")
        with self.auth_events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write('{"partial"')
        historical = self.auth_events_path.with_name("a-history.jsonl")
        self.auth_events_path.replace(historical)
        current = self.auth_events_path.with_name("z-current.jsonl")
        current.write_text("\n", encoding="utf-8")
        self.write_statuses(grant_active=True)

        session = self.manager.snapshot()

        self.assertEqual(session["currentStep"]["status"], "failed")
        self.assertIn(
            "authorization_events_corrupt",
            session["currentStep"]["errors"],
        )

    def test_authorization_and_outcome_require_same_action_run(self):
        self.write_statuses()
        self.start()
        self.confirm_current()
        self.clock.advance()
        self.append_auth_event("grant_issued")
        self.write_statuses(grant_active=True)
        self.manager.snapshot()
        self.clock.advance()
        self.append_auth_event(
            "action_authorized",
            action="assistant:check_status",
            action_run_id="run-a",
        )
        self.clock.advance()
        self.append_auth_event(
            "action_authorized",
            action="assistant:check_status",
            action_run_id="run-a",
        )
        self.write_statuses(grant_active=True)
        self.manager.snapshot()
        self.clock.advance()
        self.append_auth_event(
            "action_outcome",
            action="assistant:check_status",
            outcome_status="ok",
            verified=True,
            evidence_code="status_snapshot_built",
            authorization_current=True,
            action_run_id="run-b",
        )
        self.write_statuses(grant_active=True)

        session = self.manager.snapshot()

        self.assertEqual(session["currentStep"]["id"], "03-assistant-outcome-verified")
        self.assertEqual(session["currentStep"]["status"], "pending")
        self.assertFalse(session["summary"]["assistantTrack"] == "passed")

    def test_pre_and_post_authorization_are_both_required(self):
        self.write_statuses()
        self.start()
        self.confirm_current()
        self.clock.advance()
        self.append_auth_event("grant_issued")
        self.write_statuses(grant_active=True)
        self.manager.snapshot()
        self.clock.advance()
        self.append_auth_event(
            "action_authorized",
            action="assistant:check_status",
            action_run_id="single-check",
        )
        self.write_statuses(grant_active=True)

        session = self.manager.snapshot()

        self.assertEqual(session["currentStep"]["id"], "02-assistant-action-authorized")
        self.assertEqual(session["currentStep"]["status"], "pending")
        self.assertTrue(
            session["currentStep"]["requirements"][
                "preAuthorizationObserved"
            ]
        )
        self.assertFalse(
            session["currentStep"]["requirements"][
                "postAuthorizationObserved"
            ]
        )

    def test_outcome_before_post_authorization_fails_journal_order(self):
        self.write_statuses()
        self.start()
        self.confirm_current()
        self.clock.advance()
        self.append_auth_event("grant_issued")
        self.write_statuses(grant_active=True)
        self.manager.snapshot()
        self.clock.advance()
        self.append_auth_event(
            "action_authorized",
            action="assistant:check_status",
            action_run_id="out-of-order",
        )
        self.append_auth_event(
            "action_outcome",
            action="assistant:check_status",
            outcome_status="ok",
            verified=True,
            evidence_code="status_snapshot_built",
            authorization_current=True,
            action_run_id="out-of-order",
        )
        self.append_auth_event(
            "action_authorized",
            action="assistant:check_status",
            action_run_id="out-of-order",
        )
        self.write_statuses(grant_active=True)

        session = self.manager.snapshot()

        self.assertEqual(session["currentStep"]["status"], "failed")
        self.assertIn(
            "assistant_evidence_order_invalid",
            session["currentStep"]["errors"],
        )

    def test_duplicate_outcomes_for_one_action_run_fail_closed(self):
        self.write_statuses()
        self.start()
        self.confirm_current()
        self.clock.advance()
        self.append_auth_event("grant_issued")
        self.write_statuses(grant_active=True)
        self.manager.snapshot()
        self.clock.advance()
        for _ in range(2):
            self.append_auth_event(
                "action_authorized",
                action="assistant:check_status",
                action_run_id="duplicate-outcome",
            )
        self.write_statuses(grant_active=True)
        self.manager.snapshot()
        self.clock.advance()
        for _ in range(2):
            self.append_auth_event(
                "action_outcome",
                action="assistant:check_status",
                outcome_status="ok",
                verified=True,
                evidence_code="status_snapshot_built",
                authorization_current=True,
                action_run_id="duplicate-outcome",
            )
        self.write_statuses(grant_active=True)

        session = self.manager.snapshot()

        self.assertEqual(session["currentStep"]["status"], "failed")
        self.assertIn(
            "assistant_outcome_duplicate",
            session["currentStep"]["errors"],
        )

    def test_restart_recovers_preflight_and_expiry_fails_closed(self):
        session = self.start()
        recovered = AutonomyValidationManager(
            root=self.root,
            now=self.clock,
            ttl_sec=10,
            status_max_age_sec=30,
        )
        self.assertEqual(recovered.snapshot()["sessionId"], session["sessionId"])

        self.clock.advance(1801)
        expired = recovered.snapshot()

        self.assertEqual(expired["state"], "failed")
        self.assertEqual(expired["failureCode"], "session_expired")
        report = (
            self.root
            / "autonomy_validation"
            / "reports"
            / f"{session['sessionId']}.json"
        )
        self.assertTrue(report.is_file())

    def test_forged_persisted_pass_is_rejected_on_recovery(self):
        session = self.start()
        active = self.root / "autonomy_validation" / "active.json"
        payload = json.loads(active.read_text(encoding="utf-8"))
        payload["state"] = "passed"
        active.write_text(json.dumps(payload), encoding="utf-8")

        recovered = AutonomyValidationManager(root=self.root, now=self.clock)

        snapshot = recovered.snapshot()
        self.assertEqual(snapshot["state"], "failed")
        self.assertEqual(snapshot["failureCode"], "session_recovery_invalid")
        self.assertNotEqual(snapshot["sessionId"], session["sessionId"])

    def test_retry_budget_is_three_attempts(self):
        self.write_statuses()
        session = self.start()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.write_statuses()
            current = self.manager.snapshot()["currentStep"]
            confirmed = self.manager.confirm(
                session_id=session["sessionId"],
                step_id=current["id"],
                attempt=attempt,
                acknowledged=True,
            )
            self.assertTrue(confirmed["ok"], confirmed)
            self.auth_status_path.unlink()
            failed = self.manager.snapshot()
            self.assertEqual(failed["currentStep"]["status"], "failed")
            if attempt < MAX_ATTEMPTS:
                retried = self.manager.retry(
                    session_id=session["sessionId"],
                    step_id=current["id"],
                    attempt=attempt,
                )
                self.assertTrue(retried["ok"], retried)

        exhausted = self.manager.retry(
            session_id=session["sessionId"],
            step_id="01-explicit-grant",
            attempt=MAX_ATTEMPTS,
        )
        self.assertFalse(exhausted["ok"])
        self.assertEqual(exhausted["error"], "validation_session_terminal")

    def test_retry_cannot_revive_expired_session(self):
        session = self.start()
        self.clock.advance(1801)

        retried = self.manager.retry(
            session_id=session["sessionId"],
            step_id="01-explicit-grant",
            attempt=1,
        )

        self.assertFalse(retried["ok"])
        self.assertEqual(retried["error"], "validation_session_terminal")
        self.assertEqual(retried["session"]["failureCode"], "session_expired")

    def test_retry_is_audited_and_fails_closed_if_journal_breaks(self):
        self.write_statuses()
        session = self.start()
        self.confirm_current()
        self.auth_status_path.unlink()
        failed = self.manager.snapshot()
        self.assertEqual(failed["currentStep"]["status"], "failed")
        audit_directory = self.root / "autonomy_validation" / "events"
        for path in audit_directory.iterdir():
            path.unlink()
        audit_directory.rmdir()
        audit_directory.write_text("not-a-directory", encoding="utf-8")

        retried = self.manager.retry(
            session_id=session["sessionId"],
            step_id=failed["currentStep"]["id"],
            attempt=failed["currentStep"]["attempt"],
        )

        self.assertFalse(retried["ok"])
        self.assertEqual(retried["error"], "validation_audit_unavailable")
        self.assertEqual(retried["session"]["state"], "failed")

    def test_successful_retry_records_new_attempt_event(self):
        self.write_statuses()
        session = self.start()
        self.confirm_current()
        self.auth_status_path.unlink()
        failed = self.manager.snapshot()

        retried = self.manager.retry(
            session_id=session["sessionId"],
            step_id=failed["currentStep"]["id"],
            attempt=failed["currentStep"]["attempt"],
        )

        self.assertTrue(retried["ok"], retried)
        audit_path = (
            self.root
            / "autonomy_validation"
            / "events"
            / f"{session['sessionId']}.jsonl"
        )
        rows = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        retry = [row for row in rows if row["event"] == "step_retry_started"]
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0]["attempt"], 2)

    def test_start_fails_closed_when_validation_audit_is_unavailable(self):
        audit_directory = self.root / "autonomy_validation" / "events"
        audit_directory.parent.mkdir(parents=True, exist_ok=True)
        audit_directory.write_text("not-a-directory", encoding="utf-8")

        result = self.manager.start(
            suite=SUITE_ID,
            guild_id=7,
            dry_run=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "validation_audit_unavailable")
        self.assertEqual(result["session"]["state"], "failed")
        report = (
            self.root
            / "autonomy_validation"
            / "reports"
            / f"{result['session']['sessionId']}.json"
        )
        self.assertTrue(report.is_file())

    def test_confirm_fails_closed_when_validation_audit_is_unavailable(self):
        self.write_statuses()
        session = self.start()
        audit_directory = self.root / "autonomy_validation" / "events"
        for path in audit_directory.iterdir():
            path.unlink()
        audit_directory.rmdir()
        audit_directory.write_text("not-a-directory", encoding="utf-8")
        step = session["currentStep"]

        result = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=step["attempt"],
            acknowledged=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "validation_audit_unavailable")
        self.assertEqual(result["session"]["state"], "failed")

    def test_machine_evidence_audit_failure_closes_session(self):
        self.write_statuses()
        session = self.start()
        self.confirm_current()
        self.clock.advance()
        self.append_auth_event("grant_issued")
        self.write_statuses(grant_active=True)
        self.break_validation_audit()

        failed = self.manager.snapshot()

        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["failureCode"], "validation_audit_unavailable")
        report = (
            self.root
            / "autonomy_validation"
            / "reports"
            / f"{session['sessionId']}.json"
        )
        self.assertTrue(report.is_file())

    def test_step_pass_audit_failure_cannot_advance_cursor(self):
        self.write_statuses()
        self.start()
        self.confirm_current()
        self.clock.advance()
        self.append_auth_event("grant_issued")
        self.write_statuses(grant_active=True)
        append = self.manager._append_own_event

        def fail_step_pass(event, **kwargs):
            if event == "step_passed":
                self.break_validation_audit()
            return append(event, **kwargs)

        with patch.object(
            self.manager,
            "_append_own_event",
            side_effect=fail_step_pass,
        ):
            failed = self.manager.snapshot()

        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["failureCode"], "validation_audit_unavailable")
        self.assertEqual(failed["currentStep"]["id"], "01-explicit-grant")

    def test_abort_audit_failure_returns_error(self):
        session = self.start()
        self.break_validation_audit()

        result = self.manager.abort(session_id=session["sessionId"])

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "validation_audit_unavailable")
        self.assertEqual(result["session"]["state"], "failed")

    def test_terminal_report_exposes_privacy_flags_without_private_keys(self):
        session = self.start()
        aborted = self.manager.abort(session_id=session["sessionId"])
        self.assertTrue(aborted["ok"])
        report_path = (
            self.root
            / "autonomy_validation"
            / "reports"
            / f"{session['sessionId']}.json"
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertTrue(report["privacy"]["contentFree"])
        for value in report["privacy"].values():
            if isinstance(value, bool) and value is not report["privacy"]["contentFree"]:
                self.assertFalse(value)
        forbidden = {
            "guildId",
            "grantId",
            "leaseId",
            "processNonce",
            "issuerRef",
            "source",
            "goal",
            "chat",
            "transcript",
            "coordinates",
            "inventory",
            "path",
        }
        self.assertTrue(forbidden.isdisjoint(set(_walk_keys(report))))


if __name__ == "__main__":
    unittest.main()
