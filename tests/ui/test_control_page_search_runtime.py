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


def _deps(**overrides) -> tuple[ControlPageSearchRuntimeDeps, dict[str, object]]:
    state: dict[str, object] = {
        "history": [],
        "active": [],
        "tts": [],
        "route": [],
        "search": [],
        "synthesis": [],
        "events": [],
        "locks": {},
    }

    async def execute_search_then_answer_action(**kwargs):
        state["search"].append(kwargs)
        return SimpleNamespace(answer_text="search answer")

    async def synthesize_tool_result_with_main_llm(**kwargs):
        state["synthesis"].append(kwargs)
        return "final answer"

    def get_session_lock(session_key: str) -> asyncio.Lock:
        locks = state["locks"]
        if session_key not in locks:
            locks[session_key] = asyncio.Lock()
        return locks[session_key]

    async def commit_session_continuity():
        state["events"].append("commit")
        return {"generation": 4}

    deps = ControlPageSearchRuntimeDeps(
        control_page_effective_guild_id=lambda guild: int(getattr(guild, "id", 999) or 999),
        control_page_session_key=lambda guild_id: f"control:{guild_id}",
        get_conversation_history=lambda **kwargs: [{"role": "user", "content": "recent"}],
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
            state["synthesis"][0]["metrics"]["meta"]["continuity_generation"],
            4,
        )

    def test_search_answer_falls_back_to_action_result_when_synthesis_is_empty(self) -> None:
        async def synthesize_tool_result_with_main_llm(**_kwargs):
            return "   "

        deps, _state = _deps(synthesize_tool_result_with_main_llm=synthesize_tool_result_with_main_llm)

        reply = asyncio.run(self._run(deps))

        self.assertEqual(reply, "display:search answer")


if __name__ == "__main__":
    unittest.main()
