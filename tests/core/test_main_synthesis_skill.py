from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.skills.base import SkillContext  # noqa: E402
from evelyn_core.skills.main_synthesis import execute  # noqa: E402


class MainSynthesisSkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_synthesis_calls_main_llm_callback_with_tool_result(self) -> None:
        calls: list[dict] = []

        async def fake_synthesis(**kwargs):
            calls.append(kwargs)
            return "광주 날씨는 지금 흐리고 선선한 편이야."

        result = await execute(
            SkillContext(
                source="control_page",
                guild_id=123,
                session_key="session-1",
                metrics={},
                extras={
                    "user_text": "광주광역시 날씨 찾아줘",
                    "tool_name": "search",
                    "tool_result_text": "광주광역시 현재 날씨: 흐림, 18도",
                    "synthesize_tool_result_with_main_llm_fn": fake_synthesis,
                    "messages": [{"role": "system", "content": "ctx"}],
                    "cognitive_state": {"action": "search_then_answer"},
                },
            )
        )

        self.assertEqual(result.route, "main_synthesis")
        self.assertEqual(result.display_text, "광주 날씨는 지금 흐리고 선선한 편이야.")
        self.assertTrue(result.metadata["synthesized"])
        self.assertEqual(calls[0]["tool_name"], "search")
        self.assertIn("광주광역시 현재 날씨", calls[0]["tool_result_text"])
        self.assertEqual(calls[0]["source"], "control_page")


if __name__ == "__main__":
    unittest.main()
