from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_search_runtime import (  # noqa: E402
    ControlPageSearchRuntimeDeps,
    answer_control_page_search_text_from_runtime,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MEMORY_DELETION_POSITION_SCHEMA,
    MemoryDeletionJournalIntegrityError,
    MemoryDeletionPosition,
)
from evelyn_core.memory_exposure import (  # noqa: E402
    MemoryExposurePosition,
    capture_memory_exposure_position,
)
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)


NOTE_A = "concept-0123456789abcdef"
NOTE_B = "concept-fedcba9876543210"


def memory_exposure() -> MemoryExposurePosition:
    return MemoryExposurePosition(
        deletion_position=MemoryDeletionPosition(
            schema=MEMORY_DELETION_POSITION_SCHEMA,
            root_digest="1" * 64,
            sequence=1,
            position_digest="2" * 64,
        ),
        memory_version=7,
        supplied_note_ids=(NOTE_A,),
    )


def _deps(**overrides) -> tuple[ControlPageSearchRuntimeDeps, dict[str, object]]:
    state: dict[str, object] = {
        "history": [],
        "active": [],
        "tts": [],
        "route": [],
        "search": [],
        "synthesis": [],
        "events": [],
        "commitTargets": [],
        "locks": {},
    }

    async def execute_search_then_answer_action(**kwargs):
        state["search"].append(kwargs)
        return SimpleNamespace(answer_text="search answer")

    async def synthesize_tool_result_with_main_llm(**kwargs):
        state["synthesis"].append(kwargs)
        kwargs["metrics"]["meta"]["context_pipeline"] = {
            "memory_receipt": {
                "schema": "memory.context-receipt.v1",
                "state": "not_requested",
                "memoryVersion": 0,
                "contentFree": True,
            }
        }
        return "final answer"

    def get_session_lock(session_key: str) -> asyncio.Lock:
        locks = state["locks"]
        if session_key not in locks:
            locks[session_key] = asyncio.Lock()
        return locks[session_key]

    async def commit_session_continuity(*args):
        state["events"].append("commit")
        state["commitTargets"].append(args)
        return durable_continuity_status(4)

    deps = ControlPageSearchRuntimeDeps(
        control_page_effective_guild_id=lambda guild: int(getattr(guild, "id", 999) or 999),
        control_page_session_key=lambda guild_id: f"control:{guild_id}",
        get_conversation_history=lambda **kwargs: [{"role": "user", "content": "recent"}],
        memory_index_dir=REPO_ROOT / "unused-memory-index",
        build_route_decision=lambda **kwargs: state["route"].append(kwargs) or SimpleNamespace(**kwargs),
        monotonic=lambda: 12.5,
        execute_search_then_answer_action=execute_search_then_answer_action,
        synthesize_tool_result_with_main_llm=synthesize_tool_result_with_main_llm,
        clean_text=lambda text: text.strip(),
        get_session_lock=get_session_lock,
        append_history=lambda *args, **kwargs: (
            state["events"].append("history"),
            state["history"].append((args, kwargs)),
        )[-1],
        mark_session_active=lambda *args, **kwargs: (
            state["events"].append("active"),
            state["active"].append((args, kwargs)),
        )[-1],
        commit_session_continuity=commit_session_continuity,
        active_conversation_text_sec=30.0,
        build_topic_id=lambda *texts: "topic:" + "|".join(texts),
        schedule_local_control_tts=lambda *args, **kwargs: (
            state["events"].append("tts"),
            state["tts"].append((args, kwargs)),
        )[-1],
        current_turn_id=lambda session_key: f"turn:{session_key}",
        format_display_text=lambda text, **_kwargs: f"display:{text}",
        fallback_answer_for=lambda text: f"fallback:{text}",
        log=lambda *args, **kwargs: None,
    )
    if overrides:
        deps = ControlPageSearchRuntimeDeps(**{**deps.__dict__, **overrides})
    return deps, state


class ControlPageSearchRuntimeTests(unittest.TestCase):
    async def _run(self, deps: ControlPageSearchRuntimeDeps) -> str:
        return await answer_control_page_search_text_from_runtime(SimpleNamespace(id=7), "오늘 날씨", deps=deps)

    def test_search_answer_runs_search_synthesis_and_records_session_state(self) -> None:
        deps, state = _deps()

        reply = asyncio.run(self._run(deps))

        self.assertEqual(reply, "display:final answer")
        self.assertEqual(state["route"][0]["route"], "search_executor")
        self.assertEqual(state["route"][0]["needs_search"], True)
        self.assertEqual(state["search"][0]["session_key"], "control:7")
        self.assertEqual(state["synthesis"][0]["tool_result_text"], "search answer")
        self.assertEqual(state["synthesis"][0]["metrics"]["meta"]["selected_path"], "control_page_search_direct")
        self.assertEqual(state["history"][0][0], ("control:7", "오늘 날씨", "final answer"))
        self.assertEqual(state["active"][0][1]["topic_id"], "topic:오늘 날씨|search_executor|final answer")
        self.assertEqual(state["tts"][0][0], ("final answer",))
        self.assertEqual(state["tts"][0][1]["turn_id"], "turn:control:7")
        self.assertEqual(state["events"], ["history", "active", "commit", "tts"])
        self.assertEqual(
            state["commitTargets"],
            [("control:7", "turn:control:7")],
        )
        self.assertEqual(
            state["synthesis"][0]["metrics"]["meta"]["continuity_generation"],
            4,
        )
        self.assertEqual(
            state["history"][0][1]["memory_receipt"]["state"],
            "not_used",
        )

    def test_search_answer_falls_back_to_action_result_when_synthesis_is_empty(self) -> None:
        async def synthesize_tool_result_with_main_llm(**kwargs):
            kwargs["metrics"]["meta"]["context_pipeline"] = {
                "memory_receipt": {
                    "schema": "memory.context-receipt.v1",
                    "state": "not_requested",
                    "memoryVersion": 0,
                    "contentFree": True,
                }
            }
            return "   "

        deps, _state = _deps(synthesize_tool_result_with_main_llm=synthesize_tool_result_with_main_llm)

        reply = asyncio.run(self._run(deps))

        self.assertEqual(reply, "display:search answer")

    def test_partial_commit_status_is_not_marked_durable(
        self,
    ) -> None:
        private = (
            "Bearer search-continuity-secret "
            "https://internal.example/private"
        )

        async def partial_commit(*_args):
            return {
                "state": "ready",
                "rollbackProtected": True,
                "privateMessage": private,
            }

        deps, state = _deps(
            commit_session_continuity=partial_commit
        )

        reply = asyncio.run(self._run(deps))
        metrics = state["synthesis"][0]["metrics"]["meta"]

        self.assertEqual(reply, "display:final answer")
        self.assertEqual(
            metrics["continuity_commit"],
            "failed",
        )
        self.assertEqual(
            metrics["continuity_error"],
            "conversation_continuity_commit_failed",
        )
        self.assertNotIn(private, str(metrics))

    def test_bound_receipt_note_mismatch_reaches_no_sink(self) -> None:
        async def synthesize_with_mismatched_boundary(**kwargs):
            capture_memory_exposure_position(memory_exposure())
            kwargs["metrics"]["meta"]["context_pipeline"] = {
                "memory_receipt": {
                    "schema": CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
                    "state": "bound",
                    "memoryVersion": 7,
                    "suppliedNoteIds": [NOTE_B],
                    "suppliedNoteCount": 1,
                    "contentFree": True,
                }
            }
            return "private mismatched reply"

        deps, state = _deps(
            synthesize_tool_result_with_main_llm=(
                synthesize_with_mismatched_boundary
            )
        )

        with self.assertRaises(MemoryDeletionJournalIntegrityError):
            asyncio.run(self._run(deps))

        self.assertEqual(state["history"], [])
        self.assertEqual(state["active"], [])
        self.assertEqual(state["commitTargets"], [])
        self.assertEqual(state["tts"], [])
        self.assertEqual(state["events"], [])


if __name__ == "__main__":
    unittest.main()
