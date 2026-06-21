from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())


class RuntimeDependencyContextTests(unittest.TestCase):
    def test_main_llm_receives_runtime_dependency_topology(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        context_assembly_py = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "llm_context_assembly.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def build_evelyn_runtime_dependency_context", main_py)
        self.assertIn("render_self_judgment_context", main_py)
        self.assertIn("self_judgment_context = deps.render_self_judgment_context", context_assembly_py)
        self.assertIn("self_judgment_context", context_assembly_py)
        self.assertIn("Evelyn dependency topology:", main_py)
        self.assertIn("role=primary answer text generation", main_py)
        self.assertIn("role=route/cognitive policy before the main answer", main_py)
        self.assertIn("runtime_state=runtime_context if context_policy.needs_runtime_state else dependency_context", context_assembly_py)

    def test_runtime_status_context_includes_current_gpu_oom_signal(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        route_execution_py = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_route_execution.py"
        ).read_text(encoding="utf-8")
        runtime_status_context = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "runtime_status_context.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def load_runtime_gpu_status", runtime_status_context)
        self.assertIn("current_gpu_snapshot=", main_py)
        self.assertIn("current_oom_signal=", main_py)
        self.assertIn("recent_errors_are_historical=true", main_py)
        self.assertIn("RUNTIME_STATUS_RULE", main_py)
        self.assertIn("needs_runtime_status_context = route_decision.needs_runtime_state", route_execution_py)
        self.assertIn("def answer_gpu_runtime_status_query", runtime_status_context)
        self.assertIn("gpu_runtime_status_fast_path", route_execution_py)

    def test_main_llm_blocks_unrequested_minecraft_domain_leaks(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        route_execution_py = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_route_execution.py"
        ).read_text(encoding="utf-8")
        prompt_contract = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "assistant_prompt_contract.py"
        ).read_text(encoding="utf-8")
        response_policy = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "response_output_policy.py"
        ).read_text(encoding="utf-8")

        self.assertIn("build_evelyn_system_prompt", main_py)
        self.assertIn("Domain rule: Minecraft/Voyager/block/coordinate/pathfinding", prompt_contract)
        self.assertIn("def user_explicitly_mentions_minecraft", response_policy)
        self.assertIn("def answer_contains_minecraft_leak", response_policy)
        self.assertIn("def sanitize_unrequested_minecraft_leak", response_policy)
        self.assertIn("fallback_for_unrequested_minecraft_leak", response_policy)
        self.assertIn("negative_or_meta_markers", response_policy)
        self.assertIn("하지 마", response_policy)
        self.assertIn("def answer_simple_local_chat_query", response_policy)
        self.assertIn("simple_local_chat_fast_path", route_execution_py)
        self.assertIn("suppressed_minecraft_leak_stream", route_execution_py)
        self.assertIn("그쪽 얘기는 빼고", response_policy)
        self.assertNotIn("return \"마크 얘기는 빼고", main_py)
        self.assertIn("응답 규칙: 짧게 바로 답해라", main_py)
        self.assertIn("답변 끝에 새 질문을 덧붙이지 마라", main_py)
        self.assertNotIn("[QUESTION_HINT]", main_py)
        self.assertIn("Vision rule: Do not claim you can see the user's screen", prompt_contract)


if __name__ == "__main__":
    unittest.main()
