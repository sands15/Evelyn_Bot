from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.main_llm_runtime import (  # noqa: E402
    task_loop_completed_evidence,
    task_loop_grounded_draft_evidence,
    task_loop_terminal_outcome,
)
from evelyn_core.task_grounded_draft_runtime import (  # noqa: E402
    GROUNDED_DRAFT_SCHEMA,
    grounded_evidence_fragments,
    validate_grounded_draft,
)
from evelyn_core.task_loop_runtime import (  # noqa: E402
    TaskGrant,
    TaskLoopDeps,
    TaskLoopResult,
    TaskStepReceipt,
    run_task_loop_from_runtime,
)


TASK_ID = "task-grounded-integration"
SOURCE_PATH = "docs/source.md"
SOURCE_BODY = "PRIVATE_GROUNDED_SOURCE_BODY_SENTINEL"
CLAIM_TEXT = "현재 실행에서 읽은 자료를 바탕으로 만든 검토 대상 주장이다."


def _grant(*, max_steps: int = 3) -> TaskGrant:
    return TaskGrant(
        task_id=TASK_ID,
        grant_id="grant-grounded-integration",
        source="control_page",
        auto_tools=frozenset({"workspace_read", "web_search"}),
        approval_tools=frozenset(),
        forbidden_tools=frozenset({"unrestricted_shell"}),
        issued_at=10.0,
        expires_at=1_000.0,
        max_steps=max_steps,
        deadline_sec=120.0,
    )


def _read_evidence() -> dict:
    encoded = SOURCE_BODY.encode("utf-8")
    length = len(encoded)
    return {
        "path": SOURCE_PATH,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": length,
        "offset": 0,
        "length": length,
        "nextOffset": length,
        "eof": True,
        "content": SOURCE_BODY,
        "truncated": False,
    }


def _receipt(kwargs: dict, evidence: dict) -> TaskStepReceipt:
    return TaskStepReceipt(
        step_id=kwargs["step_id"],
        tool=kwargs["tool"],
        attempted=True,
        executed=True,
        observed=True,
        verified=True,
        outcome="success",
        code="workspace_read_completed",
        summary="verified read",
        evidence=json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
        action_run_id=kwargs["action_run_id"],
        grant_id=kwargs["grant_id"],
        verification_evidence=evidence,
    )


def _draft_from_manifest(manifest: dict, *, evidence_ref: str | None = None) -> dict:
    fragment = manifest["fragments"][0]
    return {
        "schema": GROUNDED_DRAFT_SCHEMA,
        "taskId": manifest["taskId"],
        "kind": manifest["kind"],
        "sections": [
            {
                "title": "핵심",
                "claims": [
                    {
                        "text": CLAIM_TEXT,
                        "stepId": fragment["stepId"],
                        "evidenceRef": evidence_ref or fragment["evidenceRef"],
                    }
                ],
            }
        ],
        "semanticVerified": False,
        "humanReviewRequired": True,
    }


def _serialized_observation(
    *,
    step: int,
    tool: str,
    code: str,
    evidence: dict,
) -> dict:
    return {
        "step": step,
        "tool": tool,
        "verified": True,
        "outcome": "success",
        "code": code,
        "summary": "verified",
        "evidence": json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
    }


class TaskGroundedDraftIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_loop_returns_reviewable_draft_without_completed_false_green(
        self,
    ) -> None:
        worker_states: list[dict] = []

        async def decide_next(state: dict) -> dict:
            worker_states.append(copy.deepcopy(state))
            manifest = state.get("groundedEvidence")
            if manifest is None:
                return {
                    "type": "tool",
                    "tool": "workspace_read",
                    "args": {"path": SOURCE_PATH},
                }
            self.assertEqual(state["observations"][0]["evidence"], "")
            self.assertNotIn(SOURCE_BODY, json.dumps(state["observations"]))
            return {
                "type": "grounded_draft",
                "draft": _draft_from_manifest(manifest),
            }

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            self.assertEqual(kwargs["tool"], "workspace_read")
            self.assertEqual(kwargs["args"], {"path": SOURCE_PATH})
            return _receipt(kwargs, _read_evidence())

        goal = f"{SOURCE_PATH} 내용을 요약해줘"
        result = await run_task_loop_from_runtime(
            goal,
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(),
        )

        self.assertEqual(
            (result.status, result.code),
            ("grounded_draft_ready", "grounded_draft_ready"),
        )
        self.assertFalse(result.completed)
        self.assertEqual((result.step_count, result.model_call_count), (1, 2))
        self.assertEqual(len(worker_states), 2)
        self.assertIsNotNone(result.grounded_draft)
        assert result.grounded_draft is not None
        self.assertFalse(result.grounded_draft.to_dict()["semanticVerified"])
        self.assertTrue(result.grounded_draft.to_dict()["humanReviewRequired"])
        self.assertEqual(
            result.public_task_record()["status"],
            "grounded_draft_ready",
        )

        evidence = result.evidence_text()
        rendered = task_loop_terminal_outcome(evidence, goal=goal)
        self.assertIsNotNone(rendered)
        assert rendered is not None
        self.assertIn(CLAIM_TEXT, rendered)
        self.assertIn(SOURCE_PATH, rendered)
        self.assertNotIn(SOURCE_BODY, rendered)
        self.assertTrue(task_loop_grounded_draft_evidence(evidence, goal=goal))
        self.assertFalse(task_loop_completed_evidence(evidence, goal=goal))

    async def test_fabricated_reference_never_falls_back_to_completed(self) -> None:
        async def decide_next(state: dict) -> dict:
            manifest = state.get("groundedEvidence")
            if manifest is None:
                return {
                    "type": "tool",
                    "tool": "workspace_read",
                    "args": {"path": SOURCE_PATH},
                }
            if state["step"] == 2:
                return {
                    "type": "grounded_draft",
                    "draft": _draft_from_manifest(
                        manifest,
                        evidence_ref="evref-" + "0" * 64,
                    ),
                }
            return {
                "type": "final",
                "summary": "검증됐다고 주장하는 자유문",
                "verified_step": 1,
            }

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return _receipt(kwargs, _read_evidence())

        goal = f"{SOURCE_PATH} 내용을 요약해줘"
        result = await run_task_loop_from_runtime(
            goal,
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(max_steps=3),
        )

        self.assertEqual(
            (result.status, result.code),
            ("budget_exhausted", "task_max_steps_exhausted"),
        )
        self.assertFalse(result.completed)
        self.assertIsNone(result.grounded_draft)
        self.assertEqual(
            [item["code"] for item in result.observations],
            [
                "workspace_read_completed",
                "grounded_draft_reference_invalid",
                "task_verification_required",
            ],
        )

    def test_public_projection_revalidates_grounded_reference(self) -> None:
        read_observation = {
            "schema": "evelyn.task-observation.v1",
            **_serialized_observation(
                step=1,
                tool="workspace_read",
                code="workspace_read_completed",
                evidence=_read_evidence(),
            ),
            "attempted": True,
            "executed": True,
            "observed": True,
        }
        fragments = grounded_evidence_fragments(TASK_ID, [read_observation])
        draft = validate_grounded_draft(
            {
                "schema": GROUNDED_DRAFT_SCHEMA,
                "taskId": TASK_ID,
                "kind": "summarize",
                "sections": [
                    {
                        "title": "핵심",
                        "claims": [
                            {
                                "text": CLAIM_TEXT,
                                "stepId": fragments[0].step_id,
                                "evidenceRef": fragments[0].evidence_ref,
                            }
                        ],
                    }
                ],
                "semanticVerified": False,
                "humanReviewRequired": True,
            },
            task_id=TASK_ID,
            expected_kind="summarize",
            fragments=fragments,
        )
        result = TaskLoopResult(
            task_id=TASK_ID,
            status="grounded_draft_ready",
            code="grounded_draft_ready",
            summary="reviewable",
            step_count=2,
            model_call_count=3,
            observations=(
                {
                    "schema": "evelyn.task-observation.v1",
                    "step": 1,
                    "tool": "workspace_edit",
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "success",
                    "code": "workspace_edit_completed",
                    "summary": "mutation",
                    "evidence": "{}",
                },
                {**read_observation, "step": 2},
            ),
            grounded_draft=draft,
        )

        with self.assertRaises(ValueError):
            result.public_task_record()


class TaskGroundedDraftMainFinalizerTests(unittest.TestCase):
    def test_link_is_displayed_only_for_explicit_request_and_safe_web_receipt(
        self,
    ) -> None:
        source_body = "PRIVATE_WEB_SOURCE_BODY_SENTINEL"
        url = "https://example.com/source"
        observation = _serialized_observation(
            step=1,
            tool="web_search",
            code="web_search_completed",
            evidence={
                "query": "public test query",
                "results": [
                    {
                        "title": "Public source title",
                        "snippet": source_body,
                        "url": url,
                    }
                ],
            },
        )
        fragments = grounded_evidence_fragments(TASK_ID, [observation])
        draft = validate_grounded_draft(
            {
                "schema": GROUNDED_DRAFT_SCHEMA,
                "taskId": TASK_ID,
                "kind": "summarize",
                "sections": [
                    {
                        "title": "핵심",
                        "claims": [
                            {
                                "text": CLAIM_TEXT,
                                "stepId": fragments[0].step_id,
                                "evidenceRef": fragments[0].evidence_ref,
                            }
                        ],
                    }
                ],
                "semanticVerified": False,
                "humanReviewRequired": True,
            },
            task_id=TASK_ID,
            expected_kind="summarize",
            fragments=fragments,
        )
        result = TaskLoopResult(
            task_id=TASK_ID,
            status="grounded_draft_ready",
            code="grounded_draft_ready",
            summary="reviewable",
            step_count=1,
            model_call_count=2,
            observations=(observation,),
            grounded_draft=draft,
        )
        evidence = result.evidence_text()

        without_link = task_loop_terminal_outcome(evidence, goal="결과를 요약해줘")
        with_link = task_loop_terminal_outcome(
            evidence,
            goal="출처 링크를 포함해서 결과를 요약해줘",
        )

        self.assertIsNotNone(without_link)
        self.assertIsNotNone(with_link)
        assert without_link is not None and with_link is not None
        self.assertNotIn(url, without_link)
        self.assertIn(url, with_link)
        self.assertNotIn(source_body, without_link)
        self.assertNotIn(source_body, with_link)

    def test_stale_evidence_is_rejected_by_terminal_finalizer(self) -> None:
        observation = _serialized_observation(
            step=1,
            tool="workspace_read",
            code="workspace_read_completed",
            evidence=_read_evidence(),
        )
        fragments = grounded_evidence_fragments(TASK_ID, [observation])
        draft_value = {
            "schema": GROUNDED_DRAFT_SCHEMA,
            "taskId": TASK_ID,
            "kind": "summarize",
            "sections": [
                {
                    "title": "핵심",
                    "claims": [
                        {
                            "text": CLAIM_TEXT,
                            "stepId": 1,
                            "evidenceRef": fragments[0].evidence_ref,
                        }
                    ],
                }
            ],
            "semanticVerified": False,
            "humanReviewRequired": True,
        }
        payload = {
            "schema": "evelyn.task-loop.v1",
            "taskId": TASK_ID,
            "status": "grounded_draft_ready",
            "code": "grounded_draft_ready",
            "summary": "reviewable",
            "stepCount": 1,
            "modelCallCount": 2,
            "approvalTool": "",
            "observations": [observation],
            "groundedDraft": draft_value,
        }
        stale = copy.deepcopy(payload)
        stale_evidence = _read_evidence()
        stale_evidence["content"] = "different"
        stale_evidence["length"] = len("different")
        stale_evidence["bytes"] = len("different")
        stale_evidence["nextOffset"] = len("different")
        stale_evidence["sha256"] = hashlib.sha256(b"different").hexdigest()
        stale["observations"][0]["evidence"] = json.dumps(
            stale_evidence,
            separators=(",", ":"),
        )

        self.assertIsNone(
            task_loop_terminal_outcome(
                json.dumps(stale, ensure_ascii=False, separators=(",", ":")),
                goal="결과를 요약해줘",
            )
        )


if __name__ == "__main__":
    unittest.main()
