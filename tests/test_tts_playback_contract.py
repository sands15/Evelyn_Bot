import sys
import unittest
import asyncio
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.tts_playback import (  # noqa: E402
    PreparedPlaybackStarter,
    PreparedTtsPlaybackQueue,
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


class TtsPlaybackContractTests(unittest.TestCase):
    def tearDown(self) -> None:
        configure_tts_playback_logging(lambda event, **payload: None)

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
        relative_path = Path("tests") / Path(__file__).name

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

            def play(self, _source: object, *, after: object) -> None:
                self.play_called = True
                after(None)

        class FakeSource:
            error = None

            def __init__(self) -> None:
                self.cleaned = False

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

    def test_playback_manager_stream_sentences_runs_prepared_playback(self) -> None:
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

            def play(self, _source: object, *, after: object) -> None:
                self.play_called = True
                after(None)

        class FakeSource:
            error = None

            def __init__(self) -> None:
                self.cleaned = False

            async def wait_until_ready(self, timeout: float = 1.0) -> bool:
                return True

            def read(self) -> bytes:
                return b""

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
            metrics: dict = {"meta": {}}
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
        self.assertEqual(synthesized, [("hello", 1)])
        self.assertEqual(metrics["meta"]["playback_started"], True)
        self.assertEqual(metrics["meta"]["playback_completed"], True)
        self.assertFalse(manager.is_active(123))
        self.assertIn(123, manager.tracker.last_audio_end_at)

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
            await stop_tts_playback_state(
                {
                    "vc": vc,
                    "sentence_queue": sentence_queue,
                    "prepared_queue": prepared_queue,
                    "playback_source": source,
                }
            )
            return vc, source, sentence_queue, prepared_queue

        vc, source, sentence_queue, prepared_queue = asyncio.run(runner())

        self.assertTrue(vc.stopped)
        self.assertTrue(source.finished)
        self.assertIsNone(sentence_queue.get_nowait())
        self.assertIsNone(prepared_queue.get_nowait())

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

    def test_play_audio_source_raises_after_play_error(self) -> None:
        class FakeVc:
            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, _source, *, after) -> None:
                after(RuntimeError("boom"))

        async def runner() -> None:
            await play_audio_source(FakeVc(), object())  # type: ignore[arg-type]

        with self.assertRaisesRegex(RuntimeError, "boom"):
            asyncio.run(runner())

    def test_speech_chunker_dispatches_on_natural_sentence_end(self) -> None:
        chunker = SpeechChunker()

        chunks = chunker.push("오늘은 여기까지 하면 된다고 생각해. 다음은 나중에 보자.", max_chunks=None)

        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "오늘은 여기까지 하면 된다고 생각해.")

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
