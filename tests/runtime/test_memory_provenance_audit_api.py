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

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.memory_root_patch.stop()
        self.temp_dir.cleanup()

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
            source="sub-llm-semantic-consolidation",
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
