from __future__ import annotations

import json
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


class MemoryProvenanceAuditApiTests(
    unittest.IsolatedAsyncioTestCase
):
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
        self.origin = str(
            self.client.make_url("/")
        ).rstrip("/")
        session_response = await self.client.get(
            "/api/control-page/session",
            headers={"Origin": self.origin},
        )
        self.csrf = (
            await session_response.json()
        )["csrfToken"]

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.memory_root_patch.stop()
        self.temp_dir.cleanup()

    def headers(self) -> dict[str, str]:
        return {
            "Origin": self.origin,
            CONTROL_PAGE_CSRF_HEADER: self.csrf,
        }

    async def test_read_only_audit_route_exposes_exact_candidates(
        self,
    ) -> None:
        source_path = memory_vault.write_memory_vault_note(
            note_type="daily",
            title="API Audit Source",
            body="private API audit source",
            source="conversation-turn-log",
        )
        source = memory_vault.parse_memory_note(source_path)
        source_ref = (
            source_path.relative_to(
                Path(self.temp_dir.name) / "memory_vault"
            )
            .with_suffix("")
            .as_posix()
        )
        memory_vault.write_memory_vault_note(
            note_type="episode",
            title="API Audit Target",
            body="private API audit target",
            source="legacy-sub-llm-semantic-consolidation",
            source_refs=[source_ref],
            evidence_hashes=[source.source_hash],
        )

        response = await self.client.get(
            "/api/control-page/memory-provenance-audit",
            headers={"Origin": self.origin},
        )
        payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["readOnly"])
        self.assertFalse(payload["autoApply"])
        self.assertFalse(payload["contentSimilarityUsed"])
        self.assertEqual(
            payload["summary"]["verifiedCount"],
            1,
        )
        self.assertEqual(
            payload["candidates"][0]["candidateSources"][
                0
            ]["id"],
            source.note_id,
        )
        self.assertTrue(
            payload["candidates"][0]["canApply"]
        )

    async def test_two_step_backfill_requires_csrf_and_applies(
        self,
    ) -> None:
        source_path = memory_vault.write_memory_vault_note(
            note_type="daily",
            title="API Apply Source",
            body="API apply source body",
            source="conversation-turn-log",
        )
        source = memory_vault.parse_memory_note(source_path)
        source_ref = (
            source_path.relative_to(
                Path(self.temp_dir.name) / "memory_vault"
            )
            .with_suffix("")
            .as_posix()
        )
        target_path = memory_vault.write_memory_vault_note(
            note_type="episode",
            title="API Apply Target",
            body="API apply target body",
            source="legacy-sub-llm-semantic-consolidation",
            source_refs=[source_ref],
            evidence_hashes=[source.source_hash],
        )
        target = memory_vault.parse_memory_note(target_path)
        preview_path = (
            "/api/control-page/memory-provenance-backfill/"
            f"{target.note_id}/preview"
        )
        apply_path = (
            "/api/control-page/memory-provenance-backfill/"
            f"{target.note_id}/apply"
        )

        denied_preview = await self.client.post(
            preview_path,
            headers={"Origin": self.origin},
            json={"sourceNoteIds": [source.note_id]},
        )
        preview_response = await self.client.post(
            preview_path,
            headers=self.headers(),
            json={"sourceNoteIds": [source.note_id]},
        )
        preview = await preview_response.json()
        denied_apply = await self.client.post(
            apply_path,
            headers={"Origin": self.origin},
            json={"confirmToken": preview["confirmToken"]},
        )
        apply_response = await self.client.post(
            apply_path,
            headers=self.headers(),
            json={"confirmToken": preview["confirmToken"]},
        )
        applied = await apply_response.json()
        detail_response = await self.client.get(
            f"/api/control-page/memory/{target.note_id}",
            headers={"Origin": self.origin},
        )
        detail = await detail_response.json()

        self.assertEqual(denied_preview.status, 403)
        self.assertEqual(preview_response.status, 200)
        self.assertEqual(
            preview["schema"],
            "memory.provenance.backfill-preview.v1",
        )
        self.assertEqual(denied_apply.status, 403)
        self.assertEqual(apply_response.status, 200)
        self.assertTrue(applied["applied"])
        self.assertEqual(
            detail["card"]["provenance"]["derivedFrom"],
            [source.note_id],
        )

    async def test_changed_binding_maps_to_conflict(
        self,
    ) -> None:
        with patch.object(
            control_page_server,
            "apply_memory_provenance_backfill",
            return_value={
                "ok": False,
                "error": (
                    "memory_provenance_backfill_changed_since_preview"
                ),
            },
        ):
            response = await self.client.post(
                (
                    "/api/control-page/"
                    "memory-provenance-backfill/"
                    "target/apply"
                ),
                headers=self.headers(),
                json={"confirmToken": "stale"},
            )

        self.assertEqual(response.status, 409)

    async def test_manual_source_selection_requires_csrf_and_applies(
        self,
    ) -> None:
        source_path = memory_vault.write_memory_vault_note(
            note_type="daily",
            title="Manual API Source",
            body="manual API source body",
            source="conversation-turn-log",
        )
        source = memory_vault.parse_memory_note(source_path)
        target_path = memory_vault.write_memory_vault_note(
            note_type="episode",
            title="Manual API Target",
            body="manual API target body",
            source="legacy-importer",
        )
        target = memory_vault.parse_memory_note(target_path)
        sources_path = (
            "/api/control-page/memory-provenance-manual/"
            f"{target.note_id}/sources"
        )
        preview_path = (
            "/api/control-page/memory-provenance-manual/"
            f"{target.note_id}/preview"
        )
        apply_path = (
            "/api/control-page/memory-provenance-backfill/"
            f"{target.note_id}/apply"
        )

        options_response = await self.client.get(
            sources_path,
            headers={"Origin": self.origin},
        )
        options = await options_response.json()
        preflight_response = await self.client.options(
            sources_path,
            headers={"Origin": self.origin},
        )
        denied_preview = await self.client.post(
            preview_path,
            headers={"Origin": self.origin},
            json={"sourceNoteIds": [source.note_id]},
        )
        preview_response = await self.client.post(
            preview_path,
            headers=self.headers(),
            json={"sourceNoteIds": [source.note_id]},
        )
        preview = await preview_response.json()
        apply_response = await self.client.post(
            apply_path,
            headers=self.headers(),
            json={"confirmToken": preview["confirmToken"]},
        )
        applied = await apply_response.json()
        audit_response = await self.client.get(
            "/api/control-page/memory-provenance-audit",
            headers={"Origin": self.origin},
        )
        audit = await audit_response.json()

        self.assertEqual(options_response.status, 200)
        self.assertEqual(preflight_response.status, 204)
        self.assertEqual(
            preflight_response.headers[
                "Access-Control-Allow-Methods"
            ],
            "GET,POST,OPTIONS",
        )
        self.assertEqual(
            options["schema"],
            "memory.provenance.manual-source-options.v1",
        )
        self.assertEqual(
            [item["id"] for item in options["sourceOptions"]],
            [source.note_id],
        )
        serialized_options = json.dumps(
            options,
            ensure_ascii=False,
        )
        self.assertNotIn("manual API source body", serialized_options)
        self.assertNotIn("sourceHash", serialized_options)
        self.assertNotIn("contentHash", serialized_options)
        self.assertEqual(denied_preview.status, 403)
        self.assertEqual(preview_response.status, 200)
        self.assertEqual(
            preview["selectionMode"],
            "user_selected",
        )
        self.assertEqual(apply_response.status, 200)
        self.assertTrue(applied["applied"])
        self.assertEqual(
            applied["selectionMode"],
            "user_selected",
        )
        self.assertEqual(
            audit["coverage"]["needsReviewCount"],
            0,
        )
        self.assertEqual(audit["manualReviewTargets"], [])

    async def test_memory_snapshot_includes_quarantine_status(
        self,
    ) -> None:
        response = await self.client.get(
            "/api/control-page/memory?limit=5",
            headers={"Origin": self.origin},
        )
        payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertEqual(
            payload["quarantineStatus"]["schema"],
            "memory.quarantine.status.v1",
        )
        self.assertEqual(
            payload["quarantineStatus"]["state"],
            "clear",
        )


if __name__ == "__main__":
    unittest.main()
