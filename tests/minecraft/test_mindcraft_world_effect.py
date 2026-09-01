from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.mindcraft_world_effect import (  # noqa: E402
    MINDCRAFT_WORLD_EFFECT_BINDING_SCHEMA,
    MINDCRAFT_WORLD_EFFECT_ARCHIVE_EVENT_SCHEMA,
    MINDCRAFT_WORLD_EFFECT_EVENT_SCHEMA,
    MINDCRAFT_WORLD_EFFECT_STATUS_SCHEMA,
    MINDCRAFT_WORLD_EFFECT_TELEMETRY_SCHEMA,
    MindcraftWorldEffectProjector,
    load_mindcraft_world_effect_status,
    validate_mindcraft_world_effect_status,
)


class FakeClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class GuardProbe:
    def __init__(self) -> None:
        self.lease_ok = True
        self.readiness_ok = True
        self.lease_calls: list[dict] = []
        self.readiness_calls: list[dict] = []

    def lease(self, binding: dict) -> tuple[bool, str]:
        self.lease_calls.append(binding)
        return (
            self.lease_ok,
            "" if self.lease_ok else "guarded_lease_invalid",
        )

    def readiness(self, binding: dict) -> tuple[bool, str]:
        self.readiness_calls.append(binding)
        return (
            self.readiness_ok,
            "" if self.readiness_ok else "functional_readiness_blocked",
        )


def binding(*, sequence: int = 1) -> dict:
    return {
        "schema": MINDCRAFT_WORLD_EFFECT_BINDING_SCHEMA,
        "goalRunId": "goal-run-1",
        "actionRunId": "action-run-1",
        "actionKey": "minecraft:find_food_source",
        "contractCode": "mindcraft_food_recovery.v1",
        "leaseId": "lease-1",
        "leaseProcessNonce": "lease-process-1",
        "producerNonce": "producer-1",
        "candidateSequence": sequence,
        "contentFree": True,
    }


def candidate(*, observed_at: float = 1_001.0, sequence: int = 1) -> dict:
    return {
        "schema": MINDCRAFT_WORLD_EFFECT_TELEMETRY_SCHEMA,
        "goalRunId": "goal-run-1",
        "actionRunId": "action-run-1",
        "actionKey": "minecraft:find_food_source",
        "contractCode": "mindcraft_food_recovery.v1",
        "leaseId": "lease-1",
        "leaseProcessNonce": "lease-process-1",
        "producerNonce": "producer-1",
        "candidateSequence": sequence,
        "executionSequence": 7,
        "observedAt": observed_at,
        "evidenceCode": "mindcraft_explicit_postcondition_candidate",
        "postconditionCode": "food_reserve_ready",
        "beforeSatisfied": False,
        "afterSatisfied": True,
        "autonomous": True,
        "relevant": True,
        "actionSucceeded": True,
        "worldChanged": True,
        "goalProgress": True,
        "predicateCompleted": True,
        "completionDelta": 1,
        "blockedDelta": 0,
        "contentFree": True,
    }


def archive_context(*, command_parent: str | None = None) -> dict:
    source = binding()
    context = {
        "guildId": 7,
        "authorizationGrantId": "grant-1",
        **{
            key: source[key]
            for key in (
                "goalRunId",
                "actionRunId",
                "actionKey",
                "contractCode",
                "leaseId",
                "leaseProcessNonce",
            )
        },
        "parameters": {"privateCommand": "PRIVATE command canary"},
    }
    if command_parent is not None:
        context["parentRecordIds"] = [command_parent]
    return context


class MindcraftWorldEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.clock = FakeClock()
        self.guards = GuardProbe()
        self.projector = self.make_projector(self.root)

    def make_projector(self, root: Path) -> MindcraftWorldEffectProjector:
        return MindcraftWorldEffectProjector(
            status_path=root / "status.json",
            events_dir=root / "events",
            validate_guarded_lease=self.guards.lease,
            validate_readiness=self.guards.readiness,
            now=self.clock,
            telemetry_max_age_sec=5.0,
        )

    def read_events(self, root: Path | None = None) -> list[dict]:
        rows: list[dict] = []
        for path in (root or self.root).joinpath("events").glob("*.jsonl"):
            rows.extend(
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        return rows

    def test_initialization_is_idle_and_never_restores_armed_binding(
        self,
    ) -> None:
        first_nonce = self.projector.status()["processNonce"]
        self.assertTrue(self.projector.arm(binding())["ok"])

        replacement = self.make_projector(self.root)
        status = replacement.status()

        self.assertEqual(status["schema"], MINDCRAFT_WORLD_EFFECT_STATUS_SCHEMA)
        self.assertEqual(status["state"], "idle")
        self.assertFalse(status["armed"])
        self.assertEqual(status["binding"], {})
        self.assertNotEqual(status["processNonce"], first_nonce)
        self.assertFalse(status["policy"]["restoredAfterRestart"])

    def test_exact_fresh_false_to_true_transition_is_verified(self) -> None:
        armed = self.projector.arm(binding())
        self.clock.value = 1_001.0
        observed = self.projector.observe(candidate())

        self.assertTrue(armed["accepted"])
        self.assertTrue(observed["ok"])
        self.assertTrue(observed["verified"])
        status = observed["status"]
        self.assertEqual(status["state"], "verified")
        self.assertFalse(status["armed"])
        self.assertTrue(status["evidence"]["worldChanged"])
        self.assertTrue(status["evidence"]["goalProgress"])
        loaded, error = load_mindcraft_world_effect_status(
            self.root / "status.json",
            now=self.clock.value,
        )
        self.assertEqual(error, "")
        self.assertIsNotNone(loaded)
        verified_events = [
            row for row in self.read_events() if row["event"] == "effect_verified"
        ]
        self.assertEqual(len(verified_events), 1)
        self.assertEqual(
            verified_events[0]["schema"],
            MINDCRAFT_WORLD_EFFECT_EVENT_SCHEMA,
        )

    def test_archive_callback_receives_only_verified_final_result(self) -> None:
        archived: list[dict] = []
        projector = MindcraftWorldEffectProjector(
            status_path=self.root / "archive-status.json",
            events_dir=self.root / "archive-events",
            validate_guarded_lease=self.guards.lease,
            validate_readiness=self.guards.readiness,
            now=self.clock,
            telemetry_max_age_sec=5.0,
            archive_verified_effect=lambda event: (
                archived.append(event) is None,
                "",
            ),
            validate_archive_ready=lambda: (True, ""),
            archive_required=True,
        )

        self.assertTrue(projector.arm(binding())["accepted"])
        self.assertEqual(archived, [])
        self.clock.value = 1_001.0
        observed = projector.observe(
            candidate(),
            archive_context=archive_context(
                command_parent="minecraft-command-1"
            ),
        )

        self.assertTrue(observed["verified"])
        self.assertEqual(len(archived), 1)
        event = archived[0]
        self.assertEqual(
            event["schema"],
            MINDCRAFT_WORLD_EFFECT_ARCHIVE_EVENT_SCHEMA,
        )
        self.assertEqual(event["recordType"], "minecraft_result")
        self.assertEqual(event["guildId"], "7")
        self.assertEqual(
            event["parentRecordIds"],
            ["grant-1", "minecraft-command-1"],
        )
        self.assertTrue(event["verified"])
        self.assertTrue(event["worldChanged"])
        serialized = json.dumps(event, ensure_ascii=False)
        self.assertNotIn("PRIVATE command canary", serialized)
        self.assertNotIn("parameters", event)

    def test_verified_lifecycle_result_is_typed_under_exact_command_root(
        self,
    ) -> None:
        archived: list[dict] = []
        projector = MindcraftWorldEffectProjector(
            status_path=self.root / "lifecycle-status.json",
            events_dir=self.root / "lifecycle-events",
            validate_guarded_lease=self.guards.lease,
            validate_readiness=self.guards.readiness,
            now=self.clock,
            archive_verified_effect=lambda event: (
                archived.append(event) is None,
                "",
            ),
            validate_archive_ready=lambda: (True, ""),
            archive_required=True,
        )

        accepted, error = projector.archive_verified_lifecycle(
            guild_id=7,
            parent_record_ids=("minecraft-command-1",),
            operation="goal",
            outcome_code="minecraft_goal_confirmed",
        )

        self.assertTrue(accepted)
        self.assertEqual(error, "")
        self.assertEqual(archived[0]["recordType"], "minecraft_result")
        self.assertEqual(
            archived[0]["parentRecordIds"],
            ["minecraft-command-1"],
        )
        self.assertEqual(archived[0]["operation"], "goal")
        self.assertTrue(archived[0]["contentFree"])

    def test_archive_failure_blocks_result_and_future_actions(self) -> None:
        archived: list[dict] = []

        def reject(event: dict) -> tuple[bool, str]:
            archived.append(event)
            return False, "archive_primary_write_rejected"

        projector = MindcraftWorldEffectProjector(
            status_path=self.root / "blocked-status.json",
            events_dir=self.root / "blocked-events",
            validate_guarded_lease=self.guards.lease,
            validate_readiness=self.guards.readiness,
            now=self.clock,
            telemetry_max_age_sec=5.0,
            archive_verified_effect=reject,
            validate_archive_ready=lambda: (True, ""),
            archive_required=True,
        )
        self.assertTrue(projector.arm(binding())["accepted"])
        self.clock.value = 1_001.0

        observed = projector.observe(
            candidate(),
            archive_context=archive_context(),
        )

        self.assertFalse(observed["verified"])
        self.assertEqual(observed["code"], "archive_primary_write_rejected")
        self.assertEqual(
            observed["status"]["state"],
            "manual_intervention_required",
        )
        self.assertFalse(projector.archive_ready())
        self.assertEqual(len(archived), 1)
        self.assertEqual(
            projector.arm(binding(sequence=2))["code"],
            "mindcraft_world_effect_archive_unavailable",
        )
        rows: list[dict] = []
        for path in (self.root / "blocked-events").glob("*.jsonl"):
            rows.extend(
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        self.assertEqual(
            [row["event"] for row in rows].count("effect_verified"),
            0,
        )
        self.assertEqual(
            [row["event"] for row in rows].count("audit_failed"),
            1,
        )

    def test_required_archive_without_callback_blocks_arm(self) -> None:
        projector = MindcraftWorldEffectProjector(
            status_path=self.root / "missing-status.json",
            events_dir=self.root / "missing-events",
            validate_guarded_lease=self.guards.lease,
            validate_readiness=self.guards.readiness,
            archive_required=True,
        )

        result = projector.arm(binding())

        self.assertFalse(result["accepted"])
        self.assertEqual(
            result["code"],
            "mindcraft_world_effect_archive_unavailable",
        )

    def test_every_identity_field_must_match_exactly(self) -> None:
        for index, key in enumerate(
            (
                "goalRunId",
                "actionRunId",
                "actionKey",
                "contractCode",
                "leaseId",
                "leaseProcessNonce",
                "producerNonce",
            )
        ):
            with self.subTest(key=key):
                root = self.root / f"mismatch-{index}"
                projector = self.make_projector(root)
                self.assertTrue(projector.arm(binding())["ok"])
                self.clock.value = 1_001.0
                changed = candidate()
                changed[key] = f"different-{index}"

                observed = projector.observe(changed)

                self.assertFalse(observed["ok"])
                self.assertEqual(
                    observed["code"],
                    f"mindcraft_world_effect_{key.lower()}_mismatch",
                )
                self.assertEqual(observed["status"]["state"], "rejected")
                self.assertFalse(observed["status"]["armed"])

    def test_sequence_must_be_the_exact_next_value(self) -> None:
        for index, sequence in enumerate((0, 2)):
            with self.subTest(sequence=sequence):
                root = self.root / f"sequence-{index}"
                projector = self.make_projector(root)
                self.clock.value = 1_000.0
                self.assertTrue(projector.arm(binding())["ok"])
                self.clock.value = 1_001.0

                observed = projector.observe(candidate(sequence=sequence))

                self.assertFalse(observed["ok"])
                self.assertEqual(
                    observed["code"],
                    (
                        "mindcraft_world_effect_sequence_out_of_order"
                        if sequence == 0
                        else "mindcraft_world_effect_sequence_gap"
                    ),
                )

    def test_stale_and_future_telemetry_fail_closed(self) -> None:
        cases = (
            ("stale", 990.0, "mindcraft_world_effect_telemetry_stale"),
            (
                "future",
                1_003.0,
                "mindcraft_world_effect_telemetry_clock_invalid",
            ),
        )
        for index, (name, observed_at, code) in enumerate(cases):
            with self.subTest(name=name):
                root = self.root / f"freshness-{index}"
                self.clock.value = 1_000.0
                projector = self.make_projector(root)
                self.assertTrue(projector.arm(binding())["ok"])

                observed = projector.observe(
                    candidate(observed_at=observed_at)
                )

                self.assertFalse(observed["ok"])
                self.assertEqual(observed["code"], code)
                self.assertFalse(observed["status"]["armed"])

    def test_false_to_true_and_all_effect_flags_are_exact(self) -> None:
        bad_binding = binding()
        bad_binding["contractCode"] = "unknown_contract.v1"
        self.assertEqual(
            self.projector.arm(bad_binding)["code"],
            "mindcraft_world_effect_contract_invalid",
        )

        for index, key in enumerate(
            (
                "beforeSatisfied",
                "afterSatisfied",
                "autonomous",
                "relevant",
                "actionSucceeded",
                "worldChanged",
                "goalProgress",
                "predicateCompleted",
            )
        ):
            with self.subTest(key=key):
                root = self.root / f"flag-{index}"
                self.clock.value = 1_000.0
                projector = self.make_projector(root)
                self.assertTrue(projector.arm(binding())["ok"])
                self.clock.value = 1_001.0
                telemetry = candidate()
                telemetry[key] = key == "beforeSatisfied"

                observed = projector.observe(telemetry)

                self.assertEqual(
                    observed["code"],
                    "mindcraft_world_effect_transition_unproven",
                )
                self.assertFalse(observed["verified"])

    def test_guarded_lease_and_readiness_are_rechecked_at_observe(self) -> None:
        self.assertTrue(self.projector.arm(binding())["ok"])
        self.assertEqual(len(self.guards.lease_calls), 1)
        self.assertEqual(len(self.guards.readiness_calls), 1)
        self.guards.readiness_ok = False
        self.clock.value = 1_001.0

        observed = self.projector.observe(candidate())

        self.assertEqual(observed["code"], "functional_readiness_blocked")
        self.assertEqual(len(self.guards.lease_calls), 2)
        self.assertEqual(len(self.guards.readiness_calls), 2)
        self.assertFalse(observed["status"]["armed"])

    def test_recursive_sensitive_key_is_rejected_and_never_persisted(self) -> None:
        self.assertTrue(self.projector.arm(binding())["ok"])
        self.clock.value = 1_001.0
        telemetry = candidate()
        telemetry["metadata"] = {
            "nested": {"rawTranscript": "PRIVATE-SENTINEL"}
        }

        observed = self.projector.observe(telemetry)

        self.assertEqual(
            observed["code"],
            "mindcraft_world_effect_candidate_invalid",
        )
        persisted = (self.root / "status.json").read_text(encoding="utf-8")
        persisted += json.dumps(self.read_events(), ensure_ascii=False)
        self.assertNotIn("PRIVATE-SENTINEL", persisted)
        self.assertNotIn("rawTranscript", persisted)

    def test_event_is_fsynced_and_status_writes_are_durable(self) -> None:
        with patch(
            "evelyn_core.mindcraft_world_effect.os.fsync",
            wraps=os.fsync,
        ) as sync, patch(
            "evelyn_core.mindcraft_world_effect.atomic_json_write",
            wraps=__import__(
                "evelyn_core.runtime_artifact_io",
                fromlist=["atomic_json_write"],
            ).atomic_json_write,
        ) as write:
            result = self.projector.arm(binding())

        self.assertTrue(result["ok"])
        self.assertGreaterEqual(sync.call_count, 1)
        self.assertGreaterEqual(write.call_count, 2)
        self.assertTrue(
            all(call.kwargs.get("durable") is True for call in write.call_args_list)
        )

    def test_audit_and_status_failures_clear_authority(self) -> None:
        original_open = Path.open

        def fail_event_open(path: Path, *args, **kwargs):
            if path.suffix == ".jsonl":
                raise OSError("audit unavailable")
            return original_open(path, *args, **kwargs)

        # Fail only the append-only audit sink. Mocking os.fsync globally
        # also breaks the independent durable status writer and therefore
        # tests the wrong (status failure) branch.
        with patch("pathlib.Path.open", new=fail_event_open):
            audit_failure = self.projector.arm(binding())
        self.assertEqual(
            audit_failure["code"],
            "mindcraft_world_effect_audit_unavailable",
        )
        self.assertEqual(
            audit_failure["status"]["state"],
            "manual_intervention_required",
        )
        self.assertFalse(audit_failure["status"]["armed"])

        root = self.root / "status-failure"
        projector = self.make_projector(root)
        with patch(
            "evelyn_core.mindcraft_world_effect.atomic_json_write",
            side_effect=OSError("status unavailable"),
        ):
            status_failure = projector.arm(binding())
        self.assertEqual(
            status_failure["code"],
            "mindcraft_world_effect_status_write_failed",
        )
        self.assertFalse(status_failure["status"]["armed"])
        self.assertFalse(status_failure["status"]["statusReady"])

    def test_status_observer_rejects_tamper_and_staleness(self) -> None:
        status = self.projector.status()
        validated, error = validate_mindcraft_world_effect_status(
            status,
            now=self.clock.value,
        )
        self.assertEqual(error, "")
        self.assertIsNotNone(validated)

        tampered = json.loads(json.dumps(status))
        tampered["evidence"]["worldChanged"] = "yes"
        self.assertEqual(
            validate_mindcraft_world_effect_status(
                tampered,
                now=self.clock.value,
            )[1],
            "mindcraft_world_effect_status_invalid",
        )
        for invalid_max_age in (
            True,
            0.0,
            float("nan"),
            float("inf"),
            {"seconds": 5.0},
            [5.0],
            "not-a-number",
            10**1_000,
        ):
            with self.subTest(telemetry_max_age_sec=invalid_max_age):
                tampered = json.loads(json.dumps(status))
                tampered["policy"]["telemetryMaxAgeSec"] = invalid_max_age
                tampered = json.loads(json.dumps(tampered))
                self.assertEqual(
                    validate_mindcraft_world_effect_status(
                        tampered,
                        now=self.clock.value,
                    ),
                    (None, "mindcraft_world_effect_status_invalid"),
                )
        self.assertEqual(
            validate_mindcraft_world_effect_status(
                status,
                now=self.clock.value + 20.0,
            )[1],
            "mindcraft_world_effect_status_stale",
        )


if __name__ == "__main__":
    unittest.main()
