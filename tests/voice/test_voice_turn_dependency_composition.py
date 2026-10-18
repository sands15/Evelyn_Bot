from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_mode_policy import RuntimeModeResolver
from evelyn_core.runtime_state import RuntimeCounter


class VoiceTurnDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_live_runtime_mode_before_voice_consumer(self) -> None:
        tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))
        namespace = {
            "RuntimeModeResolver": RuntimeModeResolver,
            "RuntimeCounter": RuntimeCounter,
            # No audio is played: only the composition's pressure inputs are needed.
            "tts_playback_tracker": [],
            "tracked_tts_playback_count": len,
        }
        consumer = None
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            if name in {"inflight_llm_requests_counter", "compute_runtime_mode"}:
                exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), namespace)
            elif name == "voice_turn_dependency_composition":
                deps_call = node.value.args[0]
                binding = next(keyword.value for keyword in deps_call.keywords if keyword.arg == "compute_runtime_mode")
                # Evaluate the actual eager argument at its original point in startup.
                try:
                    consumer = eval(compile(ast.Expression(binding), "main.py", "eval"), namespace)
                except NameError as exc:
                    self.fail(f"voice composition references an unbound dependency: {exc}")

        self.assertIsNotNone(consumer)
        self.assertEqual(consumer(None), "normal")
        counter = namespace["inflight_llm_requests_counter"]
        counter.increment()
        counter.increment()
        self.assertEqual(consumer(None), "congested")
        namespace["tts_playback_tracker"].extend((object(), object()))
        self.assertEqual(consumer(None), "realtime")
        namespace["tts_playback_tracker"].clear()
        counter.decrement()
        counter.decrement()
        self.assertEqual(consumer(None), "normal")

    def test_main_binds_turn_composition_before_fast_path_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "voice_turn_dependency_composition = VoiceTurnDependencyComposition("
        )
        fast_path_index = source.index("fast_path_policy_composition = FastPathPolicyComposition(")
        self.assertLess(composition_index, fast_path_index)
        for name in (
            "build_voice_barge_in_continuity_runtime_deps",
            "build_voice_reply_side_effect_deps",
            "build_voice_reply_gate_runtime_deps",
            "build_voice_ingress_runtime_deps",
            "build_voice_ingress_entrypoint_deps",
        ):
            self.assertIn(f"voice_turn_dependency_composition.{name}", source)

    def test_composition_keeps_five_builder_signatures(self) -> None:
        module = ast.parse(
            (RUNTIME_ROOT / "evelyn_core" / "voice_turn_dependency_composition.py").read_text(
                encoding="utf-8"
            )
        )
        cls = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "VoiceTurnDependencyComposition"
        )
        functions = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
        for name in (
            "build_voice_barge_in_continuity_runtime_deps",
            "build_voice_reply_side_effect_deps",
            "build_voice_reply_gate_runtime_deps",
            "build_voice_ingress_runtime_deps",
            "build_voice_ingress_entrypoint_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_has_explicit_turn_dependencies(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "voice_turn_dependency_composition.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("schedule_memory_update=deps.schedule_memory_update", source)
        self.assertIn("process_member_audio=deps.process_member_audio", source)
        self.assertIn("schedule_voice_utterance_item=deps.schedule_voice_utterance_item", source)


if __name__ == "__main__":
    unittest.main()
