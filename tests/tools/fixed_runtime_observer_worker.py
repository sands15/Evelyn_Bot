from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
import sys
from pathlib import Path
from typing import Any


REQUEST_SCHEMA = "evelyn.latency-runtime-observer-request.v1"
SOURCE_SCHEMA = "evelyn.test-runtime-observer-source.v1"
RECEIPT_ID_SCHEMA = "evelyn.latency-runtime-observation-receipt-id.v1"
SCHEMAS = {
    "canary_deployment": "evelyn.latency-canary-deployment-observation.v1",
    "soak_evaluation": "evelyn.latency-soak-evaluation-observation.v1",
    "rollback_cleanup": "evelyn.latency-rollback-cleanup-observation.v1",
}
REQUEST_FIELDS = {
    "schema",
    "kind",
    "authorityId",
    "identityDigest",
    "candidateId",
    "runId",
    "evaluationId",
    "predecessorId",
    "expected",
}
MAX_LINE_BYTES = 65_536


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _response(value: dict[str, Any]) -> None:
    sys.stdout.write(_canonical(value).decode("ascii") + "\n")
    sys.stdout.flush()


def _main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", required=True)
    parser.add_argument("--authority", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--worker-identity", required=True)
    parser.add_argument("--argv-identity", required=True)
    parser.add_argument("--source-identity", required=True)
    args = parser.parse_args()
    source = Path(args.source)
    signing_key = secrets.token_bytes(32)

    for raw_line in sys.stdin.buffer:
        if len(raw_line) > MAX_LINE_BYTES:
            _response({"ok": False, "code": "request_invalid"})
            continue
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict) or set(request) != {"op", "payload"}:
                raise ValueError
            operation = request["op"]
            payload = request["payload"]
            if operation == "observe":
                if (
                    not isinstance(payload, dict)
                    or set(payload) != REQUEST_FIELDS
                    or payload["schema"] != REQUEST_SCHEMA
                    or payload["authorityId"] != args.authority
                    or payload["identityDigest"] != args.identity
                    or payload["kind"] not in SCHEMAS
                    or not isinstance(payload["expected"], dict)
                ):
                    raise ValueError
                source_value = json.loads(source.read_text(encoding="utf-8"))
                if (
                    not isinstance(source_value, dict)
                    or set(source_value) != {"schema", "kind", "facts"}
                    or source_value["schema"] != SOURCE_SCHEMA
                    or source_value["kind"] != payload["kind"]
                    or not isinstance(source_value["facts"], dict)
                ):
                    raise ValueError
                unsigned = {
                    "schema": SCHEMAS[payload["kind"]],
                    "kind": payload["kind"],
                    "authorityId": payload["authorityId"],
                    "identityDigest": payload["identityDigest"],
                    "candidateId": payload["candidateId"],
                    "runId": payload["runId"],
                    "evaluationId": payload["evaluationId"],
                    "predecessorId": payload["predecessorId"],
                    "observerWorkerIdentity": args.worker_identity,
                    "observerArgvIdentity": args.argv_identity,
                    "observerSourceIdentity": args.source_identity,
                    **source_value["facts"],
                }
                receipt_id = "sha256:" + hashlib.sha256(
                    _canonical({"schema": RECEIPT_ID_SCHEMA, "observation": unsigned})
                ).hexdigest()
                signature_payload = {
                    "receiptId": receipt_id,
                    "observation": unsigned,
                }
                signature = "hmac-sha256:" + hmac.new(
                    signing_key,
                    _canonical(
                        {
                            "purpose": unsigned["schema"],
                            "payload": signature_payload,
                        }
                    ),
                    hashlib.sha256,
                ).hexdigest()
                _response(
                    {
                        "ok": True,
                        "receipt": {
                            **unsigned,
                            "receiptId": receipt_id,
                            "signature": signature,
                        },
                    }
                )
            elif operation == "verify":
                if not isinstance(payload, dict):
                    raise ValueError
                signature = payload.get("signature")
                receipt_id = payload.get("receiptId")
                unsigned = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"receiptId", "signature"}
                }
                valid_id = "sha256:" + hashlib.sha256(
                    _canonical({"schema": RECEIPT_ID_SCHEMA, "observation": unsigned})
                ).hexdigest()
                expected = "hmac-sha256:" + hmac.new(
                    signing_key,
                    _canonical(
                        {
                            "purpose": unsigned.get("schema"),
                            "payload": {
                                "receiptId": receipt_id,
                                "observation": unsigned,
                            },
                        }
                    ),
                    hashlib.sha256,
                ).hexdigest()
                _response(
                    {
                        "ok": True,
                        "valid": receipt_id == valid_id
                        and isinstance(signature, str)
                        and hmac.compare_digest(signature, expected),
                    }
                )
            else:
                raise ValueError
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            _response({"ok": False, "code": "request_invalid"})
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
