from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.autonomy import AutonomyEngine, AutonomyGoal, assistant_proactive_impulse_text
from evelyn_core.self_model import (
    IDLE_ACTIVITY_TTL_SEC,
    EvelynSelfState,
    build_self_judgment,
    ensure_idle_activity,
    ensure_self_identity_profile,
    record_self_identity_turn,
    render_self_identity_context,
    render_self_judgment_context,
    render_self_state_context,
    select_self_impulse,
    update_self_state_from_observation,
)


class DummyExecutor:
    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def observe(self) -> dict[str, Any]:
        return {}

    async def execute_step(self, step: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "step": step}


class DummyVisionPolicy:
    needs_vision = True


class SelfModelVisionAwarenessTests(unittest.TestCase):
    def test_vision_impulse_is_gated_by_runtime_activity(self) -> None:
        state = EvelynSelfState(curiosity=0.8, restraint=0.2)

        self.assertEqual(select_self_impulse(state, {"local_tts_active": True}), ("stay_silent", "tts_active"))
        self.assertEqual(select_self_impulse(state, {"local_mic_recent": True}), ("stay_silent", "user_voice_recent"))
        self.assertEqual(select_self_impulse(state, {"vision_unreliable": True}), ("stay_silent", "vision_unreliable"))

    def test_repeated_vision_fingerprint_is_suppressed(self) -> None:
        now = time.time()
        state = EvelynSelfState(curiosity=0.7, restraint=0.2)
        observation = {
            "vision_change_recent": True,
            "vision_fingerprint": "abc123",
            "vision_watch": {"image_fingerprint": "abc123"},
            "last_autonomy_ping_sec": 999999,
        }

        updated = update_self_state_from_observation(observation, state=state, save=False)
        self.assertEqual(updated.last_impulse, "comment_on_screen_change")

        updated.last_proactive_at = 0.0
        updated.last_vision_fingerprint = "abc123"
        updated.last_vision_reacted_at = now
        repeated = update_self_state_from_observation(observation, state=updated, save=False)

        self.assertEqual(repeated.last_impulse, "stay_silent")
        self.assertEqual(repeated.last_gate_reason, "vision_repeat")

    def test_autonomy_ping_uses_impulse_text_metadata(self) -> None:
        engine = AutonomyEngine(guild_id=1, executor=DummyExecutor())
        text = assistant_proactive_impulse_text("comment_on_screen_change")
        goal = AutonomyGoal(
            kind="ping",
            summary="screen changed",
            priority=0.2,
            metadata={"domain": "assistant", "text": text},
        )

        plan = engine.plan_goal(goal, observation={})

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.steps[0]["action"], "maybe_ping_user")
        self.assertEqual(plan.steps[0]["text"], text)

    def test_autonomy_ping_need_requires_queued_question(self) -> None:
        engine = AutonomyEngine(guild_id=1, executor=DummyExecutor())
        base_observation = {
            "active_sessions": 1,
            "known_followup_channels": 1,
            "inflight_llm_requests": 0,
            "recent_context_items": 0,
            "last_autonomy_ping_sec": 999999,
            "quiet_hours": False,
            "self_state": {"last_impulse": "ask_light_question", "last_gate_reason": "curiosity"},
        }

        no_queue = engine.derive_needs(dict(base_observation))
        with_queue = engine.derive_needs({**base_observation, "queued_proactive_question_available": True})

        self.assertNotIn("ping", [need.kind for need in no_queue])
        self.assertIn("ping", [need.kind for need in with_queue])

    def test_idle_activity_persists_within_ttl(self) -> None:
        state = EvelynSelfState(mood="calm")

        first = ensure_idle_activity(state, now=1_800_000_000.0, save=False)
        first_activity = first.idle_activity
        first_label = first.idle_activity_label
        first_expires_at = first.idle_activity_expires_at
        first_revision = first.idle_activity_revision

        second = ensure_idle_activity(first, now=1_800_000_300.0, save=False)

        self.assertEqual(second.idle_activity, first_activity)
        self.assertEqual(second.idle_activity_label, first_label)
        self.assertEqual(second.idle_activity_expires_at, first_expires_at)
        self.assertEqual(second.idle_activity_revision, first_revision)

    def test_idle_activity_refreshes_after_expiry(self) -> None:
        now = 1_800_000_000.0
        state = ensure_idle_activity(EvelynSelfState(mood="calm"), now=now, save=False)
        first_revision = state.idle_activity_revision
        state.idle_activity_expires_at = now - 1.0

        refreshed = ensure_idle_activity(state, now=now + IDLE_ACTIVITY_TTL_SEC + 1.0, save=False)

        self.assertGreater(refreshed.idle_activity_revision, first_revision)
        self.assertGreater(refreshed.idle_activity_expires_at, now + IDLE_ACTIVITY_TTL_SEC)
        self.assertTrue(refreshed.idle_activity_label)

    def test_self_context_includes_ambient_idle_activity(self) -> None:
        state = ensure_idle_activity(EvelynSelfState(mood="calm"), now=1_800_000_000.0, save=False)

        context = render_self_state_context(state)

        self.assertIn("ambient_idle_activity=", context)
        self.assertIn(state.idle_activity, context)
        self.assertIn(state.idle_activity_label, context)

    def test_identity_profile_and_review_candidates_render(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_path = root / "identity.md"
            queue_path = root / "identity_queue.jsonl"

            profile = ensure_self_identity_profile(profile_path)
            self.assertIn("Evelyn Identity Profile", profile)
            self.assertTrue(profile_path.exists())

            decision = record_self_identity_turn(
                "말투가 아직 친근하지 않아. ~할게도 줄였으면 좋겠어.",
                "응, 바로 고쳐볼게.",
                source="test",
                queue_path=queue_path,
            )
            self.assertTrue(decision["recorded"])

            context = render_self_identity_context(
                profile_path=profile_path,
                queue_path=queue_path,
            )
            self.assertIn("Evelyn identity model:", context)
            self.assertIn("tone_feedback", context)
            self.assertIn("suffix_balance", context)
            self.assertIn("말투가 아직 친근하지 않아", context)

    def test_self_judgment_detects_identity_and_stance_topic(self) -> None:
        state = EvelynSelfState(mood="curious", curiosity=0.7, restraint=0.35)

        judgment = build_self_judgment(
            "대화하면서 정체성을 가지게 할 수 있잖아",
            state=state,
            route="main_llm",
        )
        context = render_self_judgment_context(
            "대화하면서 정체성을 가지게 할 수 있잖아",
            state=state,
            route="main_llm",
        )

        self.assertIn("identity_or_stance", judgment["topics"])
        self.assertIn("Evelyn self judgment stage:", context)
        self.assertIn("not hidden chain-of-thought", context)
        self.assertIn("Have opinions", context)
        self.assertIn("Identity/stance topic", context)

    def test_self_judgment_treats_screen_reading_as_evidence_limited(self) -> None:
        context = render_self_judgment_context(
            "내 화면 분석해줘",
            state=EvelynSelfState(),
            context_policy=DummyVisionPolicy(),
        )

        self.assertIn("vision_evidence", context)
        self.assertIn("if screen/OCR evidence is weak", context)
        self.assertIn("instead of guessing", context)


if __name__ == "__main__":
    unittest.main()
