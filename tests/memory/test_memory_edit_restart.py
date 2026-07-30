from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import memory_vault  # noqa: E402


RESTART_READER = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    from evelyn_core import memory_vault
    from evelyn_core.assistant_contracts import MemoryRecallRequest

    root = Path(sys.argv[1])
    note_id = sys.argv[2]
    detail = memory_vault.memory_vault_user_note(
        note_id,
        root=root,
    )
    recall = memory_vault.recall_memory_vault(
        MemoryRecallRequest(
            turn_id="memory-edit-restart",
            session_key="restart",
            guild_id=None,
            user_text="restart corrected memory",
            topic_id=None,
            source="test",
            max_items=2,
        ),
        root=root,
    )
    print(json.dumps({
        "detail": detail,
        "recallContext": recall.context_text,
        "recallProvenance": recall.metadata.get("provenance", []),
    }, ensure_ascii=False))
    """
)


class MemoryEditRestartTests(unittest.TestCase):
    def subprocess_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item
            for item in (str(RUNTIME_ROOT), existing)
            if item
        )
        return environment

    def test_user_edit_provenance_survives_fresh_process_recall(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = memory_vault.write_memory_vault_note(
                note_type="concept",
                title="Restart Corrected Memory",
                body="generated value before correction",
                source=(
                    "legacy-sub-llm-semantic-consolidation"
                ),
                source_refs=["daily/restart-source"],
                evidence_hashes=["generated-evidence"],
                root=root,
            )
            original = memory_vault.parse_memory_note(path)
            edited = memory_vault.update_memory_vault_user_note(
                original.note_id,
                "edit",
                title="Restart Corrected Memory",
                body="restart corrected memory from the user",
                expected_content_hash=original.source_hash,
                root=root,
            )
            restarted = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    RESTART_READER,
                    str(root),
                    original.note_id,
                ],
                cwd=REPO_ROOT,
                env=self.subprocess_environment(),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                restarted.returncode,
                0,
                restarted.stderr + restarted.stdout,
            )
            result = json.loads(restarted.stdout)

        self.assertTrue(edited["ok"])
        card = result["detail"]["card"]
        self.assertIn(
            "restart corrected memory from the user",
            card["body"],
        )
        self.assertEqual(
            card["provenance"]["source"],
            "user-edit",
        )
        self.assertEqual(card["provenance"]["revision"], 1)
        self.assertIn(
            "restart corrected memory from the user",
            result["recallContext"],
        )
        self.assertEqual(
            result["recallProvenance"][0]["source"],
            "user-edit",
        )
        self.assertEqual(
            result["recallProvenance"][0]["revision"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
