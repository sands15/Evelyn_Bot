from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import sys
import threading
from contextlib import closing
from dataclasses import dataclass, field, replace
from enum import Enum
from itertools import product
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Sequence


PROPOSAL_SCHEMA = "evelyn.latency-candidate-proposal.v1"
CANDIDATE_SCHEMA = "evelyn.latency-candidate.v1"
CANDIDATE_ID_SCHEMA = "evelyn.latency-candidate-id.v1"
FEEDBACK_SCHEMA = "evelyn.latency-feedback.v1"
MAX_CANDIDATES = 12
MAX_INPUT_BYTES = 65_536

CONFIG_DOMAINS: Mapping[str, tuple[int, ...]] = MappingProxyType(
    {
        "main.batch": (1024, 2048, 4096),
        "main.ubatch": (512, 1024, 2048),
        "main.cacheReuse": (64, 128, 256, 512),
        "main.cacheRamMiB": (4096, 8192, 12288),
        "main.cudaGraph": (0, 1),
        "main.swaFull": (0, 1),
    }
)
CONFIG_KEYS = tuple(CONFIG_DOMAINS)
FALLBACK_SWEEP: tuple[tuple[str, int], ...] = (
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
    # Keep the default values in the policy so non-default baselines still
    # enumerate every allowlisted one-knob alternative.
    ("main.batch", 2048),
    ("main.ubatch", 1024),
    ("main.cacheReuse", 256),
    ("main.cacheRamMiB", 8192),
    ("main.cudaGraph", 1),
    ("main.swaFull", 0),
)
IDENTITY_KEYS = ("baseline", "source", "model", "gpu", "corpus", "harness")
CANDIDATE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
AUTH_TAG_PATTERN = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z", re.ASCII)
PROMOTION_EVIDENCE_SCHEMA = "evelyn.latency-promotion-evidence.v1"
MAIN_LATENCY_EVALUATOR_ID = "main-latency-fixed-evaluator-v2"
COORDINATOR_AUTHORITY_SCHEMA = "evelyn.latency-fixed-coordinator.v1"
IDENTITY_PIN_SCHEMA = "evelyn.latency-identity-pin.v1"
APPROVAL_RECEIPT_SCHEMA = "evelyn.latency-approval-receipt.v1"
CANARY_RECEIPT_SCHEMA = "evelyn.latency-canary-receipt.v1"
ACCEPTANCE_RECEIPT_SCHEMA = "evelyn.latency-acceptance-receipt.v1"
ROLLBACK_RECEIPT_SCHEMA = "evelyn.latency-rollback-receipt.v1"
LIFECYCLE_RECEIPT_ID_SCHEMA = "evelyn.latency-lifecycle-receipt-id.v1"
LIFECYCLE_EVIDENCE_ID_SCHEMA = "evelyn.latency-lifecycle-evidence-id.v1"
CANARY_DEPLOYMENT_EVIDENCE_SCHEMA = "evelyn.latency-canary-deployment-evidence.v1"
SOAK_EVALUATION_EVIDENCE_SCHEMA = "evelyn.latency-soak-evaluation-evidence.v1"
ROLLBACK_CLEANUP_EVIDENCE_SCHEMA = "evelyn.latency-rollback-cleanup-evidence.v1"
RUNTIME_OBSERVER_REQUEST_SCHEMA = "evelyn.latency-runtime-observer-request.v1"
RUNTIME_OBSERVATION_RECEIPT_ID_SCHEMA = (
    "evelyn.latency-runtime-observation-receipt-id.v1"
)
CANARY_DEPLOYMENT_OBSERVATION_SCHEMA = (
    "evelyn.latency-canary-deployment-observation.v1"
)
SOAK_EVALUATION_OBSERVATION_SCHEMA = "evelyn.latency-soak-evaluation-observation.v1"
ROLLBACK_CLEANUP_OBSERVATION_SCHEMA = "evelyn.latency-rollback-cleanup-observation.v1"

FEEDBACK_VERDICTS = frozenset({"rejected", "inconclusive", "frontier", "eligible"})
FEEDBACK_CODES = (
    "candidate_passed",
    "frontier_improved",
    "invalid_candidate",
    "latency_regressed",
    "quality_regressed",
    "safety_failed",
    "resource_failed",
    "environment_drift",
    "insufficient_samples",
    "evaluator_failed",
    "harness_change_requested",
)
FEEDBACK_CODES_BY_VERDICT: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "rejected": frozenset(
            {
                "invalid_candidate",
                "latency_regressed",
                "quality_regressed",
                "safety_failed",
                "resource_failed",
            }
        ),
        "inconclusive": frozenset(
            {
                "environment_drift",
                "insufficient_samples",
                "evaluator_failed",
                "harness_change_requested",
            }
        ),
        "frontier": frozenset({"frontier_improved"}),
        "eligible": frozenset({"candidate_passed"}),
    }
)
FEEDBACK_METRICS = (
    "firstSentenceP50DeltaMs",
    "firstSentenceP95DeltaMs",
    "postSttFirstPcmP50DeltaMs",
    "postSttFirstPcmP95DeltaMs",
    "postSttFirstPcmP99DeltaMs",
    "restartReadyFirstPcmP95DeltaMs",
    "restartStartupToReadyP95DeltaMs",
    "promptEvalP95DeltaMs",
    "promptCacheHitRatioDelta",
    "gpuMinFreeDeltaMiB",
)


class ContractError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require_exact_fields(value: Any, fields: Sequence[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ContractError(code)
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


@dataclass(frozen=True)
class IdentitySet:
    baseline: str
    source: str
    model: str
    gpu: str
    corpus: str
    harness: str

    @classmethod
    def from_mapping(cls, raw: Any) -> "IdentitySet":
        values = _require_exact_fields(raw, IDENTITY_KEYS, "identities_invalid")
        parsed: dict[str, str] = {}
        for key in IDENTITY_KEYS:
            value = values[key]
            if not isinstance(value, str) or CANDIDATE_ID_PATTERN.fullmatch(value) is None:
                raise ContractError("identity_invalid")
            parsed[key] = value
        return cls(**parsed)

    def to_dict(self) -> dict[str, str]:
        return {key: getattr(self, key) for key in IDENTITY_KEYS}


_COORDINATOR_BOOTSTRAP_TOKEN = object()


def _runner_signature_run_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for field in ("cleanup", "receipt", "attestation"):
        nested = payload.get(field)
        if isinstance(nested, dict):
            run_id = nested.get("runId")
            if isinstance(run_id, str) and CANDIDATE_ID_PATTERN.fullmatch(run_id):
                return run_id
    return None


class _SigningCapability:
    __slots__ = ("__key", "authority_id", "identity_digest")

    def __init__(
        self,
        key: bytes,
        authority_id: str,
        identity_digest: str,
        token: object,
    ) -> None:
        if (
            token is not _COORDINATOR_BOOTSTRAP_TOKEN
            or not isinstance(key, bytes)
            or len(key) != 32
            or CANDIDATE_ID_PATTERN.fullmatch(authority_id) is None
            or CANDIDATE_ID_PATTERN.fullmatch(identity_digest) is None
        ):
            raise ContractError("coordinator_capability_invalid")
        self.__key = key
        self.authority_id = authority_id
        self.identity_digest = identity_digest

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    def __reduce_ex__(self, _protocol: int) -> Any:
        raise TypeError("coordinator_capability_not_serializable")

    def __copy__(self) -> Any:
        raise TypeError("coordinator_capability_not_copyable")

    def __deepcopy__(self, _memo: Any) -> Any:
        raise TypeError("coordinator_capability_not_copyable")

    def _sign_for(self, purpose: str, payload: Any) -> str:
        message = _canonical_bytes({"purpose": purpose, "payload": payload})
        return f"hmac-sha256:{hmac.new(self.__key, message, hashlib.sha256).hexdigest()}"

    def _derive_key(self, purpose: str, payload: Any) -> bytes:
        message = _canonical_bytes({"purpose": purpose, "payload": payload})
        return hmac.new(self.__key, message, hashlib.sha256).digest()

    def _key_for_transfer(self, token: object) -> bytes:
        if token is not _COORDINATOR_BOOTSTRAP_TOKEN:
            raise ContractError("runner_capability_invalid")
        return self.__key


class RunnerRunCapability(_SigningCapability):
    __slots__ = ("run_id", "__claimed", "__exported", "__lock")

    def __init__(
        self,
        key: bytes,
        authority_id: str,
        identity_digest: str,
        run_id: str,
        token: object,
    ) -> None:
        super().__init__(key, authority_id, identity_digest, token)
        if CANDIDATE_ID_PATTERN.fullmatch(run_id) is None:
            raise ContractError("runner_capability_invalid")
        self.run_id = run_id
        self.__claimed = False
        self.__exported = False
        self.__lock = threading.Lock()

    def _claim_receipt(self, run_id: str) -> None:
        with self.__lock:
            if self.__claimed or self.__exported or run_id != self.run_id:
                raise ContractError("runner_capability_consumed")
            self.__claimed = True

    def _export_once(self) -> dict[str, str]:
        with self.__lock:
            if self.__exported or self.__claimed:
                raise ContractError("runner_capability_consumed")
            self.__exported = True
        transfer = {
            "schema": "evelyn.runner-one-run-capability.v1",
            "authorityId": self.authority_id,
            "identityDigest": self.identity_digest,
            "runId": self.run_id,
        }
        transfer["secret"] = self._key_for_transfer(_COORDINATOR_BOOTSTRAP_TOKEN).hex()
        return transfer


class RunnerCapability(_SigningCapability):
    __slots__ = ("__issued_runs", "__issued_runs_lock")

    def __init__(
        self,
        key: bytes,
        authority_id: str,
        identity_digest: str,
        token: object,
    ) -> None:
        super().__init__(key, authority_id, identity_digest, token)
        self.__issued_runs: set[str] = set()
        self.__issued_runs_lock = threading.Lock()

    def _issue_one_run(self, run_id: str) -> RunnerRunCapability:
        if not isinstance(run_id, str) or CANDIDATE_ID_PATTERN.fullmatch(run_id) is None:
            raise ContractError("runner_capability_invalid")
        with self.__issued_runs_lock:
            if run_id in self.__issued_runs:
                raise ContractError("runner_capability_consumed")
            self.__issued_runs.add(run_id)
        binding = {
            "authorityId": self.authority_id,
            "identityDigest": self.identity_digest,
            "runId": run_id,
        }
        key = self._derive_key("runner-one-run-key-v1", binding)
        return RunnerRunCapability(
            key,
            self.authority_id,
            self.identity_digest,
            run_id,
            _COORDINATOR_BOOTSTRAP_TOKEN,
        )


class EvaluatorCapability(_SigningCapability):
    __slots__ = ()


class LifecycleCapability(_SigningCapability):
    __slots__ = ()


@dataclass(frozen=True)
class _RuntimeObserverBinding:
    worker_identity: str
    argv_identity: str
    source_identity: str


class CoordinatorTrustRoot:
    __slots__ = (
        "authority_id",
        "identity_digest",
        "pinned_identities",
        "__runner_key",
        "__evaluator_key",
        "__lifecycle_key",
        "__journal_path",
        "__observer_adapter",
        "__observer_binding",
        "__closed",
        "__consumed_receipts",
        "__consumed_lock",
    )

    def __init__(
        self,
        authority_id: str,
        identity_digest: str,
        pinned_identities: IdentitySet,
        runner_key: bytes,
        evaluator_key: bytes,
        lifecycle_key: bytes,
        journal_path: str | None,
        token: object,
    ) -> None:
        if (
            token is not _COORDINATOR_BOOTSTRAP_TOKEN
            or CANDIDATE_ID_PATTERN.fullmatch(authority_id) is None
            or CANDIDATE_ID_PATTERN.fullmatch(identity_digest) is None
            or not isinstance(pinned_identities, IdentitySet)
            or any(not isinstance(key, bytes) or len(key) != 32 for key in (
                runner_key,
                evaluator_key,
                lifecycle_key,
            ))
        ):
            raise ContractError("coordinator_trust_root_invalid")
        self.authority_id = authority_id
        self.identity_digest = identity_digest
        self.pinned_identities = pinned_identities
        self.__runner_key = runner_key
        self.__evaluator_key = evaluator_key
        self.__lifecycle_key = lifecycle_key
        self.__journal_path = journal_path
        self.__observer_adapter: Any = None
        self.__observer_binding: _RuntimeObserverBinding | None = None
        self.__closed = False
        self.__consumed_receipts: set[str] = set()
        self.__consumed_lock = threading.Lock()

    def __repr__(self) -> str:
        return "CoordinatorTrustRoot(<pinned>)"

    def __reduce_ex__(self, _protocol: int) -> Any:
        raise TypeError("coordinator_trust_root_not_serializable")

    def __copy__(self) -> Any:
        raise TypeError("coordinator_trust_root_not_copyable")

    def __deepcopy__(self, _memo: Any) -> Any:
        raise TypeError("coordinator_trust_root_not_copyable")

    def close(self) -> None:
        """Fail-stop this authority handle; a journalled authority may be reopened."""

        with self.__consumed_lock:
            self.__closed = True

    def _is_open(self) -> bool:
        return not self.__closed

    def _bind_runtime_observer(self, adapter: Any, token: object) -> None:
        if token is not _COORDINATOR_BOOTSTRAP_TOKEN or self.__observer_adapter is not None:
            raise ContractError("runtime_observer_invalid")
        identities = tuple(
            getattr(adapter, field, None)
            for field in ("worker_identity", "argv_identity", "source_identity")
        )
        if (
            any(
                not isinstance(value, str)
                or CANDIDATE_ID_PATTERN.fullmatch(value) is None
                for value in identities
            )
            or not callable(getattr(adapter, "bind", None))
            or not callable(getattr(adapter, "observe", None))
            or not callable(getattr(adapter, "verify", None))
        ):
            raise ContractError("runtime_observer_invalid")
        try:
            bound = adapter.bind(self.authority_id, self.identity_digest)
        except Exception:
            raise ContractError("runtime_observer_invalid") from None
        if bound is not True:
            raise ContractError("runtime_observer_invalid")
        self.__observer_adapter = adapter
        self.__observer_binding = _RuntimeObserverBinding(*identities)

    @staticmethod
    def _verify_with(key: bytes, purpose: str, payload: Any, signature: str) -> bool:
        if not isinstance(signature, str) or AUTH_TAG_PATTERN.fullmatch(signature) is None:
            return False
        message = _canonical_bytes({"purpose": purpose, "payload": payload})
        expected = f"hmac-sha256:{hmac.new(key, message, hashlib.sha256).hexdigest()}"
        return hmac.compare_digest(expected, signature)

    def _verify_runner(self, purpose: str, payload: Any, signature: str) -> bool:
        if self._verify_with(self.__runner_key, purpose, payload, signature):
            return True
        run_id = _runner_signature_run_id(payload)
        if run_id is None:
            return False
        binding = {
            "authorityId": self.authority_id,
            "identityDigest": self.identity_digest,
            "runId": run_id,
        }
        derived_key = hmac.new(
            self.__runner_key,
            _canonical_bytes({"purpose": "runner-one-run-key-v1", "payload": binding}),
            hashlib.sha256,
        ).digest()
        return self._verify_with(derived_key, purpose, payload, signature)

    def _verify_evaluator(self, purpose: str, payload: Any, signature: str) -> bool:
        return self._verify_with(self.__evaluator_key, purpose, payload, signature)

    def _verify_lifecycle(self, purpose: str, payload: Any, signature: str) -> bool:
        return self._verify_with(self.__lifecycle_key, purpose, payload, signature)

    def _request_runtime_observation(self, request: Mapping[str, Any]) -> Any:
        if self.__closed or self.__observer_adapter is None:
            raise ContractError("runtime_observer_unavailable")
        try:
            receipt = self.__observer_adapter.observe(dict(request))
        except Exception:
            raise ContractError("runtime_observer_unavailable") from None
        if not isinstance(receipt, dict):
            raise ContractError("runtime_observer_receipt_invalid")
        return receipt

    def _verify_runtime_observer(self, receipt: Any) -> bool:
        binding = self.__observer_binding
        if self.__closed or self.__observer_adapter is None or binding is None:
            return False
        if (
            getattr(receipt, "observer_worker_identity", None) != binding.worker_identity
            or getattr(receipt, "observer_argv_identity", None) != binding.argv_identity
            or getattr(receipt, "observer_source_identity", None) != binding.source_identity
        ):
            return False
        try:
            return self.__observer_adapter.verify(receipt.to_dict()) is True
        except Exception:
            return False

    def _consume_once(self, purpose: str, receipt_id: str) -> bool:
        if purpose not in {"runner", "lifecycle", "observer"}:
            return False
        if self.__closed:
            return False
        if self.__journal_path is not None and purpose == "observer":
            try:
                with closing(_open_lifecycle_journal(self.__journal_path)) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "INSERT INTO consumed_observations(receipt_id) VALUES (?)",
                        (receipt_id,),
                    )
                    connection.commit()
                return True
            except sqlite3.DatabaseError:
                return False
        consumed_id = f"{purpose}:{receipt_id}"
        with self.__consumed_lock:
            if consumed_id in self.__consumed_receipts:
                return False
            self.__consumed_receipts.add(consumed_id)
            return True

    def _observation_consumed(self, receipt_id: str) -> bool:
        if (
            self.__closed
            or not isinstance(receipt_id, str)
            or CANDIDATE_ID_PATTERN.fullmatch(receipt_id) is None
        ):
            return False
        if self.__journal_path is not None:
            try:
                with closing(_open_lifecycle_journal(self.__journal_path)) as connection:
                    return connection.execute(
                        "SELECT 1 FROM consumed_observations WHERE receipt_id = ?",
                        (receipt_id,),
                    ).fetchone() == (1,)
            except sqlite3.DatabaseError:
                return False
        with self.__consumed_lock:
            return f"observer:{receipt_id}" in self.__consumed_receipts

    def _consume_lifecycle_transition(
        self,
        receipt_id: str,
        predecessor_id: str,
        *,
        candidate_id: str | None = None,
        run_id: str | None = None,
        evaluation_id: str | None = None,
        source_state: str | None = None,
        target_state: str | None = None,
    ) -> bool:
        if any(
            not isinstance(value, str)
            or CANDIDATE_ID_PATTERN.fullmatch(value) is None
            for value in (receipt_id, predecessor_id)
        ):
            return False
        if self.__closed:
            return False
        if self.__journal_path is not None:
            if any(
                not isinstance(value, str)
                or CANDIDATE_ID_PATTERN.fullmatch(value) is None
                for value in (candidate_id, run_id, evaluation_id)
            ) or not isinstance(source_state, str) or not isinstance(target_state, str):
                return False
            try:
                with closing(_open_lifecycle_journal(self.__journal_path)) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute(
                        """
                        SELECT state, head_id
                        FROM lifecycle_campaigns
                        WHERE authority_id = ? AND candidate_id = ?
                          AND run_id = ? AND evaluation_id = ?
                        """,
                        (self.authority_id, candidate_id, run_id, evaluation_id),
                    ).fetchone()
                    if row != (source_state, predecessor_id):
                        connection.rollback()
                        return False
                    connection.execute(
                        """
                        INSERT INTO consumed_lifecycle_receipts(
                            receipt_id, predecessor_id, authority_id,
                            candidate_id, run_id, evaluation_id
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            receipt_id,
                            predecessor_id,
                            self.authority_id,
                            candidate_id,
                            run_id,
                            evaluation_id,
                        ),
                    )
                    changed = connection.execute(
                        """
                        UPDATE lifecycle_campaigns
                        SET state = ?, head_id = ?
                        WHERE authority_id = ? AND candidate_id = ?
                          AND run_id = ? AND evaluation_id = ?
                          AND state = ? AND head_id = ?
                        """,
                        (
                            target_state,
                            receipt_id,
                            self.authority_id,
                            candidate_id,
                            run_id,
                            evaluation_id,
                            source_state,
                            predecessor_id,
                        ),
                    ).rowcount
                    if changed != 1:
                        connection.rollback()
                        return False
                    connection.commit()
                return True
            except sqlite3.DatabaseError:
                return False
        receipt_key = f"lifecycle:{receipt_id}"
        branch_key = f"lifecycle-predecessor:{predecessor_id}"
        with self.__consumed_lock:
            if (
                receipt_key in self.__consumed_receipts
                or branch_key in self.__consumed_receipts
            ):
                return False
            self.__consumed_receipts.update((receipt_key, branch_key))
            return True

    def _start_lifecycle_campaign(
        self,
        *,
        candidate_id: str,
        run_id: str,
        evaluation_id: str,
    ) -> bool:
        if self.__closed:
            return False
        if self.__journal_path is None:
            return True
        try:
            with closing(_open_lifecycle_journal(self.__journal_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO lifecycle_campaigns(
                        authority_id, candidate_id, run_id, evaluation_id,
                        state, head_id
                    ) VALUES (?, ?, ?, ?, 'awaiting_approval', ?)
                    """,
                    (
                        self.authority_id,
                        candidate_id,
                        run_id,
                        evaluation_id,
                        evaluation_id,
                    ),
                )
                connection.commit()
            return True
        except sqlite3.DatabaseError:
            return False


def _identity_digest(identities: IdentitySet) -> str:
    payload = {"schema": IDENTITY_PIN_SCHEMA, "identities": identities.to_dict()}
    return f"sha256:{hashlib.sha256(_canonical_bytes(payload)).hexdigest()}"


def _open_lifecycle_journal(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _journal_authority(
    journal_path: str | Path,
    identities: IdentitySet,
) -> tuple[str, bytes, bytes, bytes, str]:
    path = Path(journal_path).resolve()
    if not path.parent.is_dir() or path.is_dir():
        raise ContractError("coordinator_journal_invalid")
    identity_digest = _identity_digest(identities)
    try:
        with closing(_open_lifecycle_journal(str(path))) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS coordinator_authority (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    authority_id TEXT NOT NULL,
                    identity_digest TEXT NOT NULL,
                    identities_json TEXT NOT NULL,
                    runner_key BLOB NOT NULL CHECK (length(runner_key) = 32),
                    evaluator_key BLOB NOT NULL CHECK (length(evaluator_key) = 32),
                    lifecycle_key BLOB NOT NULL CHECK (length(lifecycle_key) = 32)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_campaigns (
                    authority_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    evaluation_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('awaiting_approval', 'staged', 'canary',
                                  'accepted', 'rolled_back')
                    ),
                    head_id TEXT NOT NULL,
                    PRIMARY KEY (authority_id, candidate_id, run_id, evaluation_id)
                );
                CREATE TABLE IF NOT EXISTS consumed_lifecycle_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    predecessor_id TEXT NOT NULL UNIQUE,
                    authority_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    evaluation_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consumed_observations (
                    receipt_id TEXT PRIMARY KEY
                );
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT authority_id, identity_digest, identities_json,
                       runner_key, evaluator_key, lifecycle_key
                FROM coordinator_authority WHERE singleton = 1
                """
            ).fetchone()
            identities_json = _canonical_bytes(identities.to_dict()).decode("ascii")
            if row is None:
                authority_payload = {
                    "schema": COORDINATOR_AUTHORITY_SCHEMA,
                    "identityDigest": identity_digest,
                    "nonce": secrets.token_hex(32),
                }
                authority_id = (
                    f"sha256:{hashlib.sha256(_canonical_bytes(authority_payload)).hexdigest()}"
                )
                runner_key, evaluator_key, lifecycle_key = (
                    secrets.token_bytes(32) for _ in range(3)
                )
                connection.execute(
                    """
                    INSERT INTO coordinator_authority(
                        singleton, authority_id, identity_digest, identities_json,
                        runner_key, evaluator_key, lifecycle_key
                    ) VALUES (1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        authority_id,
                        identity_digest,
                        identities_json,
                        runner_key,
                        evaluator_key,
                        lifecycle_key,
                    ),
                )
            else:
                (
                    authority_id,
                    stored_digest,
                    stored_identities,
                    runner_key,
                    evaluator_key,
                    lifecycle_key,
                ) = row
                if stored_digest != identity_digest or stored_identities != identities_json:
                    connection.rollback()
                    raise ContractError("coordinator_journal_identity_mismatch")
                if (
                    CANDIDATE_ID_PATTERN.fullmatch(authority_id) is None
                    or any(
                        not isinstance(key, bytes) or len(key) != 32
                        for key in (runner_key, evaluator_key, lifecycle_key)
                    )
                ):
                    connection.rollback()
                    raise ContractError("coordinator_journal_invalid")
            connection.commit()
    except ContractError:
        raise
    except sqlite3.DatabaseError:
        raise ContractError("coordinator_journal_invalid") from None
    return authority_id, runner_key, evaluator_key, lifecycle_key, str(path)


def _validated_trust_root(value: Any) -> CoordinatorTrustRoot:
    if (
        not isinstance(value, CoordinatorTrustRoot)
        or not value._is_open()
        or CANDIDATE_ID_PATTERN.fullmatch(value.authority_id) is None
        or CANDIDATE_ID_PATTERN.fullmatch(value.identity_digest) is None
        or not isinstance(value.pinned_identities, IdentitySet)
        or _identity_digest(value.pinned_identities) != value.identity_digest
    ):
        raise ContractError("coordinator_trust_root_invalid")
    return value


def _capability_matches(
    trust_root: CoordinatorTrustRoot,
    capability: Any,
    expected_type: type[_SigningCapability],
    verifier: str,
) -> bool:
    if (
        not isinstance(capability, expected_type)
        or capability.authority_id != trust_root.authority_id
        or capability.identity_digest != trust_root.identity_digest
    ):
        return False
    payload = {
        "authorityId": trust_root.authority_id,
        "identityDigest": trust_root.identity_digest,
        "capability": expected_type.__name__,
    }
    signature = capability._sign_for(COORDINATOR_AUTHORITY_SCHEMA, payload)
    return getattr(trust_root, verifier)(COORDINATOR_AUTHORITY_SCHEMA, payload, signature)


def bootstrap_ephemeral_fixed_coordinator(
    pinned_identities: IdentitySet,
    *,
    journal_path: str | Path | None = None,
) -> tuple[CoordinatorTrustRoot, RunnerCapability, EvaluatorCapability, LifecycleCapability]:
    """Create one fixed coordinator authority.

    The default is process-local and non-resumable.  An explicit journal path
    preserves the authority and lifecycle CAS state for a human-approved resume.
    Keys are never emitted as loop output; the LLM receives only typed domains and
    aggregate feedback.
    """
    identities = IdentitySet.from_mapping(pinned_identities.to_dict())
    identity_digest = _identity_digest(identities)
    resolved_journal: str | None = None
    if journal_path is None:
        authority_payload = {
            "schema": COORDINATOR_AUTHORITY_SCHEMA,
            "identityDigest": identity_digest,
            "nonce": secrets.token_hex(32),
        }
        authority_id = (
            f"sha256:{hashlib.sha256(_canonical_bytes(authority_payload)).hexdigest()}"
        )
        runner_key, evaluator_key, lifecycle_key = (
            secrets.token_bytes(32) for _ in range(3)
        )
    else:
        (
            authority_id,
            runner_key,
            evaluator_key,
            lifecycle_key,
            resolved_journal,
        ) = _journal_authority(journal_path, identities)
    root = CoordinatorTrustRoot(
        authority_id,
        identity_digest,
        identities,
        runner_key,
        evaluator_key,
        lifecycle_key,
        resolved_journal,
        _COORDINATOR_BOOTSTRAP_TOKEN,
    )
    return (
        root,
        RunnerCapability(runner_key, authority_id, identity_digest, _COORDINATOR_BOOTSTRAP_TOKEN),
        EvaluatorCapability(
            evaluator_key,
            authority_id,
            identity_digest,
            _COORDINATOR_BOOTSTRAP_TOKEN,
        ),
        LifecycleCapability(
            lifecycle_key,
            authority_id,
            identity_digest,
            _COORDINATOR_BOOTSTRAP_TOKEN,
        ),
    )


def _bootstrap_test_coordinator(
    pinned_identities: IdentitySet,
    *,
    journal_path: str | Path | None = None,
    observer_adapter: Any = None,
) -> tuple[CoordinatorTrustRoot, RunnerCapability, EvaluatorCapability, LifecycleCapability]:
    """Compatibility alias used by contract tests and fixed isolated workers."""

    coordinator = bootstrap_ephemeral_fixed_coordinator(
        pinned_identities,
        journal_path=journal_path,
    )
    if observer_adapter is not None:
        coordinator[0]._bind_runtime_observer(
            observer_adapter,
            _COORDINATOR_BOOTSTRAP_TOKEN,
        )
    return coordinator


@dataclass(frozen=True)
class MainLatencyConfig:
    batch: int
    ubatch: int
    cache_reuse: int
    cache_ram_mib: int
    cuda_graph: int
    swa_full: int

    def __post_init__(self) -> None:
        for key, value in self.to_dict().items():
            if type(value) is not int or value not in CONFIG_DOMAINS[key]:
                raise ContractError("config_value_invalid")
        self.validate_compatibility()

    @classmethod
    def from_mapping(cls, raw: Any) -> "MainLatencyConfig":
        values = _require_exact_fields(raw, CONFIG_KEYS, "config_fields_invalid")
        field_names = (
            ("main.batch", "batch"),
            ("main.ubatch", "ubatch"),
            ("main.cacheReuse", "cache_reuse"),
            ("main.cacheRamMiB", "cache_ram_mib"),
            ("main.cudaGraph", "cuda_graph"),
            ("main.swaFull", "swa_full"),
        )
        return cls(**{field_name: values[key] for key, field_name in field_names})

    def validate_compatibility(self) -> None:
        if self.ubatch > self.batch:
            raise ContractError("config_incompatible")

    def to_dict(self) -> dict[str, int]:
        return {
            "main.batch": self.batch,
            "main.ubatch": self.ubatch,
            "main.cacheReuse": self.cache_reuse,
            "main.cacheRamMiB": self.cache_ram_mib,
            "main.cudaGraph": self.cuda_graph,
            "main.swaFull": self.swa_full,
        }


@dataclass(frozen=True)
class CandidateChange:
    key: str
    previous: int
    value: int

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "from": self.previous, "to": self.value}


@dataclass(frozen=True)
class CandidateManifest:
    candidate_id: str
    identities: IdentitySet
    baseline_config: MainLatencyConfig
    candidate_config: MainLatencyConfig
    changes: tuple[CandidateChange, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CANDIDATE_SCHEMA,
            "candidateId": self.candidate_id,
            "identities": self.identities.to_dict(),
            "baselineConfig": self.baseline_config.to_dict(),
            "candidateConfig": self.candidate_config.to_dict(),
            "changes": [change.to_dict() for change in self.changes],
        }


def _candidate_id_for(
    identities: IdentitySet,
    baseline: MainLatencyConfig,
    candidate: MainLatencyConfig,
) -> str:
    payload = {
        "schema": CANDIDATE_ID_SCHEMA,
        "identities": identities.to_dict(),
        "baselineConfig": baseline.to_dict(),
        "candidateConfig": candidate.to_dict(),
    }
    return f"sha256:{hashlib.sha256(_canonical_bytes(payload)).hexdigest()}"


def _parse_changes(raw: Any, baseline: MainLatencyConfig) -> tuple[CandidateChange, ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= len(CONFIG_KEYS):
        raise ContractError("changes_invalid")
    baseline_values = baseline.to_dict()
    seen: set[str] = set()
    changes: list[CandidateChange] = []
    for item in raw:
        values = _require_exact_fields(item, ("key", "value"), "change_fields_invalid")
        key = values["key"]
        value = values["value"]
        if not isinstance(key, str) or key not in CONFIG_DOMAINS:
            raise ContractError("change_key_invalid")
        if key in seen:
            raise ContractError("change_duplicate")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractError("change_value_invalid")
        if value not in CONFIG_DOMAINS[key]:
            raise ContractError("change_value_invalid")
        if value == baseline_values[key]:
            raise ContractError("change_noop")
        seen.add(key)
        changes.append(CandidateChange(key, baseline_values[key], value))
    changes.sort(key=lambda change: CONFIG_KEYS.index(change.key))
    return tuple(changes)


def compile_candidate(
    raw: Any,
    *,
    trust_root: CoordinatorTrustRoot,
) -> CandidateManifest:
    trust_root = _validated_trust_root(trust_root)
    values = _require_exact_fields(
        raw,
        ("schema", "identities", "baselineConfig", "changes"),
        "proposal_fields_invalid",
    )
    if values["schema"] != PROPOSAL_SCHEMA:
        raise ContractError("proposal_schema_invalid")
    identities = IdentitySet.from_mapping(values["identities"])
    if identities != trust_root.pinned_identities:
        raise ContractError("identity_pin_mismatch")
    baseline = MainLatencyConfig.from_mapping(values["baselineConfig"])
    changes = _parse_changes(values["changes"], baseline)
    candidate_values = baseline.to_dict()
    for change in changes:
        candidate_values[change.key] = change.value
    candidate = MainLatencyConfig.from_mapping(candidate_values)
    candidate_id = _candidate_id_for(identities, baseline, candidate)
    return CandidateManifest(candidate_id, identities, baseline, candidate, changes)


def candidate_proposal(
    identities: IdentitySet,
    baseline: MainLatencyConfig,
    changes: Mapping[str, int],
) -> dict[str, Any]:
    if not isinstance(changes, dict) or not changes or not set(changes).issubset(CONFIG_DOMAINS):
        raise ContractError("changes_invalid")
    return {
        "schema": PROPOSAL_SCHEMA,
        "identities": identities.to_dict(),
        "baselineConfig": baseline.to_dict(),
        "changes": [{"key": key, "value": changes[key]} for key in CONFIG_KEYS if key in changes],
    }


def _validate_attempted_ids(raw: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ContractError("attempted_candidates_invalid")
    if len(raw) > MAX_CANDIDATES:
        raise ContractError("attempted_candidates_invalid")
    values = tuple(raw)
    if any(
        not isinstance(value, str) or CANDIDATE_ID_PATTERN.fullmatch(value) is None
        for value in values
    ) or len(set(values)) != len(values):
        raise ContractError("attempted_candidates_invalid")
    return values


def _valid_candidate_ids(
    identities: IdentitySet,
    baseline: MainLatencyConfig,
) -> frozenset[str]:
    baseline_values = baseline.to_dict()
    valid: set[str] = set()
    for values in product(*(CONFIG_DOMAINS[key] for key in CONFIG_KEYS)):
        candidate_values = dict(zip(CONFIG_KEYS, values))
        if candidate_values == baseline_values:
            continue
        try:
            candidate = MainLatencyConfig.from_mapping(candidate_values)
        except ContractError as exc:
            if exc.code == "config_incompatible":
                continue
            raise
        valid.add(_candidate_id_for(identities, baseline, candidate))
    return frozenset(valid)


def enumerate_next_candidates(
    identities: IdentitySet,
    baseline: MainLatencyConfig,
    *,
    trust_root: CoordinatorTrustRoot,
    attempted_candidate_ids: Sequence[str] = (),
) -> tuple[CandidateManifest, ...]:
    trust_root = _validated_trust_root(trust_root)
    if not isinstance(identities, IdentitySet):
        raise ContractError("identities_invalid")
    if not isinstance(baseline, MainLatencyConfig):
        raise ContractError("config_fields_invalid")
    identities = IdentitySet.from_mapping(identities.to_dict())
    if identities != trust_root.pinned_identities:
        raise ContractError("identity_pin_mismatch")
    baseline = MainLatencyConfig.from_mapping(baseline.to_dict())
    attempted_values = _validate_attempted_ids(attempted_candidate_ids)
    attempted = set(attempted_values)
    candidates: list[CandidateManifest] = []
    baseline_values = baseline.to_dict()
    for key, value in FALLBACK_SWEEP:
        if value == baseline_values[key]:
            continue
        try:
            candidate = compile_candidate(
                candidate_proposal(identities, baseline, {key: value}),
                trust_root=trust_root,
            )
        except ContractError as exc:
            if exc.code == "config_incompatible":
                continue
            raise
        candidates.append(candidate)
    valid_ids = _valid_candidate_ids(identities, baseline)
    if not attempted.issubset(valid_ids):
        raise ContractError("attempted_candidate_identity_mismatch")
    remaining = MAX_CANDIDATES - len(attempted_values)
    return tuple(
        candidate for candidate in candidates if candidate.candidate_id not in attempted
    )[:remaining]


def next_candidate(
    identities: IdentitySet,
    baseline: MainLatencyConfig,
    *,
    trust_root: CoordinatorTrustRoot,
    attempted_candidate_ids: Sequence[str] = (),
) -> CandidateManifest | None:
    candidates = enumerate_next_candidates(
        identities,
        baseline,
        trust_root=trust_root,
        attempted_candidate_ids=attempted_candidate_ids,
    )
    return candidates[0] if candidates else None


@dataclass(frozen=True)
class CandidateFeedback:
    candidate_id: str
    attempt: int
    verdict: str
    codes: tuple[str, ...]
    metrics: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": FEEDBACK_SCHEMA,
            "candidateId": self.candidate_id,
            "attempt": self.attempt,
            "verdict": self.verdict,
            "codes": list(self.codes),
            "metrics": dict(self.metrics),
        }


def compile_feedback(raw: Any) -> CandidateFeedback:
    values = _require_exact_fields(
        raw,
        ("schema", "candidateId", "attempt", "verdict", "codes", "metrics"),
        "feedback_fields_invalid",
    )
    if values["schema"] != FEEDBACK_SCHEMA:
        raise ContractError("feedback_schema_invalid")
    candidate_id = values["candidateId"]
    if not isinstance(candidate_id, str) or CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None:
        raise ContractError("feedback_candidate_invalid")
    attempt = values["attempt"]
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= MAX_CANDIDATES:
        raise ContractError("feedback_attempt_invalid")
    verdict = values["verdict"]
    if not isinstance(verdict, str) or verdict not in FEEDBACK_VERDICTS:
        raise ContractError("feedback_verdict_invalid")
    raw_codes = values["codes"]
    if (
        not isinstance(raw_codes, list)
        or not raw_codes
        or any(not isinstance(code, str) or code not in FEEDBACK_CODES for code in raw_codes)
        or len(set(raw_codes)) != len(raw_codes)
    ):
        raise ContractError("feedback_codes_invalid")
    allowed_codes = FEEDBACK_CODES_BY_VERDICT[verdict]
    if not set(raw_codes).issubset(allowed_codes):
        raise ContractError("feedback_codes_invalid")
    if verdict in {"eligible", "frontier"} and len(raw_codes) != 1:
        raise ContractError("feedback_codes_invalid")
    raw_metrics = values["metrics"]
    if not isinstance(raw_metrics, dict) or not set(raw_metrics).issubset(FEEDBACK_METRICS):
        raise ContractError("feedback_metrics_invalid")
    metrics: dict[str, float] = {}
    for key in FEEDBACK_METRICS:
        if key not in raw_metrics:
            continue
        value = raw_metrics[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContractError("feedback_metrics_invalid")
        try:
            number = float(value)
        except (OverflowError, ValueError):
            raise ContractError("feedback_metrics_invalid") from None
        if not math.isfinite(number) or abs(number) > 1_000_000_000:
            raise ContractError("feedback_metrics_invalid")
        metrics[key] = number
    ordered_codes = tuple(code for code in FEEDBACK_CODES if code in raw_codes)
    return CandidateFeedback(
        candidate_id,
        attempt,
        verdict,
        ordered_codes,
        MappingProxyType(metrics),
    )


@dataclass(frozen=True)
class PromotionEvidence:
    authority_id: str
    identity_digest: str
    candidate_id: str
    run_id: str
    receipt_id: str
    cleanup_proof_id: str
    evaluation_id: str
    evaluator_contract: str
    feedback_digest: str
    _signature: str = field(repr=False)

    def _payload(self) -> dict[str, str]:
        return {
            "schema": PROMOTION_EVIDENCE_SCHEMA,
            "authorityId": self.authority_id,
            "identityDigest": self.identity_digest,
            "candidateId": self.candidate_id,
            "runId": self.run_id,
            "receiptId": self.receipt_id,
            "cleanupProofId": self.cleanup_proof_id,
            "evaluationId": self.evaluation_id,
            "evaluatorContract": self.evaluator_contract,
            "feedbackDigest": self.feedback_digest,
        }


def _feedback_digest(feedback: CandidateFeedback) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(feedback.to_dict())).hexdigest()}"


def _issue_promotion_evidence(
    capability: EvaluatorCapability,
    *,
    feedback: CandidateFeedback,
    run_id: str,
    receipt_id: str,
    cleanup_proof_id: str,
    evaluation_id: str,
) -> PromotionEvidence:
    if (
        not isinstance(capability, EvaluatorCapability)
        or not isinstance(feedback, CandidateFeedback)
        or feedback.verdict != "eligible"
        or any(
            not isinstance(value, str) or CANDIDATE_ID_PATTERN.fullmatch(value) is None
            for value in (feedback.candidate_id, run_id, receipt_id, cleanup_proof_id, evaluation_id)
        )
    ):
        raise ContractError("promotion_evidence_invalid")
    evidence = PromotionEvidence(
        capability.authority_id,
        capability.identity_digest,
        feedback.candidate_id,
        run_id,
        receipt_id,
        cleanup_proof_id,
        evaluation_id,
        MAIN_LATENCY_EVALUATOR_ID,
        _feedback_digest(feedback),
        "",
    )
    signature = capability._sign_for(PROMOTION_EVIDENCE_SCHEMA, evidence._payload())
    return replace(evidence, _signature=signature)


@dataclass(frozen=True)
class _RuntimeObservationReceipt:
    receipt_id: str
    authority_id: str
    identity_digest: str
    candidate_id: str
    run_id: str
    evaluation_id: str
    predecessor_id: str
    observer_worker_identity: str
    observer_argv_identity: str
    observer_source_identity: str
    _signature: str = field(repr=False)

    KIND: ClassVar[str]
    SCHEMA: ClassVar[str]

    def _details(self) -> dict[str, Any]:
        raise NotImplementedError

    def _unsigned_payload(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "kind": self.KIND,
            "authorityId": self.authority_id,
            "identityDigest": self.identity_digest,
            "candidateId": self.candidate_id,
            "runId": self.run_id,
            "evaluationId": self.evaluation_id,
            "predecessorId": self.predecessor_id,
            "observerWorkerIdentity": self.observer_worker_identity,
            "observerArgvIdentity": self.observer_argv_identity,
            "observerSourceIdentity": self.observer_source_identity,
            **self._details(),
        }

    def _signature_payload(self) -> dict[str, Any]:
        return {"receiptId": self.receipt_id, "observation": self._unsigned_payload()}

    def to_dict(self) -> dict[str, Any]:
        result = self._unsigned_payload()
        result["receiptId"] = self.receipt_id
        result["signature"] = self._signature
        return result


@dataclass(frozen=True)
class CanaryDeploymentObservation(_RuntimeObservationReceipt):
    candidate_config: MainLatencyConfig
    previous_runtime_identity: str
    deployment_identity: str
    runtime_identity: str
    backend_epoch: str
    observation_window_id: str
    health_receipt_id: str
    healthy: bool
    error_receipt_id: str
    error_count: int
    sample_receipt_id: str
    sample_count: int
    rollback_checkpoint_id: str
    cleanup_proof_id: str
    remaining_processes: int
    remaining_artifacts: int
    rollback_ready: bool

    KIND: ClassVar[str] = "canary_deployment"
    SCHEMA: ClassVar[str] = CANARY_DEPLOYMENT_OBSERVATION_SCHEMA

    def _details(self) -> dict[str, Any]:
        return {
            "candidateConfig": self.candidate_config.to_dict(),
            "previousRuntimeIdentity": self.previous_runtime_identity,
            "deploymentIdentity": self.deployment_identity,
            "runtimeIdentity": self.runtime_identity,
            "backendEpoch": self.backend_epoch,
            "observationWindowId": self.observation_window_id,
            "healthReceiptId": self.health_receipt_id,
            "healthy": self.healthy,
            "errorReceiptId": self.error_receipt_id,
            "errorCount": self.error_count,
            "sampleReceiptId": self.sample_receipt_id,
            "sampleCount": self.sample_count,
            "rollbackCheckpointId": self.rollback_checkpoint_id,
            "cleanupProofId": self.cleanup_proof_id,
            "remainingProcesses": self.remaining_processes,
            "remainingArtifacts": self.remaining_artifacts,
            "rollbackReady": self.rollback_ready,
        }


@dataclass(frozen=True)
class SoakEvaluationObservation(_RuntimeObservationReceipt):
    candidate_config: MainLatencyConfig
    deployment_identity: str
    runtime_identity: str
    backend_epoch: str
    observation_window_id: str
    soak_receipt_id: str
    evaluation_receipt_id: str
    health_receipt_id: str
    healthy: bool
    error_receipt_id: str
    error_count: int
    sample_receipt_id: str
    sample_count: int
    rollback_checkpoint_id: str
    cleanup_proof_id: str
    remaining_processes: int
    remaining_artifacts: int
    rollback_ready_receipt_id: str
    rollback_ready: bool

    KIND: ClassVar[str] = "soak_evaluation"
    SCHEMA: ClassVar[str] = SOAK_EVALUATION_OBSERVATION_SCHEMA

    def _details(self) -> dict[str, Any]:
        return {
            "candidateConfig": self.candidate_config.to_dict(),
            "deploymentIdentity": self.deployment_identity,
            "runtimeIdentity": self.runtime_identity,
            "backendEpoch": self.backend_epoch,
            "observationWindowId": self.observation_window_id,
            "soakReceiptId": self.soak_receipt_id,
            "evaluationReceiptId": self.evaluation_receipt_id,
            "healthReceiptId": self.health_receipt_id,
            "healthy": self.healthy,
            "errorReceiptId": self.error_receipt_id,
            "errorCount": self.error_count,
            "sampleReceiptId": self.sample_receipt_id,
            "sampleCount": self.sample_count,
            "rollbackCheckpointId": self.rollback_checkpoint_id,
            "cleanupProofId": self.cleanup_proof_id,
            "remainingProcesses": self.remaining_processes,
            "remainingArtifacts": self.remaining_artifacts,
            "rollbackReadyReceiptId": self.rollback_ready_receipt_id,
            "rollbackReady": self.rollback_ready,
        }


@dataclass(frozen=True)
class RollbackCleanupObservation(_RuntimeObservationReceipt):
    candidate_config: MainLatencyConfig
    deployed_runtime_identity: str
    deployed_backend_epoch: str
    observation_window_id: str
    failure_receipt_id: str
    rollback_checkpoint_id: str
    restored_runtime_identity: str
    cleanup_proof_id: str
    remaining_processes: int
    remaining_artifacts: int
    health_receipt_id: str
    healthy: bool
    error_receipt_id: str
    error_count: int

    KIND: ClassVar[str] = "rollback_cleanup"
    SCHEMA: ClassVar[str] = ROLLBACK_CLEANUP_OBSERVATION_SCHEMA

    def _details(self) -> dict[str, Any]:
        return {
            "candidateConfig": self.candidate_config.to_dict(),
            "deployedRuntimeIdentity": self.deployed_runtime_identity,
            "deployedBackendEpoch": self.deployed_backend_epoch,
            "observationWindowId": self.observation_window_id,
            "failureReceiptId": self.failure_receipt_id,
            "rollbackCheckpointId": self.rollback_checkpoint_id,
            "restoredRuntimeIdentity": self.restored_runtime_identity,
            "cleanupProofId": self.cleanup_proof_id,
            "remainingProcesses": self.remaining_processes,
            "remainingArtifacts": self.remaining_artifacts,
            "healthReceiptId": self.health_receipt_id,
            "healthy": self.healthy,
            "errorReceiptId": self.error_receipt_id,
            "errorCount": self.error_count,
        }


_OBSERVATION_COMMON_FIELDS = (
    "schema",
    "kind",
    "authorityId",
    "identityDigest",
    "candidateId",
    "runId",
    "evaluationId",
    "predecessorId",
    "observerWorkerIdentity",
    "observerArgvIdentity",
    "observerSourceIdentity",
    "receiptId",
    "signature",
)


def _compile_runtime_observation(
    raw: Any,
    expected_type: type[_RuntimeObservationReceipt],
) -> _RuntimeObservationReceipt:
    detail_fields: tuple[str, ...]
    if expected_type is CanaryDeploymentObservation:
        detail_fields = (
            "candidateConfig",
            "previousRuntimeIdentity",
            "deploymentIdentity",
            "runtimeIdentity",
            "backendEpoch",
            "observationWindowId",
            "healthReceiptId",
            "healthy",
            "errorReceiptId",
            "errorCount",
            "sampleReceiptId",
            "sampleCount",
            "rollbackCheckpointId",
            "cleanupProofId",
            "remainingProcesses",
            "remainingArtifacts",
            "rollbackReady",
        )
    elif expected_type is SoakEvaluationObservation:
        detail_fields = (
            "candidateConfig",
            "deploymentIdentity",
            "runtimeIdentity",
            "backendEpoch",
            "observationWindowId",
            "soakReceiptId",
            "evaluationReceiptId",
            "healthReceiptId",
            "healthy",
            "errorReceiptId",
            "errorCount",
            "sampleReceiptId",
            "sampleCount",
            "rollbackCheckpointId",
            "cleanupProofId",
            "remainingProcesses",
            "remainingArtifacts",
            "rollbackReadyReceiptId",
            "rollbackReady",
        )
    elif expected_type is RollbackCleanupObservation:
        detail_fields = (
            "candidateConfig",
            "deployedRuntimeIdentity",
            "deployedBackendEpoch",
            "observationWindowId",
            "failureReceiptId",
            "rollbackCheckpointId",
            "restoredRuntimeIdentity",
            "cleanupProofId",
            "remainingProcesses",
            "remainingArtifacts",
            "healthReceiptId",
            "healthy",
            "errorReceiptId",
            "errorCount",
        )
    else:
        raise ContractError("runtime_observer_receipt_invalid")
    values = _require_exact_fields(
        raw,
        (*_OBSERVATION_COMMON_FIELDS, *detail_fields),
        "runtime_observer_receipt_invalid",
    )
    if values["schema"] != expected_type.SCHEMA or values["kind"] != expected_type.KIND:
        raise ContractError("runtime_observer_receipt_invalid")
    common = {
        "receipt_id": values["receiptId"],
        "authority_id": values["authorityId"],
        "identity_digest": values["identityDigest"],
        "candidate_id": values["candidateId"],
        "run_id": values["runId"],
        "evaluation_id": values["evaluationId"],
        "predecessor_id": values["predecessorId"],
        "observer_worker_identity": values["observerWorkerIdentity"],
        "observer_argv_identity": values["observerArgvIdentity"],
        "observer_source_identity": values["observerSourceIdentity"],
        "_signature": values["signature"],
        "candidate_config": MainLatencyConfig.from_mapping(values["candidateConfig"]),
    }
    if expected_type is CanaryDeploymentObservation:
        return CanaryDeploymentObservation(
            **common,
            previous_runtime_identity=values["previousRuntimeIdentity"],
            deployment_identity=values["deploymentIdentity"],
            runtime_identity=values["runtimeIdentity"],
            backend_epoch=values["backendEpoch"],
            observation_window_id=values["observationWindowId"],
            health_receipt_id=values["healthReceiptId"],
            healthy=values["healthy"],
            error_receipt_id=values["errorReceiptId"],
            error_count=values["errorCount"],
            sample_receipt_id=values["sampleReceiptId"],
            sample_count=values["sampleCount"],
            rollback_checkpoint_id=values["rollbackCheckpointId"],
            cleanup_proof_id=values["cleanupProofId"],
            remaining_processes=values["remainingProcesses"],
            remaining_artifacts=values["remainingArtifacts"],
            rollback_ready=values["rollbackReady"],
        )
    if expected_type is SoakEvaluationObservation:
        return SoakEvaluationObservation(
            **common,
            deployment_identity=values["deploymentIdentity"],
            runtime_identity=values["runtimeIdentity"],
            backend_epoch=values["backendEpoch"],
            observation_window_id=values["observationWindowId"],
            soak_receipt_id=values["soakReceiptId"],
            evaluation_receipt_id=values["evaluationReceiptId"],
            health_receipt_id=values["healthReceiptId"],
            healthy=values["healthy"],
            error_receipt_id=values["errorReceiptId"],
            error_count=values["errorCount"],
            sample_receipt_id=values["sampleReceiptId"],
            sample_count=values["sampleCount"],
            rollback_checkpoint_id=values["rollbackCheckpointId"],
            cleanup_proof_id=values["cleanupProofId"],
            remaining_processes=values["remainingProcesses"],
            remaining_artifacts=values["remainingArtifacts"],
            rollback_ready_receipt_id=values["rollbackReadyReceiptId"],
            rollback_ready=values["rollbackReady"],
        )
    return RollbackCleanupObservation(
        **common,
        deployed_runtime_identity=values["deployedRuntimeIdentity"],
        deployed_backend_epoch=values["deployedBackendEpoch"],
        observation_window_id=values["observationWindowId"],
        failure_receipt_id=values["failureReceiptId"],
        rollback_checkpoint_id=values["rollbackCheckpointId"],
        restored_runtime_identity=values["restoredRuntimeIdentity"],
        cleanup_proof_id=values["cleanupProofId"],
        remaining_processes=values["remainingProcesses"],
        remaining_artifacts=values["remainingArtifacts"],
        health_receipt_id=values["healthReceiptId"],
        healthy=values["healthy"],
        error_receipt_id=values["errorReceiptId"],
        error_count=values["errorCount"],
    )


@dataclass(frozen=True)
class _EvaluatorLifecycleEvidence:
    evidence_id: str
    authority_id: str
    identity_digest: str
    candidate_id: str
    predecessor_id: str
    _signature: str = field(repr=False)

    KIND: ClassVar[str]
    SCHEMA: ClassVar[str]

    def _details(self) -> dict[str, Any]:
        raise NotImplementedError

    def _unsigned_payload(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "kind": self.KIND,
            "authorityId": self.authority_id,
            "identityDigest": self.identity_digest,
            "candidateId": self.candidate_id,
            "predecessorId": self.predecessor_id,
            **self._details(),
        }

    def _signature_payload(self) -> dict[str, Any]:
        return {"evidenceId": self.evidence_id, "evidence": self._unsigned_payload()}

    def to_dict(self) -> dict[str, Any]:
        result = self._unsigned_payload()
        result["evidenceId"] = self.evidence_id
        result["signature"] = self._signature
        return result


@dataclass(frozen=True)
class CanaryDeploymentEvidence(_EvaluatorLifecycleEvidence):
    candidate_config: MainLatencyConfig
    previous_runtime_identity: str
    deployment_identity: str
    runtime_identity: str
    backend_epoch: str
    observation_window_id: str
    health_receipt_id: str
    healthy: bool
    error_receipt_id: str
    error_count: int
    sample_receipt_id: str
    sample_count: int
    rollback_checkpoint_id: str
    cleanup_proof_id: str
    remaining_processes: int
    remaining_artifacts: int
    rollback_ready: bool
    observation_receipt: CanaryDeploymentObservation

    KIND: ClassVar[str] = "canary_deployment"
    SCHEMA: ClassVar[str] = CANARY_DEPLOYMENT_EVIDENCE_SCHEMA

    def _details(self) -> dict[str, Any]:
        return {
            "candidateConfig": self.candidate_config.to_dict(),
            "previousRuntimeIdentity": self.previous_runtime_identity,
            "deploymentIdentity": self.deployment_identity,
            "runtimeIdentity": self.runtime_identity,
            "backendEpoch": self.backend_epoch,
            "observationWindowId": self.observation_window_id,
            "healthReceiptId": self.health_receipt_id,
            "healthy": self.healthy,
            "errorReceiptId": self.error_receipt_id,
            "errorCount": self.error_count,
            "sampleReceiptId": self.sample_receipt_id,
            "sampleCount": self.sample_count,
            "rollbackCheckpointId": self.rollback_checkpoint_id,
            "cleanupProofId": self.cleanup_proof_id,
            "remainingProcesses": self.remaining_processes,
            "remainingArtifacts": self.remaining_artifacts,
            "rollbackReady": self.rollback_ready,
            "observationReceipt": self.observation_receipt.to_dict(),
        }


@dataclass(frozen=True)
class SoakEvaluationEvidence(_EvaluatorLifecycleEvidence):
    candidate_config: MainLatencyConfig
    deployment_identity: str
    runtime_identity: str
    backend_epoch: str
    observation_window_id: str
    soak_receipt_id: str
    evaluation_receipt_id: str
    health_receipt_id: str
    healthy: bool
    error_receipt_id: str
    error_count: int
    sample_receipt_id: str
    sample_count: int
    rollback_checkpoint_id: str
    cleanup_proof_id: str
    remaining_processes: int
    remaining_artifacts: int
    rollback_ready_receipt_id: str
    rollback_ready: bool
    observation_receipt: SoakEvaluationObservation

    KIND: ClassVar[str] = "soak_evaluation"
    SCHEMA: ClassVar[str] = SOAK_EVALUATION_EVIDENCE_SCHEMA

    def _details(self) -> dict[str, Any]:
        return {
            "candidateConfig": self.candidate_config.to_dict(),
            "deploymentIdentity": self.deployment_identity,
            "runtimeIdentity": self.runtime_identity,
            "backendEpoch": self.backend_epoch,
            "observationWindowId": self.observation_window_id,
            "soakReceiptId": self.soak_receipt_id,
            "evaluationReceiptId": self.evaluation_receipt_id,
            "healthReceiptId": self.health_receipt_id,
            "healthy": self.healthy,
            "errorReceiptId": self.error_receipt_id,
            "errorCount": self.error_count,
            "sampleReceiptId": self.sample_receipt_id,
            "sampleCount": self.sample_count,
            "rollbackCheckpointId": self.rollback_checkpoint_id,
            "cleanupProofId": self.cleanup_proof_id,
            "remainingProcesses": self.remaining_processes,
            "remainingArtifacts": self.remaining_artifacts,
            "rollbackReadyReceiptId": self.rollback_ready_receipt_id,
            "rollbackReady": self.rollback_ready,
            "observationReceipt": self.observation_receipt.to_dict(),
        }


@dataclass(frozen=True)
class RollbackCleanupEvidence(_EvaluatorLifecycleEvidence):
    candidate_config: MainLatencyConfig
    deployed_runtime_identity: str
    deployed_backend_epoch: str
    observation_window_id: str
    failure_receipt_id: str
    rollback_checkpoint_id: str
    restored_runtime_identity: str
    cleanup_proof_id: str
    remaining_processes: int
    remaining_artifacts: int
    health_receipt_id: str
    healthy: bool
    error_receipt_id: str
    error_count: int
    observation_receipt: RollbackCleanupObservation

    KIND: ClassVar[str] = "rollback_cleanup"
    SCHEMA: ClassVar[str] = ROLLBACK_CLEANUP_EVIDENCE_SCHEMA

    def _details(self) -> dict[str, Any]:
        return {
            "candidateConfig": self.candidate_config.to_dict(),
            "deployedRuntimeIdentity": self.deployed_runtime_identity,
            "deployedBackendEpoch": self.deployed_backend_epoch,
            "observationWindowId": self.observation_window_id,
            "failureReceiptId": self.failure_receipt_id,
            "rollbackCheckpointId": self.rollback_checkpoint_id,
            "restoredRuntimeIdentity": self.restored_runtime_identity,
            "cleanupProofId": self.cleanup_proof_id,
            "remainingProcesses": self.remaining_processes,
            "remainingArtifacts": self.remaining_artifacts,
            "healthReceiptId": self.health_receipt_id,
            "healthy": self.healthy,
            "errorReceiptId": self.error_receipt_id,
            "errorCount": self.error_count,
            "observationReceipt": self.observation_receipt.to_dict(),
        }


@dataclass(frozen=True)
class _LifecycleReceipt:
    receipt_id: str
    authority_id: str
    identity_digest: str
    candidate_id: str
    run_id: str
    evaluation_id: str
    predecessor_id: str
    _signature: str = field(repr=False)

    KIND: ClassVar[str]
    SCHEMA: ClassVar[str]

    def _unsigned_payload(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "kind": self.KIND,
            "authorityId": self.authority_id,
            "identityDigest": self.identity_digest,
            "candidateId": self.candidate_id,
            "runId": self.run_id,
            "evaluationId": self.evaluation_id,
            "predecessorId": self.predecessor_id,
        }

    def _signature_payload(self) -> dict[str, Any]:
        return {"receiptId": self.receipt_id, "receipt": self._unsigned_payload()}

    def to_dict(self) -> dict[str, Any]:
        result = self._unsigned_payload()
        result["receiptId"] = self.receipt_id
        result["signature"] = self._signature
        return result


@dataclass(frozen=True)
class ApprovalReceipt(_LifecycleReceipt):
    KIND: ClassVar[str] = "approval"
    SCHEMA: ClassVar[str] = APPROVAL_RECEIPT_SCHEMA


@dataclass(frozen=True)
class CanaryReceipt(_LifecycleReceipt):
    deployment_evidence: CanaryDeploymentEvidence

    KIND: ClassVar[str] = "canary"
    SCHEMA: ClassVar[str] = CANARY_RECEIPT_SCHEMA

    def _unsigned_payload(self) -> dict[str, Any]:
        result = super()._unsigned_payload()
        result["deploymentEvidence"] = self.deployment_evidence.to_dict()
        return result


@dataclass(frozen=True)
class AcceptanceReceipt(_LifecycleReceipt):
    soak_evidence: SoakEvaluationEvidence

    KIND: ClassVar[str] = "acceptance"
    SCHEMA: ClassVar[str] = ACCEPTANCE_RECEIPT_SCHEMA

    def _unsigned_payload(self) -> dict[str, Any]:
        result = super()._unsigned_payload()
        result["soakEvidence"] = self.soak_evidence.to_dict()
        return result


@dataclass(frozen=True)
class RollbackReceipt(_LifecycleReceipt):
    cleanup_evidence: RollbackCleanupEvidence

    KIND: ClassVar[str] = "rollback"
    SCHEMA: ClassVar[str] = ROLLBACK_RECEIPT_SCHEMA

    def _unsigned_payload(self) -> dict[str, Any]:
        result = super()._unsigned_payload()
        result["cleanupEvidence"] = self.cleanup_evidence.to_dict()
        return result


def _promotion_signature_valid(
    trust_root: CoordinatorTrustRoot,
    evidence: Any,
) -> bool:
    return (
        isinstance(evidence, PromotionEvidence)
        and evidence.authority_id == trust_root.authority_id
        and evidence.identity_digest == trust_root.identity_digest
        and evidence.evaluator_contract == MAIN_LATENCY_EVALUATOR_ID
        and trust_root._verify_evaluator(
            PROMOTION_EVIDENCE_SCHEMA,
            evidence._payload(),
            evidence._signature,
        )
    )


def _lifecycle_receipt_id(receipt: _LifecycleReceipt) -> str:
    payload = {
        "schema": LIFECYCLE_RECEIPT_ID_SCHEMA,
        "receipt": receipt._unsigned_payload(),
    }
    return f"sha256:{hashlib.sha256(_canonical_bytes(payload)).hexdigest()}"


def _lifecycle_evidence_id(evidence: _EvaluatorLifecycleEvidence) -> str:
    payload = {
        "schema": LIFECYCLE_EVIDENCE_ID_SCHEMA,
        "evidence": evidence._unsigned_payload(),
    }
    return f"sha256:{hashlib.sha256(_canonical_bytes(payload)).hexdigest()}"


def _runtime_observation_receipt_id(receipt: _RuntimeObservationReceipt) -> str:
    payload = {
        "schema": RUNTIME_OBSERVATION_RECEIPT_ID_SCHEMA,
        "observation": receipt._unsigned_payload(),
    }
    return f"sha256:{hashlib.sha256(_canonical_bytes(payload)).hexdigest()}"


def _valid_config_object(value: Any) -> bool:
    try:
        return (
            type(value) is MainLatencyConfig
            and MainLatencyConfig.from_mapping(value.to_dict()) == value
        )
    except (AttributeError, ContractError):
        return False


def _valid_count(value: Any, *, positive: bool = False) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and (1 <= value <= 1_000_000_000 if positive else 0 <= value <= 1_000_000_000)
    )


def _runtime_observation_signature_valid(
    trust_root: CoordinatorTrustRoot,
    receipt: Any,
    expected_type: type[_RuntimeObservationReceipt],
) -> bool:
    if (
        type(receipt) is not expected_type
        or receipt.authority_id != trust_root.authority_id
        or receipt.identity_digest != trust_root.identity_digest
        or any(
            not isinstance(value, str)
            or CANDIDATE_ID_PATTERN.fullmatch(value) is None
            for value in (
                receipt.receipt_id,
                receipt.candidate_id,
                receipt.run_id,
                receipt.evaluation_id,
                receipt.predecessor_id,
                receipt.observer_worker_identity,
                receipt.observer_argv_identity,
                receipt.observer_source_identity,
            )
        )
    ):
        return False
    try:
        expected_id = _runtime_observation_receipt_id(receipt)
    except (AttributeError, ContractError, TypeError, ValueError):
        return False
    return (
        receipt.receipt_id == expected_id
        and trust_root._verify_runtime_observer(receipt)
    )


def _canary_deployment_observation_valid(
    trust_root: CoordinatorTrustRoot,
    receipt: Any,
) -> bool:
    digests = () if not isinstance(receipt, CanaryDeploymentObservation) else (
        receipt.previous_runtime_identity,
        receipt.deployment_identity,
        receipt.runtime_identity,
        receipt.backend_epoch,
        receipt.observation_window_id,
        receipt.health_receipt_id,
        receipt.error_receipt_id,
        receipt.sample_receipt_id,
        receipt.rollback_checkpoint_id,
        receipt.cleanup_proof_id,
    )
    return (
        _runtime_observation_signature_valid(
            trust_root,
            receipt,
            CanaryDeploymentObservation,
        )
        and _valid_config_object(receipt.candidate_config)
        and all(
            isinstance(value, str) and CANDIDATE_ID_PATTERN.fullmatch(value) is not None
            for value in digests
        )
        and len(set(digests[4:])) == len(digests[4:])
        and type(receipt.healthy) is bool
        and _valid_count(receipt.error_count)
        and _valid_count(receipt.sample_count)
        and _valid_count(receipt.remaining_processes)
        and _valid_count(receipt.remaining_artifacts)
        and type(receipt.rollback_ready) is bool
    )


def _soak_evaluation_observation_valid(
    trust_root: CoordinatorTrustRoot,
    receipt: Any,
) -> bool:
    digests = () if not isinstance(receipt, SoakEvaluationObservation) else (
        receipt.deployment_identity,
        receipt.runtime_identity,
        receipt.backend_epoch,
        receipt.observation_window_id,
        receipt.soak_receipt_id,
        receipt.evaluation_receipt_id,
        receipt.health_receipt_id,
        receipt.error_receipt_id,
        receipt.sample_receipt_id,
        receipt.rollback_checkpoint_id,
        receipt.cleanup_proof_id,
        receipt.rollback_ready_receipt_id,
    )
    return (
        _runtime_observation_signature_valid(
            trust_root,
            receipt,
            SoakEvaluationObservation,
        )
        and _valid_config_object(receipt.candidate_config)
        and all(
            isinstance(value, str) and CANDIDATE_ID_PATTERN.fullmatch(value) is not None
            for value in digests
        )
        and len(set(digests[3:])) == len(digests[3:])
        and type(receipt.healthy) is bool
        and _valid_count(receipt.error_count)
        and _valid_count(receipt.sample_count)
        and _valid_count(receipt.remaining_processes)
        and _valid_count(receipt.remaining_artifacts)
        and type(receipt.rollback_ready) is bool
    )


def _rollback_cleanup_observation_valid(
    trust_root: CoordinatorTrustRoot,
    receipt: Any,
) -> bool:
    digests = () if not isinstance(receipt, RollbackCleanupObservation) else (
        receipt.deployed_runtime_identity,
        receipt.deployed_backend_epoch,
        receipt.observation_window_id,
        receipt.failure_receipt_id,
        receipt.rollback_checkpoint_id,
        receipt.restored_runtime_identity,
        receipt.cleanup_proof_id,
        receipt.health_receipt_id,
        receipt.error_receipt_id,
    )
    return (
        _runtime_observation_signature_valid(
            trust_root,
            receipt,
            RollbackCleanupObservation,
        )
        and _valid_config_object(receipt.candidate_config)
        and all(
            isinstance(value, str) and CANDIDATE_ID_PATTERN.fullmatch(value) is not None
            for value in digests
        )
        and len(set(digests[2:])) == len(digests[2:])
        and type(receipt.healthy) is bool
        and _valid_count(receipt.error_count)
        and _valid_count(receipt.remaining_processes)
        and _valid_count(receipt.remaining_artifacts)
    )


def _fields_match(left: Any, right: Any, fields: Sequence[str]) -> bool:
    return all(
        getattr(left, field, object()) == getattr(right, field, object())
        for field in fields
    )


def _evaluator_evidence_signature_valid(
    trust_root: CoordinatorTrustRoot,
    evidence: Any,
    expected_type: type[_EvaluatorLifecycleEvidence],
) -> bool:
    if (
        type(evidence) is not expected_type
        or evidence.authority_id != trust_root.authority_id
        or evidence.identity_digest != trust_root.identity_digest
        or any(
            not isinstance(value, str) or CANDIDATE_ID_PATTERN.fullmatch(value) is None
            for value in (
                evidence.evidence_id,
                evidence.candidate_id,
                evidence.predecessor_id,
            )
        )
    ):
        return False
    try:
        expected_id = _lifecycle_evidence_id(evidence)
    except (AttributeError, ContractError, TypeError, ValueError):
        return False
    return (
        evidence.evidence_id == expected_id
        and trust_root._verify_evaluator(
            evidence.SCHEMA,
            evidence._signature_payload(),
            evidence._signature,
        )
    )


def _canary_deployment_evidence_valid(
    trust_root: CoordinatorTrustRoot,
    evidence: Any,
) -> bool:
    digest_values = () if not isinstance(evidence, CanaryDeploymentEvidence) else (
        evidence.previous_runtime_identity,
        evidence.deployment_identity,
        evidence.runtime_identity,
        evidence.backend_epoch,
        evidence.observation_window_id,
        evidence.health_receipt_id,
        evidence.error_receipt_id,
        evidence.sample_receipt_id,
        evidence.rollback_checkpoint_id,
        evidence.cleanup_proof_id,
    )
    return (
        _evaluator_evidence_signature_valid(
            trust_root,
            evidence,
            CanaryDeploymentEvidence,
        )
        and _valid_config_object(evidence.candidate_config)
        and _canary_deployment_observation_valid(
            trust_root,
            evidence.observation_receipt,
        )
        and evidence.observation_receipt.candidate_id == evidence.candidate_id
        and evidence.observation_receipt.predecessor_id == evidence.predecessor_id
        and _fields_match(
            evidence,
            evidence.observation_receipt,
            (
                "candidate_config",
                "previous_runtime_identity",
                "deployment_identity",
                "runtime_identity",
                "backend_epoch",
                "observation_window_id",
                "health_receipt_id",
                "healthy",
                "error_receipt_id",
                "error_count",
                "sample_receipt_id",
                "sample_count",
                "rollback_checkpoint_id",
                "cleanup_proof_id",
                "remaining_processes",
                "remaining_artifacts",
                "rollback_ready",
            ),
        )
        and all(
            isinstance(value, str) and CANDIDATE_ID_PATTERN.fullmatch(value) is not None
            for value in digest_values
        )
        and len(set(digest_values[4:])) == len(digest_values[4:])
        and evidence.previous_runtime_identity not in {
            evidence.deployment_identity,
            evidence.runtime_identity,
            evidence.backend_epoch,
        }
        and evidence.deployment_identity != evidence.runtime_identity
        and evidence.healthy is True
        and evidence.error_count == 0
        and _valid_count(evidence.error_count)
        and _valid_count(evidence.sample_count, positive=True)
        and evidence.remaining_processes == 0
        and evidence.remaining_artifacts == 0
        and _valid_count(evidence.remaining_processes)
        and _valid_count(evidence.remaining_artifacts)
        and evidence.rollback_ready is True
    )


def _soak_evaluation_evidence_valid(
    trust_root: CoordinatorTrustRoot,
    evidence: Any,
) -> bool:
    digest_values = () if not isinstance(evidence, SoakEvaluationEvidence) else (
        evidence.deployment_identity,
        evidence.runtime_identity,
        evidence.backend_epoch,
        evidence.observation_window_id,
        evidence.soak_receipt_id,
        evidence.evaluation_receipt_id,
        evidence.health_receipt_id,
        evidence.error_receipt_id,
        evidence.sample_receipt_id,
        evidence.rollback_checkpoint_id,
        evidence.cleanup_proof_id,
        evidence.rollback_ready_receipt_id,
    )
    return (
        _evaluator_evidence_signature_valid(
            trust_root,
            evidence,
            SoakEvaluationEvidence,
        )
        and _valid_config_object(evidence.candidate_config)
        and _soak_evaluation_observation_valid(
            trust_root,
            evidence.observation_receipt,
        )
        and evidence.observation_receipt.candidate_id == evidence.candidate_id
        and evidence.observation_receipt.predecessor_id == evidence.predecessor_id
        and _fields_match(
            evidence,
            evidence.observation_receipt,
            (
                "candidate_config",
                "deployment_identity",
                "runtime_identity",
                "backend_epoch",
                "observation_window_id",
                "soak_receipt_id",
                "evaluation_receipt_id",
                "health_receipt_id",
                "healthy",
                "error_receipt_id",
                "error_count",
                "sample_receipt_id",
                "sample_count",
                "rollback_checkpoint_id",
                "cleanup_proof_id",
                "remaining_processes",
                "remaining_artifacts",
                "rollback_ready_receipt_id",
                "rollback_ready",
            ),
        )
        and all(
            isinstance(value, str) and CANDIDATE_ID_PATTERN.fullmatch(value) is not None
            for value in digest_values
        )
        and len(set(digest_values[3:])) == len(digest_values[3:])
        and evidence.healthy is True
        and evidence.error_count == 0
        and _valid_count(evidence.error_count)
        and _valid_count(evidence.sample_count, positive=True)
        and evidence.remaining_processes == 0
        and evidence.remaining_artifacts == 0
        and _valid_count(evidence.remaining_processes)
        and _valid_count(evidence.remaining_artifacts)
        and evidence.rollback_ready is True
    )


def _rollback_cleanup_evidence_valid(
    trust_root: CoordinatorTrustRoot,
    evidence: Any,
) -> bool:
    digest_values = () if not isinstance(evidence, RollbackCleanupEvidence) else (
        evidence.deployed_runtime_identity,
        evidence.deployed_backend_epoch,
        evidence.observation_window_id,
        evidence.failure_receipt_id,
        evidence.rollback_checkpoint_id,
        evidence.restored_runtime_identity,
        evidence.cleanup_proof_id,
        evidence.health_receipt_id,
        evidence.error_receipt_id,
    )
    return (
        _evaluator_evidence_signature_valid(
            trust_root,
            evidence,
            RollbackCleanupEvidence,
        )
        and _valid_config_object(evidence.candidate_config)
        and _rollback_cleanup_observation_valid(
            trust_root,
            evidence.observation_receipt,
        )
        and evidence.observation_receipt.candidate_id == evidence.candidate_id
        and evidence.observation_receipt.predecessor_id == evidence.predecessor_id
        and _fields_match(
            evidence,
            evidence.observation_receipt,
            (
                "candidate_config",
                "deployed_runtime_identity",
                "deployed_backend_epoch",
                "observation_window_id",
                "failure_receipt_id",
                "rollback_checkpoint_id",
                "restored_runtime_identity",
                "cleanup_proof_id",
                "remaining_processes",
                "remaining_artifacts",
                "health_receipt_id",
                "healthy",
                "error_receipt_id",
                "error_count",
            ),
        )
        and all(
            isinstance(value, str) and CANDIDATE_ID_PATTERN.fullmatch(value) is not None
            for value in digest_values
        )
        and len(set(digest_values[2:])) == len(digest_values[2:])
        and evidence.deployed_runtime_identity != evidence.restored_runtime_identity
        and evidence.remaining_processes == 0
        and evidence.remaining_artifacts == 0
        and _valid_count(evidence.remaining_processes)
        and _valid_count(evidence.remaining_artifacts)
        and evidence.healthy is True
        and evidence.error_count == 0
        and _valid_count(evidence.error_count)
    )


def _lifecycle_receipt_valid(
    trust_root: CoordinatorTrustRoot,
    receipt: Any,
    expected_type: type[_LifecycleReceipt],
) -> bool:
    if (
        type(receipt) is not expected_type
        or receipt.authority_id != trust_root.authority_id
        or receipt.identity_digest != trust_root.identity_digest
        or any(
            not isinstance(value, str) or CANDIDATE_ID_PATTERN.fullmatch(value) is None
            for value in (
                receipt.receipt_id,
                receipt.candidate_id,
                receipt.run_id,
                receipt.evaluation_id,
                receipt.predecessor_id,
            )
        )
    ):
        return False
    if expected_type is CanaryReceipt and (
        not _canary_deployment_evidence_valid(trust_root, receipt.deployment_evidence)
        or not trust_root._observation_consumed(
            receipt.deployment_evidence.observation_receipt.receipt_id
        )
        or receipt.deployment_evidence.candidate_id != receipt.candidate_id
        or receipt.deployment_evidence.predecessor_id != receipt.predecessor_id
        or receipt.deployment_evidence.observation_receipt.run_id != receipt.run_id
        or receipt.deployment_evidence.observation_receipt.evaluation_id
        != receipt.evaluation_id
    ):
        return False
    if expected_type is AcceptanceReceipt and (
        not _soak_evaluation_evidence_valid(trust_root, receipt.soak_evidence)
        or not trust_root._observation_consumed(
            receipt.soak_evidence.observation_receipt.receipt_id
        )
        or receipt.soak_evidence.candidate_id != receipt.candidate_id
        or receipt.soak_evidence.predecessor_id != receipt.predecessor_id
        or receipt.soak_evidence.observation_receipt.run_id != receipt.run_id
        or receipt.soak_evidence.observation_receipt.evaluation_id
        != receipt.evaluation_id
    ):
        return False
    if expected_type is RollbackReceipt and (
        not _rollback_cleanup_evidence_valid(trust_root, receipt.cleanup_evidence)
        or not trust_root._observation_consumed(
            receipt.cleanup_evidence.observation_receipt.receipt_id
        )
        or receipt.cleanup_evidence.candidate_id != receipt.candidate_id
        or receipt.cleanup_evidence.predecessor_id != receipt.predecessor_id
        or receipt.cleanup_evidence.observation_receipt.run_id != receipt.run_id
        or receipt.cleanup_evidence.observation_receipt.evaluation_id
        != receipt.evaluation_id
    ):
        return False
    try:
        expected_id = _lifecycle_receipt_id(receipt)
    except (AttributeError, ContractError, TypeError, ValueError):
        return False
    return (
        receipt.receipt_id == expected_id
        and trust_root._verify_lifecycle(
            receipt.SCHEMA,
            receipt._signature_payload(),
            receipt._signature,
        )
    )


def _issue_lifecycle_receipt(
    capability: LifecycleCapability,
    receipt_type: type[_LifecycleReceipt],
    *,
    candidate_id: str,
    run_id: str,
    evaluation_id: str,
    predecessor_id: str,
    evidence: _EvaluatorLifecycleEvidence | None = None,
) -> _LifecycleReceipt:
    if (
        not isinstance(capability, LifecycleCapability)
        or any(
            not isinstance(value, str) or CANDIDATE_ID_PATTERN.fullmatch(value) is None
            for value in (candidate_id, run_id, evaluation_id, predecessor_id)
        )
    ):
        raise ContractError("lifecycle_capability_invalid")
    extra: dict[str, Any] = {}
    if receipt_type is CanaryReceipt:
        extra["deployment_evidence"] = evidence
    elif receipt_type is AcceptanceReceipt:
        extra["soak_evidence"] = evidence
    elif receipt_type is RollbackReceipt:
        extra["cleanup_evidence"] = evidence
    elif evidence is not None:
        raise ContractError("lifecycle_evidence_invalid")
    receipt = receipt_type(
        receipt_id="",
        authority_id=capability.authority_id,
        identity_digest=capability.identity_digest,
        candidate_id=candidate_id,
        run_id=run_id,
        evaluation_id=evaluation_id,
        predecessor_id=predecessor_id,
        _signature="",
        **extra,
    )
    receipt_id = _lifecycle_receipt_id(receipt)
    receipt = replace(receipt, receipt_id=receipt_id)
    return replace(
        receipt,
        _signature=capability._sign_for(receipt.SCHEMA, receipt._signature_payload()),
    )


def issue_approval_receipt(
    capability: LifecycleCapability,
    trust_root: CoordinatorTrustRoot,
    promotion_evidence: PromotionEvidence,
) -> ApprovalReceipt:
    trust_root = _validated_trust_root(trust_root)
    if (
        not _capability_matches(
            trust_root,
            capability,
            LifecycleCapability,
            "_verify_lifecycle",
        )
        or not _promotion_signature_valid(trust_root, promotion_evidence)
    ):
        raise ContractError("approval_receipt_invalid")
    return _issue_lifecycle_receipt(
        capability,
        ApprovalReceipt,
        candidate_id=promotion_evidence.candidate_id,
        run_id=promotion_evidence.run_id,
        evaluation_id=promotion_evidence.evaluation_id,
        predecessor_id=promotion_evidence.evaluation_id,
    )


def _candidate_manifest_valid_for_root(
    trust_root: CoordinatorTrustRoot,
    candidate: Any,
    expected_candidate_id: str,
) -> bool:
    return (
        type(candidate) is CandidateManifest
        and candidate.identities == trust_root.pinned_identities
        and _valid_config_object(candidate.baseline_config)
        and _valid_config_object(candidate.candidate_config)
        and candidate.candidate_id == expected_candidate_id
        and _candidate_id_for(
            candidate.identities,
            candidate.baseline_config,
            candidate.candidate_config,
        ) == candidate.candidate_id
    )


def _sign_lifecycle_evidence(
    capability: EvaluatorCapability,
    evidence: _EvaluatorLifecycleEvidence,
) -> _EvaluatorLifecycleEvidence:
    evidence = replace(evidence, evidence_id=_lifecycle_evidence_id(evidence))
    return replace(
        evidence,
        _signature=capability._sign_for(evidence.SCHEMA, evidence._signature_payload()),
    )


def request_canary_deployment_observation(
    trust_root: CoordinatorTrustRoot,
    approval: ApprovalReceipt,
    candidate: CandidateManifest,
) -> CanaryDeploymentObservation:
    trust_root = _validated_trust_root(trust_root)
    if (
        not _lifecycle_receipt_valid(trust_root, approval, ApprovalReceipt)
        or not _candidate_manifest_valid_for_root(
            trust_root,
            candidate,
            approval.candidate_id,
        )
    ):
        raise ContractError("canary_deployment_observation_invalid")
    request = {
        "schema": RUNTIME_OBSERVER_REQUEST_SCHEMA,
        "kind": CanaryDeploymentObservation.KIND,
        "authorityId": trust_root.authority_id,
        "identityDigest": trust_root.identity_digest,
        "candidateId": approval.candidate_id,
        "runId": approval.run_id,
        "evaluationId": approval.evaluation_id,
        "predecessorId": approval.receipt_id,
        "expected": {"candidateConfig": candidate.candidate_config.to_dict()},
    }
    try:
        receipt = _compile_runtime_observation(
            trust_root._request_runtime_observation(request),
            CanaryDeploymentObservation,
        )
    except ContractError as exc:
        if exc.code == "runtime_observer_unavailable":
            raise
        raise ContractError("canary_deployment_observation_invalid") from None
    if (
        not _canary_deployment_observation_valid(trust_root, receipt)
        or receipt.authority_id != trust_root.authority_id
        or receipt.identity_digest != trust_root.identity_digest
        or receipt.candidate_id != approval.candidate_id
        or receipt.run_id != approval.run_id
        or receipt.evaluation_id != approval.evaluation_id
        or receipt.predecessor_id != approval.receipt_id
        or receipt.candidate_config != candidate.candidate_config
    ):
        raise ContractError("canary_deployment_observation_invalid")
    return receipt


def issue_canary_deployment_evidence(
    capability: EvaluatorCapability,
    trust_root: CoordinatorTrustRoot,
    approval: ApprovalReceipt,
    candidate: CandidateManifest,
    observation: CanaryDeploymentObservation,
) -> CanaryDeploymentEvidence:
    trust_root = _validated_trust_root(trust_root)
    if (
        not _capability_matches(
            trust_root,
            capability,
            EvaluatorCapability,
            "_verify_evaluator",
        )
        or not _lifecycle_receipt_valid(trust_root, approval, ApprovalReceipt)
        or not _candidate_manifest_valid_for_root(
            trust_root,
            candidate,
            approval.candidate_id,
        )
        or not _canary_deployment_observation_valid(trust_root, observation)
        or observation.candidate_id != approval.candidate_id
        or observation.run_id != approval.run_id
        or observation.evaluation_id != approval.evaluation_id
        or observation.predecessor_id != approval.receipt_id
        or observation.candidate_config != candidate.candidate_config
    ):
        raise ContractError("canary_deployment_evidence_invalid")
    evidence = CanaryDeploymentEvidence(
        evidence_id="",
        authority_id=capability.authority_id,
        identity_digest=capability.identity_digest,
        candidate_id=approval.candidate_id,
        predecessor_id=approval.receipt_id,
        _signature="",
        candidate_config=observation.candidate_config,
        previous_runtime_identity=observation.previous_runtime_identity,
        deployment_identity=observation.deployment_identity,
        runtime_identity=observation.runtime_identity,
        backend_epoch=observation.backend_epoch,
        observation_window_id=observation.observation_window_id,
        health_receipt_id=observation.health_receipt_id,
        healthy=observation.healthy,
        error_receipt_id=observation.error_receipt_id,
        error_count=observation.error_count,
        sample_receipt_id=observation.sample_receipt_id,
        sample_count=observation.sample_count,
        rollback_checkpoint_id=observation.rollback_checkpoint_id,
        cleanup_proof_id=observation.cleanup_proof_id,
        remaining_processes=observation.remaining_processes,
        remaining_artifacts=observation.remaining_artifacts,
        rollback_ready=observation.rollback_ready,
        observation_receipt=observation,
    )
    evidence = _sign_lifecycle_evidence(capability, evidence)
    if (
        not _canary_deployment_evidence_valid(trust_root, evidence)
        or not trust_root._consume_once("observer", observation.receipt_id)
    ):
        raise ContractError("canary_deployment_evidence_invalid")
    return evidence


def issue_canary_receipt(
    capability: LifecycleCapability,
    trust_root: CoordinatorTrustRoot,
    approval: ApprovalReceipt,
    deployment_evidence: CanaryDeploymentEvidence,
) -> CanaryReceipt:
    trust_root = _validated_trust_root(trust_root)
    if (
        not _capability_matches(
            trust_root,
            capability,
            LifecycleCapability,
            "_verify_lifecycle",
        )
        or not _lifecycle_receipt_valid(trust_root, approval, ApprovalReceipt)
        or not _canary_deployment_evidence_valid(trust_root, deployment_evidence)
        or not trust_root._observation_consumed(
            deployment_evidence.observation_receipt.receipt_id
        )
        or deployment_evidence.candidate_id != approval.candidate_id
        or deployment_evidence.predecessor_id != approval.receipt_id
    ):
        raise ContractError("canary_receipt_invalid")
    return _issue_lifecycle_receipt(
        capability,
        CanaryReceipt,
        candidate_id=approval.candidate_id,
        run_id=approval.run_id,
        evaluation_id=approval.evaluation_id,
        predecessor_id=approval.receipt_id,
        evidence=deployment_evidence,
    )


def request_soak_evaluation_observation(
    trust_root: CoordinatorTrustRoot,
    canary: CanaryReceipt,
) -> SoakEvaluationObservation:
    trust_root = _validated_trust_root(trust_root)
    if not _lifecycle_receipt_valid(trust_root, canary, CanaryReceipt):
        raise ContractError("soak_evaluation_observation_invalid")
    deployment = canary.deployment_evidence
    request = {
        "schema": RUNTIME_OBSERVER_REQUEST_SCHEMA,
        "kind": SoakEvaluationObservation.KIND,
        "authorityId": trust_root.authority_id,
        "identityDigest": trust_root.identity_digest,
        "candidateId": canary.candidate_id,
        "runId": canary.run_id,
        "evaluationId": canary.evaluation_id,
        "predecessorId": canary.receipt_id,
        "expected": {
            "candidateConfig": deployment.candidate_config.to_dict(),
            "deploymentIdentity": deployment.deployment_identity,
            "runtimeIdentity": deployment.runtime_identity,
            "backendEpoch": deployment.backend_epoch,
            "rollbackCheckpointId": deployment.rollback_checkpoint_id,
        },
    }
    try:
        receipt = _compile_runtime_observation(
            trust_root._request_runtime_observation(request),
            SoakEvaluationObservation,
        )
    except ContractError as exc:
        if exc.code == "runtime_observer_unavailable":
            raise
        raise ContractError("soak_evaluation_observation_invalid") from None
    if (
        not _soak_evaluation_observation_valid(trust_root, receipt)
        or receipt.authority_id != trust_root.authority_id
        or receipt.identity_digest != trust_root.identity_digest
        or receipt.candidate_id != canary.candidate_id
        or receipt.run_id != canary.run_id
        or receipt.evaluation_id != canary.evaluation_id
        or receipt.predecessor_id != canary.receipt_id
    ):
        raise ContractError("soak_evaluation_observation_invalid")
    return receipt


def issue_soak_evaluation_evidence(
    capability: EvaluatorCapability,
    trust_root: CoordinatorTrustRoot,
    canary: CanaryReceipt,
    observation: SoakEvaluationObservation,
) -> SoakEvaluationEvidence:
    trust_root = _validated_trust_root(trust_root)
    deployment = canary.deployment_evidence if isinstance(canary, CanaryReceipt) else None
    fresh_ids = () if not isinstance(observation, SoakEvaluationObservation) else (
        observation.observation_window_id,
        observation.soak_receipt_id,
        observation.evaluation_receipt_id,
        observation.health_receipt_id,
        observation.error_receipt_id,
        observation.sample_receipt_id,
        observation.cleanup_proof_id,
        observation.rollback_ready_receipt_id,
    )
    prior_ids = () if deployment is None else (
        deployment.observation_window_id,
        deployment.health_receipt_id,
        deployment.error_receipt_id,
        deployment.sample_receipt_id,
        deployment.cleanup_proof_id,
        deployment.evidence_id,
        deployment.observation_receipt.receipt_id,
    )
    if (
        not _capability_matches(
            trust_root,
            capability,
            EvaluatorCapability,
            "_verify_evaluator",
        )
        or not _lifecycle_receipt_valid(trust_root, canary, CanaryReceipt)
        or not _soak_evaluation_observation_valid(trust_root, observation)
        or observation.candidate_id != canary.candidate_id
        or observation.run_id != canary.run_id
        or observation.evaluation_id != canary.evaluation_id
        or observation.predecessor_id != canary.receipt_id
        or observation.candidate_config != deployment.candidate_config
        or observation.deployment_identity != deployment.deployment_identity
        or observation.runtime_identity != deployment.runtime_identity
        or observation.backend_epoch != deployment.backend_epoch
        or observation.rollback_checkpoint_id != deployment.rollback_checkpoint_id
        or not _valid_count(observation.sample_count, positive=True)
        or observation.sample_count <= deployment.sample_count
        or any(value in prior_ids for value in fresh_ids)
    ):
        raise ContractError("soak_evaluation_evidence_invalid")
    evidence = SoakEvaluationEvidence(
        evidence_id="",
        authority_id=capability.authority_id,
        identity_digest=capability.identity_digest,
        candidate_id=canary.candidate_id,
        predecessor_id=canary.receipt_id,
        _signature="",
        candidate_config=observation.candidate_config,
        deployment_identity=observation.deployment_identity,
        runtime_identity=observation.runtime_identity,
        backend_epoch=observation.backend_epoch,
        observation_window_id=observation.observation_window_id,
        soak_receipt_id=observation.soak_receipt_id,
        evaluation_receipt_id=observation.evaluation_receipt_id,
        health_receipt_id=observation.health_receipt_id,
        healthy=observation.healthy,
        error_receipt_id=observation.error_receipt_id,
        error_count=observation.error_count,
        sample_receipt_id=observation.sample_receipt_id,
        sample_count=observation.sample_count,
        rollback_checkpoint_id=observation.rollback_checkpoint_id,
        cleanup_proof_id=observation.cleanup_proof_id,
        remaining_processes=observation.remaining_processes,
        remaining_artifacts=observation.remaining_artifacts,
        rollback_ready_receipt_id=observation.rollback_ready_receipt_id,
        rollback_ready=observation.rollback_ready,
        observation_receipt=observation,
    )
    evidence = _sign_lifecycle_evidence(capability, evidence)
    if (
        not _soak_evaluation_evidence_valid(trust_root, evidence)
        or not trust_root._consume_once("observer", observation.receipt_id)
    ):
        raise ContractError("soak_evaluation_evidence_invalid")
    return evidence


def issue_acceptance_receipt(
    capability: LifecycleCapability,
    trust_root: CoordinatorTrustRoot,
    canary: CanaryReceipt,
    soak_evidence: SoakEvaluationEvidence,
) -> AcceptanceReceipt:
    trust_root = _validated_trust_root(trust_root)
    if (
        not _capability_matches(
            trust_root,
            capability,
            LifecycleCapability,
            "_verify_lifecycle",
        )
        or not _lifecycle_receipt_valid(trust_root, canary, CanaryReceipt)
        or not _soak_evaluation_evidence_valid(trust_root, soak_evidence)
        or not trust_root._observation_consumed(
            soak_evidence.observation_receipt.receipt_id
        )
        or soak_evidence.candidate_id != canary.candidate_id
        or soak_evidence.predecessor_id != canary.receipt_id
        or soak_evidence.candidate_config != canary.deployment_evidence.candidate_config
        or soak_evidence.deployment_identity != canary.deployment_evidence.deployment_identity
        or soak_evidence.runtime_identity != canary.deployment_evidence.runtime_identity
        or soak_evidence.backend_epoch != canary.deployment_evidence.backend_epoch
        or soak_evidence.rollback_checkpoint_id
        != canary.deployment_evidence.rollback_checkpoint_id
    ):
        raise ContractError("acceptance_receipt_invalid")
    return _issue_lifecycle_receipt(
        capability,
        AcceptanceReceipt,
        candidate_id=canary.candidate_id,
        run_id=canary.run_id,
        evaluation_id=canary.evaluation_id,
        predecessor_id=canary.receipt_id,
        evidence=soak_evidence,
    )


def request_rollback_cleanup_observation(
    trust_root: CoordinatorTrustRoot,
    canary: CanaryReceipt,
) -> RollbackCleanupObservation:
    trust_root = _validated_trust_root(trust_root)
    if not _lifecycle_receipt_valid(trust_root, canary, CanaryReceipt):
        raise ContractError("rollback_cleanup_observation_invalid")
    deployment = canary.deployment_evidence
    request = {
        "schema": RUNTIME_OBSERVER_REQUEST_SCHEMA,
        "kind": RollbackCleanupObservation.KIND,
        "authorityId": trust_root.authority_id,
        "identityDigest": trust_root.identity_digest,
        "candidateId": canary.candidate_id,
        "runId": canary.run_id,
        "evaluationId": canary.evaluation_id,
        "predecessorId": canary.receipt_id,
        "expected": {
            "candidateConfig": deployment.candidate_config.to_dict(),
            "deployedRuntimeIdentity": deployment.runtime_identity,
            "deployedBackendEpoch": deployment.backend_epoch,
            "rollbackCheckpointId": deployment.rollback_checkpoint_id,
            "restoredRuntimeIdentity": deployment.previous_runtime_identity,
        },
    }
    try:
        receipt = _compile_runtime_observation(
            trust_root._request_runtime_observation(request),
            RollbackCleanupObservation,
        )
    except ContractError as exc:
        if exc.code == "runtime_observer_unavailable":
            raise
        raise ContractError("rollback_cleanup_observation_invalid") from None
    if (
        not _rollback_cleanup_observation_valid(trust_root, receipt)
        or receipt.authority_id != trust_root.authority_id
        or receipt.identity_digest != trust_root.identity_digest
        or receipt.candidate_id != canary.candidate_id
        or receipt.run_id != canary.run_id
        or receipt.evaluation_id != canary.evaluation_id
        or receipt.predecessor_id != canary.receipt_id
    ):
        raise ContractError("rollback_cleanup_observation_invalid")
    return receipt


def issue_rollback_cleanup_evidence(
    capability: EvaluatorCapability,
    trust_root: CoordinatorTrustRoot,
    canary: CanaryReceipt,
    observation: RollbackCleanupObservation,
) -> RollbackCleanupEvidence:
    trust_root = _validated_trust_root(trust_root)
    deployment = canary.deployment_evidence if isinstance(canary, CanaryReceipt) else None
    if (
        not _capability_matches(
            trust_root,
            capability,
            EvaluatorCapability,
            "_verify_evaluator",
        )
        or not _lifecycle_receipt_valid(trust_root, canary, CanaryReceipt)
        or not _rollback_cleanup_observation_valid(trust_root, observation)
        or observation.candidate_id != canary.candidate_id
        or observation.run_id != canary.run_id
        or observation.evaluation_id != canary.evaluation_id
        or observation.predecessor_id != canary.receipt_id
        or observation.candidate_config != deployment.candidate_config
        or observation.deployed_runtime_identity != deployment.runtime_identity
        or observation.deployed_backend_epoch != deployment.backend_epoch
        or observation.rollback_checkpoint_id != deployment.rollback_checkpoint_id
        or observation.restored_runtime_identity != deployment.previous_runtime_identity
        or observation.cleanup_proof_id == deployment.cleanup_proof_id
    ):
        raise ContractError("rollback_cleanup_evidence_invalid")
    evidence = RollbackCleanupEvidence(
        evidence_id="",
        authority_id=capability.authority_id,
        identity_digest=capability.identity_digest,
        candidate_id=canary.candidate_id,
        predecessor_id=canary.receipt_id,
        _signature="",
        candidate_config=observation.candidate_config,
        deployed_runtime_identity=observation.deployed_runtime_identity,
        deployed_backend_epoch=observation.deployed_backend_epoch,
        observation_window_id=observation.observation_window_id,
        failure_receipt_id=observation.failure_receipt_id,
        rollback_checkpoint_id=observation.rollback_checkpoint_id,
        restored_runtime_identity=observation.restored_runtime_identity,
        cleanup_proof_id=observation.cleanup_proof_id,
        remaining_processes=observation.remaining_processes,
        remaining_artifacts=observation.remaining_artifacts,
        health_receipt_id=observation.health_receipt_id,
        healthy=observation.healthy,
        error_receipt_id=observation.error_receipt_id,
        error_count=observation.error_count,
        observation_receipt=observation,
    )
    evidence = _sign_lifecycle_evidence(capability, evidence)
    if (
        not _rollback_cleanup_evidence_valid(trust_root, evidence)
        or not trust_root._consume_once("observer", observation.receipt_id)
    ):
        raise ContractError("rollback_cleanup_evidence_invalid")
    return evidence


def issue_rollback_receipt(
    capability: LifecycleCapability,
    trust_root: CoordinatorTrustRoot,
    canary: CanaryReceipt,
    cleanup_evidence: RollbackCleanupEvidence,
) -> RollbackReceipt:
    trust_root = _validated_trust_root(trust_root)
    if (
        not _capability_matches(
            trust_root,
            capability,
            LifecycleCapability,
            "_verify_lifecycle",
        )
        or not _lifecycle_receipt_valid(trust_root, canary, CanaryReceipt)
        or not _rollback_cleanup_evidence_valid(trust_root, cleanup_evidence)
        or not trust_root._observation_consumed(
            cleanup_evidence.observation_receipt.receipt_id
        )
        or cleanup_evidence.candidate_id != canary.candidate_id
        or cleanup_evidence.predecessor_id != canary.receipt_id
    ):
        raise ContractError("rollback_receipt_invalid")
    return _issue_lifecycle_receipt(
        capability,
        RollbackReceipt,
        candidate_id=canary.candidate_id,
        run_id=canary.run_id,
        evaluation_id=canary.evaluation_id,
        predecessor_id=canary.receipt_id,
        evidence=cleanup_evidence,
    )


class LatencyState(str, Enum):
    IDLE = "idle"
    SNAPSHOT = "snapshot"
    BASELINE_RUNNING = "baseline_running"
    CANDIDATE_READY = "candidate_ready"
    CANDIDATE_RUNNING = "candidate_running"
    EVALUATING = "evaluating"
    FEEDBACK_READY = "feedback_ready"
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    STAGED = "staged"
    CANARY = "canary"
    ACCEPTED = "accepted"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    CLEANUP_REQUIRED = "cleanup_required"


STATE_TRANSITIONS: Mapping[LatencyState, frozenset[LatencyState]] = MappingProxyType(
    {
        LatencyState.IDLE: frozenset({LatencyState.SNAPSHOT}),
        LatencyState.SNAPSHOT: frozenset({LatencyState.BASELINE_RUNNING, LatencyState.FAILED}),
        LatencyState.BASELINE_RUNNING: frozenset(
            {LatencyState.CANDIDATE_READY, LatencyState.FAILED, LatencyState.CLEANUP_REQUIRED}
        ),
        LatencyState.CANDIDATE_READY: frozenset(
            {LatencyState.CANDIDATE_RUNNING, LatencyState.FAILED, LatencyState.CLEANUP_REQUIRED}
        ),
        LatencyState.CANDIDATE_RUNNING: frozenset(
            {LatencyState.EVALUATING, LatencyState.FAILED, LatencyState.CLEANUP_REQUIRED}
        ),
        LatencyState.EVALUATING: frozenset(
            {LatencyState.FEEDBACK_READY, LatencyState.FAILED, LatencyState.CLEANUP_REQUIRED}
        ),
        LatencyState.FEEDBACK_READY: frozenset(
            {LatencyState.PROPOSED, LatencyState.AWAITING_APPROVAL, LatencyState.FAILED}
        ),
        LatencyState.PROPOSED: frozenset({LatencyState.CANDIDATE_READY, LatencyState.FAILED}),
        LatencyState.AWAITING_APPROVAL: frozenset(
            {LatencyState.STAGED}
        ),
        LatencyState.STAGED: frozenset(
            {LatencyState.CANARY}
        ),
        LatencyState.CANARY: frozenset(
            {
                LatencyState.ACCEPTED,
                LatencyState.ROLLED_BACK,
            }
        ),
        LatencyState.ACCEPTED: frozenset(),
        LatencyState.ROLLED_BACK: frozenset(),
        LatencyState.FAILED: frozenset(),
        LatencyState.CLEANUP_REQUIRED: frozenset(),
    }
)


def validate_state_transition(
    current: str | LatencyState,
    target: str | LatencyState,
    *,
    candidate_id: str | None = None,
    feedback: CandidateFeedback | None = None,
    promotion_evidence: PromotionEvidence | None = None,
    trust_root: CoordinatorTrustRoot | None = None,
    lifecycle_receipt: (
        ApprovalReceipt | CanaryReceipt | AcceptanceReceipt | RollbackReceipt | None
    ) = None,
    expected_run_id: str | None = None,
    expected_receipt_id: str | None = None,
    expected_cleanup_proof_id: str | None = None,
    expected_evaluation_id: str | None = None,
    expected_previous_receipt_id: str | None = None,
    expected_attempt: int | None = None,
) -> LatencyState:
    try:
        source_state = LatencyState(current)
        target_state = LatencyState(target)
    except (TypeError, ValueError):
        raise ContractError("state_invalid") from None
    if target_state not in STATE_TRANSITIONS[source_state]:
        raise ContractError("state_transition_invalid")
    if source_state is LatencyState.FEEDBACK_READY and target_state is LatencyState.AWAITING_APPROVAL:
        if (
            not isinstance(candidate_id, str)
            or CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None
            or not isinstance(feedback, CandidateFeedback)
        ):
            raise ContractError("state_transition_evidence_invalid")
        try:
            verified_feedback = compile_feedback(feedback.to_dict())
        except ContractError:
            raise ContractError("state_transition_evidence_invalid") from None
        if verified_feedback.verdict != "eligible" or verified_feedback.candidate_id != candidate_id:
            raise ContractError("state_transition_evidence_invalid")
        try:
            pinned_root = _validated_trust_root(trust_root)
        except ContractError:
            raise ContractError("state_transition_evidence_invalid") from None
        if (
            not _promotion_signature_valid(pinned_root, promotion_evidence)
            or promotion_evidence.candidate_id != candidate_id
            or promotion_evidence.feedback_digest != _feedback_digest(verified_feedback)
            or isinstance(expected_attempt, bool)
            or not isinstance(expected_attempt, int)
            or not 1 <= expected_attempt <= MAX_CANDIDATES
            or verified_feedback.attempt != expected_attempt
            or promotion_evidence.run_id != expected_run_id
            or promotion_evidence.receipt_id != expected_receipt_id
            or promotion_evidence.cleanup_proof_id != expected_cleanup_proof_id
            or promotion_evidence.evaluation_id != expected_evaluation_id
            or any(
                not isinstance(value, str) or CANDIDATE_ID_PATTERN.fullmatch(value) is None
                for value in (
                    expected_run_id,
                    expected_receipt_id,
                    expected_cleanup_proof_id,
                    expected_evaluation_id,
                )
            )
        ):
            raise ContractError("state_transition_evidence_invalid")
        if not pinned_root._start_lifecycle_campaign(
            candidate_id=candidate_id,
            run_id=expected_run_id,
            evaluation_id=expected_evaluation_id,
        ):
            raise ContractError("state_transition_evidence_invalid")
    lifecycle_type = {
        (LatencyState.AWAITING_APPROVAL, LatencyState.STAGED): ApprovalReceipt,
        (LatencyState.STAGED, LatencyState.CANARY): CanaryReceipt,
        (LatencyState.CANARY, LatencyState.ACCEPTED): AcceptanceReceipt,
        (LatencyState.CANARY, LatencyState.ROLLED_BACK): RollbackReceipt,
    }.get((source_state, target_state))
    if lifecycle_type is not None:
        try:
            pinned_root = _validated_trust_root(trust_root)
        except ContractError:
            raise ContractError("state_transition_evidence_invalid") from None
        expected_predecessor = (
            expected_evaluation_id
            if lifecycle_type is ApprovalReceipt
            else expected_previous_receipt_id
        )
        if (
            not _lifecycle_receipt_valid(pinned_root, lifecycle_receipt, lifecycle_type)
            or not isinstance(candidate_id, str)
            or CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None
            or lifecycle_receipt.candidate_id != candidate_id
            or lifecycle_receipt.run_id != expected_run_id
            or lifecycle_receipt.evaluation_id != expected_evaluation_id
            or lifecycle_receipt.predecessor_id != expected_predecessor
            or any(
                not isinstance(value, str) or CANDIDATE_ID_PATTERN.fullmatch(value) is None
                for value in (
                    expected_run_id,
                    expected_evaluation_id,
                    expected_predecessor,
                )
            )
            or not pinned_root._consume_lifecycle_transition(
                lifecycle_receipt.receipt_id,
                lifecycle_receipt.predecessor_id,
                candidate_id=candidate_id,
                run_id=expected_run_id,
                evaluation_id=expected_evaluation_id,
                source_state=source_state.value,
                target_state=target_state.value,
            )
        ):
            raise ContractError("state_transition_evidence_invalid")
    return target_state


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("json_duplicate_key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ContractError("json_invalid")


def parse_json_bytes(raw: bytes) -> Any:
    if len(raw) > MAX_INPUT_BYTES:
        raise ContractError("input_too_large")
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except ContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise ContractError("json_invalid") from None


def _read_input() -> bytes:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    except (AttributeError, OSError, ValueError):
        raise ContractError("input_unavailable") from None
    if len(raw) > MAX_INPUT_BYTES:
        raise ContractError("input_too_large")
    return raw


def self_test() -> None:
    identities = IdentitySet(*(f"sha256:{index:064x}" for index in range(1, 7)))
    trust_root, _, _, _ = _bootstrap_test_coordinator(identities)
    baseline = MainLatencyConfig(2048, 1024, 256, 8192, 1, 0)
    manifest = compile_candidate(
        candidate_proposal(identities, baseline, {"main.ubatch": 2048}),
        trust_root=trust_root,
    )
    assert CANDIDATE_ID_PATTERN.fullmatch(manifest.candidate_id)
    assert manifest.candidate_config.ubatch == 2048
    assert len(
        enumerate_next_candidates(identities, baseline, trust_root=trust_root)
    ) <= MAX_CANDIDATES
    assert validate_state_transition("idle", "snapshot") is LatencyState.SNAPSHOT
    try:
        compile_candidate(
            candidate_proposal(identities, baseline, {"main.cudaGraph": True}),
            trust_root=trust_root,
        )
    except ContractError as exc:
        assert exc.code == "change_value_invalid"
    else:
        raise AssertionError("bool candidate value accepted")


class _ContentFreeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        rendered = json.dumps(
            {"ok": False, "code": "arguments_invalid"},
            separators=(",", ":"),
        )
        self.exit(2, f"{rendered}\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = _ContentFreeArgumentParser(
        description="Self-test the fixed-coordinator-bound Main latency candidate contract."
    )
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        self_test()
        print("self-test: ok")
        return 0
    print(
        json.dumps(
            {"ok": False, "code": "coordinator_context_required"},
            separators=(",", ":"),
        ),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
