from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import wave
import asyncio
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

try:
    import numpy as np

    from evelyn_core.voice_debug_audio import (  # noqa: E402
        build_voice_debug_audio_item,
        debug_write_worker_from_runtime,
        enqueue_voice_debug_audio_from_runtime,
        ensure_debug_write_worker_started_from_runtime,
        inventory_voice_debug_bundles,
        purge_voice_debug_audio_for_turns,
        sanitize_debug_label,
        save_voice_debug_audio_now,
        trim_voice_debug_dir,
        trim_voice_debug_root,
        voice_debug_drop_message,
    )

    NUMPY_AVAILABLE = True
except ModuleNotFoundError:
    np = None
    NUMPY_AVAILABLE = False


@unittest.skipUnless(NUMPY_AVAILABLE, "numpy is required for voice debug audio tests")
class VoiceDebugAudioTests(unittest.TestCase):
    def test_sanitize_debug_label_keeps_filename_safe_text(self) -> None:
        self.assertEqual(sanitize_debug_label(" 정훈 / mic? "), "정훈_mic")
        self.assertEqual(sanitize_debug_label("***"), "unknown")

    def test_build_item_copies_audio_and_metadata(self) -> None:
        audio = np.array([0.1, 0.2], dtype=np.float32)
        item = build_voice_debug_audio_item(
            guild_id=1,
            speaker="user",
            pcm_bytes=b"1234",
            audio16k=audio,
            debug_meta={"turn_id": "a"},
            stt_meta={"ok": True},
        )
        audio[0] = 0.9

        self.assertAlmostEqual(float(item["audio16k"][0]), 0.1, places=6)
        self.assertEqual(item["debug_meta"], {"turn_id": "a"})
        self.assertEqual(item["stt_meta"], {"ok": True})

    def test_save_voice_debug_audio_now_writes_wavs_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs: list[str] = []
            counts: dict[int, int] = {}
            stems: dict[tuple[int, str, str, str], str] = {}
            pcm = (np.zeros(480, dtype=np.int16)).tobytes()
            audio16k = np.array([0.0, 0.5, -0.5], dtype=np.float32)

            save_voice_debug_audio_now(
                project_root=root,
                configured_dir="debug_audio",
                max_files_per_guild=20,
                raw_channels=2,
                raw_rate=48000,
                stt_rate=16000,
                counts=counts,
                stems=stems,
                log=logs.append,
                guild_id=42,
                speaker="정훈/mic",
                pcm_bytes=pcm,
                audio16k=audio16k,
                final_text="hello",
                debug_meta={"turn_id": "turn-1", "segment_id": "7"},
                stt_meta={"backend": "test"},
                session_key="session",
                stage_label="final",
            )

            guild_dir = root / "debug_audio" / "42"
            raw_path = next(guild_dir.glob("*_raw48k.wav"))
            stt_path = next(guild_dir.glob("*_stt16k.wav"))
            meta_path = next(guild_dir.glob("*.json"))
            meta = json.loads(meta_path.read_text(encoding="utf-8"))

            with wave.open(str(raw_path), "rb") as raw_wav:
                self.assertEqual(raw_wav.getnchannels(), 2)
                self.assertEqual(raw_wav.getframerate(), 48000)
            with wave.open(str(stt_path), "rb") as stt_wav:
                self.assertEqual(stt_wav.getnchannels(), 1)
                self.assertEqual(stt_wav.getframerate(), 16000)
            self.assertEqual(meta["final_text"], "hello")
            self.assertEqual(meta["turn_id"], "turn-1")
            self.assertEqual(meta["segment_id"], 7)
            self.assertIn("[VOICE DEBUG SAVE]", logs[-1])

    def test_trim_voice_debug_dir_removes_complete_oldest_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            guild_dir = Path(temp_dir)
            for idx in range(4):
                old_time = time.time() - (10 - idx)
                for suffix in (
                    "_raw48k.wav",
                    "_stt16k.wav",
                    ".pcm",
                    ".json",
                ):
                    path = guild_dir / f"{idx}{suffix}"
                    path.write_bytes(b"x")
                    os.utime(path, (old_time, old_time))

            result = trim_voice_debug_dir(guild_dir, max_files=2)

            self.assertEqual([item.stem for item in inventory_voice_debug_bundles(guild_dir)], ["2", "3"])
            self.assertEqual(result.candidate_count, 2)
            self.assertEqual(result.deleted_count, 2)
            self.assertEqual(sorted(path.name for path in guild_dir.iterdir()), [
                "2.json", "2.pcm", "2_raw48k.wav", "2_stt16k.wav",
                "3.json", "3.pcm", "3_raw48k.wav", "3_stt16k.wav",
            ])

    def test_trim_voice_debug_dir_dry_run_applies_age_without_deleting(self) -> None:
        now = 1_000_000.0
        with tempfile.TemporaryDirectory() as temp_dir:
            guild_dir = Path(temp_dir)
            for stem, mtime in (("old", now - 8 * 86400), ("new", now - 1)):
                for suffix in ("_raw48k.wav", ".json"):
                    path = guild_dir / f"{stem}{suffix}"
                    path.write_bytes(b"1234")
                    os.utime(path, (mtime, mtime))

            result = trim_voice_debug_dir(
                guild_dir,
                max_files=200,
                max_age_days=7,
                preserve_newest=1,
                dry_run=True,
                now=now,
            )

            self.assertEqual(result.candidate_count, 1)
            self.assertEqual(result.candidate_bytes, 8)
            self.assertEqual(result.deleted_count, 0)
            self.assertTrue((guild_dir / "old.json").exists())

    def test_trim_voice_debug_root_reports_each_guild_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for guild in ("1", "2"):
                guild_dir = root / guild
                guild_dir.mkdir()
                for idx in range(3):
                    path = guild_dir / f"{idx}.json"
                    path.write_text("{}", encoding="utf-8")
                    os.utime(path, (100 + idx, 100 + idx))

            result = trim_voice_debug_root(
                root,
                max_files=1,
                max_age_days=None,
                max_total_bytes_per_guild=None,
                preserve_newest=1,
                dry_run=True,
            )

            self.assertEqual(result["candidate_count"], 4)
            self.assertEqual(result["deleted_count"], 0)
            self.assertEqual(len(list(root.rglob("*.json"))), 6)

    def test_inventory_ignores_symlinks_instead_of_owning_their_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            guild_dir = Path(temp_dir)
            target = guild_dir / "keep.json"
            target.write_text("{}", encoding="utf-8")
            link = guild_dir / "alias.json"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("file symlinks are unavailable")

            bundles = inventory_voice_debug_bundles(guild_dir)

            self.assertEqual([bundle.stem for bundle in bundles], ["keep"])
            self.assertEqual(bundles[0].paths, (target,))

    def test_drop_message_is_stable(self) -> None:
        self.assertEqual(
            voice_debug_drop_message(speaker="user", stage_label="drop"),
            "[VOICE DEBUG DROP] speaker=user stage=drop reason=queue_full",
        )

    def test_ensure_worker_reuses_live_task_and_creates_done_task(self) -> None:
        class FakeTask:
            def __init__(self, done: bool) -> None:
                self._done = done

            def done(self) -> bool:
                return self._done

        created: list[object] = []
        live_task = FakeTask(done=False)
        done_task = FakeTask(done=True)

        self.assertIs(
            ensure_debug_write_worker_started_from_runtime(
                current_task=live_task,
                create_task=lambda coro: created.append(coro) or "new",
                worker_coro_factory=lambda: "worker",
            ),
            live_task,
        )
        self.assertEqual(
            ensure_debug_write_worker_started_from_runtime(
                current_task=done_task,
                create_task=lambda coro: created.append(coro) or "new",
                worker_coro_factory=lambda: "worker",
            ),
            "new",
        )
        self.assertEqual(created, ["worker"])

    def test_enqueue_voice_debug_audio_respects_enabled_and_queue_full(self) -> None:
        class FullQueue:
            def put_nowait(self, _item: dict) -> None:
                raise asyncio.QueueFull()

        started: list[bool] = []
        logs: list[str] = []
        disabled = enqueue_voice_debug_audio_from_runtime(
            enabled=False,
            ensure_worker_started=lambda: started.append(True),
            queue=FullQueue(),
            log=logs.append,
            guild_id=1,
            speaker="user",
            pcm_bytes=b"12",
            audio16k=np.array([0.0], dtype=np.float32),
            stage_label="stage",
        )
        dropped = enqueue_voice_debug_audio_from_runtime(
            enabled=True,
            ensure_worker_started=lambda: started.append(True),
            queue=FullQueue(),
            log=logs.append,
            guild_id=1,
            speaker="user",
            pcm_bytes=b"12",
            audio16k=np.array([0.0], dtype=np.float32),
            stage_label="stage",
        )

        self.assertFalse(disabled)
        self.assertFalse(dropped)
        self.assertEqual(started, [True])
        self.assertEqual(logs, ["[VOICE DEBUG DROP] speaker=user stage=stage reason=queue_full"])

    def test_archive_mode_blocks_queue_and_direct_debug_writes(self) -> None:
        class CaptureQueue:
            def __init__(self) -> None:
                self.items: list[dict] = []

            def put_nowait(self, item: dict) -> None:
                self.items.append(item)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue = CaptureQueue()
            started: list[bool] = []
            logs: list[str] = []
            with patch.dict(
                os.environ,
                {"EVELYN_CONVERSATION_ARCHIVE_ENABLED": "true"},
            ):
                admitted = enqueue_voice_debug_audio_from_runtime(
                    enabled=True,
                    ensure_worker_started=lambda: started.append(True),
                    queue=queue,
                    log=logs.append,
                    guild_id=1,
                    speaker="private-speaker",
                    pcm_bytes=b"12",
                    audio16k=np.array([0.0], dtype=np.float32),
                    stage_label="final",
                )
                save_voice_debug_audio_now(
                    project_root=root,
                    configured_dir="debug_audio",
                    max_files_per_guild=20,
                    raw_channels=2,
                    raw_rate=48000,
                    stt_rate=16000,
                    counts={},
                    stems={},
                    log=logs.append,
                    guild_id=1,
                    speaker="private-speaker",
                    pcm_bytes=b"12",
                    audio16k=np.array([0.0], dtype=np.float32),
                    stage_label="final",
                )

            self.assertFalse(admitted)
            self.assertEqual(started, [])
            self.assertEqual(queue.items, [])
            self.assertFalse((root / "debug_audio").exists())
            self.assertEqual(len(logs), 2)
            self.assertTrue(
                all("reason=conversation_archive_enabled" in row for row in logs)
            )
            self.assertNotIn("private-speaker", " ".join(logs))

    def test_exact_turn_purge_returns_content_free_generation_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            guild_dir = root / "42"
            guild_dir.mkdir()
            for stem, turn_id in (
                ("target", "PRIVATE-turn-target"),
                ("survivor", "turn-survivor"),
            ):
                (guild_dir / f"{stem}_raw48k.wav").write_bytes(b"raw")
                (guild_dir / f"{stem}_stt16k.wav").write_bytes(b"stt")
                (guild_dir / f"{stem}.json").write_text(
                    json.dumps({"turn_id": turn_id}),
                    encoding="utf-8",
                )

            receipt = purge_voice_debug_audio_for_turns(
                root,
                deletion_generation=9,
                turn_ids=("PRIVATE-turn-target",),
                guild_id=42,
            )

            self.assertTrue(receipt["complete"])
            self.assertEqual(receipt["deletionGeneration"], 9)
            self.assertEqual(receipt["matchedCount"], 1)
            self.assertEqual(receipt["deletedCount"], 1)
            self.assertFalse((guild_dir / "target.json").exists())
            self.assertFalse((guild_dir / "target_raw48k.wav").exists())
            self.assertTrue((guild_dir / "survivor.json").exists())
            self.assertNotIn(
                "PRIVATE-turn-target",
                json.dumps(receipt, ensure_ascii=False),
            )

    def test_unattributed_debug_bundle_keeps_purge_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            guild_dir = root / "7"
            guild_dir.mkdir()
            (guild_dir / "orphan_raw48k.wav").write_bytes(b"raw")

            receipt = purge_voice_debug_audio_for_turns(
                root,
                deletion_generation=3,
                turn_ids=("turn-1",),
            )

            self.assertFalse(receipt["complete"])
            self.assertEqual(receipt["status"], "cleanup_pending")
            self.assertEqual(receipt["unresolvedCount"], 1)
            self.assertTrue((guild_dir / "orphan_raw48k.wav").exists())


@unittest.skipUnless(NUMPY_AVAILABLE, "numpy is required for voice debug audio tests")
class VoiceDebugAudioWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_debug_worker_saves_item_and_marks_done(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        saved: list[dict] = []
        await queue.put({"guild_id": 1})

        async def fake_to_thread(save_now, **item):
            saved.append(dict(item))
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await debug_write_worker_from_runtime(
                queue=queue,
                save_now=lambda **_item: None,
                to_thread=fake_to_thread,
                log=lambda _message: None,
            )

        self.assertEqual(saved, [{"guild_id": 1}])
        self.assertEqual(queue._unfinished_tasks, 0)


if __name__ == "__main__":
    unittest.main()
