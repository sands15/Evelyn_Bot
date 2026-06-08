from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class RuntimeDependencyContextTests(unittest.TestCase):
    def test_main_llm_receives_runtime_dependency_topology(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("def build_evelyn_runtime_dependency_context", main_py)
        self.assertIn("Evelyn dependency topology:", main_py)
        self.assertIn("role=primary answer text generation", main_py)
        self.assertIn("role=route/cognitive policy before the main answer", main_py)
        self.assertIn("runtime_state=runtime_context if context_policy.needs_runtime_state else dependency_context", main_py)

    def test_runtime_status_context_includes_current_gpu_oom_signal(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("def load_runtime_gpu_status", main_py)
        self.assertIn("current_gpu_snapshot=", main_py)
        self.assertIn("current_oom_signal=", main_py)
        self.assertIn("recent_errors_are_historical=true", main_py)
        self.assertIn("RUNTIME_STATUS_RULE", main_py)
        self.assertIn("needs_runtime_status_context = route_decision.needs_runtime_state", main_py)
        self.assertIn("def answer_gpu_runtime_status_query", main_py)
        self.assertIn("gpu_runtime_status_fast_path", main_py)

    def test_main_llm_blocks_unrequested_minecraft_domain_leaks(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("Domain rule: Minecraft/Voyager/block/coordinate/pathfinding", main_py)
        self.assertIn("def user_explicitly_mentions_minecraft", main_py)
        self.assertIn("def answer_contains_minecraft_leak", main_py)
        self.assertIn("def sanitize_unrequested_minecraft_leak", main_py)
        self.assertIn("fallback_for_unrequested_minecraft_leak", main_py)
        self.assertIn("negative_or_meta_markers", main_py)
        self.assertIn("하지 마", main_py)
        self.assertIn("def answer_simple_local_chat_query", main_py)
        self.assertIn("simple_local_chat_fast_path", main_py)
        self.assertIn("suppressed_minecraft_leak_stream", main_py)
        self.assertIn("그쪽 얘기는 빼고", main_py)
        self.assertNotIn("return \"마크 얘기는 빼고", main_py)
        self.assertIn("응답 규칙: 짧게 바로 답해라", main_py)
        self.assertIn("답변 끝에 새 질문을 덧붙이지 마라", main_py)
        self.assertNotIn("[QUESTION_HINT]", main_py)
        self.assertIn("Vision rule: Do not claim you can see the user's screen", main_py)


if __name__ == "__main__":
    unittest.main()
