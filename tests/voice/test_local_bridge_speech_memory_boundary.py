from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
sys.modules.setdefault("numpy", types.SimpleNamespace(ndarray=object))

from evelyn_core import fast_control_api as fast_api  # noqa: E402

# ``local_io_bridge`` only needs the host-vision class while constructing the
# full Windows process.  This boundary test deliberately constructs no host
# service, and the lightweight Bot API test image does not include Pillow.
# Keep that optional dependency from hiding the queue/playback contract under
# test while leaving normal environments on the real import path.
_host_vision_module_name = "evelyn_core.host_vision_bridge"
_host_vision_stubbed = False
try:  # pragma: no cover - depends on the selected test image
    import PIL  # noqa: F401,E402
    from PIL import Image  # noqa: F401,E402
except ImportError:  # pragma: no cover - exercised in lightweight/ABI-mismatched test images
    _host_vision_stub = types.ModuleType(_host_vision_module_name)

    class _UnavailableHostVisionBridge:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "host vision must not be constructed by this unit test"
            )

    _host_vision_stub.HostVisionBridge = _UnavailableHostVisionBridge
    sys.modules[_host_vision_module_name] = _host_vision_stub
    _host_vision_stubbed = True

from evelyn_core import local_io_bridge  # noqa: E402

if _host_vision_stubbed:
    sys.modules.pop(_host_vision_module_name, None)

from evelyn_core import memory_deletion_journal as deletion_journal  # noqa: E402
from evelyn_core import memory_exposure  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


_NOTE_ID = "concept-0123456789abcdef"


class LocalBridgeSpeechMemoryBoundaryTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        memory_exposure.reset_memory_exposure_position()

    def tearDown(self) -> None:
        memory_exposure.reset_memory_exposure_position()

    @contextmanager
    def isolated_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bot_memory = root / "bot_memory"
            runtime_artifacts = root / "runtime_artifacts"
            bot_memory.mkdir()
            runtime_artifacts.mkdir()
            with patch.dict(
                os.environ,
                {
                    "BOT_MEMORY_DIR": str(bot_memory),
                    "EVELYN_RUNTIME_ARTIFACTS_DIR": str(
                        runtime_artifacts
                    ),
                    MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                    MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                    MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
                },
            ), patch.object(
                fast_api,
                "MEMORY_ROOT",
                bot_memory,
            ), patch.object(
                local_io_bridge,
                "MEMORY_ROOT",
                bot_memory,
            ), patch.object(
                fast_api,
                "LOCAL_BRIDGE_SPEAK_QUEUE",
                [],
            ), patch.object(
                fast_api,
                "LOCAL_BRIDGE_SPEAK_SEQ",
                0,
            ), patch.object(
                fast_api,
                "LOCAL_BRIDGE_STATUS",
                {
                    "enabled": True,
                    "ready": True,
                    "lastError": "",
                    "updatedAt": fast_api.time.time(),
                },
            ):
                yield bot_memory, runtime_artifacts

    @staticmethod
    def write_memory_version(index_dir: Path, version: int) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(index_dir / memory_exposure.MEMORY_INDEX_DB_NAME)
        )
        try:
            connection.execute(
                "CREATE TABLE metadata (key TEXT PRIMARY KEY, value NOT NULL)"
            )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?)",
                ("memory_version", str(version)),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def replace_memory_version(index_dir: Path, version: int) -> None:
        connection = sqlite3.connect(
            str(index_dir / memory_exposure.MEMORY_INDEX_DB_NAME)
        )
        try:
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = ?",
                (str(version), "memory_version"),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def exposure_position(
        index_dir: Path,
        *,
        version: int,
    ) -> memory_exposure.MemoryExposurePosition:
        return memory_exposure.MemoryExposurePosition(
            deletion_position=(
                deletion_journal.memory_deletion_journal_position(
                    index_dir
                )
            ),
            memory_version=version,
            supplied_note_ids=(_NOTE_ID,),
        )

    def test_queue_serializes_current_exposure_as_content_free_wire(self) -> None:
        with self.isolated_roots() as (bot_memory, _runtime_artifacts):
            index_dir = bot_memory / "memory_index"
            self.write_memory_version(index_dir, 1)
            position = self.exposure_position(index_dir, version=1)
            memory_exposure.capture_memory_exposure_position(position)

            queued = fast_api.queue_local_bridge_speech(
                "synthetic playback",
                source="unit",
            )

            self.assertIsNotNone(queued)
            boundary = queued["memoryBoundary"]
            self.assertEqual(
                boundary,
                memory_exposure.memory_exposure_position_to_dict(
                    position
                ),
            )
            self.assertEqual(
                set(boundary),
                {
                    "schema",
                    "deletionPosition",
                    "memoryVersion",
                    "suppliedNoteIds",
                    "contentFree",
                },
            )
            self.assertTrue(boundary["contentFree"])
            encoded = json.dumps(boundary, sort_keys=True)
            self.assertNotIn(str(bot_memory), encoded)
            self.assertNotIn("synthetic playback", encoded)

    def test_drain_drops_stale_and_tombstoned_boundaries(self) -> None:
        for mode in ("version", "tombstone"):
            with self.subTest(mode=mode):
                memory_exposure.reset_memory_exposure_position()
                with self.isolated_roots() as (
                    bot_memory,
                    _runtime_artifacts,
                ):
                    index_dir = bot_memory / "memory_index"
                    self.write_memory_version(index_dir, 1)
                    position = self.exposure_position(
                        index_dir,
                        version=1,
                    )
                    memory_exposure.capture_memory_exposure_position(
                        position
                    )
                    queued = fast_api.queue_local_bridge_speech(
                        "must not drain",
                        source="unit",
                    )
                    self.assertIsNotNone(queued)
                    if mode == "version":
                        self.replace_memory_version(index_dir, 2)
                    else:
                        deletion_journal.append_memory_deletion_tombstone(
                            index_dir,
                            {
                                "schema": deletion_journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                                "noteId": _NOTE_ID,
                                "noteType": "concept",
                                "sourceType": "conversation",
                                "reason": "privacy_request",
                                "deletedAt": "2026-08-01T00:00:00Z",
                            },
                        )

                    self.assertEqual(
                        fast_api.drain_local_bridge_speak_requests(),
                        [],
                    )
                    self.assertEqual(
                        fast_api.LOCAL_BRIDGE_SPEAK_QUEUE,
                        [],
                    )

    def test_drain_preserves_queue_while_deletion_journal_is_busy(self) -> None:
        private_canary = "private speech must stay queued"
        with self.isolated_roots() as (bot_memory, _runtime_artifacts):
            index_dir = bot_memory / "memory_index"
            self.write_memory_version(index_dir, 1)
            position = self.exposure_position(index_dir, version=1)
            memory_exposure.capture_memory_exposure_position(position)
            queued = fast_api.queue_local_bridge_speech(
                private_canary,
                source="unit",
            )
            before = [dict(item) for item in fast_api.LOCAL_BRIDGE_SPEAK_QUEUE]

            with patch.object(
                fast_api,
                "memory_exposure_guard",
                side_effect=deletion_journal.MemoryDeletionJournalBusyError(
                    private_canary
                ),
            ):
                with self.assertRaises(
                    deletion_journal.MemoryDeletionJournalBusyError
                ) as raised:
                    fast_api.drain_local_bridge_speak_requests()

            self.assertIsNotNone(queued)
            self.assertEqual(fast_api.LOCAL_BRIDGE_SPEAK_QUEUE, before)
            self.assertEqual(
                str(raised.exception),
                deletion_journal.MEMORY_DELETION_JOURNAL_BUSY_ERROR,
            )
            self.assertNotIn(private_canary, str(raised.exception))

    async def test_local_worker_never_speaks_stale_boundary(self) -> None:
        with self.isolated_roots() as (bot_memory, _runtime_artifacts):
            index_dir = bot_memory / "memory_index"
            self.write_memory_version(index_dir, 1)
            position = self.exposure_position(index_dir, version=1)
            wire_boundary = (
                memory_exposure.memory_exposure_position_to_dict(
                    position
                )
            )
            self.replace_memory_version(index_dir, 2)

            bridge = object.__new__(local_io_bridge.LocalIoBridge)
            bridge.speak_request_queue = asyncio.Queue()
            bridge.speak_request_queue.put_nowait(
                {
                    "id": "synthetic-stale-request",
                    "text": "must not play",
                    "memoryBoundary": wire_boundary,
                }
            )
            bridge.active_turn_task = None
            bridge.last_tts_playback = {}
            bridge.last_latency = {}
            bridge.last_error = ""
            bridge.runtime_errors = Mock()
            bridge._speak = AsyncMock()
            bridge._post_status = AsyncMock()

            with patch.object(
                local_io_bridge,
                "LOCAL_BRIDGE_TTS_ENABLED",
                True,
            ):
                await local_io_bridge.LocalIoBridge._speak_request_worker(
                    bridge
                )

            bridge._speak.assert_not_awaited()
            bridge._post_status.assert_awaited_once_with()
            bridge.runtime_errors.record.assert_not_called()
            self.assertEqual(bridge.speak_request_queue.qsize(), 0)
            await bridge.speak_request_queue.join()


if __name__ == "__main__":
    unittest.main()
