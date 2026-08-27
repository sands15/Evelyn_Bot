from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
for import_root in (REPO_ROOT, RUNTIME_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from evelyn_core.runtime_artifact_io import atomic_json_write  # noqa: E402
from tools import discord_corpus_model_diagnostic as diagnostic  # noqa: E402


RECEIPT_SCHEMA = "evelyn.discord-corpus-user-acceptance.v2"
DECISION = "accepted_for_corpus_selection"
SCOPE = "domain-discord-pcm-10-only"
EVIDENCE_PAIRING = "explicit_user_pairing"
MAX_REPORT_BYTES = 128 * 1024
MAX_RECEIPT_BYTES = 32 * 1024

_REPORT_KEYS = {"schema", "status", "failureCode", "counts", "gates", "health"}
_COUNT_KEYS = {
    "expectedWavCount",
    "validWavCount",
    "batchAttemptCount",
    "responseCount",
    "nonemptyCount",
    "matchedCount",
    "normalizedExactCount",
    "criticalEntityExactCount",
    "sameIndexStrictUniqueBestCount",
    "errorCount",
}
_GATE_KEYS = {
    "canonicalExact10",
    "batchExactlyOnce10",
    "preHealthExact",
    "postHealthExact",
    "nonempty10",
    "matched10",
    "normalizedExact10",
    "criticalEntityExact10",
    "sameIndexStrictUniqueBest10",
    "errorsZero",
}
_OPERATIONAL_GATES = {
    "canonicalExact10",
    "batchExactlyOnce10",
    "preHealthExact",
    "postHealthExact",
    "nonempty10",
    "errorsZero",
}
_CONTENT_GATE_COUNTS = {
    "matched10": "matchedCount",
    "normalizedExact10": "normalizedExactCount",
    "criticalEntityExact10": "criticalEntityExactCount",
    "sameIndexStrictUniqueBest10": "sameIndexStrictUniqueBestCount",
}


class AcceptanceFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise AcceptanceFailure("invalid_arguments")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _load_json(path: Path, *, max_bytes: int, error: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = diagnostic._read_stable_regular(Path(path).absolute(), max_bytes=max_bytes)
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except Exception as exc:
        raise AcceptanceFailure(error) from exc
    if not isinstance(payload, dict):
        raise AcceptanceFailure(error)
    return payload, raw


def _is_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_failed_diagnostic(payload: Mapping[str, Any]) -> str | None:
    counts = payload.get("counts")
    gates = payload.get("gates")
    health = payload.get("health")
    schema = payload.get("schema")
    if schema == diagnostic.LEGACY_REPORT_SCHEMA:
        expected_keys = _REPORT_KEYS
        marker_sha256 = None
    elif schema == diagnostic.REPORT_SCHEMA:
        expected_keys = _REPORT_KEYS | {"captureMarkerSha256"}
        marker_sha256 = payload.get("captureMarkerSha256")
        if not _is_sha256(marker_sha256):
            raise AcceptanceFailure("diagnostic_invalid")
    else:
        raise AcceptanceFailure("diagnostic_invalid")
    if (
        set(payload) != expected_keys
        or payload.get("status") != "fail"
        or payload.get("failureCode") != "model_diagnostic_failed"
        or not isinstance(counts, Mapping)
        or set(counts) != _COUNT_KEYS
        or not isinstance(gates, Mapping)
        or set(gates) != _GATE_KEYS
        or not isinstance(health, Mapping)
        or set(health) != {"pre", "post"}
        or health.get("pre") is not True
        or health.get("post") is not True
    ):
        raise AcceptanceFailure("diagnostic_invalid")
    if any(type(counts[key]) is not int for key in _COUNT_KEYS):
        raise AcceptanceFailure("diagnostic_invalid")
    if any(type(gates[key]) is not bool for key in _GATE_KEYS):
        raise AcceptanceFailure("diagnostic_invalid")
    if (
        counts["expectedWavCount"] != 10
        or counts["validWavCount"] != 10
        or counts["batchAttemptCount"] != 10
        or counts["responseCount"] != 10
        or counts["nonemptyCount"] != 10
        or counts["errorCount"] != 0
        or any(not gates[key] for key in _OPERATIONAL_GATES)
    ):
        raise AcceptanceFailure("diagnostic_invalid")
    for gate_key, count_key in _CONTENT_GATE_COUNTS.items():
        count = counts[count_key]
        if not 0 <= count <= 10 or gates[gate_key] != (count == 10):
            raise AcceptanceFailure("diagnostic_invalid")
    if all(gates[key] for key in _CONTENT_GATE_COUNTS):
        raise AcceptanceFailure("diagnostic_invalid")
    return marker_sha256


def _bound_hashes(
    diagnostic_report: Path,
    capture_dir: Path,
) -> tuple[str, str, str, bool]:
    report, report_raw = _load_json(
        diagnostic_report,
        max_bytes=MAX_REPORT_BYTES,
        error="diagnostic_invalid",
    )
    report_marker_sha256 = _validate_failed_diagnostic(report)
    root = Path(capture_dir).absolute()
    try:
        loaded, marker_sha256 = diagnostic.load_canonical_corpus_bound(root)
        if len(loaded) != 10:
            raise diagnostic.DiagnosticFailure("corpus_invalid")
        del loaded
    except Exception as exc:
        raise AcceptanceFailure("capture_invalid") from exc
    same_run_binding = report_marker_sha256 is not None
    if same_run_binding and not secrets.compare_digest(
        report_marker_sha256,
        marker_sha256,
    ):
        raise AcceptanceFailure("diagnostic_capture_mismatch")
    return (
        hashlib.sha256(report_raw).hexdigest(),
        marker_sha256,
        str(report["schema"]),
        same_run_binding,
    )


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _content_digest(payload_without_digest: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload_without_digest)).hexdigest()


def _accepted_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _exists_exact(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise AcceptanceFailure("output_invalid") from exc
    return True


def create_acceptance(
    *,
    diagnostic_report: Path,
    capture_dir: Path,
    output: Path,
    accepted_at: str | None = None,
) -> dict[str, Any]:
    output = Path(output).absolute().resolve(strict=False)
    if _exists_exact(output):
        raise AcceptanceFailure("output_exists")
    try:
        output.relative_to(Path(capture_dir).absolute().resolve(strict=True))
    except ValueError:
        pass
    except OSError as exc:
        raise AcceptanceFailure("capture_invalid") from exc
    else:
        raise AcceptanceFailure("output_invalid")

    (
        diagnostic_sha256,
        marker_sha256,
        diagnostic_schema,
        same_run_binding,
    ) = _bound_hashes(diagnostic_report, capture_dir)
    timestamp = accepted_at or _accepted_at()
    if not _valid_timestamp(timestamp):
        raise AcceptanceFailure("invalid_arguments")
    body: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "decision": DECISION,
        "scope": SCOPE,
        "evidencePairing": EVIDENCE_PAIRING,
        "sameRunCryptographicBinding": same_run_binding,
        "acceptedAt": timestamp,
        "automatedDiagnostic": {
            "schema": diagnostic_schema,
            "status": "fail",
            "failureCode": "model_diagnostic_failed",
            "sha256": diagnostic_sha256,
        },
        "capture": {"itemCount": 10, "markerSha256": marker_sha256},
        "productionPromotionAuthorized": False,
    }
    receipt = {**body, "contentDigestSha256": _content_digest(body)}
    try:
        atomic_json_write(output, receipt, attempts=1, durable=True)
    except Exception as exc:
        raise AcceptanceFailure("output_invalid") from exc
    return verify_acceptance(
        diagnostic_report=diagnostic_report,
        capture_dir=capture_dir,
        receipt_path=output,
    )


def verify_acceptance(
    *,
    diagnostic_report: Path,
    capture_dir: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    receipt, _raw = _load_json(
        receipt_path,
        max_bytes=MAX_RECEIPT_BYTES,
        error="receipt_invalid",
    )
    expected_keys = {
        "schema",
        "decision",
        "scope",
        "evidencePairing",
        "sameRunCryptographicBinding",
        "acceptedAt",
        "automatedDiagnostic",
        "capture",
        "productionPromotionAuthorized",
        "contentDigestSha256",
    }
    automated = receipt.get("automatedDiagnostic")
    capture = receipt.get("capture")
    if (
        set(receipt) != expected_keys
        or receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("decision") != DECISION
        or receipt.get("scope") != SCOPE
        or receipt.get("evidencePairing") != EVIDENCE_PAIRING
        or type(receipt.get("sameRunCryptographicBinding")) is not bool
        or not _valid_timestamp(receipt.get("acceptedAt"))
        or receipt.get("productionPromotionAuthorized") is not False
        or not isinstance(automated, Mapping)
        or set(automated) != {"schema", "status", "failureCode", "sha256"}
        or automated.get("schema")
        not in {diagnostic.LEGACY_REPORT_SCHEMA, diagnostic.REPORT_SCHEMA}
        or automated.get("status") != "fail"
        or automated.get("failureCode") != "model_diagnostic_failed"
        or not isinstance(capture, Mapping)
        or set(capture) != {"itemCount", "markerSha256"}
        or capture.get("itemCount") != 10
    ):
        raise AcceptanceFailure("receipt_invalid")
    body = {
        key: value for key, value in receipt.items() if key != "contentDigestSha256"
    }
    content_digest = receipt.get("contentDigestSha256")
    if not isinstance(content_digest, str) or not secrets.compare_digest(
        content_digest,
        _content_digest(body),
    ):
        raise AcceptanceFailure("receipt_invalid")
    (
        diagnostic_sha256,
        marker_sha256,
        diagnostic_schema,
        same_run_binding,
    ) = _bound_hashes(diagnostic_report, capture_dir)
    if (
        not isinstance(automated.get("sha256"), str)
        or not isinstance(capture.get("markerSha256"), str)
        or not secrets.compare_digest(automated["sha256"], diagnostic_sha256)
        or not secrets.compare_digest(capture["markerSha256"], marker_sha256)
        or automated.get("schema") != diagnostic_schema
        or receipt.get("sameRunCryptographicBinding") is not same_run_binding
    ):
        raise AcceptanceFailure("receipt_stale")
    return dict(receipt)


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        description="Create or verify one dual-artifact-hash Discord corpus user acceptance."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "verify"):
        action = subparsers.add_parser(command)
        action.add_argument("--diagnostic-report", required=True, type=Path)
        action.add_argument("--capture-dir", required=True, type=Path)
        target = "--output" if command == "create" else "--receipt"
        action.add_argument(target, required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.command == "create":
            receipt = create_acceptance(
                diagnostic_report=args.diagnostic_report,
                capture_dir=args.capture_dir,
                output=args.output,
            )
            action = "created"
        else:
            receipt = verify_acceptance(
                diagnostic_report=args.diagnostic_report,
                capture_dir=args.capture_dir,
                receipt_path=args.receipt,
            )
            action = "verified"
    except AcceptanceFailure as exc:
        print(f"acceptance_failed code={exc.code}", file=sys.stderr)
        return 1
    except Exception:
        print("acceptance_failed code=internal_failure", file=sys.stderr)
        return 1
    binding = str(receipt["sameRunCryptographicBinding"]).lower()
    print(
        f"acceptance_{action} decision={DECISION} scope={SCOPE} "
        f"sameRunCryptographicBinding={binding}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
