from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class ConversationPolicyDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_policy_composition_before_session_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "conversation_policy_dependency_composition = ConversationPolicyDependencyComposition("
        )
        session_index = source.index(
            "conversation_session_composition = ConversationSessionComposition("
        )

        self.assertLess(composition_index, session_index)
        for name in (
            "build_question_policy_runtime_deps",
            "build_question_policy_state_runtime_deps",
            "build_session_turn_runtime_deps",
            "build_discord_session_policy_runtime_deps",
            "build_response_output_policy_runtime_deps",
        ):
            self.assertIn(f"conversation_policy_dependency_composition.{name}", source)

    def test_composition_keeps_five_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "conversation_policy_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ConversationPolicyDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_question_policy_runtime_deps",
            "build_question_policy_state_runtime_deps",
            "build_session_turn_runtime_deps",
            "build_discord_session_policy_runtime_deps",
            "build_response_output_policy_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_has_explicit_policy_dependencies(self) -> None:
        source = (
            RUNTIME_ROOT
            / "evelyn_core"
            / "conversation_policy_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("session_state_store=deps.session_state_store", source)
        self.assertIn("audio_duration_fn=deps.audio_duration", source)
        self.assertIn("session_state_snapshot_fn=deps.session_state_snapshot", source)

    def test_main_partials_discord_session_policy_entrypoints(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn(
            "discord_session_policy_runtime_deps = build_discord_session_policy_runtime_deps()",
            source,
        )
        for name in (
            "should_ignore_short_transcription",
            "is_short_followup_candidate",
            "should_skip_full_stt_after_wake_probe",
            "should_require_confirm_exact_for_wake",
            "is_transport_corrupted_audio",
            "is_tail_fragment_candidate",
        ):
            self.assertNotIn(f"def {name}(", source)
            self.assertIn(f"{name} = partial(", source)

    def test_main_partials_response_output_entrypoints(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn(
            "response_output_policy_runtime_deps = build_response_output_policy_runtime_deps()",
            source,
        )
        for name in (
            "should_label_question_response",
            "fallback_for_unrequested_minecraft_leak",
            "sanitize_unrequested_minecraft_leak",
            "format_display_text",
        ):
            self.assertNotIn(f"def {name}(", source)
            self.assertIn(f"{name} = partial(", source)


if __name__ == "__main__":
    unittest.main()
