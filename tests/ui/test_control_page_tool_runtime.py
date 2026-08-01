from __future__ import annotations

import sys
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_tool_runtime import (  # noqa: E402
    ControlPageInputRuntimeDeps,
    ControlPageToolRuntimeDeps,
    decide_control_page_tool_call_from_runtime,
    execute_control_page_tool_from_runtime,
    execute_control_page_memory_panel_action_from_runtime,
    handle_control_page_input_from_runtime,
    recent_control_page_history_for_router_from_runtime,
    remember_control_page_tool_turn_from_runtime,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    current_conversation_memory_receipt_ref,
    not_used_memory_receipt_ref,
)


def _deps(**overrides) -> tuple[ControlPageToolRuntimeDeps, dict[str, object]]:
    state: dict[str, object] = {"ui": [], "records": [], "history": []}
    deps = ControlPageToolRuntimeDeps(
        memory_index_dir=REPO_ROOT / "unused-memory-index",
        clean_text=lambda text: text.strip(),
        enqueue_control_page_ui_command=lambda action, *, panel_id=None: state["ui"].append((action, panel_id)) or {"action": action},
        memory_panel_reply=lambda action: f"panel:{action}",
        create_task=lambda coro: coro.close(),
        restart_bot_process=lambda: None,
        get_conversation_history=lambda **kwargs: state["history"].append(kwargs) or [
            {"role": "user", "content": "recent"},
            {
                "role": "assistant",
                "content": "safe reply",
                "memoryReceiptRef": not_used_memory_receipt_ref(),
            },
        ],
        record_tool_assistant_turn=lambda *args, **kwargs: state["records"].append((args, kwargs)),
        control_page_effective_guild_id=lambda guild: int(getattr(guild, "id", 999) or 999),
        control_page_session_key=lambda guild_id: f"control:{guild_id}",
        system_prompt="system",
        max_history_items=12,
        active_conversation_text_sec=30.0,
        router_llm_enabled=True,
        route_timeout_sec=1.5,
        control_page_tool_registry_prompt=lambda: "control_page.memory_panel",
        ask_router_llm=None,
        current_turn_id=lambda session_key: f"turn:{session_key}",
        log=lambda *_args, **_kwargs: None,
    )
    if overrides:
        deps = ControlPageToolRuntimeDeps(**{**deps.__dict__, **overrides})
    return deps, state


def _input_deps(**overrides) -> tuple[ControlPageInputRuntimeDeps, dict[str, object]]:
    state: dict[str, object] = {"remembered": [], "executed": [], "decided": []}

    async def execute_tool(guild, decision):
        state["executed"].append((guild, decision))
        return f"executed:{decision['tool']}"

    async def decide_tool_call(text, **kwargs):
        state["decided"].append((text, kwargs))
        return None

    async def answer_search(guild, text):
        return f"search:{text}"

    async def answer_text(guild, text):
        return f"text:{text}"

    deps = ControlPageInputRuntimeDeps(
        clean_text=lambda text: text.strip(),
        control_page_effective_guild_id=lambda guild: int(getattr(guild, "id", 999) or 999),
        control_page_session_key=lambda guild_id: f"control:{guild_id}",
        cheap_control_page_tool_decision=lambda _text: None,
        execute_control_page_tool=execute_tool,
        remember_control_page_tool_turn=lambda *args, **kwargs: state["remembered"].append((args, kwargs)),
        should_route_control_page_tool_candidate=lambda _text: False,
        decide_control_page_tool_call=decide_tool_call,
        control_page_tool_decision_from_llm=lambda raw: raw if isinstance(raw, dict) and raw.get("tool") else None,
        control_page_tool_policy_error=lambda *_args, **_kwargs: None,
        control_page_tool_reply_from_execution=lambda decision, reply: f"final:{decision['tool']}:{reply}",
        should_force_search_query=lambda _text: False,
        answer_control_page_search_text=answer_search,
        answer_control_page_text=answer_text,
    )
    if overrides:
        deps = ControlPageInputRuntimeDeps(**{**deps.__dict__, **overrides})
    return deps, state


class ControlPageToolRuntimeTests(unittest.TestCase):
    def test_memory_panel_action_normalizes_invalid_action_to_toggle(self) -> None:
        deps, state = _deps()

        reply = execute_control_page_memory_panel_action_from_runtime("  weird  ", deps=deps)

        self.assertEqual(reply, "panel:toggle")
        self.assertEqual(state["ui"], [("toggle", "memory")])

    def test_recent_history_for_router_passes_runtime_context(self) -> None:
        deps, state = _deps()

        history = recent_control_page_history_for_router_from_runtime(
            session_key="session",
            guild_id=7,
            limit=3,
            deps=deps,
        )

        self.assertEqual(history, "user: recent\nassistant: safe reply")
        self.assertEqual(state["history"][0]["system_prompt"], "system")
        self.assertEqual(state["history"][0]["session_key"], "session")
        self.assertEqual(state["history"][0]["guild_id"], 7)

    def test_remember_tool_turn_records_session_metadata(self) -> None:
        deps, state = _deps()

        remember_control_page_tool_turn_from_runtime(
            SimpleNamespace(id=7),
            "user",
            "reply",
            {"tool": " runtime.status "},
            deps=deps,
        )

        args, kwargs = state["records"][0]
        self.assertEqual(args, ("control:7", "user", "reply"))
        self.assertEqual(kwargs["tool_name"], "runtime.status")
        self.assertEqual(kwargs["system_prompt"], "system")
        self.assertEqual(kwargs["max_history_items"], 12)
        self.assertEqual(kwargs["guild_id"], 7)
        self.assertEqual(kwargs["ttl_sec"], 30.0)

    async def _run_decision(self, deps: ControlPageToolRuntimeDeps):
        return await decide_control_page_tool_call_from_runtime(
            " 메모리 열어줘 ",
            guild_id=7,
            session_key="session",
            deps=deps,
        )

    def test_decide_control_page_tool_call_builds_router_request(self) -> None:
        calls: list[dict[str, object]] = []

        async def ask_router_llm(messages, **kwargs):
            calls.append({"messages": messages, **kwargs})
            return {"tool": "control_page.memory_panel"}

        deps, state = _deps(ask_router_llm=ask_router_llm)

        result = asyncio.run(self._run_decision(deps))

        self.assertEqual(result, {"tool": "control_page.memory_panel"})
        self.assertEqual(calls[0]["session_key"], "session")
        self.assertEqual(calls[0]["guild_id"], 7)
        self.assertEqual(calls[0]["turn_id"], "turn:session")
        self.assertIn("control_page.memory_panel", calls[0]["messages"][0]["content"])
        self.assertEqual(state["history"][0]["session_key"], "session")

    async def _run_execute(self, decision: dict[str, object], deps: ControlPageToolRuntimeDeps, guild=None) -> str:
        return await execute_control_page_tool_from_runtime(guild, decision, deps=deps)

    def test_execute_control_page_tool_returns_policy_error_before_dispatch(self) -> None:
        async def memory_tool(*_args, **_kwargs):
            raise AssertionError("memory tool should not be called")

        deps, _state = _deps(
            control_page_tool_policy_error=lambda *_args, **_kwargs: "blocked",
            execute_control_page_memory_tool=memory_tool,
        )

        reply = asyncio.run(self._run_execute({"tool": "control_page.memory_panel"}, deps))

        self.assertEqual(reply, "blocked")

    def test_execute_control_page_tool_returns_help_reply(self) -> None:
        deps, _state = _deps(
            control_page_tool_policy_error=lambda *_args, **_kwargs: None,
            build_control_page_help_reply=lambda: "help text",
        )

        reply = asyncio.run(self._run_execute({"tool": "control_page.help"}, deps, guild=SimpleNamespace(id=7)))

        self.assertEqual(reply, "help text")

    def test_execute_control_page_tool_dispatches_memory_tool_with_runtime_helpers(self) -> None:
        calls: list[dict[str, object]] = []

        async def memory_tool(tool_name, arguments, **kwargs):
            calls.append({"tool_name": tool_name, "arguments": arguments, **kwargs})
            return kwargs["execute_memory_panel_action"]("open")

        deps, state = _deps(
            control_page_tool_policy_error=lambda *_args, **_kwargs: None,
            execute_control_page_memory_tool=memory_tool,
        )

        reply = asyncio.run(
            self._run_execute(
                {"tool": " control_page.memory_panel ", "arguments": {"action": "open"}},
                deps,
                guild=SimpleNamespace(id=7),
            )
        )

        self.assertEqual(reply, "panel:open")
        self.assertEqual(calls[0]["tool_name"], "control_page.memory_panel")
        self.assertEqual(calls[0]["arguments"], {"action": "open"})
        self.assertEqual(state["ui"], [("open", "memory")])

    def test_execute_control_page_tool_dispatches_runtime_tool_with_runtime_callbacks(self) -> None:
        async def runtime_tool(tool_name, **kwargs):
            self.assertEqual(tool_name, "runtime.status")
            self.assertEqual(kwargs["guild"].id, 7)
            self.assertEqual(kwargs["build_status_reply"](), "status")
            return "runtime reply"

        deps, _state = _deps(
            control_page_tool_policy_error=lambda *_args, **_kwargs: None,
            execute_control_page_runtime_tool=runtime_tool,
            guild_getter_runtime={"build_status_reply": lambda: "status"},
        )

        reply = asyncio.run(self._run_execute({"tool": "runtime.status"}, deps, guild=SimpleNamespace(id=7)))

        self.assertEqual(reply, "runtime reply")

    async def _run_input(self, text: str, deps: ControlPageInputRuntimeDeps, guild=None) -> str:
        return await handle_control_page_input_from_runtime(guild, text, deps=deps)

    async def _run_input_with_receipt(
        self,
        text: str,
        deps: ControlPageInputRuntimeDeps,
        guild=None,
    ):
        reply = await handle_control_page_input_from_runtime(
            guild,
            text,
            deps=deps,
        )
        return reply, current_conversation_memory_receipt_ref()

    def test_handle_control_page_input_uses_cheap_decision_before_router(self) -> None:
        deps, state = _input_deps(
            cheap_control_page_tool_decision=lambda _text: {"tool": "runtime.status"},
            should_route_control_page_tool_candidate=lambda _text: True,
        )

        reply, receipt = asyncio.run(
            self._run_input_with_receipt(
                "status",
                deps,
                guild=SimpleNamespace(id=7),
            )
        )

        self.assertEqual(reply, "executed:runtime.status")
        self.assertEqual(len(state["executed"]), 1)
        self.assertEqual(len(state["remembered"]), 1)
        self.assertEqual(state["decided"], [])
        self.assertEqual(
            receipt["state"],
            "not_used",
        )

    def test_handle_control_page_input_blocks_router_policy_before_execution_reply(self) -> None:
        async def decide_tool_call(_text, **_kwargs):
            return {"tool": "runtime.restart_bot"}

        deps, state = _input_deps(
            should_route_control_page_tool_candidate=lambda _text: True,
            decide_control_page_tool_call=decide_tool_call,
            control_page_tool_policy_error=lambda *_args, **_kwargs: "blocked",
        )

        reply = asyncio.run(self._run_input("restart maybe", deps))

        self.assertEqual(reply, "blocked")
        self.assertEqual(state["executed"], [])
        self.assertEqual(len(state["remembered"]), 1)

    def test_handle_control_page_input_uses_router_reply_without_tool_decision(self) -> None:
        async def decide_tool_call(_text, **_kwargs):
            return {"reply": "router says no tool"}

        deps, state = _input_deps(
            should_route_control_page_tool_candidate=lambda _text: True,
            decide_control_page_tool_call=decide_tool_call,
        )

        reply, receipt = asyncio.run(
            self._run_input_with_receipt("ambiguous", deps)
        )

        self.assertEqual(reply, "router says no tool")
        self.assertEqual(state["executed"], [])
        self.assertEqual(state["remembered"], [])
        self.assertEqual(
            receipt["state"],
            "unattributed",
        )

    def test_handle_control_page_input_routes_search_before_main_text(self) -> None:
        deps, _state = _input_deps(should_force_search_query=lambda _text: True)

        reply = asyncio.run(self._run_input("오늘 날씨", deps))

        self.assertEqual(reply, "search:오늘 날씨")


if __name__ == "__main__":
    unittest.main()
