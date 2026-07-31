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
        legacy_scope = Path(self.temp_dir.name) / "guild_7"
        legacy_scope.mkdir(parents=True)
        (legacy_scope / "raw_transcript.jsonl").write_text(
            json.dumps(
                {
                    "role": "user",
                    "text": "PRIVATE_LEGACY_API_CANARY",
                }
            )
            + "\n",
            encoding="utf-8",
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
        legacy_coverage = payload["legacyContextCoverage"]
        self.assertEqual(
            legacy_coverage["schema"],
            "memory.legacy-context-coverage.v1",
        )
        self.assertEqual(legacy_coverage["totalStoredItemCount"], 1)
        self.assertEqual(legacy_coverage["confirmOnlyStoredItemCount"], 1)
        self.assertNotIn(
            "PRIVATE_LEGACY_API_CANARY",
            json.dumps(payload, ensure_ascii=False),
        )
        persisted_raw = Path(payload["reportPath"]).read_text(encoding="utf-8")
        self.assertIn("memory.legacy-context-coverage.v1", persisted_raw)
        self.assertNotIn("PRIVATE_LEGACY_API_CANARY", persisted_raw)

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

    async def test_correction_relink_and_undo_require_csrf(
        self,
    ) -> None:
        source_a_path = memory_vault.write_memory_vault_note(
            note_type="daily",
            title="Correction API Source A",
            body="private correction API source A body",
            source="conversation-turn-log",
        )
        source_b_path = memory_vault.write_memory_vault_note(
            note_type="daily",
            title="Correction API Source B",
            body="private correction API source B body",
            source="conversation-turn-log",
        )
        source_a = memory_vault.parse_memory_note(
            source_a_path
        )
        source_b = memory_vault.parse_memory_note(
            source_b_path
        )
        target_path = memory_vault.write_memory_vault_note(
            note_type="episode",
            title="Correction API Target",
            body="private correction API target body",
            source="sub-llm-semantic-consolidation",
            derived_from=[source_a.note_id],
        )
        target = memory_vault.parse_memory_note(target_path)
        base_path = (
            "/api/control-page/memory-provenance-corrections"
        )
        note_path = f"{base_path}/{target.note_id}"

        overview_response = await self.client.get(
            base_path,
            headers={"Origin": self.origin},
        )
        overview = await overview_response.json()
        sources_response = await self.client.get(
            f"{note_path}/sources",
            headers={"Origin": self.origin},
        )
        options = await sources_response.json()
        preflight_response = await self.client.options(
            f"{note_path}/preview",
            headers={"Origin": self.origin},
        )
        denied_preview = await self.client.post(
            f"{note_path}/preview",
            headers={"Origin": self.origin},
            json={"sourceNoteIds": [source_b.note_id]},
        )
        invalid_preview = await self.client.post(
            f"{note_path}/preview",
            headers=self.headers(),
            json={},
        )
        preview_response = await self.client.post(
            f"{note_path}/preview",
            headers=self.headers(),
            json={"sourceNoteIds": [source_b.note_id]},
        )
        preview = await preview_response.json()
        denied_apply = await self.client.post(
            f"{note_path}/apply",
            headers={"Origin": self.origin},
            json={"confirmToken": preview["confirmToken"]},
        )
        apply_response = await self.client.post(
            f"{note_path}/apply",
            headers=self.headers(),
            json={"confirmToken": preview["confirmToken"]},
        )
        applied = await apply_response.json()
        changed_overview_response = await self.client.get(
            base_path,
            headers={"Origin": self.origin},
        )
        changed_overview = (
            await changed_overview_response.json()
        )
        undo_preview_response = await self.client.post(
            f"{note_path}/undo/preview",
            headers=self.headers(),
            json={"changeId": applied["changeId"]},
        )
        undo_preview = await undo_preview_response.json()
        undo_apply_response = await self.client.post(
            f"{note_path}/undo/apply",
            headers=self.headers(),
            json={
                "confirmToken": undo_preview[
                    "confirmToken"
                ]
            },
        )
        undone = await undo_apply_response.json()
        final_note = memory_vault.parse_memory_note(
            target_path
        )

        self.assertEqual(overview_response.status, 200)
        self.assertEqual(
            overview["schema"],
            "memory.provenance.corrections.v1",
        )
        self.assertEqual(overview["relationshipCount"], 1)
        self.assertEqual(
            overview["relationships"][0]["currentSourceIds"],
            [source_a.note_id],
        )
        self.assertEqual(sources_response.status, 200)
        self.assertEqual(
            {
                item["id"]
                for item in options["sourceOptions"]
            },
            {source_a.note_id, source_b.note_id},
        )
        self.assertTrue(options["unlinkAllowed"])
        serialized_read_models = json.dumps(
            [overview, options],
            ensure_ascii=False,
        )
        for private_value in (
            "private correction API source A body",
            "private correction API source B body",
            "private correction API target body",
            "sourceHash",
            "contentHash",
        ):
            self.assertNotIn(
                private_value,
                serialized_read_models,
            )
        self.assertEqual(preflight_response.status, 204)
        self.assertEqual(denied_preview.status, 403)
        self.assertEqual(invalid_preview.status, 400)
        self.assertEqual(preview_response.status, 200)
        self.assertEqual(preview["action"], "relink")
        self.assertEqual(denied_apply.status, 403)
        self.assertEqual(apply_response.status, 200)
        self.assertTrue(applied["applied"])
        self.assertNotIn("contentHash", applied)
        self.assertNotIn("previousContentHash", applied)
        self.assertTrue(
            changed_overview["relationships"][0][
                "latestChange"
            ]["canUndo"]
        )
        self.assertEqual(undo_preview_response.status, 200)
        self.assertEqual(
            undo_preview["previewKind"],
            "undo",
        )
        self.assertEqual(undo_apply_response.status, 200)
        self.assertTrue(undone["applied"])
        self.assertEqual(
            final_note.metadata["derived_from"],
            f"[{source_a.note_id}]",
        )

    async def test_correction_stale_binding_maps_to_conflict(
        self,
    ) -> None:
        with patch.object(
            control_page_server,
            "apply_memory_provenance_correction",
            return_value={
                "ok": False,
                "error": (
                    "memory_provenance_correction_"
                    "changed_since_preview"
                ),
            },
        ):
            response = await self.client.post(
                (
                    "/api/control-page/"
                    "memory-provenance-corrections/"
                    "target/apply"
                ),
                headers=self.headers(),
                json={"confirmToken": "stale"},
            )

        self.assertEqual(response.status, 409)

    async def test_correction_integrity_failure_is_unavailable(
        self,
    ) -> None:
        with patch.object(
            control_page_server,
            "memory_provenance_correction_overview",
            return_value={
                "ok": False,
                "error": (
                    "memory_provenance_correction_"
                    "journal_integrity_failed"
                ),
            },
        ):
            response = await self.client.get(
                (
                    "/api/control-page/"
                    "memory-provenance-corrections"
                ),
                headers={"Origin": self.origin},
            )

        self.assertEqual(response.status, 503)

    async def test_correction_authenticity_failures_are_unavailable(
        self,
    ) -> None:
        errors = (
            "memory_provenance_correction_auth_failed",
            "memory_provenance_correction_auth_bootstrap_required",
            "memory_provenance_correction_anchor_bootstrap_required",
            "memory_provenance_correction_anchor_replay_detected",
            "memory_provenance_correction_anchor_unavailable",
        )
        for error in errors:
            with self.subTest(error=error), patch.object(
                control_page_server,
                "memory_provenance_correction_overview",
                return_value={"ok": False, "error": error},
            ):
                response = await self.client.get(
                    (
                        "/api/control-page/"
                        "memory-provenance-corrections"
                    ),
                    headers={"Origin": self.origin},
                )
                self.assertEqual(response.status, 503)

    async def test_correction_writer_conflict_is_unavailable(
        self,
    ) -> None:
        with patch.object(
            control_page_server,
            "apply_memory_provenance_correction",
            return_value={
                "ok": False,
                "applied": False,
                "error": (
                    "memory_provenance_correction_"
                    "writer_unavailable"
                ),
            },
        ):
            response = await self.client.post(
                (
                    "/api/control-page/"
                    "memory-provenance-corrections/"
                    "target/apply"
                ),
                headers=self.headers(),
                json={"confirmToken": "busy"},
            )

        self.assertEqual(response.status, 503)

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
