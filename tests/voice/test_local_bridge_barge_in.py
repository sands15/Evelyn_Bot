from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_bridge_barge_in import (  # noqa: E402
    SingleOwnerPlaybackController,
    evaluate_local_barge_in,
)


class LocalBridgeBargeInTests(unittest.TestCase):
    def strong_meta(self):
        return {
            "duration_sec": 0.8,
            "voice_filter": {
                "vadSilent": False,
                "environmentNoise": False,
                "weakWaveform": False,
                "bodyRms": 0.025,
                "rms": 0.02,
            },
        }

    def test_strong_voice_segment_uses_shared_tts_interrupt_rules(self):
        decision = evaluate_local_barge_in(self.strong_meta(), body_rms_min=0.01)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "qualified_user_audio")
        self.assertTrue(decision.interrupt_meta.active_speaker_match)

    def test_weak_or_echo_segment_is_rejected(self):
        meta = self.strong_meta()
        meta["duration_sec"] = 0.2
        meta["voice_filter"]["weakWaveform"] = True
        meta["voice_filter"]["bodyRms"] = 0.001
        decision = evaluate_local_barge_in(meta, body_rms_min=0.01)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "weak_or_echo_input")

    def test_enabled_speaker_verification_rejection_blocks_interrupt(self):
        verification = SimpleNamespace(
            matched=False,
            to_dict=lambda: {"status": "rejected", "score": 0.2},
        )
        decision = evaluate_local_barge_in(
            self.strong_meta(),
            body_rms_min=0.01,
            speaker_verification=verification,
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "speaker_verification_rejected")

    def test_playback_controller_keeps_one_owner_until_release(self):
        cancelled = []
        controller = SingleOwnerPlaybackController()

        self.assertTrue(controller.claim("turn-1", lambda: cancelled.append("turn-1")))
        self.assertFalse(controller.claim("turn-2", lambda: cancelled.append("turn-2")))
        self.assertTrue(controller.request_cancel())
        self.assertFalse(controller.request_cancel())
        self.assertEqual(cancelled, ["turn-1"])
        self.assertFalse(controller.release("turn-2"))
        self.assertTrue(controller.release("turn-1"))
        self.assertTrue(controller.claim("turn-2", lambda: cancelled.append("turn-2")))


if __name__ == "__main__":
    unittest.main()
