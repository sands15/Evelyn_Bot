from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
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

    def test_default_urls_match_diagnostic_loopback_ports(self) -> None:
        args = self.args()

        self.assertEqual(args.main_url, "http://127.0.0.1:9820/v1/chat/completions")
        self.assertEqual(args.qwen_url, "http://127.0.0.1:9823/v1/chat/completions")
        self.assertEqual(args.stt_url, "http://127.0.0.1:8892/v1/stt/transcribe")

    def test_command_output_is_decoded_as_utf8_on_windows(self) -> None:
        completed = benchmark.subprocess.CompletedProcess(
            ["docker", "inspect"],
            0,
            stdout='{"Source":"C:/Users/Admin/Documents/이블린"}\n',
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed) as run:
            output = benchmark._run_text(["docker", "inspect"])

        self.assertIn("이블린", output)
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "strict")

    def test_docker_desktop_mount_sources_normalize_to_windows_paths(self) -> None:
        expected = benchmark._canonical_host_path("C:/Users/Admin/.cache/huggingface/hub")
        for source in (
            "/run/desktop/mnt/host/c/Users/Admin/.cache/huggingface/hub",
            "/host_mnt/c/Users/Admin/.cache/huggingface/hub",
            "/mnt/host/c/Users/Admin/.cache/huggingface/hub",
        ):
            with self.subTest(source=source):
                self.assertEqual(benchmark._canonical_host_path(source), expected)

    def test_large_identity_hashes_use_bounded_extended_timeouts(self) -> None:
        with patch.object(benchmark, "_run_text", return_value="d" * 64 + "\n") as run:
            benchmark._container_tree_sha256(
                "a" * 64,
                "/model",
                domain="test.tree.v1",
            )
            self.assertEqual(run.call_args.kwargs["timeout_sec"], 900.0)
        with patch.object(
            benchmark,
            "_run_text",
            return_value="e" * 64 + "  /model.gguf\n",
        ) as run:
            benchmark._container_file_sha256("a" * 64, "/model.gguf")
            self.assertEqual(run.call_args.kwargs["timeout_sec"], 900.0)

    def _p0_values(self, phase: str) -> list[str]:
        values = [
            "--phase",
            phase,
            "--warmup-iterations",
            "2",
            "--iterations",
            "20",
            "--attempt-id",
            "p0-4-test",
            "--compose-project",
            "evelyn-p04-test",
            "--source-revision",
            "1" * 40,
            "--main-image-id",
            "sha256:" + "2" * 64,
            "--qwen-image-id",
            "sha256:" + "3" * 64,
            "--stt-image-id",
            "sha256:" + ("4" if phase == "old-stt" else "5") * 64,
            "--gpu-uuid",
            "GPU-96c554e6-feef-2980-6722-efcb0af098f9",
            "--model-cache-revision",
            "6" * 40,
        ]
        return values

    def p0_4_args(self, phase: str):
        values = self._p0_values(phase)
        if phase == "new-stt":
            values.extend(("--baseline-report", "old.json", "--baseline-sha256", "7" * 64))
        return benchmark.parse_args(values)

    @staticmethod
    def observed(args) -> dict:
        main_gpu = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        containers = {}
        for role, service, container_id, image_id, gpu_uuid in (
            ("main", "main_llm", "a" * 64, args.main_image_id, main_gpu),
            ("qwen", "minecraft_llm", "b" * 64, args.qwen_image_id, args.gpu_uuid),
            ("stt", "stt", "c" * 64, args.stt_image_id, args.gpu_uuid),
        ):
            containers[role] = {
                "id": container_id,
                "imageId": image_id,
                "service": service,
                "startedAt": "2026-08-27T00:00:00Z",
                "restartCount": 0,
                "gpuUuid": gpu_uuid,
            }
        return {
            "source": {"revision": args.source_revision, "clean": True},
            "composeProject": args.compose_project,
            "gpus": {
                "main": {"index": 0, "uuid": main_gpu},
                "shared": {"index": 1, "uuid": args.gpu_uuid},
            },
            "containers": containers,
            "main": {
                "modelSha256": benchmark.P0_4_MAIN_MODEL_SHA256,
                "serverRuntimeSha256": "9" * 64,
                "runtimeTemplateSha256": "a" * 64,
                "llamaMountSourceSha256": hashlib.sha256(
                    benchmark._canonical_host_path(
                        benchmark._expected_llama_sources()[0]
                    ).encode("utf-8")
                ).hexdigest(),
                "buildMountSourceSha256": hashlib.sha256(
                    benchmark._canonical_host_path(
                        benchmark._expected_llama_sources()[1]
                    ).encode("utf-8")
                ).hexdigest(),
            },
            "qwen": {
                "modelSha256": benchmark.P0_4_QWEN_MODEL_SHA256,
                "serverRuntimeSha256": "c" * 64,
                "llamaMountSourceSha256": hashlib.sha256(
                    benchmark._canonical_host_path(
                        benchmark._expected_llama_sources()[0]
                    ).encode("utf-8")
                ).hexdigest(),
            },
            "stt": {
                "model": benchmark.P0_4_STT_MODEL,
                "backend": benchmark.P0_4_STT_BACKEND,
                "memoryUtilization": benchmark.P0_4_STT_MEMORY_UTILIZATION,
                "modelCacheRevision": args.model_cache_revision,
                "modelContentSha256": "d" * 64,
                "cacheSourceSha256": hashlib.sha256(
                    benchmark._canonical_host_path(
                        benchmark._expected_hf_hub_source()
                    ).encode("utf-8")
                ).hexdigest(),
                "packageSetSha256": hashlib.sha256(
                    b"pip==25.0\nqwen-asr==0.0.4\n"
                ).hexdigest(),
                "embeddedPackageSetSha256": (
                    hashlib.sha256(b"pip==25.0\nqwen-asr==0.0.4\n").hexdigest()
                    if args.phase == "new-stt"
                    else None
                ),
                "runtimeSourceTreeSha256": "e" * 64,
                "checkoutSourceTreeSha256": "e" * 64,
                "sourceMatchesCheckout": True,
                "imageProvenance": (
                    {
                        "sourceRevision": args.source_revision,
                        "baseDigest": benchmark.P0_4_STT_BASE_DIGEST,
                        "dockerfileSha256": hashlib.sha256(
                            benchmark.P0_4_STT_DOCKERFILE.read_bytes()
                        ).hexdigest(),
                        "requirementsSha256": hashlib.sha256(
                            benchmark.P0_4_STT_REQUIREMENTS.read_bytes()
                        ).hexdigest(),
                    }
                    if args.phase == "new-stt"
                    else None
                ),
                "cuda": True,
                "gpuName": "NVIDIA GeForce RTX 3090",
                "cacheReadOnly": True,
                "offline": True,
            },
        }

    @staticmethod
    def inspected_containers(args) -> list[dict]:
        observed = Gpu1LatencyBenchmarkTests.observed(args)["containers"]
        llama_source, main_build_source = benchmark._expected_llama_sources()
        cache_source = benchmark._expected_hf_hub_source()
        rows = []
        for role, (name, service, gpu) in benchmark._P0_4_CONTAINERS.items():
            environment = [f"NVIDIA_VISIBLE_DEVICES={gpu}", f"CUDA_VISIBLE_DEVICES={gpu}"]
            if role == "stt":
                environment += [
                    "STT_MODEL_NAME=Qwen/Qwen3-ASR-1.7B",
                    "STT_LOAD_ON_START=true",
                    "STT_VLLM_GPU_MEMORY_UTILIZATION=0.35",
                    "HF_HUB_OFFLINE=1",
                    "HF_HUB_DISABLE_IMPLICIT_TOKEN=1",
                    "HF_HOME=/tmp/huggingface-empty",
                    "HF_HUB_CACHE=/root/.cache/huggingface",
                    "HF_TOKEN=",
                    "HUGGING_FACE_HUB_TOKEN=",
                    "TRANSFORMERS_OFFLINE=1",
                ]
            rows.append(
                {
                    "Name": "/" + name,
                    "Id": observed[role]["id"],
                    "Image": observed[role]["imageId"],
                    "RestartCount": 0,
                    "State": {
                        "Running": True,
                        "StartedAt": "2026-08-27T00:00:00Z",
                        "Health": {"Status": "healthy"},
                    },
                    "Config": {
                        "Labels": {
                            "com.docker.compose.project": args.compose_project,
                            "com.docker.compose.service": service,
                            "com.docker.compose.oneoff": "False",
                        },
                        "Env": environment,
                    },
                    "HostConfig": {
                        "DeviceRequests": [
                            {
                                "Driver": "nvidia",
                                "Count": 0,
                                "DeviceIDs": [gpu],
                                "Capabilities": [["gpu"]],
                            }
                        ]
                    },
                    "Mounts": (
                        [
                            {
                                "Type": "bind",
                                "Destination": "/root/.cache/huggingface",
                                "Source": str(cache_source),
                                "RW": False,
                            },
                            {
                                "Type": "volume",
                                "Destination": "/app/logs",
                                "RW": True,
                            },
                        ]
                        if role == "stt"
                        else (
                            [
                                {
                                    "Type": "bind",
                                    "Destination": "/llama",
                                    "Source": str(llama_source),
                                    "RW": False,
                                }
                            ]
                            if role == "qwen"
                            else (
                                [
                                    {
                                        "Type": "bind",
                                        "Destination": "/llama",
                                        "Source": str(llama_source),
                                        "RW": False,
                                    },
                                    {
                                        "Type": "bind",
                                        "Destination": "/llama/build",
                                        "Source": str(main_build_source),
                                        "RW": False,
                                    },
                                ]
                                if role == "main"
                                else []
                            )
                        )
                    ),
                }
            )
        return rows

    def probe_side_effect(self, args):
        payload = self.inspected_containers(args)
        main_gpu = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        package_sha256 = hashlib.sha256(
            b"pip==25.0\nqwen-asr==0.0.4\n"
        ).hexdigest()
        checkout_source_sha256 = benchmark._tree_sha256(
            benchmark.P0_4_STT_SOURCE_ROOT,
            domain="evelyn.stt-source-tree.v1",
            exclude_bytecode=True,
        )

        def run(command, *, timeout_sec=15.0):
            del timeout_sec
            if command[:4] == ["git", "-C", str(benchmark.REPO_ROOT), "rev-parse"]:
                return args.source_revision + "\n"
            if command[:4] == ["git", "-C", str(benchmark.REPO_ROOT), "status"]:
                return ""
            if command[:2] == ["nvidia-smi", "--id"]:
                return (main_gpu if command[2] == "0" else args.gpu_uuid) + "\n"
            if command[:3] == ["docker", "container", "inspect"]:
                return json.dumps(payload)
            if command[:3] == ["docker", "image", "inspect"]:
                return json.dumps(
                    [
                        {
                            "Id": args.stt_image_id,
                            "Config": {
                                "Labels": {
                                    "org.opencontainers.image.revision": args.source_revision,
                                    "org.opencontainers.image.base.digest": benchmark.P0_4_STT_BASE_DIGEST,
                                    "io.evelyn.stt.dockerfile.sha256": hashlib.sha256(
                                        benchmark.P0_4_STT_DOCKERFILE.read_bytes()
                                    ).hexdigest(),
                                    "io.evelyn.stt.requirements.sha256": hashlib.sha256(
                                        benchmark.P0_4_STT_REQUIREMENTS.read_bytes()
                                    ).hexdigest(),
                                }
                            },
                        }
                    ]
                )
            if command[:2] == ["docker", "ps"]:
                return "\n".join(row["Id"] for row in payload) + "\n"
            if command[:2] == ["docker", "exec"] and command[3] == "nvidia-smi":
                return (main_gpu if command[2] == "a" * 64 else args.gpu_uuid) + "\n"
            if command[:2] == ["docker", "exec"] and command[3] == "cat":
                return "\n".join(
                    (benchmark.P0_4_MAIN_MODEL_SHA256, "9" * 64, "a" * 64)
                ) + "\n"
            if command[:2] == ["docker", "exec"] and command[3] == "sha256sum":
                digest = (
                    benchmark.P0_4_MAIN_MODEL_SHA256
                    if command[2] == "a" * 64
                    else benchmark.P0_4_QWEN_MODEL_SHA256
                    if command[2] == "b" * 64
                    else package_sha256
                )
                return f"{digest}  {command[4]}\n"
            if command[:2] == ["docker", "exec"] and command[3] == "bash":
                return ("9" * 64 if command[2] == "a" * 64 else "c" * 64) + "\n"
            if command[:2] == ["docker", "exec"] and command[3] == "python":
                if command[4:] == ["-m", "pip", "freeze", "--all"]:
                    return "qwen-asr==0.0.4\npip==25.0\n"
                if len(command) > 6 and "/snapshots/" in command[6]:
                    return "d" * 64 + "\n"
                if len(command) > 6 and command[6] == "/app/evelyn_core/runtime/evelyn_core":
                    return checkout_source_sha256 + "\n"
                return args.model_cache_revision + "|1\n"
            raise AssertionError(command)

        return run

    def p0_report(self, phase: str, *, stt_ms: float, baseline_sha256: str | None = None):
        args = self.p0_4_args(phase)
        if phase == "new-stt" and baseline_sha256 is None:
            baseline_sha256 = args.baseline_sha256
        observed = self.observed(args)
        report = benchmark.build_report(
            args,
            iterations=[self.iteration(stt_ms=stt_ms) for _ in range(20)],
            audio_metadata={
                "sha256": benchmark.GPU1_BENCHMARK_AUDIO_SHA256,
                "durationMs": 1_640.0,
                "sampleCount": 26_240,
            },
            observed_environment=observed,
            baseline_sha256=baseline_sha256,
            generated_at=1_000.0,
        )
        environment_hash = benchmark._content_sha256(observed)
        report["environmentProof"] = {
            "preflightSha256": environment_hash,
            "postflightSha256": environment_hash,
            "stable": True,
        }
        return report

    def test_p0_4_mode_requires_exact_identity_and_2_plus_20(self) -> None:
        with self.assertRaises(SystemExit):
            benchmark.parse_args(["--phase", "old-stt"])
        with self.assertRaises(SystemExit):
            benchmark.parse_args(
                [
                    "--phase",
                    "old-stt",
                    "--warmup-iterations",
                    "2",
                    "--iterations",
                    "20",
                ]
            )

        args = self.p0_4_args("old-stt")
        self.assertEqual(args.iterations, 20)
        self.assertEqual(args.stt_memory_utilization, 0.35)
        self.assertEqual(args.compose_project, "evelyn-p04-test")

    def test_p0_4_mode_rejects_nonfinite_or_changed_fixed_contract(self) -> None:
        base = self._p0_values("old-stt")
        for changed in (
            ("--main-url", "http://127.0.0.1:9999/v1/chat/completions"),
            ("--qwen-model", "other.gguf"),
            ("--main-ttft-budget-ms", "nan"),
            ("--qwen-timeout-ms", "6001"),
            ("--stt-timeout-sec", "16"),
            ("--gpu-sample-interval-ms", "51"),
        ):
            with self.subTest(changed=changed), self.assertRaises(SystemExit):
                benchmark.parse_args([*base, *changed])

    def test_p0_4_observation_binds_live_git_images_gpus_health_and_cache(self) -> None:
        args = self.p0_4_args("old-stt")
        health = {
            "ok": True,
            "ready": True,
            "model": benchmark.P0_4_STT_MODEL,
            "backend": benchmark.P0_4_STT_BACKEND,
            "loadOnStart": True,
            "gpu": {"cuda": True, "name": "NVIDIA GeForce RTX 3090"},
        }
        with (
            patch.object(benchmark, "_run_text", side_effect=self.probe_side_effect(args)),
            patch.object(benchmark, "_get_json", return_value=health),
        ):
            observed = benchmark._observe_p0_4_environment(args)

        self.assertEqual(observed["containers"]["stt"]["imageId"], args.stt_image_id)
        self.assertEqual(observed["gpus"]["shared"]["uuid"], args.gpu_uuid)
        self.assertEqual(observed["containers"]["main"]["gpuDeviceId"], "0")
        self.assertEqual(observed["containers"]["qwen"]["gpuDeviceId"], "1")
        self.assertEqual(observed["containers"]["stt"]["gpuDeviceId"], "1")
        self.assertEqual(
            observed["main"]["modelSha256"], benchmark.P0_4_MAIN_MODEL_SHA256
        )
        self.assertEqual(
            observed["qwen"]["modelSha256"], benchmark.P0_4_QWEN_MODEL_SHA256
        )
        self.assertEqual(observed["stt"]["modelContentSha256"], "d" * 64)
        self.assertEqual(
            observed["stt"]["packageSetSha256"],
            hashlib.sha256(b"pip==25.0\nqwen-asr==0.0.4\n").hexdigest(),
        )
        self.assertEqual(
            benchmark._validation_binding(args, observed)["images"]["stt"],
            args.stt_image_id,
        )

    def test_p0_4_observation_rejects_observed_identity_mismatches(self) -> None:
        args = self.p0_4_args("new-stt")
        health = {
            "ok": True,
            "ready": True,
            "model": benchmark.P0_4_STT_MODEL,
            "backend": benchmark.P0_4_STT_BACKEND,
            "loadOnStart": True,
            "gpu": {"cuda": True, "name": "NVIDIA GeForce RTX 3090"},
        }
        for failure in (
            "project_leak",
            "image_label",
            "cache_source",
            "gpu_request",
            "qwen_mount_type",
            "qwen_model_content",
            "runtime_source",
        ):
            normal = self.probe_side_effect(args)
            bad_payload = self.inspected_containers(args)
            if failure == "cache_source":
                stt = next(
                    row for row in bad_payload if row["Name"] == "/evelyn-p04-stt"
                )
                cache = next(
                    mount
                    for mount in stt["Mounts"]
                    if mount["Destination"] == "/root/.cache/huggingface"
                )
                cache["Source"] = str(Path("C:/wrong/huggingface/hub"))
            elif failure == "qwen_mount_type":
                qwen = next(
                    row
                    for row in bad_payload
                    if row["Name"] == "/evelyn-p04-qwen-llm"
                )
                qwen["Mounts"][0]["Type"] = "volume"
            elif failure == "gpu_request":
                stt = next(
                    row for row in bad_payload if row["Name"] == "/evelyn-p04-stt"
                )
                stt["HostConfig"]["DeviceRequests"][0]["DeviceIDs"] = ["0"]

            def run(command, *, timeout_sec=15.0):
                if failure == "image_label" and command[:3] == [
                    "docker",
                    "image",
                    "inspect",
                ]:
                    image = json.loads(normal(command, timeout_sec=timeout_sec))
                    image[0]["Config"]["Labels"][
                        "org.opencontainers.image.revision"
                    ] = "0" * 40
                    return json.dumps(image)
                if failure in {
                    "cache_source",
                    "gpu_request",
                    "qwen_mount_type",
                } and command[:3] == ["docker", "container", "inspect"]:
                    return json.dumps(bad_payload)
                if failure == "project_leak" and command[:2] == ["docker", "ps"]:
                    return normal(command, timeout_sec=timeout_sec) + "f" * 64 + "\n"
                if (
                    failure == "qwen_model_content"
                    and command[:2] == ["docker", "exec"]
                    and command[2] == "b" * 64
                    and command[3] == "sha256sum"
                ):
                    return f"{'f' * 64}  {command[4]}\n"
                if (
                    failure == "runtime_source"
                    and command[:2] == ["docker", "exec"]
                    and command[3] == "python"
                    and len(command) > 6
                    and command[6] == "/app/evelyn_core/runtime/evelyn_core"
                ):
                    return "f" * 64 + "\n"
                return normal(command, timeout_sec=timeout_sec)

            with (
                self.subTest(failure=failure),
                patch.object(benchmark, "_run_text", side_effect=run),
                patch.object(benchmark, "_get_json", return_value=health),
                self.assertRaises(benchmark._ValidationFailure),
            ):
                benchmark._observe_p0_4_environment(args)

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

    def test_p0_4_binding_and_relative_stt_gate_are_exact(self) -> None:
        old = self.p0_report("old-stt", stt_ms=1_000.0)
        old["binding"]["stt"]["runtimeSourceTreeSha256"] = "1" * 64
        old["binding"]["stt"]["sourceMatchesCheckout"] = False
        new = self.p0_report("new-stt", stt_ms=1_101.0)

        self.assertEqual(old["binding"]["phase"], "old-stt")
        self.assertEqual(old["binding"]["stt"]["backend"], "vllm")
        comparison = benchmark.compare_stt_baseline(
            old,
            new,
            baseline_sha256=new["baselineReportSha256"],
        )
        self.assertEqual(comparison["status"], "fail")
        self.assertEqual(comparison["maximumNewP95Ms"], 1_100.0)
        self.assertIn("stt_relative_regression", comparison["violations"])

        new["summary"]["sttFinalLatencyP95Ms"] = 1_100.0
        comparison = benchmark.compare_stt_baseline(
            old,
            new,
            baseline_sha256=new["baselineReportSha256"],
        )
        self.assertEqual(comparison["status"], "fail")
        self.assertIn("new-stt_sample_integrity_invalid", comparison["violations"])

        new = self.p0_report("new-stt", stt_ms=1_100.0)
        new["binding"]["stt"]["packageSetSha256"] = "f" * 64
        new["binding"]["stt"]["embeddedPackageSetSha256"] = "f" * 64
        self.assertEqual(
            benchmark.compare_stt_baseline(
                old,
                new,
                baseline_sha256=new["baselineReportSha256"],
            )["status"],
            "pass",
        )
        self.assertIn(
            "baseline_report_hash_mismatch",
            benchmark.compare_stt_baseline(
                old,
                new,
                baseline_sha256="8" * 64,
            )["violations"],
        )

        for section, field, violation in (
            ("main", "modelSha256", "main_runtime_identity_mismatch"),
            ("qwen", "modelSha256", "qwen_runtime_identity_mismatch"),
            ("stt", "modelContentSha256", "stt_runtime_identity_mismatch"),
        ):
            changed = self.p0_report("new-stt", stt_ms=1_100.0)
            changed["binding"][section][field] = "0" * 64
            result = benchmark.compare_stt_baseline(
                old,
                changed,
                baseline_sha256=changed["baselineReportSha256"],
            )
            self.assertIn(violation, result["violations"])

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

    def test_new_phase_rejects_wrong_baseline_hash_before_network(self) -> None:
        old = self.p0_report("old-stt", stt_ms=1_000.0)
        with tempfile.TemporaryDirectory() as directory:
            baseline_path = Path(directory) / "old.json"
            baseline_path.write_text(
                json.dumps(old, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            output_path = Path(directory) / "candidate.json"
            values = [
                *self._p0_values("new-stt"),
                "--baseline-report",
                str(baseline_path),
                "--baseline-sha256",
                "0" * 64,
                "--output",
                str(output_path),
            ]
            candidate_args = benchmark.parse_args(values)
            with (
                patch.object(
                    benchmark,
                    "_observe_p0_4_environment",
                    return_value=self.observed(candidate_args),
                ) as observe,
                patch.object(benchmark, "run_iteration") as run_iteration,
            ):
                result = benchmark.main(values)

            self.assertEqual(result, 2)
            observe.assert_not_called()
            run_iteration.assert_not_called()
            failure = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(failure["violations"], ["baseline_report_hash_mismatch"])

    def test_new_phase_rejects_output_alias_without_overwriting_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            baseline_path = Path(directory) / "old.json"
            original = b'{"baseline":"must-survive"}'
            baseline_path.write_bytes(original)
            values = [
                *self._p0_values("new-stt"),
                "--baseline-report",
                str(baseline_path),
                "--baseline-sha256",
                hashlib.sha256(original).hexdigest(),
                "--output",
                str(baseline_path),
            ]
            with (
                patch.object(benchmark, "_observe_p0_4_environment") as observe,
                self.assertRaises(SystemExit) as raised,
            ):
                benchmark.main(values)

            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(baseline_path.read_bytes(), original)
            observe.assert_not_called()

    def test_old_phase_postflight_drift_cannot_pass(self) -> None:
        values = self._p0_values("old-stt")
        args = benchmark.parse_args(values)
        preflight = self.observed(args)
        postflight = copy.deepcopy(preflight)
        postflight["containers"]["stt"]["startedAt"] = "2026-08-27T00:01:00Z"
        writes: list[dict] = []
        with (
            patch.object(
                benchmark,
                "_observe_p0_4_environment",
                side_effect=(preflight, postflight),
            ),
            patch.object(
                benchmark,
                "run_iteration",
                side_effect=lambda *_args, **_kwargs: self.iteration(),
            ),
            patch.object(
                benchmark,
                "_write_report",
                side_effect=lambda _path, report: writes.append(copy.deepcopy(report)),
            ),
        ):
            result = benchmark.main(values)

        self.assertEqual(result, 2)
        self.assertIn("validation_environment_drift", writes[-1]["violations"])

    def test_run_writes_running_state_before_measurements(self) -> None:
        writes: list[dict] = []
        with (
            patch.object(benchmark, "_observe_p0_4_environment") as observe,
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
        observe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
