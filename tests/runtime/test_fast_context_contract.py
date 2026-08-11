from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import fast_context_contract as fast_contract  # noqa: E402
from evelyn_core.assistant_contracts import MemoryRecallResult  # noqa: E402
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    memory_receipt_ref_from_receipt,
)
from evelyn_core.fast_context_contract import (  # noqa: E402
    build_fast_log_context,
    build_fast_control_context,
    build_fast_main_llm_request,
    build_fast_main_llm_messages,
)
from evelyn_core.host_vision_client import HostVisionResult  # noqa: E402
from evelyn_core.memory_prompt_policy import MEMORY_PROMPT_MAX_CHARS  # noqa: E402
from evelyn_core.memory_prompt_policy import memory_deletion_boundary_from_position  # noqa: E402
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalBusyError,
    MemoryDeletionJournalIntegrityError,
    MemoryDeletionPosition,
    memory_deletion_ledger_note_id,
)
from evelyn_core.memory_deletion_outbound import (  # noqa: E402
    capture_memory_deletion_outbound_position,
    reset_memory_deletion_outbound_position,
)
from evelyn_core.memory_exposure import (  # noqa: E402
    MemoryExposurePosition,
    capture_memory_exposure_position,
    reset_memory_exposure_position,
)
from evelyn_core.vision_runtime import VisionEvidence  # noqa: E402


async def fake_runtime_health() -> dict[str, object]:
    return {
        "overallState": "up",
        "summary": "All runtime services are ready.",
        "services": [
            {"id": "bot_api", "state": "up", "reason": "ok"},
            {"id": "main_llm", "state": "up", "reason": "ok"},
            {"id": "tts", "state": "up", "reason": "ok"},
        ],
        "diagnostics": [],
    }


async def fake_search(query: str) -> tuple[str, list[dict[str, str]]]:
    return query, [
        {
            "title": "Weather Example",
            "snippet": "Today is rainy and cool.",
            "url": "https://example.test/weather",
        }
    ]


async def fake_memory(_: str) -> str:
    return "Memory note: 정훈 prefers exact stabilization reports."


TEST_DELETION_POSITION = MemoryDeletionPosition(
    schema="memory.deletion.position.v1",
    root_digest="a" * 64,
    sequence=13,
    position_digest="b" * 64,
)


def capture_test_deletion_boundary() -> dict[str, object]:
    capture_memory_deletion_outbound_position(TEST_DELETION_POSITION)
    return memory_deletion_boundary_from_position(TEST_DELETION_POSITION)


async def fake_logs(_: str) -> str:
    return "Recent Evelyn log evidence: background_start/Bot-Control.log\napi_error:500 while handling /shutdown"


async def fake_observed_vision(user_text: str, *, run_ocr: bool) -> HostVisionResult:
    now = time.time()
    return HostVisionResult(
        observation=(
            "Local screen vision observation is available.\n"
            "scene: Evelyn Control Page가 열려 있다.\n"
            + ("ocr_text: Start voice validation" if run_ocr else "")
        ),
        evidence=VisionEvidence(
            state="observed",
            reason_code="live_observation",
            evidence_available=True,
            scene_available=True,
            ocr_available=run_ocr,
            confidence="normal",
            actionable=True,
            freshness="live",
            observed_at=now,
            expires_at=now + 15.0,
        ),
        screenshot_deleted=True,
    )


async def fake_accessibility_vision(user_text: str, *, run_ocr: bool) -> HostVisionResult:
    now = time.time()
    return HostVisionResult(
        observation=(
            "Local screen vision observation is available.\n"
            "foreground_window: title=Minecraft 26.2 - 싱글플레이; class=GLFW30\n"
            "ocr_text:\n"
            "Window: Minecraft 26.2 - 싱글플레이"
        ),
        evidence=VisionEvidence(
            state="observed",
            reason_code="live_accessibility_observation",
            evidence_available=True,
            scene_available=True,
            ocr_available=True,
            confidence="normal",
            actionable=True,
            freshness="live",
            observed_at=now,
            expires_at=now + 15.0,
        ),
        screenshot_deleted=True,
    )


async def fake_failed_vision(user_text: str, *, run_ocr: bool) -> HostVisionResult:
    return HostVisionResult(
        observation="Screen capture returned a black frame. Do not claim screen contents.",
        evidence=VisionEvidence(
            state="failed",
            reason_code="black_frame",
        ),
        error_code="black_frame",
        screenshot_deleted=True,
    )


async def fake_scene_only_vision(user_text: str, *, run_ocr: bool) -> HostVisionResult:
    now = time.time()
    return HostVisionResult(
        observation=(
            "Local screen vision observation is available.\n"
            "scene: Evelyn."
        ),
        evidence=VisionEvidence(
            state="observed",
            reason_code="live_observation",
            evidence_available=True,
            scene_available=True,
            ocr_available=False,
            confidence="normal",
            actionable=True,
            freshness="live",
            observed_at=now,
            expires_at=now + 15.0,
        ),
        screenshot_deleted=True,
    )


async def fake_stale_vision(user_text: str, *, run_ocr: bool) -> HostVisionResult:
    now = time.time()
    return HostVisionResult(
        observation="scene: STALE_PRIVATE_SCREEN_CONTENT",
        evidence=VisionEvidence(
            state="observed",
            reason_code="claimed_live",
            evidence_available=True,
            scene_available=True,
            ocr_available=run_ocr,
            confidence="normal",
            actionable=True,
            freshness="live",
            observed_at=now - 30.0,
            expires_at=now - 15.0,
        ),
        screenshot_deleted=True,
    )


def fake_local_bridge_status() -> dict[str, object]:
    return {
        "enabled": True,
        "ready": True,
        "micEnabled": False,
        "mic": {"enabled": False, "captureActive": False},
        "segmentCount": 0,
        "transcriptCount": 0,
        "speaking": False,
    }


class FastContextContractTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        reset_memory_deletion_outbound_position()

    def test_bot_api_requirements_include_memory_recall_dependency(self) -> None:
        requirements = (REPO_ROOT / "docker" / "requirements.bot-api.txt").read_text(encoding="utf-8")

        self.assertIn("numpy", requirements)

    async def test_plain_turn_keeps_not_used_memory_receipt(self) -> None:
        context = await build_fast_control_context(
            "안녕",
            source="control_page",
        )

        self.assertEqual(context.memory_receipt["state"], "not_requested")
        self.assertEqual(
            context.memory_receipt["groundingState"],
            "not_requested",
        )
        self.assertEqual(
            memory_receipt_ref_from_receipt(
                context.memory_receipt
            )["state"],
            "not_used",
        )

    async def test_runtime_status_tool_is_executed_in_fast_context(self) -> None:
        context = await build_fast_control_context(
            "main llm runtime status and gpu status?",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            local_bridge_status_provider=fake_local_bridge_status,
        )

        by_name = {item.tool_name: item for item in context.tool_use_decisions}
        self.assertIn("runtime_status", by_name)
        self.assertEqual(by_name["runtime_status"].status, "executed")
        self.assertIn("All runtime services are ready", by_name["runtime_status"].evidence)
        self.assertIn("mic_enabled=false", by_name["runtime_status"].evidence)
        self.assertIn("runtime_status", context.system_context)
        self.assertIn("mic_capture_active=false", context.system_context)
        self.assertIn("mic_enabled=false", context.local_bridge_context)
        self.assertIn("fast_control_route", context.system_context)

    async def test_current_info_executes_search_in_fast_context(self) -> None:
        context = await build_fast_control_context(
            "weather today?",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            search_provider=fake_search,
        )

        web = next(item for item in context.tool_use_decisions if item.tool_name == "web_current_info")
        self.assertTrue(web.auto_allowed)
        self.assertTrue(web.required_before_answer)
        self.assertEqual(web.status, "executed")
        self.assertIn("Weather Example", web.evidence)
        self.assertIn("Search tool result", context.system_context)
        self.assertIn("Today is rainy and cool", context.system_context)

    async def test_korean_research_request_executes_search_without_vision_false_positive(self) -> None:
        context = await build_fast_control_context(
            "STT 모델 후보를 알아봐줘",
            source="local_bridge",
            runtime_health_provider=fake_runtime_health,
            search_provider=fake_search,
        )

        by_name = {item.tool_name: item for item in context.tool_use_decisions}
        self.assertEqual(by_name["web_current_info"].status, "executed")
        self.assertNotIn("vision_capture_or_watch", by_name)

    async def test_contextual_tool_text_can_ground_short_followup_search(self) -> None:
        context = await build_fast_control_context(
            "그거 해줘",
            tool_user_text="로컬 STT 모델 교체 후보 찾아봐",
            source="local_bridge",
            runtime_health_provider=fake_runtime_health,
            search_provider=fake_search,
        )

        web = next(item for item in context.tool_use_decisions if item.tool_name == "web_current_info")
        self.assertEqual(web.status, "executed")
        self.assertIn("로컬 STT 모델 교체 후보 찾아봐", context.search_context)

    async def test_tool_diagnostic_executes_mounted_log_read_in_fast_context(self) -> None:
        context = await build_fast_control_context(
            "\uba54\uc778 llm\uc758 \ub3c4\uad6c \ud638\ucd9c\uc774 \ub108\ubb34 \uc57d\ud574",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            search_provider=fake_search,
            log_provider=fake_logs,
        )

        by_name = {item.tool_name: item for item in context.tool_use_decisions}
        self.assertIn("runtime_status", by_name)
        self.assertIn("local_file_or_log_read", by_name)
        self.assertEqual(by_name["local_file_or_log_read"].status, "executed")
        self.assertIn("api_error:500", by_name["local_file_or_log_read"].evidence)
        self.assertIn("runtime_log_read", context.system_context)
        self.assertIn("api_error:500", context.system_context)

    async def test_plain_log_request_executes_mounted_log_read_in_fast_context(self) -> None:
        context = await build_fast_control_context(
            "/shutdown api_error:500 로그 확인해줘",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            log_provider=fake_logs,
        )

        log_read = next(item for item in context.tool_use_decisions if item.tool_name == "local_file_or_log_read")
        self.assertTrue(log_read.auto_allowed)
        self.assertEqual(log_read.status, "executed")
        self.assertIn("api_error:500", context.system_context)

    def test_fast_log_context_reads_recent_bounded_log_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            nested = root / "background_start"
            nested.mkdir()
            log_path = nested / "Bot-Control.log"
            log_path.write_text(
                "\n".join(
                    [
                        "startup ok",
                        "[LOCAL BRIDGE] transcript='private voice text' [LOCAL BRIDGE] error=none",
                        "error authorization: should-not-leak",
                        "api_error:500 while handling /shutdown",
                    ]
                ),
                encoding="utf-16",
            )

            context = build_fast_log_context("/shutdown 로그 확인", roots=[root], max_files=2, max_chars=1000)

        self.assertIn("background_start", context)
        self.assertIn("api_error:500", context)
        self.assertIn("authorization=<redacted>", context)
        self.assertNotIn("should-not-leak", context)
        self.assertNotIn("private voice text", context)

    def test_fast_log_context_can_require_query_match_for_investigation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            (root / "runtime.log").write_text(
                "unrelated startup line\nminecraft warning only\n",
                encoding="utf-8",
            )

            context = build_fast_log_context(
                "tts",
                roots=[root],
                require_match=True,
            )

        self.assertEqual(context, "")

    async def test_memory_recall_executes_in_fast_context(self) -> None:
        context = await build_fast_control_context(
            "memory previous preference?",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            memory_provider=fake_memory,
        )

        memory = next(item for item in context.tool_use_decisions if item.tool_name == "memory_recall")
        self.assertEqual(memory.status, "executed_withheld")
        self.assertNotIn("exact stabilization reports", memory.evidence)
        self.assertIn("grounding=unattributed", memory.evidence)
        self.assertIn("status=executed_withheld", context.system_context)
        self.assertIn(
            "the tool ran but its result was deliberately excluded",
            context.system_context,
        )
        self.assertIn("[Retrieved Memory]", context.system_context)
        self.assertNotIn("exact stabilization reports", context.system_context)
        self.assertIn("MEMORY_DATA_RULE:", context.system_context)
        self.assertIn("MEMORY_CONFIRMATION_RULE:", context.system_context)
        self.assertIn("MEMORY_WITHHELD_RULE:", context.system_context)
        self.assertEqual(context.memory_receipt["state"], "withheld")
        self.assertEqual(context.memory_receipt["groundingState"], "unattributed")
        self.assertEqual(context.memory_receipt["usePolicy"], "memory.context-use.v1")
        self.assertEqual(context.memory_receipt["confirmOnlyItemCount"], 0)
        self.assertTrue(context.memory_receipt["promptMemoryWithheld"])
        self.assertEqual(context.memory_receipt["withheldItemCount"], 1)
        self.assertTrue(context.memory_receipt["contentFree"])

    async def test_default_memory_busy_before_entry_uses_shared_position(
        self,
    ) -> None:
        @contextlib.contextmanager
        def busy_guard(*_args, **_kwargs):
            raise MemoryDeletionJournalBusyError()
            yield

        @contextlib.contextmanager
        def shared_guard(*_args, **_kwargs):
            yield TEST_DELETION_POSITION

        result = MemoryRecallResult(
            turn_id="fast-shared",
            ok=True,
            context_text="trusted fast shared context",
            metadata={
                "index_fresh": False,
                "read_only_fallback": True,
            },
        )
        recall_receipt = {
            "state": "provided",
            "groundingState": "attributed",
            "noteIds": ["note-fast-shared"],
            "indexFresh": False,
            "readOnlyFallback": True,
        }
        with patch.object(
            fast_contract,
            "memory_deletion_journal_guard",
            side_effect=busy_guard,
        ), patch.object(
            fast_contract,
            "memory_deletion_journal_read_guard",
            side_effect=shared_guard,
        ) as shared, patch(
            "evelyn_core.memory_vault.recall_memory_vault",
            return_value=result,
        ), patch(
            "evelyn_core.memory_vault.build_memory_recall_receipt",
            return_value=recall_receipt,
        ):
            context, receipt = (
                await fast_contract._default_memory_provider_result(
                    "memory shared fallback"
                )
            )

        self.assertEqual(context, "trusted fast shared context")
        self.assertFalse(receipt["indexFresh"])
        self.assertTrue(receipt["readOnlyFallback"])
        self.assertEqual(
            receipt["deletionBoundary"]["sequence"],
            TEST_DELETION_POSITION.sequence,
        )
        shared.assert_called_once()

    async def test_default_memory_busy_after_entry_does_not_retry_shared(
        self,
    ) -> None:
        @contextlib.contextmanager
        def writer_guard(*_args, **_kwargs):
            yield TEST_DELETION_POSITION

        with patch.object(
            fast_contract,
            "memory_deletion_journal_guard",
            side_effect=writer_guard,
        ), patch.object(
            fast_contract,
            "memory_deletion_journal_read_guard",
        ) as shared, patch(
            "evelyn_core.memory_vault.recall_memory_vault",
            side_effect=MemoryDeletionJournalBusyError(),
        ):
            with self.assertRaises(MemoryDeletionJournalBusyError):
                await fast_contract._default_memory_provider_result(
                    "memory busy body"
                )

        shared.assert_not_called()

    async def test_fast_memory_receipt_keeps_note_ids_without_memory_text(self) -> None:
        canonical_note_id = "opaque-" + ("a" * 64)

        async def grounded_memory(_text: str):
            return (
                "PRIVATE_GROUNDED_MEMORY",
                {
                    "state": "provided",
                    "groundingState": "attributed",
                    "memoryVersion": 9,
                    "retrievalMode": (
                        "PRIVATE retrieval-mode transcript canary"
                    ),
                    "noteIds": [
                        "note-2",
                        "note-1",
                        canonical_note_id,
                    ],
                    "sourceTypeCounts": {"user": 2},
                    "deletionBoundary": capture_test_deletion_boundary(),
                    "private": "MUST_NOT_SURVIVE",
                },
            )

        context = await build_fast_control_context(
            "memory previous preference?",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            memory_provider=grounded_memory,
        )

        self.assertEqual(context.memory_receipt["groundingState"], "attributed")
        self.assertEqual(context.memory_receipt["confirmOnlyItemCount"], 0)
        self.assertEqual(
            context.memory_receipt["retrievalMode"],
            "unknown",
        )
        self.assertNotIn(
            "PRIVATE retrieval-mode transcript canary",
            str(context.memory_receipt),
        )
        self.assertEqual(
            context.memory_receipt["suppliedNoteIds"],
            sorted(
                {
                    memory_deletion_ledger_note_id("note-1"),
                    memory_deletion_ledger_note_id("note-2"),
                    canonical_note_id,
                }
            ),
        )
        self.assertNotIn("note-1", str(context.memory_receipt))
        self.assertNotIn("note-2", str(context.memory_receipt))
        self.assertEqual(context.memory_receipt["sourceTypeCounts"], {"user": 2})
        self.assertEqual(
            context.memory_receipt["deletionBoundary"]["state"],
            "captured",
        )
        self.assertIs(
            context.memory_deletion_position,
            TEST_DELETION_POSITION,
        )
        self.assertIn("MEMORY_DATA_RULE:", context.system_context)
        self.assertNotIn("MEMORY_CONFIRMATION_RULE:", context.system_context)
        self.assertNotIn("PRIVATE_GROUNDED_MEMORY", str(context.memory_receipt))
        self.assertNotIn("MUST_NOT_SURVIVE", str(context.memory_receipt))

    async def test_memory_bearing_custom_provider_without_boundary_fails_closed(self) -> None:
        async def unguarded_memory(_text: str):
            return (
                "PRIVATE_UNGUARDED_MEMORY",
                {
                    "state": "provided",
                    "groundingState": "attributed",
                    "noteIds": ["note-unguarded"],
                },
            )

        with self.assertRaisesRegex(
            MemoryDeletionJournalIntegrityError,
            "^memory_deletion_journal_integrity_failed$",
        ):
            await build_fast_control_context(
                "memory previous preference?",
                source="control_page",
                runtime_health_provider=fake_runtime_health,
                memory_provider=unguarded_memory,
            )

    async def test_fast_memory_cannot_claim_attribution_without_evidence_ids(self) -> None:
        async def falsely_grounded_memory(_text: str):
            return (
                "PRIVATE_UNATTRIBUTED_MEMORY",
                {
                    "state": "provided",
                    "groundingState": "attributed",
                    "memoryVersion": 10,
                    "sourceTypeCounts": {"user": 1},
                },
            )

        context = await build_fast_control_context(
            "memory previous preference?",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            memory_provider=falsely_grounded_memory,
        )

        self.assertEqual(context.memory_receipt["groundingState"], "unattributed")
        self.assertEqual(context.memory_receipt["state"], "withheld")
        self.assertEqual(context.memory_receipt["confirmOnlyItemCount"], 0)
        self.assertTrue(context.memory_receipt["promptMemoryWithheld"])
        self.assertIn("MEMORY_CONFIRMATION_RULE:", context.memory_context)
        self.assertNotIn("PRIVATE_UNATTRIBUTED_MEMORY", context.memory_context)

    async def test_oversized_fast_memory_downgrades_grounding_before_prompt_trim(self) -> None:
        async def oversized_memory(_text: str):
            return (
                "PRIVATE_MEMORY_BLOCK " * 300,
                {
                    "state": "provided",
                    "groundingState": "attributed",
                    "memoryVersion": 10,
                    "noteIds": ["note-oversized"],
                    "sourceTypeCounts": {"user": 1},
                },
            )

        context = await build_fast_control_context(
            "memory previous preference?",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            memory_provider=oversized_memory,
        )

        self.assertLessEqual(len(context.memory_context), MEMORY_PROMPT_MAX_CHARS)
        self.assertIn("MEMORY_CONFIRMATION_RULE:", context.memory_context)
        self.assertIn("MEMORY_WITHHELD_RULE:", context.memory_context)
        self.assertNotIn("PRIVATE_MEMORY_BLOCK", context.memory_context)
        self.assertEqual(context.memory_receipt["state"], "withheld")
        self.assertEqual(context.memory_receipt["groundingState"], "unattributed")
        self.assertTrue(context.memory_receipt["promptTruncated"])
        self.assertTrue(context.memory_receipt["promptEvidenceDiscarded"])
        self.assertTrue(context.memory_receipt["promptMemoryWithheld"])
        self.assertEqual(context.memory_receipt["suppliedNoteIds"], [])
        self.assertEqual(context.memory_receipt["suppliedNoteCount"], 0)
        self.assertEqual(context.memory_receipt["preTruncationNoteCount"], 1)
        self.assertEqual(context.memory_receipt["withheldItemCount"], 1)
        self.assertEqual(context.memory_receipt["withheldNoteCount"], 1)
        self.assertEqual(context.memory_receipt["opaqueConfirmOnlyComponentCount"], 0)
        memory = next(
            item
            for item in context.tool_use_decisions
            if item.tool_name == "memory_recall"
        )
        self.assertIn("prompt_truncated=true", memory.evidence)

    async def test_fast_memory_failure_does_not_enter_prompt_or_evidence(self) -> None:
        async def failed_memory(_text: str):
            raise RuntimeError("PRIVATE_MEMORY_FAILURE")

        context = await build_fast_control_context(
            "memory previous preference?",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            memory_provider=failed_memory,
        )

        memory = next(item for item in context.tool_use_decisions if item.tool_name == "memory_recall")
        self.assertEqual(memory.status, "failed")
        self.assertEqual(memory.evidence, "memory_recall_runtime_error")
        self.assertEqual(context.memory_receipt["state"], "unavailable")
        self.assertNotIn("PRIVATE_MEMORY_FAILURE", context.system_context)

    async def test_fast_memory_integrity_failure_is_not_downgraded(self) -> None:
        async def failed_memory(_text: str):
            raise MemoryDeletionJournalIntegrityError(
                "PRIVATE_INTEGRITY_DETAIL"
            )

        with self.assertRaisesRegex(
            MemoryDeletionJournalIntegrityError,
            "^memory_deletion_journal_integrity_failed$",
        ):
            await build_fast_control_context(
                "memory previous preference?",
                source="control_page",
                runtime_health_provider=fake_runtime_health,
                memory_provider=failed_memory,
            )

    async def test_fast_main_llm_messages_include_context_pipeline_contract(self) -> None:
        messages = await build_fast_main_llm_messages(
            base_system_prompt="base prompt",
            recent_messages=[{"role": "assistant", "content": "previous"}],
            user_text="weather today?",
            final_user_text="final user text",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            search_provider=fake_search,
            memory_provider=fake_memory,
            local_bridge_status_provider=fake_local_bridge_status,
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("base prompt", messages[0]["content"])
        self.assertIn("[Tool Use Policy]", messages[0]["content"])
        self.assertIn("web_current_info", messages[0]["content"])
        self.assertIn("Weather Example", messages[0]["content"])
        self.assertEqual(messages[-1], {"role": "user", "content": "final user text"})

    async def test_fast_request_carries_internal_position_outside_model_json(self) -> None:
        async def grounded_memory(_text: str):
            return (
                "PRIVATE_GROUNDED_MEMORY",
                {
                    "state": "provided",
                    "groundingState": "attributed",
                    "noteIds": ["note-1"],
                    "deletionBoundary": capture_test_deletion_boundary(),
                },
            )

        request = await build_fast_main_llm_request(
            base_system_prompt="base",
            recent_messages=[],
            user_text="memory previous preference?",
            final_user_text="answer",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            memory_provider=grounded_memory,
        )

        serialized_messages = json.dumps(
            request.messages,
            ensure_ascii=False,
        )
        self.assertIs(
            request.memory_deletion_position,
            TEST_DELETION_POSITION,
        )
        self.assertNotIn(TEST_DELETION_POSITION.root_digest, serialized_messages)
        self.assertNotIn(
            TEST_DELETION_POSITION.position_digest,
            serialized_messages,
        )

    async def test_fast_request_combines_prebuilt_and_recalled_exposure(self) -> None:
        existing_note = "opaque-" + ("c" * 64)
        recalled_note = "opaque-" + ("d" * 64)
        capture_memory_exposure_position(
            MemoryExposurePosition(
                deletion_position=TEST_DELETION_POSITION,
                memory_version=9,
                supplied_note_ids=(existing_note,),
            )
        )

        async def grounded_memory(_text: str):
            return (
                "PRIVATE_GROUNDED_MEMORY",
                {
                    "state": "provided",
                    "groundingState": "attributed",
                    "memoryVersion": 9,
                    "noteIds": [recalled_note],
                    "deletionBoundary": capture_test_deletion_boundary(),
                },
            )

        try:
            request = await build_fast_main_llm_request(
                base_system_prompt="base",
                recent_messages=[
                    {"role": "assistant", "content": "prior bound reply"},
                ],
                user_text="memory previous preference?",
                final_user_text="answer",
                source="control_page",
                runtime_health_provider=fake_runtime_health,
                memory_provider=grounded_memory,
            )
        finally:
            reset_memory_exposure_position()

        self.assertEqual(
            request.memory_exposure_position,
            MemoryExposurePosition(
                deletion_position=TEST_DELETION_POSITION,
                memory_version=9,
                supplied_note_ids=tuple(
                    sorted((existing_note, recalled_note))
                ),
            ),
        )

    async def test_fast_main_preserves_unanswered_turn_and_adds_fixed_rule(self) -> None:
        private_text = "PRIVATE_FAST_UNANSWERED_TEXT"

        request = await build_fast_main_llm_request(
            base_system_prompt="base prompt",
            recent_messages=[
                {"role": "user", "content": private_text},
            ],
            user_text="continue",
            final_user_text="current request",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
        )

        self.assertTrue(request.context.unanswered_user_turn_context)
        self.assertIn(
            "continuity_schema: conversation.unanswered-user.v1",
            request.messages[0]["content"],
        )
        self.assertEqual(
            request.messages[-2:],
            [
                {"role": "user", "content": private_text},
                {"role": "user", "content": "current request"},
            ],
        )
        self.assertNotIn(private_text, request.context.system_context)

    async def test_fast_main_omits_rule_after_delivered_answer(self) -> None:
        request = await build_fast_main_llm_request(
            base_system_prompt="base prompt",
            recent_messages=[
                {"role": "user", "content": "previous"},
                {"role": "assistant", "content": "delivered"},
            ],
            user_text="continue",
            final_user_text="current request",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
        )

        self.assertFalse(request.context.unanswered_user_turn_context)
        self.assertNotIn(
            "conversation.unanswered-user.v1",
            request.messages[0]["content"],
        )

    async def test_screen_text_request_uses_live_host_evidence(self) -> None:
        context = await build_fast_control_context(
            "현재 화면에 보이는 글자를 읽어줘",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            vision_provider=fake_observed_vision,
        )

        by_name = {item.tool_name: item for item in context.tool_use_decisions}
        self.assertEqual(by_name["vision_capture_or_watch"].status, "executed")
        self.assertEqual(by_name["vision_ocr"].status, "executed")
        self.assertTrue(context.vision_evidence.evidence_available)
        self.assertTrue(context.vision_evidence.ocr_available)
        self.assertIn("Start voice validation", context.vision_context)
        self.assertIn("schema=vision.evidence.v2", context.system_context)
        self.assertIn(
            "supported_inline_tools=vision_capture_or_watch,vision_ocr",
            context.system_context,
        )
        self.assertNotIn(
            "unsupported_inline_tools=vision_capture_or_watch",
            context.system_context,
        )

    async def test_failed_screen_capture_never_marks_tool_executed(self) -> None:
        context = await build_fast_control_context(
            "현재 화면을 봐줘",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            vision_provider=fake_failed_vision,
        )

        vision = next(
            item
            for item in context.tool_use_decisions
            if item.tool_name == "vision_capture_or_watch"
        )
        self.assertEqual(vision.status, "failed_or_unavailable")
        self.assertIn("reason=black_frame", vision.evidence)
        self.assertFalse(context.vision_evidence.evidence_available)
        self.assertIn("observation was discarded", context.system_context)
        self.assertIn("화면을 확인할 수 없었어", context.required_evidence_failure_reply)

    async def test_stale_screen_content_is_removed_before_llm_context(self) -> None:
        context = await build_fast_control_context(
            "현재 화면을 봐줘",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            vision_provider=fake_stale_vision,
        )

        vision = next(
            item
            for item in context.tool_use_decisions
            if item.tool_name == "vision_capture_or_watch"
        )
        self.assertEqual(vision.status, "failed_or_unavailable")
        self.assertIn("reason=stale_visual_evidence", vision.evidence)
        self.assertNotIn("STALE_PRIVATE_SCREEN_CONTENT", context.system_context)
        self.assertIn("observation was discarded", context.system_context)

    async def test_missing_required_ocr_is_a_deterministic_pre_llm_gate(self) -> None:
        context = await build_fast_control_context(
            "현재 화면에서 가장 큰 제목과 보이는 버튼 하나만 말해줘.",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            vision_provider=fake_scene_only_vision,
        )

        by_name = {item.tool_name: item for item in context.tool_use_decisions}
        self.assertEqual(by_name["vision_capture_or_watch"].status, "executed")
        self.assertEqual(by_name["vision_ocr"].status, "failed_or_unavailable")
        self.assertIn("글자를 읽을 수 있는 근거", context.required_evidence_failure_reply)
        self.assertIn("추측하지 않을게", context.required_evidence_failure_reply)

    async def test_observed_required_ocr_does_not_gate_the_llm(self) -> None:
        context = await build_fast_control_context(
            "현재 화면에서 가장 큰 제목과 보이는 버튼 하나만 말해줘.",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            vision_provider=fake_observed_vision,
        )

        self.assertEqual(context.required_evidence_failure_reply, "")

    async def test_exact_window_title_is_copied_from_accessibility_evidence(self) -> None:
        context = await build_fast_control_context(
            "현재 Windows 화면의 창 제목만 정확히 말해줘.",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            vision_provider=fake_accessibility_vision,
        )

        self.assertEqual(context.required_evidence_failure_reply, "")
        self.assertEqual(
            context.grounded_evidence_reply,
            "Minecraft 26.2 - 싱글플레이",
        )

    async def test_non_accessibility_ocr_does_not_bypass_the_llm(self) -> None:
        context = await build_fast_control_context(
            "현재 Windows 화면의 창 제목만 정확히 말해줘.",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            vision_provider=fake_observed_vision,
        )

        self.assertEqual(context.required_evidence_failure_reply, "")
        self.assertEqual(context.grounded_evidence_reply, "")

    async def test_non_vision_turn_does_not_touch_host_capture(self) -> None:
        called = False

        async def forbidden_provider(user_text: str, *, run_ocr: bool) -> HostVisionResult:
            nonlocal called
            called = True
            raise AssertionError("vision provider should not be called")

        context = await build_fast_control_context(
            "이 문서를 읽고 요약해줘",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            vision_provider=forbidden_provider,
        )

        self.assertFalse(called)
        self.assertEqual(context.vision_context, "")
        self.assertEqual(context.vision_evidence.reason_code, "not_requested")


if __name__ == "__main__":
    unittest.main()
