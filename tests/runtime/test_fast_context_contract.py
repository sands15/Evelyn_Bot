from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.fast_context_contract import (  # noqa: E402
    build_fast_log_context,
    build_fast_control_context,
    build_fast_main_llm_messages,
)


async def fake_runtime_health() -> dict[str, object]:
    return {
        "overallState": "up",
        "summary": "All runtime services are ready.",
        "services": [
            {"id": "bot_api", "state": "up", "reason": "ok"},
            {"id": "main_llm", "state": "up", "reason": "ok"},
            {"id": "tts", "state": "up", "reason": "ok"},
        ],
        "diagnostics": [],
    }


async def fake_search(query: str) -> tuple[str, list[dict[str, str]]]:
    return query, [
        {
            "title": "Weather Example",
            "snippet": "Today is rainy and cool.",
            "url": "https://example.test/weather",
        }
    ]


async def fake_memory(_: str) -> str:
    return "Memory note: 정훈 prefers exact stabilization reports."


async def fake_logs(_: str) -> str:
    return "Recent Evelyn log evidence: background_start/Bot-Control.log\napi_error:500 while handling /shutdown"


class FastContextContractTests(unittest.IsolatedAsyncioTestCase):
    def test_bot_api_requirements_include_memory_recall_dependency(self) -> None:
        requirements = (REPO_ROOT / "docker" / "requirements.bot-api.txt").read_text(encoding="utf-8")

        self.assertIn("numpy", requirements)

    async def test_runtime_status_tool_is_executed_in_fast_context(self) -> None:
        context = await build_fast_control_context(
            "main llm runtime status and gpu status?",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
        )

        by_name = {item.tool_name: item for item in context.tool_use_decisions}
        self.assertIn("runtime_status", by_name)
        self.assertEqual(by_name["runtime_status"].status, "executed")
        self.assertIn("All runtime services are ready", by_name["runtime_status"].evidence)
        self.assertIn("runtime_status", context.system_context)
        self.assertIn("fast_control_route", context.system_context)

    async def test_current_info_executes_search_in_fast_context(self) -> None:
        context = await build_fast_control_context(
            "weather today?",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            search_provider=fake_search,
        )

        web = next(item for item in context.tool_use_decisions if item.tool_name == "web_current_info")
        self.assertTrue(web.auto_allowed)
        self.assertTrue(web.required_before_answer)
        self.assertEqual(web.status, "executed")
        self.assertIn("Weather Example", web.evidence)
        self.assertIn("Search tool result", context.system_context)
        self.assertIn("Today is rainy and cool", context.system_context)

    async def test_tool_diagnostic_executes_mounted_log_read_in_fast_context(self) -> None:
        context = await build_fast_control_context(
            "\uba54\uc778 llm\uc758 \ub3c4\uad6c \ud638\ucd9c\uc774 \ub108\ubb34 \uc57d\ud574",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            search_provider=fake_search,
            log_provider=fake_logs,
        )

        by_name = {item.tool_name: item for item in context.tool_use_decisions}
        self.assertIn("runtime_status", by_name)
        self.assertIn("local_file_or_log_read", by_name)
        self.assertEqual(by_name["local_file_or_log_read"].status, "executed")
        self.assertIn("api_error:500", by_name["local_file_or_log_read"].evidence)
        self.assertIn("runtime_log_read", context.system_context)
        self.assertIn("api_error:500", context.system_context)

    async def test_plain_log_request_executes_mounted_log_read_in_fast_context(self) -> None:
        context = await build_fast_control_context(
            "/shutdown api_error:500 로그 확인해줘",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            log_provider=fake_logs,
        )

        log_read = next(item for item in context.tool_use_decisions if item.tool_name == "local_file_or_log_read")
        self.assertTrue(log_read.auto_allowed)
        self.assertEqual(log_read.status, "executed")
        self.assertIn("api_error:500", context.system_context)

    def test_fast_log_context_reads_recent_bounded_log_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            nested = root / "background_start"
            nested.mkdir()
            log_path = nested / "Bot-Control.log"
            log_path.write_text(
                "\n".join(
                    [
                        "startup ok",
                        "[LOCAL BRIDGE] transcript='private voice text' [LOCAL BRIDGE] error=none",
                        "error authorization: should-not-leak",
                        "api_error:500 while handling /shutdown",
                    ]
                ),
                encoding="utf-16",
            )

            context = build_fast_log_context("/shutdown 로그 확인", roots=[root], max_files=2, max_chars=1000)

        self.assertIn("background_start", context)
        self.assertIn("api_error:500", context)
        self.assertIn("authorization=<redacted>", context)
        self.assertNotIn("should-not-leak", context)
        self.assertNotIn("private voice text", context)

    async def test_memory_recall_executes_in_fast_context(self) -> None:
        context = await build_fast_control_context(
            "memory previous preference?",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            memory_provider=fake_memory,
        )

        memory = next(item for item in context.tool_use_decisions if item.tool_name == "memory_recall")
        self.assertEqual(memory.status, "executed")
        self.assertIn("exact stabilization reports", memory.evidence)
        self.assertIn("[Retrieved Memory]", context.system_context)
        self.assertIn("exact stabilization reports", context.system_context)

    async def test_fast_main_llm_messages_include_context_pipeline_contract(self) -> None:
        messages = await build_fast_main_llm_messages(
            base_system_prompt="base prompt",
            recent_messages=[{"role": "assistant", "content": "previous"}],
            user_text="weather today?",
            final_user_text="final user text",
            source="control_page",
            runtime_health_provider=fake_runtime_health,
            search_provider=fake_search,
            memory_provider=fake_memory,
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("base prompt", messages[0]["content"])
        self.assertIn("[Tool Use Policy]", messages[0]["content"])
        self.assertIn("web_current_info", messages[0]["content"])
        self.assertIn("Weather Example", messages[0]["content"])
        self.assertEqual(messages[-1], {"role": "user", "content": "final user text"})


if __name__ == "__main__":
    unittest.main()
