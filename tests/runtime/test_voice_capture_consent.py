from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_capture_consent import (  # noqa: E402
    CONSENT_SCHEMA,
    VoiceCaptureConsentManager,
    attach_voice_capture_consent,
)


class Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class VoiceCaptureConsentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.clock = Clock()
        self.manager = VoiceCaptureConsentManager(
            root=self.root,
            now=self.clock,
            owner_nonce="process-a",
            preview_ttl_sec=120,
            armed_ttl_sec=300,
            active_ttl_sec=1800,
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

    def test_preview_is_short_lived_and_one_time(self):
        preview = self.manager.preview()
        first = self.manager.begin_apply(confirm_token=preview["confirmToken"])
        second = self.manager.begin_apply(confirm_token=preview["confirmToken"])

        self.assertTrue(first["ok"])
        self.assertEqual(second["error"], "voice_capture_confirm_token_reused")

        other = VoiceCaptureConsentManager(
            root=self.root / "expired",
            now=self.clock,
            owner_nonce="process-a",
            preview_ttl_sec=2,
        )
        expired_preview = other.preview()
        self.clock.value += 3
        expired = other.begin_apply(confirm_token=expired_preview["confirmToken"])
        self.assertEqual(expired["error"], "voice_capture_confirm_token_expired")

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

    def test_unbound_lease_expires_early_and_bound_lease_gets_full_budget(self):
        consent = self.activate()
        self.clock.value = float(consent["expiresAt"]) + 0.01
        self.assertEqual(self.manager.revocation_reason(), "consent_expired")

        second = VoiceCaptureConsentManager(
            root=self.root / "bound",
            now=self.clock,
            owner_nonce="process-a",
            armed_ttl_sec=300,
            active_ttl_sec=1800,
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

    def test_persisted_state_is_content_free(self):
        self.activate()
        payload = json.loads(self.manager.state_path.read_text(encoding="utf-8"))
        serialized = json.dumps(payload, ensure_ascii=False).lower()

        self.assertEqual(payload["schema"], CONSENT_SCHEMA)
        self.assertNotIn("audio", serialized)
        self.assertNotIn("transcript", serialized)
        self.assertNotIn("prompt", serialized)
        self.assertNotIn("text", serialized)

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
