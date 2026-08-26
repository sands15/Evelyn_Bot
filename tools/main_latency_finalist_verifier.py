"""Offline verification for a completed Main latency finalist artifact."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__:
    from tools.main_latency_lab_contract import (
        build_runner_plan,
        compile_host_restoration_proof,
        compile_runner_receipt,
        evaluate_runner_receipt,
    )
    from tools.optimize_main_latency import (
        IdentitySet,
        MainLatencyConfig,
        bootstrap_ephemeral_fixed_coordinator,
        candidate_proposal,
        compile_candidate,
    )
else:
    from main_latency_lab_contract import (
        build_runner_plan,
        compile_host_restoration_proof,
        compile_runner_receipt,
        evaluate_runner_receipt,
    )
    from optimize_main_latency import (
        IdentitySet,
        MainLatencyConfig,
        bootstrap_ephemeral_fixed_coordinator,
        candidate_proposal,
        compile_candidate,
    )


ARTIFACT_SCHEMA = "evelyn.main-latency-finalist-validation-artifact.v1"
VERIFICATION_SCHEMA = "evelyn.main-latency-offline-verification.v1"
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_JOURNAL_BYTES = 4 * 1024 * 1024


class FinalistVerificationError(RuntimeError):
    pass


def _fail() -> None:
    raise FinalistVerificationError("finalist_artifact_invalid")


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail()
    return dict(value)


def verify_completed_artifact(path: str | Path) -> dict[str, Any]:
    artifact = Path(path).resolve()
    try:
        if artifact.is_symlink() or not artifact.is_file():
            _fail()
        raw_bytes = artifact.read_bytes()
        if not 0 < len(raw_bytes) <= MAX_ARTIFACT_BYTES:
            _fail()
        state = json.loads(raw_bytes.decode("ascii"))
    except FinalistVerificationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail()
    state = _mapping(state)
    if state.get("schema") != ARTIFACT_SCHEMA or state.get("status") != "completed":
        _fail()

    plan_raw = _mapping(state.get("plan"))
    journal_value = state.get("authorityJournal")
    if not isinstance(journal_value, str):
        _fail()
    journal = Path(journal_value).resolve()
    expected_journal = artifact.with_name(artifact.stem + ".authority.sqlite3")
    try:
        if (
            journal != expected_journal
            or journal.is_symlink()
            or not journal.is_file()
            or not 0 < journal.stat().st_size <= MAX_JOURNAL_BYTES
        ):
            _fail()
    except OSError:
        _fail()

    identities = IdentitySet.from_mapping(plan_raw.get("identities"))
    baseline = MainLatencyConfig.from_mapping(plan_raw.get("baselineConfig"))
    changes_raw = plan_raw.get("changes")
    if not isinstance(changes_raw, list):
        _fail()
    changes: dict[str, int] = {}
    for item in changes_raw:
        if (
            not isinstance(item, dict)
            or set(item) != {"key", "from", "to"}
            or not isinstance(item["key"], str)
            or isinstance(item["to"], bool)
            or not isinstance(item["to"], int)
            or item["key"] in changes
        ):
            _fail()
        changes[item["key"]] = item["to"]

    trust_root = None
    try:
        (
            trust_root,
            _runner_capability,
            evaluator_capability,
            _lifecycle_capability,
        ) = bootstrap_ephemeral_fixed_coordinator(
            identities,
            journal_path=journal,
        )
        candidate = compile_candidate(
            candidate_proposal(identities, baseline, changes),
            trust_root=trust_root,
        )
        plan = build_runner_plan(
            candidate,
            profile=plan_raw.get("profile"),
            attempt=plan_raw.get("attempt"),
            trust_root=trust_root,
        )
        if plan.to_dict() != plan_raw:
            _fail()
        receipt = compile_runner_receipt(
            plan,
            _mapping(state.get("receipt")),
            trust_root=trust_root,
        )
        host_proof = compile_host_restoration_proof(
            plan,
            receipt,
            _mapping(state.get("hostRestorationProof")),
            trust_root=trust_root,
        )
        evaluation = evaluate_runner_receipt(
            plan,
            receipt,
            trust_root=trust_root,
            evaluator_capability=evaluator_capability,
            host_restoration_proof=host_proof,
        )
        if (
            evaluation.to_dict() != _mapping(state.get("evaluation"))
            or evaluation.verdict != "eligible"
            or evaluation.code != "candidate_passed"
            or evaluation.gate != "passed"
            or state.get("runId") != plan.run_id
            or state.get("candidateId") != candidate.candidate_id
        ):
            _fail()
        return {
            "schema": VERIFICATION_SCHEMA,
            "status": "verified",
            "authorityId": trust_root.authority_id,
            "identityDigest": trust_root.identity_digest,
            "runId": plan.run_id,
            "candidateId": candidate.candidate_id,
            "receiptId": receipt.receipt_id,
            "cleanupProofId": receipt.cleanup.proof_id,
            "hostRestorationProofId": host_proof.proof_id,
            "evaluationId": evaluation.evaluation_id,
        }
    except FinalistVerificationError:
        raise
    except Exception:
        _fail()
    finally:
        if trust_root is not None:
            trust_root.close()
    raise AssertionError("unreachable")


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    try:
        result = verify_completed_artifact(sys.argv[1])
    except FinalistVerificationError:
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
