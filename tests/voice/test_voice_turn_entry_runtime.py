from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_turn_entry_runtime import (  # noqa: E402
    VoiceTurnEntryRuntimeDeps,
    ask_llm_streaming_from_runtime,
)


class FakeScope:
    def raise_if_cancelled(self) -> None:
        return None


class VoiceTurnEntryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.attached: list[object] = []
        self.detached: list[tuple[object, object]] = []
        self.main_requests: list[tuple[object, object, object]] = []
        self.failures: list[tuple[tuple, dict]] = []
        self.route_error: Exception | None = None

    async def prepare_route_context(self, *_args, **_kwargs):
        if self.route_error is not None:
            raise self.route_error
        route = SimpleNamespace(
            route="main_direct",
            needs_main_llm=True,
            needs_tts=True,
            user_visible_preface="",
        )
        return [], None, route, None, False

    async def short_circuit(self, **kwargs):
        return None, kwargs.get("on_first_chunk")

    async def registered_route(self, **_kwargs):
        return None

    async def run_main(self, *, request, route_context, on_first_chunk):
        self.main_requests.append((request, route_context, on_first_chunk))
        return "streamed answer"

    def build_deps(self) -> VoiceTurnEntryRuntimeDeps:
        def attach(scope):
            task = ("task", scope)
            self.attached.append(task)
            return task

        return VoiceTurnEntryRuntimeDeps(
            attach_current_task=attach,
            detach_task=lambda scope, task: self.detached.append((scope, task)),
            prepare_route_context=self.prepare_route_context,
            maybe_handle_short_circuit_route=self.short_circuit,
            maybe_execute_registered_route=self.registered_route,
            run_main_llm_turn=self.run_main,
            emit_delivery_plan_chunks=lambda *_args, **_kwargs: None,
            build_answer_payload_from_text=lambda text: text,
            build_delivery_plan=lambda *args, **kwargs: (args, kwargs),
            split_tts_sentences=lambda text, **_kwargs: ([text], ""),
            record_voice_pipeline_failure=lambda *args, **kwargs: self.failures.append((args, kwargs)),
        )

    async def test_builds_request_runs_orchestrator_and_detaches(self) -> None:
        scope = FakeScope()
        metrics = {"meta": {}}

        result = await ask_llm_streaming_from_runtime(
            "질문",
            deps=self.build_deps(),
            guild_id=77,
            session_key="session-1",
            room_key="room-1",
            person_key="person-1",
            session_memory_key="memory-1",
            source="voice",
            debug_text="debug",
            metrics=metrics,
            turn_scope=scope,
        )

        self.assertEqual(result, "streamed answer")
        request = self.main_requests[0][0]
        self.assertEqual(request.user_text, "질문")
        self.assertEqual(request.guild_id, 77)
        self.assertEqual(request.session_key, "session-1")
        self.assertEqual(request.source, "voice")
        self.assertIs(request.metrics, metrics)
        self.assertEqual(self.detached, [(scope, ("task", scope))])

    async def test_failure_is_recorded_and_task_is_detached(self) -> None:
        self.route_error = RuntimeError("route failed")
        scope = FakeScope()
        metrics: dict = {}

        with self.assertRaisesRegex(RuntimeError, "route failed"):
            await ask_llm_streaming_from_runtime(
                "질문",
                deps=self.build_deps(),
                metrics=metrics,
                turn_scope=scope,
            )

        args, kwargs = self.failures[0]
        self.assertEqual(args[0], "llm_failed")
        self.assertIs(args[2], metrics)
        self.assertEqual(kwargs["stage"], "ask_llm_streaming")
        self.assertEqual(len(self.detached), 1)

    def test_main_delegates_streaming_entry_to_runtime_module(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        start = source.index("async def ask_llm_streaming(")
        end = source.index("def start_streaming_voice_delivery", start)
        function_source = source[start:end]

        self.assertIn("ask_llm_streaming_from_runtime(", function_source)
        self.assertNotIn("VoiceTurnOrchestrator(", function_source)
        self.assertNotIn("record_voice_pipeline_failure(", function_source)


if __name__ == "__main__":
    unittest.main()
