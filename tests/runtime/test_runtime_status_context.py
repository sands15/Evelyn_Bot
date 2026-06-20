from __future__ import annotations

import sys
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
    load_runtime_gpu_status,
    runtime_status_port_from_url,
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


if __name__ == "__main__":
    unittest.main()
