import sys
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_artifacts_retention import (  # noqa: E402
    DEFAULT_RETENTION_RULES,
    RetentionRule,
    apply_cleanup_plan,
    build_cleanup_plan,
    inventory_runtime_artifacts,
    main,
)


def write_file(path: Path, text: str, *, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.touch()
    import os

    os.utime(path, (mtime, mtime))


class RuntimeArtifactsRetentionTests(unittest.TestCase):
    def test_autonomy_validation_artifacts_have_bounded_retention(
        self,
    ) -> None:
        rules = {
            row.name: row
            for row in DEFAULT_RETENTION_RULES
            if row.name.startswith("autonomy_validation_")
        }

        reports = rules["autonomy_validation_reports"]
        self.assertEqual(
            reports.patterns,
            ("autonomy_validation/reports/*.json",),
        )
        self.assertEqual(reports.max_age_days, 30)
        self.assertEqual(reports.max_total_bytes, 20 * 1024 * 1024)
        self.assertEqual(reports.preserve_newest, 20)

        events = rules["autonomy_validation_events"]
        self.assertEqual(
            events.patterns,
            ("autonomy_validation/events/*.jsonl",),
        )
        self.assertEqual(events.max_age_days, 30)
        self.assertEqual(events.max_total_bytes, 50 * 1024 * 1024)
        self.assertEqual(events.preserve_newest, 20)

    def test_autonomy_authorization_journal_has_bounded_retention(
        self,
    ) -> None:
        rule = next(
            row
            for row in DEFAULT_RETENTION_RULES
            if row.name == "autonomy_authorization_events"
        )

        self.assertEqual(
            rule.patterns,
            ("autonomy_authorization/events/*.jsonl",),
        )
        self.assertEqual(rule.max_age_days, 30)
        self.assertEqual(rule.max_total_bytes, 20 * 1024 * 1024)
        self.assertEqual(rule.preserve_newest, 7)

    def test_minecraft_world_lease_journal_has_bounded_retention(
        self,
    ) -> None:
        rule = next(
            row
            for row in DEFAULT_RETENTION_RULES
            if row.name == "minecraft_world_lease_events"
        )

        self.assertEqual(
            rule.patterns,
            ("minecraft_world_lease/events/*.jsonl",),
        )
        self.assertEqual(rule.max_age_days, 30)
        self.assertEqual(rule.max_total_bytes, 20 * 1024 * 1024)
        self.assertEqual(rule.preserve_newest, 7)

    def test_mindcraft_world_effect_journal_has_bounded_retention(
        self,
    ) -> None:
        rule = next(
            row
            for row in DEFAULT_RETENTION_RULES
            if row.name == "mindcraft_world_effect_events"
        )

        self.assertEqual(
            rule.patterns,
            ("mindcraft_world_effect/events/*.jsonl",),
        )
        self.assertEqual(rule.max_age_days, 30)
        self.assertEqual(rule.max_total_bytes, 20 * 1024 * 1024)
        self.assertEqual(rule.preserve_newest, 7)

    def test_host_ui_action_journal_has_bounded_retention(
        self,
    ) -> None:
        rule = next(
            row
            for row in DEFAULT_RETENTION_RULES
            if row.name == "host_ui_action_events"
        )

        self.assertEqual(
            rule.patterns,
            ("host_ui_action/events/*.jsonl",),
        )
        self.assertEqual(rule.max_age_days, 30)
        self.assertEqual(rule.max_total_bytes, 20 * 1024 * 1024)
        self.assertEqual(rule.preserve_newest, 7)

    def test_inventory_stays_within_root(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_file(root / "logs" / "app.log", "hello", mtime=time.time())

            artifacts = inventory_runtime_artifacts(root)

        self.assertEqual([item.relative_path for item in artifacts], ["logs/app.log"])

    def test_age_rule_selects_old_file_but_preserves_newest(self) -> None:
        now = 1_000_000.0
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_file(root / "logs" / "old.log", "old", mtime=now - 10 * 86400)
            write_file(root / "logs" / "new.log", "new", mtime=now - 1)

            plan = build_cleanup_plan(
                root,
                rules=(RetentionRule("logs", ("logs/*.log",), max_age_days=2, preserve_newest=1),),
                now=now,
            )

        self.assertEqual([item.relative_path for item in plan.candidates], ["logs/old.log"])

    def test_size_rule_selects_oldest_until_under_limit(self) -> None:
        now = 1_000_000.0
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_file(root / "benchmarks" / "a.jsonl", "a" * 10, mtime=now - 30)
            write_file(root / "benchmarks" / "b.jsonl", "b" * 10, mtime=now - 20)
            write_file(root / "benchmarks" / "c.jsonl", "c" * 10, mtime=now - 10)

            plan = build_cleanup_plan(
                root,
                rules=(RetentionRule("bench", ("benchmarks/*.jsonl",), max_total_bytes=15, preserve_newest=1),),
                now=now,
            )

        self.assertEqual([item.relative_path for item in plan.candidates], ["benchmarks/a.jsonl", "benchmarks/b.jsonl"])

    def test_active_status_files_are_not_selected(self) -> None:
        now = 1_000_000.0
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_file(root / "voyager" / "upstream_bridge_status.json", "{}", mtime=now - 100 * 86400)
            write_file(root / "voyager" / "death_events.jsonl", "{}", mtime=now - 100 * 86400)

            plan = build_cleanup_plan(
                root,
                rules=(RetentionRule("voyager", ("voyager/*",), max_age_days=1, preserve_newest=0),),
                now=now,
            )

        self.assertEqual([item.relative_path for item in plan.candidates], ["voyager/death_events.jsonl"])

    def test_owner_claim_lock_is_never_selected(self) -> None:
        now = 1_000_000.0
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_file(
                root / "voice_capture_consent" / "owner_claim.lock",
                "\0",
                mtime=now - 100 * 86400,
            )

            plan = build_cleanup_plan(
                root,
                rules=(
                    RetentionRule(
                        "voice_capture_consent",
                        ("voice_capture_consent/*",),
                        max_age_days=1,
                        preserve_newest=0,
                    ),
                ),
                now=now,
            )

        self.assertEqual(plan.candidates, [])

    def test_dry_run_does_not_delete_files(self) -> None:
        now = 1_000_000.0
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_path = root / "logs" / "old.log"
            write_file(old_path, "old", mtime=now - 10 * 86400)
            plan = build_cleanup_plan(
                root,
                rules=(RetentionRule("logs", ("logs/*.log",), max_age_days=1, preserve_newest=0),),
                now=now,
            )

            result = apply_cleanup_plan(plan, dry_run=True)

            self.assertTrue(old_path.exists())
        self.assertEqual(result["dry_run"], True)
        self.assertEqual(result["candidate_count"], 1)

    def test_apply_plan_deletes_only_candidates(self) -> None:
        now = 1_000_000.0
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_path = root / "logs" / "old.log"
            new_path = root / "logs" / "new.log"
            write_file(old_path, "old", mtime=now - 10 * 86400)
            write_file(new_path, "new", mtime=now - 1)
            plan = build_cleanup_plan(
                root,
                rules=(RetentionRule("logs", ("logs/*.log",), max_age_days=1, preserve_newest=1),),
                now=now,
            )

            result = apply_cleanup_plan(plan, dry_run=False)

            self.assertFalse(old_path.exists())
            self.assertTrue(new_path.exists())
        self.assertEqual(result["deleted"], ["logs/old.log"])

    def test_cli_defaults_to_dry_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_path = root / "logs" / "old.log"
            write_file(old_path, "old", mtime=time.time() - 10 * 86400)

            with redirect_stdout(StringIO()):
                exit_code = main(["--root", str(root)])

            self.assertTrue(old_path.exists())
        self.assertEqual(exit_code, 0)

    def test_log_backup_pattern_is_included_by_default(self) -> None:
        now = time.time()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup = root / "logs" / "service.log.1"
            write_file(backup, "old", mtime=now - 20 * 86400)
            write_file(root / "logs" / "service.log", "new", mtime=now)

            plan = build_cleanup_plan(root, now=now)

        self.assertEqual([item.relative_path for item in plan.candidates], ["logs/service.log.1"])

    def test_stale_conversation_continuity_checkpoint_is_selected(self) -> None:
        now = time.time()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = (
                root / "conversation_continuity" / "active.json"
            )
            write_file(
                checkpoint,
                '{"completedTurnText": true}',
                mtime=now - 2 * 86400,
            )
            write_file(
                (
                    root
                    / "conversation_continuity"
                    / "checkpoint_head.json"
                ),
                '{"contentFree": true}',
                mtime=now - 2 * 86400,
            )

            plan = build_cleanup_plan(root, now=now)

        self.assertEqual(
            [item.relative_path for item in plan.candidates],
            [
                "conversation_continuity/active.json",
                (
                    "conversation_continuity/"
                    "checkpoint_head.json"
                ),
            ],
        )

    def test_stale_fast_control_continuity_checkpoint_is_selected(
        self,
    ) -> None:
        now = time.time()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("active.json", "checkpoint_head.json"):
                write_file(
                    root / "fast_control_continuity" / name,
                    '{"contentFree": true}',
                    mtime=now - 2 * 86400,
                )

            plan = build_cleanup_plan(root, now=now)

        self.assertEqual(
            [item.relative_path for item in plan.candidates],
            [
                "fast_control_continuity/active.json",
                (
                    "fast_control_continuity/"
                    "checkpoint_head.json"
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
