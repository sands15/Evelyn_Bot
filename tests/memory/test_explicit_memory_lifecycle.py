from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.explicit_memory_confirmation import (  # noqa: E402
    store_explicit_memory_confirmation,
)
from evelyn_core import memory_vault as memory_vault_module  # noqa: E402
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalBusyError,
)
from evelyn_core.memory_confirmation_contract import (  # noqa: E402
    memory_owner_scope,
    memory_reset_scope,
)
from evelyn_core.memory_prompt_policy import (  # noqa: E402
    prepare_memory_context_for_prompt,
    reconcile_memory_receipt_for_prompt,
    validated_memory_grounding_state,
)
from evelyn_core.memory_vault import (  # noqa: E402
    MemoryGuildResetError,
    append_turn_rows_to_memory_vault,
    build_memory_vault_context,
    consolidate_daily_memory_once,
    delete_memory_vault_user_note,
    memory_note_was_deleted,
    memory_vault_user_note,
    memory_vault_user_snapshot,
    parse_memory_note,
    preview_memory_vault_user_note_deletion,
    refresh_legacy_memory_mirror,
    refresh_legacy_memory_node_notes,
    reset_guild_memory_vault,
    run_memory_vault_maintenance_once,
    run_semantic_memory_consolidation_once,
    update_memory_vault_user_note,
    write_memory_vault_note,
)


class ExplicitMemoryLifecycleTests(unittest.TestCase):
    owner_scope = memory_owner_scope(
        guild_id=7,
        person_key="user:11",
    )

    def test_guild_reset_tombstones_exact_confirmed_notes_and_restart_cannot_recall(
        self,
    ) -> None:
        target_canary = "guild-reset-target-canary-1041"
        second_target_canary = "guild-reset-target-canary-1042"
        other_canary = "guild-reset-other-canary-1043"
        local_canary = "guild-reset-local-canary-1044"
        second_target_owner = memory_owner_scope(
            guild_id=7,
            person_key="user:12",
        )
        other_owner = memory_owner_scope(
            guild_id=8,
            person_key="user:11",
        )
        local_owner = memory_owner_scope(
            guild_id=None,
            person_key="control-page:local",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_notes = (
                store_explicit_memory_confirmation(
                    target_canary,
                    action_id="guild-reset-action-1041",
                    source="discord-user",
                    owner_scope=self.owner_scope,
                    reset_scope=memory_reset_scope(7),
                    root=root,
                ),
                store_explicit_memory_confirmation(
                    second_target_canary,
                    action_id="guild-reset-action-1042",
                    source="discord-user",
                    owner_scope=second_target_owner,
                    reset_scope=memory_reset_scope(7),
                    root=root,
                ),
            )
            other_note = store_explicit_memory_confirmation(
                other_canary,
                action_id="guild-reset-action-1043",
                source="control-page-user",
                owner_scope=other_owner,
                reset_scope=memory_reset_scope(8),
                root=root,
            )
            local_note = store_explicit_memory_confirmation(
                local_canary,
                action_id="guild-reset-action-1044",
                owner_scope=local_owner,
                root=root,
            )
            local_path = next(
                path
                for path in (
                    root / "memory_vault" / "concepts"
                ).glob("user-confirmed-*.md")
                if local_note["noteId"]
                in path.read_text(encoding="utf-8")
            )
            local_path.write_text(
                "\n".join(
                    line
                    for line in local_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if not line.startswith("reset_scope:")
                )
                + "\n",
                encoding="utf-8",
            )
            target_dir = root / "guild_7"
            other_dir = root / "guild_8"
            target_dir.mkdir()
            other_dir.mkdir()

            result = reset_guild_memory_vault(7, root=root)

            for note in target_notes:
                self.assertFalse(
                    memory_vault_user_note(
                        note["noteId"],
                        root=root,
                    )["ok"]
                )
                self.assertTrue(
                    memory_note_was_deleted(
                        note["noteId"],
                        root=root,
                    )
                )
            self.assertTrue(
                memory_vault_user_note(
                    other_note["noteId"],
                    root=root,
                )["ok"]
            )
            self.assertFalse(
                memory_note_was_deleted(
                    other_note["noteId"],
                    root=root,
                )
            )
            self.assertTrue(
                memory_vault_user_note(
                    local_note["noteId"],
                    root=root,
                )["ok"]
            )
            self.assertFalse(target_dir.exists())
            self.assertTrue(other_dir.exists())
            self.assertNotIn(target_canary, str(result))
            self.assertNotIn(memory_reset_scope(7), str(result))

            worker = """
import json
import sys
from pathlib import Path
from evelyn_core.memory_vault import build_memory_vault_context
root = Path(sys.argv[1])
target = build_memory_vault_context(
    7, sys.argv[2], owner_scope=sys.argv[3], root=root
)
other = build_memory_vault_context(
    8, sys.argv[4], owner_scope=sys.argv[5], root=root
)
print(json.dumps({"target": sys.argv[2] in target, "other": sys.argv[4] in other}))
"""
            environment = dict(os.environ)
            environment["PYTHONPATH"] = os.pathsep.join(
                filter(
                    None,
                    (
                        str(RUNTIME_ROOT),
                        environment.get("PYTHONPATH", ""),
                    ),
                )
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    worker,
                    str(root),
                    target_canary,
                    self.owner_scope,
                    other_canary,
                    other_owner,
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            restarted = json.loads(completed.stdout)

        self.assertEqual(restarted, {"target": False, "other": True})

    def test_guild_reset_legacy_scope_fails_before_any_mutation(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = store_explicit_memory_confirmation(
                "legacy-reset-scope-canary-1051",
                action_id="guild-reset-action-1051",
                source="control-page-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            target = store_explicit_memory_confirmation(
                "target-reset-scope-canary-1052",
                action_id="guild-reset-action-1052",
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            legacy_path = next(
                path
                for path in (
                    root / "memory_vault" / "concepts"
                ).glob("user-confirmed-*.md")
                if legacy["noteId"]
                in path.read_text(encoding="utf-8")
            )
            legacy_path.write_text(
                "\n".join(
                    line
                    for line in legacy_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if not line.startswith("reset_scope:")
                )
                + "\n",
                encoding="utf-8",
            )
            target_dir = root / "guild_7"
            target_dir.mkdir()

            with self.assertRaisesRegex(
                MemoryGuildResetError,
                "^memory_guild_reset_legacy_scope_missing$",
            ):
                reset_guild_memory_vault(7, root=root)

            self.assertTrue(target_dir.exists())
            self.assertTrue(
                memory_vault_user_note(
                    legacy["noteId"], root=root
                )["ok"]
            )
            self.assertTrue(
                memory_vault_user_note(
                    target["noteId"], root=root
                )["ok"]
            )

    def test_guild_reset_uses_canonical_binding_after_markers_are_damaged(
        self,
    ) -> None:
        canary = "guild-reset-damaged-marker-canary-1055"
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            renamed = write_memory_vault_note(
                note_type="concept",
                title="Renamed damaged reset binding",
                body=canary,
                storage_key="renamed-damaged-reset-binding",
                tags=[],
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                source_refs=["turn:guild-reset-action-1055:user"],
                evidence_hashes=[
                    hashlib.sha256(
                        canary.encode("utf-8")
                    ).hexdigest()
                ],
                confirmed_at="2026-08-16T00:00:00+00:00",
                root=root,
            )
            stored_note_id = parse_memory_note(renamed).note_id
            before = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                root=root,
            )

            reset_guild_memory_vault(7, root=root)

            after = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                root=root,
            )
            deleted_durably = memory_note_was_deleted(
                stored_note_id,
                root=root,
            )

        self.assertIn(canary, before)
        self.assertNotIn(canary, after)
        self.assertTrue(deleted_durably)

    def test_guild_reset_invalid_utf8_fails_before_deletion(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                "guild-reset-invalid-utf8-canary-1056",
                action_id="guild-reset-action-1056",
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            corrupt = (
                root
                / "memory_vault"
                / "concepts"
                / "corrupt-utf8.md"
            )
            corrupt.write_bytes(b"\xff\xfe")
            target_dir = root / "guild_7"
            target_dir.mkdir()

            with self.assertRaisesRegex(
                MemoryGuildResetError,
                "^memory_guild_reset_scan_failed$",
            ):
                reset_guild_memory_vault(7, root=root)

            self.assertTrue(target_dir.exists())
            self.assertTrue(
                memory_vault_user_note(
                    stored["noteId"], root=root
                )["ok"]
            )

    def test_guild_reset_delete_failure_never_removes_guild_directory(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                "guild-reset-delete-failure-canary-1061",
                action_id="guild-reset-action-1061",
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            target_dir = root / "guild_7"
            target_dir.mkdir()

            with patch(
                "evelyn_core.memory_vault."
                "delete_memory_vault_user_note",
                return_value={
                    "ok": False,
                    "error": "memory_delete_cleanup_required",
                },
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_delete_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            self.assertTrue(target_dir.exists())
            self.assertTrue(
                memory_vault_user_note(
                    stored["noteId"], root=root
                )["ok"]
            )

    def test_guild_reset_rechecks_scope_after_scan_before_delete(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                "guild-reset-scope-race-canary-1062",
                action_id="guild-reset-action-1062",
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            target_path = next(
                (root / "memory_vault" / "concepts").glob(
                    "user-confirmed-*.md"
                )
            )
            target_dir = root / "guild_7"
            target_dir.mkdir()
            original_scan = (
                memory_vault_module._guild_reset_confirmed_notes
            )
            scan_count = 0

            def rebind_after_first_scan(**kwargs):
                nonlocal scan_count
                result = original_scan(**kwargs)
                scan_count += 1
                if scan_count == 1:
                    raw = target_path.read_text(encoding="utf-8")
                    target_path.write_text(
                        raw.replace(
                            memory_reset_scope(7),
                            memory_reset_scope(8),
                        ),
                        encoding="utf-8",
                    )
                return result

            with patch.object(
                memory_vault_module,
                "_guild_reset_confirmed_notes",
                side_effect=rebind_after_first_scan,
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_delete_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            self.assertTrue(target_path.exists())
            self.assertTrue(target_dir.exists())
            self.assertFalse(
                memory_note_was_deleted(
                    stored["noteId"],
                    root=root,
                )
            )

    def test_guild_reset_linearizes_against_concurrent_confirmed_write(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store_explicit_memory_confirmation(
                "guild-reset-linearized-old-canary-1071",
                action_id="guild-reset-action-1071",
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            entered = threading.Event()
            release = threading.Event()
            reset_errors: list[BaseException] = []
            original_scan = (
                memory_vault_module._guild_reset_confirmed_notes
            )
            scan_count = 0

            def paused_scan(**kwargs):
                nonlocal scan_count
                result = original_scan(**kwargs)
                scan_count += 1
                if scan_count == 1:
                    entered.set()
                    if not release.wait(timeout=5):
                        raise RuntimeError("test_release_timeout")
                return result

            def reset_worker() -> None:
                try:
                    reset_guild_memory_vault(7, root=root)
                except BaseException as exc:
                    reset_errors.append(exc)

            with patch.object(
                memory_vault_module,
                "_guild_reset_confirmed_notes",
                side_effect=paused_scan,
            ):
                worker = threading.Thread(target=reset_worker)
                worker.start()
                self.assertTrue(entered.wait(timeout=5))
                with self.assertRaises(
                    MemoryDeletionJournalBusyError
                ):
                    store_explicit_memory_confirmation(
                        "guild-reset-linearized-new-canary-1072",
                        action_id="guild-reset-action-1072-blocked",
                        source="discord-user",
                        owner_scope=self.owner_scope,
                        reset_scope=memory_reset_scope(7),
                        root=root,
                    )
                release.set()
                worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(reset_errors, [])
            retried = store_explicit_memory_confirmation(
                "guild-reset-linearized-new-canary-1072",
                action_id="guild-reset-action-1072-retry",
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            context = build_memory_vault_context(
                7,
                "guild-reset-linearized-new-canary-1072",
                owner_scope=self.owner_scope,
                root=root,
            )

        self.assertEqual(retried["state"], "stored")
        self.assertIn(
            "guild-reset-linearized-new-canary-1072",
            context,
        )

    def test_guild_reset_removes_scoped_automatic_memory_and_preserves_other_guild(
        self,
    ) -> None:
        target_canary = "guild-reset-generated-target-1081"
        other_canary = "guild-reset-generated-other-1082"

        def semantic_result(canary: str):
            return lambda _messages: {
                "notes": [
                    {
                        "type": "procedure",
                        "title": "Shared generated procedure",
                        "body": (
                            f"{canary} is durable generated memory "
                            "with enough detail for consolidation."
                        ),
                        "tags": ["generated"],
                        "links": [],
                        "importance": 0.7,
                        "confidence": "high",
                    }
                ]
            }

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daily_paths = {}
            episode_paths = {}
            semantic_paths = {}
            legacy_paths = {}
            for guild_id, canary in (
                (7, target_canary),
                (8, other_canary),
            ):
                guild_dir = root / f"guild_{guild_id}"
                guild_dir.mkdir()
                (guild_dir / "rolling_summary.txt").write_text(
                    f"legacy summary {canary}",
                    encoding="utf-8",
                )
                if guild_id == 7:
                    (guild_dir / "proactive_questions.jsonl").write_text(
                        '{"text":"safe cleanup during reset"}\n',
                        encoding="utf-8",
                    )
                daily_paths[guild_id] = append_turn_rows_to_memory_vault(
                    guild_id,
                    [
                        {
                            "role": "user",
                            "text": f"remember generated canary {canary}",
                        },
                        {
                            "role": "assistant",
                            "text": f"stored generated canary {canary}",
                        },
                    ],
                    root=root,
                )
                episode_paths[guild_id] = consolidate_daily_memory_once(
                    guild_id,
                    root=root,
                    min_chars=1,
                )
                semantic = run_semantic_memory_consolidation_once(
                    guild_id,
                    root=root,
                    sub_llm_health={"available": True},
                    llm_client=semantic_result(canary),
                    min_chars=1,
                )
                semantic_paths[guild_id] = Path(
                    semantic["created_notes"][0]
                )
                mirror = refresh_legacy_memory_mirror(
                    guild_id,
                    root=root,
                )
                nodes = refresh_legacy_memory_node_notes(
                    guild_id,
                    root=root,
                )
                legacy_paths[guild_id] = [mirror, *nodes]

            target_episode_id = parse_memory_note(
                episode_paths[7]
            ).note_id
            nested_child = write_memory_vault_note(
                note_type="procedure",
                title="Nested target derivation",
                body=(
                    f"{target_canary} nested child must be deleted "
                    "before its derived parent source."
                ),
                storage_key="guild-7-nested-child-1081",
                source="memory-recomposition",
                reset_scope=memory_reset_scope(7),
                derived_from=[target_episode_id],
                root=root,
            )
            target_paths = [
                daily_paths[7],
                episode_paths[7],
                semantic_paths[7],
                nested_child,
                *legacy_paths[7],
            ]
            other_paths = [
                daily_paths[8],
                episode_paths[8],
                semantic_paths[8],
                *legacy_paths[8],
            ]
            legacy_unscoped_path = legacy_paths[7][-1]
            assert legacy_unscoped_path is not None
            legacy_unscoped_path.write_text(
                "\n".join(
                    line
                    for line in legacy_unscoped_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if not line.startswith("reset_scope:")
                )
                + "\n",
                encoding="utf-8",
            )
            tombstoned_target_paths = [
                path
                for path in target_paths
                if path != legacy_unscoped_path
            ]
            self.assertNotEqual(daily_paths[7], daily_paths[8])
            self.assertNotEqual(episode_paths[7], episode_paths[8])
            self.assertNotEqual(semantic_paths[7], semantic_paths[8])
            self.assertTrue(all(path is not None for path in target_paths))
            self.assertTrue(all(path is not None for path in other_paths))
            target_ids = [
                parse_memory_note(path).note_id
                for path in tombstoned_target_paths
                if path is not None
            ]
            legacy_unscoped_id = parse_memory_note(
                legacy_unscoped_path
            ).note_id
            other_ids = [
                parse_memory_note(path).note_id
                for path in other_paths
                if path is not None
            ]

            result = reset_guild_memory_vault(7, root=root)

            self.assertEqual(
                result["deletedNoteCount"],
                len(target_ids),
            )
            self.assertTrue(
                all(not path.exists() for path in target_paths)
            )
            self.assertTrue(
                all(
                    memory_note_was_deleted(note_id, root=root)
                    for note_id in target_ids
                )
            )
            self.assertFalse(
                memory_note_was_deleted(
                    legacy_unscoped_id,
                    root=root,
                )
            )
            self.assertTrue(all(path.exists() for path in other_paths))
            self.assertTrue(
                all(
                    not memory_note_was_deleted(note_id, root=root)
                    for note_id in other_ids
                )
            )
            self.assertFalse((root / "guild_7").exists())
            self.assertTrue((root / "guild_8").exists())
            consolidate_daily_memory_once(8, root=root, min_chars=1)
            remaining = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (root / "memory_vault").rglob("*.md")
            )
            self.assertNotIn(target_canary, remaining)
            self.assertIn(other_canary, remaining)

            worker = """
import json
import sys
from pathlib import Path
from unittest.mock import patch
from evelyn_core.memory_vault import build_memory_vault_context, run_memory_vault_maintenance_once
root = Path(sys.argv[1])
with patch(
    "evelyn_core.memory_vault.probe_sub_llm_dependency",
    return_value={"available": False, "fallback_mode": "test"},
):
    maintenance = run_memory_vault_maintenance_once(7, root=root)
context = build_memory_vault_context(7, sys.argv[2], root=root)
remaining = "\\n".join(
    path.read_text(encoding="utf-8")
    for path in (root / "memory_vault").rglob("*.md")
)
print(json.dumps({
    "targetInContext": sys.argv[2] in context,
    "targetOnDisk": sys.argv[2] in remaining,
    "legacyMirror": maintenance["legacy_mirror"],
    "dailyConsolidation": maintenance["daily_consolidation"],
}))
"""
            environment = dict(os.environ)
            environment["PYTHONPATH"] = os.pathsep.join(
                filter(
                    None,
                    (
                        str(RUNTIME_ROOT),
                        environment.get("PYTHONPATH", ""),
                    ),
                )
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    worker,
                    str(root),
                    target_canary,
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            restarted = json.loads(completed.stdout)

        self.assertEqual(
            restarted,
            {
                "targetInContext": False,
                "targetOnDisk": False,
                "legacyMirror": "",
                "dailyConsolidation": "",
            },
        )

    def test_guild_reset_unattributed_generated_memory_fails_before_mutation(
        self,
    ) -> None:
        for artifact_kind in ("conversation", "derived", "legacy"):
            with self.subTest(artifact_kind=artifact_kind):
                with TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    target = write_memory_vault_note(
                        note_type="concept",
                        title="Scoped reset target",
                        body="scoped-reset-target-1091",
                        storage_key="scoped-reset-target-1091",
                        source="discord-user",
                        reset_scope=memory_reset_scope(7),
                        root=root,
                    )
                    if artifact_kind == "conversation":
                        unattributed = (
                            root
                            / "memory_vault"
                            / "daily"
                            / "2026-08-17.md"
                        )
                        unattributed.parent.mkdir(
                            parents=True,
                            exist_ok=True,
                        )
                        unattributed.write_text(
                            """---
id: daily-2026-08-17
type: daily
title: Legacy shared daily
source: conversation-turn-log
source_refs: [guild:7]
---

# Legacy shared daily

legacy-shared-daily-canary-1092
""",
                            encoding="utf-8",
                        )
                    else:
                        unattributed = write_memory_vault_note(
                            note_type=(
                                "concept"
                                if artifact_kind == "derived"
                                else "legacy"
                            ),
                            title=f"Unattributed {artifact_kind}",
                            body=f"unattributed-{artifact_kind}-1092",
                            storage_key=(
                                f"unattributed-{artifact_kind}-1092"
                            ),
                            source=(
                                "daily-consolidation"
                                if artifact_kind == "derived"
                                else "legacy-memory-import"
                            ),
                            derived_from=(
                                ["missing-legacy-source"]
                                if artifact_kind == "derived"
                                else None
                            ),
                            root=root,
                        )
                    target_dir = root / "guild_7"
                    target_dir.mkdir()
                    target_id = parse_memory_note(target).note_id

                    with self.assertRaisesRegex(
                        MemoryGuildResetError,
                        "^memory_guild_reset_legacy_scope_missing$",
                    ):
                        reset_guild_memory_vault(7, root=root)

                    self.assertTrue(target.exists())
                    self.assertTrue(unattributed.exists())
                    self.assertTrue(target_dir.exists())
                    self.assertFalse(
                        memory_note_was_deleted(
                            target_id,
                            root=root,
                        )
                    )

    def test_guild_reset_rejects_generated_scope_rebinding_before_mutation(
        self,
    ) -> None:
        for rebound_artifact in (
            "daily_path",
            "daily_source_ref",
            "derived_source_ref",
            "derived_extra_source_ref",
        ):
            with self.subTest(rebound_artifact=rebound_artifact):
                with TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    daily = append_turn_rows_to_memory_vault(
                        7,
                        [
                            {
                                "role": "user",
                                "text": "scope rebind canary 1093",
                            }
                        ],
                        root=root,
                    )
                    assert daily is not None
                    episode = consolidate_daily_memory_once(
                        7,
                        root=root,
                        min_chars=1,
                    )
                    assert episode is not None
                    daily_id = parse_memory_note(daily).note_id
                    rebound_path = (
                        daily
                        if rebound_artifact.startswith("daily_")
                        else episode
                    )
                    rebound_raw = rebound_path.read_text(encoding="utf-8")
                    if rebound_artifact == "daily_source_ref":
                        rebound_raw = rebound_raw.replace(
                            "source_refs: [guild:7]",
                            "source_refs: [guild:8]",
                        )
                    elif rebound_artifact == "derived_source_ref":
                        rebound_raw = rebound_raw.replace(
                            "source_refs: [daily/guild-7/",
                            "source_refs: [daily/guild-8/",
                        )
                    elif rebound_artifact == "derived_extra_source_ref":
                        rebound_raw = "\n".join(
                            (
                                line[:-1] + ", guild:8]"
                                if line.startswith("source_refs: [")
                                else line
                            )
                            for line in rebound_raw.splitlines()
                        ) + "\n"
                    else:
                        rebound_raw = rebound_raw.replace(
                            memory_reset_scope(7),
                            memory_reset_scope(8),
                        )
                    rebound_path.write_text(rebound_raw, encoding="utf-8")

                    with self.assertRaisesRegex(
                        MemoryGuildResetError,
                        "^memory_guild_reset_legacy_scope_missing$",
                    ):
                        reset_guild_memory_vault(7, root=root)

                    self.assertTrue(daily.exists())
                    self.assertTrue(episode.exists())
                    self.assertFalse(
                        memory_note_was_deleted(
                            daily_id,
                            root=root,
                        )
                    )

    def test_guild_reset_rejects_ref_only_generated_rebinding_before_mutation(
        self,
    ) -> None:
        for artifact_kind in (
            "episode_extra_ref",
            "semantic_extra_ref",
            "legacy_mirror_ref",
            "legacy_node_ref",
        ):
            with self.subTest(artifact_kind=artifact_kind):
                with TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    daily = append_turn_rows_to_memory_vault(
                        7,
                        [
                            {
                                "role": "user",
                                "text": (
                                    "ref-only generated binding canary "
                                    f"{artifact_kind} 1094"
                                ),
                            }
                        ],
                        root=root,
                    )
                    assert daily is not None
                    daily_id = parse_memory_note(daily).note_id
                    if artifact_kind == "episode_extra_ref":
                        artifact = consolidate_daily_memory_once(
                            7,
                            root=root,
                            min_chars=1,
                        )
                        assert artifact is not None
                    elif artifact_kind == "semantic_extra_ref":
                        semantic = run_semantic_memory_consolidation_once(
                            7,
                            root=root,
                            sub_llm_health={"available": True},
                            llm_client=lambda _messages: {
                                "notes": [
                                    {
                                        "type": "procedure",
                                        "title": "Ref-only semantic",
                                        "body": (
                                            "semantic source refs must have "
                                            "one exact daily source"
                                        ),
                                    }
                                ]
                            },
                            min_chars=1,
                        )
                        artifact = Path(semantic["created_notes"][0])
                    else:
                        guild_dir = root / "guild_7"
                        guild_dir.mkdir()
                        (guild_dir / "rolling_summary.txt").write_text(
                            "legacy ref-only binding canary 1094",
                            encoding="utf-8",
                        )
                        if artifact_kind == "legacy_mirror_ref":
                            artifact = refresh_legacy_memory_mirror(
                                7,
                                root=root,
                            )
                            assert artifact is not None
                        else:
                            nodes = refresh_legacy_memory_node_notes(
                                7,
                                root=root,
                            )
                            artifact = nodes[0]
                    raw = artifact.read_text(encoding="utf-8")
                    if artifact_kind in {
                        "episode_extra_ref",
                        "semantic_extra_ref",
                    }:
                        raw = "\n".join(
                            (
                                line[:-1] + ", guild:8]"
                                if line.startswith("source_refs: [")
                                else line
                            )
                            for line in raw.splitlines()
                        ) + "\n"
                    elif artifact_kind == "legacy_mirror_ref":
                        raw = raw.replace(
                            "source_refs: [guild:7]",
                            "source_refs: [guild:8]",
                        )
                    else:
                        raw = raw.replace("/guild_7/", "/guild_8/")
                    artifact.write_text(raw, encoding="utf-8")

                    with self.assertRaisesRegex(
                        MemoryGuildResetError,
                        "^memory_guild_reset_legacy_scope_missing$",
                    ):
                        reset_guild_memory_vault(7, root=root)

                    self.assertTrue(daily.exists())
                    self.assertTrue(artifact.exists())
                    self.assertFalse(
                        memory_note_was_deleted(daily_id, root=root)
                    )

    def test_guild_reset_rejects_cross_scope_derived_edge_before_mutation(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daily_7 = append_turn_rows_to_memory_vault(
                7,
                [{"role": "user", "text": "cross scope source 1095"}],
                root=root,
            )
            daily_8 = append_turn_rows_to_memory_vault(
                8,
                [{"role": "user", "text": "other guild source 1096"}],
                root=root,
            )
            assert daily_7 is not None
            assert daily_8 is not None
            daily_7_id = parse_memory_note(daily_7).note_id
            cross_scope_child = write_memory_vault_note(
                note_type="procedure",
                title="Cross-scope derived child",
                body=(
                    "guild 8 child must not derive from guild 7 "
                    "automatic memory source"
                ),
                storage_key="guild-8-cross-scope-child-1095",
                source="memory-recomposition",
                reset_scope=memory_reset_scope(8),
                derived_from=[daily_7_id],
                root=root,
            )

            with self.assertRaisesRegex(
                MemoryGuildResetError,
                "^memory_guild_reset_legacy_scope_missing$",
            ):
                reset_guild_memory_vault(7, root=root)

            self.assertTrue(daily_7.exists())
            self.assertTrue(daily_8.exists())
            self.assertTrue(cross_scope_child.exists())
            self.assertFalse(
                memory_note_was_deleted(daily_7_id, root=root)
            )

    def test_guild_reset_rejects_cross_scope_cascade_after_preflight(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daily_7 = append_turn_rows_to_memory_vault(
                7,
                [{"role": "user", "text": "race source guild 7 1097"}],
                root=root,
            )
            daily_8 = append_turn_rows_to_memory_vault(
                8,
                [{"role": "user", "text": "race source guild 8 1098"}],
                root=root,
            )
            assert daily_7 is not None
            assert daily_8 is not None
            daily_7_id = parse_memory_note(daily_7).note_id
            daily_8_id = parse_memory_note(daily_8).note_id
            other_child = write_memory_vault_note(
                note_type="procedure",
                title="Other guild race child",
                body="other guild child must survive reset race",
                storage_key="guild-8-race-child-1097",
                source="memory-recomposition",
                reset_scope=memory_reset_scope(8),
                derived_from=[daily_8_id],
                root=root,
            )
            other_child_id = parse_memory_note(other_child).note_id
            original_preview = (
                memory_vault_module.
                preview_memory_vault_user_note_deletion
            )
            mutated = False

            def rebind_before_preview(*args, **kwargs):
                nonlocal mutated
                if not mutated:
                    other_child.write_text(
                        other_child.read_text(
                            encoding="utf-8"
                        ).replace(daily_8_id, daily_7_id),
                        encoding="utf-8",
                    )
                    mutated = True
                return original_preview(*args, **kwargs)

            with patch.object(
                memory_vault_module,
                "preview_memory_vault_user_note_deletion",
                side_effect=rebind_before_preview,
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_delete_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            self.assertTrue(daily_7.exists())
            self.assertTrue(daily_8.exists())
            self.assertTrue(other_child.exists())
            self.assertFalse(
                memory_note_was_deleted(daily_7_id, root=root)
            )
            self.assertFalse(
                memory_note_was_deleted(other_child_id, root=root)
            )

    def test_guild_reset_rechecks_target_attribution_after_preflight(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daily = append_turn_rows_to_memory_vault(
                7,
                [
                    {
                        "role": "user",
                        "text": "target attribution race canary 1099",
                    }
                ],
                root=root,
            )
            assert daily is not None
            daily_id = parse_memory_note(daily).note_id
            original_preview = (
                memory_vault_module.
                preview_memory_vault_user_note_deletion
            )
            mutated = False

            def rebind_before_preview(*args, **kwargs):
                nonlocal mutated
                if not mutated:
                    daily.write_text(
                        daily.read_text(encoding="utf-8").replace(
                            "source_refs: [guild:7]",
                            "source_refs: [guild:8]",
                        ),
                        encoding="utf-8",
                    )
                    mutated = True
                return original_preview(*args, **kwargs)

            with patch.object(
                memory_vault_module,
                "preview_memory_vault_user_note_deletion",
                side_effect=rebind_before_preview,
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_delete_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            self.assertTrue(daily.exists())
            self.assertFalse(
                memory_note_was_deleted(daily_id, root=root)
            )

    def test_guild_reset_rejects_unrecognized_legacy_tree_content_before_mutation(
        self,
    ) -> None:
        for artifact_kind in ("local_note", "unknown_file"):
            with self.subTest(artifact_kind=artifact_kind):
                with TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    target = store_explicit_memory_confirmation(
                        f"legacy-tree-target-{artifact_kind}-1100",
                        action_id=f"legacy-tree-target-{artifact_kind}-1100",
                        source="discord-user",
                        owner_scope=self.owner_scope,
                        reset_scope=memory_reset_scope(7),
                        root=root,
                    )
                    legacy_dir = (
                        root / "memory_vault" / "legacy" / "guild-7"
                    )
                    legacy_dir.mkdir(parents=True)
                    if artifact_kind == "local_note":
                        local_owner = memory_owner_scope(
                            guild_id=None,
                            person_key="control-page:local",
                        )
                        local = store_explicit_memory_confirmation(
                            "legacy-tree-local-canary-1100",
                            action_id="legacy-tree-local-1100",
                            source="control-page-user",
                            owner_scope=local_owner,
                            root=root,
                        )
                        local_source = next(
                            path
                            for path in (
                                root / "memory_vault" / "concepts"
                            ).glob("user-confirmed-*.md")
                            if local["noteId"]
                            in path.read_text(encoding="utf-8")
                        )
                        artifact = legacy_dir / "local.md"
                        local_source.replace(artifact)
                    else:
                        artifact = legacy_dir / "unknown.bin"
                        artifact.write_bytes(b"must not be raw-deleted")

                    with self.assertRaisesRegex(
                        MemoryGuildResetError,
                        "^memory_guild_reset_legacy_scope_missing$",
                    ):
                        reset_guild_memory_vault(7, root=root)

                    self.assertTrue(artifact.exists())
                    self.assertTrue(
                        memory_vault_user_note(
                            target["noteId"], root=root
                        )["ok"]
                    )
                    self.assertFalse(
                        memory_note_was_deleted(
                            target["noteId"], root=root
                        )
                    )

    def test_guild_reset_stops_cross_scope_cascade_inside_target_delete(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daily_7 = append_turn_rows_to_memory_vault(
                7,
                [{"role": "user", "text": "delete race guild 7 1100"}],
                root=root,
            )
            daily_8 = append_turn_rows_to_memory_vault(
                8,
                [{"role": "user", "text": "delete race guild 8 1100"}],
                root=root,
            )
            assert daily_7 is not None
            assert daily_8 is not None
            daily_7_id = parse_memory_note(daily_7).note_id
            daily_8_id = parse_memory_note(daily_8).note_id
            other_child = write_memory_vault_note(
                note_type="procedure",
                title="Other guild delete-race child",
                body="other guild delete-race child must survive",
                storage_key="other-guild-delete-race-child-1100",
                source="memory-recomposition",
                reset_scope=memory_reset_scope(8),
                derived_from=[daily_8_id],
                root=root,
            )
            other_child_id = parse_memory_note(other_child).note_id
            original_append = (
                memory_vault_module._append_memory_deletion_tombstone
            )
            mutated = False

            def rebind_after_tombstone(*args, **kwargs):
                nonlocal mutated
                tombstone = original_append(*args, **kwargs)
                payload = args[0] if args else {}
                if (
                    not mutated
                    and payload.get("noteId") == daily_7_id
                ):
                    other_child.write_text(
                        other_child.read_text(encoding="utf-8").replace(
                            daily_8_id,
                            daily_7_id,
                        ),
                        encoding="utf-8",
                    )
                    mutated = True
                return tombstone

            with patch.object(
                memory_vault_module,
                "_append_memory_deletion_tombstone",
                side_effect=rebind_after_tombstone,
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_delete_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            self.assertTrue(daily_7.exists())
            self.assertTrue(memory_note_was_deleted(daily_7_id, root=root))
            self.assertTrue(other_child.exists())
            self.assertFalse(
                memory_note_was_deleted(other_child_id, root=root)
            )

            other_child.write_text(
                other_child.read_text(encoding="utf-8").replace(
                    daily_7_id,
                    daily_8_id,
                ),
                encoding="utf-8",
            )
            retried = reset_guild_memory_vault(7, root=root)

            self.assertEqual(retried["state"], "reset")
            self.assertFalse(daily_7.exists())
            self.assertTrue(daily_8.exists())
            self.assertTrue(other_child.exists())
            self.assertFalse(
                memory_note_was_deleted(other_child_id, root=root)
            )

    def test_guild_reset_rechecks_graph_after_target_delete_returns(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daily_7 = append_turn_rows_to_memory_vault(
                7,
                [{"role": "user", "text": "post-delete guild 7 1100"}],
                root=root,
            )
            daily_8 = append_turn_rows_to_memory_vault(
                8,
                [{"role": "user", "text": "post-delete guild 8 1100"}],
                root=root,
            )
            assert daily_7 is not None
            assert daily_8 is not None
            daily_7_id = parse_memory_note(daily_7).note_id
            daily_8_id = parse_memory_note(daily_8).note_id
            other_child = write_memory_vault_note(
                note_type="procedure",
                title="Other guild post-delete child",
                body="other guild post-delete child must survive",
                storage_key="other-guild-post-delete-child-1100",
                source="memory-recomposition",
                reset_scope=memory_reset_scope(8),
                derived_from=[daily_8_id],
                root=root,
            )
            other_child_id = parse_memory_note(other_child).note_id
            original_delete = (
                memory_vault_module.delete_memory_vault_user_note
            )
            mutated = False

            def rebind_after_delete(*args, **kwargs):
                nonlocal mutated
                result = original_delete(*args, **kwargs)
                if not mutated and result.get("ok") is True:
                    other_child.write_text(
                        other_child.read_text(encoding="utf-8").replace(
                            daily_8_id,
                            daily_7_id,
                        ),
                        encoding="utf-8",
                    )
                    mutated = True
                return result

            with patch.object(
                memory_vault_module,
                "delete_memory_vault_user_note",
                side_effect=rebind_after_delete,
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_verification_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            self.assertFalse(daily_7.exists())
            self.assertTrue(memory_note_was_deleted(daily_7_id, root=root))
            self.assertTrue(other_child.exists())
            self.assertFalse(
                memory_note_was_deleted(other_child_id, root=root)
            )

    def test_guild_reset_rejects_target_injected_after_remaining_scan(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir, TemporaryDirectory() as seed_dir:
            root = Path(temp_dir)
            target = store_explicit_memory_confirmation(
                "late-reset-old-target-1100",
                action_id="late-reset-old-target-1100",
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            late_canary = "late-reset-injected-target-1100"
            late = store_explicit_memory_confirmation(
                late_canary,
                action_id="late-reset-injected-target-1100",
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=Path(seed_dir),
            )
            late_source = next(
                path
                for path in (
                    Path(seed_dir) / "memory_vault" / "concepts"
                ).glob("user-confirmed-*.md")
                if late["noteId"] in path.read_text(encoding="utf-8")
            )
            late_bytes = late_source.read_bytes()
            late_path = (
                root
                / "memory_vault"
                / "concepts"
                / "late-reset-injected-target-1100.md"
            )
            original_remove = (
                memory_vault_module._remove_guild_legacy_copies
            )

            def inject_before_legacy_cleanup(*args, **kwargs):
                late_path.write_bytes(late_bytes)
                return original_remove(*args, **kwargs)

            with patch.object(
                memory_vault_module,
                "_remove_guild_legacy_copies",
                side_effect=inject_before_legacy_cleanup,
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_verification_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            self.assertTrue(
                memory_note_was_deleted(target["noteId"], root=root)
            )
            self.assertTrue(late_path.exists())
            self.assertFalse(
                memory_note_was_deleted(late["noteId"], root=root)
            )

    def test_guild_reset_legacy_tree_swap_preserves_other_guild(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for guild_id, canary in (
                (7, "legacy-swap-target-1101"),
                (8, "legacy-swap-other-1102"),
            ):
                guild_dir = root / f"guild_{guild_id}"
                guild_dir.mkdir()
                (guild_dir / "rolling_summary.txt").write_text(
                    canary,
                    encoding="utf-8",
                )
            target_nodes = refresh_legacy_memory_node_notes(
                7,
                root=root,
            )
            other_nodes = refresh_legacy_memory_node_notes(
                8,
                root=root,
            )
            self.assertTrue(target_nodes)
            self.assertTrue(other_nodes)
            other_node_id = parse_memory_note(other_nodes[0]).note_id
            target_tree = root / "memory_vault" / "legacy" / "guild-7"
            other_tree = root / "memory_vault" / "legacy" / "guild-8"
            staged_target = (
                root / "memory_vault" / "legacy" / "guild-7-original"
            )
            original_remove = (
                memory_vault_module._remove_guild_legacy_copies
            )

            def swap_before_remove(*args, **kwargs):
                target_tree.rename(staged_target)
                other_tree.rename(target_tree)
                return original_remove(*args, **kwargs)

            with patch.object(
                memory_vault_module,
                "_remove_guild_legacy_copies",
                side_effect=swap_before_remove,
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_directory_delete_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            remaining = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (root / "memory_vault" / "legacy").rglob(
                    "*.md"
                )
            )
            self.assertIn("legacy-swap-other-1102", remaining)
            self.assertFalse(
                memory_note_was_deleted(other_node_id, root=root)
            )

    def test_guild_reset_raw_tree_swap_preserves_other_guild(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "guild_7"
            other_dir = root / "guild_8"
            target_dir.mkdir()
            other_dir.mkdir()
            identical_canary = "raw-swap-identical-1104"
            (target_dir / "rolling_summary.txt").write_text(
                identical_canary,
                encoding="utf-8",
            )
            (other_dir / "rolling_summary.txt").write_text(
                identical_canary,
                encoding="utf-8",
            )
            target_identity = target_dir.stat().st_ino
            other_identity = other_dir.stat().st_ino
            staged_target = root / "guild_7_original"
            original_remove = (
                memory_vault_module._remove_guild_memory_directory
            )

            def swap_before_remove(*args, **kwargs):
                target_dir.rename(staged_target)
                other_dir.rename(target_dir)
                return original_remove(*args, **kwargs)

            with patch.object(
                memory_vault_module,
                "_remove_guild_memory_directory",
                side_effect=swap_before_remove,
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_directory_delete_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            self.assertEqual(staged_target.stat().st_ino, target_identity)
            self.assertEqual(target_dir.stat().st_ino, other_identity)
            self.assertEqual(
                (target_dir / "rolling_summary.txt").read_text(
                    encoding="utf-8"
                ),
                identical_canary,
            )

    def test_guild_reset_raw_tree_swap_after_snapshot_preserves_other_guild(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "guild_7"
            other_dir = root / "guild_8"
            target_dir.mkdir()
            other_dir.mkdir()
            for directory in (target_dir, other_dir):
                (directory / "same.txt").write_text(
                    "identical raw bytes 1105",
                    encoding="utf-8",
                )
            target_identity = target_dir.stat().st_ino
            other_identity = other_dir.stat().st_ino
            staged_target = root / "guild_7_original"
            original_snapshot = (
                memory_vault_module._guild_reset_path_snapshot
            )
            target_snapshot_count = 0

            def swap_after_snapshot(path, **kwargs):
                nonlocal target_snapshot_count
                snapshot = original_snapshot(path, **kwargs)
                if path == target_dir:
                    target_snapshot_count += 1
                    if target_snapshot_count == 3:
                        target_dir.rename(staged_target)
                        other_dir.rename(target_dir)
                return snapshot

            with patch.object(
                memory_vault_module,
                "_guild_reset_path_snapshot",
                side_effect=swap_after_snapshot,
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_directory_delete_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            self.assertEqual(target_snapshot_count, 3)
            self.assertEqual(staged_target.stat().st_ino, target_identity)
            self.assertEqual(target_dir.stat().st_ino, other_identity)
            self.assertTrue((target_dir / "same.txt").exists())

    def test_guild_reset_raw_tree_injection_fails_before_unlink(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "guild_7"
            target_dir.mkdir()
            original_file = target_dir / "rolling_summary.txt"
            original_file.write_text(
                "raw injection original 1106",
                encoding="utf-8",
            )
            injected_file = target_dir / "injected.txt"
            original_snapshot = (
                memory_vault_module._guild_reset_path_snapshot
            )
            target_snapshot_count = 0

            def inject_before_final_snapshot(path, **kwargs):
                nonlocal target_snapshot_count
                if path == target_dir:
                    target_snapshot_count += 1
                    if target_snapshot_count == 2:
                        injected_file.write_text(
                            "must not be absorbed into reset deletion",
                            encoding="utf-8",
                        )
                return original_snapshot(path, **kwargs)

            with patch.object(
                memory_vault_module,
                "_guild_reset_path_snapshot",
                side_effect=inject_before_final_snapshot,
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_directory_delete_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            self.assertEqual(target_snapshot_count, 2)
            self.assertTrue(original_file.exists())
            self.assertTrue(injected_file.exists())

    def test_guild_reset_rechecks_generated_hash_after_preview(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daily = append_turn_rows_to_memory_vault(
                7,
                [{"role": "user", "text": "preview race canary 1094"}],
                root=root,
            )
            assert daily is not None
            daily_id = parse_memory_note(daily).note_id
            original_preview = (
                memory_vault_module.
                preview_memory_vault_user_note_deletion
            )

            def mutate_after_preview(*args, **kwargs):
                preview = original_preview(*args, **kwargs)
                daily.write_text(
                    daily.read_text(encoding="utf-8")
                    + "\npost-preview mutation\n",
                    encoding="utf-8",
                )
                return preview

            with patch.object(
                memory_vault_module,
                "preview_memory_vault_user_note_deletion",
                side_effect=mutate_after_preview,
            ):
                with self.assertRaisesRegex(
                    MemoryGuildResetError,
                    "^memory_guild_reset_delete_failed$",
                ):
                    reset_guild_memory_vault(7, root=root)

            self.assertTrue(daily.exists())
            self.assertFalse(
                memory_note_was_deleted(daily_id, root=root)
            )

    def test_owner_scope_isolates_recall_cache_and_legacy_notes(
        self,
    ) -> None:
        canary = "evelyn-owner-scope-canary-991"
        owner_b = memory_owner_scope(
            guild_id=7,
            person_key="user:12",
        )
        local_owner = memory_owner_scope(
            guild_id=None,
            person_key="control-page:local",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                f"개인 기억 표식은 {canary}",
                action_id="owner-scope-action-991",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            other_first_context = build_memory_vault_context(
                7,
                "개인 기억 표식",
                owner_scope=owner_b,
                root=root,
            )
            owner_after_empty_cache = build_memory_vault_context(
                7,
                "개인 기억 표식",
                owner_scope=self.owner_scope,
                root=root,
            )
            owner_receipt: dict = {}
            owner_context = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                root=root,
                receipt=owner_receipt,
            )
            for other_scope in (owner_b, local_owner):
                other_receipt: dict = {}
                other_context = build_memory_vault_context(
                    7,
                    canary,
                    owner_scope=other_scope,
                    root=root,
                    receipt=other_receipt,
                )
                self.assertNotIn(canary, other_context)
                self.assertNotIn(
                    stored["noteId"],
                    other_receipt["suppliedNoteIds"],
                )
            owner_after_other_cache = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                root=root,
            )
            detail = memory_vault_user_note(
                stored["noteId"],
                root=root,
            )
            path = next(
                (root / "memory_vault" / "concepts").glob(
                    "user-confirmed-*.md"
                )
            )
            raw = path.read_text(encoding="utf-8")
            path.write_text(
                "\n".join(
                    line
                    for line in raw.splitlines()
                    if not line.startswith("owner_scope:")
                )
                + "\n",
                encoding="utf-8",
            )
            legacy_context = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                root=root,
            )

        self.assertIn(canary, owner_context)
        self.assertNotIn(canary, other_first_context)
        self.assertIn(canary, owner_after_empty_cache)
        self.assertIn(
            stored["noteId"],
            owner_receipt["suppliedNoteIds"],
        )
        self.assertIn(canary, owner_after_other_cache)
        self.assertNotIn(self.owner_scope, str(stored))
        self.assertNotIn(self.owner_scope, str(detail))
        self.assertNotIn(canary, legacy_context)

    def test_renamed_marker_only_v1_note_is_not_recalled(self) -> None:
        fact = "legacy-marker-only-canary-991"
        other_owner = memory_owner_scope(
            guild_id=7,
            person_key="user:99",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_memory_vault_note(
                note_type="concept",
                title="Renamed Legacy Memory",
                body=fact,
                storage_key="renamed-memory",
                source="control-page-user",
                source_refs=["turn:legacy-v1:user"],
                evidence_hashes=[
                    hashlib.sha256(fact.encode("utf-8")).hexdigest()
                ],
                confirmed_at="2026-07-31T00:00:00+00:00",
                memory_contract="memory.user-confirmation.note.v1",
                root=root,
            )
            context = build_memory_vault_context(
                7,
                fact,
                owner_scope=other_owner,
                root=root,
            )

        self.assertNotIn(fact, context)

    def test_confirmed_memory_is_attributed_then_disappears_after_delete(self) -> None:
        canary = "evelyn-canary-orchid-731"
        fact = f"내가 좋아하는 암호명은 {canary}이야"
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                fact,
                action_id="discord-message:7:8:900",
                evidence_turn_id="discord-turn-memory-900",
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            before_receipt: dict = {}
            before_context = build_memory_vault_context(
                7,
                canary,
                source="discord-text",
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=before_receipt,
            )
            grounding_state = validated_memory_grounding_state(
                before_receipt,
                has_context=bool(before_context),
            )
            before_boundary = prepare_memory_context_for_prompt(
                before_context,
                grounding_state=grounding_state,
            )
            reconcile_memory_receipt_for_prompt(
                before_receipt,
                before_boundary,
            )

            preview = preview_memory_vault_user_note_deletion(
                stored["noteId"],
                reason="privacy_request",
                root=root,
                now=lambda: 100.0,
            )
            deleted = delete_memory_vault_user_note(
                stored["noteId"],
                preview["confirmToken"],
                reason="privacy_request",
                root=root,
                now=lambda: 101.0,
            )
            after_receipt: dict = {}
            after_context = build_memory_vault_context(
                7,
                canary,
                source="discord-text",
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=after_receipt,
            )
            tombstone = (
                root
                / "memory_index"
                / "memory_deletions.jsonl"
            ).read_text(encoding="utf-8")

        self.assertEqual(stored["state"], "stored")
        self.assertEqual(before_receipt["groundingState"], "attributed")
        self.assertIn(stored["noteId"], before_receipt["suppliedNoteIds"])
        self.assertEqual(before_receipt["sourceTypeCounts"]["user"], 1)
        self.assertIn(fact, before_boundary.context)
        self.assertNotIn("MEMORY_CONFIRMATION_RULE:", before_boundary.context)
        self.assertTrue(preview["ok"])
        self.assertNotIn(canary, str(preview))
        self.assertTrue(deleted["ok"])
        self.assertNotIn(canary, str(deleted))
        self.assertGreater(
            deleted["memoryVersion"],
            before_receipt["memoryVersion"],
        )
        self.assertNotIn(stored["noteId"], after_receipt["suppliedNoteIds"])
        self.assertNotIn(fact, after_context)
        self.assertNotIn(canary, tombstone)

    def test_damaged_confirmed_memory_is_evicted_from_cached_recall(self) -> None:
        canary = "evelyn-damaged-memory-842"
        fact = f"손상 차단 표식은 {canary}"
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                fact,
                action_id="control-damaged-memory-842",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            warm_receipt: dict = {}
            build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=warm_receipt,
            )
            cached_receipt: dict = {}
            cached_context = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=cached_receipt,
            )
            path = next(
                (root / "memory_vault" / "concepts").glob(
                    "user-confirmed-*.md"
                )
            )
            lines = path.read_text(encoding="utf-8").splitlines()
            path.write_text(
                "\n".join(
                    "evidence_hashes: []"
                    if line.startswith("evidence_hashes:")
                    else line
                    for line in lines
                )
                + "\n",
                encoding="utf-8",
            )

            blocked_receipt: dict = {}
            blocked_context = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=blocked_receipt,
            )
            detail = memory_vault_user_note(
                stored["noteId"],
                root=root,
            )
            snapshot = memory_vault_user_snapshot(root=root)

        self.assertIn(fact, cached_context)
        self.assertTrue(cached_receipt["cacheHit"])
        self.assertNotIn(fact, blocked_context)
        self.assertNotIn(
            stored["noteId"],
            blocked_receipt["suppliedNoteIds"],
        )
        self.assertGreater(
            blocked_receipt["memoryVersion"],
            cached_receipt["memoryVersion"],
        )
        self.assertEqual(
            detail["card"]["userConfirmationIntegrity"],
            "invalid",
        )
        self.assertFalse(detail["card"]["recallEligible"])
        self.assertFalse(detail["card"]["canConfirm"])
        self.assertTrue(detail["card"]["canEdit"])
        self.assertEqual(
            detail["card"]["recallBlockedReason"],
            "user_confirmation_integrity_invalid",
        )
        self.assertEqual(snapshot["counts"]["integrityInvalid"], 1)

    def test_legacy_confirmed_memory_without_contract_marker_still_recalls(self) -> None:
        canary = "evelyn-legacy-confirmed-memory-854"
        fact = f"기존 확인 기억 표식은 {canary}"
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                fact,
                action_id="control-legacy-memory-854",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            path = next(
                (root / "memory_vault" / "concepts").glob(
                    "user-confirmed-*.md"
                )
            )
            raw = path.read_text(encoding="utf-8")
            path.write_text(
                "\n".join(
                    line
                    for line in raw.splitlines()
                    if not line.startswith("memory_contract:")
                )
                + "\n",
                encoding="utf-8",
            )

            receipt: dict = {}
            context = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=receipt,
            )
            detail = memory_vault_user_note(
                stored["noteId"],
                root=root,
            )

        self.assertIn(fact, context)
        self.assertIn(stored["noteId"], receipt["suppliedNoteIds"])
        self.assertEqual(
            detail["card"]["userConfirmationIntegrity"],
            "verified",
        )
        self.assertTrue(detail["card"]["recallEligible"])

    def test_user_edit_rebinds_confirmed_memory_integrity(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                "수정 전 표식 old-confirmed-memory-913",
                action_id="control-edit-memory-913",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(7),
                root=root,
            )
            before = memory_vault_user_note(
                stored["noteId"],
                root=root,
            )
            edited = update_memory_vault_user_note(
                stored["noteId"],
                "edit",
                title="사용자가 수정한 확인 기억",
                body=(
                    "수정 후 표식 new-confirmed-memory-913\n"
                    "두 번째 줄도 같은 사용자 수정 근거야"
                ),
                expected_content_hash=before["card"]["sourceHash"],
                root=root,
            )
            after = memory_vault_user_note(
                stored["noteId"],
                root=root,
            )
            receipt: dict = {}
            context = build_memory_vault_context(
                7,
                "new-confirmed-memory-913",
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=receipt,
            )

        self.assertTrue(edited["ok"])
        self.assertEqual(
            after["card"]["userConfirmationIntegrity"],
            "verified",
        )
        self.assertTrue(after["card"]["recallEligible"])
        self.assertEqual(
            after["card"]["provenance"]["source"],
            "user-edit",
        )
        self.assertIn(stored["noteId"], receipt["suppliedNoteIds"])
        self.assertIn("new-confirmed-memory-913", context)
        self.assertIn("두 번째 줄도 같은 사용자 수정 근거야", context)
        self.assertNotIn("old-confirmed-memory-913", context)


if __name__ == "__main__":
    unittest.main()
