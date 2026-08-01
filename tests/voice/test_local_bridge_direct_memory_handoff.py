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
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import AsyncMock, Mock, patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

# The bundled test Python may not have ABI-compatible host audio/image wheels.
# None of those implementations are exercised by these protocol-only tests.
sys.modules.setdefault("numpy", types.SimpleNamespace(ndarray=object))

_host_vision_module_name = "evelyn_core.host_vision_bridge"
_host_vision_stubbed = False
try:  # pragma: no cover - depends on the selected test environment
    import PIL  # noqa: F401,E402
    from PIL import Image  # noqa: F401,E402
except ImportError:  # pragma: no cover - lightweight/ABI-mismatched images
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
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalIntegrityError,
)
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


_NOTE_ID = "concept-0123456789abcdef"


class _AsyncLines:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._lines = iter(
            (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
            for event in events
        )

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration from None


class _StreamingResponse:
    status = 200

    def __init__(
        self,
        events: list[dict[str, Any]],
        *,
        state: dict[str, Any],
        on_exit: Callable[[], None] | None = None,
    ) -> None:
        self.content = _AsyncLines(events)
        self._state = state
        self._on_exit = on_exit

    async def __aenter__(self):
        self._state["response_open"] = True
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        self._state["response_open"] = False
        self._state["response_exited"] = True
        if self._on_exit is not None:
            self._on_exit()


class _JsonResponse:
    status = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return None

    async def json(self, **_kwargs) -> dict[str, Any]:
        return dict(self._payload)


class _Session:
    def __init__(
        self,
        response: _StreamingResponse | _JsonResponse,
        websocket: Any | None = None,
    ) -> None:
        self.response = response
        self.websocket = websocket

    def post(self, *_args, **_kwargs):
        return self.response

    async def ws_connect(self, *_args, **_kwargs):
        if self.websocket is None:
            raise AssertionError("websocket must not be requested")
        return self.websocket


class _OutputStream:
    def __init__(self, *, state: dict[str, Any]) -> None:
        self._state = state
        self.writes: list[bytes] = []
        self.write_observations: list[tuple[bool, bool]] = []
        self.abort_count = 0

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return None

    def write(self, payload: bytes) -> None:
        self.write_observations.append(
            (
                bool(self._state["response_exited"]),
                bool(self._state["guard_active"]),
            )
        )
        self.writes.append(bytes(payload))

    def abort(self) -> None:
        self.abort_count += 1

    def stop(self) -> None:
        return None


class _SoundDevice:
    def __init__(self, *, state: dict[str, Any]) -> None:
        self.stream = _OutputStream(state=state)

    def RawOutputStream(self, **_kwargs):
        return self.stream


class _WebSocket:
    def __init__(self, *, state: dict[str, Any]) -> None:
        self._state = state
        self._flush_sent = asyncio.Event()
        self._receive_index = 0
        self.closed = False
        self.sent: list[dict[str, Any]] = []
        self.send_observations: list[tuple[str, bool, bool]] = []

    async def receive_json(self, **_kwargs) -> dict[str, str]:
        return {"type": "ready"}

    async def send_json(self, payload: dict[str, Any]) -> None:
        command_type = str(payload.get("type") or "")
        self.sent.append(dict(payload))
        self.send_observations.append(
            (
                command_type,
                bool(self._state["response_exited"]),
                bool(self._state["guard_active"]),
            )
        )
        if command_type == "flush":
            self._flush_sent.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._flush_sent.wait()
        if self._receive_index == 0:
            self._receive_index += 1
            return SimpleNamespace(
                type=local_io_bridge.aiohttp.WSMsgType.BINARY,
                data=b"\x01\x02\x03\x04",
            )
        if self._receive_index == 1:
            self._receive_index += 1
            return SimpleNamespace(
                type=local_io_bridge.aiohttp.WSMsgType.TEXT,
                data=json.dumps({"type": "done"}),
            )
        raise StopAsyncIteration

    async def close(self) -> None:
        self.closed = True


class LocalBridgeDirectMemoryHandoffTests(
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
                local_io_bridge,
                "MEMORY_ROOT",
                bot_memory,
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
                "CREATE TABLE metadata "
                "(key TEXT PRIMARY KEY, value NOT NULL)"
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

    @staticmethod
    def stale_memory(index_dir: Path, mode: str) -> None:
        if mode == "version":
            LocalBridgeDirectMemoryHandoffTests.replace_memory_version(
                index_dir,
                2,
            )
            return
        if mode != "tombstone":
            raise AssertionError(f"unexpected stale mode: {mode}")
        deletion_journal.append_memory_deletion_tombstone(
            index_dir,
            {
                "schema": (
                    deletion_journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA
                ),
                "noteId": _NOTE_ID,
                "noteType": "concept",
                "sourceType": "conversation",
                "reason": "privacy_request",
                "deletedAt": "2026-08-01T00:00:00Z",
            },
        )

    @staticmethod
    def bound_events(
        position: memory_exposure.MemoryExposurePosition,
        *,
        delta: bool,
    ) -> list[dict[str, Any]]:
        wire = memory_exposure.memory_exposure_position_to_dict(position)
        events: list[dict[str, Any]] = [
            {
                "type": "memory_boundary",
                "memoryState": "bound",
                "memoryBoundary": wire,
            }
        ]
        if delta:
            events.extend(
                (
                    {"type": "progress", "text": "생각 중"},
                    {"type": "delta", "text": "안녕"},
                    {"type": "sentence", "text": "안녕."},
                )
            )
        else:
            events.extend(
                (
                    {"type": "sentence", "text": "첫 문장"},
                    {"type": "sentence", "text": "둘째 문장"},
                )
            )
        events.append(
            {
                "type": "done",
                "reply": "안녕.",
                "memoryState": "bound",
                "memoryBoundary": wire,
            }
        )
        return events

    @staticmethod
    def configured_bridge() -> local_io_bridge.LocalIoBridge:
        bridge = local_io_bridge.LocalIoBridge()
        bridge._post_status = AsyncMock()
        bridge._local_voice_chat_payload = AsyncMock(return_value={})
        bridge._apply_voice_admission_status = Mock()
        bridge._mark_reply_final_once = Mock()
        bridge._mark_playback_started_once = Mock()
        bridge._ensure_validation_attempt_current = Mock()
        return bridge

    @staticmethod
    def observed_guard(
        state: dict[str, Any],
        entries: list[dict[str, Any]],
    ):
        real_guard = memory_exposure.memory_exposure_guard

        @contextmanager
        def guard(**kwargs):
            entries.append(dict(kwargs))
            if not state["response_exited"]:
                raise AssertionError(
                    "host guard entered before the HTTP response closed"
                )
            with real_guard(**kwargs) as lease:
                state["guard_active"] = True
                try:
                    yield lease
                finally:
                    state["guard_active"] = False

        return guard

    async def test_sentence_bound_waits_for_eof_and_speaks_under_guard(
        self,
    ) -> None:
        with self.isolated_roots() as (bot_memory, _artifacts):
            index_dir = bot_memory / "memory_index"
            self.write_memory_version(index_dir, 1)
            position = self.exposure_position(index_dir, version=1)
            state = {
                "response_open": False,
                "response_exited": False,
                "guard_active": False,
            }
            spoken: list[tuple[str, bool, bool]] = []
            guard_entries: list[dict[str, Any]] = []

            bridge = self.configured_bridge()

            async def speak(text: str) -> None:
                spoken.append(
                    (
                        text,
                        bool(state["response_exited"]),
                        bool(state["guard_active"]),
                    )
                )

            bridge._speak = AsyncMock(side_effect=speak)
            response = _StreamingResponse(
                self.bound_events(position, delta=False),
                state=state,
                on_exit=lambda: self.assertEqual(spoken, []),
            )
            bridge.session = _Session(response)

            with patch.object(
                local_io_bridge,
                "memory_exposure_guard",
                self.observed_guard(state, guard_entries),
            ):
                result = await bridge._chat_sentence_stream_and_speak(
                    "test",
                    grant={},
                )

            self.assertEqual(result["reply"], "안녕.")
            self.assertEqual(
                spoken,
                [
                    ("첫 문장", True, True),
                    ("둘째 문장", True, True),
                ],
            )
            self.assertEqual(len(guard_entries), 1)
            self.assertEqual(
                guard_entries[0]["expected_position"],
                position,
            )
            self.assertTrue(guard_entries[0]["required"])

    async def test_sentence_stale_at_eof_never_speaks(self) -> None:
        for mode in ("version", "tombstone"):
            with self.subTest(mode=mode):
                with self.isolated_roots() as (
                    bot_memory,
                    _artifacts,
                ):
                    index_dir = bot_memory / "memory_index"
                    self.write_memory_version(index_dir, 1)
                    position = self.exposure_position(
                        index_dir,
                        version=1,
                    )
                    state = {
                        "response_open": False,
                        "response_exited": False,
                        "guard_active": False,
                    }
                    guard_entries: list[dict[str, Any]] = []
                    bridge = self.configured_bridge()
                    bridge._speak = AsyncMock()
                    response = _StreamingResponse(
                        self.bound_events(position, delta=False),
                        state=state,
                        on_exit=lambda: self.stale_memory(
                            index_dir,
                            mode,
                        ),
                    )
                    bridge.session = _Session(response)

                    with patch.object(
                        local_io_bridge,
                        "memory_exposure_guard",
                        self.observed_guard(state, guard_entries),
                    ):
                        with self.assertRaises(
                            MemoryDeletionJournalIntegrityError
                        ):
                            await bridge._chat_sentence_stream_and_speak(
                                "test",
                                grant={},
                            )

                    bridge._speak.assert_not_awaited()
                    self.assertEqual(len(guard_entries), 1)

    async def test_delta_bound_defers_tts_and_pcm_until_eof_guard(
        self,
    ) -> None:
        with self.isolated_roots() as (bot_memory, _artifacts):
            index_dir = bot_memory / "memory_index"
            self.write_memory_version(index_dir, 1)
            position = self.exposure_position(index_dir, version=1)
            state = {
                "response_open": False,
                "response_exited": False,
                "guard_active": False,
            }
            guard_entries: list[dict[str, Any]] = []
            websocket = _WebSocket(state=state)
            sound_device = _SoundDevice(state=state)
            bridge = self.configured_bridge()

            def assert_nothing_synthesized_before_exit() -> None:
                self.assertEqual(
                    [item["type"] for item in websocket.sent],
                    ["start"],
                )
                self.assertEqual(sound_device.stream.writes, [])

            response = _StreamingResponse(
                self.bound_events(position, delta=True),
                state=state,
                on_exit=assert_nothing_synthesized_before_exit,
            )
            bridge.session = _Session(response, websocket)

            with patch.object(
                local_io_bridge,
                "sd",
                sound_device,
            ), patch.object(
                local_io_bridge,
                "memory_exposure_guard",
                self.observed_guard(state, guard_entries),
            ):
                result = await bridge._chat_delta_stream_and_speak(
                    "test",
                    grant={},
                )

            self.assertEqual(result["reply"], "안녕.")
            self.assertEqual(
                [item["type"] for item in websocket.sent],
                ["start", "append", "commit", "append", "flush"],
            )
            for command, response_exited, guard_active in (
                websocket.send_observations[1:]
            ):
                with self.subTest(command=command):
                    self.assertTrue(response_exited)
                    self.assertTrue(guard_active)
            self.assertGreater(len(sound_device.stream.writes), 0)
            self.assertTrue(
                all(
                    response_exited and guard_active
                    for response_exited, guard_active
                    in sound_device.stream.write_observations
                )
            )
            self.assertEqual(len(guard_entries), 1)

    async def test_delta_stale_at_eof_never_synthesizes_or_plays(
        self,
    ) -> None:
        for mode in ("version", "tombstone"):
            with self.subTest(mode=mode):
                with self.isolated_roots() as (
                    bot_memory,
                    _artifacts,
                ):
                    index_dir = bot_memory / "memory_index"
                    self.write_memory_version(index_dir, 1)
                    position = self.exposure_position(
                        index_dir,
                        version=1,
                    )
                    state = {
                        "response_open": False,
                        "response_exited": False,
                        "guard_active": False,
                    }
                    guard_entries: list[dict[str, Any]] = []
                    websocket = _WebSocket(state=state)
                    sound_device = _SoundDevice(state=state)
                    bridge = self.configured_bridge()
                    response = _StreamingResponse(
                        self.bound_events(position, delta=True),
                        state=state,
                        on_exit=lambda: self.stale_memory(
                            index_dir,
                            mode,
                        ),
                    )
                    bridge.session = _Session(response, websocket)

                    with patch.object(
                        local_io_bridge,
                        "sd",
                        sound_device,
                    ), patch.object(
                        local_io_bridge,
                        "memory_exposure_guard",
                        self.observed_guard(state, guard_entries),
                    ):
                        with self.assertRaises(
                            MemoryDeletionJournalIntegrityError
                        ):
                            await bridge._chat_delta_stream_and_speak(
                                "test",
                                grant={},
                            )

                    self.assertEqual(
                        [item["type"] for item in websocket.sent],
                        ["start", "cancel"],
                    )
                    self.assertEqual(sound_device.stream.writes, [])
                    bridge._mark_playback_started_once.assert_not_called()
                    self.assertEqual(len(guard_entries), 1)

    async def test_non_stream_chat_missing_or_malformed_boundary_fails_closed(
        self,
    ) -> None:
        bad_payloads = (
            {"ok": True, "reply": "must not escape"},
            {
                "ok": True,
                "reply": "must not escape",
                "memoryState": "bound",
                "memoryBoundary": {"schema": "wrong"},
            },
            {
                "ok": True,
                "reply": "must not escape",
                "memoryState": "not_used",
                "memoryBoundary": {"unexpected": True},
            },
        )
        with self.isolated_roots():
            for payload in bad_payloads:
                with self.subTest(payload=payload):
                    bridge = self.configured_bridge()
                    bridge.session = _Session(_JsonResponse(payload))
                    with self.assertRaises(
                        MemoryDeletionJournalIntegrityError
                    ):
                        await bridge._chat("test", grant={})


if __name__ == "__main__":
    unittest.main()
