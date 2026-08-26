from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
TOOL_PATH = REPO_ROOT / "tools" / "post_stt_latency_benchmark.py"
SPEC = importlib.util.spec_from_file_location("post_stt_latency_benchmark_under_test", TOOL_PATH)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


class _FakeResponse:
    def __init__(self, *, lines=(), chunks=()) -> None:
        self.status = 200
        self.headers = {"Content-Type": "audio/L16"}
        self._lines = tuple(lines)
        self._chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def __iter__(self):
        return iter(self._lines)

    def read1(self, _size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def read(self, size: int) -> bytes:
        return self.read1(size)


class PostSttLatencyBenchmarkTests(unittest.TestCase):
    def test_run_once_captures_only_numeric_llama_diagnostics(self) -> None:
        chat = _FakeResponse(
            lines=(
                b'{"type":"delta","text":"x"}\n',
                b'{"type":"sentence","text":"x."}\n',
                (
                    json.dumps(
                        {
                            "type": "done",
                            "ok": True,
                            "reply": "x.",
                            "mainTiming": {
                                "promptTokensProcessed": 20,
                                "promptTokensCached": 180,
                                "promptTokensTotal": 200,
                                "promptCacheHitRatio": 0.9,
                                "promptEvalMs": 4.5,
                                "predictedTokens": 2,
                                "queueMs": 1.25,
                                "prompt": "must-not-survive",
                            },
                            "latencyTrace": {
                                "schema": "evelyn.voice-latency-trace.v1",
                                "markers_ms": {
                                    "request_received": 0.0,
                                    "raw_first_token": 123.4567,
                                    "private_prompt": "must-not-survive",
                                },
                                "durations_ms": {
                                    "main_request_written_to_raw_first_token_ms": 120.1254,
                                    "private_to_raw_first_token_ms": 1.0,
                                },
                                "text": "must-not-survive",
                            },
                            "path": "C:/private/audio.wav",
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                ),
            )
        )
        tts = _FakeResponse(chunks=(b"\x00\x01",))

        def fake_urlopen(req, **_kwargs):
            return tts if req.full_url.endswith("/speech") else chat

        with patch.object(benchmark.request, "urlopen", side_effect=fake_urlopen):
            sample = benchmark.run_once(
                phase="measured",
                index=1,
                chat_url="http://127.0.0.1/chat",
                tts_url="http://127.0.0.1/speech",
                prompt="private prompt",
                source="direct_api",
                num_step=12,
                timeout_sec=1.0,
            )

        self.assertEqual(sample["llmTimingMetrics"]["promptTokensTotal"], 200)
        self.assertEqual(sample["llmTimingMetrics"]["promptCacheHitRatio"], 0.9)
        self.assertEqual(sample["llmTimingMetrics"]["queueMs"], 1.2)
        self.assertEqual(sample["ttsContentType"], "audio/l16")
        self.assertEqual(
            sample["latencyTrace"]["durations_ms"]
            ["main_request_written_to_raw_first_token_ms"],
            120.125,
        )
        self.assertNotIn("private prompt", str(sample))
        self.assertNotIn("must-not-survive", str(sample))
        self.assertNotIn("audio.wav", str(sample))

    def test_first_pcm_mode_closes_after_the_first_audio_chunk(self) -> None:
        tts = _FakeResponse(chunks=(b"\x00\x01", b"\x02\x03"))
        result: dict = {}
        with patch.object(benchmark.request, "urlopen", return_value=tts):
            benchmark._tts_request(
                "fixed",
                url="http://127.0.0.1/speech",
                num_step=12,
                timeout_sec=1.0,
                chat_started=0.0,
                result=result,
                first_pcm_only=True,
            )
        self.assertEqual(result["bytes"], 2)
        self.assertEqual(tts._chunks, [b"\x02\x03"])

    def test_readiness_requires_cache_proof(self) -> None:
        state = {
            "runtime": {
                "services": {"chatReady": True, "mainWarmupReady": True},
                "mainWarmup": {
                    "ready": True,
                    "cacheProof": False,
                    "promptAbiProductionMatch": True,
                },
            }
        }
        self.assertFalse(benchmark._state_ready(state))
        state["runtime"]["mainWarmup"]["cacheProof"] = True
        self.assertTrue(benchmark._state_ready(state))

    def test_readiness_reports_restart_epoch_to_cache_proof_completion(self) -> None:
        state = {
            "runtime": {
                "services": {"chatReady": True, "mainWarmupReady": True},
                "mainWarmup": {
                    "ready": True,
                    "cacheProof": True,
                    "promptAbiProductionMatch": True,
                },
            }
        }

        def response(url: str, **_kwargs):
            if url.endswith("/state"):
                return 200, state
            return 200, {"ready": True, "model_loaded": True}

        with (
            patch.object(benchmark, "_json_request", side_effect=response),
            patch.object(benchmark.time, "perf_counter", side_effect=(10.0, 10.1)),
            patch.object(benchmark.time, "time", return_value=1235.0),
        ):
            observed = benchmark.wait_until_ready(
                "http://fixed/state",
                "http://fixed/health",
                timeout_sec=1.0,
                request_timeout_sec=1.0,
                startup_epoch=1234.5,
            )

        self.assertEqual(observed["startupToReadyMs"], 500.0)
        self.assertEqual(observed["observedWaitMs"], 100.0)

    def test_readiness_uses_fixed_lab_predicate_when_supplied(self) -> None:
        state = {"runtime": {"services": {"chatReady": False}}}

        def response(url: str, **_kwargs):
            if url.endswith("/state"):
                return 200, state
            return 200, {"ready": True, "model_loaded": True}

        predicate = Mock(return_value=True)
        with (
            patch.object(benchmark, "_json_request", side_effect=response),
            patch.object(benchmark.time, "perf_counter", side_effect=(10.0, 10.1)),
            patch.object(benchmark.time, "time", return_value=1235.0),
        ):
            observed = benchmark.wait_until_ready(
                "http://fixed/state",
                "http://fixed/health",
                timeout_sec=1.0,
                request_timeout_sec=1.0,
                startup_epoch=None,
                state_ready=predicate,
            )

        self.assertEqual(observed["observedWaitMs"], 100.0)
        predicate.assert_called_once_with(state)

    def test_public_metadata_strips_credentials_query_and_untrusted_media_type(self) -> None:
        self.assertEqual(
            benchmark.public_endpoint(
                "http://user:secret@127.0.0.1:8798/path?token=private#fragment"
            ),
            "http://127.0.0.1:8798/path",
        )
        self.assertEqual(
            benchmark.public_tts_media_type("audio/L16; rate=24000"),
            "audio/l16",
        )
        self.assertEqual(
            benchmark.public_tts_media_type("private/type\r\nX-Secret: value"),
            "other",
        )

    def test_shared_equivalence_key_is_cross_run_stable_without_reporting_key(self) -> None:
        key = b"k" * 32
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "equivalence.key"
            path.write_bytes(key)
            loaded = benchmark.load_equivalence_key(path)
        first = benchmark.content_fingerprint("same reply", key=loaded)
        second = benchmark.content_fingerprint("same reply", key=key)
        self.assertEqual(first, second)
        self.assertNotEqual(first, benchmark.content_fingerprint("other reply", key=key))
        self.assertNotIn("same reply", first)

    def test_required_main_diagnostics_fail_closed(self) -> None:
        sample = self._sample(
            {
                "promptTokensProcessed": 10,
                "promptTokensCached": 90,
                "promptTokensTotal": 100,
                "promptCacheHitRatio": 0.9,
                "promptEvalMs": 4.0,
            }
        )
        sample["latencyTrace"] = {
            "schema": "evelyn.voice-latency-trace.v1",
            "markers_ms": {name: float(index) for index, name in enumerate(
                sorted(benchmark._REQUIRED_TRACE_MARKERS)
            )},
            "durations_ms": {
                "main_request_written_to_raw_first_token_ms": 120.0,
                "raw_first_token_to_speech_prefix_committed_ms": 30.0,
            },
        }
        self.assertTrue(benchmark.sample_has_required_main_diagnostics(sample))
        del sample["llmTimingMetrics"]["promptEvalMs"]
        self.assertFalse(benchmark.sample_has_required_main_diagnostics(sample))

    def test_summary_reports_timing_availability_and_per_metric_sample_counts(self) -> None:
        samples = [
            self._sample(
                {"promptEvalMs": 10.0, "promptCacheHitRatio": 0.9123},
                trace_ms=120.0,
            ),
            self._sample({"promptEvalMs": 20.0, "unknownPrivateField": "secret"}),
            self._sample({}, trace_ms=180.0),
        ]

        summary = benchmark._summary(samples)

        self.assertEqual(benchmark.SCHEMA, "evelyn.post-stt-latency.v3")
        self.assertEqual(summary["llmTimings"]["availableSampleCount"], 2)
        self.assertEqual(summary["llmTimings"]["metrics"]["promptEvalMs"]["sampleCount"], 2)
        self.assertEqual(
            summary["llmTimings"]["metrics"]["promptCacheHitRatio"]["p50"],
            0.9123,
        )
        self.assertNotIn("unknownPrivateField", str(summary["llmTimings"]))
        self.assertNotIn("secret", str(summary["llmTimings"]))
        trace_summary = summary["voiceLatencyTrace"]
        self.assertEqual(trace_summary["availableSampleCount"], 2)
        self.assertEqual(
            trace_summary["durations"]
            ["main_request_written_to_raw_first_token_ms"]["p50"],
            150.0,
        )

    @staticmethod
    def _sample(timings: dict, *, trace_ms: float | None = None) -> dict:
        trace = None
        if trace_ms is not None:
            trace = {
                "schema": "evelyn.voice-latency-trace.v1",
                "markers_ms": {},
                "durations_ms": {
                    "main_request_written_to_raw_first_token_ms": trace_ms,
                },
            }
        return {
            "firstDeltaMs": 1.0,
            "firstSentenceMs": 2.0,
            "chatDoneMs": 3.0,
            "chatEofMs": 4.0,
            "ttsFirstPcmMs": 5.0,
            "postSttFirstPcmMs": 7.0,
            "postSttAllReadyMs": 8.0,
            "ttsTotalMs": 6.0,
            "audioBytes": 2,
            "replyFingerprint": "a",
            "replyChars": 2,
            "ttsInputFingerprint": "a",
            "ttsInputChars": 2,
            "llmTimingMetrics": timings,
            "latencyTrace": trace,
        }


if __name__ == "__main__":
    unittest.main()
