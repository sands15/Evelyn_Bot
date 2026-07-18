from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.main_llm_runtime import (  # noqa: E402
    AskLlmOnceRuntimeDeps,
    ask_llm_once_from_runtime,
)


class AskLlmOnceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.route_decision = SimpleNamespace(
            route="main_direct",
            user_visible_preface="",
            prompt_text="",
            needs_runtime_state=True,
        )
        self.skill_answer: str | None = None
        self.awaiting_user_reply = True
        self.casual_turn = False
        self.answer = "원본 답변"
        self.answer_source = "content"
        self.stages: list[tuple[tuple, dict]] = []
        self.session_updates: list[tuple[tuple, dict]] = []
        self.minecraft_calls: list[int | None] = []
        self.runtime_calls: list[bool] = []
        self.payloads: list[dict] = []
        self.execute_calls: list[dict] = []
        self.resolve_calls: list[dict] = []
        self.question_traces: list[dict] = []

    async def prepare_route_context(self, _user_text: str, **_kwargs):
        return ([{"role": "system", "content": "system"}], {"mood": "calm"}, self.route_decision, {}, self.awaiting_user_reply)

    async def maybe_execute_registered_route(self, **_kwargs):
        return self.skill_answer

    async def observe_minecraft(self, guild_id: int | None):
        self.minecraft_calls.append(guild_id)
        return {"online": True}

    async def runtime_status(self, *, force: bool):
        self.runtime_calls.append(force)
        return {"ready": True}

    async def execute_main(self, **kwargs):
        self.execute_calls.append(kwargs)
        return self.answer, self.answer_source

    async def resolve_promised(self, **kwargs):
        self.resolve_calls.append(kwargs)
        return f"{kwargs['answer_text']} resolved"

    def build_deps(self) -> AskLlmOnceRuntimeDeps:
        return AskLlmOnceRuntimeDeps(
            log_voice_stage=lambda *args, **kwargs: self.stages.append((args, kwargs)),
            clean_text=lambda text: text.strip(),
            prepare_route_context=self.prepare_route_context,
            maybe_execute_registered_route=self.maybe_execute_registered_route,
            is_user_echo_answer=lambda user, answer: user.strip() == answer.strip(),
            update_session_state=lambda *args, **kwargs: self.session_updates.append((args, kwargs)),
            build_answer_payload_from_text=lambda text: SimpleNamespace(display_text=f"display:{text}"),
            session_is_casual_call_or_status_question=lambda _text: self.casual_turn,
            observe_live_minecraft_state=self.observe_minecraft,
            build_runtime_status_context=self.runtime_status,
            build_main_response_guidance=lambda cognitive, **kwargs: f"guidance:{cognitive['mood']}:{kwargs['minecraft_state']}",
            build_main_llm_payload=lambda **kwargs: self._capture_payload(kwargs),
            execute_main_llm_once=self.execute_main,
            sanitize_unrequested_minecraft_leak=lambda _prompt, answer: f"{answer} sanitized",
            resolve_promised_search_final_answer=self.resolve_promised,
            enforce_question_limits=lambda answer, _route: (f"{answer} limited", {"question_count": 1}),
            record_question_trace=lambda **kwargs: self.question_traces.append(kwargs),
            model_name="main-model",
            main_llm_chat_content_format="string",
            voice_llm_max_tokens=512,
            main_llm_stop_tokens=("STOP",),
        )

    def _capture_payload(self, kwargs: dict) -> dict:
        self.payloads.append(kwargs)
        return {"payload": True, **kwargs}

    async def test_skill_route_answer_short_circuits_main_llm_and_updates_session(self) -> None:
        self.skill_answer = "스킬 답변"

        result = await ask_llm_once_from_runtime(
            "질문",
            deps=self.build_deps(),
            guild_id=11,
            session_key="session-1",
        )

        self.assertEqual(result, "display:스킬 답변")
        self.assertEqual(self.session_updates[0][0], ("session-1",))
        self.assertEqual(self.session_updates[0][1]["answer_text"], "스킬 답변")
        self.assertTrue(self.session_updates[0][1]["awaiting_user_reply"])
        self.assertEqual(self.execute_calls, [])
        self.assertIn("skill_route=main_direct", self.stages[-1][1]["extra"])

    async def test_policy_preface_short_circuits_main_llm(self) -> None:
        self.route_decision.user_visible_preface = "잠깐 확인할게"

        result = await ask_llm_once_from_runtime("질문", deps=self.build_deps(), session_key="session-2")

        self.assertEqual(result, "display:잠깐 확인할게")
        self.assertEqual(self.session_updates[0][1]["answer_text"], "잠깐 확인할게")
        self.assertEqual(self.execute_calls, [])
        self.assertIn("policy_len=", self.stages[-1][1]["extra"])

    async def test_full_main_path_builds_context_and_records_question_trace(self) -> None:
        metrics = {"meta": {"question_cooldown_hit": True}}
        self.route_decision.prompt_text = "유도된 질문"

        result = await ask_llm_once_from_runtime(
            "원래 질문",
            deps=self.build_deps(),
            guild_id=22,
            session_key="session-3",
            source="voice",
            metrics=metrics,
        )

        self.assertEqual(result, "display:원본 답변 sanitized resolved limited")
        self.assertEqual(self.minecraft_calls, [22])
        self.assertEqual(self.runtime_calls, [True])
        self.assertEqual(self.payloads[0]["model_name"], "main-model")
        self.assertIn("유도된 질문", self.payloads[0]["final_user_text"])
        self.assertIn("{'online': True}", self.payloads[0]["final_user_text"])
        self.assertEqual(self.execute_calls[0]["user_text"], "원래 질문")
        self.assertEqual(self.resolve_calls[0]["answer_text"], "원본 답변 sanitized")
        self.assertTrue(self.question_traces[0]["cooldown_hit"])
        self.assertIn("answer_len=", self.stages[-1][1]["extra"])

    async def test_casual_turn_skips_minecraft_and_can_disable_question_trace(self) -> None:
        self.casual_turn = True
        self.answer_source = "fallback_empty_body"

        result = await ask_llm_once_from_runtime(
            "뭐 해?",
            deps=self.build_deps(),
            record_question_trace_enabled=False,
        )

        self.assertTrue(result.startswith("display:"))
        self.assertEqual(self.minecraft_calls, [])
        self.assertEqual(self.question_traces, [])
        self.assertIn("LLM canned reply 사용", self.stages[-1][0])

    async def test_echo_skill_and_preface_fall_through_to_main(self) -> None:
        self.skill_answer = "같은 말"
        self.route_decision.user_visible_preface = "같은 말"

        result = await ask_llm_once_from_runtime("같은 말", deps=self.build_deps())

        self.assertTrue(result.startswith("display:원본 답변"))
        self.assertEqual(len(self.execute_calls), 1)

    def test_main_delegates_once_orchestration_to_runtime_module(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "llm_route_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = source.index("async def ask_llm_once(")
        end = source.index("def resolve_route_executor(", start)
        function_source = source[start:end]

        self.assertIn("ask_llm_once_from_runtime(", function_source)
        self.assertNotIn("prepare_route_context(", function_source)
        self.assertNotIn("execute_main_llm_once(", function_source)


if __name__ == "__main__":
    unittest.main()
