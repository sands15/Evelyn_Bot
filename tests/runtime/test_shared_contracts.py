from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.assistant_prompt_contract import (  # noqa: E402
    FAST_MAIN_LLM_USER_PREFIX,
    build_evelyn_system_prompt,
    build_fast_main_llm_user_text,
)
from evelyn_core.control_page_contracts import (  # noqa: E402
    CONTROL_PAGE_UI_PANELS,
    build_control_page_panel_state_payload,
    detect_memory_panel_action,
)
from evelyn_core.llm_context_assembly import bound_voice_prompt_history  # noqa: E402


class SharedContractTests(unittest.TestCase):
    def test_prompt_contract_contains_core_evelyn_identity(self) -> None:
        prompt = build_evelyn_system_prompt(omnivoice_tag_guidance="tag guidance")

        self.assertIn("너는 Evelyn", prompt)
        self.assertIn("한국어로 친구처럼 짧게 반말", prompt)
        self.assertIn("정훈의 로컬 PC", prompt)
        self.assertIn("generic remote text-only chatbot", prompt)
        self.assertIn("Domain rule: Minecraft/Voyager/block/coordinate/pathfinding", prompt)
        self.assertIn("Vision rule: Do not claim you can see the user's screen", prompt)
        self.assertIn("tag guidance", prompt)

    def test_fast_user_text_keeps_dynamic_content_compact(self) -> None:
        text = build_fast_main_llm_user_text("안녕")

        self.assertNotIn(FAST_MAIN_LLM_USER_PREFIX, text)
        self.assertEqual(text, "안녕")

    def test_voice_prompt_keeps_system_and_only_recent_turns(self) -> None:
        messages = [{"role": "system", "content": "stable"}]
        messages.extend(
            {"role": "user" if index % 2 == 0 else "assistant", "content": str(index)}
            for index in range(12)
        )
        messages.insert(7, {"role": "system", "content": "dynamic"})

        bounded = bound_voice_prompt_history(messages, limit=4)

        self.assertEqual(bounded[0], {"role": "system", "content": "stable"})
        self.assertEqual([message["content"] for message in bounded[1:]], ["8", "9", "10", "11"])
        self.assertEqual(bound_voice_prompt_history(messages, limit=-1), [messages[0]])

    def test_control_page_memory_panel_contract_is_shared(self) -> None:
        self.assertEqual(CONTROL_PAGE_UI_PANELS["memory"], "Memory")
        self.assertEqual(
            CONTROL_PAGE_UI_PANELS["voice_validation"],
            "Voice validation",
        )
        self.assertEqual(detect_memory_panel_action("/memory"), "toggle")
        self.assertEqual(detect_memory_panel_action("메모리 패널 열어줘"), "open")

    def test_panel_state_payload_shape_matches_frontend_contract(self) -> None:
        state = build_control_page_panel_state_payload(
            [{"id": 1, "panel": "memory", "action": "open"}],
            revision=1,
        )

        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["commands"][0]["panel"], "memory")
        self.assertIn({"id": "memory", "label": "Memory"}, state["panels"])

    def test_control_page_server_imports_shared_panel_contract(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py"
        ).read_text(encoding="utf-8")

        self.assertIn("build_control_page_panel_state_payload", source)
        self.assertIn("memory_panel_reply as shared_memory_panel_reply", source)
        self.assertIn("return shared_memory_panel_reply(action)", source)


if __name__ == "__main__":
    unittest.main()
