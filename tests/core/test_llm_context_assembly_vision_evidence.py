from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.context_pipeline import (  # noqa: E402
    ContextPolicy,
    ToolUseDecision,
    build_basic_context_packet,
    build_conversation_state_context,
    build_tool_use_decisions,
    build_vision_context_hint,
    render_tool_use_context,
)
from evelyn_core.llm_context_assembly import (  # noqa: E402
    LlmContextAssemblyDeps,
    apply_vision_evidence_to_tool_decisions,
    prepare_llm_messages_from_runtime,
)
from evelyn_core.cross_surface_continuity import (  # noqa: E402
    CrossSurfaceMergeOutcome,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MEMORY_DELETION_POSITION_SCHEMA,
    MemoryDeletionPosition,
)
from evelyn_core.memory_deletion_outbound import (  # noqa: E402
    capture_memory_deletion_outbound_position,
)
from evelyn_core.memory_prompt_policy import (  # noqa: E402
    MEMORY_PROMPT_MAX_CHARS,
    memory_deletion_boundary_from_position,
)
from evelyn_core.skills.routing.voice_llm import (  # noqa: E402
    build_main_llm_payload,
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
        now = time.time()
        evidence = VisionEvidence(
            state="observed",
            reason_code="live_observation",
            evidence_available=True,
            scene_available=True,
            ocr_available=False,
            confidence="low",
            actionable=False,
            freshness="live",
            observed_at=now,
            expires_at=now + 15.0,
        )

        apply_vision_evidence_to_tool_decisions(decisions, evidence)

        self.assertEqual(decisions[0].status, "executed")
        self.assertEqual(decisions[1].status, "failed_or_unavailable")
        self.assertIn("freshness=live", decisions[0].evidence)
        self.assertIn("ocr_available=false", decisions[1].evidence)

    def test_usable_live_ocr_satisfies_both_tools(self) -> None:
        decisions = self.decisions()
        now = time.time()
        evidence = VisionEvidence(
            state="observed",
            reason_code="live_observation",
            evidence_available=True,
            scene_available=True,
            ocr_available=True,
            confidence="normal",
            actionable=True,
            freshness="live",
            observed_at=now,
            expires_at=now + 15.0,
        )

        apply_vision_evidence_to_tool_decisions(decisions, evidence)

        self.assertEqual([decision.status for decision in decisions], ["executed", "executed"])
        self.assertTrue(all("schema=vision.evidence.v2" in decision.evidence for decision in decisions))
        self.assertTrue(all("tool_satisfied=true" in decision.evidence for decision in decisions))

    def test_unknown_legacy_callback_result_fails_closed(self) -> None:
        decisions = self.decisions()

        apply_vision_evidence_to_tool_decisions(decisions, VisionEvidence())

        self.assertTrue(all(decision.status == "failed_or_unavailable" for decision in decisions))
        self.assertTrue(all("reason=missing_evidence_contract" in decision.evidence for decision in decisions))


class LlmContextAssemblyVisionEvidenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def build_deps(
        self,
        live_vision_callback,
        *,
        merge_cross_surface_context=None,
        memory_context_callback=None,
        conversation_history=None,
        conversation_context_callback=None,
    ) -> LlmContextAssemblyDeps:
        async def unused_async(*_args, **_kwargs):
            return None

        return LlmContextAssemblyDeps(
            compute_runtime_mode=lambda _metrics: "normal",
            apply_runtime_mode=lambda _mode: {"skip_router": True, "memory_update_mode": "defer"},
            classify_llm_route_fallback=lambda *_args, **_kwargs: "chat",
            classify_llm_route_async=unused_async,
            session_topic_ids={},
            get_conversation_history=lambda **_kwargs: list(
                conversation_history or []
            ),
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
            memory_index_dir=REPO_ROOT / "memory_index",
            build_memory_context=(
                memory_context_callback
                or (lambda *_args, **_kwargs: "")
            ),
            update_self_state_for_turn=lambda *_args, **_kwargs: {},
            observe_live_minecraft_state=unused_async,
            attach_minecraft_runtime_snapshot=lambda value, **_kwargs: value,
            control_page_minecraft_cache_refresh_sec=1.0,
            control_page_minecraft_cache_max_stale_sec=2.0,
            build_conversation_state_context=(
                conversation_context_callback
                or (lambda **_kwargs: "")
            ),
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
            merge_cross_surface_context=(
                merge_cross_surface_context
            ),
            log=lambda *_args, **_kwargs: None,
        )

    async def test_verified_cross_surface_context_enters_common_prompt(
        self,
    ) -> None:
        calls = []

        def merge(messages, **kwargs):
            calls.append((list(messages), dict(kwargs)))
            return CrossSurfaceMergeOutcome(
                messages=(
                    {
                        "role": "user",
                        "content": (
                            "다른 surface의 검증된 질문"
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": (
                            "다른 surface의 검증된 답"
                        ),
                        "memoryReceiptRef": (
                            not_used_memory_receipt_ref()
                        ),
                    },
                ),
                evidence={
                    "schema": (
                        "cross_surface_continuity.merge.v1"
                    ),
                    "state": "merged",
                    "sourceSurface": "main",
                    "reasonCode": "",
                    "localOwnerState": "verified",
                    "crossOwnerState": "verified",
                    "localGeneration": 3,
                    "crossGeneration": 4,
                    "localMessageCount": 0,
                    "crossMessageCount": 2,
                    "outputMessageCount": 2,
                    "ordering": "cross_after_local",
                    "updatedAt": 1000.0,
                    "latencyMs": 0.5,
                    "policy": {
                        "contentFree": True,
                        "persisted": False,
                        "readOnly": True,
                    },
                    "privateMessage": "TURN_PRIVATE_CONTENT",
                },
            )

        async def no_vision(
            _user_text: str,
            *,
            metrics: dict | None = None,
        ) -> str:
            return ""

        metrics = {
            "started_at": time.monotonic(),
            "meta": {},
            "marks": {},
        }
        messages, _state, _route, _policy = (
            await prepare_llm_messages_from_runtime(
                "현재 질문",
                deps=self.build_deps(
                    no_vision,
                    merge_cross_surface_context=merge,
                ),
                guild_id=7,
                session_key="guild:7:text:8:user:9",
                metrics=metrics,
            )
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0][1],
            {
                "session_key": "guild:7:text:8:user:9",
                "current_user_text": "현재 질문",
            },
        )
        self.assertTrue(
            any(
                message.get("content")
                == "다른 surface의 검증된 질문"
                for message in messages
            )
        )
        evidence = metrics["meta"][
            "cross_surface_continuity"
        ]
        self.assertEqual(
            evidence["schema"],
            "cross_surface_continuity.merge.v1",
        )
        self.assertEqual(evidence["state"], "merged")
        serialized_evidence = str(evidence)
        self.assertNotIn(
            "다른 surface의 검증된 질문",
            serialized_evidence,
        )
        self.assertNotIn(
            "다른 surface의 검증된 답",
            serialized_evidence,
        )
        self.assertNotIn(
            "TURN_PRIVATE_CONTENT",
            serialized_evidence,
        )
        self.assertTrue(
            any(
                message.get("content")
                == "다른 surface의 검증된 답"
                for message in messages
            )
        )

    async def test_receipt_fields_never_enter_context_or_voice_payload(
        self,
    ) -> None:
        async def no_vision(
            _user_text: str,
            *,
            metrics: dict | None = None,
        ) -> str:
            return ""

        receipt_canary = "CONVERSATION_RECEIPT_CANARY"
        history = [
            {
                "role": "user",
                "content": "SAFE_USER_HISTORY",
                "memoryReceipt": {"canary": receipt_canary},
                "memoryReceiptRef": {"canary": receipt_canary},
            },
            {
                "role": "assistant",
                "content": "SAFE_ASSISTANT_HISTORY",
                "memoryReceiptRef": not_used_memory_receipt_ref(),
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            deps = replace(
                self.build_deps(
                    no_vision,
                    conversation_history=history,
                ),
                project_root=temp_root / "project",
                memory_index_dir=temp_root / "memory_index",
                odyssey_capability_json_dir=(
                    temp_root / "capabilities"
                ),
            )
            metrics = {
                "started_at": time.monotonic(),
                "meta": {},
                "marks": {},
            }

            messages, _state, _route, _policy = (
                await prepare_llm_messages_from_runtime(
                    "CURRENT_USER_TURN",
                    deps=deps,
                    guild_id=7,
                    session_key="session-7",
                    metrics=metrics,
                )
            )
            payload = build_main_llm_payload(
                model_name="test-model",
                messages=messages,
                final_user_text="CURRENT_USER_TURN",
                source="voice",
                stream=True,
            )

        self.assertTrue(
            any(
                message.get("content") == "SAFE_USER_HISTORY"
                for message in messages
            )
        )
        self.assertTrue(
            any(
                message.get("content") == "SAFE_ASSISTANT_HISTORY"
                for message in messages
            )
        )
        for projection in (messages, payload["messages"]):
            serialized = json.dumps(
                projection,
                ensure_ascii=False,
            )
            self.assertNotIn("memoryReceipt", serialized)
            self.assertNotIn("memoryReceiptRef", serialized)
            self.assertNotIn(receipt_canary, serialized)
            self.assertEqual(
                sum(
                    "memoryReceipt" in message
                    or "memoryReceiptRef" in message
                    for message in projection
                ),
                0,
            )

    async def test_unanswered_history_gets_content_free_continuity_rule(self) -> None:
        async def no_vision(
            _user_text: str,
            *,
            metrics: dict | None = None,
        ) -> str:
            return ""

        private_text = "PRIVATE_UNANSWERED_HISTORY_TEXT"
        metrics = {
            "started_at": time.monotonic(),
            "meta": {},
            "marks": {},
        }
        messages, _state, _route, _policy = (
            await prepare_llm_messages_from_runtime(
                "current request",
                deps=self.build_deps(
                    no_vision,
                    conversation_history=[
                        {"role": "user", "content": private_text},
                    ],
                    conversation_context_callback=(
                        build_conversation_state_context
                    ),
                ),
                metrics=metrics,
            )
        )

        self.assertIn(
            "continuity_schema: conversation.unanswered-user.v1",
            messages[0]["content"],
        )
        self.assertEqual(
            [
                message
                for message in messages
                if message.get("role") == "user"
            ],
            [{"role": "user", "content": private_text}],
        )
        context_meta = metrics["meta"]["context_pipeline"]
        self.assertTrue(context_meta["unanswered_user_turn_context"])
        self.assertNotIn(private_text, str(context_meta))

    async def test_answered_history_does_not_get_unanswered_rule(self) -> None:
        async def no_vision(
            _user_text: str,
            *,
            metrics: dict | None = None,
        ) -> str:
            return ""

        metrics = {
            "started_at": time.monotonic(),
            "meta": {},
            "marks": {},
        }
        messages, _state, _route, _policy = (
            await prepare_llm_messages_from_runtime(
                "current request",
                deps=self.build_deps(
                    no_vision,
                    conversation_history=[
                        {"role": "user", "content": "previous"},
                        {
                            "role": "assistant",
                            "content": "delivered",
                            "memoryReceiptRef": (
                                not_used_memory_receipt_ref()
                            ),
                        },
                    ],
                    conversation_context_callback=(
                        build_conversation_state_context
                    ),
                ),
                metrics=metrics,
            )
        )

        self.assertNotIn(
            "conversation.unanswered-user.v1",
            messages[0]["content"],
        )
        self.assertFalse(
            metrics["meta"]["context_pipeline"][
                "unanswered_user_turn_context"
            ]
        )

    async def test_cross_surface_unanswered_history_gets_same_rule(self) -> None:
        async def no_vision(
            _user_text: str,
            *,
            metrics: dict | None = None,
        ) -> str:
            return ""

        def merge(_messages, **_kwargs):
            return CrossSurfaceMergeOutcome(
                messages=(
                    {
                        "role": "user",
                        "content": "PRIVATE_CROSS_SURFACE_UNANSWERED",
                    },
                ),
                evidence={
                    "state": "merged",
                    "sourceSurface": "fast_control",
                    "localOwnerState": "empty",
                    "crossOwnerState": "verified",
                    "crossMessageCount": 1,
                    "outputMessageCount": 1,
                    "ordering": "cross_after_local",
                },
            )

        metrics = {
            "started_at": time.monotonic(),
            "meta": {},
            "marks": {},
        }
        messages, _state, _route, _policy = (
            await prepare_llm_messages_from_runtime(
                "current request",
                deps=self.build_deps(
                    no_vision,
                    merge_cross_surface_context=merge,
                    conversation_context_callback=(
                        build_conversation_state_context
                    ),
                ),
                metrics=metrics,
            )
        )

        self.assertIn(
            "continuity_schema: conversation.unanswered-user.v1",
            messages[0]["content"],
        )
        self.assertTrue(
            metrics["meta"]["context_pipeline"][
                "unanswered_user_turn_context"
            ]
        )
        self.assertNotIn(
            "PRIVATE_CROSS_SURFACE_UNANSWERED",
            str(metrics["meta"]),
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

    async def test_memory_receipt_reaches_context_metrics_without_memory_text(self) -> None:
        private_note_id = "note-verified"

        async def no_vision(_user_text: str, *, metrics: dict | None = None) -> str:
            return ""

        def grounded_memory(*_args, receipt=None, **_kwargs):
            deletion_position = MemoryDeletionPosition(
                schema=MEMORY_DELETION_POSITION_SCHEMA,
                root_digest="a" * 64,
                sequence=0,
                position_digest="b" * 64,
            )
            capture_memory_deletion_outbound_position(
                deletion_position
            )
            receipt.update(
                {
                    "schema": "memory.context-receipt.v1",
                    "state": "provided",
                    "groundingState": "attributed",
                    "suppliedNoteIds": [private_note_id],
                    "suppliedNoteCount": 1,
                    "legacyItemCount": 0,
                    "hotContextState": "empty",
                    "memoryVersion": 4,
                    "deletionBoundary": (
                        memory_deletion_boundary_from_position(
                            deletion_position
                        )
                    ),
                    "contentFree": True,
                    "privateReceiptField": "PRIVATE_RECEIPT_FIELD",
                }
            )
            return "PRIVATE_MEMORY_TEXT"

        deps = self.build_deps(
            no_vision,
            memory_context_callback=grounded_memory,
        )
        metrics = {"started_at": time.monotonic(), "meta": {}, "marks": {}}
        messages, _state, _route, _policy = await prepare_llm_messages_from_runtime(
            "remember my preference",
            deps=deps,
            guild_id=7,
            metrics=metrics,
        )

        self.assertIn("PRIVATE_MEMORY_TEXT", messages[0]["content"])
        self.assertIn("MEMORY_DATA_RULE:", messages[0]["content"])
        self.assertNotIn("MEMORY_CONFIRMATION_RULE:", messages[0]["content"])
        receipt = metrics["meta"]["context_pipeline"]["memory_receipt"]
        self.assertEqual(receipt["groundingState"], "attributed")
        self.assertEqual(receipt["usePolicy"], "memory.context-use.v1")
        self.assertEqual(receipt["confirmOnlyItemCount"], 0)
        self.assertEqual(len(receipt["suppliedNoteIds"]), 1)
        self.assertRegex(
            receipt["suppliedNoteIds"][0],
            r"^opaque-[0-9a-f]{64}$",
        )
        self.assertNotIn(private_note_id, str(receipt))
        self.assertNotIn("PRIVATE_MEMORY_TEXT", str(receipt))
        self.assertNotIn("PRIVATE_RECEIPT_FIELD", str(receipt))

    async def test_unattributed_memory_body_is_withheld_at_final_prompt_boundary(self) -> None:
        async def no_vision(_user_text: str, *, metrics: dict | None = None) -> str:
            return ""

        def unattributed_memory(*_args, receipt=None, **_kwargs):
            receipt.update(
                {
                    "state": "provided",
                    "groundingState": "unattributed",
                    "confirmOnlyItemCount": "invalid",
                    "contentFree": True,
                }
            )
            return "PRIVATE_LEGACY_MEMORY"

        deps = self.build_deps(
            no_vision,
            memory_context_callback=unattributed_memory,
        )
        metrics = {"started_at": time.monotonic(), "meta": {}, "marks": {}}
        messages, _state, _route, _policy = await prepare_llm_messages_from_runtime(
            "remember",
            deps=deps,
            guild_id=7,
            metrics=metrics,
        )

        system_context = messages[0]["content"]
        self.assertNotIn("PRIVATE_LEGACY_MEMORY", system_context)
        self.assertIn("MEMORY_DATA_RULE:", system_context)
        self.assertIn("MEMORY_CONFIRMATION_RULE:", system_context)
        self.assertIn("MEMORY_WITHHELD_RULE:", system_context)
        self.assertIn("status=executed_withheld", system_context)
        self.assertIn(
            "the tool ran but its result was deliberately excluded",
            system_context,
        )
        receipt = metrics["meta"]["context_pipeline"]["memory_receipt"]
        self.assertEqual(receipt["usePolicy"], "memory.context-use.v1")
        self.assertEqual(receipt["state"], "withheld")
        self.assertEqual(receipt["confirmOnlyItemCount"], 0)
        self.assertTrue(receipt["promptMemoryWithheld"])
        self.assertEqual(receipt["withheldItemCount"], 1)

    async def test_oversized_main_memory_discards_attribution_before_context_builder(self) -> None:
        async def no_vision(_user_text: str, *, metrics: dict | None = None) -> str:
            return ""

        def oversized_memory(*_args, receipt=None, **_kwargs):
            receipt.update(
                {
                    "state": "provided",
                    "groundingState": "attributed",
                    "vaultState": "provided",
                    "suppliedNoteIds": ["note-oversized"],
                    "suppliedNoteCount": 1,
                    "sourceTypeCounts": {"user": 1},
                    "legacyItemCount": 1,
                    "legacyAttributedItemCount": 1,
                    "legacyEvidenceIds": ["turn:a:user"],
                    "contentFree": True,
                }
            )
            return "PRIVATE_OVERSIZED_MEMORY " * 300

        deps = self.build_deps(
            no_vision,
            memory_context_callback=oversized_memory,
        )
        metrics = {"started_at": time.monotonic(), "meta": {}, "marks": {}}
        messages, _state, _route, _policy = await prepare_llm_messages_from_runtime(
            "remember",
            deps=deps,
            guild_id=7,
            metrics=metrics,
        )

        receipt = metrics["meta"]["context_pipeline"]["memory_receipt"]
        self.assertEqual(receipt["state"], "withheld")
        self.assertEqual(receipt["groundingState"], "unattributed")
        self.assertTrue(receipt["promptTruncated"])
        self.assertTrue(receipt["promptEvidenceDiscarded"])
        self.assertTrue(receipt["promptMemoryWithheld"])
        self.assertEqual(receipt["suppliedNoteIds"], [])
        self.assertEqual(receipt["suppliedNoteCount"], 0)
        self.assertEqual(receipt["legacyEvidenceIds"], [])
        self.assertEqual(receipt["legacyItemCount"], 0)
        self.assertEqual(receipt["preTruncationNoteCount"], 1)
        self.assertEqual(receipt["preTruncationLegacyItemCount"], 1)
        self.assertEqual(receipt["withheldItemCount"], 2)
        self.assertEqual(receipt["withheldNoteCount"], 1)
        self.assertEqual(receipt["withheldLegacyItemCount"], 1)
        self.assertEqual(receipt["opaqueConfirmOnlyComponentCount"], 0)
        self.assertLessEqual(
            metrics["meta"]["context_pipeline"]["memory_context_chars"],
            MEMORY_PROMPT_MAX_CHARS,
        )
        self.assertIn("MEMORY_CONFIRMATION_RULE:", messages[0]["content"])
        self.assertIn("MEMORY_WITHHELD_RULE:", messages[0]["content"])
        self.assertNotIn("PRIVATE_OVERSIZED_MEMORY", messages[0]["content"])

    async def test_live_scene_and_ocr_mark_required_tools_executed(self) -> None:
        async def observed_vision(_user_text: str, *, metrics: dict | None = None) -> str:
            now = time.time()
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
                    observed_at=now,
                    expires_at=now + 15.0,
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

    async def test_stale_observation_text_is_removed_before_llm_context(self) -> None:
        async def stale_vision(_user_text: str, *, metrics: dict | None = None) -> str:
            now = time.time()
            record_vision_evidence(
                metrics,
                VisionEvidence(
                    state="observed",
                    reason_code="claimed_live",
                    evidence_available=True,
                    scene_available=True,
                    ocr_available=True,
                    confidence="normal",
                    actionable=True,
                    freshness="live",
                    observed_at=now - 30.0,
                    expires_at=now - 15.0,
                ),
            )
            return "scene: STALE_PRIVATE_SCREEN_CONTENT"

        metrics = {"started_at": time.monotonic(), "meta": {}, "marks": {}}
        messages, _state, _route, _policy = await prepare_llm_messages_from_runtime(
            "화면 글자 읽어줘",
            deps=self.build_deps(stale_vision),
            metrics=metrics,
        )

        system_context = messages[0]["content"]
        self.assertNotIn("STALE_PRIVATE_SCREEN_CONTENT", system_context)
        self.assertIn("observation was discarded", system_context)
        self.assertIn("reason=stale_visual_evidence", system_context)
        self.assertNotIn("status=executed;", system_context)

    async def test_unexpected_vision_runtime_error_degrades_without_losing_turn(self) -> None:
        private_canary = "PRIVATE_VISION_ERROR C:/secret/decoder-token"

        async def broken_vision(_user_text: str, *, metrics: dict | None = None) -> str:
            raise ValueError(private_canary)

        metrics = {"started_at": time.monotonic(), "meta": {}, "marks": {}}
        messages, _state, _route, _policy = await prepare_llm_messages_from_runtime(
            "화면 글자 읽어줘",
            deps=self.build_deps(broken_vision),
            metrics=metrics,
        )

        system_context = messages[0]["content"]
        self.assertIn("reason=vision_runtime_error", system_context)
        self.assertIn("status=failed_or_unavailable", system_context)
        self.assertNotIn(private_canary, system_context)
        self.assertEqual(
            metrics["meta"]["vision_runtime_error"],
            "vision_runtime_error",
        )
        self.assertNotIn(private_canary, str(metrics))
        self.assertEqual(
            metrics["meta"]["context_pipeline"]["vision_evidence_state"],
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
