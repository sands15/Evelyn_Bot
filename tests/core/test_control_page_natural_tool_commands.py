from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"


def load_control_page_classifier() -> dict[str, Any]:
    source = MAIN_PY.read_text(encoding="utf-8")
    module = ast.parse(source)
    namespace: dict[str, Any] = {
        "Any": Any,
        "re": re,
        "clean_text": lambda value: " ".join(str(value or "").strip().split()),
    }

    def control_page_tool_decision(tool_name: str, **kwargs: Any) -> dict[str, Any]:
        return {"tool": tool_name, **kwargs}

    namespace["control_page_tool_decision"] = control_page_tool_decision
    wanted_functions = {
        "control_page_compact_has_any",
        "is_control_page_question_text",
        "is_control_page_runtime_status_request",
        "is_explicit_control_page_restart_request",
        "cheap_control_page_tool_decision",
        "should_route_control_page_tool_candidate",
    }
    for node in module.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "CONTROL_PAGE_SLASH_TOOL_ALIASES" for target in targets):
                exec(compile(ast.Module([node], []), str(MAIN_PY), "exec"), namespace)
        if isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            exec(compile(ast.Module([node], []), str(MAIN_PY), "exec"), namespace)
    return namespace


class ControlPageNaturalToolCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.classifier = staticmethod(load_control_page_classifier()["cheap_control_page_tool_decision"])

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


if __name__ == "__main__":
    unittest.main()
