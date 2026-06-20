from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_tools import (  # noqa: E402
    cheap_control_page_tool_decision,
    control_page_tool_policy_error,
    control_page_tool_reply_from_execution,
)


class ControlPageNaturalToolCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.classifier = staticmethod(cheap_control_page_tool_decision)

    def assertRoutes(self, text: str, tool_name: str) -> None:
        decision = self.classifier(text)
        self.assertIsNotNone(decision, text)
        self.assertEqual(decision["tool"], tool_name)

    def test_polite_runtime_control_questions_still_route_to_tools(self) -> None:
        cases = {
            "재시작해줄래?": "runtime.restart_bot",
            "메모리 패널 열어줄래?": "control_page.memory_panel",
            "메모리 패널 닫아줄래?": "control_page.memory_panel",
            "옵시디언 열어줄래?": "memory.open_vault",
            "음성 다시 연결해줄래?": "voice.reconnect",
            "마크 연결해줄래?": "minecraft.connect",
            "마크 종료해줄래?": "minecraft.disconnect",
        }
        for text, expected_tool in cases.items():
            with self.subTest(text=text):
                self.assertRoutes(text, expected_tool)

    def test_status_questions_and_real_questions_keep_their_intent(self) -> None:
        self.assertRoutes("이블린 지금 상태 어때?", "runtime.status")
        self.assertRoutes("llm 로딩 안돼?", "runtime.status")
        self.assertIsNone(self.classifier("날씨 어때?"))
        self.assertIsNone(self.classifier("왜 재시작해야 해?"))

    def test_tool_policy_and_reply_postprocessing(self) -> None:
        self.assertEqual(
            control_page_tool_policy_error({"tool": "minecraft.status"}, guild_available=False),
            "그 명령은 Discord 연결이 필요해.",
        )
        self.assertEqual(
            control_page_tool_policy_error(
                {"tool": "runtime.restart_bot", "risk": "medium", "source": "router", "confidence": 0.3},
                guild_available=True,
            ),
            "그 명령은 조금 애매해. 한 번만 더 정확히 말해줘.",
        )
        self.assertEqual(
            control_page_tool_reply_from_execution(
                {"tool": "control_page.memory_panel", "reply": "응, 열어둘게."},
                "메모리 패널을 열어둘게.",
            ),
            "응, 열어둘게.",
        )
        self.assertEqual(
            control_page_tool_reply_from_execution({"tool": "runtime.status", "reply": "router"}, "runtime"),
            "runtime",
        )


if __name__ == "__main__":
    unittest.main()
