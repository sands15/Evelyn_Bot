from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_status_context import (  # noqa: E402
    answer_gpu_runtime_status_query,
    compact_runtime_error,
    load_runtime_recent_errors,
    load_runtime_gpu_status,
    render_runtime_recent_error_marker,
    runtime_status_port_from_url,
    sanitize_runtime_recent_error_marker,
)


class RuntimeStatusContextTests(unittest.TestCase):
    def test_runtime_status_port_from_url_uses_scheme_default_ports(self) -> None:
        self.assertEqual(runtime_status_port_from_url("http://127.0.0.1:9820/v1/chat"), ("127.0.0.1", 9820))
        self.assertEqual(runtime_status_port_from_url("https://example.test/path"), ("example.test", 443))
        self.assertIsNone(runtime_status_port_from_url("not a url"))

    def test_compact_runtime_error_strips_whitespace_and_truncates(self) -> None:
        self.assertEqual(compact_runtime_error("  one\n two\tthree  "), "one two three")
        self.assertEqual(compact_runtime_error("abcdef", max_chars=5), "ab...")

    def test_load_runtime_gpu_status_marks_near_full_by_free_memory(self) -> None:
        completed = type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": "0, NVIDIA GeForce RTX 3090, 24000, 24576\n1, NVIDIA GeForce RTX 4060 Laptop GPU, 1000, 8192\n",
            },
        )()

        with patch("evelyn_core.runtime_status_context.subprocess.run", return_value=completed):
            status, near_full = load_runtime_gpu_status()

        self.assertTrue(near_full)
        self.assertIn("NVIDIA GeForce RTX 3090", status)
        self.assertIn("free=576MB", status)

    def test_answer_gpu_runtime_status_query_uses_gpu_snapshot(self) -> None:
        with patch(
            "evelyn_core.runtime_status_context.load_runtime_gpu_status",
            return_value=("gpu0 NVIDIA GeForce RTX 3090 used=1000/24576MB (4.1%), free=23576MB", False),
        ):
            answer = answer_gpu_runtime_status_query("GPU 상태 어때?")

        self.assertIn("현재 OOM 신호는 없어", answer)
        self.assertIn("NVIDIA GeForce RTX 3090", answer)

    def test_recent_error_loader_returns_content_free_markers(self) -> None:
        private = (
            "Bearer artifact-secret http://internal:9820 "
            "C:\\Users\\Admin\\private.txt"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_path = root / "codex_gateway" / "last_request.json"
            voyager_path = (
                root / "voyager" / "upstream_bridge_status.json"
            )
            logs = root / "logs"
            codex_path.parent.mkdir(parents=True)
            voyager_path.parent.mkdir(parents=True)
            logs.mkdir(parents=True)
            codex_path.write_text(
                json.dumps(
                    {
                        "phase": "error",
                        "error": private,
                        "stderr_tail": private,
                    }
                ),
                encoding="utf-8",
            )
            voyager_path.write_text(
                json.dumps({"last_error": private}),
                encoding="utf-8",
            )
            (logs / "voyager_service_errors.log").write_text(
                private,
                encoding="utf-8",
            )
            (logs / "upstream_bridge_errors.log").write_text(
                private,
                encoding="utf-8",
            )

            with patch(
                "evelyn_core.runtime_status_context."
                "RUNTIME_ARTIFACTS_ROOT",
                root,
            ):
                markers = load_runtime_recent_errors()

        self.assertEqual(len(markers), 3)
        self.assertEqual(
            [marker["owner"] for marker in markers],
            ["codex_gateway", "voyager", "voyager_service"],
        )
        self.assertEqual(
            [marker["code"] for marker in markers],
            [
                "codex_backend_failed",
                "voyager_runtime_failed",
                "voyager_service_log_present",
            ],
        )
        serialized = json.dumps(markers)
        self.assertNotIn("artifact-secret", serialized)
        self.assertNotIn("internal:9820", serialized)
        self.assertNotIn("private.txt", serialized)

    def test_success_critique_is_not_treated_as_recent_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            voyager_path = (
                root / "voyager" / "upstream_bridge_status.json"
            )
            voyager_path.parent.mkdir(parents=True)
            voyager_path.write_text(
                json.dumps(
                    {
                        "last_error": None,
                        "last_critique": (
                            "private successful critique"
                        ),
                        "last_completion_reason": "critic_success",
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "evelyn_core.runtime_status_context."
                "RUNTIME_ARTIFACTS_ROOT",
                root,
            ):
                markers = load_runtime_recent_errors()

        self.assertEqual(markers, [])

    def test_recent_error_marker_rejects_unknown_contract_values(
        self,
    ) -> None:
        private = "Bearer marker-secret C:\\private.txt"
        valid = {
            "schema": "runtime.recent-error.v1",
            "owner": "upstream_bridge",
            "code": "upstream_bridge_log_present",
            "ageBucket": "gte_1d",
            "detail": private,
        }

        sanitized = sanitize_runtime_recent_error_marker(valid)

        self.assertEqual(
            sanitized,
            {
                "schema": "runtime.recent-error.v1",
                "owner": "upstream_bridge",
                "code": "upstream_bridge_log_present",
                "ageBucket": "gte_1d",
            },
        )
        self.assertEqual(
            render_runtime_recent_error_marker(valid),
            "owner=upstream_bridge,"
            "code=upstream_bridge_log_present,age=gte_1d",
        )
        self.assertIsNone(
            sanitize_runtime_recent_error_marker(
                {
                    **valid,
                    "owner": "unknown",
                    "code": private,
                }
            )
        )
        self.assertNotIn(
            "marker-secret",
            render_runtime_recent_error_marker(valid),
        )


if __name__ == "__main__":
    unittest.main()
