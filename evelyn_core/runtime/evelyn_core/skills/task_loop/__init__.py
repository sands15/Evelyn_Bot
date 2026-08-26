from __future__ import annotations

import sys

from ...task_loop_runtime import parse_task_request, run_default_task_loop
from ..base import SkillContext, SkillResult
from ..registry import skill_registry


name = "task_loop"
routes = ("task_executor",)
sources = ("text", "voice", "control_page")
description = "Run a bounded verify-and-replan task loop and return evidence for Main."


async def execute(context: SkillContext) -> SkillResult:
    extras = context.extras or {}
    user_text = str(extras.get("user_text") or "")
    goal = parse_task_request(user_text)
    if not goal:
        return SkillResult(
            skill=name,
            route="task_executor",
            handled=True,
            status="failed",
            display_text='{"status":"failed","code":"task_goal_empty"}',
            answer_text='{"status":"failed","code":"task_goal_empty"}',
            metadata={"status": "failed", "code": "task_goal_empty"},
        )
    result = await run_default_task_loop(
        goal,
        source=context.source,
        turn_scope=extras.get("turn_scope"),
    )
    evidence = result.evidence_text()
    return SkillResult(
        skill=name,
        route="task_executor",
        handled=True,
        status=result.status,
        display_text=evidence,
        answer_text=evidence,
        dedupe_key=f"task-loop|{result.task_id}",
        executor_used="bounded_task_loop",
        metadata={
            "status": result.status,
            "code": result.code,
            "steps": result.step_count,
            "worker_calls": result.model_call_count,
        },
    )


TASK_LOOP_SKILL = skill_registry.register_module(sys.modules[__name__])

__all__ = ["TASK_LOOP_SKILL", "description", "execute", "name", "routes", "sources"]
