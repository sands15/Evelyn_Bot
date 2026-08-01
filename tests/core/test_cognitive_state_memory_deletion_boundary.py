from __future__ import annotations

import asyncio
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.cognitive_state_runtime import (  # noqa: E402
    CognitiveStateRuntimeDeps,
    update_cognitive_state_from_runtime,
)
from evelyn_core import memory_deletion_journal as deletion_journal  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


class ContextTooLarge(RuntimeError):
    pass


class CognitiveStateMemoryDeletionBoundaryTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.writes: list[dict] = []
        self.detached: list[tuple[object, object]] = []
        self.logs: list[str] = []
        self.ask_calls: list[tuple[list[dict], dict]] = []
        self.environment = patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def build_deps(self, ask_router_llm) -> CognitiveStateRuntimeDeps:
        layers = {
            "guild": {
                "summary": "PRIVATE summary canary",
                "raw": [{"text": "PRIVATE raw canary"}],
                "facts": [{"text": "PRIVATE fact canary"}],
                "questions": [{"text": "PRIVATE question canary"}],
            }
        }
        return CognitiveStateRuntimeDeps(
            attach_current_task=lambda _scope: "task-token",
            detach_task=lambda scope, task: self.detached.append((scope, task)),
            cognitive_locks={},
            collect_memory_layers=lambda *_args, **_kwargs: layers,
            layered_summary_text=lambda _layers: "PRIVATE summary canary",
            normalize_cognitive_state=lambda value: dict(value),
            read_layered_cognitive_state=lambda *_args, **_kwargs: {
                "mood": "remembered"
            },
            get_matching_speculative_policy=lambda *_args: None,
            fast_path_policy=lambda *_args: None,
            session_state_snapshot=lambda _key: {},
            build_fast_cognitive_state=lambda *_args, **_kwargs: {},
            write_json_file=lambda _path, state: self.writes.append(dict(state)),
            cognitive_state_path=lambda *_args, **_kwargs: Path("state.json"),
            recent_memory_groups=lambda *_args, **_kwargs: {
                "raw": layers["guild"]["raw"],
                "facts": layers["guild"]["facts"],
                "questions": layers["guild"]["questions"],
            },
            memory_cognitive_raw_limit=8,
            build_cognitive_state_messages=lambda **_kwargs: [
                {"role": "system", "content": "PRIVATE primary memory"}
            ],
            ask_router_llm=ask_router_llm,
            cognitive_max_tokens=200,
            cognitive_timeout_sec=5.0,
            current_turn_id=lambda key: f"turn:{key}",
            is_context_size_error=lambda exc: isinstance(exc, ContextTooLarge),
            build_compact_cognitive_state_messages=lambda **_kwargs: [
                {"role": "system", "content": "PRIVATE compact memory"}
            ],
            should_log_voice_timing=lambda _elapsed: False,
            build_cognitive_fallback_state=lambda **_kwargs: {
                "action": "answer",
                "fallback": True,
            },
            finalize_cognitive_state=lambda result, **_kwargs: dict(result),
            log=self.logs.append,
        )

    async def test_background_compact_retry_carries_same_required_position(self) -> None:
        async def ask(messages, **kwargs):
            self.ask_calls.append((messages, kwargs))
            if len(self.ask_calls) == 1:
                raise ContextTooLarge("primary too large")
            return {"action": "answer", "confidence": 0.9}

        with TemporaryDirectory() as tmp:
            index_dir = Path(tmp) / "memory_index"
            task = asyncio.create_task(
                update_cognitive_state_from_runtime(
                    7,
                    "continue",
                    deps=self.build_deps(ask),
                    session_key="session-1",
                    memory_index_dir=index_dir,
                )
            )
            state = await task

        self.assertEqual(state["confidence"], 0.9)
        self.assertEqual(len(self.ask_calls), 2)
        first_position = self.ask_calls[0][1]["memory_deletion_position"]
        self.assertIsInstance(
            first_position,
            deletion_journal.MemoryDeletionPosition,
        )
        self.assertEqual(
            self.ask_calls[1][1]["memory_deletion_position"],
            first_position,
        )
        for _messages, kwargs in self.ask_calls:
            self.assertTrue(kwargs["memory_boundary_required"])
            self.assertEqual(kwargs["memory_deletion_index_dir"], index_dir)
        self.assertEqual(self.writes, [state])

    async def test_delete_after_response_blocks_cognitive_state_write(self) -> None:
        with TemporaryDirectory() as tmp:
            index_dir = Path(tmp) / "memory_index"

            async def ask(_messages, **_kwargs):
                deletion_journal.append_memory_deletion_tombstone(
                    index_dir,
                    {
                        "schema": deletion_journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                        "noteId": "concept-0123456789abcdef",
                        "noteType": "concept",
                        "sourceType": "conversation",
                        "reason": "privacy_request",
                        "deletedAt": "2026-08-01T00:00:00Z",
                    },
                )
                return {"action": "answer"}

            with self.assertRaises(
                deletion_journal.MemoryDeletionJournalIntegrityError
            ) as raised:
                await update_cognitive_state_from_runtime(
                    7,
                    "continue",
                    deps=self.build_deps(ask),
                    memory_index_dir=index_dir,
                )

        self.assertEqual(
            str(raised.exception),
            deletion_journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
        )
        self.assertEqual(self.writes, [])
        self.assertEqual(len(self.detached), 1)

    async def test_delete_after_failed_response_blocks_fallback_write(self) -> None:
        with TemporaryDirectory() as tmp:
            index_dir = Path(tmp) / "memory_index"

            async def ask(_messages, **_kwargs):
                deletion_journal.append_memory_deletion_tombstone(
                    index_dir,
                    {
                        "schema": deletion_journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                        "noteId": "concept-fedcba9876543210",
                        "noteType": "concept",
                        "sourceType": "conversation",
                        "reason": "privacy_request",
                        "deletedAt": "2026-08-01T00:00:01Z",
                    },
                )
                raise RuntimeError("router down")

            with self.assertRaises(
                deletion_journal.MemoryDeletionJournalIntegrityError
            ):
                await update_cognitive_state_from_runtime(
                    7,
                    "continue",
                    deps=self.build_deps(ask),
                    memory_index_dir=index_dir,
                )

        self.assertEqual(self.writes, [])

    async def test_delete_after_fast_path_memory_read_blocks_state_write(self) -> None:
        with TemporaryDirectory() as tmp:
            index_dir = Path(tmp) / "memory_index"

            async def ask(_messages, **_kwargs):
                self.fail("fast path must not call the router")

            def mutate_then_select_fast_policy(*_args):
                deletion_journal.append_memory_deletion_tombstone(
                    index_dir,
                    {
                        "schema": deletion_journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                        "noteId": "concept-0011223344556677",
                        "noteType": "concept",
                        "sourceType": "conversation",
                        "reason": "privacy_request",
                        "deletedAt": "2026-08-01T00:00:02Z",
                    },
                )
                return {
                    "action": "answer",
                    "reason_brief": "fast_path",
                }

            deps = replace(
                self.build_deps(ask),
                fast_path_policy=mutate_then_select_fast_policy,
            )
            with self.assertRaises(
                deletion_journal.MemoryDeletionJournalIntegrityError
            ):
                await update_cognitive_state_from_runtime(
                    7,
                    "continue",
                    deps=deps,
                    memory_index_dir=index_dir,
                )

        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
