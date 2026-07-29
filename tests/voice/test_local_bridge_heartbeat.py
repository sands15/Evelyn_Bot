from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_io_bridge import LocalIoBridge  # noqa: E402


class _Response:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def json(self, *, content_type=None):
        _ = content_type
        return {}


class _Session:
    def post(self, *_args, **_kwargs):
        return _Response()


class LocalBridgeHeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_write_clears_transient_heartbeat_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = Path(temp_dir) / "local_bridge" / "status.json"
            bridge = LocalIoBridge()
            bridge.session = _Session()  # type: ignore[assignment]
            bridge.last_error = "heartbeat_write_failed: PermissionError"

            with (
                patch(
                    "evelyn_core.local_io_bridge.LOCAL_BRIDGE_STATUS_PATH",
                    status_path,
                ),
                patch.object(bridge, "_output_devices_snapshot", return_value=[]),
            ):
                await bridge._post_status()

            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["lastError"], "")
            self.assertEqual(bridge.last_error, "")


if __name__ == "__main__":
    unittest.main()
