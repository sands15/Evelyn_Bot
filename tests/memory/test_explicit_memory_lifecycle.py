from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


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
from evelyn_core.memory_prompt_policy import (  # noqa: E402
    prepare_memory_context_for_prompt,
    reconcile_memory_receipt_for_prompt,
    validated_memory_grounding_state,
)
from evelyn_core.memory_vault import (  # noqa: E402
    build_memory_vault_context,
    delete_memory_vault_user_note,
    preview_memory_vault_user_note_deletion,
)


class ExplicitMemoryLifecycleTests(unittest.TestCase):
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
                root=root,
            )
            before_receipt: dict = {}
            before_context = build_memory_vault_context(
                7,
                canary,
                source="discord-text",
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


if __name__ == "__main__":
    unittest.main()
