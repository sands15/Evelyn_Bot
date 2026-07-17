from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_mode_policy import (  # noqa: E402
    RuntimeModeResolver,
    apply_runtime_mode_policy,
    compute_runtime_mode_from_state,
)


class RuntimeModePolicyTests(unittest.TestCase):
    def test_compute_runtime_mode_prefers_realtime_for_tts_backlog_or_queue_wait(self) -> None:
        self.assertEqual(compute_runtime_mode_from_state({}, tts_backlog=2, inflight_llm_requests=0), "realtime")
        self.assertEqual(
            compute_runtime_mode_from_state(
                {"meta": {"voice_queue_wait_ms": 250.0}},
                tts_backlog=0,
                inflight_llm_requests=0,
            ),
            "realtime",
        )
        self.assertEqual(
            compute_runtime_mode_from_state(
                {"marks": {"voice_queue_wait_ms": 300.0}},
                tts_backlog=0,
                inflight_llm_requests=0,
            ),
            "realtime",
        )

    def test_compute_runtime_mode_uses_congested_for_llm_pressure(self) -> None:
        self.assertEqual(compute_runtime_mode_from_state({}, tts_backlog=0, inflight_llm_requests=2), "congested")
        self.assertEqual(compute_runtime_mode_from_state({}, tts_backlog=0, inflight_llm_requests=1), "normal")

    def test_runtime_mode_resolver_reads_live_pressure_on_each_call(self) -> None:
        pressure = {"tts": 0, "llm": 0}
        resolver = RuntimeModeResolver(
            tts_backlog_get=lambda: pressure["tts"],
            inflight_llm_requests_get=lambda: pressure["llm"],
        )

        self.assertEqual(resolver({}), "normal")
        pressure["llm"] = 2
        self.assertEqual(resolver({}), "congested")
        pressure["tts"] = 2
        self.assertEqual(resolver({}), "realtime")

    def test_apply_runtime_mode_policy_sets_defaults_and_overrides(self) -> None:
        self.assertEqual(
            apply_runtime_mode_policy("normal"),
            {
                "skip_router": False,
                "skip_search_followup": False,
                "memory_update_mode": "normal",
                "tts_chunk_min_chars": 12,
            },
        )
        self.assertEqual(
            apply_runtime_mode_policy("realtime", {"custom": True}),
            {
                "custom": True,
                "skip_router": True,
                "skip_search_followup": True,
                "memory_update_mode": "defer",
                "tts_chunk_min_chars": 18,
            },
        )
        congested = apply_runtime_mode_policy("congested", {"skip_router": True})
        self.assertFalse(congested["skip_router"])
        self.assertEqual(congested["memory_update_mode"], "batch")


if __name__ == "__main__":
    unittest.main()
