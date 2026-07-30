from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server, memory_vault  # noqa: E402
from evelyn_core.control_page_http import CONTROL_PAGE_CSRF_HEADER  # noqa: E402


class MemoryDeletionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.memory_root_patch = patch.object(
            memory_vault,
            "MEMORY_ROOT",
            Path(self.temp_dir.name),
        )
        self.memory_root_patch.start()
        self.client = TestClient(TestServer(control_page_server.create_app()))
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

    async def test_preview_and_apply_use_two_step_contract(self) -> None:
        preview_result = {
            "ok": True,
            "schema": "memory.deletion.preview.v1",
            "confirmToken": "one-use-token",
            "note": {"id": "concept-test"},
        }
        apply_result = {
            "ok": True,
            "schema": "memory.deletion.result.v1",
            "noteId": "concept-test",
            "deleted": True,
        }
        with (
            patch.object(
                control_page_server,
                "preview_memory_vault_user_note_deletion",
                return_value=preview_result,
            ) as preview,
            patch.object(
                control_page_server,
                "delete_memory_vault_user_note",
                return_value=apply_result,
            ) as apply,
        ):
            preview_response = await self.client.post(
                "/api/control-page/memory/concept-test/delete/preview",
                headers=self.headers(),
                json={"reason": "privacy_request"},
            )
            applied_response = await self.client.post(
                "/api/control-page/memory/concept-test/delete/apply",
                headers=self.headers(),
                json={
                    "confirmToken": "one-use-token",
                    "reason": "privacy_request",
                },
            )

        self.assertEqual(preview_response.status, 200)
        self.assertEqual(
            (await preview_response.json())["schema"],
            "memory.deletion.preview.v1",
        )
        self.assertEqual(applied_response.status, 200)
        self.assertTrue((await applied_response.json())["deleted"])
        preview.assert_called_once_with(
            "concept-test",
            reason="privacy_request",
        )
        apply.assert_called_once_with(
            "concept-test",
            "one-use-token",
            reason="privacy_request",
        )

    async def test_both_mutating_routes_require_csrf(self) -> None:
        for action in ("preview", "apply"):
            with self.subTest(action=action):
                response = await self.client.post(
                    f"/api/control-page/memory/concept-test/delete/{action}",
                    headers={"Origin": self.origin},
                    json={},
                )
                self.assertEqual(response.status, 403)
                self.assertEqual(
                    (await response.json())["error"],
                    "csrf_token_required",
                )

    async def test_end_to_end_api_removes_source_and_keeps_content_free_tombstone(
        self,
    ) -> None:
        title = "API Deletion Canary"
        body = "sensitive API deletion body"
        path = memory_vault.write_memory_vault_note(
            note_type="concept",
            title=title,
            body=body,
            source="control-page-user",
        )
        note = memory_vault.parse_memory_note(path)
        preview_response = await self.client.post(
            f"/api/control-page/memory/{note.note_id}/delete/preview",
            headers=self.headers(),
            json={"reason": "privacy_request"},
        )
        preview = await preview_response.json()
        applied_response = await self.client.post(
            f"/api/control-page/memory/{note.note_id}/delete/apply",
            headers=self.headers(),
            json={
                "confirmToken": preview["confirmToken"],
                "reason": "privacy_request",
            },
        )
        applied = await applied_response.json()
        tombstone_raw = (
            Path(self.temp_dir.name)
            / "memory_index"
            / "memory_deletions.jsonl"
        ).read_text(encoding="utf-8")

        self.assertEqual(preview_response.status, 200)
        self.assertEqual(applied_response.status, 200)
        self.assertTrue(applied["deleted"])
        self.assertFalse(path.exists())
        self.assertNotIn(title, tombstone_raw)
        self.assertNotIn(body, tombstone_raw)

    async def test_conflict_errors_are_not_reported_as_not_found(self) -> None:
        with patch.object(
            control_page_server,
            "preview_memory_vault_user_note_deletion",
            return_value={
                "ok": False,
                "error": "memory_note_delete_protected",
                "reason": "bootstrap_contract_note",
            },
        ):
            response = await self.client.post(
                "/api/control-page/memory/core-contract/delete/preview",
                headers=self.headers(),
                json={},
            )

        self.assertEqual(response.status, 409)
        self.assertEqual(
            (await response.json())["error"],
            "memory_note_delete_protected",
        )

    async def test_cleanup_required_is_reported_as_service_unavailable(
        self,
    ) -> None:
        cleanup_result = {
            "ok": False,
            "schema": "memory.deletion.result.v1",
            "noteId": "concept-test",
            "deleted": False,
            "tombstoned": True,
            "sourceFileDeleted": False,
            "error": "memory_delete_cleanup_required",
            "cleanupErrors": [
                "memory_delete_source_cleanup_failed",
            ],
        }
        with patch.object(
            control_page_server,
            "delete_memory_vault_user_note",
            return_value=cleanup_result,
        ):
            response = await self.client.post(
                "/api/control-page/memory/concept-test/delete/apply",
                headers=self.headers(),
                json={"confirmToken": "one-use-token"},
            )

        self.assertEqual(response.status, 503)
        payload = await response.json()
        self.assertTrue(payload["tombstoned"])
        self.assertEqual(
            payload["error"],
            "memory_delete_cleanup_required",
        )

    async def test_api_reports_and_applies_derivation_impact(
        self,
    ) -> None:
        source_a_path = memory_vault.write_memory_vault_note(
            note_type="concept",
            title="API Derivation Source A",
            body="revoked API source body",
            source="control-page-user",
        )
        source_b_path = memory_vault.write_memory_vault_note(
            note_type="concept",
            title="API Derivation Source B",
            body="remaining API source body",
            source="control-page-user",
        )
        source_a = memory_vault.parse_memory_note(
            source_a_path
        )
        source_b = memory_vault.parse_memory_note(
            source_b_path
        )
        single_path = memory_vault.write_memory_vault_note(
            note_type="episode",
            title="API Single Derived",
            body="single API derived body",
            source="sub-llm-semantic-consolidation",
            derived_from=[source_a.note_id],
        )
        multi_path = memory_vault.write_memory_vault_note(
            note_type="concept",
            title="API Multi Derived",
            body="multi API derived body",
            source="sub-llm-semantic-consolidation",
            derived_from=[
                source_a.note_id,
                source_b.note_id,
            ],
        )
        multi = memory_vault.parse_memory_note(multi_path)

        preview_response = await self.client.post(
            (
                f"/api/control-page/memory/{source_a.note_id}"
                "/delete/preview"
            ),
            headers=self.headers(),
            json={"reason": "privacy_request"},
        )
        preview = await preview_response.json()
        apply_response = await self.client.post(
            (
                f"/api/control-page/memory/{source_a.note_id}"
                "/delete/apply"
            ),
            headers=self.headers(),
            json={
                "confirmToken": preview["confirmToken"],
                "reason": "privacy_request",
            },
        )
        applied = await apply_response.json()
        snapshot_response = await self.client.get(
            "/api/control-page/memory?limit=20",
            headers={"Origin": self.origin},
        )
        snapshot = await snapshot_response.json()

        self.assertEqual(preview_response.status, 200)
        self.assertEqual(
            preview["derivationImpact"][
                "cascadeDeleteCount"
            ],
            1,
        )
        self.assertEqual(
            preview["derivationImpact"]["quarantineCount"],
            1,
        )
        self.assertEqual(apply_response.status, 200)
        self.assertEqual(
            applied["derivationImpact"]["quarantineCount"],
            1,
        )
        self.assertFalse(source_a_path.exists())
        self.assertFalse(single_path.exists())
        self.assertTrue(multi_path.exists())
        cards = {
            card["id"]: card
            for card in snapshot["cards"]
        }
        self.assertTrue(cards[multi.note_id]["quarantined"])

    async def test_api_rejects_stale_derivation_impact(
        self,
    ) -> None:
        source_path = memory_vault.write_memory_vault_note(
            note_type="concept",
            title="API Stale Impact Source",
            body="stale impact source body",
            source="control-page-user",
        )
        source = memory_vault.parse_memory_note(source_path)
        preview_response = await self.client.post(
            (
                f"/api/control-page/memory/{source.note_id}"
                "/delete/preview"
            ),
            headers=self.headers(),
            json={},
        )
        preview = await preview_response.json()
        memory_vault.write_memory_vault_note(
            note_type="episode",
            title="API Late Derived",
            body="late derived after preview",
            source="sub-llm-semantic-consolidation",
            derived_from=[source.note_id],
        )
        apply_response = await self.client.post(
            (
                f"/api/control-page/memory/{source.note_id}"
                "/delete/apply"
            ),
            headers=self.headers(),
            json={"confirmToken": preview["confirmToken"]},
        )
        payload = await apply_response.json()

        self.assertEqual(apply_response.status, 409)
        self.assertEqual(
            payload["error"],
            (
                "memory_derivation_impact_changed_since_preview"
            ),
        )
        self.assertTrue(source_path.exists())


if __name__ == "__main__":
    unittest.main()
