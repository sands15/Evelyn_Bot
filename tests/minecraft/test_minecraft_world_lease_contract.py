from __future__ import annotations

import json
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

from evelyn_core.minecraft_world_lease_contract import (  # noqa: E402
    MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
    MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA,
    MINECRAFT_WORLD_LEASE_OWNER_CONFLICT,
    MINECRAFT_WORLD_LEASE_PROOF_SCHEMA,
    MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
    MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
    MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
    build_world_lease_proof,
    load_guarded_world_lease,
    load_valid_world_lease,
    validate_world_lease_request,
)


class MinecraftWorldLeaseContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "status.json"
        self.secret_path = (
            Path(self.temp_dir.name) / "secret.json"
        )
        self.owner_claim_path = (
            Path(self.temp_dir.name) / "owner_claim.json"
        )
        self.now = 1000.0
        self.status = {
            "schema": MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
            "state": "authorized",
            "updatedAt": self.now,
            "processNonce": "process-1",
            "active": True,
            "auditReady": True,
            "statusReady": True,
            "lease": {
                "leaseId": "lease-1",
                "guildId": 7,
                "source": "discord_command",
                "issuedAt": self.now,
                "expiresAt": self.now + 60,
            },
        }
        self.path.write_text(
            json.dumps(self.status),
            encoding="utf-8",
        )
        self.secret_path.write_text(
            json.dumps(
                {
                    "schema": MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
                    "processNonce": "process-1",
                    "authorizationToken": "secret-1",
                    "leaseId": "lease-1",
                    "expiresMonotonic": 1e300,
                }
            ),
            encoding="utf-8",
        )
        self.write_owner_claim()

    def write_owner_claim(
        self,
        *,
        process_nonce: str = "process-1",
        schema: str = MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA,
    ) -> None:
        self.owner_claim_path.write_text(
            json.dumps(
                {
                    "schema": schema,
                    "processNonce": process_nonce,
                    "updatedAt": self.now,
                    "pid": 123,
                }
            ),
            encoding="utf-8",
        )

    def proof(self) -> dict:
        return build_world_lease_proof(
            self.status,
            authorization_token="secret-1",
        )

    def test_active_fresh_lease_and_exact_proof_pass(self) -> None:
        valid, error = validate_world_lease_request(
            {"worldLease": self.proof()},
            status_path=self.path,
            secret_path=self.secret_path,
            now=lambda: self.now + 5,
        )

        self.assertTrue(valid)
        self.assertEqual(error, "")
        self.assertEqual(
            self.proof()["schema"],
            MINECRAFT_WORLD_LEASE_PROOF_SCHEMA,
        )

        status, error = load_guarded_world_lease(
            self.path,
            self.secret_path,
            owner_claim_path=self.owner_claim_path,
            now=self.now + 5,
        )
        self.assertEqual(status, self.status)
        self.assertEqual(error, "")

    def test_guarded_load_rejects_private_monotonic_expiry(self) -> None:
        secret = json.loads(self.secret_path.read_text(encoding="utf-8"))
        secret["expiresMonotonic"] = 60.0
        self.secret_path.write_text(
            json.dumps(secret),
            encoding="utf-8",
        )

        status, error = load_guarded_world_lease(
            self.path,
            self.secret_path,
            owner_claim_path=self.owner_claim_path,
            now=self.now,
            monotonic_now=61.0,
        )

        self.assertEqual(status, {})
        self.assertEqual(error, "minecraft_world_lease_expired")

    def test_owner_claim_is_required_and_must_match_status_epoch(
        self,
    ) -> None:
        cases = (
            ("missing", None, None),
            ("corrupt", "{", None),
            (
                "wrong_schema",
                None,
                {
                    "schema": "wrong",
                    "processNonce": "process-1",
                },
            ),
            (
                "wrong_nonce",
                None,
                {
                    "schema": MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA,
                    "processNonce": "replacement-process",
                },
            ),
            (
                "non_string_nonce",
                None,
                {
                    "schema": MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA,
                    "processNonce": 123,
                },
            ),
            (
                "noncanonical_nonce_whitespace",
                None,
                {
                    "schema": MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA,
                    "processNonce": " process-1 ",
                },
            ),
        )
        for name, raw, payload in cases:
            with self.subTest(name=name):
                if name == "missing":
                    self.owner_claim_path.unlink(missing_ok=True)
                elif raw is not None:
                    self.owner_claim_path.write_text(
                        raw,
                        encoding="utf-8",
                    )
                else:
                    self.owner_claim_path.write_text(
                        json.dumps(payload),
                        encoding="utf-8",
                    )

                status, error = load_valid_world_lease(
                    self.path,
                    owner_claim_path=self.owner_claim_path,
                    now=self.now,
                )
                self.assertEqual(status, {})
                self.assertEqual(
                    error,
                    MINECRAFT_WORLD_LEASE_OWNER_CONFLICT,
                )
                self.write_owner_claim()

    def test_valid_load_rechecks_claim_at_final_snapshot_fence(
        self,
    ) -> None:
        with patch(
            "evelyn_core.minecraft_world_lease_contract."
            "_read_owner_claim_nonce",
            side_effect=("process-1", "replacement-process"),
        ):
            status, error = load_valid_world_lease(
                self.path,
                owner_claim_path=self.owner_claim_path,
                now=self.now,
            )

        self.assertEqual(status, {})
        self.assertEqual(
            error,
            MINECRAFT_WORLD_LEASE_OWNER_CONFLICT,
        )

    def test_guarded_load_rechecks_claim_after_secret_read(self) -> None:
        with patch(
            "evelyn_core.minecraft_world_lease_contract."
            "_read_owner_claim_nonce",
            side_effect=(
                "process-1",
                "process-1",
                "replacement-process",
            ),
        ):
            status, error = load_guarded_world_lease(
                self.path,
                self.secret_path,
                owner_claim_path=self.owner_claim_path,
                now=self.now,
            )

        self.assertEqual(status, {})
        self.assertEqual(
            error,
            MINECRAFT_WORLD_LEASE_OWNER_CONFLICT,
        )

    def test_request_rechecks_claim_after_secret_and_proof(self) -> None:
        with patch(
            "evelyn_core.minecraft_world_lease_contract."
            "_read_owner_claim_nonce",
            side_effect=(
                "process-1",
                "process-1",
                "replacement-process",
            ),
        ):
            valid, error = validate_world_lease_request(
                {"worldLease": self.proof()},
                status_path=self.path,
                secret_path=self.secret_path,
                owner_claim_path=self.owner_claim_path,
                now=lambda: self.now,
            )

        self.assertFalse(valid)
        self.assertEqual(
            error,
            MINECRAFT_WORLD_LEASE_OWNER_CONFLICT,
        )

    def test_missing_or_mismatched_proof_fails(self) -> None:
        valid, error = validate_world_lease_request(
            {},
            status_path=self.path,
            secret_path=self.secret_path,
            now=lambda: self.now,
        )
        self.assertFalse(valid)
        self.assertEqual(
            error,
            "minecraft_world_lease_proof_missing",
        )

        proof = self.proof()
        proof["leaseId"] = "stale"
        valid, error = validate_world_lease_request(
            {"worldLease": proof},
            status_path=self.path,
            secret_path=self.secret_path,
            now=lambda: self.now,
        )
        self.assertFalse(valid)
        self.assertEqual(
            error,
            "minecraft_world_lease_proof_mismatch",
        )

    def test_stale_heartbeat_fails_closed_before_expiry(self) -> None:
        valid, error = validate_world_lease_request(
            {"worldLease": self.proof()},
            status_path=self.path,
            secret_path=self.secret_path,
            now=lambda: self.now + 16,
        )

        self.assertFalse(valid)
        self.assertEqual(
            error,
            "minecraft_world_lease_heartbeat_stale",
        )

    def test_missing_or_false_audit_readiness_fails_closed(self) -> None:
        for audit_ready in (None, False, "true", 1):
            status = dict(self.status)
            if audit_ready is None:
                status.pop("auditReady", None)
            else:
                status["auditReady"] = audit_ready
            self.path.write_text(
                json.dumps(status),
                encoding="utf-8",
            )

            valid, error = validate_world_lease_request(
                {"worldLease": self.proof()},
                status_path=self.path,
                secret_path=self.secret_path,
                now=lambda: self.now,
            )

            with self.subTest(audit_ready=audit_ready):
                self.assertFalse(valid)
                self.assertEqual(
                    error,
                    MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
                )

    def test_missing_or_false_status_readiness_fails_closed(self) -> None:
        for status_ready in (None, False, "true", 1):
            status = dict(self.status)
            if status_ready is None:
                status.pop("statusReady", None)
            else:
                status["statusReady"] = status_ready
            self.path.write_text(
                json.dumps(status),
                encoding="utf-8",
            )

            valid, error = validate_world_lease_request(
                {"worldLease": self.proof()},
                status_path=self.path,
                secret_path=self.secret_path,
                now=lambda: self.now,
            )

            with self.subTest(status_ready=status_ready):
                self.assertFalse(valid)
                self.assertEqual(
                    error,
                    MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
                )

    def test_missing_or_wrong_secret_fails(self) -> None:
        self.secret_path.unlink()
        valid, error = validate_world_lease_request(
            {"worldLease": self.proof()},
            status_path=self.path,
            secret_path=self.secret_path,
            now=lambda: self.now,
        )
        self.assertFalse(valid)
        self.assertEqual(
            error,
            "minecraft_world_lease_secret_missing",
        )

        self.secret_path.write_text(
            json.dumps(
                {
                    "schema": MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
                    "processNonce": "process-1",
                    "authorizationToken": "different",
                }
            ),
            encoding="utf-8",
        )
        valid, error = validate_world_lease_request(
            {"worldLease": self.proof()},
            status_path=self.path,
            secret_path=self.secret_path,
            now=lambda: self.now,
        )
        self.assertFalse(valid)
        self.assertEqual(
            error,
            "minecraft_world_lease_secret_mismatch",
        )

        self.secret_path.write_text(
            json.dumps(
                {
                    "schema": MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
                    "processNonce": "stale-process",
                    "authorizationToken": "different",
                }
            ),
            encoding="utf-8",
        )
        status, error = load_guarded_world_lease(
            self.path,
            self.secret_path,
            now=self.now,
        )
        self.assertEqual(status, {})
        self.assertEqual(
            error,
            "minecraft_world_lease_secret_mismatch",
        )

    def test_missing_corrupt_and_expired_status_fail_closed(self) -> None:
        self.path.unlink()
        status, error = load_valid_world_lease(
            self.path,
            now=self.now,
        )
        self.assertEqual(status, {})
        self.assertEqual(
            error,
            "minecraft_world_lease_status_missing",
        )

        self.path.write_text("{", encoding="utf-8")
        status, error = load_valid_world_lease(
            self.path,
            now=self.now,
        )
        self.assertEqual(status, {})
        self.assertEqual(
            error,
            "minecraft_world_lease_status_missing",
        )

        expired = dict(self.status)
        expired["lease"] = dict(self.status["lease"])
        expired["lease"]["expiresAt"] = self.now - 1
        self.path.write_text(
            json.dumps(expired),
            encoding="utf-8",
        )
        status, error = load_valid_world_lease(
            self.path,
            now=self.now,
        )
        self.assertEqual(status, {})
        self.assertEqual(
            error,
            "minecraft_world_lease_expired",
        )


if __name__ == "__main__":
    unittest.main()
