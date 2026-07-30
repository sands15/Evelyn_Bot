from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server, memory_vault  # noqa: E402
from evelyn_core.control_page_http import (  # noqa: E402
    CONTROL_PAGE_CSRF_HEADER,
)


class MemoryEditApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.memory_root_patch = patch.object(
            memory_vault,
            "MEMORY_ROOT",
            Path(self.temp_dir.name),
        )
        self.memory_root_patch.start()
        self.client = TestClient(
            TestServer(control_page_server.create_app())
        )
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")
        session_response = await self.client.get(
            "/api/control-page/session",
            headers={"Origin": self.origin},
        )
        self.csrf = (await session_response.json())["csrfToken"]

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.memory_root_patch.stop()
        self.temp_dir.cleanup()

    def headers(self) -> dict[str, str]:
        return {
            "Origin": self.origin,
            CONTROL_PAGE_CSRF_HEADER: self.csrf,
        }

    def create_note(self) -> memory_vault.MemoryVaultNote:
        path = memory_vault.write_memory_vault_note(
            note_type="concept",
            title="API Memory Edit",
            body="generated API memory",
            source="sub-llm-semantic-consolidation",
            source_refs=["daily/2026-07-30"],
            evidence_hashes=["old-api-evidence"],
        )
        return memory_vault.parse_memory_note(path)

    async def test_edit_requires_current_content_hash(self) -> None:
        note = self.create_note()

        response = await self.client.post(
            f"/api/control-page/memory/{note.note_id}",
            headers=self.headers(),
            json={
                "action": "edit",
                "title": "Corrected API Memory",
                "body": "user corrected API memory",
            },
        )

        self.assertEqual(response.status, 400)
        self.assertEqual(
            (await response.json())["error"],
            "memory_edit_content_hash_required",
        )

    async def test_edit_updates_provenance_and_rejects_stale_retry(
        self,
    ) -> None:
        note = self.create_note()
        payload = {
            "action": "edit",
            "title": "Corrected API Memory",
            "body": "user corrected API memory",
            "expectedContentHash": note.source_hash,
        }

        accepted_response = await self.client.post(
            f"/api/control-page/memory/{note.note_id}",
            headers=self.headers(),
            json=payload,
        )
        stale_response = await self.client.post(
            f"/api/control-page/memory/{note.note_id}",
            headers=self.headers(),
            json={
                **payload,
                "body": "stale overwrite",
            },
        )
        detail_response = await self.client.get(
            f"/api/control-page/memory/{note.note_id}",
            headers={"Origin": self.origin},
        )
        accepted = await accepted_response.json()
        stale = await stale_response.json()
        detail = await detail_response.json()

        self.assertEqual(accepted_response.status, 200)
        self.assertEqual(
            accepted["schema"],
            "memory.edit.result.v1",
        )
        self.assertTrue(accepted["edited"])
        self.assertEqual(
            accepted["provenance"]["source"],
            "user-edit",
        )
        self.assertEqual(
            accepted["provenance"]["revision"],
            1,
        )
        self.assertEqual(stale_response.status, 409)
        self.assertEqual(
            stale["error"],
            "memory_note_changed_since_read",
        )
        self.assertIn(
            "user corrected API memory",
            detail["card"]["body"],
        )
        self.assertNotIn(
            "stale overwrite",
            detail["card"]["body"],
        )

    async def test_edit_requires_csrf(self) -> None:
        note = self.create_note()

        response = await self.client.post(
            f"/api/control-page/memory/{note.note_id}",
            headers={"Origin": self.origin},
            json={
                "action": "edit",
                "expectedContentHash": note.source_hash,
            },
        )

        self.assertEqual(response.status, 403)
        self.assertEqual(
            (await response.json())["error"],
            "csrf_token_required",
        )

    async def test_atomic_write_failure_is_not_reported_as_success(
        self,
    ) -> None:
        note = self.create_note()
        with patch.object(
            memory_vault,
            "atomic_text_write",
            side_effect=OSError("disk unavailable"),
        ):
            response = await self.client.post(
                f"/api/control-page/memory/{note.note_id}",
                headers=self.headers(),
                json={
                    "action": "edit",
                    "title": "Failed Edit",
                    "body": "must not persist",
                    "expectedContentHash": note.source_hash,
                },
            )

        self.assertEqual(response.status, 500)
        payload = await response.json()
        self.assertFalse(payload["edited"])
        self.assertEqual(payload["error"], "memory_edit_failed")


if __name__ == "__main__":
    unittest.main()
