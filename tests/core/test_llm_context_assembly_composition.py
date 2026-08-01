from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.llm_context_assembly_composition import LlmContextAssemblyComposition, LlmContextAssemblyCompositionDeps


class LlmContextAssemblyCompositionTests(unittest.TestCase):
    def build(self):
        callback = Mock()
        async_callback = AsyncMock()
        merge_callback = Mock()
        deps = LlmContextAssemblyCompositionDeps(
            compute_runtime_mode=callback, apply_runtime_mode=callback,
            classify_llm_route_async=async_callback, session_topic_ids={},
            get_conversation_history=callback, read_cached_cognitive_state=callback,
            get_matching_speculative_policy=callback, fast_path_policy=callback,
            session_state_snapshot=callback, context_policy_for_fast_path_policy=callback,
            extract_question_policy_from_route_meta=callback, update_cognitive_state=async_callback,
            schedule_cognitive_refresh=callback, build_runtime_status_context=async_callback,
            project_root=REPO_ROOT, runtime_artifacts_root=REPO_ROOT / "runtime_artifacts",
            memory_index_dir=REPO_ROOT / "memory_index",
            observe_live_minecraft_state=async_callback,
            control_page_minecraft_cache_refresh_sec=1.0,
            control_page_minecraft_cache_max_stale_sec=2.0,
            local_tts_snapshot=Mock(return_value={"enabled": True}),
            local_mic_snapshot=Mock(return_value={"running": True}),
            local_only_mode=True, discord_enabled=False, model_name="main",
            llm_server_url="http://main", router_model_name="router",
            summary_model_name="summary", stt_model_name="stt", stt_backend="local",
            omnivoice_server_url="http://tts", omnivoice_voice="voice", omnivoice_speed=1.0,
            voice_input_mode_status_line=Mock(return_value="voice=local"),
            odyssey_capability_json_dir=REPO_ROOT, build_live_vision_context=async_callback,
            log_turn_event=callback,
            merge_cross_surface_context=merge_callback,
            log=callback,
        )
        return LlmContextAssemblyComposition(deps)

    def test_runtime_dependency_context_reads_live_snapshots(self) -> None:
        composition = self.build()
        text = composition.build_evelyn_runtime_dependency_context()
        self.assertIn("main", text)
        composition.deps.local_tts_snapshot.assert_called_once_with()
        composition.deps.local_mic_snapshot.assert_called_once_with()

    def test_runtime_deps_keep_live_route_callbacks(self) -> None:
        composition = self.build()
        runtime = composition.build_runtime_deps()
        self.assertIs(runtime.fast_path_policy, composition.deps.fast_path_policy)
        self.assertIs(runtime.classify_llm_route_async, composition.deps.classify_llm_route_async)
        self.assertIs(
            runtime.merge_cross_surface_context,
            composition.deps.merge_cross_surface_context,
        )

    def test_main_uses_explicit_binding(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("llm_context_assembly_composition = LlmContextAssemblyComposition(", source)
        self.assertIn("build_llm_context_assembly_deps = llm_context_assembly_composition.build_runtime_deps", source)


if __name__ == "__main__":
    unittest.main()
