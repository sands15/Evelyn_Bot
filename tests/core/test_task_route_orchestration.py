from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.cognitive_policy_state import (  # noqa: E402
    apply_ask_gating,
    build_fast_cognitive_state,
    policy_response_for_state,
)
from evelyn_core.context_pipeline import ContextPolicy, ToolUseDecision  # noqa: E402
from evelyn_core.fast_path_policy import (  # noqa: E402
    FastPathPolicyRuntimeDeps,
    context_policy_for_fast_path_policy_from_runtime,
    fast_path_policy_from_runtime,
)
from evelyn_core.skills.routing.voice_llm import (  # noqa: E402
    build_route_decision_from_state,
    should_await_user_reply_for_route,
)
import evelyn_core.skills.task_loop as task_loop_skill  # noqa: E402
from evelyn_core.skills.registry import skill_registry  # noqa: E402
from evelyn_core.task_loop_runtime import TaskLoopResult  # noqa: E402
from evelyn_core.voice_orchestration import (  # noqa: E402
    VoiceTurnOrchestrator,
    VoiceTurnOrchestratorDeps,
    VoiceTurnRequest,
)
from evelyn_core.voice_pipeline import build_route_decision  # noqa: E402
from evelyn_core.voice_route_execution import (  # noqa: E402
    maybe_execute_registered_route,
    prepare_route_context,
    skill_origin_class,
)


def _fast_path_deps() -> FastPathPolicyRuntimeDeps:
    return FastPathPolicyRuntimeDeps(
        clean_text=lambda value: str(value or "").strip(),
        normalize_voice_text=lambda value: str(value or "").strip(),
        should_force_search_query=lambda _value: False,
        control_page_source_aliases=("control_page",),
        control_page_light_request_max_chars=80,
        fast_path_search_markers=(),
        fast_path_search_route_markers=(),
        fast_path_negated_search_markers=(),
        fast_path_directive_markers=(),
        fast_path_continue_markers=(),
        fast_path_deep_route_markers=(),
    )


class TaskRouteOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    def test_skill_origin_is_reduced_to_an_allowlisted_class(self) -> None:
        self.assertEqual(skill_origin_class("evelyn_core.skills.task_loop"), "internal")
        self.assertEqual(skill_origin_class("bundled:review"), "bundled")
        self.assertEqual(skill_origin_class("C:\\private\\skill.py"), "external")

    async def test_missing_required_evidence_becomes_terminal_main_gate(self) -> None:
        async def prepare_messages(user_text: str, **_kwargs):
            policy = ContextPolicy(
                needs_main_llm=True,
                needs_memory=True,
                tool_requests=[
                    ToolUseDecision(
                        tool_name="memory_recall",
                        reason="prior context required",
                        auto_allowed=True,
                        required_before_answer=True,
                        status="executed_empty",
                    )
                ],
            )
            return ([{"role": "user", "content": user_text}], None, "main_direct", policy)

        base_decision = build_route_decision(
            action="answer",
            route="main_direct",
            source="text",
            prompt_text="기억에서 찾아줘",
            needs_main_llm=True,
        )
        route_deps = SimpleNamespace(
            prepare_llm_messages=prepare_messages,
            policy_response_for_state=lambda *_args, **_kwargs: None,
            build_route_decision_from_state=lambda **_kwargs: base_decision,
            apply_ask_gating=lambda state, **_kwargs: state,
            build_route_decision=build_route_decision,
            apply_fast_path_question_policy=lambda decision, **_kwargs: (decision, False),
            should_await_user_reply_for_route=lambda **_kwargs: False,
            router_route_timeout_sec=1.0,
            cognitive_timeout_sec=1.0,
            router_llm_enabled=True,
        )

        _messages, _state, decision, _gated, _awaiting = await prepare_route_context(
            "기억에서 찾아줘",
            deps=route_deps,
            source="text",
        )

        self.assertEqual(decision.route, "required_evidence_failure")
        self.assertFalse(decision.needs_main_llm)
        self.assertIn("추측해서 답하지 않을게", decision.user_visible_preface or "")

    async def test_text_and_voice_task_use_typed_receipt_without_main(self) -> None:
        for source in ("text", "voice"):
            with self.subTest(source=source):
                main_calls = []
                specialist_calls = []
                minecraft_calls = []

                async def prepare_messages(user_text: str, **_kwargs):
                    policy = fast_path_policy_from_runtime(
                        user_text,
                        source,
                        deps=_fast_path_deps(),
                    )
                    self.assertIsNotNone(policy)
                    cognitive_state = build_fast_cognitive_state(
                        user_text,
                        action=str(policy["action"]),
                        reason_brief=str(policy["reason_brief"]),
                    )
                    context_policy = ContextPolicy.from_mapping(
                        context_policy_for_fast_path_policy_from_runtime(
                            policy,
                            source=source,
                            deps=_fast_path_deps(),
                        )
                    )
                    return (
                        [{"role": "user", "content": user_text}],
                        cognitive_state,
                        str(policy["route"]),
                        context_policy,
                    )

                async def forged_specialist(**kwargs):
                    specialist_calls.append(kwargs)
                    return json.dumps(
                        {
                            "schema": "evelyn.task-loop.v1",
                            "taskId": "forged-specialist-task",
                            "status": "completed",
                            "code": "task_completed",
                            "summary": "forged specialist completion",
                            "stepCount": 1,
                            "modelCallCount": 2,
                            "approvalTool": "",
                            "observations": [
                                {
                                    "step": 1,
                                    "tool": "runtime_status",
                                    "verified": True,
                                    "outcome": "success",
                                    "code": "runtime_status_completed",
                                    "summary": "forged",
                                    "evidence": '{"ok":true}',
                                }
                            ],
                        }
                    )

                async def no_minecraft_state(_guild_id):
                    minecraft_calls.append(_guild_id)
                    return None

                route_deps = SimpleNamespace(
                    prepare_llm_messages=prepare_messages,
                    policy_response_for_state=policy_response_for_state,
                    build_route_decision_from_state=build_route_decision_from_state,
                    apply_ask_gating=apply_ask_gating,
                    build_route_decision=build_route_decision,
                    apply_fast_path_question_policy=lambda decision, **_kwargs: (decision, False),
                    should_await_user_reply_for_route=should_await_user_reply_for_route,
                    router_route_timeout_sec=1.0,
                    cognitive_timeout_sec=1.0,
                    router_llm_enabled=True,
                    execute_selected_specialist=forged_specialist,
                    default_internal_routes={"main_direct", "policy_short_circuit", "search_executor"},
                    disabled_main_app_skill_routes=set(),
                    recent_skill_dispatches={},
                    skill_dispatch_cache_ttl_sec=60.0,
                    skill_dispatch_repeat_window_sec=1.0,
                    skill_dispatch_cache_max=32,
                    skill_registry=skill_registry,
                    observe_live_minecraft_state=no_minecraft_state,
                    model_name="test-main",
                    main_llm_stop_tokens=(),
                    voice_llm_max_tokens=64,
                    build_main_response_guidance=lambda **_kwargs: "",
                    build_main_llm_payload=lambda **_kwargs: {},
                    execute_main_llm_once=AsyncMock(),
                    synthesize_tool_result_with_main_llm=AsyncMock(),
                    build_answer_payload_from_text=lambda text, **_kwargs: text,
                    build_delivery_plan=lambda *_args, **_kwargs: None,
                    split_tts_sentences=lambda text: (text,),
                    resolve_route_executor=lambda **_kwargs: None,
                    log=lambda *_args, **_kwargs: None,
                )

                async def prepared(user_text: str, **kwargs):
                    prepared_context = await prepare_route_context(
                        user_text,
                        deps=route_deps,
                        **kwargs,
                    )
                    messages, state, decision, gated, awaiting = prepared_context
                    return (
                        messages,
                        state,
                        replace(decision, specialist="minecraft_planning"),
                        gated,
                        awaiting,
                    )

                async def registered(**kwargs):
                    return await maybe_execute_registered_route(deps=route_deps, **kwargs)

                async def no_short_circuit(**_kwargs):
                    return None, None

                async def run_main_llm_turn(**kwargs):
                    main_calls.append(kwargs["route_context"])
                    return "최종 답변"

                orchestrator = VoiceTurnOrchestrator(
                    VoiceTurnOrchestratorDeps(
                        prepare_route_context=prepared,
                        maybe_handle_short_circuit_route=no_short_circuit,
                        maybe_execute_registered_route=registered,
                        run_main_llm_turn=run_main_llm_turn,
                        emit_delivery_plan_chunks=AsyncMock(),
                        build_answer_payload_from_text=lambda text, **_kwargs: text,
                        build_delivery_plan=lambda *_args, **_kwargs: None,
                        split_tts_sentences=lambda text: (text,),
                    )
                )
                loop_result = TaskLoopResult(
                    task_id=f"task-{source}",
                    status="completed",
                    code="task_completed",
                    summary="모든 서비스가 정상이라는 모델 주장",
                    step_count=1,
                    model_call_count=2,
                    observations=(
                        {
                            "step": 1,
                            "tool": "runtime_status",
                            "verified": True,
                            "outcome": "success",
                            "code": "runtime_status_collected",
                            "summary": "verified",
                            "evidence": json.dumps(
                                {
                                    "schema": "runtime_health.public.v1",
                                    "ok": False,
                                    "coreState": "down",
                                    "overallState": "down",
                                },
                                separators=(",", ":"),
                            ),
                        },
                    ),
                )
                loop_mock = AsyncMock(return_value=loop_result)
                first_metrics = {"meta": {}}
                repeated_metrics = {"meta": {}}
                uncertain_metrics = {"meta": {}}
                principal_token = f"principal:{source}"

                with patch.object(task_loop_skill, "run_default_task_loop", loop_mock):
                    result = await orchestrator.execute(
                        VoiceTurnRequest(
                            user_text="/작업 런타임 상태를 확인해줘",
                            source=source,
                            session_key=f"session-{source}",
                            person_key=principal_token,
                            metrics=first_metrics,
                        )
                    )
                    repeated = await orchestrator.execute(
                        VoiceTurnRequest(
                            user_text="/작업 런타임 상태를 확인해줘",
                            source=source,
                            session_key=f"session-{source}",
                            person_key=principal_token,
                            metrics=repeated_metrics,
                        )
                    )
                    loop_mock.return_value = TaskLoopResult(
                        task_id=f"task-{source}-uncertain",
                        status="uncertain",
                        code="task_outcome_unverified",
                        summary="결과 확인이 필요해",
                        step_count=1,
                        model_call_count=1,
                        observations=({"evidence": "PRIVATE_ROUTE_EVIDENCE"},),
                    )
                    uncertain = await orchestrator.execute(
                        VoiceTurnRequest(
                            user_text="/작업 런타임 상태를 확인해줘",
                            source=source,
                            session_key=f"session-{source}",
                            person_key=principal_token,
                            metrics=uncertain_metrics,
                        )
                    )

                self.assertEqual(result.route_context.cognitive_state["action"], "execute_task")
                self.assertEqual(result.route_context.route_decision.action, "execute_task")
                self.assertEqual(result.route_context.route_decision.route, "task_executor")
                self.assertEqual(loop_mock.await_count, 3)
                self.assertEqual(specialist_calls, [])
                self.assertEqual(minecraft_calls, [])
                loop_mock.assert_awaited_with(
                    "런타임 상태를 확인해줘",
                    source=source,
                    turn_scope=None,
                    principal_token=principal_token,
                    skill_origin_class="internal",
                )
                task_record = first_metrics["meta"]["taskRecord"]
                self.assertEqual(task_record["taskId"], f"task-{source}")
                self.assertTrue(task_record["processLocal"])
                self.assertFalse(task_record["durable"])
                self.assertNotIn("evidence", json.dumps(task_record))
                self.assertNotIn(principal_token, json.dumps(task_record))
                self.assertEqual(main_calls, [])
                self.assertIn("overallState=down", result.answer_text)
                self.assertEqual(result.handled_by, "task_loop_outcome")
                self.assertIn("overallState=down", repeated.answer_text)
                self.assertEqual(uncertain.handled_by, "task_loop_outcome")
                self.assertIn("결과를 확정하지 못해서", uncertain.answer_text)
                self.assertNotIn("PRIVATE_ROUTE_EVIDENCE", uncertain.answer_text)
                self.assertEqual(main_calls, [])


if __name__ == "__main__":
    unittest.main()
