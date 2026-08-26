from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
TOOL_PATH = REPO_ROOT / "tools" / "gpu1_latency_benchmark.py"
SPEC = importlib.util.spec_from_file_location("gpu1_latency_benchmark", TOOL_PATH)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


class Gpu1LatencyBenchmarkTests(unittest.TestCase):
    def args(self):
        return benchmark.parse_args([])

    @staticmethod
    def iteration(
        *,
        main_ms: float = 700.0,
        qwen_ms: float = 3_000.0,
        stt_ms: float = 900.0,
        free_mb: float = 3_000.0,
    ) -> dict:
        return {
            "main": {
                "ok": True,
                "ttftMs": main_ms,
                "totalMs": main_ms + 20,
                "promptEvalMs": 12.0,
                "promptTokensPerSec": 7_160.0,
                "promptCacheHitRatio": 0.95,
            },
            "qwen": {"ok": True, "timedOut": False, "latencyMs": qwen_ms},
            "stt": {"ok": True, "latencyMs": stt_ms, "serviceDurationMs": stt_ms - 10},
            "gpu": {
                "samples": [
                    {
                        "usedMb": 20_000.0,
                        "freeMb": free_mb,
                        "totalMb": 24_576.0,
                        "utilizationPct": 80.0,
                    }
                ],
                "errorCount": 0,
            },
        }

    def test_report_passes_only_when_every_concurrent_budget_passes(self) -> None:
        args = self.args()
        report = benchmark.build_report(
            args,
            iterations=[self.iteration() for _ in range(args.iterations)],
            audio_metadata={
                "sha256": benchmark.GPU1_BENCHMARK_AUDIO_SHA256,
                "durationMs": 1_640.0,
                "sampleCount": 26_240,
            },
            generated_at=1_000.0,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["violations"], [])
        self.assertNotIn("audio_f32_base64", str(report))

    def test_qwen_timeout_and_stt_overrun_fail_the_report(self) -> None:
        args = self.args()
        rows = [self.iteration(stt_ms=1_201.0) for _ in range(args.iterations)]
        rows[0]["qwen"] = {
            "ok": False,
            "timedOut": True,
            "latencyMs": 6_000.0,
            "errorType": "TimeoutError",
        }

        report = benchmark.build_report(
            args,
            iterations=rows,
            audio_metadata={
                "sha256": benchmark.GPU1_BENCHMARK_AUDIO_SHA256,
                "durationMs": 1_640.0,
                "sampleCount": 26_240,
            },
            generated_at=1_000.0,
        )

        self.assertEqual(report["status"], "fail")
        self.assertIn("qwen_timeout_budget_exceeded", report["violations"])
        self.assertIn("stt_final_latency_budget_exceeded", report["violations"])

    def test_short_smoke_cannot_create_a_passing_report(self) -> None:
        args = self.args()
        args.iterations = 1
        report = benchmark.build_report(
            args,
            iterations=[self.iteration()],
            audio_metadata={
                "sha256": benchmark.GPU1_BENCHMARK_AUDIO_SHA256,
                "durationMs": 1_640.0,
                "sampleCount": 26_240,
            },
            generated_at=1_000.0,
        )

        self.assertEqual(report["status"], "fail")
        self.assertIn("insufficient_samples", report["violations"])

    def test_bundled_audio_is_the_reproducible_pcm16_fixture(self) -> None:
        payload, metadata = benchmark._load_audio(benchmark.DEFAULT_AUDIO)
        self.assertEqual(payload["sampling_rate"], 16_000)
        self.assertEqual(payload["sample_count"], 26_240)
        self.assertEqual(metadata["durationMs"], 1_640.0)

    def test_server_timings_expose_prefill_and_prompt_cache_separately(self) -> None:
        metrics = benchmark.extract_llama_timing_metrics(
            {
                "timings": {
                    "prompt_n": 10,
                    "cache_n": 190,
                    "prompt_ms": 1.4,
                    "prompt_per_token_ms": 0.14,
                    "prompt_per_second": 7_160.2,
                    "predicted_n": 4,
                    "predicted_ms": 32.8,
                    "predicted_per_token_ms": 8.2,
                    "predicted_per_second": 122.3,
                }
            }
        )

        self.assertEqual(metrics["promptTokensTotal"], 200)
        self.assertEqual(metrics["promptCacheHitRatio"], 0.95)
        self.assertEqual(metrics["promptTokensPerSec"], 7_160.2)
        self.assertEqual(metrics["predictedTokens"], 4)
        self.assertEqual(metrics["predictedMs"], 32.8)

        summary = benchmark.summarize([self.iteration()])
        self.assertEqual(summary["mainPromptEvalP95Ms"], 12.0)
        self.assertEqual(summary["mainPromptTokensPerSecAvg"], 7_160.0)
        self.assertEqual(summary["mainPromptCacheHitRatioMin"], 0.95)

    def test_server_timing_parser_uses_usage_fallback_and_drops_private_fields(self) -> None:
        metrics = benchmark.extract_llama_timing_metrics(
            {
                "timings": {
                    "prompt_ms": 8.25,
                    "queue_ms": 2.75,
                    "prompt": "private prompt",
                    "bad": float("inf"),
                },
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 7,
                    "prompt_tokens_details": {"cached_tokens": 100},
                },
                "reply": "private reply",
                "path": "C:/private/audio.wav",
            }
        )

        self.assertEqual(metrics["promptTokensProcessed"], 20)
        self.assertEqual(metrics["promptTokensCached"], 100)
        self.assertEqual(metrics["predictedTokens"], 7)
        self.assertEqual(metrics["queueMs"], 2.8)
        self.assertTrue(all(isinstance(value, (int, float)) for value in metrics.values()))
        self.assertNotIn("private", str(metrics))
        self.assertNotIn("path", metrics)

    def test_run_writes_running_state_before_measurements(self) -> None:
        writes: list[dict] = []
        with (
            patch.object(
                benchmark,
                "run_iteration",
                side_effect=lambda *_args, **_kwargs: self.iteration(),
            ),
            patch.object(
                benchmark,
                "_write_report",
                side_effect=lambda _path, report: writes.append(report),
            ),
        ):
            result = benchmark.main(["--warmup-iterations", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(writes[0]["status"], "running")
        self.assertEqual(writes[0]["violations"], ["benchmark_in_progress"])
        self.assertEqual(writes[-1]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
