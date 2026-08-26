from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import main_latency_lab_contract as lab  # noqa: E402
import main_latency_lab_harness as harness  # noqa: E402
import main_latency_owned_lab_worker as worker  # noqa: E402
import main_latency_short_diagnostic as diagnostic  # noqa: E402


class MainLatencyShortDiagnosticTests(unittest.TestCase):
    @staticmethod
    def _sample() -> dict:
        return {
            "replyFingerprint": "must-not-escape",
            "ttsInputFingerprint": "must-not-escape",
            "llmPromptEvalMs": 40.0,
            "llmPromptCacheHitRatio": 0.9,
            "llmPromptTokensProcessed": 20,
            "llmPromptTokensCached": 180,
            "llmPromptTokensTotal": 200,
            "llmPredictedTokens": 4,
            "llmPredictedMs": 80.0,
            "llmPredictedTokensPerSec": 50.0,
            "llmQueueMs": 1.0,
            "routeStageMs": 3.0,
            "contextStageMs": 8.0,
            "rawFirstTokenMs": 300.0,
            "safePrefixCommitMs": 430.0,
            "ttsFirstPcmMs": 200.0,
            "answerFirstPcmMs": 800.0,
            "externalInterference": 0,
            "safetyFailure": 0,
            "qualityFailure": 0,
            "orderViolation": 0,
            "staleSpeech": 0,
            "unsafePrefix": 0,
            "errorEvents": 0,
        }

    @staticmethod
    def _direct_sample(condition: str, phase: str) -> dict:
        token_shape = {
            "cold": (200, 0),
            "capture": (0, 200),
            "resident": (0, 200),
            "afterIdle": (0, 200),
        }[phase]
        first_token_ms = {
            ("baseline", "cold"): 160.0,
            ("baseline", "capture"): 120.0,
            ("baseline", "resident"): 100.0,
            ("baseline", "afterIdle"): 110.0,
            ("candidate", "cold"): 150.0,
            ("candidate", "capture"): 110.0,
            ("candidate", "resident"): 90.0,
            ("candidate", "afterIdle"): 120.0,
        }[(condition, phase)]
        return {
            "payloadProof": "a" * 64,
            "rawFirstTokenMs": first_token_ms,
            "promptEvalMs": 40.0,
            "promptCacheHitRatio": token_shape[1] / 200,
            "promptTokensProcessed": token_shape[0],
            "promptTokensCached": token_shape[1],
            "promptTokensTotal": 200,
        }

    @staticmethod
    def _clean(run_id: str) -> dict:
        return {
            "schema": lab.CLEANUP_SCHEMA,
            "runId": run_id,
            "owner": lab.LAB_OWNER,
            "status": "clean",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 0,
            "remainingArtifacts": 0,
        }

    @classmethod
    def _global_clean(cls) -> dict:
        return cls._clean(diagnostic.adapter.GLOBAL_RECONCILE_RUN_ID)

    @staticmethod
    def _summary(count: int) -> dict:
        return {
            name: {"sampleCount": count, "p50": value, "p95": value}
            for name, value in {
                "promptEvalMs": 40.0,
                "promptCacheHitRatio": 0.9,
                "promptTokensProcessed": 20.0,
                "promptTokensCached": 180.0,
                "promptTokensTotal": 200.0,
                "queueMs": 1.0,
                "routeMs": 3.0,
                "contextMs": 8.0,
                "firstTokenMs": 300.0,
                "safePrefixCommitMs": 430.0,
                "ttsFirstPcmMs": 200.0,
                "answerFirstPcmMs": 800.0,
                "predictedTokens": 4.0,
                "predictedMs": 80.0,
                "predictedTokensPerSec": 50.0,
            }.items()
        }

    @staticmethod
    def _ordered_samples(count: int) -> list[dict]:
        return [
            {
                "ordinal": ordinal,
                "promptEvalMs": 40.0,
                "promptCacheHitRatio": 0.9,
                "promptTokensProcessed": 20,
                "promptTokensCached": 180,
                "promptTokensTotal": 200,
                "firstTokenMs": 300.0,
                "safePrefixCommitMs": 430.0,
                "ttsFirstPcmMs": 200.0,
                "answerFirstPcmMs": 800.0,
            }
            for ordinal in range(1, count + 1)
        ]

    @classmethod
    def _valid_public_result(cls) -> dict:
        graphs_off = {
            phase: worker._direct_public_sample(cls._direct_sample("baseline", phase))
            for phase in ("cold", "capture", "resident", "afterIdle")
        }
        graphs_on = {
            phase: worker._direct_public_sample(cls._direct_sample("candidate", phase))
            for phase in ("cold", "capture", "resident", "afterIdle")
        }
        return {
            "schema": worker.SHORT_DIAGNOSTIC_SCHEMA,
            "status": "completed",
            "config": diagnostic.DEFAULT_CONFIG.to_dict(),
            "e2e": {
                "causal": False,
                "idleSeconds": worker.SHORT_DIAGNOSTIC_IDLE_SECONDS,
                "samples": {
                    "firstAfterWarmup": 1,
                    "resident": worker.SHORT_DIAGNOSTIC_RESIDENT_SAMPLES,
                    "afterIdle": 1,
                },
                "measurements": {
                    "firstAfterWarmup": cls._summary(1),
                    "resident": cls._summary(
                        worker.SHORT_DIAGNOSTIC_RESIDENT_SAMPLES
                    ),
                    "afterIdle": cls._summary(1),
                },
                "orderedSamples": {
                    "firstAfterWarmup": cls._ordered_samples(1),
                    "resident": cls._ordered_samples(
                        worker.SHORT_DIAGNOSTIC_RESIDENT_SAMPLES
                    ),
                    "afterIdle": cls._ordered_samples(1),
                },
            },
            "backendObservation": {
                "causal": False,
                "idleSeconds": worker.SHORT_DIAGNOSTIC_IDLE_SECONDS,
                "samplesPerControl": {
                    phase: 1
                    for phase in ("cold", "capture", "resident", "afterIdle")
                },
                "graphsOff": graphs_off,
                "graphsOn": graphs_on,
                "invariants": {
                    "payloadExact": True,
                    "promptTotalsExact": True,
                    "residentKvExactAcrossIdle": True,
                    "controlsComparable": True,
                },
            },
            "observations": {
                "cacheProofChecks": 22,
                "cacheProofFailures": 0,
                "gpuMinFreeMiB": 8000.0,
                "gpuMaxUtilization": 25.0,
                "peakHostRamMiB": 100,
                "sampleValidityFailures": 0,
                "runtimeMs": 1000,
            },
            "cleanup": {
                "status": "clean",
                "remainingProcesses": 0,
                "remainingGpuAllocations": 0,
                "remainingArtifacts": 0,
            },
        }

    def test_worker_keeps_e2e_observations_separate_from_graph_controls(self) -> None:
        config = {
            **diagnostic.DEFAULT_CONFIG.to_dict(),
            "main.cudaGraph": 0,
        }
        identities = {
            key: f"sha256:{index:064x}"
            for index, key in enumerate(
                ("baseline", "source", "model", "gpu", "corpus", "harness"),
                1,
            )
        }
        image_env = {
            "LAB_MAIN_LLM_IMAGE": f"sha256:{101:064x}",
            "LAB_BOT_API_IMAGE": f"sha256:{102:064x}",
            "LAB_TTS_IMAGE": f"sha256:{103:064x}",
        }
        e2e_calls: list[tuple[str, str, int]] = []
        direct_calls: list[tuple[str, str]] = []
        activations: list[tuple[bool, int]] = []
        sequence: list[tuple[str, ...]] = []

        def activate(_plan, *, config, initial, **_kwargs):
            activations.append((initial, int(config["main.cudaGraph"])))
            sequence.append(("activate", str(initial), str(config["main.cudaGraph"])))
            return {}

        def batch(_plan, *, condition, phase, count, **_kwargs):
            e2e_calls.append((condition, phase, count))
            sequence.append(("e2e", str(count)))
            return [self._sample() for _ in range(count)], 2, 0, None

        def direct(_plan, *, condition, phase, **_kwargs):
            direct_calls.append((condition, phase))
            return self._direct_sample(condition, phase), 2, 0

        def clean(plan, **_kwargs):
            return self._clean(plan["runId"])

        paths = {
            "llama": Path("/fixed/llama"),
            "main_build": Path("/fixed/llama/build-sm120-v1"),
            "profiles": Path("/fixed/profiles"),
            "hub": Path("/fixed/hub"),
        }
        with (
            patch.object(
                worker,
                "_identity_probe_state",
                return_value=(identities, image_env),
            ),
            patch.object(worker, "_activate", side_effect=activate),
            patch.object(
                worker, "_gpu_boundary_observation", return_value=(25.0, 8000.0)
            ),
            patch.object(worker, "_direct_harness_sample", side_effect=direct),
            patch.object(worker, "_harness_batch", side_effect=batch),
            patch.object(
                worker,
                "_tts_harness_warmup",
                side_effect=lambda *_args, **_kwargs: sequence.append(("tts",)),
            ) as tts_warmup,
            patch.object(worker, "_container_observation", return_value=(100, 0)),
            patch.object(worker, "_image_metadata", return_value=[{}, {}, {}]),
            patch.object(worker, "_actual_identities", return_value=identities),
            patch.object(worker, "_production_absent", return_value=True),
            patch.object(worker, "_cleanup", side_effect=clean),
            patch.object(
                worker,
                "_run_source_checks",
                side_effect=AssertionError("promotion checks used"),
            ),
            patch.object(worker.time, "sleep") as sleep,
        ):
            result = worker._run_short_diagnostic(
                config,
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
                paths=paths,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            activations,
            [(True, 0), (False, 0), (False, 1), (False, 0)],
        )
        self.assertEqual(
            direct_calls,
            [
                (condition, phase)
                for condition in ("baseline", "candidate")
                for phase in ("cold", "capture", "resident", "afterIdle")
            ],
        )
        self.assertEqual(
            e2e_calls,
            [
                ("baseline", "warm", 1),
                ("baseline", "warm", 5),
                ("baseline", "warm", 1),
            ],
        )
        tts_warmup.assert_called_once()
        self.assertEqual(
            sequence[-5:],
            [
                ("activate", "False", "0"),
                ("tts",),
                ("e2e", "1"),
                ("e2e", "5"),
                ("e2e", "1"),
            ],
        )
        self.assertIs(result["e2e"]["causal"], False)
        self.assertEqual(
            result["e2e"]["samples"],
            {"firstAfterWarmup": 1, "resident": 5, "afterIdle": 1},
        )
        self.assertEqual(
            [row["ordinal"] for row in result["e2e"]["orderedSamples"]["resident"]],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            result["e2e"]["measurements"]["resident"]["ttsFirstPcmMs"],
            {"sampleCount": 5, "p50": 200.0, "p95": 200.0},
        )
        self.assertEqual(
            [
                row["ttsFirstPcmMs"]
                for row in result["e2e"]["orderedSamples"]["resident"]
            ],
            [200.0] * 5,
        )
        self.assertIs(result["backendObservation"]["causal"], False)
        self.assertTrue(all(result["backendObservation"]["invariants"].values()))
        self.assertEqual(sleep.call_count, 3)
        sleep.assert_called_with(worker.SHORT_DIAGNOSTIC_IDLE_SECONDS)
        self.assertEqual(result["cleanup"], self._valid_public_result()["cleanup"])
        diagnostic.normalize_result(result)

        rendered = json.dumps(result, sort_keys=True).casefold()
        for forbidden in (
            "must-not-escape",
            "fingerprint",
            "payloadproof",
            "runid",
            "identities",
            "signature",
            "receipt",
            "private/audio",
            "http://",
            "https://",
            '"raw',
        ):
            self.assertNotIn(forbidden, rendered)

    def test_worker_preserves_execution_failure_when_cleanup_is_dirty(self) -> None:
        config = diagnostic.DEFAULT_CONFIG.to_dict()
        dirty = self._clean(worker._short_diagnostic_plan(config)["runId"])
        dirty.update({"status": "cleanup_required", "remainingArtifacts": 1})
        with (
            patch.object(
                worker,
                "_identity_probe_state",
                side_effect=worker.LabFailure("runner_failed"),
            ),
            patch.object(worker, "_cleanup", return_value=dirty),
        ):
            result = worker._run_short_diagnostic(
                config,
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
                paths={},
            )

        self.assertEqual(result["status"], "runner_failed")
        self.assertEqual(result["cleanup"]["status"], "cleanup_required")
        self.assertEqual(result["e2e"]["samples"], {
            "firstAfterWarmup": 0,
            "resident": 0,
            "afterIdle": 0,
        })

    def test_harness_requires_complete_strict_prompt_token_diagnostics(self) -> None:
        timings = {
            "promptEvalMs": 40.0,
            "promptCacheHitRatio": 0.9,
            "promptTokensProcessed": 20,
            "promptTokensCached": 180,
            "promptTokensTotal": 200,
            "predictedTokens": 4,
            "predictedMs": 80.0,
            "predictedTokensPerSec": 50.0,
        }
        result = harness._private_timing_diagnostics(
            {"llmTimingMetrics": timings}, {"durations_ms": {}}
        )
        self.assertEqual(result["llmPromptTokensCached"], 180)
        self.assertEqual(result["llmPromptTokensTotal"], 200)
        self.assertEqual(result["llmPredictedTokensPerSec"], 50.0)

        for key, value in (
            ("predictedTokens", True),
            ("predictedMs", 30_000.1),
            ("predictedTokensPerSec", float("inf")),
            ("promptTokensTotal", 199),
            ("promptCacheHitRatio", 0.5),
        ):
            invalid = dict(timings)
            invalid[key] = value
            with self.assertRaisesRegex(RuntimeError, "lab_main_diagnostics_invalid"):
                harness._private_timing_diagnostics(
                    {"llmTimingMetrics": invalid}, {"durations_ms": {}}
                )

    def test_direct_harness_consumes_content_and_returns_only_numeric_diagnostics(self) -> None:
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                events = (
                    {
                        "choices": [{"delta": {"content": "must-not-escape"}}],
                        "mainTiming": {
                            "promptEvalMs": 40.0,
                            "promptCacheHitRatio": 0.9,
                            "promptTokensProcessed": 20,
                            "promptTokensCached": 180,
                            "promptTokensTotal": 200,
                        },
                    },
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                )
                for event in events:
                    yield f"data: {json.dumps(event)}\n".encode("utf-8")
                yield b"data: [DONE]\n"

        with (
            patch.object(harness.request, "urlopen", return_value=Response()) as open_url,
            patch.object(harness.time, "perf_counter", side_effect=(10.0, 10.1)),
        ):
            sample = harness._direct_backend_sample(
                "http://main_llm_gateway_lab:9819/v1/chat/completions",
                b"k" * 32,
            )

        self.assertEqual(set(sample), worker._DIRECT_SAMPLE_FIELDS)
        self.assertEqual(sample["promptTokensProcessed"], 20)
        self.assertEqual(sample["promptTokensCached"], 180)
        self.assertEqual(sample["promptTokensTotal"], 200)
        self.assertNotIn("must-not-escape", json.dumps(sample))
        request_value = open_url.call_args.args[0]
        self.assertEqual(
            request_value.headers["X-evelyn-main-request-kind"], "interactive"
        )
        self.assertEqual(open_url.call_args.kwargs["timeout"], 180.0)

    def test_tts_harness_warmup_uses_one_request_and_fully_drains_content(self) -> None:
        class Response:
            status = 200

            def __init__(self) -> None:
                self.chunks = [b"pcm", b"tail", b""]
                self.read_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read1(self, _size: int) -> bytes:
                self.read_calls += 1
                return self.chunks.pop(0)

        response = Response()
        with patch.object(harness.request, "urlopen", return_value=response) as open_url:
            proof = harness._tts_generate_warmup(
                "http://tts_lab:8880/v1/audio/speech"
            )

        self.assertEqual(
            proof,
            {
                "schema": harness.TTS_WARMUP_PROOF_SCHEMA,
                "externalDefaultRoute": False,
                "requestCount": 1,
                "fullDrain": True,
                "audioPresent": True,
            },
        )
        self.assertEqual(response.read_calls, 3)
        self.assertEqual(open_url.call_count, 1)
        self.assertEqual(open_url.call_args.kwargs["timeout"], 60.0)
        request_value = open_url.call_args.args[0]
        self.assertEqual(request_value.method, "POST")
        self.assertEqual(json.loads(request_value.data)["input"], "안녕")

    def test_tts_harness_warmup_rejects_zero_bytes(self) -> None:
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size: int) -> bytes:
                return b""

        with patch.object(harness.request, "urlopen", return_value=Response()):
            with self.assertRaisesRegex(RuntimeError, "^lab_tts_warmup_failed$"):
                harness._tts_generate_warmup(
                    "http://tts_lab:8880/v1/audio/speech"
                )

    def test_tts_harness_warmup_closes_response_at_size_limit(self) -> None:
        class Response:
            status = 200

            def __init__(self) -> None:
                self.chunks = [b"abc", b"d"]
                self.closed = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.closed = True
                return False

            def read(self, _size: int) -> bytes:
                return self.chunks.pop(0)

        response = Response()
        with patch.object(harness, "_MAX_TTS_WARMUP_BYTES", 3), patch.object(
            harness.request, "urlopen", return_value=response
        ):
            with self.assertRaisesRegex(RuntimeError, "^lab_tts_warmup_failed$"):
                harness._tts_generate_warmup(
                    "http://tts_lab:8880/v1/audio/speech"
                )

        self.assertTrue(response.closed)

    def test_worker_accepts_only_exact_content_free_tts_warmup_proof(self) -> None:
        plan = worker._short_diagnostic_plan(diagnostic.DEFAULT_CONFIG.to_dict())
        proof = {
            "schema": harness.TTS_WARMUP_PROOF_SCHEMA,
            "externalDefaultRoute": False,
            "requestCount": 1,
            "fullDrain": True,
            "audioPresent": True,
        }
        with patch.object(
            worker, "_run_command", return_value=json.dumps(proof)
        ) as run_command:
            worker._tts_harness_warmup(
                plan,
                docker=Path("/fixed/docker"),
                config_dir=Path("/fixed/config"),
                compose_env={"LAB_EXECUTION_MODE": "e2e"},
                deadline=worker.time.monotonic() + 100.0,
            )

        self.assertEqual(
            run_command.call_args.kwargs["extra_env"]["LAB_EXECUTION_MODE"],
            "tts_warmup",
        )
        invalid_proofs = (
            {**proof, "audioBytes": 7},
            {**proof, "externalDefaultRoute": 0},
            {**proof, "requestCount": True},
            {**proof, "fullDrain": 1},
            {**proof, "audioPresent": 1},
        )
        for invalid in invalid_proofs:
            with self.subTest(invalid=invalid), patch.object(
                worker, "_run_command", return_value=json.dumps(invalid)
            ):
                with self.assertRaisesRegex(worker.LabFailure, "^runner_failed$"):
                    worker._tts_harness_warmup(
                        plan,
                        docker=Path("/fixed/docker"),
                        config_dir=Path("/fixed/config"),
                        compose_env={},
                        deadline=worker.time.monotonic() + 100.0,
                    )

    def test_direct_harness_uses_non_restart_readiness_contract(self) -> None:
        env = {
            "LAB_EXECUTION_MODE": "direct_backend",
            "LAB_CONDITION": "baseline",
            "LAB_PHASE": "cold",
            "LAB_EQUIVALENCE_KEY_HEX": "ab" * 32,
            "LAB_SAMPLE_COUNT": "1",
            "LAB_STATE_URL": "http://bot_api_lab:8798/api/control-page/state",
            "LAB_MAIN_DIRECT_URL": (
                "http://main_llm_gateway_lab:9819/v1/chat/completions"
            ),
        }
        sample = self._direct_sample("baseline", "cold")
        with (
            patch.dict(harness.os.environ, env, clear=False),
            patch.object(harness, "_has_external_default_route", return_value=False),
            patch.object(
                harness.benchmark,
                "wait_until_ready",
                return_value={"startupToReadyMs": None},
            ) as wait_ready,
            patch.object(harness, "_cache_proof_ready", return_value=True),
            patch.object(harness, "_direct_backend_sample", return_value=sample),
        ):
            result = harness.run()

        self.assertEqual(result["samples"], [sample])
        self.assertIsNone(wait_ready.call_args.kwargs["startup_epoch"])
        self.assertIs(wait_ready.call_args.kwargs["state_ready"], harness._lab_state_ready)

    def test_noninitial_activation_recreates_main_gateway_and_bot_before_readiness(self) -> None:
        plan = worker._short_diagnostic_plan(diagnostic.DEFAULT_CONFIG.to_dict())
        commands: list[tuple[str, ...]] = []

        def command(_docker, _project, *parts):
            return tuple(parts)

        def run(command_value, **_kwargs):
            commands.append(command_value)
            return ""

        with (
            patch.object(
                worker,
                "_config_env",
                return_value={
                    "LAB_MODEL_IDENTITY": "a" * 64,
                    "LAB_SERVER_IDENTITY": "b" * 64,
                },
            ),
            patch.object(
                worker,
                "_activation_state",
                side_effect=(
                    ("11111111-1111-4111-8111-111111111111", "a" * 64, "b" * 64, "c" * 64),
                    (
                        "22222222-2222-4222-8222-222222222222",
                        "a" * 64,
                        "b" * 64,
                        worker._runtime_identity(plan["baselineConfig"])[7:],
                    ),
                ),
            ),
            patch.object(worker, "_compose_command", side_effect=command),
            patch.object(worker, "_run_command", side_effect=run),
        ):
            worker._activate(
                plan,
                config=plan["baselineConfig"],
                initial=False,
                docker=Path("/fixed/docker"),
                config_dir=Path("/fixed/config"),
                base_env={},
                deadline=worker.time.monotonic() + 100.0,
            )

        self.assertEqual(
            commands[0],
            (
                "up",
                "-d",
                "--wait",
                "--force-recreate",
                "main_llm_lab",
                "main_llm_gateway_lab",
                "bot_api_lab",
            ),
        )
        self.assertEqual(len(commands), 1)

    def test_activation_rejects_stale_epoch_and_wrong_runtime_identity(self) -> None:
        plan = worker._short_diagnostic_plan(diagnostic.DEFAULT_CONFIG.to_dict())
        expected_runtime = worker._runtime_identity(plan["baselineConfig"])[7:]
        env = {
            "LAB_MODEL_IDENTITY": "a" * 64,
            "LAB_SERVER_IDENTITY": "b" * 64,
        }
        old = "11111111-1111-4111-8111-111111111111"
        new = "22222222-2222-4222-8222-222222222222"
        cases = (
            (
                "stale_epoch",
                (
                    (old, "a" * 64, "b" * 64, expected_runtime),
                    (old, "a" * 64, "b" * 64, expected_runtime),
                ),
            ),
            (
                "wrong_runtime",
                (
                    (old, "a" * 64, "b" * 64, expected_runtime),
                    (new, "a" * 64, "b" * 64, "c" * 64),
                ),
            ),
        )
        for name, states in cases:
            with self.subTest(name=name), patch.object(
                worker, "_config_env", return_value=env
            ), patch.object(
                worker, "_activation_state", side_effect=states
            ), patch.object(
                worker, "_run_command", return_value=""
            ):
                with self.assertRaisesRegex(worker.LabFailure, "environment_drift"):
                    worker._activate(
                        plan,
                        config=plan["baselineConfig"],
                        initial=False,
                        docker=Path("/fixed/docker"),
                        config_dir=Path("/fixed/config"),
                        base_env={},
                        deadline=worker.time.monotonic() + 100.0,
                    )

    def test_v4_normalizer_is_recursive_exact_and_keeps_backend_observational(self) -> None:
        valid = self._valid_public_result()
        normalized = diagnostic.normalize_result(valid)
        self.assertIs(normalized["backendObservation"]["causal"], False)

        mutations = (
            lambda value: value["cleanup"].update({"runId": "must-not-escape"}),
            lambda value: value["cleanup"].update({"remainingArtifacts": 1}),
            lambda value: value["observations"].update({"privatePath": "C:/x"}),
            lambda value: value["e2e"]["measurements"]["resident"][
                "promptEvalMs"
            ].update({"p50": float("nan")}),
            lambda value: value["e2e"]["measurements"]["resident"][
                "promptEvalMs"
            ].update({"p50": 42.0, "p95": 41.0}),
            lambda value: value["e2e"]["measurements"]["resident"][
                "promptEvalMs"
            ].update({"sampleCount": 4}),
            lambda value: value["e2e"]["samples"].update({"resident": 0}),
            lambda value: value["e2e"]["measurements"]["afterIdle"].pop(
                "predictedMs"
            ),
            lambda value: value["e2e"]["measurements"]["afterIdle"].pop(
                "ttsFirstPcmMs"
            ),
            lambda value: value["e2e"]["orderedSamples"]["resident"][1].update(
                {"ordinal": 1}
            ),
            lambda value: value["e2e"]["orderedSamples"]["resident"][0].update(
                {"promptEvalMs": 41.0}
            ),
            lambda value: value["e2e"]["orderedSamples"]["resident"][0].update(
                {"ttsFirstPcmMs": 201.0}
            ),
            lambda value: value["e2e"]["orderedSamples"]["resident"][0].update(
                {"fingerprint": "must-not-escape"}
            ),
            lambda value: value["backendObservation"]["graphsOn"]["resident"].update(
                {"raw": "must-not-escape"}
            ),
            lambda value: value["backendObservation"].update({"causal": True}),
            lambda value: value["backendObservation"]["graphsOn"]["afterIdle"].update(
                {"promptTokensProcessed": 1, "promptTokensCached": 199}
            ),
            lambda value: value["backendObservation"]["graphsOn"]["resident"].update(
                {"promptCacheHitRatio": 0.5}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                malformed = copy.deepcopy(valid)
                mutate(malformed)
                with self.assertRaisesRegex(ValueError, "short_diagnostic_malformed"):
                    diagnostic.normalize_result(malformed)

    def test_cli_uses_fixed_config_and_never_enters_receipt_or_promotion_path(self) -> None:
        raw = self._valid_public_result()
        expected = diagnostic.normalize_result(raw)
        with (
            patch.object(
                diagnostic.adapter, "_invoke_worker", return_value=raw
            ) as invoke,
            patch.object(
                diagnostic.adapter,
                "reconcile_owned_lab",
                side_effect=(self._global_clean(), self._global_clean()),
            ) as reconcile,
        ):
            result = diagnostic.run()
        self.assertEqual(result, expected)
        invoke.assert_called_once()
        self.assertEqual(reconcile.call_count, 2)
        self.assertEqual(invoke.call_args.args[0], "short_diagnostic")
        self.assertEqual(
            invoke.call_args.args[1]["config"], diagnostic.DEFAULT_CONFIG.to_dict()
        )
        self.assertEqual(
            result["config"],
            {
                "main.batch": 2048,
                "main.ubatch": 2048,
                "main.cacheReuse": 256,
                "main.cacheRamMiB": 8192,
                "main.cudaGraph": 1,
                "main.swaFull": 0,
            },
        )

    def test_cli_allows_only_bounded_swa_full_dimension(self) -> None:
        self.assertEqual(diagnostic.parse_args([]).swa_full, 0)
        self.assertEqual(diagnostic.parse_args(["--swa-full", "1"]).swa_full, 1)
        with self.assertRaises(SystemExit):
            diagnostic.parse_args(["--swa-full", "2"])

    def test_cli_malformed_worker_result_fails_closed_after_cleanup(self) -> None:
        run_id = worker._short_diagnostic_plan(
            diagnostic.DEFAULT_CONFIG.to_dict()
        )["runId"]
        raw_cleanup = {"cleanup": self._clean(run_id)}
        with (
            patch.object(
                diagnostic.adapter,
                "_invoke_worker",
                side_effect=({"status": "completed", "raw": "secret"}, raw_cleanup),
            ) as invoke,
            patch.object(
                diagnostic.adapter,
                "reconcile_owned_lab",
                side_effect=(self._global_clean(), self._global_clean()),
            ) as reconcile,
        ):
            result = diagnostic.run()
        self.assertEqual(result["status"], "worker_failed")
        self.assertEqual(result["cleanup"]["status"], "clean")
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(reconcile.call_count, 2)
        self.assertEqual(invoke.call_args_list[1].args[0], "short_diagnostic_cleanup")

    def test_huge_integer_fails_closed_and_runs_terminal_cleanup(self) -> None:
        raw = self._valid_public_result()
        raw["observations"]["runtimeMs"] = 10**10_000
        run_id = worker._short_diagnostic_plan(
            diagnostic.DEFAULT_CONFIG.to_dict()
        )["runId"]
        with (
            patch.object(
                diagnostic.adapter,
                "_invoke_worker",
                side_effect=(raw, {"cleanup": self._clean(run_id)}),
            ) as invoke,
            patch.object(
                diagnostic.adapter,
                "reconcile_owned_lab",
                side_effect=(self._global_clean(), self._global_clean()),
            ) as reconcile,
        ):
            result = diagnostic.run()

        self.assertEqual(result["status"], "worker_failed")
        self.assertEqual(result["cleanup"]["status"], "clean")
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(invoke.call_args_list[1].args[0], "short_diagnostic_cleanup")
        self.assertEqual(reconcile.call_count, 2)

    def test_startup_global_cleanup_blocks_diagnostic(self) -> None:
        dirty = self._global_clean()
        dirty.update(
            {
                "status": "cleanup_required",
                "remainingArtifacts": 1,
            }
        )
        with (
            patch.object(
                diagnostic.adapter, "reconcile_owned_lab", return_value=dirty
            ) as reconcile,
            patch.object(diagnostic.adapter, "_invoke_worker") as invoke,
        ):
            result = diagnostic.run()

        self.assertEqual(result["status"], "cleanup_required")
        self.assertEqual(result["cleanup"]["remainingArtifacts"], 1)
        reconcile.assert_called_once_with()
        invoke.assert_not_called()

    def test_terminal_global_cleanup_overrides_completed_result(self) -> None:
        dirty = self._global_clean()
        dirty.update(
            {
                "status": "cleanup_required",
                "remainingGpuAllocations": 1,
            }
        )
        with (
            patch.object(
                diagnostic.adapter,
                "reconcile_owned_lab",
                side_effect=(self._global_clean(), dirty),
            ) as reconcile,
            patch.object(
                diagnostic.adapter,
                "_invoke_worker",
                return_value=self._valid_public_result(),
            ) as invoke,
        ):
            result = diagnostic.run()

        self.assertEqual(result["status"], "cleanup_required")
        self.assertEqual(result["cleanup"]["remainingGpuAllocations"], 1)
        self.assertEqual(reconcile.call_count, 2)
        invoke.assert_called_once()

    def test_terminal_worker_cleanup_recovers_completed_result(self) -> None:
        run_id = worker._short_diagnostic_plan(
            diagnostic.DEFAULT_CONFIG.to_dict()
        )["runId"]
        delayed_cleanup = self._valid_public_result()
        delayed_cleanup["status"] = "cleanup_required"
        delayed_cleanup["cleanup"].update(
            {"status": "cleanup_required", "remainingGpuAllocations": 1}
        )
        with (
            patch.object(
                diagnostic.adapter,
                "_invoke_worker",
                side_effect=(
                    delayed_cleanup,
                    {"cleanup": self._clean(run_id)},
                ),
            ) as invoke,
            patch.object(
                diagnostic.adapter,
                "reconcile_owned_lab",
                side_effect=(self._global_clean(), self._global_clean()),
            ),
        ):
            result = diagnostic.run()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["cleanup"]["status"], "clean")
        self.assertEqual(invoke.call_count, 2)

    def test_terminal_global_cleanup_preserves_noncompleted_result(self) -> None:
        dirty = self._global_clean()
        dirty.update(
            {
                "status": "cleanup_required",
                "remainingArtifacts": 1,
            }
        )
        worker_failed = self._valid_public_result()
        worker_failed["status"] = "worker_failed"
        with (
            patch.object(
                diagnostic.adapter,
                "reconcile_owned_lab",
                side_effect=(self._global_clean(), dirty),
            ),
            patch.object(
                diagnostic.adapter,
                "_invoke_worker",
                return_value=worker_failed,
            ),
        ):
            result = diagnostic.run()

        self.assertEqual(result["status"], "worker_failed")
        self.assertEqual(result["cleanup"]["remainingArtifacts"], 1)

    def test_campaign_lock_failure_never_invokes_worker_or_cleanup(self) -> None:
        class Locked:
            def __enter__(self):
                raise RuntimeError("owned_lab_campaign_locked")

            def __exit__(self, *_args):
                return None

        with (
            patch.object(
                diagnostic.campaign_lock,
                "OwnedLabCampaignLock",
                return_value=Locked(),
            ),
            patch.object(diagnostic.adapter, "_invoke_worker") as invoke,
            patch.object(diagnostic.adapter, "reconcile_owned_lab") as reconcile,
        ):
            result = diagnostic.run()

        self.assertEqual(result["status"], "owned_lab_campaign_locked")
        self.assertEqual(result["cleanup"]["status"], "cleanup_required")
        invoke.assert_not_called()
        reconcile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
