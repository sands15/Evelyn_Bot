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
    ExplicitMemoryConfirmationError,
    parse_explicit_memory_confirmation,
    store_explicit_memory_confirmation,
)
from evelyn_core.memory_vault import memory_vault_user_note  # noqa: E402


class ExplicitMemoryConfirmationTests(unittest.TestCase):
    def test_parser_accepts_only_explicit_commands(self) -> None:
        self.assertEqual(
            parse_explicit_memory_confirmation(
                "/remember 나는 산책을 좋아해"
            ),
            "나는 산책을 좋아해",
        )
        self.assertEqual(
            parse_explicit_memory_confirmation(
                "기억해 줘: 나는 아침 커피를 좋아해"
            ),
            "나는 아침 커피를 좋아해",
        )
        self.assertIsNone(
            parse_explicit_memory_confirmation(
                "언젠가 이 이야기를 기억해 줬으면 좋겠어"
            )
        )
        with self.assertRaises(ExplicitMemoryConfirmationError) as caught:
            parse_explicit_memory_confirmation("/remember")
        self.assertEqual(
            caught.exception.code,
            "memory_confirmation_text_required",
        )

    def test_store_is_grounded_confirmed_idempotent_and_content_free(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = store_explicit_memory_confirmation(
                "나는 산책을 좋아해",
                action_id="control-request-123",
                root=root,
            )
            second = store_explicit_memory_confirmation(
                "나는 산책을 좋아해",
                action_id="control-request-123",
                root=root,
            )
            note = memory_vault_user_note(
                first["noteId"],
                root=root,
            )
            markdown_files = list(
                (root / "memory_vault" / "concepts").glob("*.md")
            )
            raw = markdown_files[0].read_text(encoding="utf-8")

        self.assertEqual(first["state"], "stored")
        self.assertEqual(second["state"], "duplicate")
        self.assertEqual(first["noteId"], second["noteId"])
        self.assertTrue(first["contentFree"])
        self.assertNotIn("산책", str(first))
        self.assertTrue(note["ok"])
        self.assertEqual(note["card"]["body"], "나는 산책을 좋아해")
        self.assertTrue(note["card"]["confirmed"])
        self.assertEqual(len(markdown_files), 1)
        self.assertIn("source: control-page-user", raw)
        self.assertIn(
            "source_refs: [turn:control-request-123:user]",
            raw,
        )
        self.assertIn("confirmed_at:", raw)
        self.assertIn("evidence_hashes:", raw)

    def test_invalid_action_id_is_not_written_to_source_ref(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receipt = store_explicit_memory_confirmation(
                "사용자 확인 사실",
                action_id="../../private path",
                root=root,
            )
            raw = next(
                (root / "memory_vault" / "concepts").glob("*.md")
            ).read_text(encoding="utf-8")

        self.assertNotIn("private path", raw)
        self.assertNotIn("private path", str(receipt))
        self.assertRegex(
            receipt["sourceRef"],
            r"^turn:[0-9a-f]{32}:user$",
        )

    def test_same_action_cannot_be_reused_for_different_content(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store_explicit_memory_confirmation(
                "첫 번째 사실",
                action_id="same-action-123",
                root=root,
            )
            with self.assertRaises(
                ExplicitMemoryConfirmationError
            ) as caught:
                store_explicit_memory_confirmation(
                    "다른 사실",
                    action_id="same-action-123",
                    root=root,
                )

        self.assertEqual(
            caught.exception.code,
            "memory_confirmation_hash_collision",
        )


if __name__ == "__main__":
    unittest.main()
