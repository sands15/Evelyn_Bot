import sys
import unittest
import asyncio
import threading
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.tts_playback import (  # noqa: E402
    PreparedPlaybackStarter,
    PreparedTtsPlaybackQueue,
    SpeechCommitContractError,
    SpeechCommitGate,
    SpeechChunker,
    OmniVoicePCMStream,
    QueuedAudioSource,
    StreamingVoiceDelivery,
    TTSQueueSink,
    TtsPlaybackManager,
    TtsSourcePlaybackRequest,
    TtsStreamingPlaybackRequest,
    TtsPlaybackRegistry,
    TtsPlaybackTracker,
    add_omnivoice_stream_contract,
    clear_tts_playback_tracking,
    cleanup_tts_playback_targets,
    cleanup_tts_stream_tasks,
    configure_tts_playback_logging,
    discord_pcm_silence_bytes,
    drain_prepared_tts_playback,
    finish_tts_playback_tracking,
    get_tracked_tts_playback,
    is_tracked_tts_playback_active,
    mark_tts_playback_summary_state,
    mark_tts_speaking,
    split_tts_sentences,
    play_audio_source,
    prefetch_tts_sources,
    resolve_cached_tts_audio_path,
    start_tts_playback_tracking,
    stop_tracked_tts_playback,
    stop_tts_playback_state,
    tracked_tts_playback_count,
    tracked_tts_playback_guild_ids,
    tts_input_suppression_reason,
    update_tts_playback_tracking,
)
from evelyn_core.config import TTS_CHUNK_TAIL_SILENCE_MS  # noqa: E402
from evelyn_core.observability_metrics import (  # noqa: E402
    VOICE_LATENCY_TRACE_METRICS_KEY,
    VoiceLatencyTrace,
)


class TtsPlaybackContractTests(unittest.TestCase):
    def tearDown(self) -> None:
        configure_tts_playback_logging(lambda event, **payload: None)

    def test_playback_manager_rejects_noncurrent_target_before_binding(self) -> None:
        seen: list[dict] = []

        class FakeVc:
            guild = type("Guild", (), {"id": 123})()

            def play(self, _source, *, after) -> None:
                raise AssertionError("stale playback must not start")

        class FakeSource:
            def __init__(self) -> None:
                self.cleaned = False

            def cleanup(self) -> None:
                self.cleaned = True

        source = FakeSource()

        def reject(target: dict) -> bool:
            seen.append(target)
            return False

        manager = TtsPlaybackManager(
            target_is_current=reject,
        )
        played = asyncio.run(
            manager.play_source_once(
                TtsSourcePlaybackRequest(
                    FakeVc(),
                    source,
                    guild_id=123,
                    turn_id="turn-deleted",
                    session_key="session-deleted",
                )
            )
        )

        self.assertFalse(played)
        self.assertTrue(source.cleaned)
        self.assertFalse(manager.is_active(123))
        self.assertEqual(seen[0]["turn_id"], "turn-deleted")

    def test_play_audio_source_rechecks_target_after_idle_wait(self) -> None:
        class FakeVc:
            source = None
            play_called = False

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, _source, *, after) -> None:
                self.play_called = True
                after(None)

        class FakeSource:
            def __init__(self) -> None:
                self.cleaned = False

            def cleanup(self) -> None:
                self.cleaned = True

        vc = FakeVc()
        source = FakeSource()
        completed = asyncio.run(
            play_audio_source(
                vc,
                source,  # type: ignore[arg-type]
                target_is_current=lambda _target: False,
                target={"turn_id": "turn-deleted"},
            )
        )

        self.assertFalse(completed)
        self.assertFalse(vc.play_called)
        self.assertTrue(source.cleaned)

    def test_stream_contract_adds_request_and_playback_fields(self) -> None:
        payload = add_omnivoice_stream_contract(
            {"input": "hello"},
            request_id="req-1",
            chunk_index=2,
        )

        self.assertEqual(payload["input"], "hello")
        self.assertEqual(payload["request_id"], "req-1")
        self.assertEqual(payload["chunk_index"], 2)
        self.assertIn("stream_strategy", payload)
        self.assertIn("playback_start_buffer_ms", payload)

    def test_queued_audio_source_logs_silence_from_module_hook(self) -> None:
        events: list[tuple[str, dict]] = []

        configure_tts_playback_logging(lambda event, **payload: events.append((event, payload)))
        source = QueuedAudioSource(trace_payload={"turn_id": "turn-1"})

        frame = source.read()

        self.assertTrue(frame)
        self.assertEqual(events[0][0], "playback_underrun_silence")
        self.assertEqual(events[0][1]["turn_id"], "turn-1")
        self.assertEqual(events[0][1]["reason"], "waiting_for_prefetched_source")

    def test_omnivoice_stream_finish_adds_tail_silence(self) -> None:
        source = OmniVoicePCMStream()

        source.finish()
        frame = source.read()

        self.assertEqual(frame, discord_pcm_silence_bytes(TTS_CHUNK_TAIL_SILENCE_MS)[: len(frame)])

    def test_omnivoice_packet_callback_waits_for_send_receipt(self) -> None:
        callbacks: list[str] = []
        source = OmniVoicePCMStream(
            on_first_frame=lambda: callbacks.append("frame"),
            on_first_packet_sent=lambda: callbacks.append("packet"),
        )
        source.feed_pcm24_mono(b"\x01\x00" * 480)
        source.finish()

        frame = source.read()

        self.assertTrue(any(frame))
        self.assertEqual(callbacks, ["frame"])
        source.mark_packet_sent(frame)
        source.mark_packet_sent(frame)
        self.assertEqual(callbacks, ["frame", "packet"])

    def test_resolve_cached_tts_audio_path_returns_matching_file(self) -> None:
        path = Path(__file__)

        resolved = resolve_cached_tts_audio_path(
            "  hello  ",
            enabled=True,
            canned_text="hello",
            canned_audio_path=path,
            project_root=REPO_ROOT,
        )

        self.assertEqual(resolved, path)

    def test_resolve_cached_tts_audio_path_rejects_disabled_or_mismatch(self) -> None:
        path = Path(__file__)

        self.assertIsNone(
            resolve_cached_tts_audio_path(
                "hello",
                enabled=False,
                canned_text="hello",
                canned_audio_path=path,
                project_root=REPO_ROOT,
            )
        )
        self.assertIsNone(
            resolve_cached_tts_audio_path(
                "different",
                enabled=True,
                canned_text="hello",
                canned_audio_path=path,
                project_root=REPO_ROOT,
            )
        )

    def test_resolve_cached_tts_audio_path_uses_project_root_for_relative_path(self) -> None:
        relative_path = Path("tests") / "voice" / Path(__file__).name

        resolved = resolve_cached_tts_audio_path(
            "hello",
            enabled=True,
            canned_text="hello",
            canned_audio_path=relative_path,
            project_root=REPO_ROOT,
        )

        self.assertEqual(resolved, REPO_ROOT / relative_path)

    def test_playback_registry_tracks_and_updates_state(self) -> None:
        registry = TtsPlaybackRegistry()

        registry.set(123, turn_id="turn-1", session_key="session-1")
        registry.update(123, playback_task="task-1")

        self.assertEqual(len(registry), 1)
        self.assertIn(123, registry)
        self.assertEqual(registry.keys(), [123])
        self.assertEqual(registry.get(123)["turn_id"], "turn-1")
        self.assertEqual(registry.get(123)["playback_task"], "task-1")
        self.assertEqual(registry.pop(123)["session_key"], "session-1")
        self.assertNotIn(123, registry)

    def test_playback_tracker_groups_registry_speaking_and_audio_end_state(self) -> None:
        tracker = TtsPlaybackTracker()

        tracker.registry.set(123, turn_id="turn-1")
        tracker.speaking_guilds.add(123)
        tracker.last_audio_end_at[123] = 10.0

        self.assertEqual(tracker.registry.get(123)["turn_id"], "turn-1")
        self.assertIn(123, tracker.speaking_guilds)
        self.assertEqual(tracker.last_audio_end_at[123], 10.0)

    def test_playback_manager_wraps_tracker_state(self) -> None:
        tracker = TtsPlaybackTracker()
        manager = TtsPlaybackManager(tracker)

        manager.start(guild_id=123, mark_speaking=True, turn_id="turn-1", session_key="session-1")
        manager.update(guild_id=123, playback_task="task-1")

        self.assertEqual(manager.active_count(), 1)
        self.assertEqual(manager.active_guild_ids(), [123])
        self.assertTrue(manager.is_active(123))
        self.assertEqual(manager.get(123)["playback_task"], "task-1")
        self.assertIn(123, manager.snapshot()["speaking_guild_ids"])

        manager.finish(guild_id=123, mark_audio_end=True, now=456.0)

        self.assertFalse(manager.is_active(123))
        self.assertEqual(tracker.last_audio_end_at[123], 456.0)

    def test_playback_manager_cancel_turn_stops_matching_state_only(self) -> None:
        class FakeVc:
            def __init__(self) -> None:
                self.stopped = False

            def is_playing(self) -> bool:
                return True

            def is_paused(self) -> bool:
                return False

            def stop(self) -> None:
                self.stopped = True

        class FakeSource:
            def __init__(self) -> None:
                self.finished = False

            def finish(self) -> None:
                self.finished = True

        async def runner() -> tuple[bool, FakeVc, FakeVc, FakeSource, FakeSource, TtsPlaybackManager]:
            manager = TtsPlaybackManager()
            vc1 = FakeVc()
            vc2 = FakeVc()
            source1 = FakeSource()
            source2 = FakeSource()
            manager.start(guild_id=123, vc=vc1, playback_source=source1, turn_id="turn-1")
            manager.start(guild_id=456, vc=vc2, playback_source=source2, turn_id="turn-2")

            stopped = await manager.cancel_turn("turn-1", now=99.0)
            return stopped, vc1, vc2, source1, source2, manager

        stopped, vc1, vc2, source1, source2, manager = asyncio.run(runner())

        self.assertTrue(stopped)
        self.assertTrue(vc1.stopped)
        self.assertTrue(source1.finished)
        self.assertFalse(vc2.stopped)
        self.assertFalse(source2.finished)
        self.assertFalse(manager.is_active(123))
        self.assertTrue(manager.is_active(456))

    def test_qualified_cancel_marks_only_exact_active_source_metrics(self) -> None:
        async def runner(reason: str, tracked_turn_id: str) -> tuple[dict, dict | None]:
            manager = TtsPlaybackManager()
            metrics: dict = {
                "meta": {
                    "playback_started": True,
                    "validation_session_id": "validation-1",
                    "validation_step_id": "07-barge-source",
                    "validation_attempt_id": "attempt-private-1",
                    "private_text": "must-not-leak",
                }
            }
            manager.start(
                guild_id=123,
                turn_id="turn-source",
                session_key="session-source",
            )
            manager._source_metrics[123] = (tracked_turn_id, metrics)
            source_context = manager.source_context(123)
            stopped = await manager.cancel_guild(123, reason=reason)
            self.assertTrue(stopped)
            self.assertNotIn("_source_metrics", manager.snapshot(123))
            self.assertEqual(manager._source_metrics, {})
            return metrics, source_context

        qualified, context = asyncio.run(runner("qualified_user_audio", "turn-source"))
        unrelated, _ = asyncio.run(runner("interrupt", "turn-source"))
        mismatched, mismatched_context = asyncio.run(runner("qualified_user_audio", "other-turn"))

        self.assertIs(qualified["meta"]["qualified_tts_interrupt"], True)
        self.assertNotIn("qualified_tts_interrupt", unrelated["meta"])
        self.assertNotIn("qualified_tts_interrupt", mismatched["meta"])
        self.assertEqual(context["source_turn_id"], "turn-source")
        self.assertEqual(context["source_session_key"], "session-source")
        self.assertEqual(context["validation_attempt_id"], "attempt-private-1")
        self.assertNotIn("private_text", context)
        self.assertIsNone(mismatched_context)

    def test_failed_cancel_does_not_mark_positive_source_evidence(self) -> None:
        async def runner() -> tuple[bool, dict]:
            manager = TtsPlaybackManager()
            metrics: dict = {"meta": "invalid"}
            manager.start(guild_id=123, turn_id="turn-source")
            manager._source_metrics[123] = ("turn-source", metrics)
            with patch(
                "evelyn_core.tts_playback.stop_tracked_tts_playback",
                return_value=False,
            ):
                stopped = await manager.cancel_guild(
                    123,
                    reason="qualified_user_audio",
                )
            return stopped, metrics

        stopped, metrics = asyncio.run(runner())

        self.assertFalse(stopped)
        self.assertEqual(metrics["meta"], "invalid")

    def test_real_stop_failure_returns_false_and_rolls_back_qualified_lease(self) -> None:
        class FakeVc:
            def is_playing(self) -> bool:
                return True

            def is_paused(self) -> bool:
                return False

            def stop(self) -> None:
                raise RuntimeError("device stop failed")

        class FakeSource:
            def __init__(self) -> None:
                self.finished = False

            def finish(self) -> None:
                self.finished = True

        async def runner() -> tuple[bool, dict, TtsPlaybackManager, FakeSource]:
            manager = TtsPlaybackManager()
            metrics: dict = {"meta": {"playback_started": True}}
            source = FakeSource()
            manager.start(
                guild_id=123,
                turn_id="turn-source",
                vc=FakeVc(),
                playback_source=source,
            )
            manager._source_metrics[123] = ("turn-source", metrics)

            stopped = await manager.cancel_guild(
                123,
                reason="qualified_user_audio",
            )
            return stopped, metrics, manager, source

        stopped, metrics, manager, source = asyncio.run(runner())

        self.assertFalse(stopped)
        self.assertTrue(source.finished)
        self.assertNotIn("qualified_tts_interrupt", metrics["meta"])
        self.assertTrue(manager.is_active(123))
        self.assertEqual(manager._source_metrics[123][0], "turn-source")

    def test_qualified_cancel_lease_is_visible_during_stop_and_rolls_back_on_false(self) -> None:
        async def runner(stopped_result: bool) -> tuple[dict, list[bool], bool]:
            manager = TtsPlaybackManager()
            metrics: dict = {"meta": {"playback_started": True}}
            manager.start(guild_id=123, turn_id="turn-source")
            binding = ("turn-source", metrics)
            manager._source_metrics[123] = binding
            observed: list[bool] = []

            async def stop_with_summary(**_kwargs) -> bool:
                observed.append(
                    metrics["meta"].get("qualified_tts_interrupt") is True
                )
                return stopped_result

            with patch(
                "evelyn_core.tts_playback.stop_tracked_tts_playback",
                side_effect=stop_with_summary,
            ):
                stopped = await manager.cancel_guild(
                    123,
                    reason="qualified_user_audio",
                )
            return metrics, observed, stopped

        succeeded, success_observed, success = asyncio.run(runner(True))
        failed, failure_observed, failure = asyncio.run(runner(False))

        self.assertTrue(success)
        self.assertEqual(success_observed, [True])
        self.assertIs(succeeded["meta"]["qualified_tts_interrupt"], True)
        self.assertFalse(failure)
        self.assertEqual(failure_observed, [True])
        self.assertNotIn("qualified_tts_interrupt", failed["meta"])

    def test_qualified_cancel_lease_is_visible_to_real_playback_task_finally(self) -> None:
        async def runner() -> tuple[bool, list[bool]]:
            manager = TtsPlaybackManager()
            metrics: dict = {"meta": {"playback_started": True}}
            observed_by_summary: list[bool] = []
            playback_started = asyncio.Event()

            async def playback() -> None:
                playback_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    observed_by_summary.append(
                        metrics["meta"].get("qualified_tts_interrupt") is True
                    )

            playback_task = asyncio.create_task(playback())
            await playback_started.wait()
            manager.start(
                guild_id=123,
                turn_id="turn-source",
                playback_task=playback_task,
            )
            manager._source_metrics[123] = ("turn-source", metrics)
            stopped = await manager.cancel_guild(
                123,
                reason="qualified_user_audio",
            )
            self.assertTrue(playback_task.done())
            return stopped, observed_by_summary

        stopped, observed = asyncio.run(runner())

        self.assertTrue(stopped)
        self.assertEqual(observed, [True])

    def test_qualified_cancel_before_play_start_has_no_positive_evidence(self) -> None:
        async def runner() -> tuple[bool, dict]:
            manager = TtsPlaybackManager()
            metrics: dict = {"meta": {"playback_started": False}}
            manager.start(guild_id=123, turn_id="turn-source")
            manager._source_metrics[123] = ("turn-source", metrics)

            stopped = await manager.cancel_guild(
                123,
                reason="qualified_user_audio",
            )
            return stopped, metrics

        stopped, metrics = asyncio.run(runner())

        self.assertTrue(stopped)
        self.assertNotIn("qualified_tts_interrupt", metrics["meta"])

    def test_old_finish_cannot_remove_newer_exact_source_binding(self) -> None:
        manager = TtsPlaybackManager()
        shared_metrics: dict = {"meta": {}}
        old_binding = ("turn-a", shared_metrics)
        new_binding = ("turn-b", shared_metrics)
        manager.start(guild_id=123, turn_id="turn-a")
        old_generation = manager.tracker.registry.generation(123)
        manager.start(guild_id=123, turn_id="turn-b")
        manager._source_metrics[123] = new_binding

        manager.finish(
            guild_id=123,
            source_metrics_binding=old_binding,
            playback_generation=old_generation,
        )

        self.assertIs(manager._source_metrics[123], new_binding)
        self.assertEqual(manager.get(123)["turn_id"], "turn-b")

    def test_cancel_a_cannot_clear_b_started_while_a_task_unwinds(self) -> None:
        async def runner() -> tuple[bool, TtsPlaybackManager, tuple]:
            manager = TtsPlaybackManager()
            metrics_a: dict = {"meta": {}}
            metrics_b: dict = {"meta": {}}
            binding_b = ("turn-b", metrics_b)
            started = asyncio.Event()

            async def playback_a() -> None:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    manager.start(guild_id=123, turn_id="turn-b")
                    manager._source_metrics[123] = binding_b

            task_a = asyncio.create_task(playback_a())
            await started.wait()
            manager.start(
                guild_id=123,
                turn_id="turn-a",
                playback_task=task_a,
            )
            manager._source_metrics[123] = ("turn-a", metrics_a)
            stopped = await manager.cancel_guild(
                123,
                reason="qualified_user_audio",
            )
            return stopped, manager, binding_b

        stopped, manager, binding_b = asyncio.run(runner())

        self.assertTrue(stopped)
        self.assertEqual(manager.get(123)["turn_id"], "turn-b")
        self.assertIs(manager._source_metrics[123], binding_b)

    def test_full_queue_cannot_delay_a_stop_until_after_b_replaces_it(self) -> None:
        class SharedVc:
            def __init__(self) -> None:
                self.owner = "turn-a"
                self.stop_owners: list[str] = []

            def is_playing(self) -> bool:
                return True

            def is_paused(self) -> bool:
                return False

            def stop(self) -> None:
                self.stop_owners.append(self.owner)

        async def runner() -> tuple[bool, SharedVc, TtsPlaybackRegistry]:
            registry = TtsPlaybackRegistry()
            vc = SharedVc()
            prepared_queue: asyncio.Queue[object] = asyncio.Queue(maxsize=1)
            prepared_queue.put_nowait("prepared-audio-a")
            registry.set(
                123,
                vc=vc,
                prepared_queue=prepared_queue,
                turn_id="turn-a",
            )
            generation_a = registry.generation(123)

            async def replace_with_b() -> None:
                vc.owner = "turn-b"
                registry.set(123, vc=vc, turn_id="turn-b")
                if not prepared_queue.empty():
                    prepared_queue.get_nowait()

            replacement = asyncio.create_task(replace_with_b())
            stopped = await stop_tracked_tts_playback(
                registry=registry,
                guild_id=123,
                expected_generation=generation_a,
            )
            await replacement
            return stopped, vc, registry

        stopped, vc, registry = asyncio.run(runner())

        self.assertTrue(stopped)
        self.assertEqual(vc.stop_owners, ["turn-a"])
        self.assertEqual(registry.get(123)["turn_id"], "turn-b")

    def test_stale_expected_generation_cannot_stop_replacement(self) -> None:
        class FakeVc:
            def __init__(self) -> None:
                self.stopped = False

            def is_playing(self) -> bool:
                return True

            def is_paused(self) -> bool:
                return False

            def stop(self) -> None:
                self.stopped = True

        async def runner() -> tuple[bool, FakeVc, TtsPlaybackRegistry]:
            registry = TtsPlaybackRegistry()
            registry.set(123, turn_id="turn-a")
            generation_a = registry.generation(123)
            vc_b = FakeVc()
            registry.set(123, vc=vc_b, turn_id="turn-b")

            stopped = await stop_tracked_tts_playback(
                registry=registry,
                guild_id=123,
                expected_generation=generation_a,
            )
            return stopped, vc_b, registry

        stopped, vc_b, registry = asyncio.run(runner())

        self.assertFalse(stopped)
        self.assertFalse(vc_b.stopped)
        self.assertEqual(registry.get(123)["turn_id"], "turn-b")

    def test_playback_manager_play_source_once_tracks_and_finishes(self) -> None:
        class FakeGuild:
            id = 123

        class FakeVc:
            def __init__(self) -> None:
                self.guild = FakeGuild()
                self.play_called = False

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, source: object, *, after: object) -> None:
                self.play_called = True
                while chunk := source.read():
                    source.mark_packet_sent(chunk)
                after(None)

        class FakeSource:
            error = None

            def __init__(self) -> None:
                self.cleaned = False
                self._read = False

            def read(self) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return b"nonzero-pcm"

            def is_opus(self) -> bool:
                return False

            def cleanup(self) -> None:
                self.cleaned = True

        async def runner() -> tuple[bool, FakeVc, FakeSource, dict, TtsPlaybackManager]:
            manager = TtsPlaybackManager()
            vc = FakeVc()
            source = FakeSource()
            metrics: dict = {"meta": {}}
            played = await manager.play_source_once(
                TtsSourcePlaybackRequest(
                    vc,
                    source,
                    guild_id=123,
                    turn_id="turn-once",
                    session_key="session-once",
                    metrics=metrics,
                    cleanup_source=True,
                )
            )
            return played, vc, source, metrics, manager

        played, vc, source, metrics, manager = asyncio.run(runner())

        self.assertTrue(played)
        self.assertTrue(vc.play_called)
        self.assertTrue(source.cleaned)
        self.assertEqual(metrics["meta"]["playback_started"], True)
        self.assertEqual(metrics["meta"]["playback_completed"], True)
        self.assertFalse(manager.is_active(123))
        self.assertIn(123, manager.tracker.last_audio_end_at)

    def test_play_audio_source_does_not_report_start_without_nonzero_pcm_effect(self) -> None:
        class FakeVc:
            source = None

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, source: object, *, after: object) -> None:
                self.source = source
                while source.read():
                    pass
                self.source = None
                after(None)

        class SilentSource:
            def __init__(self) -> None:
                self._chunks = iter((b"\x00" * 8, b""))

            def read(self) -> bytes:
                return next(self._chunks)

            def is_opus(self) -> bool:
                return False

        async def runner() -> tuple[bool, list[str]]:
            starts: list[str] = []
            completed = await play_audio_source(
                FakeVc(),
                SilentSource(),
                on_play_start=lambda: starts.append("started"),
            )
            return completed, starts

        completed, starts = asyncio.run(runner())

        self.assertTrue(completed)
        self.assertEqual(starts, [])

    def test_play_audio_source_marshals_receipt_before_completion(self) -> None:
        class FakeVc:
            source = None

            def __init__(self) -> None:
                self.worker: threading.Thread | None = None

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, source: object, *, after: object) -> None:
                self.source = source

                def play_on_worker() -> None:
                    chunk = source.read()
                    source.mark_packet_sent(chunk)
                    after(None)

                self.worker = threading.Thread(target=play_on_worker)
                self.worker.start()

        class AudibleSource:
            def read(self) -> bytes:
                return b"nonzero-pcm"

            def is_opus(self) -> bool:
                return False

        async def runner() -> tuple[list[tuple[str, int]], int]:
            loop_thread_id = threading.get_ident()
            events: list[tuple[str, int]] = []
            vc = FakeVc()
            await play_audio_source(
                vc,
                AudibleSource(),
                on_play_start=lambda: events.append(
                    ("started", threading.get_ident())
                ),
            )
            events.append(("completed", threading.get_ident()))
            if vc.worker is not None:
                vc.worker.join(timeout=1.0)
            return events, loop_thread_id

        events, loop_thread_id = asyncio.run(runner())

        self.assertEqual(
            events,
            [
                ("started", loop_thread_id),
                ("completed", loop_thread_id),
            ],
        )

    def test_failed_packet_send_cannot_qualify_interrupt(self) -> None:
        class FakeGuild:
            id = 123

        class FakeVc:
            def __init__(self) -> None:
                self.guild = FakeGuild()
                self.source = None
                self.playing = False
                self.frame_read = threading.Event()
                self.release_send = threading.Event()
                self.worker: threading.Thread | None = None

            def is_playing(self) -> bool:
                return self.playing

            def is_paused(self) -> bool:
                return False

            def play(self, source: object, *, after: object) -> None:
                self.source = source
                self.playing = True

                def fail_send_on_worker() -> None:
                    source.read()
                    self.frame_read.set()
                    self.release_send.wait(timeout=1.0)
                    after(RuntimeError("packet send failed"))

                self.worker = threading.Thread(target=fail_send_on_worker)
                self.worker.start()

            def stop(self) -> None:
                self.playing = False
                self.source = None
                self.release_send.set()

        class AudibleSource:
            error = None

            def read(self) -> bytes:
                return b"nonzero-pcm"

            def is_opus(self) -> bool:
                return False

            def finish(self) -> None:
                pass

        async def runner() -> tuple[bool, dict, FakeVc]:
            manager = TtsPlaybackManager()
            vc = FakeVc()
            metrics: dict = {"meta": {}}
            playback_task = asyncio.create_task(
                manager.play_source_once(
                    TtsSourcePlaybackRequest(
                        vc,
                        AudibleSource(),
                        guild_id=123,
                        turn_id="turn-send-failure",
                        metrics=metrics,
                    )
                )
            )
            await asyncio.to_thread(vc.frame_read.wait, 1.0)
            await asyncio.sleep(0)
            stopped = await manager.cancel_guild(
                123,
                reason="qualified_user_audio",
            )
            with self.assertRaises(asyncio.CancelledError):
                await playback_task
            if vc.worker is not None:
                vc.worker.join(timeout=1.0)
            return stopped, metrics, vc

        stopped, metrics, vc = asyncio.run(runner())

        self.assertTrue(stopped)
        self.assertIs(metrics["meta"]["playback_started"], False)
        self.assertIs(metrics["meta"]["playback_completed"], False)
        self.assertNotIn("qualified_tts_interrupt", metrics["meta"])
        self.assertIsNotNone(vc.worker)
        self.assertFalse(vc.worker.is_alive())

    def test_queued_packet_send_failure_emits_no_receipt(self) -> None:
        class FakeVc:
            source = None

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, source: object, *, after: object) -> None:
                self.source = source
                source.read()
                self.source = None
                after(RuntimeError("packet send failed"))

        class AudibleStream:
            error = None

            def __init__(self) -> None:
                self._read = False

            def read(self) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return b"nonzero-pcm"

            def is_exhausted(self) -> bool:
                return self._read

            def cleanup(self) -> None:
                pass

        async def runner() -> list[str]:
            starts: list[str] = []
            source = QueuedAudioSource()
            source.add_source(AudibleStream())
            source.finish()
            with self.assertRaisesRegex(RuntimeError, "packet send failed"):
                await play_audio_source(
                    FakeVc(),
                    source,
                    on_play_start=lambda: starts.append("started"),
                )
            return starts

        self.assertEqual(asyncio.run(runner()), [])

    def test_silent_source_cannot_complete_one_shot_playback(self) -> None:
        class FakeGuild:
            id = 123

        class FakeVc:
            def __init__(self) -> None:
                self.guild = FakeGuild()
                self.source = None

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, source: object, *, after: object) -> None:
                self.source = source
                while source.read():
                    pass
                self.source = None
                after(None)

        class SilentSource:
            error = None

            def __init__(self) -> None:
                self._chunks = iter((b"\x00" * 8, b""))

            def read(self) -> bytes:
                return next(self._chunks)

            def is_opus(self) -> bool:
                return False

        async def runner() -> tuple[bool, dict, TtsPlaybackManager]:
            manager = TtsPlaybackManager()
            metrics: dict = {"meta": {}}
            completed = await manager.play_source_once(
                TtsSourcePlaybackRequest(
                    FakeVc(),
                    SilentSource(),
                    guild_id=123,
                    turn_id="turn-silent",
                    metrics=metrics,
                )
            )
            return completed, metrics, manager

        completed, metrics, manager = asyncio.run(runner())

        self.assertFalse(completed)
        self.assertIs(metrics["meta"]["playback_started"], False)
        self.assertIs(metrics["meta"]["playback_completed"], False)
        self.assertNotIn(123, manager.tracker.last_audio_end_at)

    def test_unread_source_cannot_qualify_interrupt_or_complete_playback(self) -> None:
        class FakeGuild:
            id = 123

        class FakeVc:
            def __init__(self) -> None:
                self.guild = FakeGuild()
                self.source = None
                self.play_called = asyncio.Event()
                self.playing = False

            def is_playing(self) -> bool:
                return self.playing

            def is_paused(self) -> bool:
                return False

            def play(self, source: object, *, after: object) -> None:
                self.source = source
                self.playing = True
                self.play_called.set()

            def stop(self) -> None:
                self.playing = False

        class FakeSource:
            error = None

            def read(self) -> bytes:
                return b"nonzero-pcm-that-is-never-read"

            def is_opus(self) -> bool:
                return False

            def finish(self) -> None:
                pass

        async def runner() -> tuple[bool, dict, TtsPlaybackManager]:
            manager = TtsPlaybackManager()
            vc = FakeVc()
            metrics: dict = {"meta": {}}
            playback_task = asyncio.create_task(
                manager.play_source_once(
                    TtsSourcePlaybackRequest(
                        vc,
                        FakeSource(),
                        guild_id=123,
                        turn_id="turn-unread",
                        metrics=metrics,
                    )
                )
            )
            await vc.play_called.wait()
            stopped = await manager.cancel_guild(
                123,
                reason="qualified_user_audio",
            )
            with self.assertRaises(asyncio.CancelledError):
                await playback_task
            return stopped, metrics, manager

        stopped, metrics, manager = asyncio.run(runner())

        self.assertTrue(stopped)
        self.assertIs(metrics["meta"]["playback_started"], False)
        self.assertIs(metrics["meta"]["playback_completed"], False)
        self.assertNotIn("qualified_tts_interrupt", metrics["meta"])
        self.assertNotIn(123, manager.tracker.last_audio_end_at)

    def test_play_source_once_blocks_stale_validation_before_vc_play(self) -> None:
        class FakeGuild:
            id = 123

        class FakeVc:
            guild = FakeGuild()

            def __init__(self) -> None:
                self.play_called = False

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, _source: object, *, after: object) -> None:
                self.play_called = True
                after(None)

        async def runner() -> tuple[bool, FakeVc, dict, TtsPlaybackManager]:
            manager = TtsPlaybackManager()
            vc = FakeVc()
            metrics: dict = {
                "meta": {
                    "validation_session_id": "session-stale",
                    "validation_step_id": "step-stale",
                    "validation_attempt_id": "attempt-stale",
                }
            }
            with patch(
                "evelyn_core.tts_playback.validation_attempt_binding_is_current",
                return_value=False,
            ) as guard:
                played = await manager.play_source_once(
                    TtsSourcePlaybackRequest(
                        vc=vc,
                        source=object(),
                        guild_id=123,
                        turn_id="turn-stale",
                        metrics=metrics,
                    )
                )
                guard.assert_called_once_with(
                    metrics["meta"],
                    surface="discord",
                    reject_unbound_when_active=True,
                )
            return played, vc, metrics, manager

        played, vc, metrics, manager = asyncio.run(runner())

        self.assertFalse(played)
        self.assertFalse(vc.play_called)
        self.assertIs(metrics["meta"]["playback_started"], False)
        self.assertIs(metrics["meta"]["playback_completed"], False)
        self.assertNotIn(123, manager.tracker.last_audio_end_at)

    def test_playback_manager_stream_sentences_runs_prepared_playback(self) -> None:
        class FakeGuild:
            id = 123

        class FakeVc:
            def __init__(self) -> None:
                self.guild = FakeGuild()
                self.play_called = False
                self.worker: threading.Thread | None = None

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, source: object, *, after: object) -> None:
                self.play_called = True

                def consume() -> None:
                    while chunk := source.read():
                        source.mark_packet_sent(chunk)
                    after(None)

                self.worker = threading.Thread(target=consume, daemon=True)
                self.worker.start()

        class FakeSource:
            error = None

            def __init__(self) -> None:
                self.cleaned = False
                self._chunks = iter((b"nonzero-pcm", b""))

            async def wait_until_ready(self, timeout: float = 1.0) -> bool:
                return True

            def read(self) -> bytes:
                return next(self._chunks)

            def is_opus(self) -> bool:
                return False

            def is_exhausted(self) -> bool:
                return True

            def cleanup(self) -> None:
                self.cleaned = True

        async def runner() -> tuple[FakeVc, dict, list[tuple[str, int]], TtsPlaybackManager]:
            manager = TtsPlaybackManager()
            vc = FakeVc()
            sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
            await sentence_queue.put("hello")
            await sentence_queue.put(None)
            metrics: dict = {
                "meta": {},
                VOICE_LATENCY_TRACE_METRICS_KEY: VoiceLatencyTrace(),
            }
            synthesized: list[tuple[str, int]] = []

            async def synthesize_source(sentence: str, chunk_index: int) -> FakeSource:
                synthesized.append((sentence, chunk_index))
                return FakeSource()

            await manager.stream_sentences(
                TtsStreamingPlaybackRequest(
                    vc=vc,
                    sentence_queue=sentence_queue,
                    synthesize_source=synthesize_source,
                    guild_id=123,
                    turn_id="turn-stream",
                    session_key="session-stream",
                    metrics=metrics,
                    ready_timeout_sec=0.1,
                    prefetch_chunks=1,
                    lookahead_chunks=1,
                    lookahead_timeout_ms=50,
                )
            )
            return vc, metrics, synthesized, manager

        vc, metrics, synthesized, manager = asyncio.run(runner())

        self.assertTrue(vc.play_called)
        self.assertIsNotNone(vc.worker)
        vc.worker.join(timeout=1.0)
        self.assertFalse(vc.worker.is_alive())
        self.assertEqual(synthesized, [("hello", 1)])
        self.assertEqual(metrics["meta"]["playback_started"], True)
        self.assertEqual(metrics["meta"]["playback_completed"], True)
        self.assertIn(
            "playback_first_write",
            metrics[VOICE_LATENCY_TRACE_METRICS_KEY].public_summary()[
                "markers_ms"
            ],
        )
        self.assertFalse(manager.is_active(123))
        self.assertIn(123, manager.tracker.last_audio_end_at)

    def test_silent_stream_cannot_report_started_or_completed(self) -> None:
        class FakeGuild:
            id = 123

        class FakeVc:
            def __init__(self) -> None:
                self.guild = FakeGuild()
                self.worker: threading.Thread | None = None

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, source: object, *, after: object) -> None:
                def consume() -> None:
                    while chunk := source.read():
                        source.mark_packet_sent(chunk)
                    after(None)

                self.worker = threading.Thread(target=consume, daemon=True)
                self.worker.start()

        class SilentSource:
            error = None

            def __init__(self) -> None:
                self._chunks = iter((b"\x00" * 8, b""))

            async def wait_until_ready(self, timeout: float = 1.0) -> bool:
                return True

            def read(self) -> bytes:
                return next(self._chunks)

            def is_exhausted(self) -> bool:
                return True

            def cleanup(self) -> None:
                pass

        async def runner() -> tuple[FakeVc, dict, TtsPlaybackManager]:
            manager = TtsPlaybackManager()
            vc = FakeVc()
            sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
            sentence_queue.put_nowait("hello")
            sentence_queue.put_nowait(None)
            metrics: dict = {"meta": {}}

            async def synthesize_source(
                _sentence: str,
                _chunk_index: int,
            ) -> SilentSource:
                return SilentSource()

            await manager.stream_sentences(
                TtsStreamingPlaybackRequest(
                    vc=vc,
                    sentence_queue=sentence_queue,
                    synthesize_source=synthesize_source,
                    guild_id=123,
                    turn_id="turn-silent-stream",
                    metrics=metrics,
                    ready_timeout_sec=0.1,
                    prefetch_chunks=1,
                    lookahead_chunks=1,
                    lookahead_timeout_ms=50,
                )
            )
            return vc, metrics, manager

        vc, metrics, manager = asyncio.run(runner())

        self.assertIsNotNone(vc.worker)
        vc.worker.join(timeout=1.0)
        self.assertFalse(vc.worker.is_alive())
        self.assertIs(metrics["meta"]["playback_started"], False)
        self.assertIs(metrics["meta"]["playback_completed"], False)
        self.assertNotIn(123, manager.tracker.last_audio_end_at)

    def test_stream_sentences_blocks_stale_validation_before_vc_play(self) -> None:
        class FakeGuild:
            id = 123

        class FakeVc:
            guild = FakeGuild()

            def __init__(self) -> None:
                self.play_called = False

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, _source: object, *, after: object) -> None:
                self.play_called = True
                after(None)

        class FakeSource:
            error = None

            async def wait_until_ready(self, timeout: float = 1.0) -> bool:
                return True

            def read(self) -> bytes:
                return b""

            def is_exhausted(self) -> bool:
                return True

            def cleanup(self) -> None:
                return None

        async def runner() -> tuple[FakeVc, dict, TtsPlaybackManager]:
            manager = TtsPlaybackManager()
            vc = FakeVc()
            sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
            sentence_queue.put_nowait("hello")
            sentence_queue.put_nowait(None)
            metrics: dict = {
                "meta": {
                    "validation_session_id": "session-stale",
                    "validation_step_id": "step-stale",
                    "validation_attempt_id": "attempt-stale",
                }
            }

            async def synthesize_source(_sentence: str, _chunk_index: int) -> FakeSource:
                return FakeSource()

            with patch(
                "evelyn_core.tts_playback.validation_attempt_binding_is_current",
                return_value=False,
            ) as guard:
                await manager.stream_sentences(
                    TtsStreamingPlaybackRequest(
                        vc=vc,
                        sentence_queue=sentence_queue,
                        synthesize_source=synthesize_source,
                        guild_id=123,
                        turn_id="turn-stale",
                        metrics=metrics,
                        prefetch_chunks=1,
                        lookahead_chunks=1,
                        lookahead_timeout_ms=10,
                    )
                )
                guard.assert_called_once_with(
                    metrics["meta"],
                    surface="discord",
                    reject_unbound_when_active=True,
                )
            return vc, metrics, manager

        vc, metrics, manager = asyncio.run(runner())

        self.assertFalse(vc.play_called)
        self.assertIs(metrics["meta"]["playback_started"], False)
        self.assertIs(metrics["meta"]["playback_completed"], False)
        self.assertNotIn(123, manager.tracker.last_audio_end_at)

    def test_clear_tts_playback_tracking_resets_guild_state(self) -> None:
        registry = TtsPlaybackRegistry()
        registry.set(123, turn_id="turn-1")
        speaking_guilds = {123}
        last_audio_end_at = {123: 10.0}

        clear_tts_playback_tracking(
            registry=registry,
            speaking_guilds=speaking_guilds,
            last_audio_end_at=last_audio_end_at,
            guild_id=123,
        )

        self.assertNotIn(123, registry)
        self.assertNotIn(123, speaking_guilds)
        self.assertNotIn(123, last_audio_end_at)

    def test_clear_tts_playback_tracking_accepts_tracker(self) -> None:
        tracker = TtsPlaybackTracker()
        tracker.registry.set(123, turn_id="turn-1")
        tracker.speaking_guilds.add(123)
        tracker.last_audio_end_at[123] = 10.0

        clear_tts_playback_tracking(tracker=tracker, guild_id=123)

        self.assertNotIn(123, tracker.registry)
        self.assertNotIn(123, tracker.speaking_guilds)
        self.assertNotIn(123, tracker.last_audio_end_at)

    def test_finish_tts_playback_tracking_marks_audio_end(self) -> None:
        registry = TtsPlaybackRegistry()
        registry.set(123, turn_id="turn-1")
        speaking_guilds = {123}
        last_audio_end_at: dict[int, float] = {}

        finish_tts_playback_tracking(
            registry=registry,
            speaking_guilds=speaking_guilds,
            last_audio_end_at=last_audio_end_at,
            guild_id=123,
            mark_audio_end=True,
            now=123.5,
        )

        self.assertNotIn(123, registry)
        self.assertNotIn(123, speaking_guilds)
        self.assertEqual(last_audio_end_at[123], 123.5)

    def test_finish_tts_playback_tracking_accepts_tracker(self) -> None:
        tracker = TtsPlaybackTracker()
        tracker.registry.set(123, turn_id="turn-1")
        tracker.speaking_guilds.add(123)

        finish_tts_playback_tracking(
            tracker=tracker,
            guild_id=123,
            mark_audio_end=True,
            now=123.5,
        )

        self.assertNotIn(123, tracker.registry)
        self.assertNotIn(123, tracker.speaking_guilds)
        self.assertEqual(tracker.last_audio_end_at[123], 123.5)

    def test_finish_tts_playback_tracking_can_preserve_registry(self) -> None:
        tracker = TtsPlaybackTracker()
        tracker.registry.set(123, turn_id="turn-1")
        tracker.speaking_guilds.add(123)

        finish_tts_playback_tracking(
            tracker=tracker,
            guild_id=123,
            mark_audio_end=True,
            now=123.5,
            clear_registry=False,
        )

        self.assertIn(123, tracker.registry)
        self.assertNotIn(123, tracker.speaking_guilds)
        self.assertEqual(tracker.last_audio_end_at[123], 123.5)

    def test_finish_tts_playback_tracking_without_registry(self) -> None:
        speaking_guilds = {123}
        last_audio_end_at: dict[int, float] = {}

        finish_tts_playback_tracking(
            speaking_guilds=speaking_guilds,
            last_audio_end_at=last_audio_end_at,
            guild_id=123,
            mark_audio_end=True,
            now=321.25,
        )

        self.assertNotIn(123, speaking_guilds)
        self.assertEqual(last_audio_end_at[123], 321.25)

    def test_mark_tts_speaking_tracks_guild(self) -> None:
        speaking_guilds: set[int] = set()

        mark_tts_speaking(speaking_guilds=speaking_guilds, guild_id=123)
        mark_tts_speaking(speaking_guilds=speaking_guilds, guild_id=None)

        self.assertEqual(speaking_guilds, {123})

    def test_start_tts_playback_tracking_sets_registry_and_speaking(self) -> None:
        registry = TtsPlaybackRegistry()
        speaking_guilds: set[int] = set()

        state = start_tts_playback_tracking(
            registry=registry,
            speaking_guilds=speaking_guilds,
            guild_id=123,
            mark_speaking=True,
            turn_id="turn-1",
            playback_task="task-1",
        )

        self.assertIsNotNone(state)
        self.assertEqual(registry.get(123)["turn_id"], "turn-1")
        self.assertEqual(registry.get(123)["playback_task"], "task-1")
        self.assertIn(123, speaking_guilds)

    def test_start_tts_playback_tracking_accepts_tracker(self) -> None:
        tracker = TtsPlaybackTracker()

        state = start_tts_playback_tracking(
            tracker=tracker,
            guild_id=123,
            mark_speaking=True,
            turn_id="turn-1",
        )

        self.assertIsNotNone(state)
        self.assertEqual(tracker.registry.get(123)["turn_id"], "turn-1")
        self.assertIn(123, tracker.speaking_guilds)

    def test_start_tts_playback_tracking_ignores_missing_guild(self) -> None:
        registry = TtsPlaybackRegistry()
        speaking_guilds: set[int] = set()

        state = start_tts_playback_tracking(
            registry=registry,
            speaking_guilds=speaking_guilds,
            guild_id=None,
            mark_speaking=True,
            turn_id="turn-1",
        )

        self.assertIsNone(state)
        self.assertEqual(len(registry), 0)
        self.assertEqual(speaking_guilds, set())

    def test_update_tts_playback_tracking_updates_existing_state(self) -> None:
        registry = TtsPlaybackRegistry()
        registry.set(123, turn_id="turn-1", playback_task=None)

        state = update_tts_playback_tracking(
            registry=registry,
            guild_id=123,
            playback_task="task-1",
        )

        self.assertIsNotNone(state)
        self.assertEqual(registry.get(123)["turn_id"], "turn-1")
        self.assertEqual(registry.get(123)["playback_task"], "task-1")

    def test_update_tts_playback_tracking_accepts_tracker(self) -> None:
        tracker = TtsPlaybackTracker()
        tracker.registry.set(123, turn_id="turn-1", playback_task=None)

        state = update_tts_playback_tracking(
            tracker=tracker,
            guild_id=123,
            playback_task="task-1",
        )

        self.assertIsNotNone(state)
        self.assertEqual(tracker.registry.get(123)["playback_task"], "task-1")

    def test_update_tts_playback_tracking_ignores_missing_guild(self) -> None:
        registry = TtsPlaybackRegistry()

        state = update_tts_playback_tracking(
            registry=registry,
            guild_id=None,
            playback_task="task-1",
        )

        self.assertIsNone(state)
        self.assertEqual(len(registry), 0)

    def test_tracked_tts_playback_read_helpers(self) -> None:
        registry = TtsPlaybackRegistry()
        registry.set(123, turn_id="turn-1")

        self.assertEqual(get_tracked_tts_playback(registry, 123)["turn_id"], "turn-1")
        self.assertTrue(is_tracked_tts_playback_active(registry, 123))
        self.assertFalse(is_tracked_tts_playback_active(registry, None))
        self.assertEqual(tracked_tts_playback_count(registry), 1)
        self.assertEqual(tracked_tts_playback_guild_ids(registry), [123])
        self.assertIsNone(get_tracked_tts_playback(None, 123))
        self.assertEqual(tracked_tts_playback_count(None), 0)
        self.assertEqual(tracked_tts_playback_guild_ids(None), [])

    def test_tracked_tts_playback_read_helpers_accept_tracker(self) -> None:
        tracker = TtsPlaybackTracker()
        tracker.registry.set(123, turn_id="turn-1")

        self.assertEqual(get_tracked_tts_playback(tracker, 123)["turn_id"], "turn-1")
        self.assertTrue(is_tracked_tts_playback_active(tracker, 123))
        self.assertEqual(tracked_tts_playback_count(tracker), 1)
        self.assertEqual(tracked_tts_playback_guild_ids(tracker), [123])

    def test_tts_input_suppression_reason_prefers_active_speaking(self) -> None:
        reason = tts_input_suppression_reason(
            speaking_guilds={123},
            last_audio_end_at={123: 99.5},
            guild_id=123,
            post_tts_ignore_sec=2.0,
            now=100.0,
        )

        self.assertEqual(reason, "bot_is_speaking")

    def test_tts_input_suppression_reason_detects_post_tts_window(self) -> None:
        reason = tts_input_suppression_reason(
            speaking_guilds=set(),
            last_audio_end_at={123: 99.5},
            guild_id=123,
            post_tts_ignore_sec=2.0,
            now=100.0,
        )

        self.assertEqual(reason, "post_tts_ignore")

    def test_tts_input_suppression_reason_accepts_tracker(self) -> None:
        tracker = TtsPlaybackTracker()
        tracker.last_audio_end_at[123] = 99.5

        reason = tts_input_suppression_reason(
            tracker=tracker,
            guild_id=123,
            post_tts_ignore_sec=2.0,
            now=100.0,
        )

        self.assertEqual(reason, "post_tts_ignore")

    def test_tts_input_suppression_reason_allows_old_or_missing_state(self) -> None:
        self.assertIsNone(
            tts_input_suppression_reason(
                speaking_guilds=set(),
                last_audio_end_at={123: 95.0},
                guild_id=123,
                post_tts_ignore_sec=2.0,
                now=100.0,
            )
        )
        self.assertIsNone(
            tts_input_suppression_reason(
                speaking_guilds=set(),
                last_audio_end_at={},
                guild_id=None,
                post_tts_ignore_sec=2.0,
                now=100.0,
            )
        )

    def test_mark_tts_playback_summary_state_updates_metrics_meta(self) -> None:
        metrics: dict = {"meta": {}}

        mark_tts_playback_summary_state(
            metrics,
            started=True,
            completed=True,
            cancelled=False,
        )

        self.assertEqual(metrics["meta"]["playback_started"], True)
        self.assertEqual(metrics["meta"]["playback_completed"], True)
        self.assertEqual(metrics["meta"]["playback_cancelled"], False)

    def test_stop_tts_playback_state_finishes_source_and_queues(self) -> None:
        class FakeVc:
            def __init__(self) -> None:
                self.stopped = False

            def is_playing(self) -> bool:
                return True

            def is_paused(self) -> bool:
                return False

            def stop(self) -> None:
                self.stopped = True

        class FakeSource:
            def __init__(self) -> None:
                self.finished = False

            def finish(self) -> None:
                self.finished = True

        async def runner() -> tuple[FakeVc, FakeSource, asyncio.Queue, asyncio.Queue]:
            vc = FakeVc()
            source = FakeSource()
            sentence_queue: asyncio.Queue[object] = asyncio.Queue()
            prepared_queue: asyncio.Queue[object] = asyncio.Queue()
            stopped = await stop_tts_playback_state(
                {
                    "vc": vc,
                    "sentence_queue": sentence_queue,
                    "prepared_queue": prepared_queue,
                    "playback_source": source,
                }
            )
            self.assertTrue(stopped)
            return vc, source, sentence_queue, prepared_queue

        vc, source, sentence_queue, prepared_queue = asyncio.run(runner())

        self.assertTrue(vc.stopped)
        self.assertTrue(source.finished)
        self.assertTrue(sentence_queue.empty())
        self.assertTrue(prepared_queue.empty())

    def test_target_cleanup_clears_pcm_and_preserves_replacement(self) -> None:
        async def runner() -> tuple[int, int, TtsPlaybackRegistry, OmniVoicePCMStream]:
            registry = TtsPlaybackRegistry()
            target_pcm = OmniVoicePCMStream()
            target_pcm.feed_pcm24_mono(b"\x01\x00" * 64)
            target_source = QueuedAudioSource()
            target_source.add_source(target_pcm)
            sentence_queue: asyncio.Queue[object] = asyncio.Queue()
            sentence_queue.put_nowait("private sentence")
            prepared_queue: asyncio.Queue[object] = asyncio.Queue()
            prepared_queue.put_nowait((0, OmniVoicePCMStream()))
            registry.set(
                1,
                target="delete",
                sentence_queue=sentence_queue,
                prepared_queue=prepared_queue,
                playback_source=target_source,
                turn_id="turn-a",
            )
            registry.set(2, target="keep", turn_id="turn-b")

            removed, remaining = await cleanup_tts_playback_targets(
                lambda state: state.get("target") == "delete",
                registry=registry,
                cleanup_timeout_sec=1.0,
            )
            return removed, remaining, registry, target_pcm

        removed, remaining, registry, target_pcm = asyncio.run(runner())
        self.assertEqual((removed, remaining), (1, 0))
        self.assertNotIn(1, registry)
        self.assertEqual(registry.get(2)["turn_id"], "turn-b")
        self.assertTrue(target_pcm._queue.empty())
        self.assertEqual(target_pcm._buffer, bytearray())
        self.assertEqual(target_pcm._input_remainder, b"")

    def test_stop_tracked_tts_playback_stops_and_clears_tracking(self) -> None:
        class FakeVc:
            def __init__(self) -> None:
                self.stopped = False

            def is_playing(self) -> bool:
                return True

            def is_paused(self) -> bool:
                return False

            def stop(self) -> None:
                self.stopped = True

        class FakeSource:
            def __init__(self) -> None:
                self.finished = False

            def finish(self) -> None:
                self.finished = True

        async def runner() -> tuple[bool, FakeVc, FakeSource, set[int], dict[int, float], TtsPlaybackRegistry]:
            registry = TtsPlaybackRegistry()
            vc = FakeVc()
            source = FakeSource()
            speaking_guilds = {123}
            last_audio_end_at: dict[int, float] = {}
            registry.set(123, vc=vc, playback_source=source, turn_id="turn-1")

            stopped = await stop_tracked_tts_playback(
                registry=registry,
                speaking_guilds=speaking_guilds,
                last_audio_end_at=last_audio_end_at,
                guild_id=123,
                now=456.75,
            )
            return stopped, vc, source, speaking_guilds, last_audio_end_at, registry

        stopped, vc, source, speaking_guilds, last_audio_end_at, registry = asyncio.run(runner())

        self.assertTrue(stopped)
        self.assertTrue(vc.stopped)
        self.assertTrue(source.finished)
        self.assertNotIn(123, registry)
        self.assertNotIn(123, speaking_guilds)
        self.assertEqual(last_audio_end_at[123], 456.75)

    def test_stop_tracked_tts_playback_returns_false_without_state(self) -> None:
        stopped = asyncio.run(
            stop_tracked_tts_playback(
                registry=TtsPlaybackRegistry(),
                speaking_guilds=set(),
                last_audio_end_at={},
                guild_id=123,
            )
        )

        self.assertFalse(stopped)

    def test_tts_queue_sink_cleans_and_closes_sentence_queue(self) -> None:
        async def runner() -> tuple[asyncio.Queue, TTSQueueSink]:
            sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
            sink = TTSQueueSink(sentence_queue)
            await sink.on_chunk("  hello  ")
            await sink.on_chunk("   ")
            await sink.close("hello")
            return sentence_queue, sink

        sentence_queue, sink = asyncio.run(runner())

        self.assertEqual(sink.queued_sentence_count, 1)
        self.assertEqual(sentence_queue.get_nowait(), "hello")
        self.assertIsNone(sentence_queue.get_nowait())

    def test_streaming_voice_delivery_finalizes_playback_task(self) -> None:
        async def runner() -> tuple[int, list[str]]:
            sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
            sink = TTSQueueSink(sentence_queue)
            seen: list[str] = []

            async def playback() -> None:
                while True:
                    item = await sentence_queue.get()
                    if item is None:
                        return
                    seen.append(item)

            playback_task = asyncio.create_task(playback())
            delivery = StreamingVoiceDelivery(
                sentence_queue,
                sink,
                playback_task,
                metrics={},
            )
            await delivery.on_chunk("hello")
            await delivery.close("hello")
            count = await delivery.finalize()
            return count, seen

        count, seen = asyncio.run(runner())

        self.assertEqual(count, 1)
        self.assertEqual(seen, ["hello"])

    def test_play_audio_source_logs_invocation_and_finish(self) -> None:
        class FakeVc:
            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, _source, *, after) -> None:
                after(None)

        events: list[tuple[str, dict]] = []
        configure_tts_playback_logging(lambda event, **payload: events.append((event, payload)))

        asyncio.run(play_audio_source(FakeVc(), object(), trace_payload={"turn_id": "turn-1"}))  # type: ignore[arg-type]

        self.assertEqual(events[0][0], "discord_playback_play_invoked")
        self.assertEqual(events[0][1]["turn_id"], "turn-1")
        self.assertEqual(events[-1][0], "discord_playback_finished")

    def test_play_audio_source_redacts_dynamic_errors(self) -> None:
        private_error = "PRIVATE_DISCORD_PLAYBACK:/synthetic/voice-cache.wav"

        for stage in ("vc_play", "after_play", "source_error"):
            with self.subTest(stage=stage):
                events: list[tuple[str, dict]] = []
                configure_tts_playback_logging(
                    lambda event, **payload: events.append((event, payload))
                )
                source = OmniVoicePCMStream() if stage == "source_error" else object()
                if isinstance(source, OmniVoicePCMStream):
                    source.error = RuntimeError(private_error)

                class FakeVc:
                    def is_playing(self) -> bool:
                        return False

                    def is_paused(self) -> bool:
                        return False

                    def play(self, _source, *, after) -> None:
                        if stage == "vc_play":
                            raise RuntimeError(private_error)
                        after(
                            RuntimeError(private_error)
                            if stage == "after_play"
                            else None
                        )

                with self.assertRaisesRegex(
                    RuntimeError,
                    "PRIVATE_DISCORD_PLAYBACK",
                ):
                    asyncio.run(
                        play_audio_source(FakeVc(), source)  # type: ignore[arg-type]
                    )

                event, event_payload = events[-1]
                self.assertEqual(event, "discord_playback_exception")
                self.assertEqual(event_payload["stage"], stage)
                self.assertEqual(event_payload["error"], "discord_playback_failed")
                self.assertEqual(event_payload["error_type"], "RuntimeError")
                self.assertNotIn(private_error, repr(events))

    def test_play_audio_source_stops_stuck_prior_playback_on_timeout(self) -> None:
        class FakeVc:
            stop_count = 0

            def __init__(self) -> None:
                self.source = object()

            def is_playing(self) -> bool:
                return True

            def is_paused(self) -> bool:
                return False

            def play(self, _source, *, after) -> None:
                raise AssertionError("new playback must not start while the prior source is stuck")

            def stop(self) -> None:
                self.stop_count += 1

        vc = FakeVc()
        events: list[tuple[str, dict]] = []
        configure_tts_playback_logging(lambda event, **payload: events.append((event, payload)))

        with self.assertRaisesRegex(TimeoutError, "discord_playback_idle_timeout"):
            asyncio.run(play_audio_source(vc, object(), timeout_sec=0.01))  # type: ignore[arg-type]

        self.assertEqual(vc.stop_count, 1)
        self.assertEqual(events[-1][0], "discord_playback_exception")
        self.assertEqual(events[-1][1]["stage"], "wait_until_idle")

    def test_play_audio_source_stops_when_after_callback_is_lost(self) -> None:
        class FakeVc:
            stop_count = 0

            def __init__(self) -> None:
                self.source = None

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, source, *, after) -> None:
                self.source = source
                return None

            def stop(self) -> None:
                self.stop_count += 1

        vc = FakeVc()
        events: list[tuple[str, dict]] = []
        configure_tts_playback_logging(lambda event, **payload: events.append((event, payload)))

        with self.assertRaisesRegex(TimeoutError, "discord_playback_callback_timeout"):
            asyncio.run(play_audio_source(vc, object(), timeout_sec=0.01))  # type: ignore[arg-type]

        self.assertEqual(vc.stop_count, 1)
        self.assertEqual(events[-1][0], "discord_playback_exception")
        self.assertEqual(events[-1][1]["stage"], "after_play")

    def test_play_audio_source_cancellation_stops_only_matching_source(self) -> None:
        class FakeVc:
            def __init__(self) -> None:
                self.source = None
                self.stop_count = 0
                self.started = asyncio.Event()

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, source, *, after) -> None:
                self.source = source
                self.started.set()

            def stop(self) -> None:
                self.stop_count += 1
                self.source = None

        async def runner(*, replace_source: bool):
            vc = FakeVc()
            source = object()
            replacement = object()
            task = asyncio.create_task(
                play_audio_source(vc, source, timeout_sec=30.0)  # type: ignore[arg-type]
            )
            await vc.started.wait()
            if replace_source:
                vc.source = replacement
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return vc, source, replacement

        matching, source, _replacement = asyncio.run(
            runner(replace_source=False)
        )
        self.assertEqual(matching.stop_count, 1)
        self.assertIsNone(matching.source)

        replaced, _source, replacement = asyncio.run(
            runner(replace_source=True)
        )
        self.assertEqual(replaced.stop_count, 0)
        self.assertIs(replaced.source, replacement)

    def test_late_timeout_does_not_stop_newer_playback(self) -> None:
        replacement = object()

        class FakeVc:
            stop_count = 0

            def __init__(self) -> None:
                self.source = None

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, source, *, after) -> None:
                self.source = source
                asyncio.get_running_loop().call_soon(
                    setattr,
                    self,
                    "source",
                    replacement,
                )

            def stop(self) -> None:
                self.stop_count += 1

        vc = FakeVc()
        with self.assertRaisesRegex(
            TimeoutError,
            "discord_playback_callback_timeout",
        ):
            asyncio.run(
                play_audio_source(
                    vc,
                    object(),  # type: ignore[arg-type]
                    timeout_sec=0.01,
                )
            )

        self.assertIs(vc.source, replacement)
        self.assertEqual(vc.stop_count, 0)

    def test_speech_chunker_dispatches_on_natural_sentence_end(self) -> None:
        chunker = SpeechChunker()

        chunks = chunker.push("오늘은 여기까지 하면 된다고 생각해. 다음은 나중에 보자.", max_chunks=None)

        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "오늘은 여기까지 하면 된다고 생각해.")

    def test_speech_chunker_does_not_force_an_unsafe_character_cut(self) -> None:
        self.assertEqual(SpeechChunker().push("가" * 120, max_chunks=None), [])

    def test_speech_commit_gate_binds_current_immutable_prefixes(self) -> None:
        generation = object()
        gate = SpeechCommitGate(
            turn_id="turn-1",
            response_generation=generation,
            generation_is_current=lambda value: value is generation,
        )

        first = gate.push(
            "오늘은 여기까지 하면 된다고 생각해. 다음은 나중에 보자."
        )
        tail = gate.finish(
            "오늘은 여기까지 하면 된다고 생각해. 다음은 나중에 보자."
        )

        commits = first + tail
        self.assertEqual([item.prefix_index for item in commits], [0, 1])
        self.assertTrue(all(item.turn_id == "turn-1" for item in commits))
        self.assertTrue(all(item.response_generation is generation for item in commits))
        self.assertTrue(all(len(item.prefix_hash) == 64 for item in commits))
        self.assertEqual(
            gate.committed_prefix,
            "오늘은 여기까지 하면 된다고 생각해. 다음은 나중에 보자.",
        )

    def test_speech_commit_gate_fails_closed_for_stale_generation(self) -> None:
        gate = SpeechCommitGate(
            turn_id="turn-stale",
            response_generation=object(),
            generation_is_current=lambda _value: False,
        )

        self.assertEqual(
            gate.push("오늘은 여기까지 하면 된다고 생각해."),
            [],
        )
        self.assertTrue(gate.stale)
        self.assertTrue(gate.closed)

    def test_memory_bound_speech_waits_for_explicit_handoff(self) -> None:
        generation = object()
        handoff_ready = False
        answer = "오늘은 여기까지 하면 된다고 생각해."
        gate = SpeechCommitGate(
            turn_id="turn-memory",
            response_generation=generation,
            generation_is_current=lambda value: value is generation,
            commit_allowed=lambda: handoff_ready,
            memory_bound=True,
        )

        self.assertEqual(gate.push(answer), [])
        self.assertEqual(gate.finish(answer), [])
        self.assertFalse(gate.closed)
        handoff_ready = True
        commits = gate.finish(answer)

        self.assertEqual([item.text for item in commits], [answer])
        self.assertTrue(gate.closed)

    def test_speech_commit_gate_rejects_rewritten_final_prefix(self) -> None:
        generation = object()
        gate = SpeechCommitGate(
            turn_id="turn-rewrite",
            response_generation=generation,
            generation_is_current=lambda value: value is generation,
        )
        gate.push("오늘은 여기까지 하면 된다고 생각해.")

        with self.assertRaisesRegex(
            SpeechCommitContractError,
            "immutable final prefix",
        ):
            gate.finish("오늘은 전혀 다른 답을 하겠어.")

    def test_speech_commit_gate_binds_post_policy_candidates_and_final(self) -> None:
        generation = object()
        gate = SpeechCommitGate(
            turn_id="turn-shaped",
            response_generation=generation,
            generation_is_current=lambda value: value is generation,
        )

        gate.observe_safe_delta("첫 문장이야.")
        first = gate.commit_candidate("첫 문장이야.")
        gate.observe_safe_delta("둘째 문장이야.")
        second = gate.commit_candidate("둘째 문장이야.")
        gate.validate_final("첫 문장이야. 둘째 문장이야.")

        self.assertEqual(
            [commit.prefix_index for commit in first + second],
            [0, 1],
        )
        self.assertNotEqual(first[0].prefix_hash, second[0].prefix_hash)
        self.assertTrue(gate.closed)

    def test_memory_bound_candidate_requires_current_handoff(self) -> None:
        generation = object()
        handoff_ready = False
        gate = SpeechCommitGate(
            turn_id="turn-shaped-memory",
            response_generation=generation,
            generation_is_current=lambda value: value is generation,
            commit_allowed=lambda: handoff_ready,
            memory_bound=True,
        )

        gate.observe_safe_delta("기억을 반영한 답이야.")
        self.assertEqual(gate.commit_candidate("기억을 반영한 답이야."), [])
        handoff_ready = True
        commits = gate.commit_candidate("기억을 반영한 답이야.")
        gate.validate_final("기억을 반영한 답이야.")

        self.assertEqual([commit.text for commit in commits], ["기억을 반영한 답이야."])

    def test_split_tts_sentences_preserves_tail_until_forced(self) -> None:
        chunks, tail = split_tts_sentences("그리고", force=False)

        self.assertEqual(chunks, [])
        self.assertEqual(tail, "그리고")

        forced_chunks, forced_tail = split_tts_sentences("그리고", force=True)

        self.assertEqual(forced_chunks, ["그리고"])
        self.assertEqual(forced_tail, "")

    def test_prefetch_tts_sources_skips_empty_and_queues_ready_sources(self) -> None:
        class FakeSource:
            def __init__(self, text: str, chunk_index: int) -> None:
                self.text = text
                self.chunk_index = chunk_index
                self.ready_timeout: float | None = None

            async def wait_until_ready(self, *, timeout: float) -> bool:
                self.ready_timeout = timeout
                return True

        async def runner() -> asyncio.Queue:
            sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
            prepared_queue: asyncio.Queue[object] = asyncio.Queue()
            await sentence_queue.put("   ")
            await sentence_queue.put("hello")
            await sentence_queue.put(None)

            async def synthesize(text: str, chunk_index: int) -> FakeSource:
                return FakeSource(text, chunk_index)

            await prefetch_tts_sources(
                sentence_queue,
                prepared_queue,
                synthesize_source=synthesize,
                ready_timeout_sec=3.0,
            )
            return prepared_queue

        prepared_queue = asyncio.run(runner())
        chunk_index, source = prepared_queue.get_nowait()

        self.assertEqual(chunk_index, 1)
        self.assertEqual(source.text, "hello")
        self.assertEqual(source.chunk_index, 1)
        self.assertEqual(source.ready_timeout, 3.0)
        self.assertIsNone(prepared_queue.get_nowait())

    def test_prefetch_tts_sources_reports_failure_as_prepared_item(self) -> None:
        errors: list[Exception] = []

        async def runner() -> asyncio.Queue:
            sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
            prepared_queue: asyncio.Queue[object] = asyncio.Queue()
            await sentence_queue.put("hello")

            async def synthesize(_text: str, _chunk_index: int) -> object:
                raise RuntimeError("tts failed")

            await prefetch_tts_sources(
                sentence_queue,
                prepared_queue,
                synthesize_source=synthesize,
                ready_timeout_sec=3.0,
                on_failure=errors.append,
            )
            return prepared_queue

        prepared_queue = asyncio.run(runner())
        item = prepared_queue.get_nowait()

        self.assertIsInstance(item, RuntimeError)
        self.assertEqual(str(item), "tts failed")
        self.assertEqual(len(errors), 1)

    def test_prepared_playback_queue_handles_sources_and_sentinel(self) -> None:
        class FakePlaybackSource:
            def __init__(self) -> None:
                self.sources: list[object] = []
                self.finished = False

            def add_source(self, source: object) -> None:
                self.sources.append(source)

            def finish(self) -> None:
                self.finished = True

        ready_count = 0

        def on_ready() -> None:
            nonlocal ready_count
            ready_count += 1

        playback_source = FakePlaybackSource()
        queue = PreparedTtsPlaybackQueue(
            asyncio.Queue(),
            playback_source,  # type: ignore[arg-type]
            on_source_ready=on_ready,
        )
        source = object()

        self.assertEqual(queue.handle_prepared_item((1, source)), "source")
        self.assertEqual(queue.prepared_source_count, 1)
        self.assertEqual(playback_source.sources, [source])
        self.assertEqual(ready_count, 1)
        self.assertEqual(queue.handle_prepared_item(None), "done")
        self.assertTrue(queue.playback_finished)
        self.assertTrue(playback_source.finished)

    def test_prepared_playback_queue_raises_prepared_exception(self) -> None:
        class FakePlaybackSource:
            def __init__(self) -> None:
                self.finished = False

            def add_source(self, _source: object) -> None:
                raise AssertionError("should not add source")

            def finish(self) -> None:
                self.finished = True

        failures: list[Exception] = []
        playback_source = FakePlaybackSource()
        queue = PreparedTtsPlaybackQueue(
            asyncio.Queue(),
            playback_source,  # type: ignore[arg-type]
            on_failure=failures.append,
        )

        with self.assertRaisesRegex(RuntimeError, "prepared failed"):
            queue.handle_prepared_item(RuntimeError("prepared failed"))

        self.assertTrue(queue.playback_finished)
        self.assertTrue(playback_source.finished)
        self.assertEqual(len(failures), 1)

    def test_prepared_playback_queue_logs_lookahead_timeout(self) -> None:
        class FakePlaybackSource:
            def add_source(self, _source: object) -> None:
                raise AssertionError("should not add source")

            def finish(self) -> None:
                pass

        events: list[tuple[str, dict]] = []
        configure_tts_playback_logging(lambda event, **payload: events.append((event, payload)))

        async def runner() -> None:
            queue = PreparedTtsPlaybackQueue(
                asyncio.Queue(),
                FakePlaybackSource(),  # type: ignore[arg-type]
                turn_id="turn-1",
                session_key="session-1",
                lookahead_chunks=2,
                lookahead_timeout_ms=1,
            )
            await queue.fill_initial_lookahead()

        asyncio.run(runner())

        self.assertEqual(events[0][0], "tts_playback_lookahead_timeout")
        self.assertEqual(events[0][1]["turn_id"], "turn-1")
        self.assertEqual(events[0][1]["target_sources"], 2)

    def test_cleanup_tts_stream_tasks_finishes_and_cancels_tasks(self) -> None:
        class FakeSource:
            def __init__(self) -> None:
                self.finished = False

            def finish(self) -> None:
                self.finished = True

        async def sleeper() -> None:
            await asyncio.sleep(60)

        async def runner() -> tuple[FakeSource, asyncio.Task, asyncio.Task]:
            source = FakeSource()
            playback_task = asyncio.create_task(sleeper())
            prefetch_task = asyncio.create_task(sleeper())

            await cleanup_tts_stream_tasks(
                playback_source=source,
                playback_task=playback_task,
                prefetch_task=prefetch_task,
            )
            return source, playback_task, prefetch_task

        source, playback_task, prefetch_task = asyncio.run(runner())

        self.assertTrue(source.finished)
        self.assertTrue(playback_task.cancelled())
        self.assertTrue(prefetch_task.cancelled())

    def test_drain_prepared_tts_playback_starts_and_awaits_playback(self) -> None:
        class FakePlaybackSource:
            def __init__(self) -> None:
                self.sources: list[object] = []
                self.finished = False

            def add_source(self, source: object) -> None:
                self.sources.append(source)

            def finish(self) -> None:
                self.finished = True

        async def runner() -> tuple[int, bool, FakePlaybackSource]:
            prepared_queue: asyncio.Queue[object] = asyncio.Queue()
            playback_source = FakePlaybackSource()
            await prepared_queue.put((1, object()))
            await prepared_queue.put(None)
            playback_queue = PreparedTtsPlaybackQueue(
                prepared_queue,
                playback_source,  # type: ignore[arg-type]
                lookahead_chunks=1,
                lookahead_timeout_ms=1,
            )
            start_count = 0
            playback_done = False
            playback_task: asyncio.Task | None = None

            async def playback() -> None:
                nonlocal playback_done
                playback_done = True

            def start_playback_once() -> None:
                nonlocal start_count, playback_task
                if playback_task is not None or playback_queue.prepared_source_count <= 0:
                    return
                start_count += 1
                playback_task = asyncio.create_task(playback())

            await drain_prepared_tts_playback(
                prepared_queue,
                playback_queue,
                start_playback_once=start_playback_once,
                get_playback_task=lambda: playback_task,
            )
            return start_count, playback_done, playback_source

        start_count, playback_done, playback_source = asyncio.run(runner())

        self.assertEqual(start_count, 1)
        self.assertTrue(playback_done)
        self.assertEqual(len(playback_source.sources), 1)
        self.assertTrue(playback_source.finished)

    def test_drain_prepared_tts_playback_honors_cancel_check(self) -> None:
        class FakePlaybackSource:
            def add_source(self, _source: object) -> None:
                raise AssertionError("should not add source")

            def finish(self) -> None:
                pass

        async def runner() -> None:
            prepared_queue: asyncio.Queue[object] = asyncio.Queue()
            playback_queue = PreparedTtsPlaybackQueue(
                prepared_queue,
                FakePlaybackSource(),  # type: ignore[arg-type]
            )

            await drain_prepared_tts_playback(
                prepared_queue,
                playback_queue,
                start_playback_once=lambda: None,
                get_playback_task=lambda: None,
                check_cancelled=lambda: (_ for _ in ()).throw(asyncio.CancelledError()),
            )

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(runner())

    def test_prepared_playback_starter_starts_once_after_source_ready(self) -> None:
        class FakePlaybackSource:
            def add_source(self, _source: object) -> None:
                pass

            def finish(self) -> None:
                pass

        playback_queue = PreparedTtsPlaybackQueue(
            asyncio.Queue(),
            FakePlaybackSource(),  # type: ignore[arg-type]
        )
        starts: list[object] = []
        started: list[object] = []
        task = object()

        starter = PreparedPlaybackStarter(
            playback_queue,
            create_playback_task=lambda: starts.append(task) or task,
            on_started=started.append,
        )

        self.assertIsNone(starter.start_once())

        playback_queue.handle_prepared_item((1, object()))
        self.assertIs(starter.start_once(), task)
        self.assertIs(starter.start_once(), task)
        self.assertTrue(starter.did_start)
        self.assertEqual(starts, [task])
        self.assertEqual(started, [task])


if __name__ == "__main__":
    unittest.main()
