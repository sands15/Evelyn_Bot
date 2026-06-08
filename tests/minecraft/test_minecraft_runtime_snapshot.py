import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.minecraft_runtime_snapshot import (  # noqa: E402
    attach_minecraft_runtime_snapshot,
    build_minecraft_runtime_snapshot,
    minecraft_runtime_status_fields,
)
from evelyn_core.context_pipeline import ContextPolicy, build_minecraft_skill_context  # noqa: E402


class MinecraftRuntimeSnapshotTests(unittest.TestCase):
    def test_fresh_connected_snapshot_contains_compact_state(self) -> None:
        snapshot = build_minecraft_runtime_snapshot(
            {
                "minecraft_autonomy": True,
                "voyager_connected": True,
                "goal": "progress_to_diamond",
                "current_task": "collect iron",
                "position": {"x": 1, "y": 64, "z": -2},
                "health": 20,
                "hunger": 17,
            },
            source="test",
            now=100.0,
            observed_at=98.0,
            stale_after_sec=10.0,
        )

        self.assertEqual(snapshot["freshness"], "fresh")
        self.assertEqual(snapshot["age_sec"], 2.0)
        self.assertEqual(snapshot["running"], True)
        self.assertEqual(snapshot["connected"], True)
        self.assertEqual(snapshot["position_text"], "1.0, 64.0, -2.0")
        self.assertEqual(snapshot["goal"], "progress_to_diamond")

    def test_absent_snapshot_is_explicit(self) -> None:
        snapshot = build_minecraft_runtime_snapshot({}, source="empty", now=100.0)

        self.assertEqual(snapshot["freshness"], "absent")
        self.assertEqual(snapshot["connected"], False)
        self.assertEqual(snapshot["age_sec"], None)
        self.assertEqual(snapshot["stale"], True)

    def test_stale_and_expired_are_distinct(self) -> None:
        snapshot = build_minecraft_runtime_snapshot(
            {"connected": True},
            source="cache",
            now=100.0,
            observed_at=50.0,
            stale_after_sec=10.0,
            expired_after_sec=30.0,
        )

        self.assertEqual(snapshot["freshness"], "expired")
        self.assertEqual(snapshot["stale"], True)
        self.assertEqual(snapshot["expired"], True)

    def test_error_snapshot_surfaces_error_without_claiming_live_state(self) -> None:
        snapshot = build_minecraft_runtime_snapshot(
            {},
            source="status",
            now=100.0,
            observed_at=99.0,
            last_error="service unavailable",
        )

        self.assertEqual(snapshot["freshness"], "error")
        self.assertEqual(snapshot["last_error"], "service unavailable")

    def test_attach_preserves_existing_flat_shape(self) -> None:
        state = attach_minecraft_runtime_snapshot(
            {"connected": True, "inventory_summary": "iron x3"},
            source="control",
            now=100.0,
            observed_at=100.0,
        )

        self.assertEqual(state["connected"], True)
        self.assertEqual(state["inventory_summary"], "iron x3")
        self.assertEqual(state["runtime_snapshot"]["inventory_summary"], "iron x3")
        self.assertEqual(state["snapshot_freshness"], "fresh")

    def test_skill_context_includes_snapshot_freshness(self) -> None:
        state = attach_minecraft_runtime_snapshot(
            {"connected": True, "goal": "progress_to_diamond"},
            source="cache",
            now=100.0,
            observed_at=50.0,
            stale_after_sec=10.0,
            expired_after_sec=90.0,
        )
        policy = ContextPolicy(needs_skill_graph=True)

        context = build_minecraft_skill_context(policy, user_text="minecraft status", minecraft_state=state)

        self.assertIn("runtime_snapshot:", context)
        self.assertIn("freshness=stale", context)

    def test_status_fields_expose_runtime_snapshot_for_ui(self) -> None:
        state = attach_minecraft_runtime_snapshot(
            {"connected": True},
            source="control",
            now=100.0,
            observed_at=90.0,
            stale_after_sec=5.0,
            expired_after_sec=30.0,
        )

        fields = minecraft_runtime_status_fields(state)

        self.assertEqual(fields["snapshotFreshness"], "stale")
        self.assertEqual(fields["snapshotAgeSec"], 10.0)
        self.assertEqual(fields["snapshotStale"], True)
        self.assertEqual(fields["snapshotExpired"], False)
        self.assertEqual(fields["runtimeSnapshot"]["source"], "control")


if __name__ == "__main__":
    unittest.main()
