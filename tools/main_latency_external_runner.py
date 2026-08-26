"""Fixed, content-free runner boundary for the installed owned lab adapter."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


MAX_INPUT_BYTES = 1_048_576
REQUEST_SCHEMA = "evelyn.latency-runner-request.v1"
CAPABILITY_SCHEMA = "evelyn.runner-one-run-capability.v1"
PLAN_SCHEMA = "evelyn.latency-runner-plan.v2"
DIAGNOSTIC_SCHEMA = "evelyn.latency-external-runner-diagnostic.v1"
PRIVATE_RESULT_SCHEMA = "evelyn.latency-external-runner-result.v1"
HASH_ID = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
SECRET = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
DIAGNOSTIC_CODES = frozenset(
    {
        "change_duplicate",
        "change_key_invalid",
        "change_noop",
        "change_value_invalid",
        "changes_invalid",
        "cleanup_proof_invalid",
        "config_fields_invalid",
        "config_incompatible",
        "config_value_invalid",
        "coordinator_trust_root_invalid",
        "external_runner_contract_failed",
        "external_runner_failed",
        "external_runner_import_failed",
        "external_runner_request_invalid",
        "identities_invalid",
        "identity_binding_invalid",
        "identity_invalid",
        "identity_pin_mismatch",
        "lab_preflight_invalid",
        "lab_receipt_invalid",
        "plan_binding_invalid",
        "proposal_schema_invalid",
        "runner_attestation_invalid",
        "runner_bounds_exceeded",
        "runner_candidate_invalid",
        "runner_capability_invalid",
        "runner_checks_invalid",
        "runner_equivalence_invalid",
        "runner_metrics_invalid",
        "runner_plan_invalid",
        "runner_profile_invalid",
        "runner_receipt_auth_invalid",
        "runner_receipt_binding_invalid",
        "runner_receipt_fields_invalid",
        "runner_receipt_invalid",
        "runner_receipt_schema_invalid",
        "runner_resources_invalid",
        "runner_samples_invalid",
        "runner_statistics_invalid",
        "runner_status_invalid",
    }
)


def _deny_process_creation(event: str, _args: tuple[Any, ...]) -> None:
    if event == "subprocess.Popen":
        try:
            import main_latency_fixed_lab_adapter as fixed_lab

            executable = _args[0]
            argv = _args[1]
        except (ImportError, IndexError):
            pass
        else:
            if fixed_lab.is_fixed_lab_worker_command(executable, argv):
                return
        raise RuntimeError("external_runner_child_process_forbidden")
    if event in {"os.system", "os.fork", "pty.spawn"} or event.startswith(
        ("os.spawn", "os.exec", "os.posix_spawn", "os.startfile")
    ):
        raise RuntimeError("external_runner_child_process_forbidden")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _diagnostic_response(exc: Exception) -> dict[str, str]:
    if isinstance(exc, ImportError):
        code = "external_runner_import_failed"
    elif isinstance(exc, (UnicodeDecodeError, json.JSONDecodeError)):
        code = "external_runner_request_invalid"
    elif isinstance(exc, ValueError):
        candidate = str(exc)
        code = (
            candidate
            if candidate in DIAGNOSTIC_CODES
            else "external_runner_contract_failed"
        )
    else:
        code = "external_runner_failed"
    return {"schema": DIAGNOSTIC_SCHEMA, "code": code}


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise ValueError("request_size_invalid")
    value = json.loads(raw.decode("ascii"), object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "plan",
        "runnerCapability",
    }:
        raise ValueError("request_invalid")
    plan = value["plan"]
    capability = value["runnerCapability"]
    if (
        value["schema"] != REQUEST_SCHEMA
        or not isinstance(plan, dict)
        or plan.get("schema") != PLAN_SCHEMA
        or not isinstance(capability, dict)
        or set(capability)
        != {"schema", "authorityId", "identityDigest", "runId", "secret"}
        or capability["schema"] != CAPABILITY_SCHEMA
    ):
        raise ValueError("request_invalid")
    ids = (
        capability["authorityId"],
        capability["identityDigest"],
        capability["runId"],
        plan.get("authorityId"),
        plan.get("runId"),
    )
    if any(not isinstance(item, str) or HASH_ID.fullmatch(item) is None for item in ids):
        raise ValueError("request_invalid")
    if (
        capability["authorityId"] != plan["authorityId"]
        or capability["runId"] != plan["runId"]
        or not isinstance(capability["secret"], str)
        or SECRET.fullmatch(capability["secret"]) is None
    ):
        raise ValueError("request_invalid")
    return value


def _runner_measurement(
    request: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import main_latency_lab_contract as lab
    import main_latency_fixed_lab_adapter as lab_adapter
    import optimize_main_latency as optimizer

    plan_raw = request["plan"]
    transfer = request["runnerCapability"]
    secret = bytes.fromhex(transfer.pop("secret"))
    identities = optimizer.IdentitySet.from_mapping(plan_raw["identities"])
    if optimizer._identity_digest(identities) != transfer["identityDigest"]:
        raise ValueError("identity_binding_invalid")
    root = optimizer.CoordinatorTrustRoot(
        transfer["authorityId"],
        transfer["identityDigest"],
        identities,
        secret,
        bytes(32),
        bytes(32),
        None,
        optimizer._COORDINATOR_BOOTSTRAP_TOKEN,
    )
    runner_capability = optimizer.RunnerCapability(
        secret,
        transfer["authorityId"],
        transfer["identityDigest"],
        optimizer._COORDINATOR_BOOTSTRAP_TOKEN,
    )
    candidate = optimizer.compile_candidate(
        {
            "schema": optimizer.PROPOSAL_SCHEMA,
            "identities": plan_raw["identities"],
            "baselineConfig": plan_raw["baselineConfig"],
            "changes": [
                {"key": change["key"], "value": change["to"]}
                for change in plan_raw["changes"]
            ],
        },
        trust_root=root,
    )
    plan = lab.build_runner_plan(
        candidate,
        profile=plan_raw["profile"],
        attempt=plan_raw["attempt"],
        trust_root=root,
    )
    if plan.to_dict() != plan_raw:
        raise ValueError("plan_binding_invalid")
    adapter = lab_adapter.get_fixed_lab_adapter()
    preflight = adapter.preflight(plan)
    if not isinstance(preflight, lab_adapter.LabPreflight):
        raise ValueError("lab_preflight_invalid")
    if preflight.ready:
        try:
            measured, timing_diagnostics = adapter.run_with_diagnostics(plan)
            if type(measured) is not dict:
                raise ValueError("lab_receipt_invalid")
        finally:
            terminal_cleanup = adapter.cleanup(plan)
        # Cleanup is an independent promotion gate. Preserve completed,
        # content-free aggregates even when the redundant terminal proof is
        # temporarily dirty; the evaluator still fails closed on that proof.
        measured = dict(measured)
        measured["cleanup"] = terminal_cleanup
    else:
        measured = lab_adapter._failure_receipt(
            plan,
            status=preflight.code,
            cleanup=adapter.cleanup(plan),
        )
        timing_diagnostics = {}
    receipt = lab.issue_runner_receipt(
        plan,
        measured,
        trust_root=root,
        runner_capability=runner_capability,
    ).to_dict()
    return receipt, lab_adapter.normalize_private_timing_diagnostics(
        timing_diagnostics
    )


def _runner_receipt(request: dict[str, Any]) -> dict[str, Any]:
    """Compatibility surface: the signed public receipt shape remains unchanged."""

    return _runner_measurement(request)[0]


def _private_runner_result(request: dict[str, Any]) -> dict[str, Any]:
    receipt, timing_diagnostics = _runner_measurement(request)
    return {
        "schema": PRIVATE_RESULT_SCHEMA,
        "receipt": receipt,
        "timingDiagnostics": timing_diagnostics,
    }


def main() -> int:
    sys.addaudithook(_deny_process_creation)
    try:
        request = _read_request()
        response = _private_runner_result(request)
    except Exception as exc:
        response = _diagnostic_response(exc)
        status = 2
    else:
        status = 0
    sys.stdout.write(json.dumps(response, sort_keys=True, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
