from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import voice_capture_consent as consent_module  # noqa: E402
from evelyn_core.voice_capture_consent import (  # noqa: E402
    BRIDGE_STATUS_AUTH_SCOPE,
    CONSENT_SCHEMA,
    HOST_LEASE_AUTH_SCOPE,
    HOST_LEASE_MAX_BYTES,
    HOST_LEASE_SCHEMA,
    VOICE_CAPTURE_AUTH_ENV,
    VoiceCaptureConsentManager,
    attach_voice_capture_consent,
    inspect_voice_capture_host_lease,
    sign_voice_capture_artifact,
    voice_capture_auth_scrubbed_environment,
    voice_capture_artifact_is_authentic,
    voice_capture_consent_fence_matches,
)
from evelyn_core.voice_capture_consent_claim_lease import (  # noqa: E402
    VoiceCaptureConsentClaimLeaseTimeout,
    acquire_voice_capture_consent_claim_lease,
)


class Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class VoiceCaptureConsentTests(unittest.TestCase):
    def setUp(self):
        self.auth_token = "voice-capture-test-auth-token-0123456789"
        auth_patch = patch.dict(
            consent_module.os.environ,
            {VOICE_CAPTURE_AUTH_ENV: self.auth_token},
        )
        auth_patch.start()
        self.addCleanup(auth_patch.stop)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.clock = Clock()
        self.manager = self.make_manager(self.root)
        initial = self.manager.status()
        self.assertEqual(initial["state"], "revoking")
        self.assertEqual(initial["loadState"], "missing")
        self.assertTrue(initial["recoveryRequired"])
        self.acknowledge_recovery(
            self.manager,
            expected_reason="consent_state_missing",
        )

    def make_manager(
        self,
        root: Path,
        *,
        owner_nonce: str = "process-a",
        preview_ttl_sec: float = 120,
        armed_ttl_sec: float = 300,
        active_ttl_sec: float = 1800,
    ) -> VoiceCaptureConsentManager:
        return VoiceCaptureConsentManager(
            root=root,
            now=self.clock,
            owner_nonce=owner_nonce,
            preview_ttl_sec=preview_ttl_sec,
            armed_ttl_sec=armed_ttl_sec,
            active_ttl_sec=active_ttl_sec,
        )

    def acknowledge_recovery(
        self,
        manager: VoiceCaptureConsentManager,
        *,
        expected_reason: str,
    ) -> None:
        self.assertEqual(manager.status()["state"], "revoking")
        self.assertEqual(manager.revocation_reason(), expected_reason)
        pending = manager.begin_revoke(reason=expected_reason)
        self.assertTrue(pending["controlRequired"])
        completed = manager.finish_revoke(applied=True)
        self.assertTrue(completed["ok"])
        self.assertEqual(completed["consent"]["state"], "inactive")
        self.assertFalse(completed["consent"]["recoveryRequired"])

    def inactive_payload(self, *, owner_nonce: str = "process-a") -> dict:
        return {
            "schema": CONSENT_SCHEMA,
            "state": "inactive",
            "scope": "voice_validation_local",
            "ownerNonce": owner_nonce,
            "leaseId": "",
            "validationSessionId": "",
            "requestedAt": None,
            "activatedAt": None,
            "expiresAt": None,
            "updatedAt": self.clock(),
            "lastError": "",
            "lastRevocationReason": "",
            "revokedAt": None,
        }

    @staticmethod
    def write_state(root: Path, payload) -> Path:
        path = root / "voice_capture_consent" / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def assert_recovery_blocks_consent(
        self,
        manager: VoiceCaptureConsentManager,
        *,
        load_state: str,
        reason: str,
    ) -> None:
        status = manager.status()
        self.assertEqual(status["state"], "revoking")
        self.assertEqual(status["loadState"], load_state)
        self.assertTrue(status["recoveryRequired"])
        self.assertFalse(status["active"])
        self.assertEqual(manager.revocation_reason(), reason)
        preview = manager.preview()
        self.assertFalse(preview["ok"])
        self.assertEqual(
            preview["error"],
            "voice_capture_consent_recovery_required",
        )

        token = "test-recovery-confirm-token"
        manager._previews[token] = {
            "scope": "voice_validation_local",
            "issuedAt": self.clock(),
            "expiresAt": self.clock() + 30,
            "used": False,
        }
        applied = manager.begin_apply(confirm_token=token)
        self.assertFalse(applied["ok"])
        self.assertEqual(
            applied["error"],
            "voice_capture_consent_recovery_required",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def activate(self):
        preview = self.manager.preview()
        started = self.manager.begin_apply(confirm_token=preview["confirmToken"])
        completed = self.manager.finish_apply(
            lease_id=started["leaseId"],
            applied=True,
            capture_ready=True,
        )
        self.assertTrue(completed["ok"])
        return completed["consent"]

    def test_auth_environment_scrub_removes_only_voice_capture_key(self):
        with patch.dict(
            consent_module.os.environ,
            {VOICE_CAPTURE_AUTH_ENV: "secret", "EVELYN_KEEP_ME": "kept"},
            clear=True,
        ):
            environment = voice_capture_auth_scrubbed_environment()

        self.assertEqual(environment, {"EVELYN_KEEP_ME": "kept"})

    def test_preview_is_short_lived_and_one_time(self):
        preview = self.manager.preview()
        first = self.manager.begin_apply(confirm_token=preview["confirmToken"])
        second = self.manager.begin_apply(confirm_token=preview["confirmToken"])

        self.assertTrue(first["ok"])
        self.assertEqual(second["error"], "voice_capture_confirm_token_reused")

        other = self.make_manager(
            self.root / "expired",
            preview_ttl_sec=2,
        )
        self.acknowledge_recovery(
            other,
            expected_reason="consent_state_missing",
        )
        expired_preview = other.preview()
        self.clock.value += 3
        expired = other.begin_apply(confirm_token=expired_preview["confirmToken"])
        self.assertEqual(expired["error"], "voice_capture_confirm_token_expired")

    def test_durable_revoke_fences_memory_before_claim_lease_commit(self):
        self.activate()
        lease = acquire_voice_capture_consent_claim_lease(root=self.root)
        started = threading.Event()
        completed = threading.Event()
        result = []

        def revoke() -> None:
            started.set()
            result.append(self.manager.begin_revoke(reason="user_revoked"))
            completed.set()

        worker = threading.Thread(target=revoke)
        worker.start()
        try:
            self.assertTrue(started.wait(timeout=2))
            self.assertFalse(completed.wait(timeout=0.1))
            self.assertEqual(
                self.manager._state["state"],  # noqa: SLF001
                "revoking",
            )
        finally:
            lease.release()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(completed.is_set())
        self.assertTrue(result[0]["controlRequired"])
        self.assertEqual(self.manager.status()["state"], "revoking")

    def test_revoke_timeout_stays_memory_first_until_retry(self):
        self.activate()
        before_disk = json.loads(
            self.manager.state_path.read_text(encoding="utf-8")
        )
        lease = acquire_voice_capture_consent_claim_lease(root=self.root)
        try:
            with (
                patch.object(
                    consent_module,
                    "DEFAULT_BLOCKING_TIMEOUT_SEC",
                    0.02,
                ),
                self.assertRaisesRegex(
                    VoiceCaptureConsentClaimLeaseTimeout,
                    "^voice_capture_consent_claim_lease_timeout$",
                ),
            ):
                self.manager.begin_revoke(reason="user_revoked")
        finally:
            lease.release()

        self.assert_recovery_blocks_consent(
            self.manager,
            load_state="untrusted",
            reason="user_revoked",
        )
        self.assertEqual(before_disk["state"], "active")
        self.assertEqual(
            json.loads(
                self.manager.state_path.read_text(encoding="utf-8")
            )["state"],
            "active",
        )

        retried = self.manager.begin_revoke(reason="user_revoked")
        self.assertTrue(retried["controlRequired"])
        self.assertEqual(retried["consent"]["loadState"], "verified")

    def test_revoke_write_failure_stays_memory_first_until_retry(self):
        self.activate()
        with patch.object(
            consent_module,
            "atomic_json_write",
            side_effect=OSError("consent store unavailable"),
        ):
            with self.assertRaisesRegex(OSError, "store unavailable"):
                self.manager.begin_revoke(reason="user_revoked")

        self.assert_recovery_blocks_consent(
            self.manager,
            load_state="untrusted",
            reason="user_revoked",
        )
        retried = self.manager.begin_revoke(reason="user_revoked")
        self.assertTrue(retried["controlRequired"])
        self.assertEqual(retried["consent"]["loadState"], "verified")

    def test_only_latest_preview_can_authorize_capture(self):
        older = self.manager.preview()
        latest = self.manager.preview()

        stale = self.manager.begin_apply(
            confirm_token=older["confirmToken"]
        )
        current = self.manager.begin_apply(
            confirm_token=latest["confirmToken"]
        )

        self.assertFalse(stale["ok"])
        self.assertEqual(
            stale["error"],
            "voice_capture_confirm_token_invalid",
        )
        self.assertTrue(current["ok"], current)

    def test_revoke_invalidates_an_unconsumed_preview(self):
        preview = self.manager.preview()

        revoked = self.manager.begin_revoke(reason="user_revoked")
        late_apply = self.manager.begin_apply(
            confirm_token=preview["confirmToken"]
        )

        self.assertTrue(revoked["ok"], revoked)
        self.assertFalse(revoked["controlRequired"])
        self.assertFalse(late_apply["ok"])
        self.assertEqual(
            late_apply["error"],
            "voice_capture_confirm_token_reused",
        )

    def test_preview_is_bound_to_the_validation_generation(self):
        idle_binding = {
            "schema": "voice.capture-consent.validation-binding.v1",
            "sessionId": "",
            "state": "idle",
            "usesLocal": False,
        }
        running_binding = {
            "schema": "voice.capture-consent.validation-binding.v1",
            "sessionId": "voice-p0-new-session",
            "state": "running",
            "usesLocal": True,
        }
        preview = self.manager.preview(
            validation_binding=idle_binding
        )

        stale = self.manager.begin_apply(
            confirm_token=preview["confirmToken"],
            validation_binding=running_binding,
        )

        self.assertFalse(stale["ok"])
        self.assertEqual(
            stale["error"],
            "voice_capture_confirm_token_stale",
        )

    def test_discord_only_validation_cannot_preview_local_capture(self):
        result = self.manager.preview(
            validation_binding={
                "schema": "voice.capture-consent.validation-binding.v1",
                "sessionId": "voice-p0-discord",
                "state": "running",
                "usesLocal": False,
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "voice_capture_validation_context_not_allowed",
        )

    def test_active_lease_binds_to_validation_and_expires_on_terminal_state(self):
        consent = self.activate()
        self.assertTrue(consent["active"])
        self.assertLessEqual(consent["remainingSec"], 300)

        bound = self.manager.bind_validation_session("session-1")
        self.assertTrue(bound["ok"])
        self.assertEqual(bound["consent"]["validationSessionId"], "session-1")
        self.assertEqual(
            self.manager.revocation_reason(
                validation_session={"sessionId": "session-1", "state": "running"}
            ),
            "",
        )
        self.assertEqual(
            self.manager.revocation_reason(
                validation_session={"sessionId": "session-1", "state": "passed"}
            ),
            "validation_session_passed",
        )

    def test_unbound_lease_only_survives_an_exact_idle_validation(self):
        self.activate()

        self.assertEqual(
            self.manager.revocation_reason(
                validation_session={"sessionId": "", "state": "idle"}
            ),
            "",
        )
        self.assertEqual(
            self.manager.revocation_reason(
                validation_session={
                    "sessionId": "discord-session",
                    "state": "running",
                }
            ),
            "validation_session_started_before_consent_binding",
        )

    def test_bound_lease_fails_closed_when_validation_identity_is_lost(self):
        self.activate()
        self.manager.bind_validation_session("local-session")

        self.assertEqual(
            self.manager.revocation_reason(
                validation_session={"sessionId": "", "state": "idle"}
            ),
            "validation_session_replaced",
        )
        self.assertEqual(
            self.manager.revocation_reason(
                validation_session={
                    "sessionId": "local-session",
                    "state": "preflight",
                }
            ),
            "validation_session_state_invalid",
        )

    def test_unbound_lease_expires_early_and_bound_lease_gets_full_budget(self):
        consent = self.activate()
        self.clock.value = float(consent["expiresAt"]) + 0.01
        self.assertEqual(self.manager.revocation_reason(), "consent_expired")

        second = self.make_manager(
            self.root / "bound",
            armed_ttl_sec=300,
            active_ttl_sec=1800,
        )
        self.acknowledge_recovery(
            second,
            expected_reason="consent_state_missing",
        )
        preview = second.preview()
        started = second.begin_apply(confirm_token=preview["confirmToken"])
        second.finish_apply(
            lease_id=started["leaseId"],
            applied=True,
            capture_ready=True,
        )
        bound = second.bind_validation_session("session-2")
        self.assertGreater(bound["consent"]["remainingSec"], 300)

    def test_new_process_owner_requires_fail_closed_revoke(self):
        self.activate()
        restarted = VoiceCaptureConsentManager(
            root=self.root,
            now=self.clock,
            owner_nonce="process-b",
        )

        self.assertEqual(
            restarted.revocation_reason(
                validation_session={"sessionId": "", "state": "idle"}
            ),
            "control_page_restarted",
        )
        pending = restarted.begin_revoke(reason="control_page_restarted")
        self.assertTrue(pending["controlRequired"])
        completed = restarted.finish_revoke(applied=True)
        self.assertTrue(completed["ok"])
        self.assertFalse(completed["consent"]["active"])

    def test_activation_failure_remains_revoking_until_off_ack(self):
        preview = self.manager.preview()
        started = self.manager.begin_apply(confirm_token=preview["confirmToken"])
        failed = self.manager.finish_apply(
            lease_id=started["leaseId"],
            applied=False,
            capture_ready=False,
            error="mic_control_ack_timeout",
        )

        self.assertFalse(failed["ok"])
        self.assertEqual(failed["consent"]["state"], "revoking")
        self.assertFalse(self.manager.finish_revoke(applied=False)["ok"])
        self.assertEqual(self.manager.status()["state"], "revoking")
        self.assertTrue(self.manager.finish_revoke(applied=True)["ok"])
        self.assertEqual(self.manager.status()["state"], "inactive")

    def test_activation_failure_write_error_stays_memory_first(self):
        preview = self.manager.preview()
        started = self.manager.begin_apply(confirm_token=preview["confirmToken"])
        with patch.object(
            consent_module,
            "atomic_json_write",
            side_effect=OSError("consent store unavailable"),
        ):
            with self.assertRaisesRegex(OSError, "store unavailable"):
                self.manager.finish_apply(
                    lease_id=started["leaseId"],
                    applied=False,
                    capture_ready=False,
                    error="mic_control_ack_timeout",
                )

        self.assert_recovery_blocks_consent(
            self.manager,
            load_state="untrusted",
            reason="activation_failed",
        )
        self.assertEqual(
            json.loads(self.manager.state_path.read_text(encoding="utf-8"))[
                "state"
            ],
            "enabling",
        )

    def test_missing_state_requires_off_ack_before_inactive(self):
        manager = self.make_manager(self.root / "missing-state")
        self.assert_recovery_blocks_consent(
            manager,
            load_state="missing",
            reason="consent_state_missing",
        )

        pending = manager.begin_revoke(reason=manager.revocation_reason())
        self.assertTrue(pending["controlRequired"])
        self.assertEqual(manager.status()["state"], "revoking")
        completed = manager.finish_revoke(applied=True)
        self.assertTrue(completed["ok"])
        self.assertEqual(completed["consent"]["state"], "inactive")
        self.assertFalse(completed["consent"]["recoveryRequired"])

    def test_untrusted_state_shapes_require_recovery_and_block_apply(self):
        valid = self.inactive_payload()
        wrong_schema = dict(valid, schema="voice.capture-consent.v0")
        extra_key = dict(valid, unexpected="private-canary")
        unknown_state = dict(valid, state="maybe-active")
        nonfinite_timestamp = dict(valid, updatedAt=float("nan"))
        cases = {
            "truncated": '{"schema":',
            "wrong_schema": wrong_schema,
            "non_dict": [],
            "extra_key": extra_key,
            "unknown_state": unknown_state,
            "nonfinite_timestamp": nonfinite_timestamp,
        }

        for name, payload in cases.items():
            with self.subTest(case=name):
                root = self.root / f"untrusted-{name}"
                self.write_state(root, payload)
                manager = self.make_manager(root)
                self.assert_recovery_blocks_consent(
                    manager,
                    load_state="untrusted",
                    reason="consent_state_untrusted",
                )

    def test_invalid_utf8_state_requires_recovery(self):
        root = self.root / "untrusted-invalid-utf8"
        state_path = root / "voice_capture_consent" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_bytes(b"\xff\xfe\x80")

        manager = self.make_manager(root)

        self.assert_recovery_blocks_consent(
            manager,
            load_state="untrusted",
            reason="consent_state_untrusted",
        )

    def test_excessively_nested_json_state_requires_recovery(self):
        root = self.root / "untrusted-recursive-json"
        self.write_state(root, "[" * 2_000 + "0" + "]" * 2_000)

        manager = self.make_manager(root)

        self.assert_recovery_blocks_consent(
            manager,
            load_state="untrusted",
            reason="consent_state_untrusted",
        )

    def test_symlink_state_is_untrusted(self):
        root = self.root / "symlink-state"
        state_path = root / "voice_capture_consent" / "state.json"
        target = root / "outside-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.inactive_payload()),
            encoding="utf-8",
        )
        try:
            state_path.symlink_to(target)
            manager = self.make_manager(root)
        except OSError:
            state_path.unlink(missing_ok=True)
            state_path.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
            original_is_symlink = Path.is_symlink

            def report_test_state_as_symlink(path: Path) -> bool:
                if Path(path) == state_path:
                    return True
                return original_is_symlink(path)

            with patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=report_test_state_as_symlink,
            ):
                manager = self.make_manager(root)

        self.assert_recovery_blocks_consent(
            manager,
            load_state="untrusted",
            reason="consent_state_untrusted",
        )

    def test_persisted_enabling_recovers_as_activation_interrupted(self):
        root = self.root / "interrupted-enabling"
        manager = self.make_manager(root)
        self.acknowledge_recovery(
            manager,
            expected_reason="consent_state_missing",
        )
        preview = manager.preview()
        started = manager.begin_apply(confirm_token=preview["confirmToken"])
        self.assertTrue(started["ok"])
        self.assertEqual(manager.status()["state"], "enabling")

        restarted = self.make_manager(root)
        self.assert_recovery_blocks_consent(
            restarted,
            load_state="verified",
            reason="activation_interrupted",
        )
        self.acknowledge_recovery(
            restarted,
            expected_reason="activation_interrupted",
        )

    def test_failed_off_ack_remains_durable_revoking_until_retry(self):
        root = self.root / "off-ack-failed"
        manager = self.make_manager(root)
        self.assert_recovery_blocks_consent(
            manager,
            load_state="missing",
            reason="consent_state_missing",
        )
        pending = manager.begin_revoke(reason=manager.revocation_reason())
        self.assertTrue(pending["controlRequired"])
        failed = manager.finish_revoke(
            applied=False,
            error="mic_control_ack_timeout",
        )
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["consent"]["state"], "revoking")
        self.assertTrue(failed["consent"]["recoveryRequired"])

        persisted = json.loads(manager.state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["state"], "revoking")
        self.assertEqual(persisted["lastError"], "mic_control_ack_timeout")
        restarted = self.make_manager(root)
        self.assertEqual(restarted.status()["state"], "revoking")
        self.assertEqual(restarted.revocation_reason(), "consent_state_missing")
        self.assertTrue(restarted.finish_revoke(applied=True)["ok"])
        self.assertEqual(restarted.status()["state"], "inactive")

    def test_state_transitions_use_durable_runtime_artifact_writer(self):
        real_writer = consent_module.atomic_json_write
        preview = self.manager.preview()
        with patch.object(
            consent_module,
            "atomic_json_write",
            wraps=real_writer,
        ) as writer:
            started = self.manager.begin_apply(
                confirm_token=preview["confirmToken"]
            )

        self.assertTrue(started["ok"])
        writer.assert_called_once()
        self.assertIs(writer.call_args.kwargs.get("durable"), True)

    def test_write_failures_do_not_publish_uncommitted_state(self):
        before = self.manager.status()
        before_disk = self.manager.state_path.read_text(encoding="utf-8")
        preview = self.manager.preview()
        with patch.object(
            consent_module,
            "atomic_json_write",
            side_effect=OSError("consent store unavailable"),
        ):
            with self.assertRaises(OSError):
                self.manager.begin_apply(
                    confirm_token=preview["confirmToken"]
                )
        self.assertEqual(self.manager.status(), before)
        self.assertEqual(
            self.manager.state_path.read_text(encoding="utf-8"),
            before_disk,
        )

        retry_preview = self.manager.preview()
        started = self.manager.begin_apply(
            confirm_token=retry_preview["confirmToken"]
        )
        enabling = self.manager.status()
        enabling_disk = self.manager.state_path.read_text(encoding="utf-8")
        with patch.object(
            consent_module,
            "atomic_json_write",
            side_effect=OSError("consent store unavailable"),
        ):
            with self.assertRaises(OSError):
                self.manager.finish_apply(
                    lease_id=started["leaseId"],
                    applied=True,
                    capture_ready=True,
                )
        self.assertEqual(self.manager.status(), enabling)
        self.assertEqual(
            self.manager.state_path.read_text(encoding="utf-8"),
            enabling_disk,
        )

    def test_persisted_state_is_content_free(self):
        self.activate()
        payload = json.loads(self.manager.state_path.read_text(encoding="utf-8"))
        serialized = json.dumps(payload, ensure_ascii=False).lower()

        self.assertEqual(payload["schema"], CONSENT_SCHEMA)
        self.assertNotIn("audio", serialized)
        self.assertNotIn("transcript", serialized)
        self.assertNotIn("prompt", serialized)
        self.assertNotIn("text", serialized)

    def test_host_lease_heartbeat_is_content_free_and_never_extends_expiry(self):
        consent = self.activate()
        first = self.manager.publish_host_lease()
        self.clock.value += 1
        second = self.manager.publish_host_lease()
        serialized = json.dumps(second, ensure_ascii=False).lower()

        self.assertEqual(second["schema"], HOST_LEASE_SCHEMA)
        self.assertEqual(second["expiresAt"], consent["expiresAt"])
        self.assertGreater(second["heartbeatAt"], first["heartbeatAt"])
        self.assertRegex(second["ownerDigest"], r"^[0-9a-f]{64}$")
        self.assertRegex(second["leaseDigest"], r"^[0-9a-f]{64}$")
        self.assertEqual(second["authAlgorithm"], "hmac-sha256")
        self.assertRegex(second["authTag"], r"^[0-9a-f]{64}$")
        self.assertNotIn(self.manager.owner_nonce, serialized)
        self.assertNotIn(str(consent["leaseId"]), serialized)
        self.assertNotIn(self.auth_token.lower(), serialized)
        for forbidden in ("audio", "transcript", "prompt", "text"):
            self.assertNotIn(forbidden, serialized)

    def test_host_lease_fence_is_stable_across_heartbeats_and_durable(self):
        self.activate()
        with patch.object(
            consent_module,
            "atomic_json_write",
            wraps=consent_module.atomic_json_write,
        ) as atomic_write:
            first = self.manager.publish_host_lease()
            first_inspection = inspect_voice_capture_host_lease(
                self.manager.host_lease_path,
                now=self.clock,
            )
            self.clock.value += 1
            second = self.manager.publish_host_lease()
            second_inspection = inspect_voice_capture_host_lease(
                self.manager.host_lease_path,
                now=self.clock,
            )

        self.assertNotEqual(first["authTag"], second["authTag"])
        self.assertEqual(
            first_inspection["fenceDigest"],
            second_inspection["fenceDigest"],
        )
        self.assertRegex(first_inspection["fenceDigest"], r"^[0-9a-f]{64}$")
        self.assertTrue(
            voice_capture_consent_fence_matches(
                self.manager.host_lease_path,
                self.manager.state_path,
                expected_digest=first_inspection["fenceDigest"],
                now=self.clock,
            )
        )
        host_writes = [
            call
            for call in atomic_write.call_args_list
            if call.args and call.args[0] == self.manager.host_lease_path
        ]
        self.assertEqual(len(host_writes), 2)
        self.assertTrue(
            all(call.kwargs.get("durable") is True for call in host_writes)
        )

    def test_fence_matcher_closes_revoke_publish_failure_window(self):
        self.activate()
        self.manager.publish_host_lease()
        inspected = inspect_voice_capture_host_lease(
            self.manager.host_lease_path,
            now=self.clock,
        )
        expected_digest = inspected["fenceDigest"]

        self.assertTrue(
            voice_capture_consent_fence_matches(
                self.manager.host_lease_path,
                self.manager.state_path,
                expected_digest=expected_digest,
                now=self.clock,
            )
        )

        self.manager.begin_revoke(reason="user_revoked")

        # Simulate a failed host-lease publish: the old signed active lease is
        # still fresh, but the durable consent state is already revoking.
        self.assertTrue(
            inspect_voice_capture_host_lease(
                self.manager.host_lease_path,
                now=self.clock,
            )["authorized"]
        )
        self.assertFalse(
            voice_capture_consent_fence_matches(
                self.manager.host_lease_path,
                self.manager.state_path,
                expected_digest=expected_digest,
                now=self.clock,
            )
        )

    def test_fence_matcher_fails_closed_for_bad_digest_or_state(self):
        self.activate()
        self.manager.publish_host_lease()
        expected_digest = inspect_voice_capture_host_lease(
            self.manager.host_lease_path,
            now=self.clock,
        )["fenceDigest"]

        self.assertFalse(
            voice_capture_consent_fence_matches(
                self.manager.host_lease_path,
                self.manager.state_path,
                expected_digest="0" * 64,
                now=self.clock,
            )
        )
        self.manager.state_path.write_text("{", encoding="utf-8")
        self.assertFalse(
            voice_capture_consent_fence_matches(
                self.manager.host_lease_path,
                self.manager.state_path,
                expected_digest=expected_digest,
                now=self.clock,
            )
        )

    def test_host_lease_inspection_fails_closed_for_untrusted_or_stale_files(self):
        self.activate()
        valid = self.manager.publish_host_lease()
        path = self.manager.host_lease_path

        self.assertTrue(
            inspect_voice_capture_host_lease(path, now=self.clock)["authorized"]
        )
        self.assertFalse(
            voice_capture_artifact_is_authentic(
                valid,
                auth_scope=BRIDGE_STATUS_AUTH_SCOPE,
                auth_token=self.auth_token,
            )
        )
        self.assertEqual(
            inspect_voice_capture_host_lease(
                path,
                now=self.clock,
                auth_token="wrong-voice-capture-auth-token-012345",
            )["reason"],
            "voice_capture_consent_heartbeat_untrusted",
        )

        path.write_text(
            json.dumps({**valid, "heartbeatAt": self.clock() - 1}),
            encoding="utf-8",
        )
        self.assertEqual(
            inspect_voice_capture_host_lease(path, now=self.clock)["reason"],
            "voice_capture_consent_heartbeat_untrusted",
        )

        cases = {
            "wrong_schema": {**valid, "schema": "voice.capture-consent.host-lease.v0"},
            "extra_key": {**valid, "private": "canary"},
            "future": {**valid, "heartbeatAt": self.clock() + 1},
            "stale": {**valid, "heartbeatAt": self.clock() - 5},
            "expired": {**valid, "expiresAt": self.clock()},
            "nonfinite": {**valid, "heartbeatAt": float("nan")},
            "inactive": {
                **valid,
                "state": "inactive",
                "leaseDigest": "",
                "expiresAt": None,
            },
            "revoking": {**valid, "state": "revoking"},
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                path.write_text(json.dumps(payload), encoding="utf-8")
                result = inspect_voice_capture_host_lease(path, now=self.clock)
                self.assertFalse(result["authorized"])
                self.assertEqual(result["fenceDigest"], "")
                self.assertTrue(result["reason"].startswith("voice_capture_consent_"))

        path.write_text("{", encoding="utf-8")
        self.assertFalse(
            inspect_voice_capture_host_lease(path, now=self.clock)["authorized"]
        )
        path.unlink()
        self.assertEqual(
            inspect_voice_capture_host_lease(path, now=self.clock)["reason"],
            "voice_capture_consent_heartbeat_missing",
        )

    def test_host_lease_uses_time_sampled_after_atomic_read(self):
        self.activate()
        refreshed = self.manager.publish_host_lease()
        path = self.manager.host_lease_path

        def replace_during_read(_path: Path, mode: str):
            self.assertEqual(mode, "rb")
            self.clock.value += 1
            return io.BytesIO(json.dumps(
                sign_voice_capture_artifact(
                    {**refreshed, "heartbeatAt": self.clock()},
                    auth_scope=HOST_LEASE_AUTH_SCOPE,
                    auth_token=self.auth_token,
                ),
            ).encode("utf-8"))

        with patch.object(
            Path,
            "open",
            autospec=True,
            side_effect=replace_during_read,
        ):
            result = inspect_voice_capture_host_lease(path, now=self.clock)

        self.assertTrue(result["authorized"], result)
        self.assertEqual(result["heartbeatAt"], result["checkedAt"])

    def test_host_lease_reader_rejects_oversized_untrusted_artifact(self):
        path = self.manager.host_lease_path
        payload = self.manager.publish_host_lease()
        payload["authTag"] = "a" * HOST_LEASE_MAX_BYTES
        path.write_text(json.dumps(payload), encoding="utf-8")

        result = inspect_voice_capture_host_lease(path, now=self.clock)

        self.assertFalse(result["authorized"])
        self.assertEqual(
            result["reason"],
            "voice_capture_consent_heartbeat_untrusted",
        )

    def test_host_lease_publish_requires_purpose_scoped_auth_token(self):
        self.manager.auth_token = ""

        with self.assertRaisesRegex(
            RuntimeError,
            "voice_capture_auth_token_unavailable",
        ):
            self.manager.publish_host_lease()

    def test_host_lease_symlink_is_untrusted(self):
        self.activate()
        payload = self.manager.publish_host_lease()
        path = self.manager.host_lease_path
        target = path.with_name("outside.json")
        target.write_text(json.dumps(payload), encoding="utf-8")
        path.unlink()
        try:
            path.symlink_to(target)
            result = inspect_voice_capture_host_lease(path, now=self.clock)
        except OSError:
            path.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
            original_is_symlink = Path.is_symlink

            def report_test_path_as_symlink(candidate: Path) -> bool:
                return Path(candidate) == path or original_is_symlink(candidate)

            with patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=report_test_path_as_symlink,
            ):
                result = inspect_voice_capture_host_lease(path, now=self.clock)

        self.assertFalse(result["authorized"])
        self.assertEqual(
            result["reason"],
            "voice_capture_consent_heartbeat_untrusted",
        )

    def test_capability_requires_consent_and_offers_explicit_action(self):
        capabilities = {
            "voiceLocal": {
                "state": "ready",
                "ready": True,
                "blockers": [],
                "warnings": [],
                "dependencies": [
                    {"id": "local_io_bridge", "ready": True, "state": "up"}
                ],
                "repairActions": [],
            }
        }
        inactive = attach_voice_capture_consent(
            capabilities,
            self.manager.status(),
        )

        self.assertFalse(inactive["voiceLocal"]["ready"])
        self.assertIn(
            "local_mic_consent_required",
            {item["code"] for item in inactive["voiceLocal"]["blockers"]},
        )
        self.assertIn(
            "grant_voice_validation_mic_consent",
            {
                item["actionId"]
                for item in inactive["voiceLocal"]["repairActions"]
            },
        )

        active = attach_voice_capture_consent(capabilities, self.activate())
        self.assertTrue(active["voiceLocal"]["ready"])
        self.assertEqual(active["voiceLocal"]["repairActions"], [])


if __name__ == "__main__":
    unittest.main()
