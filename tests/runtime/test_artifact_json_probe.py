from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_health import _probe_artifact_json  # noqa: E402
from evelyn_core.runtime_services import HealthProbeSpec, ServiceSpec  # noqa: E402


class ArtifactJsonProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.service = ServiceSpec(
            id="heartbeat",
            label="heartbeat",
            kind="process",
            required=False,
            host="127.0.0.1",
            port=0,
            checks=(),
        )

    def probe(self, path: str = "service/status.json", **overrides):
        check = HealthProbeSpec(
            kind="artifact_json",
            host="127.0.0.1",
            port=0,
            timeout_ms=300,
            path=path,
            expect_json=overrides.pop("expect_json", {"schema": "status.v1"}),
            stale_after_sec=overrides.pop("stale_after_sec", 4),
            **overrides,
        )
        with patch.dict(
            os.environ,
            {"EVELYN_RUNTIME_ARTIFACTS_DIR": str(self.root)},
            clear=False,
        ):
            return asyncio.run(_probe_artifact_json(self.service, check))

    def write(self, payload) -> Path:
        path = self.root / "service" / "status.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_fresh_expected_json_is_ready(self):
        self.write({"schema": "status.v1", "heartbeatAt": time.time()})
        result = self.probe()
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "ok")

    def test_missing_stale_and_corrupt_artifacts_are_distinguished(self):
        self.assertEqual(self.probe()["reason"], "artifact_missing")

        self.write({"schema": "status.v1", "heartbeatAt": time.time() - 10})
        self.assertEqual(self.probe()["reason"], "artifact_stale")

        self.write("{broken")
        self.assertEqual(self.probe()["reason"], "artifact_corrupt")

    def test_unexpected_json_and_path_escape_are_rejected(self):
        self.write({"schema": "wrong.v1", "heartbeatAt": time.time()})
        self.assertEqual(self.probe()["reason"], "unexpected_json")
        self.assertEqual(
            self.probe("../outside.json")["reason"],
            "artifact_path_outside_runtime_root",
        )


if __name__ == "__main__":
    unittest.main()
