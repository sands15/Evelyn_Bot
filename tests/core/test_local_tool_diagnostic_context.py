import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_tool_diagnostic_context import (  # noqa: E402
    build_local_tool_diagnostic_context,
    collect_local_tool_diagnostic_matches,
    local_tool_diagnostic_candidate_paths,
)


class LocalToolDiagnosticContextTests(unittest.TestCase):
    def test_collect_matches_returns_clean_line_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.py"
            path.write_text(
                "nothing here\n"
                "def build_tool_use_decisions(): pass\n"
                "runtime_status = True\n",
                encoding="utf-8",
            )

            matches = collect_local_tool_diagnostic_matches(
                path,
                ("build_tool_use_decisions", "runtime_status"),
                max_matches=2,
            )

        self.assertEqual(matches[0], "sample.py:2: def build_tool_use_decisions(): pass")
        self.assertEqual(matches[1], "sample.py:3: runtime_status = True")

    def test_candidate_paths_are_project_relative_contract(self) -> None:
        paths = local_tool_diagnostic_candidate_paths(REPO_ROOT)

        self.assertIn(REPO_ROOT / "main.py", paths)
        self.assertIn(REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "context_pipeline.py", paths)

    def test_build_context_only_for_tool_diagnostic_requests(self) -> None:
        self.assertEqual(build_local_tool_diagnostic_context("그냥 안녕", project_root=REPO_ROOT), "")

        context = build_local_tool_diagnostic_context("툴 호출 상태 확인해줘", project_root=REPO_ROOT)

        self.assertTrue(context.startswith("local_tool_diagnostic_snapshot:"))
        self.assertIn("build_tool_use_decisions", context)


if __name__ == "__main__":
    unittest.main()
