from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"
CONTROL_PAGE_STATE = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_state.py"


class ControlPageWelcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")
        cls.control_page_state = CONTROL_PAGE_STATE.read_text(encoding="utf-8")

    def test_initial_welcome_uses_main_llm_once_per_chat_log(self) -> None:
        self.assertIn("CONTROL_PAGE_WELCOME_LLM_TIMEOUT_SEC", self.main_py)
        self.assertIn("async def generate_control_page_welcome_text(", self.main_py)
        self.assertIn('purpose="control_page_welcome"', self.main_py)
        self.assertIn("LLM_SERVER_URL", self.main_py)
        self.assertIn("MODEL_NAME", self.main_py)

    def test_welcome_is_cached_in_control_page_chat_log(self) -> None:
        self.assertIn("control_page_welcome_locks: dict[int, asyncio.Lock] = {}", self.main_py)
        self.assertIn("async def ensure_control_page_welcome_message(", self.main_py)
        self.assertIn("if get_control_page_chat_log(guild_id):", self.main_py)
        self.assertIn('append_control_page_chat_log(guild_id, "assistant", "Evelyn", welcome)', self.main_py)

    def test_state_generation_waits_for_main_service_ready(self) -> None:
        self.assertIn('if not bool(services.get("mainReady")):', self.main_py)
        self.assertIn("await ensure_control_page_welcome_message(None, runtime_services=runtime_services)", self.main_py)
        self.assertIn("await ensure_control_page_welcome_message(guild, runtime_services=runtime_services)", self.main_py)

    def test_prompt_avoids_command_hint_replacement(self) -> None:
        self.assertIn("명령어 설명, /memory 안내, 기능 소개", self.main_py)
        self.assertIn("한국어 한 문장만 출력한다.", self.main_py)
        self.assertIn("현재 상태를 확인한 척하지 않는다.", self.main_py)

    def test_boot_progress_does_not_block_on_stale_warmup_failure(self) -> None:
        self.assertIn("return build_control_page_boot_progress_payload(", self.main_py)
        self.assertIn('main_ready = bool(services.get("mainReady"))', self.control_page_state)
        self.assertIn('tts_ready = bool(services.get("ttsReady"))', self.control_page_state)
        self.assertIn('"main_warmup": (startup_component_state.get("main_warmup") or {}).get("status") == "done" or main_ready', self.control_page_state)
        self.assertIn('"tts_warmup": (startup_component_state.get("tts_warmup") or {}).get("status") == "done" or tts_ready', self.control_page_state)


if __name__ == "__main__":
    unittest.main()
