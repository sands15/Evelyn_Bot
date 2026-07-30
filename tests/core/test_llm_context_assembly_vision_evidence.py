from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.context_pipeline import (  # noqa: E402
    ContextPolicy,
    ToolUseDecision,
    build_basic_context_packet,
    build_tool_use_decisions,
    build_vision_context_hint,
    render_tool_use_context,
)
from evelyn_core.llm_context_assembly import (  # noqa: E402
    LlmContextAssemblyDeps,
    apply_vision_evidence_to_tool_decisions,
    prepare_llm_messages_from_runtime,
)
from evelyn_core.vision_runtime import VisionEvidence, record_vision_evidence  # noqa: E402


class LlmContextAssemblyVisionEvidenceTests(unittest.TestCase):
    def decisions(self) -> list[ToolUseDecision]:
        return [
            ToolUseDecision(
                tool_name="vision_capture_or_watch",
                reason="screen requested",
                auto_allowed=True,
                required_before_answer=True,
            ),
            ToolUseDecision(
                tool_name="vision_ocr",
                reason="screen text requested",
                auto_allowed=True,
                required_before_answer=True,
                evidence="lazy OCR hint is not evidence",
            ),
        ]

    def test_request_or_failure_text_cannot_promote_tools_to_executed(self) -> None:
        decisions = self.decisions()
        evidence = VisionEvidence(
            state="failed",
            reason_code="analysis_failed",
        )

        apply_vision_evidence_to_tool_decisions(decisions, evidence)

        self.assertEqual(
            [decision.status for decision in decisions],
            ["failed_or_unavailable", "failed_or_unavailable"],
        )
        self.assertTrue(all("state=failed" in decision.evidence for decision in decisions))
        self.assertTrue(all("tool_satisfied=false" in decision.evidence for decision in decisions))
        self.assertNotIn("lazy OCR hint", decisions[1].evidence)

    def test_scene_only_observation_satisfies_capture_but_not_ocr(self) -> None:
        decisions = self.decisions()
        evidence = VisionEvidence(
            state="observed",
            reason_code="live_observation",
            evidence_available=True,
            scene_available=True,
            ocr_available=False,
            confidence="low",
            actionable=False,
            freshness="live",
        )

        apply_vision_evidence_to_tool_decisions(decisions, evidence)

        self.assertEqual(decisions[0].status, "executed")
        self.assertEqual(decisions[1].status, "failed_or_unavailable")
        self.assertIn("freshness=live", decisions[0].evidence)
        self.assertIn("ocr_available=false", decisions[1].evidence)

    def test_usable_live_ocr_satisfies_both_tools(self) -> None:
        decisions = self.decisions()
        evidence = VisionEvidence(
            state="observed",
            reason_code="live_observation",
            evidence_available=True,
            scene_available=True,
            ocr_available=True,
            confidence="normal",
            actionable=True,
            freshness="live",
        )

        apply_vision_evidence_to_tool_decisions(decisions, evidence)

        self.assertEqual([decision.status for decision in decisions], ["executed", "executed"])
        self.assertTrue(all("schema=vision.evidence.v1" in decision.evidence for decision in decisions))
        self.assertTrue(all("tool_satisfied=true" in decision.evidence for decision in decisions))

    def test_unknown_legacy_callback_result_fails_closed(self) -> None:
        decisions = self.decisions()

        apply_vision_evidence_to_tool_decisions(decisions, VisionEvidence())

        self.assertTrue(all(decision.status == "failed_or_unavailable" for decision in decisions))
        self.assertTrue(all("reason=missing_evidence_contract" in decision.evidence for decision in decisions))


class LlmContextAssemblyVisionEvidenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def build_deps(self, live_vision_callback) -> LlmContextAssemblyDeps:
        async def unused_async(*_args, **_kwargs):
            return None

        return LlmContextAssemblyDeps(
            compute_runtime_mode=lambda _metrics: "normal",
            apply_runtime_mode=lambda _mode: {"skip_router": True, "memory_update_mode": "defer"},
            classify_llm_route_fallback=lambda *_args, **_kwargs: "chat",
            classify_llm_route_async=unused_async,
            session_topic_ids={},
            get_conversation_history=lambda **_kwargs: [],
            read_cached_cognitive_state=lambda *_args, **_kwargs: None,
            get_matching_speculative_policy=lambda *_args, **_kwargs: None,
            fast_path_policy=lambda *_args, **_kwargs: None,
            session_state_snapshot=lambda *_args, **_kwargs: {},
            context_policy_for_fast_path_policy=lambda *_args, **_kwargs: {},
            extract_question_policy_from_route_meta=lambda _meta: {},
            build_fast_cognitive_state=lambda *_args, **_kwargs: {},
            update_cognitive_state=unused_async,
            schedule_cognitive_refresh=lambda *_args, **_kwargs: None,
            build_context_policy_for_turn=lambda **_kwargs: ContextPolicy(
                needs_vision=True,
                priority="accuracy",
            ),
            build_tool_use_decisions=build_tool_use_decisions,
            build_runtime_status_context=unused_async,
            clean_text=lambda value: str(value or "").strip(),
            build_local_tool_diagnostic_context=lambda *_args, **_kwargs: "",
            project_root=REPO_ROOT,
            build_memory_context=lambda *_args, **_kwargs: "",
            update_self_state_for_turn=lambda *_args, **_kwargs: {},
            observe_live_minecraft_state=unused_async,
            attach_minecraft_runtime_snapshot=lambda value, **_kwargs: value,
            control_page_minecraft_cache_refresh_sec=1.0,
            control_page_minecraft_cache_max_stale_sec=2.0,
            build_conversation_state_context=lambda **_kwargs: "",
            build_runtime_state_context=lambda **_kwargs: "",
            build_evelyn_runtime_dependency_context=lambda: "",
            render_self_judgment_context=lambda *_args, **_kwargs: "",
            render_self_state_context=lambda _state: "",
            render_vision_watch_context=lambda: "",
            build_minecraft_skill_context=lambda *_args, **_kwargs: "",
            odyssey_capability_json_dir=REPO_ROOT,
            build_skill_context_hint=lambda _policy: "",
            build_vision_context_hint=build_vision_context_hint,
            build_live_vision_context=live_vision_callback,
            render_tool_use_context=render_tool_use_context,
            build_basic_context_packet=build_basic_context_packet,
            ask_confidence_threshold_for_source=lambda _source: 0.0,
            apply_ask_gating=lambda state, **_kwargs: state,
            log_turn_event=lambda *_args, **_kwargs: None,
            visible_text=lambda value: value,
            log=lambda *_args, **_kwargs: None,
        )

    async def test_failure_message_is_context_but_not_observation_evidence(self) -> None:
        async def failed_vision(_user_text: str, *, metrics: dict | None = None) -> str:
            record_vision_evidence(
                metrics,
                VisionEvidence(state="failed", reason_code="analysis_failed"),
            )
            return "Vision analysis failed."

        metrics = {"started_at": time.monotonic(), "meta": {}, "marks": {}}
        messages, _state, _route, _policy = await prepare_llm_messages_from_runtime(
            "화면 글자 읽어줘",
            deps=self.build_deps(failed_vision),
            metrics=metrics,
        )

        system_context = messages[0]["content"]
        self.assertIn("VISION_EVIDENCE_GATE", system_context)
        self.assertIn("state=failed", system_context)
        self.assertIn("status=failed_or_unavailable", system_context)
        self.assertNotIn("status=executed;", system_context)
        context_meta = metrics["meta"]["context_pipeline"]
        self.assertTrue(context_meta["vision_requested"])
        self.assertTrue(context_meta["vision_context"])
        self.assertFalse(context_meta["vision_evidence_available"])
        self.assertEqual(context_meta["vision_evidence_state"], "failed")

    async def test_live_scene_and_ocr_mark_required_tools_executed(self) -> None:
        async def observed_vision(_user_text: str, *, metrics: dict | None = None) -> str:
            record_vision_evidence(
                metrics,
                VisionEvidence(
                    state="observed",
                    reason_code="live_observation",
                    evidence_available=True,
                    scene_available=True,
                    ocr_available=True,
                    confidence="normal",
                    actionable=True,
                    freshness="live",
                ),
            )
            return "scene: 설정 화면\nocr_text: 저장"

        metrics = {"started_at": time.monotonic(), "meta": {}, "marks": {}}
        messages, _state, _route, _policy = await prepare_llm_messages_from_runtime(
            "화면 글자 읽어줘",
            deps=self.build_deps(observed_vision),
            metrics=metrics,
        )

        system_context = messages[0]["content"]
        self.assertEqual(system_context.count("status=executed;"), 2)
        context_meta = metrics["meta"]["context_pipeline"]
        self.assertTrue(context_meta["vision_evidence_available"])
        self.assertTrue(context_meta["vision_scene_available"])
        self.assertTrue(context_meta["vision_ocr_available"])
        self.assertTrue(context_meta["vision_actionable"])

    async def test_unexpected_vision_runtime_error_degrades_without_losing_turn(self) -> None:
        async def broken_vision(_user_text: str, *, metrics: dict | None = None) -> str:
            raise ValueError("decoder exploded")

        metrics = {"started_at": time.monotonic(), "meta": {}, "marks": {}}
        messages, _state, _route, _policy = await prepare_llm_messages_from_runtime(
            "화면 글자 읽어줘",
            deps=self.build_deps(broken_vision),
            metrics=metrics,
        )

        system_context = messages[0]["content"]
        self.assertIn("reason=vision_runtime_error", system_context)
        self.assertIn("status=failed_or_unavailable", system_context)
        self.assertNotIn("decoder exploded", system_context)
        self.assertIn("decoder exploded", metrics["meta"]["vision_runtime_error"])
        self.assertEqual(
            metrics["meta"]["context_pipeline"]["vision_evidence_state"],
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
