from __future__ import annotations

import ast
import sys
import tempfile
import types
import unittest
from dataclasses import MISSING, fields
from pathlib import Path


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
RUNTIME_PACKAGE = RUNTIME_ROOT / "evelyn_core"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

try:
    import numpy as _numpy  # noqa: F401
except ImportError:
    class _DummyNdArray:
        pass

    sys.modules["numpy"] = types.SimpleNamespace(
        ndarray=_DummyNdArray,
    )

from evelyn_core.main_llm_runtime import (  # noqa: E402
    MainLlmRuntimeDeps,
)
from evelyn_core.autonomy_runtime_composition import (  # noqa: E402
    AutonomyRuntimeCompositionDeps,
)
from evelyn_core.autonomy_runtime_factory import (  # noqa: E402
    AutonomyRuntimeFactoryDeps,
)
from evelyn_core.search_answer_runtime import (  # noqa: E402
    SearchAnswerRuntimeDeps,
)
from evelyn_core.search_followup_runtime import (  # noqa: E402
    SearchFollowupRuntimeDeps,
)
from evelyn_core.voice_response_runtime import (  # noqa: E402
    VoiceResponseRuntimeDeps,
)
from evelyn_core.voice_route_execution import (  # noqa: E402
    VoiceMainLlmStreamingDeps,
    VoiceRouteExecutionDeps,
)


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _is_deps_memory_index_dir(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "deps"
        and node.attr == "memory_index_dir"
    )


class MemoryExposureIndexDirDiTests(unittest.TestCase):
    def test_runtime_dependency_constructors_require_the_same_path(
        self,
    ) -> None:
        runtime_types = (
            MainLlmRuntimeDeps,
            VoiceResponseRuntimeDeps,
            VoiceMainLlmStreamingDeps,
            VoiceRouteExecutionDeps,
            SearchAnswerRuntimeDeps,
            SearchFollowupRuntimeDeps,
            AutonomyRuntimeCompositionDeps,
            AutonomyRuntimeFactoryDeps,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_index_dir = Path(temp_dir) / "memory_index"
            for runtime_type in runtime_types:
                with self.subTest(runtime_type=runtime_type.__name__):
                    kwargs = {}
                    for field in fields(runtime_type):
                        if field.name == "memory_index_dir":
                            kwargs[field.name] = memory_index_dir
                        elif (
                            field.default is MISSING
                            and field.default_factory is MISSING
                        ):
                            kwargs[field.name] = object()
                    deps = runtime_type(**kwargs)
                    self.assertIs(
                        deps.memory_index_dir,
                        memory_index_dir,
                    )
                    self.assertIsInstance(
                        deps.memory_index_dir,
                        Path,
                    )

    def test_every_runtime_exposure_call_uses_injected_index(
        self,
    ) -> None:
        expected_calls = {
            "main_llm_runtime.py": 1,
            "voice_response_runtime.py": 2,
            "voice_route_execution.py": 3,
            "search_followup_runtime.py": 5,
            "autonomy_runtime_factory.py": 1,
        }
        for filename, minimum_count in expected_calls.items():
            with self.subTest(filename=filename):
                tree = ast.parse(
                    (RUNTIME_PACKAGE / filename).read_text(
                        encoding="utf-8"
                    )
                )
                calls = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and _call_name(node)
                    in {
                        "memory_exposure_guard",
                        "memory_exposure_request",
                    }
                ]
                self.assertGreaterEqual(len(calls), minimum_count)
                for call in calls:
                    keyword_name = (
                        "index_dir"
                        if _call_name(call) == "memory_exposure_guard"
                        else "memory_index_dir"
                    )
                    keyword = next(
                        (
                            item
                            for item in call.keywords
                            if item.arg == keyword_name
                        ),
                        None,
                    )
                    self.assertIsNotNone(
                        keyword,
                        msg=(
                            f"{filename}:{call.lineno} lacks "
                            f"{keyword_name}"
                        ),
                    )
                    self.assertTrue(
                        _is_deps_memory_index_dir(keyword.value),
                        msg=(
                            f"{filename}:{call.lineno} does not use "
                            "deps.memory_index_dir"
                        ),
                    )

    def test_compositions_and_main_forward_configured_path(self) -> None:
        composition_targets = {
            "voice_response_dependency_composition.py": {
                "VoiceResponseRuntimeDeps",
                "MainLlmRuntimeDeps",
            },
            "voice_execution_dependency_composition.py": {
                "VoiceRouteExecutionDeps",
                "build_voice_main_llm_streaming_deps_from_runtime",
            },
            "search_memory_dependency_composition.py": {
                "SearchAnswerRuntimeDeps",
                "SearchFollowupRuntimeDeps",
            },
            "autonomy_runtime_composition.py": {
                "AutonomyRuntimeFactoryDeps",
            },
        }
        for filename, targets in composition_targets.items():
            tree = ast.parse(
                (RUNTIME_PACKAGE / filename).read_text(
                    encoding="utf-8"
                )
            )
            for target in targets:
                with self.subTest(filename=filename, target=target):
                    call = next(
                        node
                        for node in ast.walk(tree)
                        if isinstance(node, ast.Call)
                        and _call_name(node) == target
                    )
                    keyword = next(
                        item
                        for item in call.keywords
                        if item.arg == "memory_index_dir"
                    )
                    self.assertTrue(
                        _is_deps_memory_index_dir(keyword.value)
                    )

        main_tree = ast.parse(
            (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        )
        for target in (
            "VoiceResponseDependencyCompositionDeps",
            "VoiceExecutionDependencyCompositionDeps",
            "SearchMemoryDependencyCompositionDeps",
            "AutonomyRuntimeCompositionDeps",
        ):
            with self.subTest(main_target=target):
                call = next(
                    node
                    for node in ast.walk(main_tree)
                    if isinstance(node, ast.Call)
                    and _call_name(node) == target
                )
                keyword = next(
                    item
                    for item in call.keywords
                    if item.arg == "memory_index_dir"
                )
                self.assertEqual(
                    ast.unparse(keyword.value),
                    "Path(MEMORY_ROOT) / 'memory_index'",
                )


if __name__ == "__main__":
    unittest.main()
