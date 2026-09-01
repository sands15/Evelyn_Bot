from __future__ import annotations

import hashlib
import json
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
    TASK_ROUTE_EVIDENCE_MAX_CHARS,
    TASK_LOOP_VERIFIED_MUTATION_OUTCOME,
    ask_llm_once_from_runtime,
    task_loop_completed_evidence,
)
from evelyn_core.task_grounded_draft_runtime import (  # noqa: E402
    GROUNDED_DRAFT_SCHEMA,
    grounded_evidence_fragments,
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
        self.skill_calls: list[dict] = []

    async def prepare_route_context(self, _user_text: str, **_kwargs):
        return ([{"role": "system", "content": "system"}], {"mood": "calm"}, self.route_decision, {}, self.awaiting_user_reply)

    async def maybe_execute_registered_route(self, **_kwargs):
        self.skill_calls.append(_kwargs)
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

    async def test_skill_route_answer_becomes_typed_evidence_for_one_main_call(self) -> None:
        self.skill_answer = "스킬 답변"
        metrics: dict = {}

        result = await ask_llm_once_from_runtime(
            "질문",
            deps=self.build_deps(),
            guild_id=11,
            session_key="session-1",
            metrics=metrics,
        )

        self.assertEqual(result, "display:원본 답변 sanitized resolved limited")
        self.assertEqual(len(self.execute_calls), 1)
        self.assertEqual(self.session_updates, [])
        evidence_message = self.payloads[0]["messages"][-1]
        self.assertEqual(evidence_message["role"], "user")
        evidence = json.loads(evidence_message["content"])
        self.assertEqual(evidence["schema"], "evelyn.specialist-evidence.v1")
        self.assertEqual(evidence["kind"], "registered_route_result")
        self.assertEqual(evidence["route"], "main_direct")
        self.assertEqual(evidence["evidence"], "스킬 답변")
        self.assertEqual(metrics["meta"]["specialist_evidence_finalizer"]["finalizer"], "main_llm")
        self.assertEqual(self.skill_calls[0]["allow_internal_routes"], {"search_executor"})

    async def test_skill_route_evidence_is_bounded(self) -> None:
        self.skill_answer = "시작" + ("가" * 3_000) + "끝"

        await ask_llm_once_from_runtime("질문", deps=self.build_deps())

        evidence = json.loads(self.payloads[0]["messages"][-1]["content"])
        self.assertEqual(len(evidence["evidence"]), 2_000)
        self.assertNotIn("끝", evidence["evidence"])

    async def test_task_loop_status_and_last_verification_survive_evidence_bound(self) -> None:
        self.skill_answer = json.dumps(
            {
                "schema": "evelyn.task-loop.v1",
                "taskId": "task-1",
                "status": "completed",
                "code": "task_completed",
                "summary": "검증 완료",
                "stepCount": 6,
                "modelCallCount": 7,
                "approvalTool": "",
                "observations": [
                    {
                        "step": index,
                        "tool": "workspace_list",
                        "verified": True,
                        "outcome": "success",
                        "code": "final-verification" if index == 6 else "ok",
                        "summary": "s" * 500,
                        "evidence": "e" * 1_000,
                    }
                    for index in range(1, 7)
                ],
            },
            ensure_ascii=False,
        )

        result = await ask_llm_once_from_runtime("질문", deps=self.build_deps())

        self.assertEqual(result, "display:원본 답변 sanitized resolved limited")
        self.assertEqual(len(self.execute_calls), 1)
        finalizer_guidance = self.payloads[0]["messages"][-2]["content"]
        self.assertIn("selected candidate-bound sandbox test receipt", finalizer_guidance)
        self.assertIn("never as proof of behavioral correctness", finalizer_guidance)
        self.assertIn("same-path SHA post-read", finalizer_guidance)
        self.assertIn("Never claim that all tests passed", finalizer_guidance)
        envelope = json.loads(self.payloads[0]["messages"][-1]["content"])
        task_evidence = json.loads(envelope["evidence"])
        self.assertEqual(envelope["status"], "completed")
        self.assertEqual(task_evidence["status"], "completed")
        self.assertEqual(
            task_evidence["observations"][-1]["code"],
            "final-verification",
        )

    async def test_chunked_read_receipt_chain_bypasses_main_without_dropping_prefix(self) -> None:
        self.route_decision.route = "task_executor"
        contents = [f"CHUNK_{index}_" + ("x" * 1_800) for index in range(5)]
        raw = "".join(contents).encode("utf-8")
        sha256 = hashlib.sha256(raw).hexdigest()
        observations = []
        offset = 0
        for index, content in enumerate(contents):
            length = len(content.encode("utf-8"))
            evidence = json.dumps(
                {
                    "path": "docs/long.md",
                    "sha256": sha256,
                    "bytes": len(raw),
                    "offset": offset,
                    "length": length,
                    "nextOffset": offset + length,
                    "eof": index == 4,
                    "content": content,
                    "truncated": index < 4,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            observations.append(
                {
                    "step": index + 1,
                    "tool": "workspace_read",
                    "verified": True,
                    "outcome": "success",
                    "code": "workspace_read_completed",
                    "summary": "Workspace file chunk read.",
                    "evidence": evidence,
                }
            )
            offset += length
        self.skill_answer = json.dumps(
            {
                "schema": "evelyn.task-loop.v1",
                "taskId": "task-chunked-read",
                "status": "completed",
                "code": "task_completed",
                "summary": "파일 전체를 연속 청크로 읽었어.",
                "stepCount": 5,
                "modelCallCount": 0,
                "approvalTool": "",
                "observations": observations,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        result = await ask_llm_once_from_runtime(
            "/작업 docs/long.md를 읽어줘",
            deps=self.build_deps(),
        )

        self.assertEqual(self.payloads, [])
        self.assertEqual(self.execute_calls, [])
        encoded_preview = result.split("evidencePreviewHex=", 1)[1].rstrip(".")
        preview_bytes = bytes.fromhex(encoded_preview)
        self.assertIn(b"CHUNK_0_", preview_bytes)
        self.assertIn("previewTruncated=true", result)
        self.assertNotIn(b"CHUNK_4_", preview_bytes)

    def test_completed_task_contract_rejects_forged_terminal_shapes(self) -> None:
        valid_observation = {
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
        }
        base = {
            "schema": "evelyn.task-loop.v1",
            "taskId": "task-contract",
            "status": "completed",
            "code": "task_completed",
            "summary": "done",
            "stepCount": 1,
            "modelCallCount": 2,
            "approvalTool": "",
            "observations": [valid_observation],
        }
        self.assertTrue(
            task_loop_completed_evidence(
                json.dumps(base),
                goal="런타임 상태를 확인해줘",
            )
        )
        recovered = {
            **base,
            "stepCount": 2,
            "modelCallCount": 3,
            "observations": [
                {
                    "step": 1,
                    "tool": "worker",
                    "verified": True,
                    "outcome": "failed",
                    "code": "task_worker_response_invalid",
                    "summary": "invalid decision",
                    "evidence": "",
                },
                {**valid_observation, "step": 2},
            ],
        }
        self.assertTrue(
            task_loop_completed_evidence(
                json.dumps(recovered),
                goal="런타임 상태를 확인해줘",
            )
        )

        forged = []
        forged.append({**base, "observations": []})
        forged.append({key: value for key, value in base.items() if key != "stepCount"})
        forged.append({**base, "modelCallCount": -1})
        forged.append({**base, "modelCallCount": 1})
        forged.append({**base, "modelCallCount": 3})
        forged.append(
            {
                **base,
                "observations": [
                    {**valid_observation, "verified": False, "outcome": "failed"}
                ],
            }
        )
        forged.append(
            {
                **base,
                "observations": [{**valid_observation, "evidence": ""}],
            }
        )
        forged.append(
            {
                **base,
                "observations": [
                    {
                        **valid_observation,
                        "tool": "workspace_read",
                        "code": "workspace_read_completed",
                    }
                ],
            }
        )
        forged.append(
            {
                **base,
                "stepCount": 2,
                "modelCallCount": 3,
                "observations": [
                    {
                        "step": 1,
                        "tool": "workspace_edit",
                        "verified": True,
                        "outcome": "success",
                        "code": "workspace_edit_applied",
                        "summary": "applied",
                        "evidence": json.dumps(
                            {"path": "docs/file.md", "sha256": "a" * 64}
                        ),
                    },
                    {
                        "step": 2,
                        "tool": "workspace_read",
                        "verified": True,
                        "outcome": "success",
                        "code": "workspace_read_completed",
                        "summary": "read",
                        "evidence": json.dumps(
                            {
                                "path": "docs/file.md",
                                "sha256": "a" * 64,
                                "bytes": 1,
                                "offset": 0,
                                "length": 1,
                                "nextOffset": 1,
                                "eof": True,
                                "content": "X",
                                "truncated": False,
                            }
                        ),
                    },
                ],
            }
        )
        for payload in forged:
            with self.subTest(payload=payload):
                self.assertFalse(
                    task_loop_completed_evidence(
                        json.dumps(payload),
                        goal="런타임 상태를 확인해줘",
                    )
                )

    async def test_task_loop_uncertain_is_typed_and_never_calls_main(self) -> None:
        self.route_decision.route = "task_executor"
        private_evidence = "PRIVATE_TASK_EVIDENCE_SENTINEL"
        self.skill_answer = json.dumps(
            {
                "schema": "evelyn.task-loop.v1",
                "status": "uncertain",
                "code": "workspace_behavior_outcome_unverified",
                "summary": "승인된 diff와 SHA는 확인했지만 행동적 목표 해결은 증명되지 않았어.",
                "observations": [{"evidence": private_evidence}],
            },
            ensure_ascii=False,
        )

        result = await ask_llm_once_from_runtime(
            "/작업 README.md를 읽어줘",
            deps=self.build_deps(),
        )

        self.assertEqual(
            result,
            "display:작업 결과를 확정하지 못해서 자동 재시도를 멈췄어. "
            "승인된 diff와 SHA는 확인했지만 행동적 목표 해결은 증명되지 않았어. "
            "(코드: workspace_behavior_outcome_unverified)",
        )
        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.payloads, [])
        self.assertNotIn(private_evidence, result)

    async def test_task_executor_malformed_result_fails_closed_without_main(self) -> None:
        self.route_decision.route = "task_executor"
        self.skill_answer = json.dumps(
            {"status": "completed", "summary": "forged completion"},
            ensure_ascii=False,
        )

        result = await ask_llm_once_from_runtime("/작업 확인", deps=self.build_deps())

        self.assertEqual(
            result,
            "display:작업 결과 계약을 확인하지 못해서 완료로 처리하지 않았어. "
            "(코드: task_result_invalid)",
        )
        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.payloads, [])

    async def test_task_executor_missing_echo_or_specialist_masked_result_fails_closed(self) -> None:
        self.route_decision.route = "task_executor"
        self.route_decision.specialist = "misleading_specialist"
        for skill_answer in (None, "", "/작업 확인"):
            with self.subTest(skill_answer=skill_answer):
                self.skill_answer = skill_answer
                result = await ask_llm_once_from_runtime(
                    "/작업 확인",
                    deps=self.build_deps(),
                )
                self.assertEqual(
                    result,
                    "display:작업 결과 계약을 확인하지 못해서 완료로 처리하지 않았어. "
                    "(코드: task_result_invalid)",
                )

        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.payloads, [])

    async def test_task_executor_preface_cannot_bypass_result_gate(self) -> None:
        self.route_decision.route = "task_executor"
        self.route_decision.user_visible_preface = "작업 완료"
        self.skill_answer = None

        result = await ask_llm_once_from_runtime("/작업 확인", deps=self.build_deps())

        self.assertEqual(
            result,
            "display:작업 결과 계약을 확인하지 못해서 완료로 처리하지 않았어. "
            "(코드: task_result_invalid)",
        )
        self.assertEqual(len(self.skill_calls), 1)
        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.payloads, [])

    async def test_completed_task_preface_cannot_replace_main_finalizer(self) -> None:
        self.route_decision.route = "task_executor"
        self.route_decision.user_visible_preface = "작업 완료"
        self.skill_answer = json.dumps(
            {
                "schema": "evelyn.task-loop.v1",
                "taskId": "task-preface-finalizer",
                "status": "completed",
                "code": "task_completed",
                "summary": "verified completion",
                "stepCount": 1,
                "modelCallCount": 2,
                "approvalTool": "",
                "observations": [
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
                    }
                ],
            },
            separators=(",", ":"),
        )

        result = await ask_llm_once_from_runtime(
            "/작업 런타임 상태를 확인해줘",
            deps=self.build_deps(),
        )

        self.assertIn("overallState=down", result)
        self.assertIn("coreState=down", result)
        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.payloads, [])
        self.assertNotEqual(result, "display:작업 완료")

    async def test_grounded_draft_is_reviewable_terminal_without_main(self) -> None:
        self.route_decision.route = "task_executor"
        task_id = "task-main-grounded"
        source_body = "PRIVATE_MAIN_GROUNDED_SOURCE_BODY_SENTINEL"
        encoded = source_body.encode("utf-8")
        observation = {
            "step": 1,
            "tool": "workspace_read",
            "verified": True,
            "outcome": "success",
            "code": "workspace_read_completed",
            "summary": "verified read",
            "evidence": json.dumps(
                {
                    "path": "docs/source.md",
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                    "bytes": len(encoded),
                    "offset": 0,
                    "length": len(encoded),
                    "nextOffset": len(encoded),
                    "eof": True,
                    "content": source_body,
                    "truncated": False,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        fragment = grounded_evidence_fragments(task_id, [observation])[0]
        claim = "현재 실행 근거에 연결된 검토 대상 주장이다."
        self.skill_answer = json.dumps(
            {
                "schema": "evelyn.task-loop.v1",
                "taskId": task_id,
                "status": "grounded_draft_ready",
                "code": "grounded_draft_ready",
                "summary": "reviewable draft",
                "stepCount": 1,
                "modelCallCount": 2,
                "approvalTool": "",
                "observations": [observation],
                "groundedDraft": {
                    "schema": GROUNDED_DRAFT_SCHEMA,
                    "taskId": task_id,
                    "kind": "summarize",
                    "sections": [
                        {
                            "title": "핵심",
                            "claims": [
                                {
                                    "text": claim,
                                    "stepId": fragment.step_id,
                                    "evidenceRef": fragment.evidence_ref,
                                }
                            ],
                        }
                    ],
                    "semanticVerified": False,
                    "humanReviewRequired": True,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        result = await ask_llm_once_from_runtime(
            "/작업 docs/source.md 내용을 요약해줘",
            deps=self.build_deps(),
        )

        self.assertIn(claim, result)
        self.assertIn("docs/source.md", result)
        self.assertNotIn(source_body, result)
        self.assertIn("사람의 검토", result)
        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.payloads, [])

    async def test_completed_workspace_mutation_uses_bounded_outcome_without_main(self) -> None:
        self.route_decision.route = "task_executor"
        self.answer = "모든 버그를 고쳤고 전체 테스트가 통과했어."
        content = "X"
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.skill_answer = json.dumps(
            {
                "schema": "evelyn.task-loop.v1",
                "taskId": "task-mutation-finalizer",
                "status": "completed",
                "code": "task_completed",
                "summary": "모든 버그를 고쳤고 전체 테스트가 통과했어.",
                "stepCount": 2,
                "modelCallCount": 3,
                "approvalTool": "",
                "observations": [
                    {
                        "step": 1,
                        "tool": "workspace_edit",
                        "verified": True,
                        "outcome": "success",
                        "code": "workspace_edit_completed",
                        "summary": "applied",
                        "evidence": json.dumps(
                            {"path": "docs/file.md", "sha256": sha256},
                            separators=(",", ":"),
                        ),
                    },
                    {
                        "step": 2,
                        "tool": "workspace_read",
                        "verified": True,
                        "outcome": "success",
                        "code": "workspace_read_completed",
                        "summary": "read",
                        "evidence": json.dumps(
                            {
                                "path": "docs/file.md",
                                "sha256": sha256,
                                "bytes": 1,
                                "offset": 0,
                                "length": 1,
                                "nextOffset": 1,
                                "eof": True,
                                "content": content,
                                "truncated": False,
                            },
                            separators=(",", ":"),
                        ),
                    },
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        result = await ask_llm_once_from_runtime("/작업 파일 수정", deps=self.build_deps())

        self.assertEqual(result, f"display:{TASK_LOOP_VERIFIED_MUTATION_OUTCOME}")
        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.payloads, [])
        self.assertNotIn("모든 버그", result)
        self.assertNotIn("전체 테스트가 통과", result)

    async def test_task_loop_approval_is_typed_and_never_calls_main(self) -> None:
        self.route_decision.route = "task_executor"
        private_evidence = "PRIVATE_TASK_EVIDENCE_SENTINEL"
        self.skill_answer = json.dumps(
            {
                "schema": "evelyn.task-loop.v1",
                "taskId": "task-approval",
                "status": "awaiting_approval",
                "code": "task_tool_approval_required",
                "summary": "파일 변경 승인이 필요해",
                "approvalTool": "workspace_edit",
                "observations": [{"evidence": private_evidence}],
            },
            ensure_ascii=False,
        )

        result = await ask_llm_once_from_runtime(
            "/작업 README.md를 수정해줘",
            deps=self.build_deps(),
        )

        self.assertEqual(
            result,
            "display:작업을 계속하려면 별도 승인이 필요해. 파일 변경 승인이 필요해 "
            "승인 필요 도구: workspace_edit. (코드: task_tool_approval_required)",
        )
        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.payloads, [])
        self.assertEqual(self.resolve_calls, [])
        self.assertNotIn(private_evidence, result)

    async def test_task_loop_user_input_never_exposes_worker_completion_claim(self) -> None:
        self.route_decision.route = "task_executor"
        raw_worker_claim = "작업을 모두 완료했어."
        self.skill_answer = json.dumps(
            {
                "schema": "evelyn.task-loop.v1",
                "status": "awaiting_approval",
                "code": "task_user_input_required",
                "summary": raw_worker_claim,
                "observations": [],
            },
            ensure_ascii=False,
        )

        result = await ask_llm_once_from_runtime(
            "/작업 README.md를 수정해줘",
            deps=self.build_deps(),
        )

        self.assertEqual(result, "display:작업을 계속하려면 추가 입력이 필요해.")
        self.assertNotIn(raw_worker_claim, result)
        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.payloads, [])

    async def test_selected_specialist_name_labels_main_evidence(self) -> None:
        self.route_decision.specialist = "deep_reasoning"
        self.skill_answer = "전문 분석 증거"
        metrics: dict = {}

        await ask_llm_once_from_runtime("복잡한 질문", deps=self.build_deps(), metrics=metrics)

        evidence = json.loads(self.payloads[0]["messages"][-1]["content"])
        self.assertEqual(evidence["route"], "deep_reasoning")
        self.assertEqual(metrics["meta"]["specialist_evidence_finalizer"]["route"], "deep_reasoning")

    async def test_search_executor_keeps_existing_main_synthesis_without_second_call(self) -> None:
        self.route_decision.route = "search_executor"
        self.route_decision.specialist = "deep_reasoning"
        self.route_decision.user_visible_preface = "잠깐 찾을게"
        self.skill_answer = "검색을 Main이 종합한 답변"
        metrics: dict = {}

        result = await ask_llm_once_from_runtime(
            "검색 질문",
            deps=self.build_deps(),
            session_key="session-search",
            metrics=metrics,
        )

        self.assertEqual(result, "display:검색을 Main이 종합한 답변")
        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.session_updates[0][1]["answer_text"], "검색을 Main이 종합한 답변")
        self.assertEqual(
            metrics["meta"]["specialist_evidence_finalizer"]["finalizer"],
            "existing_main_synthesis",
        )

    async def test_policy_preface_short_circuits_main_llm(self) -> None:
        self.skill_answer = "스킬 증거"
        self.route_decision.user_visible_preface = "잠깐 확인할게"

        result = await ask_llm_once_from_runtime("질문", deps=self.build_deps(), session_key="session-2")

        self.assertEqual(result, "display:잠깐 확인할게")
        self.assertEqual(self.session_updates[0][1]["answer_text"], "잠깐 확인할게")
        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.skill_calls, [])
        self.assertIn("policy_len=", self.stages[-1][1]["extra"])

    async def test_required_evidence_failure_route_never_calls_main_llm(self) -> None:
        self.route_decision.route = "required_evidence_failure"
        self.route_decision.user_visible_preface = (
            "이번에는 답변에 필요한 근거를 확인하지 못했어. "
            "확인하지 못한 내용을 추측해서 답하지 않을게."
        )

        result = await ask_llm_once_from_runtime(
            "기억에서 찾아줘",
            deps=self.build_deps(),
            session_key="session-required-gate",
        )

        self.assertEqual(
            result,
            "display:" + self.route_decision.user_visible_preface,
        )
        self.assertEqual(self.execute_calls, [])
        self.assertEqual(self.skill_calls, [])

    async def test_full_main_path_reuses_assembled_context_and_records_question_trace(self) -> None:
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
        self.assertEqual(self.minecraft_calls, [])
        self.assertEqual(self.runtime_calls, [])
        self.assertEqual(self.payloads[0]["model_name"], "main-model")
        self.assertIn("유도된 질문", self.payloads[0]["final_user_text"])
        self.assertNotIn("{'online': True}", self.payloads[0]["final_user_text"])
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
