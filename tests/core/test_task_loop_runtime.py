from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.task_loop_runtime import (
    _PendingWorkspaceEdit,
    _applied_mutation_awaits_workspace_read,
    _behavioral_mutation_evidence_matches,
    _completion_evidence_matches,
    _discard_pending_workspace_edit,
    _exact_workspace_read_args,
    _extract_json_object,
    _mutation_goal_is_exact_content,
    _normalize_decision,
    _task_receipt_from_result,
    TaskGrant,
    TaskLoopDeps,
    TaskLoopResult,
    TaskPlannerGuidance,
    TaskStepReceipt,
    SkillOriginClass,
    TASK_BASE_GUIDANCE_DIGEST,
    TASK_BASE_GUIDANCE_VERSION,
    TASK_MAX_GOAL_CHARS,
    TASK_EVAL_VERSION,
    TASK_PUBLIC_RECORD_SCHEMA,
    TASK_WORK_CONTRACT_SCHEMA,
    TASK_WORKER_INSTRUCTION_DIGEST,
    TASK_WORKSPACE_READ_CHUNK_BYTES,
    build_task_grant,
    build_task_worker_payload,
    execute_default_task_tool,
    parse_task_request,
    run_default_task_loop,
    run_task_loop_from_runtime,
    task_goal_is_grounded_read_only,
    validated_task_planner_guidance,
    validated_public_task_record,
)
from evelyn_core.task_approval_runtime import (
    TaskApprovalManager,
    TaskApprovalResolution,
)
from evelyn_core.fast_path_policy import (
    FastPathPolicyRuntimeDeps,
    context_policy_for_fast_path_policy_from_runtime,
    fast_path_policy_from_runtime,
)
from evelyn_core.main_llm_runtime import (
    TASK_LOOP_VERIFIED_MUTATION_OUTCOME,
    append_registered_route_evidence,
    task_loop_completed_evidence,
    task_loop_terminal_outcome,
)
from evelyn_core.route_fallback_policy import normalize_route_name
from evelyn_core.response_output_policy import format_display_text
from evelyn_core.turn_lifecycle import TurnScope
from evelyn_core.skills.base import SkillContext
from evelyn_core.skills.registry import skill_registry


def _grant(
    *,
    max_steps: int = 6,
    deadline_sec: float = 120.0,
    auto_tools: frozenset[str] | None = None,
    expires_at: float = 1_000.0,
) -> TaskGrant:
    return TaskGrant(
        task_id="task-test",
        grant_id="grant-test",
        source="control_page",
        auto_tools=(
            auto_tools
            if auto_tools is not None
            else frozenset({"workspace_read", "workspace_test"})
        ),
        approval_tools=frozenset({"service_restart"}),
        forbidden_tools=frozenset({"unrestricted_shell"}),
        issued_at=10.0,
        expires_at=expires_at,
        max_steps=max_steps,
        deadline_sec=deadline_sec,
    )


def _receipt(
    *,
    step_id: int,
    tool: str,
    action_run_id: str,
    grant_id: str,
    outcome: str,
    verified: bool,
    executed: bool = True,
    code: str | None = None,
    evidence: dict | None = None,
) -> TaskStepReceipt:
    evidence_payload = dict(evidence or {})
    receipt_code = code or {
        "runtime_status": "runtime_status_collected",
        "web_search": "web_search_completed",
        "workspace_read": "workspace_read_completed",
        "workspace_search": "workspace_search_completed",
        "workspace_list": "workspace_list_completed",
        "workspace_diff": "workspace_diff_completed",
        "workspace_test": "workspace_test_passed",
    }.get(tool, "ok")
    return TaskStepReceipt(
        step_id=step_id,
        tool=tool,
        attempted=True,
        executed=executed,
        observed=True,
        verified=verified,
        outcome=outcome,
        code=receipt_code,
        summary=receipt_code,
        evidence=json.dumps(evidence_payload, separators=(",", ":")),
        action_run_id=action_run_id,
        grant_id=grant_id,
        verification_evidence=evidence_payload,
    )


_READ_CONTENT = "verified content"
_READ_SHA256 = hashlib.sha256(_READ_CONTENT.encode("utf-8")).hexdigest()


def _read_evidence(path: str = "README.md", *, content: str = _READ_CONTENT) -> dict:
    length = len(content.encode("utf-8"))
    return {
        "path": path,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "bytes": length,
        "offset": 0,
        "length": length,
        "nextOffset": length,
        "eof": True,
        "content": content,
        "truncated": False,
    }


def _chunk_read_evidence(
    *,
    path: str,
    offset: int,
    content: str,
    total_bytes: int,
    sha256: str = "c" * 64,
) -> dict:
    length = len(content.encode("utf-8"))
    next_offset = offset + length
    return {
        "path": path,
        "sha256": sha256,
        "bytes": total_bytes,
        "offset": offset,
        "length": length,
        "nextOffset": next_offset,
        "eof": next_offset == total_bytes,
        "content": content,
        "truncated": next_offset != total_bytes,
    }


def _runtime_evidence() -> dict:
    return {
        "schema": "runtime_health.public.v1",
        "ok": True,
        "coreState": "up",
        "overallState": "up",
    }


def _test_evidence(args: dict) -> dict:
    return {
        "runner": args["runner"],
        "targets": list(args["targets"]),
        "stdout": "passed",
        "stderr": "",
        "exitCode": 0,
        "truncated": False,
    }


def _sandbox_test_evidence(
    args: dict,
    *,
    stage_id: str,
    candidate_sha: str,
    exit_code: int = 0,
) -> dict:
    return {
        "stageId": stage_id,
        "candidatePath": "README.md",
        "candidateSha256": candidate_sha,
        "runner": args["runner"],
        "targets": list(args["targets"]),
        "baseTreeSha256": "d" * 64,
        "candidateTreeSha256": "e" * 64,
        "imageId": "sha256:" + "f" * 64,
        "stdout": "passed" if exit_code == 0 else "failed",
        "stderr": "",
        "exitCode": exit_code,
        "testsRun": 1,
        "semanticVerified": False,
        "truncated": False,
    }


def _diff_evidence(paths: list[str]) -> dict:
    return {
        "paths": list(paths),
        "diff": "@@ -1 +1 @@\n-old\n+new",
        "stderr": "",
        "exitCode": 0,
        "truncated": False,
    }


def _approval_grant(
    *,
    max_steps: int = 6,
    deadline_sec: float = 120.0,
    expires_at: float = 1_000.0,
    auto_tools: frozenset[str] = frozenset({"workspace_read"}),
) -> TaskGrant:
    return TaskGrant(
        task_id="task-approval",
        grant_id="grant-approval",
        source="control_page",
        auto_tools=auto_tools,
        approval_tools=frozenset({"workspace_edit", "workspace_test"}),
        forbidden_tools=frozenset({"unrestricted_shell"}),
        issued_at=10.0,
        expires_at=expires_at,
        max_steps=max_steps,
        deadline_sec=deadline_sec,
    )


def _stage_evidence(
    args: dict,
    *,
    candidate_sha: str = _READ_SHA256,
    issued_at: float = 20.0,
    expires_at: float = 500.0,
) -> dict:
    from evelyn_core.workspace_task_tools import workspace_task_args_hash

    full_diff = "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n"
    value = {
        "stageId": "stage-approval",
        "hostInstanceId": "host-approval",
        "path": args["path"],
        "mode": args["mode"],
        "baseSha256": args.get("expectedSha256", "ABSENT"),
        "candidateSha256": candidate_sha,
        "diffSha256": hashlib.sha256(full_diff.encode()).hexdigest(),
        "fullDiff": full_diff,
        "diffTruncated": False,
        "gitStatus": "",
        "dirtyStatus": "clean" if args["mode"] == "replace" else "absent",
        "tracked": args["mode"] == "replace",
        "dirtyBaseAcknowledgementRequired": False,
        "bytes": 3,
        "issuedAt": issued_at,
        "expiresAt": expires_at,
        "argsHash": workspace_task_args_hash(args),
    }
    value["previewDigest"] = hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return value


def _stage_receipt(kwargs: dict, evidence: dict) -> TaskStepReceipt:
    return _receipt(
        step_id=kwargs["step_id"],
        tool=kwargs["tool"],
        action_run_id=kwargs["action_run_id"],
        grant_id=kwargs["grant_id"],
        outcome="success",
        verified=True,
        code="workspace_edit_staged",
        evidence=evidence,
    )


def _approved_edit_result(*, sha256: str = _READ_SHA256, outcome: str = "succeeded") -> dict:
    return {
        "attempted": True,
        "executed": outcome == "succeeded",
        "observed": True,
        "verified": True,
        "outcome": outcome,
        "code": "workspace_edit_completed" if outcome == "succeeded" else "workspace_edit_failed",
        "summary": "approved edit result",
        "evidence": {
            "path": "README.md",
            "sha256": sha256,
            "semanticVerified": False,
        },
    }


class TaskLoopRuntimeTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
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

    def test_parse_task_request_is_explicit(self) -> None:
        self.assertEqual(parse_task_request("/작업 테스트를 고쳐줘"), "테스트를 고쳐줘")
        self.assertEqual(parse_task_request("작업: 문서를 확인해줘"), "문서를 확인해줘")
        self.assertIsNone(parse_task_request("테스트를 고쳐줘"))

    def test_overlong_task_goal_fails_closed_without_scope_rebinding(self) -> None:
        base = "create file docs/x.txt with content `X`"
        exact_limit = (
            base
            + " " * (TASK_MAX_GOAL_CHARS - len(base) - 1)
            + "."
        )
        self.assertEqual(len(exact_limit), TASK_MAX_GOAL_CHARS)
        self.assertEqual(parse_task_request(f"/task {exact_limit}"), exact_limit)
        self.assertIsNone(
            parse_task_request(f"/task {'x' * (TASK_MAX_GOAL_CHARS + 1)}")
        )
        self.assertIsNone(
            parse_task_request(f"/task {exact_limit} and deploy it")
        )

        worker_payload = build_task_worker_payload(
            {"goal": "x" * (TASK_MAX_GOAL_CHARS + 1), "observations": []}
        )
        state_text = worker_payload["messages"][1]["content"].split("\n", 1)[1]
        self.assertEqual(json.loads(state_text)["goal"], "")

    async def test_direct_task_loop_rejects_overlong_goal_before_worker(self) -> None:
        overlong_goal = "x" * (TASK_MAX_GOAL_CHARS + 1)
        decide_next = AsyncMock()
        execute_tool = AsyncMock()
        result = await run_task_loop_from_runtime(
            overlong_goal,
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 0.0,
                wall_time=lambda: 100.0,
            ),
            grant=build_task_grant(
                task_id="task-overlong",
                source="control_page",
                goal=overlong_goal,
                now=100.0,
            ),
        )

        self.assertEqual((result.status, result.code), ("failed", "task_goal_empty"))
        decide_next.assert_not_awaited()
        execute_tool.assert_not_awaited()

    async def test_accepted_goal_reaches_worker_without_silent_truncation(self) -> None:
        goal = "inspect " + "x" * (TASK_MAX_GOAL_CHARS - len("inspect "))
        seen: list[dict] = []

        async def decide_next(state: dict) -> dict:
            seen.append(state)
            return {"type": "ask_user", "question": "missing input"}

        result = await run_task_loop_from_runtime(
            goal,
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=AsyncMock(),
                monotonic=lambda: 0.0,
                wall_time=lambda: 100.0,
            ),
            grant=build_task_grant(
                task_id="task-exact-limit",
                source="control_page",
                goal=goal,
                now=100.0,
            ),
        )

        self.assertEqual(result.status, "awaiting_approval")
        self.assertEqual(seen[0]["goal"], goal)

    async def test_task_grant_expires_at_exact_deadline_before_worker(self) -> None:
        decide_next = AsyncMock()
        execute_tool = AsyncMock()
        grant = build_task_grant(
            task_id="task-exact-expiry",
            source="control_page",
            goal="inspect runtime status",
            now=100.0,
            lifetime_sec=30.0,
        )

        self.assertEqual(
            grant.authorize("runtime_status", now=grant.expires_at),
            "expired",
        )
        result = await run_task_loop_from_runtime(
            "inspect runtime status",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 0.0,
                wall_time=lambda: grant.expires_at,
            ),
            grant=grant,
        )

        self.assertEqual((result.status, result.code), ("failed", "task_grant_expired"))
        decide_next.assert_not_awaited()
        execute_tool.assert_not_awaited()

    def test_task_goal_preserves_exact_quoted_whitespace(self) -> None:
        goal = 'Create docs/a.txt with content `a  b\n  c`'
        self.assertEqual(parse_task_request(f"/task {goal}"), goal)
        self.assertTrue(
            _mutation_goal_is_exact_content(
                goal,
                {
                    "mode": "create",
                    "path": "docs/a.txt",
                    "newText": "a  b\n  c",
                },
            )
        )
        self.assertFalse(
            _mutation_goal_is_exact_content(
                goal,
                {
                    "mode": "create",
                    "path": "docs/a.txt",
                    "newText": "a b c",
                },
            )
        )
        worker_payload = build_task_worker_payload({"goal": goal, "observations": []})
        worker_state = json.loads(
            worker_payload["messages"][1]["content"].split("\n", 1)[1]
        )
        self.assertEqual(worker_state["goal"], goal)

    def test_exact_workspace_read_binding_preserves_the_literal_path(self) -> None:
        for goal, expected in (
            ("README.md를 읽어줘", {"path": "README.md"}),
            ("README.md을 읽어줘", {"path": "README.md"}),
            ("Read README.md", {"path": "README.md"}),
            ("`docs/My File.md`를 읽어줘", {"path": "docs/My File.md"}),
        ):
            with self.subTest(goal=goal):
                self.assertEqual(_exact_workspace_read_args(goal), expected)

        for goal in (
            "README.md를 읽고 내용을 알려줘",
            "Read README.md and polish it",
            "C:/tmp/README.md를 읽어줘",
            "문서를 읽어줘",
        ):
            with self.subTest(rejected_goal=goal):
                self.assertIsNone(_exact_workspace_read_args(goal))

    async def test_default_loop_executes_exact_initial_read_before_worker(self) -> None:
        class FakeWorkspaceClient:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            @staticmethod
            def available() -> bool:
                return True

            def execute(
                self,
                task_id: str,
                step_id: int,
                tool: str,
                args: dict,
                **kwargs,
            ) -> dict:
                self.calls.append(
                    {
                        "task_id": task_id,
                        "step_id": step_id,
                        "tool": tool,
                        "args": dict(args),
                        **kwargs,
                    }
                )
                return {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_read_completed",
                    "summary": "read",
                    "evidence": _read_evidence(args["path"]),
                }

        client = FakeWorkspaceClient()
        worker_states: list[dict] = []

        async def decide_next(state: dict) -> dict:
            worker_states.append(state)
            return {
                "type": "final",
                "summary": "읽기 완료",
                "verified_step": state["observations"][-1]["step"],
            }

        result = await run_default_task_loop(
            "README.md를 읽어줘",
            source="control_page",
            task_id="task-exact-read",
            workspace_client=client,
            decide_next=decide_next,
        )

        self.assertEqual((result.status, result.code), ("completed", "task_completed"))
        self.assertEqual((result.step_count, result.model_call_count), (1, 0))
        self.assertTrue(
            task_loop_completed_evidence(
                result.evidence_text(),
                goal="README.md를 읽어줘",
            )
        )
        self.assertEqual(worker_states, [])
        self.assertEqual(
            [(call["tool"], call["args"]) for call in client.calls],
            [("read", {"path": "README.md"})],
        )

    def test_control_page_grant_auto_allows_only_bounded_workspace_reads(self) -> None:
        grant = build_task_grant(
            task_id="task-1",
            source="control_page",
            goal="이 파일을 고치고 테스트해줘",
            now=100.0,
        )

        self.assertEqual(
            grant.authorize("workspace_edit", now=101.0),
            "approval_required",
        )
        self.assertEqual(
            grant.authorize("workspace_test", now=101.0),
            "approval_required",
        )

    def test_non_control_surface_cannot_auto_read_workspace(self) -> None:
        for source in ("text", "voice", "discord", "local_bridge"):
            with self.subTest(source=source):
                grant = build_task_grant(
                    task_id="task-1",
                    source=source,
                    goal="코드를 확인해줘",
                    now=100.0,
                )
                for tool in (
                    "workspace_list",
                    "workspace_search",
                    "workspace_read",
                    "workspace_diff",
                ):
                    self.assertEqual(
                        grant.authorize(tool, now=101.0),
                        "approval_required",
                    )

    def test_explicit_web_task_does_not_mix_workspace_observations(self) -> None:
        grant = build_task_grant(
            task_id="task-1",
            source="control_page",
            goal="웹에서 최신 정보를 찾아줘",
            now=100.0,
        )
        self.assertEqual(grant.authorize("web_search", now=101.0), "auto")
        self.assertEqual(
            grant.authorize("workspace_read", now=101.0),
            "approval_required",
        )
        self.assertEqual(grant.authorize("service_restart", now=101.0), "approval_required")
        self.assertEqual(grant.authorize("unrestricted_shell", now=101.0), "forbidden")

        negated = build_task_grant(
            task_id="task-2",
            source="control_page",
            goal="웹 검색은 하지 말고 현재 코드만 설명해줘",
            now=100.0,
        )
        self.assertEqual(
            negated.authorize("web_search", now=101.0),
            "approval_required",
        )

    def test_web_auto_grant_requires_an_explicit_search_imperative(self) -> None:
        for goal in (
            "웹 UI 코드를 찾아줘",
            "웹 검색 UI 코드를 고쳐줘",
            "온라인 모드를 검색해줘",
            "뉴스 기능 코드를 읽어줘",
            "뉴스 검색 기능 코드를 읽어줘",
            "웹에서 찾아보지 마",
            "웹에서 찾아보는 건 피해줘",
            "웹에서 검색해 볼 필요 없어. README.md 읽어줘",
            "인터넷에서 찾아볼 필요는 없어. 코드를 확인해줘",
            "do not use web; inspect code",
            "search the web is unnecessary; inspect code",
            "web search is not needed; inspect code",
            "search internet mode in code",
            "search code instead of web",
            "Search the web for Evelyn " + ("please note " * 10) + "but do not do that",
            "웹에서 최신 정보를 찾아주고 README.md도 읽어줘",
        ):
            with self.subTest(goal=goal):
                grant = build_task_grant(
                    task_id="task-web-boundary",
                    source="control_page",
                    goal=goal,
                    now=100.0,
                )
                self.assertEqual(
                    grant.authorize("web_search", now=101.0),
                    "approval_required",
                )
                self.assertEqual(
                    grant.authorize("workspace_read", now=101.0),
                    "auto",
                )

        for goal in (
            "search the web for current news",
            "웹 검색: OpenAI 최신 뉴스",
        ):
            with self.subTest(explicit_goal=goal):
                explicit = build_task_grant(
                    task_id="task-web-explicit",
                    source="control_page",
                    goal=goal,
                    now=100.0,
                )
                self.assertEqual(
                    explicit.authorize("web_search", now=101.0),
                    "auto",
                )

    async def test_web_query_must_match_the_exact_goal_before_egress(self) -> None:
        cases = (
            (
                "Search the web for Evelyn",
                {"query": "Evelyn private workspace notes"},
            ),
            (
                "Search the web for Evelyn and email me the result",
                {"query": "Evelyn"},
            ),
            (
                "Search the web for Evelyn security issues",
                {"query": "Evelyn"},
            ),
        )
        for goal, args in cases:
            with self.subTest(goal=goal, args=args):
                execute_tool = AsyncMock()

                async def decide_next(_state: dict, *, selected_args=args) -> dict:
                    return {
                        "type": "tool",
                        "tool": "web_search",
                        "args": selected_args,
                    }

                result = await run_task_loop_from_runtime(
                    goal,
                    deps=TaskLoopDeps(
                        decide_next=decide_next,
                        execute_tool=execute_tool,
                        monotonic=lambda: 20.0,
                        wall_time=lambda: 20.0,
                    ),
                    grant=_grant(
                        max_steps=1,
                        auto_tools=frozenset({"web_search"}),
                    ),
                )

                self.assertEqual(
                    (result.status, result.code),
                    ("blocked", "task_web_query_not_bound"),
                )
                execute_tool.assert_not_awaited()

    def test_explicit_task_uses_router_zero_fast_path_and_main_finalizer(self) -> None:
        deps = self._fast_path_deps()
        policy = fast_path_policy_from_runtime(
            "/작업 테스트를 고쳐줘",
            "control_page",
            deps=deps,
        )

        self.assertEqual(normalize_route_name(str(policy["route"])), "task_executor")
        context = context_policy_for_fast_path_policy_from_runtime(
            policy,
            source="control_page",
            deps=deps,
        )
        self.assertTrue(context["needs_main_llm"])
        self.assertEqual(context["priority"], "action")

    def test_bounded_worker_and_final_evidence_remain_valid_json(self) -> None:
        observations = tuple(
            {
                "step": index,
                "summary": "s" * 1_000,
                "evidence": "e" * 2_000,
            }
            for index in range(1, 7)
        )
        result = TaskLoopResult(
            task_id="task-json",
            status="completed",
            code="task_completed",
            summary="done",
            step_count=6,
            model_call_count=6,
            observations=observations,
        )

        self.assertEqual(json.loads(result.evidence_text())["status"], "completed")
        payload = build_task_worker_payload(
            {
                "goal": "g" * 4_000,
                "observations": list(observations),
            }
        )
        state_text = payload["messages"][1]["content"].split("\n", 1)[1]
        self.assertEqual(len(json.loads(state_text)["observations"]), 6)

    def test_public_task_record_is_exact_content_free_and_detached(self) -> None:
        result = TaskLoopResult(
            task_id="task-public",
            status="uncertain",
            code="task_outcome_unverified",
            summary="PRIVATE SUMMARY",
            step_count=1,
            model_call_count=2,
            observations=(
                {
                    "step": 1,
                    "tool": "workspace_read",
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": False,
                    "outcome": "uncertain",
                    "code": "task_outcome_unverified",
                    "summary": "PRIVATE STEP SUMMARY",
                    "evidence": "PRIVATE EVIDENCE /secret/module.py",
                },
            ),
        )

        record = result.public_task_record()

        self.assertEqual(record["schema"], TASK_PUBLIC_RECORD_SCHEMA)
        self.assertEqual(record["contractVersion"], TASK_WORK_CONTRACT_SCHEMA)
        self.assertEqual(record["evalVersion"], TASK_EVAL_VERSION)
        self.assertTrue(record["processLocal"])
        self.assertFalse(record["durable"])
        self.assertEqual(
            set(record),
            {
                "schema",
                "taskId",
                "status",
                "code",
                "stepCount",
                "modelCallCount",
                "steps",
                "contractVersion",
                "evalVersion",
                "guidanceVersion",
                "guidanceDigest",
                "guidanceMode",
                "canaryRunId",
                "processLocal",
                "durable",
            },
        )
        encoded = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("PRIVATE", encoded)
        self.assertNotIn("module.py", encoded)
        self.assertIsNotNone(validated_public_task_record(record))
        record["steps"][0]["code"] = "caller_mutated"
        self.assertEqual(
            result.public_task_record()["steps"][0]["code"],
            "task_outcome_unverified",
        )
        forged = result.public_task_record()
        forged["rawEvidence"] = "PRIVATE"
        self.assertIsNone(validated_public_task_record(forged))
        with self.assertRaisesRegex(ValueError, "task_public_status_invalid"):
            TaskLoopResult(
                task_id="task-invalid",
                status="new_unreviewed_status",
                code="task_completed",
                summary="",
                step_count=0,
                model_call_count=0,
            ).public_task_record()

    async def test_work_contract_tracks_only_actual_worker_context_and_authority(self) -> None:
        decisions = iter(
            (
                {
                    "type": "tool",
                    "tool": "runtime_status",
                    "args": {},
                    "success_criteria": "runtime status collected",
                },
                {"type": "final", "summary": "done", "verified_step": 1},
            )
        )

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                evidence=_runtime_evidence(),
            )

        owner_token = object()
        scope = TurnScope(turn_id="turn-contract")
        result = await run_task_loop_from_runtime(
            "런타임 상태를 확인해줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(auto_tools=frozenset({"runtime_status"})),
            turn_scope=scope,
            principal_token=owner_token,
            skill_origin_class=SkillOriginClass.BUNDLED,
        )

        contract = result.contract
        self.assertIsNotNone(contract)
        assert contract is not None
        self.assertEqual(contract.schema, TASK_WORK_CONTRACT_SCHEMA)
        self.assertEqual(contract.task_id, result.task_id)
        self.assertEqual(contract.route.value, "task_executor")
        self.assertEqual(contract.source.value, "control_page")
        self.assertEqual(contract.skill_origin_class, SkillOriginClass.BUNDLED)
        self.assertEqual(contract.instruction_digest, TASK_WORKER_INSTRUCTION_DIGEST)
        worker_payload = build_task_worker_payload({"goal": "x", "observations": []})
        self.assertEqual(
            hashlib.sha256(
                worker_payload["messages"][0]["content"].encode("utf-8")
            ).hexdigest(),
            contract.instruction_digest,
        )
        self.assertEqual(len(contract.consumed_contexts), 2)
        self.assertEqual(
            set(contract.tool_guidance_names),
            {"runtime_status", "service_restart"},
        )
        self.assertEqual(
            (
                contract.consumed_contexts[0].goal_present,
                contract.consumed_contexts[0].step,
                contract.consumed_contexts[0].observation_count,
            ),
            (True, 1, 0),
        )
        self.assertEqual(
            contract.consumed_contexts[1].observations[0].tool,
            "runtime_status",
        )
        self.assertEqual(
            contract.consumed_contexts[1].observations[0].code,
            "runtime_status_collected",
        )
        self.assertEqual(contract.authority.steps[0].outcome, "success")
        self.assertTrue(contract.authority.turn_current)
        self.assertTrue(contract.authority.grant_current)
        self.assertFalse(contract.authority.budget_exhausted)
        self.assertEqual(contract.authority.remaining_steps, 5)
        self.assertTrue(contract.is_owned_by(owner_token))
        self.assertFalse(contract.is_owned_by(object()))
        self.assertTrue(contract.matches_grant("grant-test"))
        self.assertNotIn("grant-test", repr(contract))
        self.assertNotIn("principal", repr(contract))
        self.assertNotIn("런타임 상태를 확인해줘", repr(contract))
        self.assertFalse(hasattr(contract, "messages"))
        self.assertFalse(hasattr(contract, "context_packet"))

    async def test_active_guidance_is_advisory_and_exactly_bound_without_public_text(self) -> None:
        guidance_text = (
            "근거가 없는 결론은 완료로 표시하지 말고 확인할 다음 단계를 선택한다."
        )
        guidance = TaskPlannerGuidance(
            version_id="guidance-v2",
            guidance_digest=hashlib.sha256(
                guidance_text.encode("utf-8")
            ).hexdigest(),
            guidance=guidance_text,
        )
        states: list[dict] = []
        decisions = iter(
            (
                {
                    "type": "tool",
                    "tool": "runtime_status",
                    "args": {},
                    "success_criteria": "runtime status collected",
                },
                {"type": "final", "summary": "done", "verified_step": 1},
            )
        )

        async def decide_next(state: dict) -> dict:
            states.append(state)
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                evidence=_runtime_evidence(),
            )

        result = await run_task_loop_from_runtime(
            "런타임 상태를 확인해줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(auto_tools=frozenset({"runtime_status"})),
            principal_token=object(),
            planner_guidance=guidance,
        )

        self.assertEqual(
            states[0]["plannerGuidance"],
            {
                "schema": "evelyn.task-planner-guidance-binding.v1",
                "versionId": "guidance-v2",
                "guidanceDigest": guidance.guidance_digest,
                "mode": "active",
                "canaryRunId": None,
                "authority": "advisory",
                "text": guidance_text,
            },
        )
        self.assertEqual(states[0]["autoTools"], ["runtime_status"])
        self.assertNotIn("plannerGuidance", states[0]["toolGuidance"])
        assert result.contract is not None
        self.assertEqual(result.contract.guidance_version, "guidance-v2")
        self.assertEqual(
            result.contract.guidance_digest, guidance.guidance_digest
        )
        record = result.public_task_record()
        self.assertEqual(record["guidanceVersion"], "guidance-v2")
        self.assertEqual(record["guidanceMode"], "active")
        self.assertNotIn(guidance_text, json.dumps(record, ensure_ascii=False))

    async def test_canary_guidance_requires_local_read_only_grounded_scope(self) -> None:
        text = "검토할 때 exact evidence reference만 사용한다."
        guidance = TaskPlannerGuidance(
            version_id="candidate-v1",
            guidance_digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            mode="canary",
            canary_run_id="canary-run-1",
            guidance=text,
        )
        with self.assertRaisesRegex(ValueError, "task_canary_scope_denied"):
            await run_task_loop_from_runtime(
                "README.md를 검토해줘",
                deps=TaskLoopDeps(
                    decide_next=AsyncMock(),
                    execute_tool=AsyncMock(),
                ),
                grant=_grant(auto_tools=frozenset({"workspace_read"})),
                principal_token=object(),
                planner_guidance=guidance,
            )

        grant = build_task_grant(
            task_id="canary-read-only",
            source="control_page",
            goal="README.md를 검토해줘",
            workspace_available=True,
            read_only=True,
        )
        self.assertTrue(grant.read_only)
        self.assertNotIn("workspace_edit", grant.auto_tools | grant.approval_tools)
        self.assertNotIn("workspace_test", grant.auto_tools | grant.approval_tools)
        self.assertTrue(task_goal_is_grounded_read_only("README.md를 검토해줘"))
        self.assertFalse(
            task_goal_is_grounded_read_only("README.md를 수정하고 검토해줘")
        )

    def test_base_guidance_identity_is_stable_and_forged_public_binding_fails(self) -> None:
        self.assertEqual(TASK_BASE_GUIDANCE_VERSION, "base")
        self.assertEqual(
            TASK_BASE_GUIDANCE_DIGEST,
            hashlib.sha256(b"").hexdigest(),
        )
        record = TaskLoopResult(
            task_id="task-guidance-record",
            status="failed",
            code="task_failed",
            summary="private",
            step_count=0,
            model_call_count=0,
        ).public_task_record()
        record["guidanceMode"] = "canary"
        self.assertIsNone(validated_public_task_record(record))
        record = TaskLoopResult(
            task_id="task-guidance-record",
            status="failed",
            code="task_failed",
            summary="private",
            step_count=0,
            model_call_count=0,
        ).public_task_record()
        record["guidanceDigest"] = "f" * 64
        self.assertIsNone(validated_public_task_record(record))
        with self.assertRaisesRegex(ValueError, "task_planner_guidance_invalid"):
            validated_task_planner_guidance(
                TaskPlannerGuidance(
                    version_id="not-base",
                    guidance_digest=TASK_BASE_GUIDANCE_DIGEST,
                ),
                source="control_page",
                principal_token=object(),
                read_only=False,
                goal="런타임 상태를 확인해줘",
            )

    async def test_unrelated_success_does_not_hide_failed_criterion(self) -> None:
        decisions = iter(
            [
                {
                    "type": "tool",
                    "tool": "workspace_read",
                    "args": {"path": "README.md"},
                    "success_criteria": "README is readable",
                },
                {
                    "type": "tool",
                    "tool": "runtime_status",
                    "args": {},
                    "success_criteria": "runtime status was collected",
                },
                {"type": "final", "summary": "검증 완료", "verified_step": 2},
            ]
        )
        states: list[dict] = []

        async def decide_next(state: dict) -> dict:
            states.append(state)
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="failed" if kwargs["step_id"] == 1 else "success",
                verified=True,
                code="read_failed" if kwargs["step_id"] == 1 else "runtime_verified",
                evidence=_runtime_evidence(),
            )

        result = await run_task_loop_from_runtime(
            "런타임 상태를 확인해줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(
                max_steps=3,
                auto_tools=frozenset({"workspace_read", "runtime_status"}),
            ),
        )

        self.assertEqual(result.status, "budget_exhausted")
        self.assertEqual(result.step_count, 3)
        self.assertEqual(result.model_call_count, 3)
        self.assertEqual(states[1]["observations"][0]["code"], "read_failed")
        self.assertEqual(len(result.observations), 3)

    async def test_later_success_for_same_criterion_resolves_failure(self) -> None:
        decisions = iter(
            [
                {
                    "type": "tool",
                    "tool": "workspace_read",
                    "args": {"path": "README.md"},
                    "success_criteria": "README is readable",
                },
                {
                    "type": "tool",
                    "tool": "workspace_read",
                    "args": {"path": "README.md"},
                    "success_criteria": "README is readable",
                },
                {"type": "final", "summary": "검증 완료", "verified_step": 2},
            ]
        )

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="failed" if kwargs["step_id"] == 1 else "success",
                verified=True,
                evidence=_read_evidence(kwargs["args"]["path"]),
            )

        result = await run_task_loop_from_runtime(
            "README.md를 읽어줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.step_count, 2)

    async def test_success_criteria_cannot_promote_a_read_into_a_mutation(self) -> None:
        async def run(goal: str) -> TaskLoopResult:
            decisions = iter(
                [
                    {
                        "type": "tool",
                        "tool": "workspace_read",
                        "args": {"path": "README.md"},
                        "success_criteria": "the requested file was edited",
                    },
                    {"type": "final", "summary": "수정 완료", "verified_step": 1},
                ]
            )

            async def decide_next(_state: dict) -> dict:
                return next(decisions)

            async def execute_tool(**kwargs) -> TaskStepReceipt:
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    evidence=_read_evidence(kwargs["args"]["path"]),
                )

            return await run_task_loop_from_runtime(
                goal,
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    monotonic=lambda: 20.0,
                    wall_time=lambda: 20.0,
                ),
                grant=_grant(max_steps=2),
            )

        for goal in (
            "README.md를 고쳐줘",
            "README.md 수정",
            "README.md를 새로 써줘",
            "README.md needs fixing",
            "Correct the typo in README.md",
            "Solve the typo in README.md",
            "README.md needs correcting",
            "Revise README.md",
            "Polish README.md",
            "Adjust README.md",
            "README.md 문장을 다듬어줘",
            "README.md를 손봐줘",
            "Read README.md and polish it",
            "Review README.md and adjust the wording",
            "Read README.md, polish it",
            "README.md를 읽고 문장을 다듬어줘",
            "README.md를 검토하고 손봐줘",
            "테스트를 통과시켜줘",
            "make tests pass",
        ):
            with self.subTest(goal=goal):
                result = await run(goal)
                self.assertEqual(result.status, "budget_exhausted")
                self.assertEqual(
                    result.observations[-1]["code"],
                    "task_verification_required",
                )

    async def test_read_completion_binds_goal_args_and_typed_evidence(self) -> None:
        async def run(
            *,
            goal: str,
            tool: str,
            args: dict,
            evidence: dict,
        ) -> TaskLoopResult:
            decisions = iter(
                [
                    {
                        "type": "tool",
                        "tool": tool,
                        "args": args,
                        # Worker prose is never completion authority.
                        "success_criteria": "the requested goal is fully verified",
                    },
                    {"type": "final", "summary": "완료", "verified_step": 1},
                ]
            )

            async def decide_next(_state: dict) -> dict:
                return next(decisions)

            async def execute_tool(**kwargs) -> TaskStepReceipt:
                typed_evidence = dict(evidence)
                if tool == "workspace_search":
                    typed_evidence.setdefault("path", args.get("path"))
                    typed_evidence.setdefault("query", args.get("query"))
                elif tool == "workspace_list":
                    typed_evidence.setdefault("path", args.get("path"))
                    typed_evidence.setdefault(
                        "recursive",
                        args.get("recursive", False),
                    )
                    typed_evidence["entries"] = [
                        (
                            item
                            if "type" in item
                            else {**item, "type": "file", "bytes": None}
                        )
                        for item in typed_evidence.get("entries", [])
                    ]
                elif tool == "workspace_diff":
                    typed_evidence.setdefault("stderr", "")
                elif tool == "web_search":
                    typed_evidence["results"] = [
                        (
                            item
                            if isinstance(item, dict)
                            else {
                                "title": str(item)[:240],
                                "snippet": str(item),
                                "url": "https://example.test/result",
                            }
                        )
                        for item in typed_evidence.get("results", [])
                    ]
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    evidence=typed_evidence,
                )

            return await run_task_loop_from_runtime(
                goal,
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    monotonic=lambda: 20.0,
                    wall_time=lambda: 20.0,
                ),
                grant=_grant(max_steps=2, auto_tools=frozenset({tool})),
            )

        cases = (
            (
                "unrelated runtime status",
                "README.md를 읽어줘",
                "runtime_status",
                {},
                _runtime_evidence(),
                "budget_exhausted",
            ),
            (
                "empty read evidence",
                "README.md를 읽어줘",
                "workspace_read",
                {"path": "README.md"},
                {},
                "budget_exhausted",
            ),
            (
                "mismatched read evidence",
                "README.md를 읽어줘",
                "workspace_read",
                {"path": "README.md"},
                _read_evidence("docs/01_NOW.md"),
                "budget_exhausted",
            ),
            (
                "same basename different target",
                "README.md를 읽어줘",
                "workspace_read",
                {"path": "docs/README.md"},
                _read_evidence("docs/README.md"),
                "budget_exhausted",
            ),
            (
                "target is only a filename prefix",
                "README.md.bak을 읽어줘",
                "workspace_read",
                {"path": "README.md"},
                _read_evidence("README.md"),
                "budget_exhausted",
            ),
            (
                "internal path whitespace mismatch",
                "docs/a  b.md를 읽어줘",
                "workspace_read",
                {"path": "docs/a b.md"},
                _read_evidence("docs/a b.md"),
                "budget_exhausted",
            ),
            (
                "exact internal path whitespace",
                "docs/a  b.md를 읽어줘",
                "workspace_read",
                {"path": "docs/a  b.md"},
                _read_evidence("docs/a  b.md"),
                "completed",
            ),
            (
                "bound read",
                "README.md를 읽어줘",
                "workspace_read",
                {"path": "README.md"},
                _read_evidence("README.md"),
                "completed",
            ),
            (
                "truncated read",
                "README.md를 읽어줘",
                "workspace_read",
                {"path": "README.md"},
                {**_read_evidence("README.md"), "truncated": True},
                "budget_exhausted",
            ),
            (
                "negated read",
                "README.md를 검토하지 마",
                "workspace_read",
                {"path": "README.md"},
                _read_evidence("README.md"),
                "budget_exhausted",
            ),
            (
                "model evidence truncated read",
                "README.md를 읽어줘",
                "workspace_read",
                {"path": "README.md"},
                {**_read_evidence("README.md"), "content": "x" * 4_000},
                "budget_exhausted",
            ),
            (
                "complete negative search evidence",
                "작업공간에서 task_loop를 검색해줘",
                "workspace_search",
                {"path": ".", "query": "task_loop"},
                {"matches": [], "truncated": False},
                "completed",
            ),
            (
                "mismatched search query",
                "작업공간에서 task_loop를 검색해줘",
                "workspace_search",
                {"path": ".", "query": "password"},
                {
                    "matches": [{"path": "README.md", "line": 1, "text": "password"}],
                    "truncated": False,
                },
                "budget_exhausted",
            ),
            (
                "bound search",
                "작업공간에서 task_loop를 검색해줘",
                "workspace_search",
                {"path": ".", "query": "task_loop"},
                {
                    "matches": [{"path": "README.md", "line": 1, "text": "task_loop"}],
                    "truncated": False,
                },
                "completed",
            ),
            (
                "exact multiword search query",
                "Search project for task loop security issues",
                "workspace_search",
                {"path": ".", "query": "task loop security issues"},
                {
                    "matches": [
                        {
                            "path": "README.md",
                            "line": 1,
                            "text": "task loop security issues",
                        }
                    ],
                    "truncated": False,
                },
                "completed",
            ),
            (
                "underbound search query",
                "Search project for task loop security issues",
                "workspace_search",
                {"path": ".", "query": "task"},
                {
                    "matches": [{"path": "README.md", "line": 1, "text": "task"}],
                    "truncated": False,
                },
                "budget_exhausted",
            ),
            (
                "quoted query whitespace mismatch",
                'Search project for "a  b"',
                "workspace_search",
                {"path": ".", "query": "a b"},
                {
                    "matches": [{"path": "README.md", "line": 1, "text": "a b"}],
                    "truncated": False,
                },
                "budget_exhausted",
            ),
            (
                "exact quoted query whitespace",
                'Search project for "a  b"',
                "workspace_search",
                {"path": ".", "query": "a  b"},
                {
                    "matches": [{"path": "README.md", "line": 1, "text": "a  b"}],
                    "truncated": False,
                },
                "completed",
            ),
            (
                "exact search scope",
                "Search docs for TODO",
                "workspace_search",
                {"path": "docs", "query": "TODO"},
                {
                    "matches": [{"path": "docs/01_NOW.md", "line": 1, "text": "TODO"}],
                    "truncated": False,
                },
                "completed",
            ),
            (
                "overbroad search scope",
                "Search docs for TODO",
                "workspace_search",
                {"path": ".", "query": "TODO"},
                {
                    "matches": [{"path": "README.md", "line": 1, "text": "TODO"}],
                    "truncated": False,
                },
                "budget_exhausted",
            ),
            (
                "truncated search",
                "작업공간에서 task_loop를 검색해줘",
                "workspace_search",
                {"path": ".", "query": "task_loop"},
                {
                    "matches": [{"path": "README.md", "line": 1, "text": "task_loop"}],
                    "truncated": True,
                },
                "budget_exhausted",
            ),
            (
                "model evidence truncated search",
                "작업공간에서 task_loop를 검색해줘",
                "workspace_search",
                {"path": ".", "query": "task_loop"},
                {
                    "matches": [
                        {"path": f"docs/{index}.md", "line": 1, "text": "task_loop"}
                        for index in range(32)
                    ],
                    "truncated": False,
                },
                "budget_exhausted",
            ),
            (
                "comma compound search",
                "Search project for TODO, delete matches",
                "workspace_search",
                {"path": ".", "query": "TODO"},
                {
                    "matches": [{"path": "README.md", "line": 1, "text": "TODO"}],
                    "truncated": False,
                },
                "budget_exhausted",
            ),
            (
                "unseparated compound search",
                "Search project for TODO also delete matches",
                "workspace_search",
                {"path": ".", "query": "TODO"},
                {
                    "matches": [{"path": "README.md", "line": 1, "text": "TODO"}],
                    "truncated": False,
                },
                "budget_exhausted",
            ),
            (
                "compound text cannot be rebound as query",
                "Search project for TODO also delete matches",
                "workspace_search",
                {"path": ".", "query": "TODO also delete matches"},
                {
                    "matches": [
                        {
                            "path": "README.md",
                            "line": 1,
                            "text": "TODO also delete matches",
                        }
                    ],
                    "truncated": False,
                },
                "budget_exhausted",
            ),
            (
                "bound list",
                "docs 폴더 파일 목록을 보여줘",
                "workspace_list",
                {"path": "docs"},
                {"entries": [{"path": "docs/01_NOW.md"}], "truncated": False},
                "completed",
            ),
            (
                "complete empty list",
                "docs 폴더 파일 목록을 보여줘",
                "workspace_list",
                {"path": "docs"},
                {"entries": [], "truncated": False},
                "completed",
            ),
            (
                "bound recursive list",
                "List project files recursively",
                "workspace_list",
                {"path": ".", "recursive": True},
                {"entries": [{"path": "docs/01_NOW.md"}], "truncated": False},
                "completed",
            ),
            (
                "underbound recursive list",
                "List project files recursively",
                "workspace_list",
                {"path": ".", "recursive": False},
                {"entries": [{"path": "README.md"}], "truncated": False},
                "budget_exhausted",
            ),
            (
                "overbroad recursive list",
                "List project files",
                "workspace_list",
                {"path": ".", "recursive": True},
                {"entries": [{"path": "docs/01_NOW.md"}], "truncated": False},
                "budget_exhausted",
            ),
            (
                "truncated list",
                "docs 폴더 파일 목록을 보여줘",
                "workspace_list",
                {"path": "docs"},
                {"entries": [{"path": "docs/01_NOW.md"}], "truncated": True},
                "budget_exhausted",
            ),
            (
                "compound list",
                "List project files and publish the list",
                "workspace_list",
                {"path": "."},
                {"entries": [{"path": "README.md"}], "truncated": False},
                "budget_exhausted",
            ),
            (
                "unseparated compound list",
                "List project files afterward publish them",
                "workspace_list",
                {"path": "."},
                {"entries": [{"path": "README.md"}], "truncated": False},
                "budget_exhausted",
            ),
            (
                "comma compound list",
                "List project files, publish them",
                "workspace_list",
                {"path": "."},
                {"entries": [{"path": "README.md"}], "truncated": False},
                "budget_exhausted",
            ),
            (
                "model evidence truncated list",
                "프로젝트 파일 목록을 보여줘",
                "workspace_list",
                {"path": "."},
                {
                    "entries": [{"path": f"docs/{index}.md"} for index in range(64)],
                    "truncated": False,
                },
                "budget_exhausted",
            ),
            (
                "bound diff",
                "README.md의 diff를 보여줘",
                "workspace_diff",
                {"paths": ["README.md"]},
                {
                    "paths": ["README.md"],
                    "diff": "--- a/README.md\n+++ b/README.md\n",
                    "exitCode": 0,
                    "truncated": False,
                },
                "completed",
            ),
            (
                "complete empty diff",
                "README.md의 diff를 보여줘",
                "workspace_diff",
                {"paths": ["README.md"]},
                {
                    "paths": ["README.md"],
                    "diff": "",
                    "exitCode": 0,
                    "truncated": False,
                },
                "completed",
            ),
            (
                "truncated diff",
                "README.md의 diff를 보여줘",
                "workspace_diff",
                {"paths": ["README.md"]},
                {
                    "paths": ["README.md"],
                    "diff": "--- a/README.md\n+++ b/README.md\n",
                    "exitCode": 0,
                    "truncated": True,
                },
                "budget_exhausted",
            ),
            (
                "fully visible bounded diff",
                "README.md의 diff를 보여줘",
                "workspace_diff",
                {"paths": ["README.md"]},
                {
                    "paths": ["README.md"],
                    "diff": "x" * 4_000,
                    "exitCode": 0,
                    "truncated": False,
                },
                "completed",
            ),
            (
                "comma compound diff",
                "Review diff for README.md, commit it",
                "workspace_diff",
                {"paths": ["README.md"]},
                {
                    "paths": ["README.md"],
                    "diff": "--- a/README.md\n+++ b/README.md\n",
                    "exitCode": 0,
                    "truncated": False,
                },
                "budget_exhausted",
            ),
            (
                "unseparated compound read",
                "Read README.md please polish it",
                "workspace_read",
                {"path": "README.md"},
                _read_evidence("README.md"),
                "budget_exhausted",
            ),
            (
                "slash compound read",
                "Read README.md / polish it",
                "workspace_read",
                {"path": "README.md"},
                _read_evidence("README.md"),
                "budget_exhausted",
            ),
            (
                "korean compound read",
                "README.md를 읽고 문장을 다듬어줘",
                "workspace_read",
                {"path": "README.md"},
                _read_evidence("README.md"),
                "budget_exhausted",
            ),
            (
                "compound runtime status",
                "Check runtime status and restart the service",
                "runtime_status",
                {},
                _runtime_evidence(),
                "budget_exhausted",
            ),
            (
                "bound runtime status",
                "런타임 상태를 확인해줘",
                "runtime_status",
                {},
                _runtime_evidence(),
                "completed",
            ),
            (
                "comma compound runtime status",
                "Check runtime status, restart the service",
                "runtime_status",
                {},
                _runtime_evidence(),
                "budget_exhausted",
            ),
            (
                "model evidence truncated runtime status",
                "Check runtime status",
                "runtime_status",
                {},
                {**_runtime_evidence(), "details": "x" * 4_000},
                "budget_exhausted",
            ),
            (
                "bound web search",
                "Search the web for Evelyn",
                "web_search",
                {"query": "Evelyn"},
                {"query": "Evelyn", "results": ["result"]},
                "completed",
            ),
            (
                "model evidence truncated web search",
                "Search the web for Evelyn",
                "web_search",
                {"query": "Evelyn"},
                {"query": "Evelyn", "results": ["x" * 4_000]},
                "budget_exhausted",
            ),
        )
        for name, goal, tool, args, evidence, expected in cases:
            with self.subTest(name=name):
                result = await run(
                    goal=goal,
                    tool=tool,
                    args=args,
                    evidence=evidence,
                )
                self.assertEqual(result.status, expected)
                if expected != "completed":
                    self.assertEqual(
                        result.observations[-1]["code"],
                        "task_verification_required",
                    )

    def test_completion_rejects_evidence_larger_than_main_projection(self) -> None:
        evidence = {**_runtime_evidence(), "details": "x" * 1_000}
        encoded = json.dumps(evidence, separators=(",", ":"))
        self.assertGreater(len(encoded), 1_000)
        self.assertLessEqual(len(encoded), 1_200)
        receipt = _receipt(
            step_id=1,
            tool="runtime_status",
            action_run_id="run-1",
            grant_id="grant-test",
            outcome="success",
            verified=True,
            evidence=evidence,
        )

        self.assertFalse(
            _completion_evidence_matches(
                goal="Check runtime status",
                verified_step=1,
                latest_observation_step=1,
                successful_actions={1: ("runtime_status", {}, receipt)},
            )
        )

    def test_runtime_and_serialized_finalizer_share_typed_read_contract(self) -> None:
        cases = (
            (
                "runtime_status",
                "런타임 상태를 확인해줘",
                {},
                _runtime_evidence(),
            ),
            (
                "web_search",
                "Search the web for Evelyn",
                {"query": "Evelyn"},
                {
                    "query": "Evelyn",
                    "results": [
                        {
                            "title": "Evelyn",
                            "snippet": "result",
                            "url": "https://example.test/evelyn",
                        }
                    ],
                },
            ),
            (
                "workspace_list",
                "docs 폴더 파일 목록을 보여줘",
                {"path": "docs"},
                {
                    "path": "docs",
                    "recursive": False,
                    "entries": [
                        {"path": "docs/01_NOW.md", "type": "file", "bytes": 1}
                    ],
                    "truncated": False,
                },
            ),
            (
                "workspace_search",
                "Search docs for TODO",
                {"path": "docs", "query": "TODO"},
                {
                    "path": "docs",
                    "query": "TODO",
                    "matches": [
                        {"path": "docs/01_NOW.md", "line": 1, "text": "TODO"}
                    ],
                    "truncated": False,
                },
            ),
            (
                "workspace_diff",
                "README.md의 diff를 보여줘",
                {"paths": ["README.md"]},
                _diff_evidence(["README.md"]),
            ),
            (
                "workspace_read",
                "README.md를 읽어줘",
                {"path": "README.md"},
                _read_evidence("README.md"),
            ),
        )
        for tool, goal, args, evidence in cases:
            with self.subTest(tool=tool):
                receipt = _receipt(
                    step_id=1,
                    tool=tool,
                    action_run_id=f"run-{tool}",
                    grant_id="grant-test",
                    outcome="success",
                    verified=True,
                    evidence=evidence,
                )
                runtime_accepts = _completion_evidence_matches(
                    goal=goal,
                    verified_step=1,
                    latest_observation_step=1,
                    successful_actions={1: (tool, args, receipt)},
                )
                serialized = TaskLoopResult(
                    task_id=f"task-{tool}",
                    status="completed",
                    code="task_completed",
                    summary="verified",
                    step_count=1,
                    model_call_count=2,
                    observations=(receipt.to_observation(),),
                ).evidence_text()
                outcome = task_loop_terminal_outcome(serialized, goal=goal)
                finalizer_accepts = outcome is not None
                self.assertTrue(runtime_accepts)
                self.assertEqual(runtime_accepts, finalizer_accepts)
                if tool != "runtime_status":
                    assert outcome is not None
                    encoded = outcome.split("evidencePreviewHex=", 1)[1].rstrip(".")
                    display = format_display_text(outcome)
                    self.assertIn(f"evidencePreviewHex={encoded}", display)
                    self.assertLess(len(display), 1_800)

                forged_receipt = _receipt(
                    step_id=1,
                    tool=tool,
                    action_run_id=f"run-{tool}-forged",
                    grant_id="grant-test",
                    outcome="success",
                    verified=True,
                    evidence={**evidence, "unexpected": True},
                )
                forged_runtime_accepts = _completion_evidence_matches(
                    goal=goal,
                    verified_step=1,
                    latest_observation_step=1,
                    successful_actions={1: (tool, args, forged_receipt)},
                )
                forged_serialized = TaskLoopResult(
                    task_id=f"task-{tool}-forged",
                    status="completed",
                    code="task_completed",
                    summary="forged",
                    step_count=1,
                    model_call_count=2,
                    observations=(forged_receipt.to_observation(),),
                ).evidence_text()
                forged_finalizer_accepts = (
                    task_loop_terminal_outcome(forged_serialized, goal=goal)
                    is not None
                )
                self.assertFalse(forged_runtime_accepts)
                self.assertEqual(forged_runtime_accepts, forged_finalizer_accepts)

    def test_runtime_status_rejects_extra_evidence_even_when_fully_visible(self) -> None:
        for target_chars in (600, 601, 1_000, 1_001):
            evidence = {**_runtime_evidence(), "details": ""}
            empty_length = len(
                json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
            )
            evidence["details"] = "x" * (target_chars - empty_length)
            encoded = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
            self.assertEqual(len(encoded), target_chars)

            receipt = _task_receipt_from_result(
                {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "runtime_status_collected",
                    "summary": "status",
                    "evidence": evidence,
                },
                step_id=1,
                tool="runtime_status",
                action_run_id="run-boundary",
                grant_id="grant-test",
            )
            self.assertEqual(len(receipt.evidence), min(target_chars, 1_000))
            self.assertFalse(
                _completion_evidence_matches(
                    goal="Check runtime status",
                    verified_step=1,
                    latest_observation_step=1,
                    successful_actions={1: ("runtime_status", {}, receipt)},
                )
            )

    def test_completion_rejects_an_old_cited_step_after_later_success(self) -> None:
        read_receipt = _receipt(
            step_id=1,
            tool="workspace_read",
            action_run_id="run-read",
            grant_id="grant-test",
            outcome="success",
            verified=True,
            evidence=_read_evidence("README.md"),
        )
        later_receipt = _receipt(
            step_id=2,
            tool="runtime_status",
            action_run_id="run-health",
            grant_id="grant-test",
            outcome="success",
            verified=True,
            evidence=_runtime_evidence(),
        )

        self.assertFalse(
            _completion_evidence_matches(
                goal="README.md를 읽어줘",
                verified_step=1,
                latest_observation_step=2,
                successful_actions={
                    1: ("workspace_read", {"path": "README.md"}, read_receipt),
                    2: ("runtime_status", {}, later_receipt),
                },
            )
        )

    async def test_completion_rejects_old_proof_after_worker_error_observations(self) -> None:
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_read", "args": {"path": "README.md"}},
                {},
                {},
                {},
                {},
                {"type": "final", "summary": "읽기 완료", "verified_step": 1},
            ]
        )

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                evidence=_read_evidence("README.md"),
            )

        result = await run_task_loop_from_runtime(
            "README.md를 읽어줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(max_steps=6, auto_tools=frozenset({"workspace_read"})),
        )

        self.assertEqual(result.status, "budget_exhausted")
        self.assertNotEqual(result.code, "task_completed")
        self.assertEqual([item["step"] for item in result.observations[-3:]], [4, 5, 6])

    async def test_same_success_criteria_does_not_cross_tool_classes(self) -> None:
        decisions = iter(
            [
                {
                    "type": "tool",
                    "tool": "workspace_read",
                    "args": {"path": "README.md"},
                    "success_criteria": "target is verified",
                },
                {
                    "type": "tool",
                    "tool": "runtime_status",
                    "args": {},
                    "success_criteria": "target is verified",
                },
                {"type": "final", "summary": "검증 완료", "verified_step": 2},
            ]
        )

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="failed" if kwargs["step_id"] == 1 else "success",
                verified=True,
            )

        result = await run_task_loop_from_runtime(
            "런타임 상태를 확인해줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(
                max_steps=3,
                auto_tools=frozenset({"workspace_read", "runtime_status"}),
            ),
        )

        self.assertEqual(result.status, "budget_exhausted")

    async def test_completed_read_evidence_is_lossless_in_main_payload(self) -> None:
        decisions = iter(
            [
                {
                    "type": "tool",
                    "tool": "workspace_read",
                    "args": {"path": "README.md"},
                },
                {"type": "final", "summary": "읽기 완료", "verified_step": 1},
            ]
        )
        content = 'def f():\n    x = "a  b"\n    return x\n' * 12

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                evidence=_read_evidence("README.md", content=content),
            )

        result = await run_task_loop_from_runtime(
            "README.md를 읽어줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(auto_tools=frozenset({"workspace_read"})),
        )

        self.assertEqual(result.status, "completed")
        payload = json.loads(result.evidence_text())
        receipt_evidence = payload["observations"][-1]["evidence"]
        self.assertLessEqual(len(receipt_evidence), 1_000)
        visible_evidence = json.loads(receipt_evidence)
        self.assertEqual(visible_evidence["content"], content)

        worker_payload = build_task_worker_payload(
            {"goal": "README.md를 읽어줘", "observations": list(result.observations)}
        )
        worker_state = json.loads(
            worker_payload["messages"][1]["content"].split("\n", 1)[1]
        )
        self.assertEqual(worker_state["observations"][-1]["evidence"], receipt_evidence)

        messages = append_registered_route_evidence(
            [],
            route="task_executor",
            evidence=result.evidence_text(),
        )
        envelope = json.loads(messages[-1]["content"])
        main_evidence = json.loads(envelope["evidence"])
        self.assertEqual(
            main_evidence["observations"][-1]["evidence"],
            receipt_evidence,
        )
        self.assertEqual(
            json.loads(main_evidence["observations"][-1]["evidence"])["content"],
            content,
        )

    async def test_mutation_completion_forces_a_later_same_path_read(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {"type": "final", "summary": "수정 완료", "verified_step": 2},
            ]
        )
        tool_calls: list[tuple[str, dict]] = []

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append((kwargs["tool"], dict(kwargs["args"])))
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                evidence=_read_evidence(kwargs["args"]["path"]),
            )

        async def request_approval(_request, _preview) -> TaskApprovalResolution:
            return TaskApprovalResolution("approved", receipt=_approved_edit_result())

        result = await run_task_loop_from_runtime(
            "README.md에서 `old`를 `new`로 바꿔줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=3),
        )

        self.assertEqual((result.status, result.code), ("completed", "task_completed"))
        self.assertEqual((result.step_count, result.model_call_count), (2, 2))
        self.assertEqual(
            tool_calls,
            [("workspace_edit", edit_args), ("workspace_read", {"path": "README.md"})],
        )

    async def _pending_candidate_replans_after_invalid_test_decision(
        self,
        invalid_decision: object,
    ) -> tuple[TaskLoopResult, list[str], list[dict]]:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "fixed",
            "expectedSha256": "a" * 64,
        }
        test_args = {
            "runner": "python_unittest",
            "targets": ["tests/core/test_example.py"],
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                invalid_decision,
                {"type": "tool", "tool": "workspace_test", "args": test_args},
            ]
        )
        tool_calls: list[str] = []
        states: list[dict] = []

        async def decide_next(state: dict) -> dict:
            states.append(state)
            decision = next(decisions)
            if isinstance(decision, Exception):
                raise decision
            return decision

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append(kwargs["tool"])
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            if kwargs["tool"] == "workspace_test":
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    code="workspace_test_passed",
                    evidence=_sandbox_test_evidence(
                        kwargs["args"],
                        stage_id=kwargs["stage_id"],
                        candidate_sha=_READ_SHA256,
                    ),
                )
            self.assertEqual(kwargs["tool"], "workspace_edit_stage_cancel")
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        result = await run_task_loop_from_runtime(
            "README.md의 버그를 고쳐줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=lambda _request, _preview: asyncio.sleep(
                    0,
                    result=TaskApprovalResolution("cancelled"),
                ),
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=3),
        )
        return result, tool_calls, states

    async def test_pending_candidate_null_test_targets_are_observed_before_retry(self) -> None:
        result, tool_calls, states = (
            await self._pending_candidate_replans_after_invalid_test_decision(
                {
                    "type": "tool",
                    "tool": "workspace_test",
                    "args": {"runner": "python_unittest", "targets": None},
                }
            )
        )

        self.assertEqual((result.status, result.code), ("cancelled", "task_approval_cancelled"))
        self.assertEqual(
            tool_calls,
            ["workspace_edit", "workspace_test", "workspace_edit_stage_cancel"],
        )
        self.assertEqual(states[2]["requiredNextTool"], "workspace_test")
        self.assertEqual(
            states[2]["observations"][-1]["code"],
            "task_worker_workspace_test_args_invalid",
        )

    async def test_pending_candidate_integer_test_targets_are_observed_before_retry(self) -> None:
        result, tool_calls, states = (
            await self._pending_candidate_replans_after_invalid_test_decision(
                {
                    "type": "tool",
                    "tool": "workspace_test",
                    "args": {"runner": "python_unittest", "targets": 7},
                }
            )
        )

        self.assertEqual((result.status, result.code), ("cancelled", "task_approval_cancelled"))
        self.assertEqual(
            tool_calls,
            ["workspace_edit", "workspace_test", "workspace_edit_stage_cancel"],
        )
        self.assertEqual(states[2]["requiredNextTool"], "workspace_test")
        self.assertEqual(
            states[2]["observations"][-1]["code"],
            "task_worker_workspace_test_args_invalid",
        )

    async def test_pending_candidate_wrong_tool_is_observed_before_retry(self) -> None:
        result, tool_calls, states = (
            await self._pending_candidate_replans_after_invalid_test_decision(
                {
                    "type": "tool",
                    "tool": "workspace_read",
                    "args": {"path": "README.md"},
                }
            )
        )

        self.assertEqual((result.status, result.code), ("cancelled", "task_approval_cancelled"))
        self.assertEqual(
            tool_calls,
            ["workspace_edit", "workspace_test", "workspace_edit_stage_cancel"],
        )
        self.assertEqual(states[2]["requiredNextTool"], "workspace_test")
        self.assertEqual(
            states[2]["observations"][-1]["code"],
            "workspace_test_required_after_stage",
        )

    async def test_pending_candidate_invalid_json_is_observed_before_retry(self) -> None:
        result, tool_calls, states = (
            await self._pending_candidate_replans_after_invalid_test_decision(
                ValueError("task_worker_response_invalid")
            )
        )

        self.assertEqual((result.status, result.code), ("cancelled", "task_approval_cancelled"))
        self.assertEqual(
            tool_calls,
            ["workspace_edit", "workspace_test", "workspace_edit_stage_cancel"],
        )
        self.assertEqual(states[2]["requiredNextTool"], "workspace_test")
        self.assertEqual(
            states[2]["observations"][-1]["code"],
            "task_worker_decision_invalid",
        )

    async def test_untyped_worker_value_error_remains_terminal(self) -> None:
        execute_tool = AsyncMock()

        async def decide_next(_state: dict) -> dict:
            raise ValueError("internal_worker_bug")

        result = await run_task_loop_from_runtime(
            "런타임 상태를 확인해줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(max_steps=2, auto_tools=frozenset({"runtime_status"})),
        )

        self.assertEqual((result.status, result.code), ("failed", "task_worker_failed"))
        self.assertEqual((result.model_call_count, result.observations), (1, ()))
        execute_tool.assert_not_awaited()

    def test_invalid_worker_json_has_exact_retryable_error_code(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^task_worker_response_invalid$",
        ):
            _extract_json_object("{not-json}")

    async def test_pending_candidate_invalid_decisions_exhaust_budget_then_discard(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "fixed",
            "expectedSha256": "a" * 64,
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {
                    "type": "tool",
                    "tool": "workspace_test",
                    "args": {"runner": "python_unittest", "targets": None},
                },
                {
                    "type": "tool",
                    "tool": "workspace_test",
                    "args": {"runner": "python_unittest", "targets": 7},
                },
            ]
        )
        tool_calls: list[str] = []

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append(kwargs["tool"])
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            self.assertEqual(kwargs["tool"], "workspace_edit_stage_cancel")
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        result = await run_task_loop_from_runtime(
            "README.md의 버그를 고쳐줘",
            deps=TaskLoopDeps(
                decide_next=lambda _state: asyncio.sleep(0, result=next(decisions)),
                execute_tool=execute_tool,
                request_approval=lambda _request, _preview: asyncio.sleep(
                    0,
                    result=TaskApprovalResolution("cancelled"),
                ),
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=3),
        )

        self.assertEqual(
            (result.status, result.code, result.step_count),
            ("budget_exhausted", "task_max_steps_exhausted", 3),
        )
        self.assertEqual(
            [item["code"] for item in result.observations[-2:]],
            [
                "task_worker_workspace_test_args_invalid",
                "task_worker_workspace_test_args_invalid",
            ],
        )
        self.assertEqual(tool_calls, ["workspace_edit", "workspace_edit_stage_cancel"])

    async def test_behavioral_mutation_is_not_completed_by_candidate_read(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {"type": "tool", "tool": "workspace_read", "args": {"path": "README.md"}},
                {"type": "final", "summary": "버그 수정 완료", "verified_step": 2},
            ]
        )
        tool_calls: list[str] = []
        approval_calls = 0

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append(kwargs["tool"])
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            if kwargs["tool"] == "workspace_edit_stage_cancel":
                self.assertEqual(kwargs["stage_id"], "stage-approval")
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    code="workspace_edit_stage_cancelled",
                )
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                evidence=_read_evidence(kwargs["args"]["path"]),
            )

        async def request_approval(_request, _preview) -> TaskApprovalResolution:
            nonlocal approval_calls
            approval_calls += 1
            return TaskApprovalResolution("approved", receipt=_approved_edit_result())

        result = await run_task_loop_from_runtime(
            "README.md의 버그를 고쳐줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=3),
        )

        self.assertEqual(
            (result.status, result.code),
            ("budget_exhausted", "task_max_steps_exhausted"),
        )
        self.assertFalse(result.completed)
        self.assertEqual(
            [item["code"] for item in result.observations[-2:]],
            [
                "workspace_test_required_after_stage",
                "workspace_test_required_after_stage",
            ],
        )
        self.assertEqual(
            (tool_calls, approval_calls),
            (["workspace_edit", "workspace_edit_stage_cancel"], 0),
        )

    async def test_behavioral_candidate_failure_replans_then_passes_before_apply(self) -> None:
        first_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "broken",
            "expectedSha256": "a" * 64,
        }
        second_args = {**first_args, "newText": "fixed"}
        first_test_args = {
            "runner": "python_unittest",
            "targets": ["tests/core/test_first.py"],
        }
        second_test_args = {
            "runner": "python_unittest",
            "targets": [
                "tests/core/test_first.py",
                "tests/core/test_revised.py",
            ],
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": first_args},
                {"type": "tool", "tool": "workspace_test", "args": first_test_args},
                {"type": "tool", "tool": "workspace_edit", "args": second_args},
                {"type": "tool", "tool": "workspace_test", "args": second_test_args},
            ]
        )
        staged: list[tuple[str, str, str]] = []
        test_actions: list[str] = []
        approvals = []

        async def decide_next(state: dict) -> dict:
            if state["step"] in {2, 4}:
                self.assertEqual(state["requiredNextTool"], "workspace_test")
            if state["step"] == 4:
                self.assertEqual(
                    state["requiredTestTargets"],
                    ["tests/core/test_first.py"],
                )
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            if kwargs["tool"] == "workspace_edit":
                self.assertIs(kwargs["requires_sandbox_test"], True)
                number = len(staged) + 1
                stage_id = f"stage-{number}"
                candidate_sha = ("b" if number == 1 else "c") * 64
                evidence = _stage_evidence(kwargs["args"], candidate_sha=candidate_sha)
                evidence["stageId"] = stage_id
                staged.append((stage_id, candidate_sha, kwargs["action_run_id"]))
                return _stage_receipt(kwargs, evidence)
            if kwargs["tool"] == "workspace_test":
                # Scaled timeout regression: sandbox work may legitimately
                # outlive the generic workspace step bound.
                await asyncio.sleep(0.03)
                index = len(test_actions)
                stage_id, candidate_sha, edit_action = staged[index]
                self.assertEqual(kwargs["stage_id"], stage_id)
                self.assertNotEqual(kwargs["action_run_id"], edit_action)
                test_actions.append(kwargs["action_run_id"])
                passed = index == 1
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success" if passed else "failed",
                    verified=True,
                    code="workspace_test_passed" if passed else "workspace_test_failed",
                    evidence=_sandbox_test_evidence(
                        kwargs["args"],
                        stage_id=stage_id,
                        candidate_sha=candidate_sha,
                        exit_code=0 if passed else 1,
                    ),
                )
            self.assertEqual(kwargs["tool"], "workspace_read")
            self.assertEqual(kwargs["args"], {"path": "README.md"})
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_read_completed",
                evidence={**_read_evidence(), "sha256": "c" * 64},
            )

        async def request_approval(request, preview) -> TaskApprovalResolution:
            approvals.append((request, preview))
            return TaskApprovalResolution(
                "approved",
                receipt=_approved_edit_result(sha256="c" * 64),
            )

        with (
            patch("evelyn_core.task_loop_runtime.TASK_STEP_TIMEOUT_SEC", 0.01),
            patch(
                "evelyn_core.task_loop_runtime.TASK_SANDBOX_STEP_TIMEOUT_SEC",
                0.1,
            ),
        ):
            result = await run_task_loop_from_runtime(
                "README.md의 버그를 고쳐줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    request_approval=request_approval,
                    monotonic=lambda: 20.0,
                    wall_time=lambda: 20.0,
                ),
                grant=_approval_grant(max_steps=6),
            )

        self.assertEqual(
            (result.status, result.code),
            ("uncertain", "workspace_behavior_outcome_unverified"),
        )
        self.assertFalse(result.completed)
        self.assertEqual((result.model_call_count, len(staged), len(test_actions)), (4, 2, 2))
        self.assertEqual(len(approvals), 1)
        request, preview = approvals[0]
        self.assertEqual(dict(request.args), second_args)
        self.assertEqual(request.action_run_id, staged[1][2])
        self.assertEqual(preview["stageId"], staged[1][0])

    async def test_revised_candidate_must_rerun_every_failed_test_target(self) -> None:
        first_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "broken",
            "expectedSha256": "a" * 64,
        }
        second_args = {**first_args, "newText": "fixed"}
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": first_args},
                {
                    "type": "tool",
                    "tool": "workspace_test",
                    "args": {
                        "runner": "python_unittest",
                        "targets": ["tests/core/test_failed.py"],
                    },
                },
                {"type": "tool", "tool": "workspace_edit", "args": second_args},
                {
                    "type": "tool",
                    "tool": "workspace_test",
                    "args": {
                        "runner": "python_unittest",
                        "targets": ["tests/core/test_easier.py"],
                    },
                },
                {
                    "type": "tool",
                    "tool": "workspace_test",
                    "args": {
                        "runner": "python_unittest",
                        "targets": [
                            "tests/core/test_failed.py",
                            "tests/core/test_easier.py",
                        ],
                    },
                },
            ]
        )
        stages: list[tuple[str, str]] = []
        tests = 0
        discarded: list[str] = []

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            nonlocal tests
            if kwargs["tool"] == "workspace_edit":
                stage_id = f"stage-{len(stages) + 1}"
                candidate_sha = ("b" if not stages else "c") * 64
                evidence = _stage_evidence(kwargs["args"], candidate_sha=candidate_sha)
                evidence["stageId"] = stage_id
                stages.append((stage_id, candidate_sha))
                return _stage_receipt(kwargs, evidence)
            if kwargs["tool"] == "workspace_test":
                tests += 1
                stage_id, candidate_sha = stages[-1]
                passed = tests > 1
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success" if passed else "failed",
                    verified=True,
                    code="workspace_test_passed" if passed else "workspace_test_failed",
                    evidence=_sandbox_test_evidence(
                        kwargs["args"],
                        stage_id=stage_id,
                        candidate_sha=candidate_sha,
                        exit_code=0 if passed else 1,
                    ),
                )
            self.assertEqual(kwargs["tool"], "workspace_edit_stage_cancel")
            discarded.append(kwargs["stage_id"])
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        result = await run_task_loop_from_runtime(
            "실패한 테스트를 찾아 고쳐줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=AsyncMock(
                    return_value=TaskApprovalResolution("cancelled")
                ),
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=5),
        )

        self.assertEqual(
            (result.status, result.code),
            ("cancelled", "task_approval_cancelled"),
        )
        self.assertEqual(
            result.observations[-2]["code"],
            "workspace_test_failed_targets_required",
        )
        self.assertEqual(tests, 2)
        self.assertEqual(discarded, ["stage-2"])

    async def test_behavioral_pass_then_approval_cancel_discards_exact_stage(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "fixed",
            "expectedSha256": "a" * 64,
        }
        test_args = {
            "runner": "python_unittest",
            "targets": ["tests/core/test_example.py"],
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {"type": "tool", "tool": "workspace_test", "args": test_args},
            ]
        )
        edit_action = ""
        discarded: list[tuple[str, str]] = []

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            nonlocal edit_action
            if kwargs["tool"] == "workspace_edit":
                edit_action = kwargs["action_run_id"]
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            if kwargs["tool"] == "workspace_test":
                self.assertEqual(kwargs["stage_id"], "stage-approval")
                self.assertNotEqual(kwargs["action_run_id"], edit_action)
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    code="workspace_test_passed",
                    evidence=_sandbox_test_evidence(
                        kwargs["args"],
                        stage_id="stage-approval",
                        candidate_sha=_READ_SHA256,
                    ),
                )
            self.assertEqual(kwargs["tool"], "workspace_edit_stage_cancel")
            discarded.append((kwargs["stage_id"], kwargs["action_run_id"]))
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        async def request_approval(request, _preview) -> TaskApprovalResolution:
            self.assertEqual(request.action_run_id, edit_action)
            return TaskApprovalResolution("cancelled")

        result = await run_task_loop_from_runtime(
            "README.md의 버그를 고쳐줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=2),
        )

        self.assertEqual((result.status, result.code), ("cancelled", "task_approval_cancelled"))
        self.assertEqual(len(discarded), 1)
        self.assertEqual(discarded[0][0], "stage-approval")
        self.assertEqual(discarded[0][1], edit_action)

    async def test_behavioral_apply_forces_read_before_unverified_terminal(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "fixed",
            "expectedSha256": "a" * 64,
        }
        test_args = {
            "runner": "python_unittest",
            "targets": ["tests/core/test_example.py"],
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {"type": "tool", "tool": "workspace_test", "args": test_args},
            ]
        )
        tool_calls: list[str] = []

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append(kwargs["tool"])
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            if kwargs["tool"] == "workspace_read":
                self.assertEqual(kwargs["args"], {"path": "README.md"})
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    code="workspace_read_completed",
                    evidence=_read_evidence(),
                )
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_test_passed",
                evidence=_sandbox_test_evidence(
                    kwargs["args"],
                    stage_id="stage-approval",
                    candidate_sha=_READ_SHA256,
                ),
            )

        result = await run_task_loop_from_runtime(
            "README.md의 버그를 고쳐줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=lambda _request, _preview: asyncio.sleep(
                    0,
                    result=TaskApprovalResolution(
                        "approved",
                        receipt=_approved_edit_result(),
                    ),
                ),
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=3),
        )

        self.assertEqual(
            (result.status, result.code),
            ("uncertain", "workspace_behavior_outcome_unverified"),
        )
        self.assertFalse(result.completed)
        self.assertEqual(result.model_call_count, 2)
        self.assertEqual(tool_calls, ["workspace_edit", "workspace_test", "workspace_read"])

    async def test_unbound_behavioral_test_is_denied_and_stage_is_discarded(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "fixed",
            "expectedSha256": "a" * 64,
        }
        test_args = {
            "runner": "python_unittest",
            "targets": ["tests/core/test_example.py"],
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {"type": "tool", "tool": "workspace_test", "args": test_args},
            ]
        )
        discarded = 0
        approvals = 0

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            nonlocal discarded
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            if kwargs["tool"] == "workspace_edit_stage_cancel":
                discarded += 1
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    code="workspace_edit_stage_cancelled",
                )
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_test_passed",
                evidence=_sandbox_test_evidence(
                    kwargs["args"],
                    stage_id="stage-forged",
                    candidate_sha="b" * 64,
                ),
            )

        async def request_approval(_request, _preview) -> TaskApprovalResolution:
            nonlocal approvals
            approvals += 1
            raise AssertionError("unbound test must not reach approval")

        result = await run_task_loop_from_runtime(
            "README.md의 버그를 고쳐줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=2),
        )

        self.assertEqual(
            (result.status, result.code),
            ("blocked", "workspace_test_stage_binding_invalid"),
        )
        self.assertEqual((approvals, discarded), (0, 1))

    async def test_behavioral_stage_fails_closed_when_sandbox_is_unavailable(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "fixed",
            "expectedSha256": "a" * 64,
        }
        calls: list[str] = []

        async def decide_next(_state: dict) -> dict:
            return {"type": "tool", "tool": "workspace_edit", "args": edit_args}

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            calls.append(kwargs["tool"])
            self.assertIs(kwargs["requires_sandbox_test"], True)
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="failed",
                verified=True,
                executed=False,
                code="workspace_test_sandbox_unavailable",
            )

        result = await run_task_loop_from_runtime(
            "README.md의 버그를 고쳐줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=AsyncMock(side_effect=AssertionError("no approval")),
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=1),
        )

        self.assertEqual(
            (result.status, result.code),
            ("blocked", "workspace_test_sandbox_unavailable"),
        )
        self.assertEqual(calls, ["workspace_edit"])

    async def test_cancellation_while_candidate_waits_discards_exact_stage(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "fixed",
            "expectedSha256": "a" * 64,
        }
        waiting = asyncio.Event()
        discarded = asyncio.Event()
        decisions = 0

        async def decide_next(_state: dict) -> dict:
            nonlocal decisions
            decisions += 1
            if decisions == 1:
                return {"type": "tool", "tool": "workspace_edit", "args": edit_args}
            waiting.set()
            await asyncio.Future()
            raise AssertionError("unreachable")

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            self.assertEqual(kwargs["tool"], "workspace_edit_stage_cancel")
            self.assertEqual(kwargs["stage_id"], "stage-approval")
            discarded.set()
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        task = asyncio.create_task(
            run_task_loop_from_runtime(
                "README.md의 버그를 고쳐줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    request_approval=AsyncMock(),
                    monotonic=asyncio.get_running_loop().time,
                    wall_time=lambda: 20.0,
                ),
                grant=_approval_grant(max_steps=2),
            )
        )
        await asyncio.wait_for(waiting.wait(), 0.2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(discarded.is_set())

    async def test_nested_exact_create_reaches_approval_without_sandbox(self) -> None:
        create_args = {
            "mode": "create",
            "path": "tools/canary.txt",
            "newText": "exact content",
        }
        tool_calls: list[dict] = []
        approvals = 0

        async def decide_next(_state: dict) -> dict:
            return {
                "type": "tool",
                "tool": "workspace_edit",
                "args": {"create": create_args},
            }

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append(kwargs)
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            self.assertEqual(kwargs["tool"], "workspace_edit_stage_cancel")
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        async def request_approval(_request, _preview) -> TaskApprovalResolution:
            nonlocal approvals
            approvals += 1
            return TaskApprovalResolution("cancelled")

        result = await run_task_loop_from_runtime(
            'Create tools/canary.txt with content "exact content"',
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=2),
        )

        self.assertEqual((result.status, result.code), ("cancelled", "task_approval_cancelled"))
        self.assertEqual(approvals, 1)
        self.assertEqual(tool_calls[0]["args"], create_args)
        self.assertIs(tool_calls[0]["requires_sandbox_test"], False)

    async def test_invalid_workspace_edit_args_are_returned_to_worker(self) -> None:
        decisions = iter(
            [
                {
                    "type": "tool",
                    "tool": "workspace_edit",
                    "args": {"path": "tools/canary.txt", "unexpected": "value"},
                },
                {
                    "type": "ask_user",
                    "question": "편집 인자 형식을 다시 확인해줘.",
                },
            ]
        )
        states: list[dict] = []
        execute_tool = AsyncMock()

        async def decide_next(state: dict) -> dict:
            states.append(state)
            return next(decisions)

        result = await run_task_loop_from_runtime(
            "tools/canary.txt를 수정해줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=AsyncMock(),
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=2),
        )

        self.assertEqual((result.status, result.code), ("awaiting_approval", "task_user_input_required"))
        execute_tool.assert_not_awaited()
        self.assertEqual(
            states[1]["observations"][0]["code"],
            "task_worker_workspace_edit_args_invalid",
        )

    def test_exact_content_gate_rejects_fix_or_solve_semantics(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        self.assertTrue(
            _mutation_goal_is_exact_content(
                "README.md에서 `old`를 `new`로 바꿔줘",
                edit_args,
            )
        )
        self.assertTrue(
            _mutation_goal_is_exact_content(
                "Change `old` to `new` in README.md",
                edit_args,
            )
        )
        self.assertFalse(
            _mutation_goal_is_exact_content(
                "README.md에서 `old`를 `new`로 바꿔줘",
                {**edit_args, "path": "other.py"},
            )
        )
        for goal in (
            "README.md에서 `old`를 `new`로 바꿔서 고쳐줘",
            "README.md에서 `old`를 `new`로 바꿔서 해결해줘",
            "change `old` to `new` in README.md to fix it",
            "change `old` to `new` in README.md to solve the problem",
            "Remediate the issue in README.md: replace `old` with `new`",
            "Prevent a crash in README.md by changing `old` to `new`",
            "Make it work in README.md: replace `old` with `new`",
            "Address the race in README.md: replace `old` with `new`",
        ):
            with self.subTest(goal=goal):
                self.assertFalse(
                    _mutation_goal_is_exact_content(goal, edit_args)
                )
        create_args = {
            "mode": "create",
            "path": "notes.txt",
            "newText": "hello",
        }
        self.assertTrue(
            _mutation_goal_is_exact_content(
                'Create notes.txt with content "hello"',
                create_args,
            )
        )
        self.assertFalse(
            _mutation_goal_is_exact_content(
                'Create notes.txt with content "hello" to prevent a crash',
                create_args,
            )
        )

    def test_workspace_edit_decision_unwraps_matching_mode_envelope(self) -> None:
        create_args = {
            "mode": "create",
            "path": "tools/canary.txt",
            "newText": "exact content",
        }

        decision = _normalize_decision(
            {
                "type": "tool",
                "tool": "workspace_edit",
                "args": {"create": create_args},
            }
        )

        self.assertEqual(decision["args"], create_args)
        self.assertTrue(
            _mutation_goal_is_exact_content(
                'Create tools/canary.txt with content "exact content"',
                decision["args"],
            )
        )

    def test_workspace_edit_decision_keeps_mismatched_envelope_invalid(self) -> None:
        nested_args = {
            "mode": "replace",
            "path": "tools/canary.txt",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }

        decision = _normalize_decision(
            {
                "type": "tool",
                "tool": "workspace_edit",
                "args": {"create": nested_args},
            }
        )

        self.assertEqual(decision["args"], {"create": nested_args})

    def test_workspace_edit_decision_normalizes_content_alias(self) -> None:
        decision = _normalize_decision(
            {
                "type": "tool",
                "tool": "workspace_edit",
                "args": {
                    "path": "tools/canary.txt",
                    "content": "exact content",
                },
            }
        )

        self.assertEqual(
            decision["args"],
            {
                "mode": "create",
                "path": "tools/canary.txt",
                "newText": "exact content",
            },
        )

    def test_behavioral_completion_requires_candidate_bound_test_receipt(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        test_args = {"runner": "python_unittest", "targets": ["tests/test_a.py"]}
        edit_receipt = _receipt(
            step_id=1,
            tool="workspace_edit",
            action_run_id="action-edit",
            grant_id="grant-test",
            outcome="success",
            verified=True,
            evidence={"path": "README.md", "sha256": "b" * 64},
        )

        read_receipt = _receipt(
            step_id=3,
            tool="workspace_read",
            action_run_id="action-read",
            grant_id="grant-test",
            outcome="success",
            verified=True,
            evidence={**_read_evidence(), "sha256": "b" * 64},
        )

        def matches(test_evidence: dict) -> bool:
            test_receipt = _receipt(
                step_id=2,
                tool="workspace_test",
                action_run_id="action-test",
                grant_id="grant-test",
                outcome="success",
                verified=True,
                code="workspace_test_passed",
                evidence=test_evidence,
            )
            return _behavioral_mutation_evidence_matches(
                goal="README.md의 버그를 고쳐줘",
                verified_step=3,
                successful_actions={
                    1: ("workspace_edit", edit_args, edit_receipt),
                    2: ("workspace_test", test_args, test_receipt),
                    3: ("workspace_read", {"path": "README.md"}, read_receipt),
                },
            )

        unbound = _sandbox_test_evidence(
            test_args,
            stage_id="stage-bound",
            candidate_sha="c" * 64,
        )
        bound = {
            **unbound,
            "candidatePath": "README.md",
            "candidateSha256": "b" * 64,
        }
        self.assertFalse(matches(unbound))
        self.assertTrue(matches(bound))
        bound_receipt = _receipt(
            step_id=2,
            tool="workspace_test",
            action_run_id="action-test",
            grant_id="grant-test",
            outcome="success",
            verified=True,
            code="workspace_test_passed",
            evidence=bound,
        )
        self.assertFalse(
            _completion_evidence_matches(
                goal="README.md의 버그를 고쳐줘",
                verified_step=3,
                latest_observation_step=3,
                successful_actions={
                    1: ("workspace_edit", edit_args, edit_receipt),
                    2: ("workspace_test", test_args, bound_receipt),
                    3: ("workspace_read", {"path": "README.md"}, read_receipt),
                },
            )
        )
        for tests_run in (None, 0, 1_000_000, True):
            with self.subTest(tests_run=tests_run):
                invalid = dict(bound)
                if tests_run is None:
                    invalid.pop("testsRun", None)
                else:
                    invalid["testsRun"] = tests_run
                self.assertFalse(matches(invalid))
        for semantic_verified in (None, True):
            with self.subTest(semantic_verified=semantic_verified):
                invalid = dict(bound)
                if semantic_verified is None:
                    invalid.pop("semanticVerified", None)
                else:
                    invalid["semanticVerified"] = semantic_verified
                self.assertFalse(matches(invalid))

    def test_behavioral_completion_accepts_a_candidate_path_discovered_during_work(self) -> None:
        candidate_sha = _READ_SHA256
        edit_args = {
            "mode": "replace",
            "path": "evelyn_core/runtime/evelyn_core/example.py",
            "oldText": "old",
            "newText": "fixed",
            "expectedSha256": "a" * 64,
        }
        test_args = {
            "runner": "python_unittest",
            "targets": ["tests/core/test_example.py"],
        }
        edit_receipt = _receipt(
            step_id=1,
            tool="workspace_edit",
            action_run_id="action-edit",
            grant_id="grant-test",
            outcome="success",
            verified=True,
            evidence={"path": edit_args["path"], "sha256": candidate_sha},
        )
        test_receipt = _receipt(
            step_id=2,
            tool="workspace_test",
            action_run_id="action-test",
            grant_id="grant-test",
            outcome="success",
            verified=True,
            code="workspace_test_passed",
            evidence={
                **_sandbox_test_evidence(
                    test_args,
                    stage_id="stage-discovered",
                    candidate_sha=candidate_sha,
                ),
                "candidatePath": edit_args["path"],
            },
        )
        read_receipt = _receipt(
            step_id=3,
            tool="workspace_read",
            action_run_id="action-read",
            grant_id="grant-test",
            outcome="success",
            verified=True,
            evidence={
                **_read_evidence(edit_args["path"]),
                "sha256": candidate_sha,
            },
        )
        successful = {
            1: ("workspace_edit", edit_args, edit_receipt),
            2: ("workspace_test", test_args, test_receipt),
            3: ("workspace_read", {"path": edit_args["path"]}, read_receipt),
        }

        self.assertTrue(
            _behavioral_mutation_evidence_matches(
                goal="실패한 테스트를 찾아 고쳐줘",
                verified_step=3,
                successful_actions=successful,
            )
        )
        self.assertFalse(
            _completion_evidence_matches(
                goal="실패한 테스트를 찾아 고쳐줘",
                verified_step=3,
                latest_observation_step=3,
                successful_actions=successful,
            )
        )
        self.assertFalse(
            _applied_mutation_awaits_workspace_read(
                goal="실패한 테스트를 찾아 고쳐줘",
                successful_actions=successful,
            )
        )

    async def test_mutation_receipt_is_not_its_own_completion_verifier(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        decisions = iter(
            [
                {
                    "type": "tool",
                    "tool": "workspace_edit",
                    "args": edit_args,
                },
            ]
        )

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return _stage_receipt(
                kwargs,
                _stage_evidence(kwargs["args"]),
            )

        async def request_approval(_request, _preview) -> TaskApprovalResolution:
            return TaskApprovalResolution("approved", receipt=_approved_edit_result())

        result = await run_task_loop_from_runtime(
            "README.md에서 `old`를 `new`로 바꿔줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=1),
        )

        self.assertEqual(
            (result.status, result.code, result.step_count, result.model_call_count),
            ("budget_exhausted", "task_max_steps_exhausted", 1, 1),
        )

    async def test_effect_without_verified_postcondition_is_terminal_and_not_retried(self) -> None:
        model_calls = 0
        tool_calls = 0

        async def decide_next(_state: dict) -> dict:
            nonlocal model_calls
            model_calls += 1
            return {"type": "tool", "tool": "runtime_status", "args": {}}

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            nonlocal tool_calls
            tool_calls += 1
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=False,
            )

        result = await run_task_loop_from_runtime(
            "런타임 상태를 확인해줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(auto_tools=frozenset({"runtime_status"})),
        )

        self.assertEqual(result.status, "uncertain")
        self.assertEqual(result.code, "task_tool_receipt_invalid")
        self.assertEqual((model_calls, tool_calls), (1, 1))

    async def test_approval_and_forbidden_tools_never_reach_executor(self) -> None:
        for tool, expected_status in (
            ("service_restart", "awaiting_approval"),
            ("unrestricted_shell", "blocked"),
        ):
            with self.subTest(tool=tool):
                tool_calls = 0

                async def decide_next(_state: dict, selected: str = tool) -> dict:
                    return {"type": "tool", "tool": selected, "args": {}}

                async def execute_tool(**_kwargs) -> TaskStepReceipt:
                    nonlocal tool_calls
                    tool_calls += 1
                    raise AssertionError("executor must not run")

                result = await run_task_loop_from_runtime(
                    "위험 작업",
                    deps=TaskLoopDeps(
                        decide_next=decide_next,
                        execute_tool=execute_tool,
                        monotonic=lambda: 20.0,
                        wall_time=lambda: 20.0,
                    ),
                    grant=_grant(),
                )
                self.assertEqual(result.status, expected_status)
                self.assertEqual(tool_calls, 0)

    async def test_step_budget_is_hard_and_does_not_make_a_seventh_call(self) -> None:
        model_calls = 0

        async def decide_next(_state: dict) -> dict:
            nonlocal model_calls
            model_calls += 1
            return {"type": "tool", "tool": "workspace_read", "args": {"path": "README.md"}}

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="failed",
                verified=True,
            )

        result = await run_task_loop_from_runtime(
            "읽어줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(max_steps=2),
        )

        self.assertEqual(result.status, "budget_exhausted")
        self.assertEqual((result.step_count, model_calls), (2, 2))

    async def test_worker_await_is_cut_off_by_task_deadline(self) -> None:
        async def decide_next(_state: dict) -> dict:
            await asyncio.sleep(1.0)
            raise AssertionError("deadline must cancel worker wait")

        result = await run_task_loop_from_runtime(
            "상태를 확인해줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=AsyncMock(),
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(deadline_sec=0.01),
        )

        self.assertEqual(result.status, "budget_exhausted")
        self.assertEqual(result.code, "task_worker_timeout")

    async def test_worker_cannot_return_tool_after_swallowing_deadline(self) -> None:
        async def decide_next(_state: dict) -> dict:
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                return {
                    "type": "tool",
                    "tool": "web_search",
                    "args": {"query": "Evelyn"},
                }

        execute_tool = AsyncMock()
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await run_task_loop_from_runtime(
            "웹에서 Evelyn 검색해줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=loop.time,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(
                deadline_sec=0.01,
                auto_tools=frozenset({"web_search"}),
            ),
        )

        self.assertEqual(
            (result.status, result.code),
            ("budget_exhausted", "task_deadline_exhausted"),
        )
        execute_tool.assert_not_awaited()
        self.assertLess(loop.time() - started, 0.2)

    async def test_worker_cap_cannot_be_swallowed_into_completed_result(self) -> None:
        model_calls = 0

        async def decide_next(state: dict) -> dict:
            nonlocal model_calls
            model_calls += 1
            if state["observations"]:
                return {"type": "final", "summary": "done", "verified_step": 1}
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {"type": "tool", "tool": "runtime_status", "args": {}}

        execute_tool = AsyncMock(
            side_effect=lambda **kwargs: _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="runtime_status_collected",
                evidence=_runtime_evidence(),
            )
        )
        with patch(
            "evelyn_core.task_loop_runtime.TASK_WORKER_WAIT_TIMEOUT_SEC",
            0.01,
        ):
            result = await run_task_loop_from_runtime(
                "런타임 상태를 확인해줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    monotonic=asyncio.get_running_loop().time,
                    wall_time=lambda: 20.0,
                ),
                grant=_grant(
                    max_steps=2,
                    deadline_sec=1.0,
                    auto_tools=frozenset({"runtime_status"}),
                ),
            )

        self.assertEqual(
            (result.status, result.code),
            ("budget_exhausted", "task_worker_timeout"),
        )
        self.assertEqual((result.model_call_count, model_calls), (1, 1))
        execute_tool.assert_not_awaited()

    async def test_worker_cannot_execute_after_swallowing_external_cancellation(
        self,
    ) -> None:
        entered = asyncio.Event()
        tool_calls: list[str] = []

        async def decide_next(state: dict) -> dict:
            if state["observations"]:
                return {"type": "final", "summary": "done", "verified_step": 1}
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {"type": "tool", "tool": "runtime_status", "args": {}}

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append(kwargs["tool"])
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="runtime_status_collected",
                evidence=_runtime_evidence(),
            )

        running = asyncio.create_task(
            run_task_loop_from_runtime(
                "런타임 상태를 확인해줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    monotonic=lambda: 20.0,
                    wall_time=lambda: 20.0,
                ),
                grant=_grant(
                    max_steps=2,
                    auto_tools=frozenset({"runtime_status"}),
                ),
            )
        )
        await entered.wait()
        running.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await running
        self.assertEqual(tool_calls, [])

    async def test_stage_and_test_cannot_return_failure_after_swallowing_external_cancellation(
        self,
    ) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        test_args = {
            "runner": "python_unittest",
            "targets": ["tests/core/test_example.py"],
        }

        for selected in ("workspace_edit", "workspace_test"):
            with self.subTest(selected=selected):
                entered = asyncio.Event()
                decisions = iter(
                    [{"type": "tool", "tool": "workspace_edit", "args": edit_args}]
                    + (
                        [{"type": "tool", "tool": "workspace_test", "args": test_args}]
                        if selected == "workspace_test"
                        else []
                    )
                )

                async def decide_next(_state: dict) -> dict:
                    return next(decisions)

                async def execute_tool(**kwargs) -> TaskStepReceipt | dict:
                    tool = kwargs["tool"]
                    if tool == "workspace_edit" and selected == "workspace_test":
                        return _stage_receipt(
                            kwargs,
                            _stage_evidence(kwargs["args"]),
                        )
                    if tool == "workspace_edit_stage_cancel":
                        return _receipt(
                            step_id=kwargs["step_id"],
                            tool=tool,
                            action_run_id=kwargs["action_run_id"],
                            grant_id=kwargs["grant_id"],
                            outcome="success",
                            verified=True,
                            code="workspace_edit_stage_cancelled",
                        )
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if selected == "workspace_edit":
                            return _receipt(
                                step_id=kwargs["step_id"],
                                tool=tool,
                                action_run_id=kwargs["action_run_id"],
                                grant_id=kwargs["grant_id"],
                                outcome="failed",
                                verified=True,
                                code="workspace_edit_stage_failed",
                            )
                        return {"attempted": "invalid"}

                request_approval = AsyncMock(
                    side_effect=AssertionError("cancelled task must not request approval")
                )
                running = asyncio.create_task(
                    run_task_loop_from_runtime(
                        (
                            "README.md의 버그를 고쳐줘"
                            if selected == "workspace_test"
                            else "README.md에서 `old`를 `new`로 바꿔줘"
                        ),
                        deps=TaskLoopDeps(
                            decide_next=decide_next,
                            execute_tool=execute_tool,
                            request_approval=request_approval,
                            monotonic=lambda: 20.0,
                            wall_time=lambda: 20.0,
                        ),
                        grant=_approval_grant(max_steps=2),
                    )
                )
                await entered.wait()
                running.cancel()

                with self.assertRaises(asyncio.CancelledError):
                    await running
                request_approval.assert_not_awaited()

    async def test_auto_tool_cap_cannot_be_swallowed_into_completed_result(self) -> None:
        decisions = iter(
            [
                {"type": "tool", "tool": "runtime_status", "args": {}},
                {"type": "final", "summary": "done", "verified_step": 1},
            ]
        )
        tool_calls = 0

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            nonlocal tool_calls
            tool_calls += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    code="runtime_status_collected",
                    evidence=_runtime_evidence(),
                )

        with patch("evelyn_core.task_loop_runtime.TASK_STEP_TIMEOUT_SEC", 0.01):
            result = await run_task_loop_from_runtime(
                "런타임 상태를 확인해줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    monotonic=asyncio.get_running_loop().time,
                    wall_time=lambda: 20.0,
                ),
                grant=_grant(
                    max_steps=2,
                    deadline_sec=1.0,
                    auto_tools=frozenset({"runtime_status"}),
                ),
            )

        self.assertEqual(
            (result.status, result.code),
            ("uncertain", "task_tool_timeout"),
        )
        self.assertEqual(tool_calls, 1)

    async def test_stage_cap_does_not_approve_and_cleans_exact_late_stage(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        tool_calls: list[tuple[str, str]] = []

        async def decide_next(_state: dict) -> dict:
            return {"type": "tool", "tool": "workspace_edit", "args": edit_args}

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            if kwargs["tool"] == "workspace_edit":
                tool_calls.append((kwargs["tool"], ""))
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            tool_calls.append((kwargs["tool"], kwargs["stage_id"]))
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        request_approval = AsyncMock()
        with patch("evelyn_core.task_loop_runtime.TASK_STEP_TIMEOUT_SEC", 0.01):
            result = await run_task_loop_from_runtime(
                "README.md에서 `old`를 `new`로 바꿔줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    request_approval=request_approval,
                    monotonic=asyncio.get_running_loop().time,
                    wall_time=lambda: 20.0,
                ),
                grant=_approval_grant(max_steps=1, deadline_sec=1.0),
            )

        self.assertEqual(
            (result.status, result.code),
            ("uncertain", "workspace_edit_stage_timeout"),
        )
        request_approval.assert_not_awaited()
        self.assertEqual(
            tool_calls,
            [("workspace_edit", ""), ("workspace_edit_stage_cancel", "stage-approval")],
        )

    async def test_sandbox_test_cap_does_not_approve_and_cleans_stage(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        test_args = {
            "runner": "python_unittest",
            "targets": ["tests/core/test_example.py"],
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {"type": "tool", "tool": "workspace_test", "args": test_args},
            ]
        )
        tool_calls: list[tuple[str, str]] = []

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool = kwargs["tool"]
            if tool == "workspace_edit":
                tool_calls.append((tool, ""))
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            if tool == "workspace_test":
                tool_calls.append((tool, kwargs["stage_id"]))
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return _receipt(
                        step_id=kwargs["step_id"],
                        tool=tool,
                        action_run_id=kwargs["action_run_id"],
                        grant_id=kwargs["grant_id"],
                        outcome="success",
                        verified=True,
                        code="workspace_test_passed",
                        evidence=_sandbox_test_evidence(
                            kwargs["args"],
                            stage_id=kwargs["stage_id"],
                            candidate_sha=_READ_SHA256,
                        ),
                    )
            tool_calls.append((tool, kwargs["stage_id"]))
            return _receipt(
                step_id=kwargs["step_id"],
                tool=tool,
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        request_approval = AsyncMock()
        with patch(
            "evelyn_core.task_loop_runtime.TASK_SANDBOX_STEP_TIMEOUT_SEC",
            0.01,
        ):
            result = await run_task_loop_from_runtime(
                "README.md의 동작 버그를 고쳐줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    request_approval=request_approval,
                    monotonic=asyncio.get_running_loop().time,
                    wall_time=lambda: 20.0,
                ),
                grant=_approval_grant(max_steps=2, deadline_sec=1.0),
            )

        self.assertEqual(
            (result.status, result.code),
            ("uncertain", "workspace_test_timeout"),
        )
        request_approval.assert_not_awaited()
        self.assertEqual(
            tool_calls,
            [
                ("workspace_edit", ""),
                ("workspace_test", "stage-approval"),
                ("workspace_edit_stage_cancel", "stage-approval"),
            ],
        )

    async def test_tool_executor_is_not_entered_when_deadline_expires_before_dispatch(
        self,
    ) -> None:
        monotonic_samples = iter((0.0, 0.0, 0.0, 0.0, 1.0))
        tool_calls: list[str] = []

        def monotonic() -> float:
            return next(monotonic_samples, 1.0)

        async def decide_next(_state: dict) -> dict:
            return {"type": "tool", "tool": "runtime_status", "args": {}}

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append(kwargs["tool"])
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
            )

        result = await run_task_loop_from_runtime(
            "상태를 확인해줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=monotonic,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(
                deadline_sec=1.0,
                auto_tools=frozenset({"runtime_status"}),
            ),
        )

        self.assertEqual(
            (result.status, result.code),
            ("budget_exhausted", "task_deadline_exhausted"),
        )
        self.assertEqual(tool_calls, [])

    async def test_tool_executor_is_not_entered_after_grant_expiry(self) -> None:
        async def decide_next(_state: dict) -> dict:
            return {
                "type": "tool",
                "tool": "runtime_status",
                "args": {},
            }

        tool_calls: list[str] = []

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append(kwargs["tool"])
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                evidence=_runtime_evidence(),
            )

        wall_times = iter((20.0, 20.0, 20.0, 20.0, 30.0))
        result = await run_task_loop_from_runtime(
            "런타임 상태를 확인해줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: next(wall_times),
            ),
            grant=_grant(
                auto_tools=frozenset({"runtime_status"}),
                expires_at=25.0,
            ),
        )

        self.assertEqual(
            (result.status, result.code),
            ("failed", "task_grant_expired"),
        )
        self.assertEqual(tool_calls, [])

    async def test_approval_is_not_entered_when_stage_returns_after_deadline(
        self,
    ) -> None:
        monotonic = [0.0]
        approval_calls = 0
        tool_calls: list[str] = []
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }

        async def decide_next(_state: dict) -> dict:
            return {"type": "tool", "tool": "workspace_edit", "args": edit_args}

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append(kwargs["tool"])
            if kwargs["tool"] == "workspace_edit":
                monotonic[0] = 121.0
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        async def request_approval(_request, _preview) -> TaskApprovalResolution:
            nonlocal approval_calls
            approval_calls += 1
            return TaskApprovalResolution(
                "approved",
                receipt=_approved_edit_result(),
            )

        result = await run_task_loop_from_runtime(
            "README.md에서 `old`를 `new`로 바꿔줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: monotonic[0],
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=2, deadline_sec=120.0),
        )

        self.assertEqual(
            (result.status, result.code),
            ("budget_exhausted", "task_deadline_exhausted"),
        )
        self.assertEqual(approval_calls, 0)
        self.assertEqual(
            tool_calls,
            ["workspace_edit", "workspace_edit_stage_cancel"],
        )
        self.assertEqual(result.observations, ())

    async def test_worker_outer_wait_includes_queue_budget_before_inference(self) -> None:
        async def decide_next(_state: dict) -> dict:
            await asyncio.sleep(0.02)
            return {
                "type": "ask_user",
                "question": "추가 입력이 필요해.",
            }

        with (
            patch(
                "evelyn_core.task_loop_runtime.TASK_WORKER_TIMEOUT_SEC",
                0.005,
            ),
            patch(
                "evelyn_core.task_loop_runtime.TASK_WORKER_WAIT_TIMEOUT_SEC",
                0.5,
            ),
        ):
            result = await run_task_loop_from_runtime(
                "상태를 확인해줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=AsyncMock(),
                    monotonic=lambda: 20.0,
                    wall_time=lambda: 20.0,
                ),
                grant=_grant(),
            )

        self.assertEqual(
            (result.status, result.code),
            ("awaiting_approval", "task_user_input_required"),
        )

    async def test_non_boolean_receipt_flags_are_terminal_uncertain(self) -> None:
        async def decide_next(_state: dict) -> dict:
            return {
                "type": "tool",
                "tool": "workspace_read",
                "args": {"path": "README.md"},
            }

        async def execute_tool(**_kwargs) -> dict:
            return {
                "attempted": "false",
                "executed": "false",
                "observed": "false",
                "verified": "false",
                "outcome": "success",
                "code": "forged",
                "summary": "forged",
                "evidence": "",
            }

        result = await run_task_loop_from_runtime(
            "읽어줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(),
        )

        self.assertEqual(result.status, "uncertain")
        self.assertEqual(result.code, "task_tool_receipt_invalid")

    async def test_cancelled_turn_stops_before_tool_execution(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        tool_calls = 0

        async def decide_next(_state: dict) -> dict:
            entered.set()
            await release.wait()
            return {"type": "tool", "tool": "workspace_read", "args": {"path": "README.md"}}

        async def execute_tool(**_kwargs) -> TaskStepReceipt:
            nonlocal tool_calls
            tool_calls += 1
            raise AssertionError("cancelled task must not execute")

        scope = TurnScope(turn_id="turn-task-test")
        task = asyncio.create_task(
            run_task_loop_from_runtime(
                "읽어줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    monotonic=lambda: 20.0,
                    wall_time=lambda: 20.0,
                ),
                grant=_grant(),
                turn_scope=scope,
            )
        )
        await entered.wait()
        scope.cancel()
        release.set()

        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(tool_calls, 0)

    async def test_workspace_tool_maps_host_receipt_to_exact_task_binding(self) -> None:
        class Client:
            def execute(self, task_id, step_id, tool, args, **binding):
                self.call = (task_id, step_id, tool, args, binding)
                return {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_read_completed",
                    "summary": "read",
                    "evidence": {"path": args["path"]},
                }

        client = Client()
        receipt = await execute_default_task_tool(
            task_id="task-test",
            step_id=2,
            tool="workspace_read",
            args={"path": "README.md"},
            action_run_id="action-test",
            grant_id="grant-test",
            surface="control_page",
            workspace_client=client,
        )

        self.assertEqual(
            client.call,
            (
                "task-test",
                2,
                "read",
                {"path": "README.md"},
                {
                    "grant_id": "grant-test",
                    "action_run_id": "action-test",
                    "surface": "control_page",
                },
            ),
        )
        self.assertEqual(receipt.outcome, "success")
        self.assertEqual((receipt.action_run_id, receipt.grant_id), ("action-test", "grant-test"))

    async def test_default_runtime_status_uses_bounded_public_schema_receipt(self) -> None:
        with patch(
            "evelyn_core.runtime_health.collect_runtime_health",
            new=AsyncMock(
                return_value={
                    "ok": True,
                    "coreState": "up",
                    "overallState": "up",
                    "services": [{"name": "private-detail"}] * 100,
                }
            ),
        ):
            receipt = await execute_default_task_tool(
                task_id="task-health",
                step_id=1,
                tool="runtime_status",
                args={},
                action_run_id="action-health",
                grant_id="grant-health",
                surface="control_page",
            )

        evidence = receipt.verification_evidence
        self.assertEqual(
            evidence,
            {
                "schema": "runtime_health.public.v1",
                "ok": True,
                "coreState": "up",
                "overallState": "up",
            },
        )
        self.assertTrue(
            _completion_evidence_matches(
                goal="Check runtime status",
                verified_step=1,
                latest_observation_step=1,
                successful_actions={1: ("runtime_status", {}, receipt)},
            )
        )

    async def test_default_runtime_status_rejects_swallowed_inner_timeout(self) -> None:
        real_timeout = asyncio.timeout
        cancellation_seen = False

        async def collect_runtime_health() -> dict:
            nonlocal cancellation_seen
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_seen = True
                return {"ok": True}

        with (
            patch(
                "evelyn_core.task_loop_runtime.asyncio.timeout",
                side_effect=lambda _seconds: real_timeout(0.01),
            ),
            patch(
                "evelyn_core.runtime_health.collect_runtime_health",
                new=collect_runtime_health,
            ),
            patch(
                "evelyn_core.runtime_health.public_runtime_health_snapshot",
                return_value=_runtime_evidence(),
            ),
        ):
            receipt = await execute_default_task_tool(
                task_id="task-health",
                step_id=1,
                tool="runtime_status",
                args={},
                action_run_id="action-health",
                grant_id="grant-health",
                surface="control_page",
            )

        self.assertTrue(cancellation_seen)
        self.assertEqual(
            (receipt.outcome, receipt.code),
            ("failed", "runtime_status_failed"),
        )

    async def test_task_web_search_preserves_goal_bound_query_before_egress(self) -> None:
        search = AsyncMock(
            return_value=[
                {
                    "title": "weather",
                    "snippet": "result",
                    "url": "https://example.test/weather",
                }
            ]
        )
        with patch("evelyn_core.search_tools.search_duckduckgo", new=search):
            receipt = await execute_default_task_tool(
                task_id="task-web",
                step_id=1,
                tool="web_search",
                args={"query": "오늘 날씨"},
                action_run_id="action-web",
                grant_id="grant-web",
                surface="control_page",
            )

        search.assert_awaited_once_with(
            "오늘 날씨",
            limit=2,
            exact_query=True,
        )
        self.assertEqual(receipt.verification_evidence["query"], "오늘 날씨")
        self.assertTrue(
            _completion_evidence_matches(
                goal="웹에서 오늘 날씨를 검색해줘",
                verified_step=1,
                latest_observation_step=1,
                successful_actions={
                    1: ("web_search", {"query": "오늘 날씨"}, receipt)
                },
            )
        )

    async def test_default_web_search_rejects_swallowed_inner_timeout(self) -> None:
        real_timeout = asyncio.timeout
        cancellation_seen = False

        async def search_duckduckgo(
            _query: str,
            *,
            limit: int,
            exact_query: bool,
        ) -> list[dict]:
            nonlocal cancellation_seen
            self.assertEqual((limit, exact_query), (2, True))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_seen = True
                return [
                    {
                        "title": "late",
                        "snippet": "late result",
                        "url": "https://example.test/late",
                    }
                ]

        with (
            patch(
                "evelyn_core.task_loop_runtime.asyncio.timeout",
                side_effect=lambda _seconds: real_timeout(0.01),
            ),
            patch(
                "evelyn_core.search_tools.search_duckduckgo",
                new=search_duckduckgo,
            ),
        ):
            receipt = await execute_default_task_tool(
                task_id="task-web",
                step_id=1,
                tool="web_search",
                args={"query": "late result"},
                action_run_id="action-web",
                grant_id="grant-web",
                surface="control_page",
            )

        self.assertTrue(cancellation_seen)
        self.assertEqual(
            (receipt.outcome, receipt.code),
            ("failed", "web_search_failed"),
        )

    async def test_default_workspace_candidate_calls_use_bound_sandbox_client_apis(self) -> None:
        calls: list[tuple] = []

        class Client:
            def stage_edit(self, task_id, step_id, args, **binding):
                calls.append(("stage", task_id, step_id, args, binding))
                return {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_edit_staged",
                    "summary": "staged",
                    "evidence": {},
                }

            def test_staged_candidate(self, task_id, step_id, args, **binding):
                calls.append(("test", task_id, step_id, args, binding))
                return {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_test_passed",
                    "summary": "passed",
                    "evidence": {},
                }

            def discard_staged_candidate(self, task_id, step_id, **binding):
                calls.append(("discard", task_id, step_id, {}, binding))
                return {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_edit_stage_cancelled",
                    "summary": "discarded",
                    "evidence": {},
                }

        client = Client()
        common = {
            "task_id": "task-test",
            "grant_id": "grant-test",
            "surface": "control_page",
            "workspace_client": client,
        }
        await execute_default_task_tool(
            **common,
            step_id=1,
            tool="workspace_edit",
            args={"mode": "create", "path": "new.txt", "newText": "x"},
            action_run_id="action-edit",
            requires_sandbox_test=True,
        )
        await execute_default_task_tool(
            **common,
            step_id=2,
            tool="workspace_test",
            args={"runner": "python_unittest", "targets": ["tests/test_a.py"]},
            action_run_id="action-test",
            stage_id="stage-bound",
        )
        await execute_default_task_tool(
            **common,
            step_id=1,
            tool="workspace_edit_stage_cancel",
            args={},
            action_run_id="action-edit",
            stage_id="stage-bound",
        )

        self.assertIs(calls[0][4]["requires_sandbox_test"], True)
        self.assertEqual(calls[1][4]["stage_id"], "stage-bound")
        self.assertEqual(calls[2][4]["stage_id"], "stage-bound")
        self.assertEqual(
            [call[4]["action_run_id"] for call in calls],
            ["action-edit", "action-test", "action-edit"],
        )

    async def test_cleanup_rejects_swallowed_timeout_receipt(self) -> None:
        real_timeout = asyncio.timeout
        cancellation_seen = False

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            nonlocal cancellation_seen
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_seen = True
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    code="workspace_edit_stage_cancelled",
                )

        deps = TaskLoopDeps(
            decide_next=AsyncMock(),
            execute_tool=execute_tool,
            monotonic=lambda: 20.0,
            wall_time=lambda: 20.0,
        )
        pending = _PendingWorkspaceEdit(
            step_id=1,
            args={"mode": "create", "path": "late.txt", "newText": "late"},
            action_run_id="action-cleanup",
            criteria="candidate removed",
            preview={"stageId": "stage-cleanup"},
        )
        with patch(
            "evelyn_core.task_loop_runtime.asyncio.timeout",
            side_effect=lambda _seconds: real_timeout(0.01),
        ):
            cleaned = await _discard_pending_workspace_edit(
                deps=deps,
                grant=_approval_grant(),
                pending=pending,
            )

        self.assertTrue(cancellation_seen)
        self.assertFalse(cleaned)

    async def test_workspace_cancellation_returns_before_late_host_outcome(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        class Client:
            def execute(self, *_args, **_kwargs):
                entered.set()
                release.wait(timeout=2.0)
                finished.set()
                return {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_read_completed",
                    "summary": "read",
                    "evidence": _read_evidence(),
                }

        task = asyncio.create_task(
            execute_default_task_tool(
                task_id="task-test",
                step_id=1,
                tool="workspace_read",
                args={"path": "README.md"},
                action_run_id="action-test",
                grant_id="grant-test",
                surface="control_page",
                workspace_client=Client(),
            )
        )
        await asyncio.to_thread(entered.wait, 2.0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.2)
        self.assertFalse(finished.is_set())

        release.set()
        self.assertTrue(await asyncio.to_thread(finished.wait, 2.0))

    async def test_cancelled_behavioral_stage_discards_late_exact_candidate(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        discarded = threading.Event()
        bindings: list[dict] = []

        class Client:
            def stage_edit(self, *_args, **_binding):
                entered.set()
                release.wait(timeout=2.0)
                return {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_edit_staged",
                    "summary": "staged",
                    "evidence": {"stageId": "stage-late"},
                }

            def discard_staged_candidate(self, *_args, **binding):
                bindings.append(binding)
                discarded.set()
                return {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_edit_stage_cancelled",
                    "summary": "discarded",
                    "evidence": {},
                }

        task = asyncio.create_task(
            execute_default_task_tool(
                task_id="task-test",
                step_id=1,
                tool="workspace_edit",
                args={"mode": "create", "path": "new.txt", "newText": "x"},
                action_run_id="action-edit",
                grant_id="grant-test",
                surface="control_page",
                workspace_client=Client(),
                requires_sandbox_test=True,
            )
        )
        await asyncio.to_thread(entered.wait, 2.0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.2)

        release.set()
        self.assertTrue(await asyncio.to_thread(discarded.wait, 2.0))
        self.assertEqual(bindings[0]["stage_id"], "stage-late")
        self.assertEqual(bindings[0]["action_run_id"], "action-edit")

    async def test_cancelled_literal_stage_discards_late_exact_candidate(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        discarded = threading.Event()

        class Client:
            def stage_edit(self, *_args, **_binding):
                entered.set()
                release.wait(timeout=2.0)
                return {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_edit_staged",
                    "summary": "staged",
                    "evidence": {"stageId": "stage-literal-late"},
                }

            def discard_staged_candidate(self, *_args, **binding):
                self.binding = binding
                discarded.set()
                return {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_edit_stage_cancelled",
                    "summary": "discarded",
                    "evidence": {},
                }

        client = Client()
        task = asyncio.create_task(
            execute_default_task_tool(
                task_id="task-test",
                step_id=1,
                tool="workspace_edit",
                args={"mode": "create", "path": "new.txt", "newText": "x"},
                action_run_id="action-edit",
                grant_id="grant-test",
                surface="control_page",
                workspace_client=client,
                requires_sandbox_test=False,
            )
        )
        await asyncio.to_thread(entered.wait, 2.0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.2)

        release.set()
        self.assertTrue(await asyncio.to_thread(discarded.wait, 2.0))
        self.assertEqual(client.binding["stage_id"], "stage-literal-late")
        self.assertEqual(client.binding["action_run_id"], "action-edit")

    async def test_workspace_host_wait_obeys_remaining_task_deadline(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        class Client:
            def execute(self, *_args, **_kwargs):
                entered.set()
                release.wait(timeout=2.0)
                finished.set()
                return {
                    "attempted": True,
                    "executed": True,
                    "observed": True,
                    "verified": True,
                    "outcome": "succeeded",
                    "code": "workspace_read_completed",
                    "summary": "read",
                    "evidence": _read_evidence(),
                }

        decisions = iter(
            [
                {
                    "type": "tool",
                    "tool": "workspace_read",
                    "args": {"path": "README.md"},
                }
            ]
        )

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return await execute_default_task_tool(
                **kwargs,
                workspace_client=Client(),
            )

        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await run_task_loop_from_runtime(
            "README.md를 읽어줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=loop.time,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(deadline_sec=0.05),
        )
        elapsed = loop.time() - started

        self.assertTrue(entered.is_set())
        self.assertEqual((result.status, result.code), ("uncertain", "task_tool_timeout"))
        self.assertLess(elapsed, 0.2)
        self.assertFalse(finished.is_set())

        release.set()
        self.assertTrue(await asyncio.to_thread(finished.wait, 2.0))

    async def test_workspace_client_exception_is_uncertain_not_safe_failure(self) -> None:
        class Client:
            def execute(self, *_args, **_kwargs):
                raise OSError("private detail")

        receipt = await execute_default_task_tool(
            task_id="task-test",
            step_id=1,
            tool="workspace_edit",
            args={"mode": "create", "path": "new.txt", "newText": "x"},
            action_run_id="action-test",
            grant_id="grant-test",
            surface="control_page",
            workspace_client=Client(),
        )

        self.assertEqual(receipt.outcome, "uncertain")
        self.assertFalse(receipt.verified)
        self.assertNotIn("private detail", receipt.summary)

    async def test_approved_edit_continues_same_frozen_action_once_then_replans(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {"type": "final", "summary": "done", "verified_step": 2},
            ]
        )
        model_states: list[dict] = []
        tool_calls: list[dict] = []
        approval_requests = []
        approval_entered = asyncio.Event()
        release_approval = asyncio.Event()
        host_effect_calls = 0

        async def decide_next(state: dict) -> dict:
            model_states.append(state)
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append(kwargs)
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                evidence=_read_evidence(kwargs["args"]["path"]),
            )

        async def request_approval(request, safe_preview) -> TaskApprovalResolution:
            nonlocal host_effect_calls
            approval_requests.append((request, safe_preview))
            approval_entered.set()
            await release_approval.wait()
            host_effect_calls += 1
            return TaskApprovalResolution("approved", receipt=_approved_edit_result())

        task = asyncio.create_task(
            run_task_loop_from_runtime(
                "README.md에서 `old`를 `new`로 바꿔줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    request_approval=request_approval,
                    monotonic=asyncio.get_running_loop().time,
                    wall_time=lambda: 20.0,
                ),
                grant=_approval_grant(max_steps=3),
            )
        )
        await asyncio.wait_for(approval_entered.wait(), 0.2)

        self.assertEqual((len(model_states), len(tool_calls), host_effect_calls), (1, 1, 0))
        request, safe_preview = approval_requests[0]
        self.assertEqual(dict(request.args), edit_args)
        self.assertEqual(request.action_run_id, tool_calls[0]["action_run_id"])
        self.assertEqual(request.args_hash, safe_preview["argsHash"])
        release_approval.set()
        result = await task

        self.assertEqual(result.status, "completed")
        self.assertEqual((result.model_call_count, host_effect_calls), (2, 1))
        self.assertEqual(len(model_states), 2)
        self.assertEqual((model_states[-1]["step"], len(model_states[-1]["observations"])), (3, 2))
        self.assertEqual(
            [call["tool"] for call in tool_calls],
            ["workspace_edit", "workspace_read"],
        )
        self.assertIs(tool_calls[0]["requires_sandbox_test"], False)
        self.assertEqual(result.observations[0]["code"], "workspace_edit_completed")

    async def test_human_wait_is_excluded_from_compute_deadline(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {"type": "final", "summary": "done", "verified_step": 2},
            ]
        )
        monotonic = [0.0]

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                evidence=_read_evidence(),
            )

        async def request_approval(_request, _preview) -> TaskApprovalResolution:
            monotonic[0] += 1_000.0
            return TaskApprovalResolution("approved", receipt=_approved_edit_result())

        result = await run_task_loop_from_runtime(
            "README.md에서 `old`를 `new`로 바꿔줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: monotonic[0],
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=3, deadline_sec=0.1),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.model_call_count, 2)

    async def test_workspace_mutations_ignore_forged_auto_tool_grants(self) -> None:
        for tool in ("workspace_edit", "workspace_test"):
            with self.subTest(tool=tool):
                calls = 0

                async def decide_next(_state: dict, selected: str = tool) -> dict:
                    return {"type": "tool", "tool": selected, "args": {}}

                async def execute_tool(**_kwargs) -> TaskStepReceipt:
                    nonlocal calls
                    calls += 1
                    raise AssertionError("coarse grant must not authorize host mutation")

                grant = TaskGrant(
                    task_id="task-forged",
                    grant_id="grant-forged",
                    source="control_page",
                    auto_tools=frozenset({tool}),
                    approval_tools=frozenset(),
                    forbidden_tools=frozenset(),
                    issued_at=10.0,
                    expires_at=1_000.0,
                )
                result = await run_task_loop_from_runtime(
                    "위험 작업",
                    deps=TaskLoopDeps(
                        decide_next=decide_next,
                        execute_tool=execute_tool,
                        monotonic=lambda: 20.0,
                        wall_time=lambda: 20.0,
                    ),
                    grant=grant,
                )
                self.assertIn(result.status, {"blocked", "awaiting_approval"})
                self.assertEqual(calls, 0)

    async def test_one_mutation_attempt_per_task_even_after_verified_failure(self) -> None:
        first_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "first",
            "expectedSha256": "a" * 64,
        }
        second_args = {**first_args, "newText": "second"}
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": first_args},
                {"type": "tool", "tool": "workspace_edit", "args": second_args},
            ]
        )
        stage_calls = 0
        approval_calls = 0
        discard_calls = 0

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            nonlocal discard_calls, stage_calls
            if kwargs["tool"] == "workspace_edit_stage_cancel":
                discard_calls += 1
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    code="workspace_edit_stage_cancelled",
                )
            stage_calls += 1
            evidence = _stage_evidence(kwargs["args"])
            evidence["stageId"] = f"stage-{stage_calls}"
            unsigned = {key: value for key, value in evidence.items() if key != "previewDigest"}
            evidence["previewDigest"] = hashlib.sha256(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            return _stage_receipt(kwargs, evidence)

        async def request_approval(_request, _preview) -> TaskApprovalResolution:
            nonlocal approval_calls
            approval_calls += 1
            return TaskApprovalResolution(
                "approved",
                receipt=_approved_edit_result(outcome="failed"),
            )

        result = await run_task_loop_from_runtime(
            "README.md에서 `old`를 `first`로 바꿔줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=2),
        )

        self.assertEqual((result.status, result.code), ("blocked", "task_workspace_mutation_limit"))
        self.assertEqual((stage_calls, approval_calls), (1, 1))
        self.assertEqual(discard_calls, 1)

    async def test_preconsume_failed_literal_apply_discards_exact_stage(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        tool_calls: list[str] = []

        async def decide_next(_state: dict) -> dict:
            return {"type": "tool", "tool": "workspace_edit", "args": edit_args}

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_calls.append(kwargs["tool"])
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            self.assertEqual(kwargs["tool"], "workspace_edit_stage_cancel")
            self.assertEqual(kwargs["stage_id"], "stage-approval")
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        result = await run_task_loop_from_runtime(
            "README.md에서 `old`를 `new`로 바꿔줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=lambda _request, _preview: asyncio.sleep(
                    0,
                    result=TaskApprovalResolution(
                        "approved",
                        receipt=_approved_edit_result(outcome="failed"),
                    ),
                ),
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=1),
        )

        self.assertEqual(
            (result.status, result.code),
            ("budget_exhausted", "task_max_steps_exhausted"),
        )
        self.assertEqual(
            tool_calls,
            ["workspace_edit", "workspace_edit_stage_cancel"],
        )

    async def test_preconsume_failed_apply_stops_if_stage_cleanup_is_unverified(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        cleanup_calls = 0

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            nonlocal cleanup_calls
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            cleanup_calls += 1
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="uncertain" if cleanup_calls == 1 else "success",
                executed=False if cleanup_calls == 1 else True,
                verified=False if cleanup_calls == 1 else True,
                code=(
                    "workspace_edit_stage_cleanup_unverified"
                    if cleanup_calls == 1
                    else "workspace_edit_stage_cancelled"
                ),
            )

        result = await run_task_loop_from_runtime(
            "README.md에서 `old`를 `new`로 바꿔줘",
            deps=TaskLoopDeps(
                decide_next=lambda _state: asyncio.sleep(
                    0,
                    result={"type": "tool", "tool": "workspace_edit", "args": edit_args},
                ),
                execute_tool=execute_tool,
                request_approval=lambda _request, _preview: asyncio.sleep(
                    0,
                    result=TaskApprovalResolution(
                        "approved",
                        receipt=_approved_edit_result(outcome="failed"),
                    ),
                ),
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=1),
        )

        self.assertEqual(
            (result.status, result.code),
            ("uncertain", "workspace_edit_stage_cleanup_unverified"),
        )
        self.assertEqual(cleanup_calls, 2)

    async def test_cancel_during_failed_apply_cleanup_propagates_after_final_retry(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        cleanup_started = asyncio.Event()
        cleanup_calls = 0

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            nonlocal cleanup_calls
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            cleanup_calls += 1
            if cleanup_calls == 1:
                cleanup_started.set()
                await asyncio.Event().wait()
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        running = asyncio.create_task(
            run_task_loop_from_runtime(
                "README.md에서 `old`를 `new`로 바꿔줘",
                deps=TaskLoopDeps(
                    decide_next=lambda _state: asyncio.sleep(
                        0,
                        result={"type": "tool", "tool": "workspace_edit", "args": edit_args},
                    ),
                    execute_tool=execute_tool,
                    request_approval=lambda _request, _preview: asyncio.sleep(
                        0,
                        result=TaskApprovalResolution(
                            "approved",
                            receipt=_approved_edit_result(outcome="failed"),
                        ),
                    ),
                    monotonic=lambda: 20.0,
                    wall_time=lambda: 20.0,
                ),
                grant=_approval_grant(max_steps=1),
            )
        )
        await asyncio.wait_for(cleanup_started.wait(), timeout=1.0)
        running.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await running
        self.assertEqual(cleanup_calls, 2)

    async def test_behavioral_success_alias_cannot_bypass_semantic_false_fence(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        test_args = {
            "runner": "python_unittest",
            "targets": ["tests/core/test_example.py"],
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {"type": "tool", "tool": "workspace_test", "args": test_args},
            ]
        )

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))
            if kwargs["tool"] == "workspace_test":
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    code="workspace_test_passed",
                    evidence=_sandbox_test_evidence(
                        kwargs["args"],
                        stage_id="stage-approval",
                        candidate_sha=_READ_SHA256,
                    ),
                )
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_edit_stage_cancelled",
            )

        forged = _approved_edit_result()
        forged["outcome"] = "success"
        forged["evidence"]["semanticVerified"] = True
        result = await run_task_loop_from_runtime(
            "README.md의 버그를 고쳐줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=lambda _request, _preview: asyncio.sleep(
                    0,
                    result=TaskApprovalResolution("approved", receipt=forged),
                ),
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=2),
        )

        self.assertEqual(
            (result.status, result.code),
            ("uncertain", "task_approval_response_invalid"),
        )
        self.assertNotIn(
            "workspace_edit_completed",
            [observation["code"] for observation in result.observations],
        )

    async def test_post_edit_read_must_match_staged_candidate_sha(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {"type": "tool", "tool": "workspace_read", "args": {"path": "README.md"}},
                {"type": "final", "summary": "done", "verified_step": 2},
            ]
        )

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(
                    kwargs,
                    _stage_evidence(kwargs["args"], candidate_sha="c" * 64),
                )
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                evidence=_read_evidence(),
            )

        async def request_approval(_request, _preview) -> TaskApprovalResolution:
            forged_result = _approved_edit_result(sha256="b" * 64)
            forged_result["evidence"]["candidateSha256"] = "c" * 64
            return TaskApprovalResolution("approved", receipt=forged_result)

        result = await run_task_loop_from_runtime(
            "README.md에서 `old`를 `new`로 바꿔줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=3),
        )

        self.assertEqual(
            (result.status, result.code),
            ("uncertain", "task_approval_response_invalid"),
        )
        self.assertEqual(result.observations, ())

    async def test_malformed_approval_preview_and_result_fail_closed(self) -> None:
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }

        async def run(*, malformed_preview: bool, malformed_result: bool) -> TaskLoopResult:
            manager = TaskApprovalManager(now=lambda: 20.0)

            async def decide_next(_state: dict) -> dict:
                return {"type": "tool", "tool": "workspace_edit", "args": edit_args}

            async def execute_tool(**kwargs) -> TaskStepReceipt:
                evidence = _stage_evidence(kwargs["args"])
                if malformed_preview:
                    evidence["diffTruncated"] = True
                return _stage_receipt(kwargs, evidence)

            async def request_approval(request, preview) -> TaskApprovalResolution:
                if malformed_preview:
                    return await manager.wait(request, preview)
                return TaskApprovalResolution(
                    "approved",
                    receipt=(
                        {"outcome": "succeeded"}
                        if malformed_result
                        else _approved_edit_result()
                    ),
                )

            return await run_task_loop_from_runtime(
                "README.md에서 `old`를 `new`로 바꿔줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    request_approval=request_approval,
                    monotonic=lambda: 20.0,
                    wall_time=lambda: 20.0,
                ),
                grant=_approval_grant(max_steps=1),
            )

        preview_result = await run(
            malformed_preview=True,
            malformed_result=False,
        )
        result_result = await run(malformed_preview=False, malformed_result=True)
        self.assertEqual(
            (preview_result.status, preview_result.code),
            ("blocked", "task_approval_unavailable"),
        )
        self.assertEqual(result_result.status, "uncertain")

    async def test_grant_expiry_while_waiting_returns_without_effect_receipt(self) -> None:
        now = [20.0]
        manager = TaskApprovalManager(now=lambda: now[0])
        discarded: list[str] = []
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }

        async def decide_next(_state: dict) -> dict:
            return {"type": "tool", "tool": "workspace_edit", "args": edit_args}

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            if kwargs["tool"] == "workspace_edit_stage_cancel":
                discarded.append(kwargs["stage_id"])
                return _receipt(
                    step_id=kwargs["step_id"],
                    tool=kwargs["tool"],
                    action_run_id=kwargs["action_run_id"],
                    grant_id=kwargs["grant_id"],
                    outcome="success",
                    verified=True,
                    code="workspace_edit_stage_cancelled",
                )
            return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))

        task = asyncio.create_task(
            run_task_loop_from_runtime(
                "README.md에서 `old`를 `new`로 바꿔줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    request_approval=manager.wait,
                    monotonic=asyncio.get_running_loop().time,
                    wall_time=lambda: now[0],
                ),
                grant=_approval_grant(max_steps=1, expires_at=25.0),
            )
        )
        for _ in range(20):
            if manager.public_snapshot():
                break
            await asyncio.sleep(0)
        public = manager.public_snapshot()
        self.assertTrue(public)
        now[0] = 30.0
        manager.issue_preview(public["taskId"], public["approvalId"])
        result = await task

        self.assertEqual((result.status, result.code), ("failed", "task_approval_expired"))
        self.assertEqual(result.observations, ())
        self.assertEqual(discarded, ["stage-approval"])

    async def test_turn_scope_cancellation_cleans_pending_approval(self) -> None:
        manager = TaskApprovalManager(now=lambda: 20.0)
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }

        async def decide_next(_state: dict) -> dict:
            return {"type": "tool", "tool": "workspace_edit", "args": edit_args}

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            return _stage_receipt(kwargs, _stage_evidence(kwargs["args"]))

        scope = TurnScope(turn_id="turn-approval")
        task = asyncio.create_task(
            run_task_loop_from_runtime(
                "README.md에서 `old`를 `new`로 바꿔줘",
                deps=TaskLoopDeps(
                    decide_next=decide_next,
                    execute_tool=execute_tool,
                    request_approval=manager.wait,
                    monotonic=asyncio.get_running_loop().time,
                    wall_time=lambda: 20.0,
                ),
                grant=_approval_grant(max_steps=1),
                turn_scope=scope,
            )
        )
        scope.register_task(task)
        for _ in range(20):
            if manager.public_snapshot():
                break
            await asyncio.sleep(0)
        self.assertTrue(manager.public_snapshot())
        scope.cancel("user_cancelled")
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(manager.public_snapshot(), {})

    async def test_chunked_workspace_read_requires_contiguous_same_sha_until_eof(self) -> None:
        raw = "AAAABBBBCCCC"
        raw_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        states: list[dict] = []
        tool_args: list[dict] = []

        async def decide_next(state: dict) -> dict:
            states.append(state)
            if not state["observations"]:
                return {
                    "type": "tool",
                    "tool": "workspace_read",
                    "args": {"path": "docs/long.md"},
                }
            if state.get("requiredNextOffset") is not None:
                return {
                    "type": "tool",
                    "tool": "workspace_read",
                    "args": {
                        "path": state["requiredReadPath"],
                        "offset": state["requiredNextOffset"],
                        "length": state["requiredReadLength"],
                        "expectedSha256": state["expectedSha256"],
                    },
                }
            return {
                "type": "final",
                "summary": "전체 파일을 읽었어.",
                "verified_step": state["observations"][-1]["step"],
            }

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_args.append(dict(kwargs["args"]))
            offset = int(kwargs["args"].get("offset", 0))
            content = raw[offset : offset + 4]
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_read_completed",
                evidence=_chunk_read_evidence(
                    path="docs/long.md",
                    offset=offset,
                    content=content,
                    total_bytes=len(raw),
                    sha256=raw_sha256,
                ),
            )

        result = await run_task_loop_from_runtime(
            "docs/long.md를 읽어줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=asyncio.get_running_loop().time,
                wall_time=lambda: 20.0,
                bind_exact_initial_read=True,
            ),
            grant=_grant(max_steps=4, auto_tools=frozenset({"workspace_read"})),
        )

        self.assertEqual((result.status, result.code), ("completed", "task_completed"))
        self.assertEqual((result.step_count, result.model_call_count), (3, 0))
        self.assertEqual(len(result.observations), 3)
        self.assertEqual(states, [])
        self.assertEqual(
            tool_args,
            [
                {"path": "docs/long.md"},
                {
                    "path": "docs/long.md",
                    "offset": 4,
                    "length": TASK_WORKSPACE_READ_CHUNK_BYTES,
                    "expectedSha256": raw_sha256,
                },
                {
                    "path": "docs/long.md",
                    "offset": 8,
                    "length": TASK_WORKSPACE_READ_CHUNK_BYTES,
                    "expectedSha256": raw_sha256,
                },
            ],
        )

    async def test_chunked_workspace_read_continuation_is_runtime_bound(self) -> None:
        raw = "AAAABBBB"
        raw_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        tool_args: list[dict] = []
        model_calls = 0

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            tool_args.append(dict(kwargs["args"]))
            offset = int(kwargs["args"].get("offset", 0))
            content = raw[offset : offset + 4]
            return _receipt(
                step_id=kwargs["step_id"],
                tool=kwargs["tool"],
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_read_completed",
                evidence=_chunk_read_evidence(
                    path="docs/long.md",
                    offset=offset,
                    content=content,
                    total_bytes=len(raw),
                    sha256=raw_sha256,
                ),
            )

        async def decide_next(_state: dict) -> dict:
            nonlocal model_calls
            model_calls += 1
            return {
                "type": "tool",
                "tool": "workspace_read",
                "args": {"path": "docs/long.md"},
            }

        result = await run_task_loop_from_runtime(
            "docs/long.md를 읽어줘",
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                monotonic=asyncio.get_running_loop().time,
                wall_time=lambda: 20.0,
            ),
            grant=_grant(max_steps=2, auto_tools=frozenset({"workspace_read"})),
        )

        self.assertEqual(
            (result.status, result.code, result.model_call_count, model_calls),
            ("budget_exhausted", "task_max_steps_exhausted", 1, 1),
        )
        self.assertEqual(len(result.observations), 2)
        self.assertEqual(
            tool_args,
            [
                {"path": "docs/long.md"},
                {
                    "path": "docs/long.md",
                    "offset": 4,
                    "length": TASK_WORKSPACE_READ_CHUNK_BYTES,
                    "expectedSha256": raw_sha256,
                },
            ],
        )

    def test_chunked_workspace_read_hashes_concatenated_content_before_completion(self) -> None:
        path = "docs/long.md"
        raw = "AAAABBBB"
        sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        args_one = {"path": path}
        args_two = {
            "path": path,
            "offset": 4,
            "length": TASK_WORKSPACE_READ_CHUNK_BYTES,
            "expectedSha256": sha256,
        }

        def completion(first_content: str, second_content: str, claimed_sha: str) -> bool:
            first = _receipt(
                step_id=1,
                tool="workspace_read",
                action_run_id="read-1",
                grant_id="grant-test",
                outcome="success",
                verified=True,
                code="workspace_read_completed",
                evidence=_chunk_read_evidence(
                    path=path,
                    offset=0,
                    content=first_content,
                    total_bytes=8,
                    sha256=claimed_sha,
                ),
            )
            second = _receipt(
                step_id=2,
                tool="workspace_read",
                action_run_id="read-2",
                grant_id="grant-test",
                outcome="success",
                verified=True,
                code="workspace_read_completed",
                evidence=_chunk_read_evidence(
                    path=path,
                    offset=4,
                    content=second_content,
                    total_bytes=8,
                    sha256=claimed_sha,
                ),
            )
            return _completion_evidence_matches(
                goal="docs/long.md를 읽어줘",
                verified_step=2,
                latest_observation_step=2,
                successful_actions={
                    1: ("workspace_read", args_one, first),
                    2: (
                        "workspace_read",
                        {**args_two, "expectedSha256": claimed_sha},
                        second,
                    ),
                },
            )

        self.assertTrue(completion("AAAA", "BBBB", sha256))
        self.assertFalse(completion("BBBB", "AAAA", sha256))
        self.assertFalse(completion("AAAA", "BBBB", "f" * 64))
        self.assertFalse(completion("AAA\x00", "BBBB", sha256))

    def test_chunked_read_chain_survives_task_and_main_projection(self) -> None:
        observations = []
        for index in range(5):
            evidence = json.dumps(
                _chunk_read_evidence(
                    path="docs/long.md",
                    offset=index * 120,
                    content="\x01" * 120,
                    total_bytes=600,
                    sha256="a" * 64,
                ),
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
                    "successCriteria": "worker-only" * 40,
                }
            )
        result = TaskLoopResult(
            task_id="task-chunk-projection",
            status="completed",
            code="task_completed",
            summary="bounded full read",
            step_count=5,
            model_call_count=6,
            observations=tuple(observations),
        )

        task_payload = json.loads(result.evidence_text())
        self.assertEqual(len(task_payload["observations"]), 5)
        self.assertNotIn("successCriteria", task_payload["observations"][0])
        messages = append_registered_route_evidence(
            [],
            route="task_executor",
            evidence=result.evidence_text(),
        )
        envelope = json.loads(messages[-1]["content"])
        main_payload = json.loads(envelope["evidence"])
        self.assertEqual(len(main_payload["observations"]), 5)
        self.assertTrue(all(item["evidence"] for item in main_payload["observations"]))

    async def test_approved_mutation_requires_full_chunked_sha_readback(self) -> None:
        raw = "AAAABBBBCCCC"
        sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        edit_args = {
            "mode": "replace",
            "path": "README.md",
            "oldText": "old",
            "newText": "new",
            "expectedSha256": "a" * 64,
        }
        decisions = iter(
            [
                {"type": "tool", "tool": "workspace_edit", "args": edit_args},
                {"type": "final", "summary": "done", "verified_step": 4},
            ]
        )
        read_args: list[dict] = []

        async def decide_next(_state: dict) -> dict:
            return next(decisions)

        async def execute_tool(**kwargs) -> TaskStepReceipt:
            if kwargs["tool"] == "workspace_edit":
                return _stage_receipt(
                    kwargs,
                    _stage_evidence(kwargs["args"], candidate_sha=sha256),
                )
            self.assertEqual(kwargs["tool"], "workspace_read")
            read_args.append(dict(kwargs["args"]))
            offset = int(kwargs["args"].get("offset", 0))
            return _receipt(
                step_id=kwargs["step_id"],
                tool="workspace_read",
                action_run_id=kwargs["action_run_id"],
                grant_id=kwargs["grant_id"],
                outcome="success",
                verified=True,
                code="workspace_read_completed",
                evidence=_chunk_read_evidence(
                    path="README.md",
                    offset=offset,
                    content=raw[offset : offset + 4],
                    total_bytes=len(raw),
                    sha256=sha256,
                ),
            )

        async def request_approval(_request, _preview) -> TaskApprovalResolution:
            return TaskApprovalResolution(
                "approved",
                receipt=_approved_edit_result(sha256=sha256),
            )

        goal = "README.md에서 `old`를 `new`로 바꿔줘"
        result = await run_task_loop_from_runtime(
            goal,
            deps=TaskLoopDeps(
                decide_next=decide_next,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=5),
        )

        self.assertEqual((result.status, result.code), ("completed", "task_completed"))
        self.assertEqual(
            read_args,
            [
                {"path": "README.md"},
                {
                    "path": "README.md",
                    "offset": 4,
                    "length": TASK_WORKSPACE_READ_CHUNK_BYTES,
                    "expectedSha256": sha256,
                },
                {
                    "path": "README.md",
                    "offset": 8,
                    "length": TASK_WORKSPACE_READ_CHUNK_BYTES,
                    "expectedSha256": sha256,
                },
            ],
        )
        self.assertIsNotNone(
            task_loop_terminal_outcome(result.evidence_text(), goal=goal)
        )

        async def decide_edit(_state: dict) -> dict:
            return {"type": "tool", "tool": "workspace_edit", "args": edit_args}

        exhausted = await run_task_loop_from_runtime(
            goal,
            deps=TaskLoopDeps(
                decide_next=decide_edit,
                execute_tool=execute_tool,
                request_approval=request_approval,
                monotonic=lambda: 20.0,
                wall_time=lambda: 20.0,
            ),
            grant=_approval_grant(max_steps=2),
        )
        self.assertFalse(exhausted.completed)
        exhausted_outcome = task_loop_terminal_outcome(
            exhausted.evidence_text(),
            goal=goal,
        )
        self.assertIsNotNone(exhausted_outcome)
        self.assertNotEqual(exhausted_outcome, TASK_LOOP_VERIFIED_MUTATION_OUTCOME)

    async def test_registered_task_skill_returns_evidence_without_main_followup(self) -> None:
        from evelyn_core.skills import task_loop as task_skill

        scope = TurnScope(turn_id="turn-skill")
        principal_token = object()
        loop_result = TaskLoopResult(
            task_id="task-skill",
            status="completed",
            code="task_completed",
            summary="done",
            step_count=2,
            model_call_count=3,
        )
        runner = AsyncMock(return_value=loop_result)
        with patch.object(task_skill, "run_default_task_loop", runner):
            result = await skill_registry.execute(
                "task_loop",
                SkillContext(
                    source="text",
                    extras={
                        "user_text": "/작업 테스트를 고쳐줘",
                        "turn_scope": scope,
                        "principal_token": principal_token,
                        "skill_origin_class": "bundled",
                    },
                ),
            )

        runner.assert_awaited_once_with(
            "테스트를 고쳐줘",
            source="text",
            turn_scope=scope,
            principal_token=principal_token,
            skill_origin_class="bundled",
        )
        self.assertIn('"status":"completed"', result.display_text)
        self.assertIsNone(result.followup_route)
        self.assertEqual(
            result.metadata["taskRecord"],
            loop_result.public_task_record(),
        )


if __name__ == "__main__":
    unittest.main()
