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
    extract_minecraft_recent_activity,
    extract_minecraft_recent_activity_live,
    format_minecraft_state_summary,
    format_position_short,
    merge_voyager_status_into_state,
    minecraft_runtime_status_fields,
    normalize_inventory_slot_entries,
    normalize_inventory_top_entries,
    normalize_inventory_used_slots,
    normalize_minecraft_item_name,
    summarize_inventory_top,
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

    def test_format_minecraft_state_summary_keeps_llm_context_compact(self) -> None:
        state = attach_minecraft_runtime_snapshot(
            {
                "minecraft_autonomy": True,
                "voyager_connected": True,
                "objective_goal": "diamond",
                "objective_stage": "mining",
                "objective_task": "collect coal",
                "position": {"x": 1, "y": 64, "z": -2},
                "health": 20,
                "hunger": 17,
                "inventory": {"stone": 3, "stick": 8},
                "nearby_blocks": ["stone", "coal_ore"],
                "active_environment": "overworld",
            },
            source="test",
            now=100.0,
            observed_at=98.0,
        )

        summary = format_minecraft_state_summary(state)

        self.assertIn("snapshot=fresh", summary)
        self.assertIn("Voyager=on", summary)
        self.assertIn("연결=on", summary)
        self.assertIn("Voyager목표=diamond", summary)
        self.assertIn("위치=(1,64,-2)", summary)
        self.assertIn("인벤토리=stickx8, stonex3", summary)
        self.assertIn("주변블록=stone, coal_ore", summary)

    def test_recent_activity_extracts_completed_failed_and_live_rows(self) -> None:
        status = {
            "current_task": "mine iron",
            "current_task_stage": "digging",
            "stability_signals": {"phase_age_seconds": 12.4},
            "current_task_bookkeeping": {
                "rollout_iteration": 1,
                "max_rollout_iterations": 3,
                "verification_state": "checking",
            },
            "last_progress_message": "found cave",
            "last_search_metrics": {
                "helper": "path",
                "goal_type": "ore",
                "completion_reason": "nearby",
            },
            "last_world_effect_verification": {
                "summary": "block unchanged",
                "reason_code": "no_effect",
                "outcome": "fail",
            },
            "completed_tasks": ["craft pickaxe", "mine stone"],
            "failed_tasks": [{"task": "mine iron", "reason": "tool broke"}],
        }

        base_rows = extract_minecraft_recent_activity(status)
        live_rows = extract_minecraft_recent_activity_live(status, base_limit=2)

        self.assertIn({"kind": "completed", "label": "mine stone", "detail": "완료"}, base_rows)
        self.assertIn({"kind": "failed", "label": "mine iron", "detail": "tool broke"}, base_rows)
        self.assertEqual(live_rows[0], {"kind": "live", "label": "mine iron", "detail": "digging / 12s"})
        self.assertIn({"kind": "live", "label": "rollout 2/3", "detail": "checking"}, live_rows)
        self.assertIn({"kind": "live", "label": "found cave", "detail": "progress"}, live_rows)
        self.assertEqual(len(live_rows), 6)

    def test_merge_voyager_status_preserves_observation_and_evaluation(self) -> None:
        merged = merge_voyager_status_into_state(
            {
                "running": True,
                "connected": True,
                "goal": "diamond",
                "stage": "mine",
                "current_task": "dig",
                "current_task_stage": "verify",
                "last_action": "swing",
                "last_progress_message": "found ore",
                "autonomy_current_execution": {"description": "fallback task", "stage": "fallback stage"},
                "voyager_evaluation": {
                    "unique_item_count": 5,
                    "travel_distance_blocks": 12,
                    "tech_tree": {"highest_unlocked": "iron"},
                    "skill_library": {"size": 7},
                },
            },
            {"position": {"x": 1, "y": 2, "z": 3}, "health": 20},
        )

        self.assertIsNotNone(merged)
        assert merged is not None
        self.assertTrue(merged["minecraft_autonomy"])
        self.assertTrue(merged["voyager_connected"])
        self.assertEqual(merged["objective_goal"], "diamond")
        self.assertEqual(merged["objective_task"], "dig")
        self.assertEqual(merged["position"], {"x": 1, "y": 2, "z": 3})
        self.assertEqual(merged["voyager_unique_item_count"], 5)
        self.assertEqual(merged["voyager_tech_tree_highest"], "iron")
        self.assertEqual(merged["voyager_skill_library_size"], 7)

    def test_inventory_and_position_helpers_keep_control_page_contract(self) -> None:
        top_entries = normalize_inventory_top_entries(
            {
                "minecraft:stone": "3",
                "stick": 8,
                "bad": 0,
            },
            limit=2,
        )
        slots = normalize_inventory_slot_entries(
            [{"slot": 36, "item": "minecraft:stone", "count": 3, "selected": True}],
            inventory={"stick": 8},
        )
        fallback_slots = normalize_inventory_slot_entries(None, inventory={"stick": 8})

        self.assertEqual(top_entries, [{"name": "stick", "count": 8}, {"name": "minecraft:stone", "count": 3}])
        self.assertEqual(summarize_inventory_top(top_entries), "stick x8, minecraft:stone x3")
        self.assertEqual(normalize_minecraft_item_name("minecraft:Iron Ingot!"), "iron_ingot")
        self.assertEqual(format_position_short({"x": 1, "y": 64, "z": -2}), "1.0, 64.0, -2.0")
        self.assertEqual(format_position_short("unknown place"), "unknown place")
        self.assertEqual(slots[-10]["section"], "hotbar")
        self.assertEqual(slots[-10]["item"], "stone")
        self.assertEqual(slots[-10]["count"], 3)
        self.assertEqual(normalize_inventory_used_slots(None, slots), 1)
        self.assertEqual(fallback_slots[4]["item"], "stick")
        self.assertEqual(fallback_slots[4]["displayName"], "stick")


if __name__ == "__main__":
    unittest.main()
