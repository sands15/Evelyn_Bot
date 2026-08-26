from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.llm_route_runtime import classify_llm_route_from_runtime  # noqa: E402
from evelyn_core.main_llm_runtime import (  # noqa: E402
    ask_llm_once_from_runtime,
    resolve_promised_search_final_answer_from_runtime,
    synthesize_tool_result_with_main_llm_from_runtime,
    tool_synthesis_failure_reply,
)
from evelyn_core.observability_metrics import (  # noqa: E402
    VOICE_LATENCY_TRACE_METRICS_KEY,
    VoiceLatencyTrace,
)
from evelyn_core.question_shaping import enforce_question_limits  # noqa: E402
from evelyn_core.response_output_policy import (  # noqa: E402
    cleanup_assistant_display_artifacts,
    sanitize_model_output,
)
from evelyn_core.tts_playback import SpeechChunker  # noqa: E402
from evelyn_core.skills import skill_registry  # noqa: E402
from evelyn_core.voice_pipeline import RouteDecision  # noqa: E402
from evelyn_core.voice_pipeline import action_result_to_answer_payload  # noqa: E402
from evelyn_core.voice_orchestration import (  # noqa: E402
    VoiceTurnRequest,
    VoiceTurnRouteContext,
)
from evelyn_core.voice_route_execution import (  # noqa: E402
    execute_main_llm_streaming_turn,
    execute_search_then_answer_action,
    maybe_execute_registered_route,
    retry_main_llm_with_existing_plan,
)
from evelyn_core.voice_stream_chunks import (  # noqa: E402
    emit_stream_delta_chunks,
    flush_streamed_answer_chunks,
)


class _NoSkillRegistry:
    def find_by_route(self, *_args, **_kwargs):
        raise AssertionError("main_direct must not dispatch an internal skill")


class LlmCallBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def _run_core_voice_stream_policy_case(
        self,
        deltas: list[str],
        *,
        contains_minecraft_leak=lambda _text: False,
        sanitize_minecraft=lambda _user, answer: answer,
        sanitize_output=lambda value: value,
        max_question_count: int = 0,
    ) -> tuple[str, list[str], VoiceLatencyTrace]:
        rows = [f"delta-{index}".encode() for index in range(len(deltas))]
        decoded = dict(zip(rows, deltas))

        class Content:
            def __init__(self) -> None:
                self.rows = iter((*rows, b"done"))

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.rows)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        class Response:
            status = 200
            headers = {"Content-Type": "text/event-stream"}

            def __init__(self) -> None:
                self.content = Content()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        class Session:
            def post(self, *_args, **_kwargs):
                return Response()

        spoken: list[str] = []
        trace = VoiceLatencyTrace()
        deps = SimpleNamespace(
            model_name="main",
            llm_server_url="http://local.test/v1/chat/completions",
            memory_index_dir=Path.cwd(),
            main_llm_chat_content_format="string",
            voice_llm_max_tokens=64,
            main_llm_stop_tokens=(),
            get_http_session=lambda: _async_value(Session()),
            build_main_response_guidance=lambda *_args, **_kwargs: "guidance",
            mark_turn_stage=lambda *_args, **_kwargs: None,
            build_main_llm_payload=lambda **kwargs: kwargs,
            build_stream_speech_chunker=lambda **_kwargs: SpeechChunker(),
            user_explicitly_mentions_minecraft=lambda _text: False,
            extract_main_llm_answer_from_choice=lambda *_args, **_kwargs: ("", "", ""),
            sanitize_model_output=sanitize_output,
            parse_response_action_tag=lambda _value: None,
            extract_answer_from_reasoning=lambda *_args: "",
            execute_main_llm_once=lambda **_kwargs: _async_value(("", "")),
            resolve_promised_search_final_answer=lambda **kwargs: _async_value(
                kwargs["answer_text"]
            ),
            enforce_question_limits=enforce_question_limits,
            record_question_trace=lambda **_kwargs: None,
            emit_delivery_plan_chunks=lambda *_args, **_kwargs: _async_value(None),
            build_delivery_plan=lambda *_args, **_kwargs: None,
            build_answer_payload_from_text=lambda *_args, **_kwargs: None,
            split_tts_sentences=lambda *_args, **_kwargs: ([], ""),
            decode_sse_stream_line=lambda raw: (
                {"delta_text": decoded[raw]}
                if raw in decoded
                else {"done": True}
            ),
            answer_contains_minecraft_leak=contains_minecraft_leak,
            emit_stream_delta_chunks=emit_stream_delta_chunks,
            record_model_call_trace=lambda **_kwargs: None,
            sanitize_unrequested_minecraft_leak=sanitize_minecraft,
            flush_streamed_answer_chunks=flush_streamed_answer_chunks,
            increment_inflight_llm_requests=lambda: None,
            decrement_inflight_llm_requests=lambda: None,
            log=lambda *_args, **_kwargs: None,
        )

        async def on_sentence(value: str) -> None:
            spoken.append(value)

        answer = await execute_main_llm_streaming_turn(
            deps=deps,
            request=VoiceTurnRequest(
                user_text="일반 질문",
                source="voice",
                on_sentence=on_sentence,
                metrics={
                    "started_at": 0.0,
                    VOICE_LATENCY_TRACE_METRICS_KEY: trace,
                },
            ),
            route_context=VoiceTurnRouteContext(
                messages=[],
                cognitive_state=None,
                route_decision=RouteDecision(
                    action="ask",
                    route="main_direct",
                    source="voice",
                    prompt_text="일반 질문",
                    needs_tts=True,
                    max_question_count=max_question_count,
                ),
                gated_state=None,
                awaiting_user_reply=False,
            ),
            on_first_chunk=None,
        )
        return answer, spoken, trace

    async def test_core_stream_filters_split_unbacked_progress_before_real_sink(
        self,
    ) -> None:
        answer, spoken, trace = await self._run_core_voice_stream_policy_case(
            ["마이크 입력은 꺼져 있어. 추가로 확인해", "볼게."]
        )

        self.assertEqual(answer, "마이크 입력은 꺼져 있어.")
        self.assertEqual(spoken, ["마이크 입력은 꺼져 있어."])
        self.assertTrue(
            {
                "raw_first_token",
                "safe_first_delta",
                "speech_prefix_committed",
            }.issubset(trace.public_summary()["markers_ms"])
        )

    async def test_core_stream_late_question_policy_keeps_exact_spoken_prefix(
        self,
    ) -> None:
        safe_prefix = "먼저 충분히 안전한 설명을 줄게."
        answer, spoken, _trace = await self._run_core_voice_stream_policy_case(
            [safe_prefix + " ", "이제 다시 질문해도 될까?"]
        )

        self.assertEqual(answer, safe_prefix)
        self.assertEqual(spoken, [safe_prefix])

    async def test_core_stream_applies_visible_rewrites_before_real_sink(
        self,
    ) -> None:
        visible_policy = lambda value: sanitize_model_output(
            value,
            cleanup_artifacts_fn=cleanup_assistant_display_artifacts,
        )
        answer, spoken, _trace = await self._run_core_voice_stream_policy_case(
            [
                "[답",
                "변] 부르셨나요 😊. ",
                "Ready to tackle this directly now. 안전한 결론이야.",
            ],
            sanitize_output=visible_policy,
            max_question_count=1,
        )

        self.assertEqual(" ".join(spoken), answer)
        self.assertNotIn("부르셨나요", answer)
        self.assertNotIn("😊", answer)
        self.assertNotIn("Ready to tackle this directly now", answer)
        self.assertIn("불렀어?", answer)
        self.assertTrue(answer.endswith("안전한 결론이야."))

    async def test_core_stream_drops_late_minecraft_sentence_without_rewriting_prefix(
        self,
    ) -> None:
        safe_prefix = "첫 번째 문장은 충분히 안전한 설명이야."
        fallback = "안전한 대체 답변이야."
        answer, spoken, trace = await self._run_core_voice_stream_policy_case(
            [safe_prefix + " ", "마인크래프트 좌표는 1 2 3이야."],
            contains_minecraft_leak=lambda text: "마인크래프트" in text,
            sanitize_minecraft=lambda _user, answer: (
                fallback if "마인크래프트" in answer else answer
            ),
        )

        self.assertEqual(answer, safe_prefix)
        self.assertEqual(spoken, [safe_prefix])
        self.assertNotIn("마인크래프트", " ".join(spoken))
        self.assertIn(
            "safe_first_delta",
            trace.public_summary()["markers_ms"],
        )

    async def test_main_direct_uses_main_once_without_internal_skill_dispatch(self) -> None:
        main_calls: list[dict] = []
        route_decision = SimpleNamespace(
            route="main_direct",
            user_visible_preface="",
            prompt_text="",
            needs_runtime_state=False,
        )

        async def execute_main_llm_once(**kwargs):
            main_calls.append(kwargs)
            return "final answer", "content"

        async def execute_registered_route(**kwargs):
            return await maybe_execute_registered_route(
                deps=SimpleNamespace(
                    default_internal_routes={"main_direct", "policy_short_circuit"},
                    disabled_main_app_skill_routes=set(),
                    skill_registry=_NoSkillRegistry(),
                ),
                **kwargs,
            )

        deps = SimpleNamespace(
            model_name="main",
            main_llm_chat_content_format="text",
            main_llm_stop_tokens=(),
            voice_llm_max_tokens=64,
            prepare_route_context=lambda *_args, **_kwargs: _async_value(
                ([{"role": "system", "content": "system"}], None, route_decision, None, False)
            ),
            maybe_execute_registered_route=execute_registered_route,
            is_user_echo_answer=lambda *_args: False,
            update_session_state=lambda *_args, **_kwargs: None,
            session_is_casual_call_or_status_question=lambda _text: True,
            observe_live_minecraft_state=lambda _guild_id: _async_value(None),
            build_runtime_status_context=lambda **_kwargs: _async_value(""),
            build_main_response_guidance=lambda *_args, **_kwargs: "guidance",
            build_main_llm_payload=lambda **kwargs: kwargs,
            execute_main_llm_once=execute_main_llm_once,
            sanitize_unrequested_minecraft_leak=lambda _user, answer: answer,
            resolve_promised_search_final_answer=lambda **kwargs: _async_value(kwargs["answer_text"]),
            enforce_question_limits=lambda answer, _route: (answer, {}),
            record_question_trace=lambda **_kwargs: None,
            build_answer_payload_from_text=lambda text: SimpleNamespace(display_text=text),
            log_voice_stage=lambda *_args, **_kwargs: None,
            clean_text=lambda value: str(value or "").strip(),
        )

        answer = await ask_llm_once_from_runtime("hello", deps=deps)

        self.assertEqual(answer, "final answer")
        self.assertEqual(len(main_calls), 1)

    async def test_route_classifier_calls_route_purpose_once(self) -> None:
        router_calls: list[dict] = []

        async def ask_router_llm(_messages, **kwargs):
            router_calls.append(kwargs)
            return {
                "selected": "main_direct",
                "confidence": 0.9,
                "reason_brief": "direct",
                "context_policy": {"needs_memory": False},
                "tools": [],
                "specialist": "none",
            }

        deps = SimpleNamespace(
            classify_llm_route_fallback=lambda *_args, **_kwargs: "main_direct",
            fast_path_policy=lambda *_args, **_kwargs: None,
            session_state_snapshot=lambda _key: {},
            normalize_cognitive_state=lambda value: dict(value),
            format_memory_rows_for_llm=lambda *_args, **_kwargs: "",
            compact_memory_text=lambda text, **_kwargs: text,
            ask_router_llm=ask_router_llm,
            current_turn_id=lambda _key: "turn-1",
            clean_text=lambda value: str(value or "").strip(),
            normalize_question_policy_mapping=lambda *_args, **_kwargs: {},
            router_route_timeout_sec=8.0,
            cognitive_timeout_sec=10.0,
            router_llm_enabled=True,
            router_route_max_tokens=220,
            log=lambda *_args, **_kwargs: None,
        )

        route, meta = await classify_llm_route_from_runtime("complex request", deps=deps)

        self.assertEqual(route, "main_direct")
        self.assertEqual(meta["source"], "router")
        self.assertEqual(len(router_calls), 1)
        self.assertEqual(router_calls[0]["purpose"], "route")

    async def test_search_collects_evidence_without_intermediate_main_call(self) -> None:
        summary_calls: list[tuple] = []

        async def answer_from_search_results(*args, **kwargs):
            summary_calls.append((args, kwargs))
            return "must not be called"

        with tempfile.TemporaryDirectory() as temp_dir:
            result = await execute_search_then_answer_action(
                deps=SimpleNamespace(
                    memory_index_dir=Path(temp_dir),
                    build_search_query=lambda *_args, **_kwargs: "Qwen 3090",
                    search_duckduckgo=lambda _query: _async_value(
                        [{"title": "result", "snippet": "fits", "url": "https://example.test"}]
                    ),
                    answer_from_search_results=answer_from_search_results,
                ),
                guild_id=None,
                user_text="검색해줘",
            )
            empty_result = await execute_search_then_answer_action(
                deps=SimpleNamespace(
                    memory_index_dir=Path(temp_dir),
                    build_search_query=lambda *_args, **_kwargs: "empty query",
                    search_duckduckgo=lambda _query: _async_value([]),
                ),
                guild_id=None,
                user_text="검색해줘",
            )

        self.assertEqual(summary_calls, [])
        self.assertEqual(result.metadata["result_count"], 1)
        encoded = result.answer_text.split("evidencePreviewHex=", 1)[1].rstrip(".")
        evidence = json.loads(bytes.fromhex(encoded).decode("utf-8"))
        self.assertEqual(evidence["query"], "Qwen 3090")
        self.assertIn("외부 인용 데이터", result.answer_text)
        self.assertEqual(evidence["cards"][0]["excerpt"], "fits")
        spoken = action_result_to_answer_payload(result).spoken_text
        self.assertEqual(spoken, "검색 결과 1건을 화면에 정리했어.")
        self.assertNotIn("result", spoken)
        self.assertEqual(
            action_result_to_answer_payload(empty_result).spoken_text,
            "검색은 실행했지만 보여줄 결과를 받지 못했어.",
        )

    async def test_registered_search_skill_uses_exactly_one_main_finalizer(self) -> None:
        main_calls: list[dict] = []
        minecraft_calls: list[int | None] = []
        route_decision = RouteDecision(
            action="search_then_answer",
            route="search_executor",
            source="text",
            prompt_text="",
            user_visible_preface="",
            needs_search=True,
            needs_runtime_state=False,
            needs_memory=False,
        )

        async def synthesize_tool_result_with_main_llm(**kwargs):
            main_calls.append(kwargs)
            self.assertIn("외부 인용 데이터", kwargs["tool_result_text"])
            self.assertEqual(
                kwargs["tool_result_metadata"]["search_result_schema"],
                "evelyn.search-cards.v1",
            )
            return "main search final"

        async def observe_live_minecraft_state(guild_id):
            minecraft_calls.append(guild_id)
            return {"online": True}

        with tempfile.TemporaryDirectory() as temp_dir:
            deps = SimpleNamespace(
                model_name="main",
                main_llm_chat_content_format="text",
                main_llm_stop_tokens=(),
                voice_llm_max_tokens=64,
                memory_index_dir=Path(temp_dir),
                prepare_route_context=lambda *_args, **_kwargs: _async_value(
                    ([{"role": "system", "content": "system"}], None, route_decision, None, False)
                ),
                is_user_echo_answer=lambda *_args: False,
                update_session_state=lambda *_args, **_kwargs: None,
                session_is_casual_call_or_status_question=lambda _text: False,
                observe_live_minecraft_state=observe_live_minecraft_state,
                build_runtime_status_context=lambda **_kwargs: _async_value(""),
                build_main_response_guidance=lambda *_args, **_kwargs: "guidance",
                build_main_llm_payload=lambda **kwargs: kwargs,
                execute_main_llm_once=lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("search must not call a second Main finalizer")
                ),
                sanitize_unrequested_minecraft_leak=lambda _user, answer: answer,
                resolve_promised_search_final_answer=lambda **kwargs: _async_value(kwargs["answer_text"]),
                enforce_question_limits=lambda answer, _route: (answer, {}),
                record_question_trace=lambda **_kwargs: None,
                build_answer_payload_from_text=lambda text: SimpleNamespace(display_text=text),
                log_voice_stage=lambda *_args, **_kwargs: None,
                clean_text=lambda value: str(value or "").strip(),
                default_internal_routes={"main_direct", "policy_short_circuit", "search_executor"},
                disabled_main_app_skill_routes=set(),
                recent_skill_dispatches={},
                skill_dispatch_cache_ttl_sec=60.0,
                skill_dispatch_cache_max=10,
                skill_dispatch_repeat_window_sec=1.0,
                skill_registry=skill_registry,
                execute_selected_specialist=lambda **_kwargs: _async_value(None),
                build_search_query=lambda *_args, **_kwargs: "Qwen 3090",
                search_duckduckgo=lambda _query: _async_value(
                    [{"title": "result", "snippet": "fits", "url": "https://example.test"}]
                ),
                answer_from_search_results=lambda *_args, **_kwargs: _async_value("unused"),
                synthesize_tool_result_with_main_llm=synthesize_tool_result_with_main_llm,
                build_delivery_plan=lambda *_args, **_kwargs: None,
                split_tts_sentences=lambda _text: [],
                resolve_route_executor=lambda **_kwargs: None,
                log=lambda *_args, **_kwargs: None,
            )

            async def execute_registered_route(**kwargs):
                return await maybe_execute_registered_route(deps=deps, **kwargs)

            deps.maybe_execute_registered_route = execute_registered_route
            answer = await ask_llm_once_from_runtime("검색해줘", deps=deps)

        self.assertEqual(answer, "main search final")
        self.assertEqual(len(main_calls), 1)
        self.assertEqual(minecraft_calls, [])

    async def test_selected_specialist_calls_qwen_once_before_main_finalization(self) -> None:
        specialist_calls: list[dict] = []
        minecraft_observations: list[int | None] = []

        async def execute_selected_specialist(**kwargs):
            specialist_calls.append(kwargs)
            return "bounded specialist evidence"

        async def observe_live_minecraft_state(guild_id):
            minecraft_observations.append(guild_id)
            return {"online": True}

        result = await maybe_execute_registered_route(
            deps=SimpleNamespace(
                execute_selected_specialist=execute_selected_specialist,
                observe_live_minecraft_state=observe_live_minecraft_state,
                log=lambda *_args, **_kwargs: None,
            ),
            route_decision=SimpleNamespace(route="main_direct", specialist="deep_reasoning"),
            user_text="복잡한 비교를 해줘",
            source="text",
            guild_id=7,
            session_key="session-1",
            room_key=None,
            person_key=None,
            session_memory_key=None,
            debug_text=None,
            metrics={},
            cognitive_state=None,
            messages=[{"role": "user", "content": "복잡한 비교를 해줘"}],
        )

        self.assertEqual(result, "bounded specialist evidence")
        self.assertEqual(len(specialist_calls), 1)
        self.assertEqual(minecraft_observations, [])

    async def test_minecraft_specialist_reuses_assembled_context_without_observing_again(self) -> None:
        observed: list[int | None] = []
        specialist_calls: list[dict] = []

        async def observe(guild_id):
            observed.append(guild_id)
            return {"inventory": ["wood"]}

        async def execute(**kwargs):
            specialist_calls.append(kwargs)
            return "safe plan evidence"

        result = await maybe_execute_registered_route(
            deps=SimpleNamespace(
                execute_selected_specialist=execute,
                observe_live_minecraft_state=observe,
                log=lambda *_args, **_kwargs: None,
            ),
            route_decision=SimpleNamespace(route="main_direct", specialist="minecraft_planning"),
            user_text="안전한 계획을 세워줘",
            source="text",
            guild_id=9,
            session_key="session-2",
            room_key=None,
            person_key=None,
            session_memory_key=None,
            debug_text=None,
            metrics={},
            cognitive_state=None,
            messages=[
                {
                    "role": "system",
                    "content": "[Skill / Capability Context]\ninventory=wood",
                }
            ],
        )

        self.assertEqual(result, "safe plan evidence")
        self.assertEqual(observed, [])
        self.assertEqual(specialist_calls[0]["minecraft_state"], "")
        self.assertIn("inventory=wood", specialist_calls[0]["messages"][0]["content"])

    async def test_search_route_never_returns_specialist_evidence_as_final_answer(self) -> None:
        specialist_calls: list[dict] = []

        async def execute_specialist(**kwargs):
            specialist_calls.append(kwargs)
            return "raw qwen evidence"

        result = await maybe_execute_registered_route(
            deps=SimpleNamespace(
                execute_selected_specialist=execute_specialist,
                default_internal_routes={"search_executor"},
                disabled_main_app_skill_routes=set(),
                recent_skill_dispatches={},
                skill_dispatch_cache_ttl_sec=60.0,
                skill_dispatch_cache_max=10,
                skill_dispatch_repeat_window_sec=1.0,
                skill_registry=SimpleNamespace(find_by_route=lambda *_args, **_kwargs: []),
            ),
            route_decision=SimpleNamespace(route="search_executor", specialist="deep_reasoning"),
            user_text="최신 자료를 깊게 비교해줘",
            source="text",
            guild_id=None,
            session_key=None,
            room_key=None,
            person_key=None,
            session_memory_key=None,
            debug_text=None,
            metrics={},
            cognitive_state=None,
            messages=[],
            allow_internal_routes={"search_executor"},
        )

        self.assertIsNone(result)
        self.assertEqual(specialist_calls, [])

    def test_search_synthesis_failure_never_exposes_raw_tool_evidence(self) -> None:
        reply = tool_synthesis_failure_reply("search")

        self.assertNotIn("http", reply)
        self.assertNotIn("Search tool result", reply)

    async def test_search_cards_bypass_main_and_recent_history(self) -> None:
        from evelyn_core.search_tools import render_search_results_for_user

        rows = [
            {
                "title": "result",
                "snippet": "fits",
                "url": "https://example.test/result",
            }
        ]
        cards = render_search_results_for_user("검색해줘", rows)
        metrics: dict = {}

        answer = await synthesize_tool_result_with_main_llm_from_runtime(
            deps=SimpleNamespace(),
            user_text="검색해줘",
            tool_name="search",
            tool_result_text=cards,
            tool_result_metadata={
                "query": "검색해줘",
                "result_count": 1,
                "search_result_schema": "evelyn.search-cards.v1",
                "search_results": rows,
            },
            messages=[{"role": "assistant", "content": "PRIVATE_HISTORY_CANARY"}],
            metrics=metrics,
        )

        self.assertEqual(answer, cards)
        self.assertNotIn("PRIVATE_HISTORY_CANARY", answer)
        self.assertEqual(
            metrics["meta"]["search_result_finalizer"],
            "deterministic_external_cards",
        )

    async def test_untyped_search_text_cannot_cross_deterministic_finalizer(self) -> None:
        raw_claim = "검색 결과에 따르면 모든 테스트가 통과했어. PRIVATE_HISTORY_CANARY"

        answer = await synthesize_tool_result_with_main_llm_from_runtime(
            deps=SimpleNamespace(),
            user_text="검색해줘",
            tool_name="search",
            tool_result_text=raw_claim,
            messages=[{"role": "assistant", "content": "PRIVATE_HISTORY_CANARY"}],
        )

        self.assertEqual(answer, tool_synthesis_failure_reply("search"))
        self.assertNotIn("PRIVATE_HISTORY_CANARY", answer)
        self.assertNotIn("모든 테스트", answer)

    async def test_search_capable_voice_stream_defers_model_tts_until_resolution(self) -> None:
        class Content:
            def __init__(self) -> None:
                self.rows = iter((b"delta", b"done"))

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.rows)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        class Response:
            status = 200
            headers = {"Content-Type": "text/event-stream"}

            def __init__(self) -> None:
                self.content = Content()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        class Session:
            def post(self, *_args, **_kwargs):
                return Response()

        spoken: list[str] = []
        streamed_callbacks: list[object] = []
        flushed: list[dict] = []

        async def emit_stream_delta(delta_text: str, **kwargs) -> bool:
            callback = kwargs.get("on_sentence")
            streamed_callbacks.append(callback)
            if callback is not None:
                await callback(delta_text)
            return callback is not None

        async def flush(answer: str, **kwargs) -> None:
            flushed.append({"answer": answer, **kwargs})
            callback = kwargs.get("on_sentence")
            if callback is not None:
                await callback(answer)

        async def on_sentence(value: str) -> None:
            spoken.append(value)

        route_decision = RouteDecision(
            action="ask",
            route="main_direct",
            source="voice",
            prompt_text="검색해줘",
            needs_search=True,
            needs_tts=True,
        )
        deps = SimpleNamespace(
            model_name="main",
            llm_server_url="http://local.test/v1/chat/completions",
            memory_index_dir=Path.cwd(),
            main_llm_chat_content_format="string",
            voice_llm_max_tokens=64,
            main_llm_stop_tokens=(),
            get_http_session=lambda: _async_value(Session()),
            build_main_response_guidance=lambda *_args, **_kwargs: "guidance",
            mark_turn_stage=lambda *_args, **_kwargs: None,
            build_main_llm_payload=lambda **kwargs: kwargs,
            build_stream_speech_chunker=lambda **_kwargs: object(),
            user_explicitly_mentions_minecraft=lambda _text: False,
            extract_main_llm_answer_from_choice=lambda *_args, **_kwargs: ("", "", ""),
            sanitize_model_output=lambda value: value,
            parse_response_action_tag=lambda _value: None,
            extract_answer_from_reasoning=lambda *_args: "",
            execute_main_llm_once=lambda **_kwargs: _async_value(("", "")),
            resolve_promised_search_final_answer=None,
            enforce_question_limits=lambda answer, _route: (answer, {}),
            record_question_trace=lambda **_kwargs: None,
            emit_delivery_plan_chunks=lambda *_args, **_kwargs: _async_value(None),
            build_delivery_plan=lambda *_args, **_kwargs: None,
            build_answer_payload_from_text=lambda *_args, **_kwargs: None,
            split_tts_sentences=lambda *_args, **_kwargs: ([], ""),
            decode_sse_stream_line=lambda raw: (
                {"delta_text": "MODEL_PROMISE_TO_SEARCH"}
                if raw == b"delta"
                else {"done": True}
            ),
            answer_contains_minecraft_leak=lambda _answer: False,
            emit_stream_delta_chunks=emit_stream_delta,
            record_model_call_trace=lambda **_kwargs: None,
            sanitize_unrequested_minecraft_leak=lambda _user, answer: answer,
            flush_streamed_answer_chunks=flush,
            increment_inflight_llm_requests=lambda: None,
            decrement_inflight_llm_requests=lambda: None,
            log=lambda *_args, **_kwargs: None,
        )

        async def resolve_success(**kwargs):
            kwargs["metrics"].setdefault("meta", {})[
                "promised_search_resolution"
            ] = "completed"
            return "DETERMINISTIC_SEARCH_CARDS"

        deps.resolve_promised_search_final_answer = resolve_success

        latency_trace = VoiceLatencyTrace()
        first_metrics = {
            "started_at": 0.0,
            VOICE_LATENCY_TRACE_METRICS_KEY: latency_trace,
        }
        answer = await execute_main_llm_streaming_turn(
            deps=deps,
            request=VoiceTurnRequest(
                user_text="검색해줘",
                source="voice",
                on_sentence=on_sentence,
                metrics=first_metrics,
            ),
            route_context=VoiceTurnRouteContext(
                messages=[],
                cognitive_state=None,
                route_decision=route_decision,
                gated_state=None,
                awaiting_user_reply=False,
            ),
            on_first_chunk=None,
        )

        self.assertEqual(answer, "DETERMINISTIC_SEARCH_CARDS")
        self.assertEqual(streamed_callbacks, [None])
        self.assertEqual(spoken, ["검색 결과를 화면에 정리했어."])
        self.assertIsNone(flushed[0]["on_sentence"])
        self.assertNotIn("MODEL_PROMISE_TO_SEARCH", spoken)
        self.assertTrue(
            {
                "prompt_compiled",
                "main_admission_requested",
                "main_slot_acquired",
                "main_request_written",
                "main_headers_received",
                "raw_first_token",
                "safe_first_delta",
                "speech_prefix_committed",
            }.issubset(latency_trace.public_summary()["markers_ms"])
        )
        self.assertNotIn(
            "turn_completed",
            latency_trace.public_summary()["markers_ms"],
        )

        async def resolve_failure(**kwargs):
            kwargs["metrics"].setdefault("meta", {})[
                "promised_search_resolution"
            ] = "failed"
            return "SAFE_SEARCH_FAILURE"

        deps.resolve_promised_search_final_answer = resolve_failure
        spoken.clear()
        await execute_main_llm_streaming_turn(
            deps=deps,
            request=VoiceTurnRequest(
                user_text="검색해줘",
                source="voice",
                on_sentence=on_sentence,
                metrics={"started_at": 0.0},
            ),
            route_context=VoiceTurnRouteContext(
                messages=[],
                cognitive_state=None,
                route_decision=route_decision,
                gated_state=None,
                awaiting_user_reply=False,
            ),
            on_first_chunk=None,
        )
        self.assertEqual(spoken, ["SAFE_SEARCH_FAILURE"])
        self.assertNotIn("검색 결과를 화면에 정리했어.", spoken)

        async def resolve_empty(**kwargs):
            kwargs["metrics"].setdefault("meta", {})[
                "promised_search_resolution"
            ] = "completed_empty"
            return "EMPTY_SEARCH_RECEIPT"

        deps.resolve_promised_search_final_answer = resolve_empty
        spoken.clear()
        await execute_main_llm_streaming_turn(
            deps=deps,
            request=VoiceTurnRequest(
                user_text="검색해줘",
                source="voice",
                on_sentence=on_sentence,
                metrics={"started_at": 0.0},
            ),
            route_context=VoiceTurnRouteContext(
                messages=[],
                cognitive_state=None,
                route_decision=route_decision,
                gated_state=None,
                awaiting_user_reply=False,
            ),
            on_first_chunk=None,
        )
        self.assertEqual(
            spoken,
            ["검색은 실행했지만 보여줄 결과를 받지 못했어."],
        )

        leak_trace = VoiceLatencyTrace()
        deps.answer_contains_minecraft_leak = lambda _answer: True
        deps.sanitize_unrequested_minecraft_leak = (
            lambda _user, _answer: ""
        )
        deps.resolve_promised_search_final_answer = (
            lambda **_kwargs: _async_value("")
        )
        await execute_main_llm_streaming_turn(
            deps=deps,
            request=VoiceTurnRequest(
                user_text="일반 질문",
                source="voice",
                on_sentence=None,
                metrics={
                    "started_at": 0.0,
                    VOICE_LATENCY_TRACE_METRICS_KEY: leak_trace,
                },
            ),
            route_context=VoiceTurnRouteContext(
                messages=[],
                cognitive_state=None,
                route_decision=RouteDecision(
                    action="ask",
                    route="main_direct",
                    source="voice",
                    prompt_text="일반 질문",
                    needs_tts=True,
                ),
                gated_state=None,
                awaiting_user_reply=False,
            ),
            on_first_chunk=None,
        )
        leak_markers = leak_trace.public_summary()["markers_ms"]
        self.assertIn("raw_first_token", leak_markers)
        self.assertNotIn("safe_first_delta", leak_markers)
        self.assertNotIn("speech_prefix_committed", leak_markers)

    async def test_unplanned_promised_search_never_calls_external_tool(self) -> None:
        search_calls: list[dict] = []
        metrics: dict = {}

        async def execute_search(**kwargs):
            search_calls.append(kwargs)
            return SimpleNamespace(answer_text="raw search evidence")

        answer = await resolve_promised_search_final_answer_from_runtime(
            deps=SimpleNamespace(
                answer_promises_search=lambda _answer: True,
                has_negated_search_marker=lambda _text: False,
                execute_search_then_answer_action=execute_search,
            ),
            user_text="일반 질문",
            answer_text="찾아볼게",
            route_decision=SimpleNamespace(needs_search=False, tool_requests=()),
            metrics=metrics,
        )

        self.assertEqual(answer, "찾아볼게")
        self.assertEqual(search_calls, [])
        self.assertEqual(
            metrics["meta"]["promised_search_escalation_skipped"],
            "turn_plan_not_approved",
        )

    async def test_promised_search_marks_only_typed_receipts_completed(self) -> None:
        from evelyn_core.search_tools import render_search_results_for_user

        rows = [{"title": "result", "snippet": "fits", "url": ""}]
        rendered = render_search_results_for_user("query", rows)

        async def execute_valid(**_kwargs):
            return SimpleNamespace(
                answer_text=rendered,
                metadata={
                    "query": "query",
                    "result_count": 1,
                    "search_result_schema": "evelyn.search-cards.v1",
                    "search_results": rows,
                },
            )

        metrics: dict = {}
        answer = await resolve_promised_search_final_answer_from_runtime(
            deps=SimpleNamespace(
                answer_promises_search=lambda value: value == "찾아볼게",
                has_negated_search_marker=lambda _text: False,
                execute_search_then_answer_action=execute_valid,
            ),
            user_text="웹에서 찾아줘",
            answer_text="찾아볼게",
            route_decision=SimpleNamespace(needs_search=True, tool_requests=()),
            metrics=metrics,
        )

        self.assertEqual(answer, rendered)
        self.assertEqual(
            metrics["meta"]["promised_search_resolution"],
            "completed",
        )

        empty_rendered = render_search_results_for_user("query", [])

        async def execute_empty(**_kwargs):
            return SimpleNamespace(
                answer_text=empty_rendered,
                metadata={
                    "query": "query",
                    "result_count": 0,
                    "search_result_schema": "evelyn.search-cards.v1",
                    "search_results": [],
                },
            )

        empty_metrics: dict = {}
        empty_answer = await resolve_promised_search_final_answer_from_runtime(
            deps=SimpleNamespace(
                answer_promises_search=lambda value: value == "찾아볼게",
                has_negated_search_marker=lambda _text: False,
                execute_search_then_answer_action=execute_empty,
            ),
            user_text="웹에서 찾아줘",
            answer_text="찾아볼게",
            route_decision=SimpleNamespace(needs_search=True, tool_requests=()),
            metrics=empty_metrics,
        )
        self.assertEqual(empty_answer, empty_rendered)
        self.assertEqual(
            empty_metrics["meta"]["promised_search_resolution"],
            "completed_empty",
        )

    async def test_approved_search_second_synthesis_failure_is_content_free(self) -> None:
        async def execute_search(**_kwargs):
            return SimpleNamespace(
                answer_text="Search tool result. url=https://private.example/evidence",
                metadata={},
            )

        metrics: dict = {}
        with patch(
            "evelyn_core.main_llm_runtime.synthesize_tool_result_with_main_llm_from_runtime",
            new=AsyncMock(return_value="찾아볼게"),
        ):
            answer = await resolve_promised_search_final_answer_from_runtime(
                deps=SimpleNamespace(
                    answer_promises_search=lambda value: value == "찾아볼게",
                    has_negated_search_marker=lambda _text: False,
                    execute_search_then_answer_action=execute_search,
                ),
                user_text="웹에서 확인해줘",
                answer_text="찾아볼게",
                route_decision=SimpleNamespace(
                    needs_search=False,
                    tool_requests=(SimpleNamespace(tool_name="web_current_info"),),
                ),
                metrics=metrics,
            )

        self.assertEqual(answer, tool_synthesis_failure_reply("search"))
        self.assertEqual(metrics["meta"]["promised_search_resolution"], "failed")
        self.assertNotIn("private.example", answer)
        self.assertNotIn("Search tool result", answer)

    async def test_empty_stream_retry_reuses_payload_without_replanning(self) -> None:
        main_calls: list[dict] = []
        trace_calls: list[dict] = []
        inflight_events: list[str] = []
        metrics: dict = {}

        async def execute_main_llm_once(**kwargs):
            main_calls.append(kwargs)
            return "retry answer", "content"

        answer = await retry_main_llm_with_existing_plan(
            deps=SimpleNamespace(
                execute_main_llm_once=execute_main_llm_once,
                increment_inflight_llm_requests=lambda: inflight_events.append("increment"),
                decrement_inflight_llm_requests=lambda: inflight_events.append("decrement"),
                record_model_call_trace=lambda **kwargs: trace_calls.append(kwargs),
                model_name="main",
                llm_server_url="http://main.local",
            ),
            payload={"model": "main", "stream": True, "messages": [{"role": "user", "content": "same plan"}]},
            user_text="question",
            metrics=metrics,
        )

        self.assertEqual(answer, "retry answer")
        self.assertEqual(len(main_calls), 1)
        self.assertFalse(main_calls[0]["payload"]["stream"])
        self.assertEqual(main_calls[0]["payload"]["messages"][0]["content"], "same plan")
        self.assertFalse(metrics["meta"]["empty_stream_retry"]["router_replanned"])
        self.assertEqual(inflight_events, ["increment", "decrement"])
        self.assertEqual(trace_calls[0]["purpose"], "main_response_retry")
        self.assertTrue(trace_calls[0]["success"])


async def _async_value(value):
    return value


if __name__ == "__main__":
    unittest.main()
