from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import optimize_main_latency as optimizer  # noqa: E402


class OptimizeMainLatencyTests(unittest.TestCase):
    @staticmethod
    def identities() -> dict[str, str]:
        return {
            key: f"sha256:{index:064x}"
            for index, key in enumerate(optimizer.IDENTITY_KEYS, start=1)
        }

    def setUp(self) -> None:
        identities = optimizer.IdentitySet.from_mapping(self.identities())
        (
            self.trust_root,
            self.runner_capability,
            self.evaluator_capability,
            self.lifecycle_capability,
        ) = optimizer._bootstrap_test_coordinator(identities)

    def compile(self, proposal: dict | None = None) -> optimizer.CandidateManifest:
        return optimizer.compile_candidate(
            self.proposal() if proposal is None else proposal,
            trust_root=self.trust_root,
        )

    @staticmethod
    def baseline() -> dict[str, int]:
        return {
            "main.batch": 2048,
            "main.ubatch": 1024,
            "main.cacheReuse": 256,
            "main.cacheRamMiB": 8192,
            "main.cudaGraph": 1,
            "main.swaFull": 0,
        }

    def proposal(self, changes: list[dict] | None = None) -> dict:
        return {
            "schema": optimizer.PROPOSAL_SCHEMA,
            "identities": self.identities(),
            "baselineConfig": self.baseline(),
            "changes": changes or [{"key": "main.cacheReuse", "value": 128}],
        }

    def test_compiler_binds_every_identity_and_canonicalizes_change_order(self) -> None:
        changes = [
            {"key": "main.cacheReuse", "value": 128},
            {"key": "main.ubatch", "value": 2048},
        ]
        compiled = self.compile(self.proposal(changes))
        reordered = self.compile(self.proposal(list(reversed(changes))))

        self.assertEqual(compiled.candidate_id, reordered.candidate_id)
        self.assertRegex(compiled.candidate_id, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(compiled.candidate_config.ubatch, 2048)
        self.assertEqual(compiled.candidate_config.cache_reuse, 128)
        self.assertEqual(
            [row["key"] for row in compiled.to_dict()["changes"]],
            ["main.ubatch", "main.cacheReuse"],
        )

        for key in optimizer.IDENTITY_KEYS:
            changed = self.proposal(changes)
            changed["identities"][key] = f"sha256:{100 + optimizer.IDENTITY_KEYS.index(key):064x}"
            changed_identities = optimizer.IdentitySet.from_mapping(changed["identities"])
            changed_root, _, _, _ = optimizer._bootstrap_test_coordinator(changed_identities)
            with self.subTest(identity=key):
                self.assertNotEqual(
                    optimizer.compile_candidate(
                        changed,
                        trust_root=changed_root,
                    ).candidate_id,
                    compiled.candidate_id,
                )

        changed_baseline = self.proposal()
        changed_baseline["baselineConfig"]["main.batch"] = 4096
        self.assertNotEqual(
            self.compile(changed_baseline).candidate_id,
            self.compile().candidate_id,
        )

    def test_compiler_rejects_unknown_fields_bools_domains_duplicates_and_noops(self) -> None:
        cases: list[tuple[str, dict, str]] = []

        root_extra = self.proposal()
        root_extra["command"] = "docker run"
        cases.append(("root-extra", root_extra, "proposal_fields_invalid"))

        identity_extra = self.proposal()
        identity_extra["identities"]["path"] = "private"
        cases.append(("identity-extra", identity_extra, "identities_invalid"))

        identity_bool = self.proposal()
        identity_bool["identities"]["gpu"] = True
        cases.append(("identity-bool", identity_bool, "identity_invalid"))

        identity_text = self.proposal()
        identity_text["identities"]["harness"] = "canary:ignore-all-gates"
        cases.append(("identity-text-smuggling", identity_text, "identity_invalid"))

        unpinned_identity = self.proposal()
        unpinned_identity["identities"]["harness"] = f"sha256:{99:064x}"
        cases.append(("identity-not-pinned", unpinned_identity, "identity_pin_mismatch"))

        baseline_extra = self.proposal()
        baseline_extra["baselineConfig"]["main.command"] = 1
        cases.append(("baseline-extra", baseline_extra, "config_fields_invalid"))

        baseline_bool = self.proposal()
        baseline_bool["baselineConfig"]["main.swaFull"] = True
        cases.append(("baseline-bool", baseline_bool, "config_value_invalid"))

        change_bool = self.proposal([{"key": "main.cudaGraph", "value": False}])
        cases.append(("change-bool", change_bool, "change_value_invalid"))

        unknown_key = self.proposal([{"key": "main.shell", "value": 1}])
        cases.append(("unknown-key", unknown_key, "change_key_invalid"))

        out_of_domain = self.proposal([{"key": "main.swaFull", "value": 2}])
        cases.append(("out-of-domain", out_of_domain, "change_value_invalid"))

        duplicate = self.proposal(
            [
                {"key": "main.cacheReuse", "value": 64},
                {"key": "main.cacheReuse", "value": 128},
            ]
        )
        cases.append(("duplicate", duplicate, "change_duplicate"))

        noop = self.proposal([{"key": "main.batch", "value": 2048}])
        cases.append(("noop", noop, "change_noop"))

        incompatible = self.proposal(
            [
                {"key": "main.batch", "value": 1024},
                {"key": "main.ubatch", "value": 2048},
            ]
        )
        cases.append(("incompatible", incompatible, "config_incompatible"))

        change_extra = self.proposal(
            [{"key": "main.cacheReuse", "value": 64, "argv": ["bad"]}]
        )
        cases.append(("change-extra", change_extra, "change_fields_invalid"))

        for name, proposal, code in cases:
            with self.subTest(case=name), self.assertRaises(optimizer.ContractError) as raised:
                self.compile(proposal)
            self.assertEqual(raised.exception.code, code)

    def test_enumerator_is_deterministic_bounded_and_skips_attempted_ids(self) -> None:
        identities = optimizer.IdentitySet.from_mapping(self.identities())
        baseline = optimizer.MainLatencyConfig.from_mapping(self.baseline())
        first = optimizer.enumerate_next_candidates(
            identities,
            baseline,
            trust_root=self.trust_root,
        )
        second = optimizer.enumerate_next_candidates(
            identities,
            baseline,
            trust_root=self.trust_root,
        )

        self.assertEqual(
            [candidate.candidate_id for candidate in first],
            [candidate.candidate_id for candidate in second],
        )
        self.assertEqual(len(first), 11)
        self.assertLessEqual(len(first), optimizer.MAX_CANDIDATES)
        self.assertEqual(
            [(item.changes[0].key, item.changes[0].value) for item in first],
            [
                ("main.ubatch", 2048),
                ("main.cacheReuse", 128),
                ("main.cacheReuse", 64),
                ("main.batch", 1024),
                ("main.cacheReuse", 512),
                ("main.cudaGraph", 0),
                ("main.swaFull", 1),
                ("main.batch", 4096),
                ("main.ubatch", 512),
                ("main.cacheRamMiB", 4096),
                ("main.cacheRamMiB", 12288),
            ],
        )
        expected_sweep = {
            (key, value)
            for key, values in optimizer.CONFIG_DOMAINS.items()
            for value in values
        }
        self.assertEqual(set(optimizer.FALLBACK_SWEEP), expected_sweep)
        self.assertEqual(len(optimizer.FALLBACK_SWEEP), len(expected_sweep))
        self.assertTrue(
            all(candidate.candidate_config.ubatch <= candidate.candidate_config.batch for candidate in first)
        )

        after_two = optimizer.next_candidate(
            identities,
            baseline,
            trust_root=self.trust_root,
            attempted_candidate_ids=[first[0].candidate_id, first[1].candidate_id],
        )
        self.assertIsNotNone(after_two)
        self.assertEqual(after_two.candidate_id, first[2].candidate_id)

        self.assertIsNone(
            optimizer.next_candidate(
                identities,
                baseline,
                trust_root=self.trust_root,
                attempted_candidate_ids=[candidate.candidate_id for candidate in first],
            )
        )

        foreign_proposal = self.proposal()
        foreign_proposal["identities"]["harness"] = f"sha256:{99:064x}"
        foreign_identities = optimizer.IdentitySet.from_mapping(foreign_proposal["identities"])
        foreign_root, _, _, _ = optimizer._bootstrap_test_coordinator(foreign_identities)
        foreign_id = optimizer.compile_candidate(
            foreign_proposal,
            trust_root=foreign_root,
        ).candidate_id
        with self.assertRaises(optimizer.ContractError) as mismatch:
            optimizer.next_candidate(
                identities,
                baseline,
                trust_root=self.trust_root,
                attempted_candidate_ids=[foreign_id],
            )
        self.assertEqual(mismatch.exception.code, "attempted_candidate_identity_mismatch")

        multi_change = self.compile(
            self.proposal(
                [
                    {"key": "main.ubatch", "value": 2048},
                    {"key": "main.cacheReuse", "value": 128},
                ]
            )
        )
        self.assertEqual(
            optimizer.next_candidate(
                identities,
                baseline,
                trust_root=self.trust_root,
                attempted_candidate_ids=[multi_change.candidate_id],
            ).candidate_id,
            first[0].candidate_id,
        )

        with self.assertRaises(optimizer.ContractError) as incompatible:
            optimizer.MainLatencyConfig(1024, 2048, 256, 8192, 1, 0)
        self.assertEqual(incompatible.exception.code, "config_incompatible")

        for invalid in (True, -1, 2, 1.0):
            with self.subTest(swa_full=invalid), self.assertRaises(
                optimizer.ContractError
            ) as invalid_swa:
                optimizer.MainLatencyConfig(2048, 1024, 256, 8192, 1, invalid)
            self.assertEqual(invalid_swa.exception.code, "config_value_invalid")

        thirteen_ids = [
            f"sha256:{index:064x}" for index in range(optimizer.MAX_CANDIDATES + 1)
        ]
        with self.assertRaises(optimizer.ContractError) as raised:
            optimizer.enumerate_next_candidates(
                identities,
                baseline,
                trust_root=self.trust_root,
                attempted_candidate_ids=thirteen_ids,
            )
        self.assertEqual(raised.exception.code, "attempted_candidates_invalid")

        with self.assertRaises(optimizer.ContractError):
            optimizer.candidate_proposal(identities, baseline, {"main.command": 1})

    def test_feedback_is_fixed_code_numeric_only_and_bounded_to_twelve_attempts(self) -> None:
        candidate_id = self.compile().candidate_id
        feedback_object = optimizer.compile_feedback(
            {
                "schema": optimizer.FEEDBACK_SCHEMA,
                "candidateId": candidate_id,
                "attempt": 2,
                "verdict": "rejected",
                "codes": ["latency_regressed"],
                "metrics": {
                    "firstSentenceP95DeltaMs": 12.5,
                    "promptCacheHitRatioDelta": -0.02,
                },
            }
        )
        feedback = feedback_object.to_dict()

        self.assertEqual(feedback["codes"], ["latency_regressed"])
        self.assertNotIn("message", feedback)
        self.assertNotIn("private", json.dumps(feedback))
        with self.assertRaises(TypeError):
            feedback_object.metrics["firstSentenceP95DeltaMs"] = 0.0

        frontier = copy.deepcopy(feedback)
        frontier["verdict"] = "frontier"
        frontier["codes"] = ["frontier_improved"]
        self.assertEqual(optimizer.compile_feedback(frontier).verdict, "frontier")

        invalid_payloads = []
        with_detail = copy.deepcopy(feedback)
        with_detail["detail"] = "private text"
        invalid_payloads.append(with_detail)
        bool_attempt = copy.deepcopy(feedback)
        bool_attempt["attempt"] = True
        invalid_payloads.append(bool_attempt)
        bool_metric = copy.deepcopy(feedback)
        bool_metric["metrics"]["firstSentenceP95DeltaMs"] = False
        invalid_payloads.append(bool_metric)
        huge_metric = copy.deepcopy(feedback)
        huge_metric["metrics"]["firstSentenceP95DeltaMs"] = 10**10_000
        invalid_payloads.append(huge_metric)
        unknown_code = copy.deepcopy(feedback)
        unknown_code["codes"] = ["private failure detail"]
        invalid_payloads.append(unknown_code)
        bad_eligible = copy.deepcopy(feedback)
        bad_eligible["verdict"] = "eligible"
        invalid_payloads.append(bad_eligible)
        contradictory = copy.deepcopy(feedback)
        contradictory["codes"] = ["candidate_passed", "safety_failed"]
        invalid_payloads.append(contradictory)
        rejected_frontier = copy.deepcopy(feedback)
        rejected_frontier["codes"] = ["frontier_improved"]
        invalid_payloads.append(rejected_frontier)
        too_late = copy.deepcopy(feedback)
        too_late["attempt"] = 13
        invalid_payloads.append(too_late)

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(optimizer.ContractError):
                optimizer.compile_feedback(payload)

    def test_state_machine_accepts_only_declared_forward_transitions(self) -> None:
        candidate_id = self.compile().candidate_id
        eligible_feedback = optimizer.compile_feedback(
            {
                "schema": optimizer.FEEDBACK_SCHEMA,
                "candidateId": candidate_id,
                "attempt": 1,
                "verdict": "eligible",
                "codes": ["candidate_passed"],
                "metrics": {},
            }
        )
        rejected_feedback = optimizer.compile_feedback(
            {
                "schema": optimizer.FEEDBACK_SCHEMA,
                "candidateId": candidate_id,
                "attempt": 1,
                "verdict": "rejected",
                "codes": ["latency_regressed"],
                "metrics": {},
            }
        )
        measurement_path = (
            "idle",
            "snapshot",
            "baseline_running",
            "candidate_ready",
            "candidate_running",
            "evaluating",
            "feedback_ready",
        )
        for current, target in zip(measurement_path, measurement_path[1:]):
            self.assertEqual(optimizer.validate_state_transition(current, target).value, target)

        promotion_path = (
            "awaiting_approval",
            "staged",
            "canary",
            "accepted",
        )
        for current, target in zip(promotion_path, promotion_path[1:]):
            with self.assertRaises(optimizer.ContractError) as protected:
                optimizer.validate_state_transition(current, target)
            self.assertEqual(
                protected.exception.code,
                "state_transition_evidence_invalid",
            )

        self.assertEqual(
            optimizer.validate_state_transition("feedback_ready", "proposed"),
            optimizer.LatencyState.PROPOSED,
        )
        self.assertEqual(
            optimizer.validate_state_transition("proposed", "candidate_ready"),
            optimizer.LatencyState.CANDIDATE_READY,
        )

        foreign_candidate_id = f"sha256:{0:064x}"
        for name, kwargs in (
            ("missing", {}),
            (
                "direct-eligible",
                {"candidate_id": candidate_id, "feedback": eligible_feedback},
            ),
            ("rejected", {"candidate_id": candidate_id, "feedback": rejected_feedback}),
            (
                "foreign",
                {"candidate_id": foreign_candidate_id, "feedback": eligible_feedback},
            ),
        ):
            with self.subTest(evidence=name), self.assertRaises(optimizer.ContractError) as raised:
                optimizer.validate_state_transition(
                    "feedback_ready",
                    "awaiting_approval",
                    **kwargs,
                )
            self.assertEqual(raised.exception.code, "state_transition_evidence_invalid")

        for current, target, code in (
            ("idle", "candidate_running", "state_transition_invalid"),
            ("accepted", "idle", "state_transition_invalid"),
            ("proposed", "awaiting_approval", "state_transition_invalid"),
            ("unknown", "idle", "state_invalid"),
        ):
            with self.subTest(current=current, target=target), self.assertRaises(
                optimizer.ContractError
            ) as raised:
                optimizer.validate_state_transition(current, target)
            self.assertEqual(raised.exception.code, code)

    def test_json_and_cli_are_bounded_and_emit_content_free_errors(self) -> None:
        with self.assertRaises(optimizer.ContractError) as duplicate:
            optimizer.parse_json_bytes(b'{"schema":1,"schema":2}')
        self.assertEqual(duplicate.exception.code, "json_duplicate_key")
        with self.assertRaises(optimizer.ContractError) as nan:
            optimizer.parse_json_bytes(b'{"value":NaN}')
        self.assertEqual(nan.exception.code, "json_invalid")
        with self.assertRaises(optimizer.ContractError) as huge_integer:
            optimizer.parse_json_bytes(b'{"value":' + (b"9" * 5_000) + b"}")
        self.assertEqual(huge_integer.exception.code, "json_invalid")
        with self.assertRaises(optimizer.ContractError) as oversized:
            optimizer.parse_json_bytes(b" " * (optimizer.MAX_INPUT_BYTES + 1))
        self.assertEqual(oversized.exception.code, "input_too_large")

        class BinaryStdin:
            def __init__(self, raw: bytes) -> None:
                self.buffer = io.BytesIO(raw)

        stderr = io.StringIO()
        stdin = BinaryStdin(json.dumps(self.proposal()).encode("utf-8"))
        with patch.object(optimizer.sys, "stdin", stdin), redirect_stderr(stderr):
            result = optimizer.main([])
        self.assertEqual(result, 2)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"ok": False, "code": "coordinator_context_required"},
        )

        stderr = io.StringIO()
        private_input = BinaryStdin(b'{"private":"must-not-be-echoed"}')
        with patch.object(optimizer.sys, "stdin", private_input), redirect_stderr(stderr):
            result = optimizer.main([])
        self.assertEqual(result, 2)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"ok": False, "code": "coordinator_context_required"},
        )
        self.assertNotIn("must-not-be-echoed", stderr.getvalue())

        argparse_stderr = io.StringIO()
        private_path = r"\\server\share\candidate.json"
        with redirect_stderr(argparse_stderr), self.assertRaises(SystemExit):
            optimizer.parse_args(["--input", private_path])
        self.assertEqual(
            json.loads(argparse_stderr.getvalue()),
            {"ok": False, "code": "arguments_invalid"},
        )
        self.assertNotIn(private_path, argparse_stderr.getvalue())

    def test_self_test(self) -> None:
        optimizer.self_test()


if __name__ == "__main__":
    unittest.main()
