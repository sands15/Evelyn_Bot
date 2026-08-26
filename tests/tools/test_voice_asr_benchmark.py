from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
TOOL_PATH = REPO_ROOT / "tools" / "voice_asr_benchmark.py"
SPEC = importlib.util.spec_from_file_location("voice_asr_benchmark", TOOL_PATH)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)

REFERENCE_CANARY = "이블린 테스트 PRIVATE_TRANSCRIPT_CANARY"
PATH_CANARY = "PRIVATE_AUDIO_PATH_CANARY"


def _write_wav(path: Path, *, positive: bool, salt: int, sample_rate: int = 16_000) -> str:
    samples = np.zeros(160, dtype="<i2")
    samples[0] = 1_000 if positive else 0
    samples[1] = salt + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_corpus(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    index = 0
    for source_class, count in benchmark.POSITIVE_CLASSES.items():
        for _ in range(count):
            name = f"{PATH_CANARY}_{index:02d}.wav"
            digest = _write_wav(root / name, positive=True, salt=index)
            items.append(
                {
                    "kind": "positive",
                    "sourceClass": source_class,
                    "audio": name,
                    "audioSha256": digest,
                    "reference": REFERENCE_CANARY,
                    "entities": ["이블린"],
                }
            )
            index += 1
    for source_class, count in benchmark.NEGATIVE_CLASSES.items():
        for _ in range(count):
            name = f"negative_{index:02d}.wav"
            digest = _write_wav(root / name, positive=False, salt=index)
            items.append(
                {
                    "kind": "negative",
                    "sourceClass": source_class,
                    "audio": name,
                    "audioSha256": digest,
                    "reference": "",
                    "entities": [],
                }
            )
            index += 1
    payload = {"schema": benchmark.MANIFEST_SCHEMA, "items": items}
    (root / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return payload


class FakeService:
    def __init__(self) -> None:
        self.next_id = 0
        self.streams: dict[str, dict] = {}
        self.batch_kwargs: list[dict] = []
        self.events: list[str] = []

    def batch(self, audio, **kwargs):
        self.events.append("batch")
        self.batch_kwargs.append(dict(kwargs))
        return {"text": REFERENCE_CANARY if float(audio[0]) > 0 else ""}

    def start(self, **_kwargs):
        self.next_id += 1
        stream_id = f"stream-{self.next_id}"
        self.streams[stream_id] = {"revision": 0, "text": ""}
        self.events.append("start")
        return {
            "streamId": stream_id,
            "samplingRate": 16_000,
            "decoderProfile": "realtime-ko",
            "nextSequence": 0,
        }

    def push(self, pcm16, *, stream_id, **_kwargs):
        state = self.streams[stream_id]
        samples = np.asarray(pcm16, dtype="<i2")
        state["revision"] += 1
        state["text"] = REFERENCE_CANARY if int(samples[0]) > 0 else ""
        self.events.append("push")
        return {
            "revision": state["revision"],
            "text": state["text"],
            "isFinal": False,
        }

    def finish(self, *, stream_id, **_kwargs):
        state = self.streams.pop(stream_id)
        state["revision"] += 1
        self.events.append("finish")
        return {
            "revision": state["revision"],
            "text": state["text"],
            "isFinal": True,
        }

    def cancel(self, *, stream_id, **_kwargs):
        self.streams.pop(stream_id)
        self.events.append("cancel")
        return {"cancelled": True}

    def clients(self):
        return benchmark.ClientFunctions(
            batch=self.batch,
            start=self.start,
            push=self.push,
            finish=self.finish,
            cancel=self.cancel,
        )


def _manifest_sha256(root: Path) -> str:
    return hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()


def _config(*, retain: bool = True, manifest_sha256: str = "3" * 64) -> object:
    return benchmark.BenchmarkConfig(
        attempt_id="attempt-1",
        source_commit="1" * 40,
        image_sha256="2" * 64,
        gpu_uuid="GPU-12345678-1234-1234-1234-123456789abc",
        expected_manifest_sha256=manifest_sha256,
        timeout_sec=2.0,
        retain_private_audio=retain,
    )


class VoiceAsrManifestTests(unittest.TestCase):
    def test_manifest_accepts_only_the_exact_private_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "voice_asr"
            payload = _build_corpus(root)
            corpus = benchmark.load_corpus_manifest(root)

            self.assertEqual(len(corpus.items), 50)
            self.assertEqual(corpus.entity_occurrences, 40)

            payload["items"][0]["reference"] += " 이블린"
            (root / "manifest.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            self.assertEqual(benchmark.load_corpus_manifest(root).entity_occurrences, 41)

            with self.assertRaisesRegex(benchmark.ContractError, "manifest_invalid"):
                benchmark.load_corpus_manifest(
                    root,
                    expected_manifest_sha256="0" * 64,
                )

            (root / "unknown.bin").write_bytes(b"private")
            with self.assertRaisesRegex(benchmark.ContractError, "manifest_invalid"):
                benchmark.load_corpus_manifest(root)
            (root / "unknown.bin").unlink()

            payload["items"][0]["unknown"] = True
            (root / "manifest.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(benchmark.ContractError, "manifest_invalid"):
                benchmark.load_corpus_manifest(root)

    def test_manifest_rejects_traversal_hash_and_wav_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "voice_asr"
            payload = _build_corpus(root)
            manifest = root / "manifest.json"

            payload["items"][0]["audio"] = "../outside.wav"
            manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(benchmark.ContractError, "manifest_invalid"):
                benchmark.load_corpus_manifest(root)

            payload = _build_corpus_after_clear(root)
            original = root / payload["items"][0]["audio"]
            nested = root / "nested"
            nested.mkdir()
            moved = nested / original.name
            original.rename(moved)
            payload["items"][0]["audio"] = f"nested/{original.name}"
            manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(benchmark.ContractError, "manifest_invalid"):
                benchmark.load_corpus_manifest(root)
            moved.unlink()
            nested.rmdir()

            payload = _build_corpus_after_clear(root)
            payload["items"][0]["audioSha256"] = "0" * 64
            manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(benchmark.ContractError, "manifest_invalid"):
                benchmark.load_corpus_manifest(root)

            payload = _build_corpus_after_clear(root)
            first = root / payload["items"][0]["audio"]
            payload["items"][0]["audioSha256"] = _write_wav(
                first, positive=True, salt=0, sample_rate=8_000
            )
            manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(benchmark.ContractError, "manifest_invalid"):
                benchmark.load_corpus_manifest(root)

    def test_manifest_rejects_duplicate_audio_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "voice_asr"
            payload = _build_corpus(root)
            first = root / payload["items"][0]["audio"]
            second = root / payload["items"][1]["audio"]
            second.write_bytes(first.read_bytes())
            payload["items"][1]["audioSha256"] = payload["items"][0]["audioSha256"]
            (root / "manifest.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(benchmark.ContractError, "manifest_invalid"):
                benchmark.load_corpus_manifest(root)

    def test_cleanup_revalidates_the_exact_manifest_binding_before_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "voice_asr"
            _build_corpus(root)
            corpus = benchmark.load_corpus_manifest(root)
            extra = root / "late-private.wav"
            extra.write_bytes(b"late")

            config = _config(manifest_sha256=corpus.manifest_sha256)
            with self.assertRaisesRegex(benchmark.ContractError, "cleanup_failed"):
                benchmark.quarantine_bound_corpus(
                    corpus,
                    config,
                    validation_root=Path(directory),
                )
            self.assertTrue(all(item.path.exists() for item in corpus.items))

            extra.unlink()
            corpus = benchmark.load_corpus_manifest(root)
            config = _config(manifest_sha256=corpus.manifest_sha256)
            quarantine = benchmark.quarantine_bound_corpus(
                corpus,
                config,
                validation_root=Path(directory),
            )
            self.assertFalse(root.exists())
            self.assertEqual(benchmark.cleanup_quarantined_corpus(corpus, quarantine), 51)
            self.assertFalse(quarantine.exists())

    def test_partial_quarantine_cleanup_stays_fail_closed_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "voice_asr"
            _build_corpus(root)
            corpus = benchmark.load_corpus_manifest(root)
            config = _config(manifest_sha256=corpus.manifest_sha256)
            quarantine = benchmark.quarantine_bound_corpus(
                corpus,
                config,
                validation_root=Path(directory),
            )
            original_unlink = Path.unlink
            calls = [0]

            def fail_second(path, *args, **kwargs):
                calls[0] += 1
                if calls[0] == 2:
                    raise PermissionError("simulated")
                return original_unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", new=fail_second):
                with self.assertRaisesRegex(benchmark.ContractError, "cleanup_failed"):
                    benchmark.cleanup_quarantined_corpus(corpus, quarantine)

            self.assertFalse(root.exists())
            self.assertTrue(quarantine.exists())
            self.assertTrue((quarantine / "manifest.json").exists())
            self.assertGreater(len(list(quarantine.glob("*.wav"))), 0)


def _build_corpus_after_clear(root: Path) -> dict:
    for child in root.iterdir():
        if child.is_file():
            child.unlink()
    return _build_corpus(root)


class VoiceAsrClientContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_flags_and_stream_chunk_contract_are_fixed(self) -> None:
        service = FakeService()
        audio = np.array([0.1, 0.0], dtype=np.float32)
        text, _ = benchmark._batch_observation(
            audio, config=_config(), batch=service.batch
        )
        self.assertEqual(text, REFERENCE_CANARY)
        self.assertEqual(
            service.batch_kwargs[-1],
            {
                "service_url": benchmark.DEFAULT_STT_URL,
                "timeout_sec": 2.0,
                "sampling_rate": 16_000,
                "max_new_tokens": 256,
                "stage": "validation-batch",
                "language": "Korean",
                "validation_bound": True,
            },
        )

        seen_sleeps: list[float] = []
        now = [0.0]

        def monotonic():
            return now[0]

        def sleep(seconds):
            seen_sleeps.append(seconds)
            now[0] += seconds

        def push(_audio, *, sequence):
            return {"text": "" if sequence == 0 else "partial"}

        paced = benchmark._PacedPush(push, monotonic=monotonic, sleep=sleep)
        paced(b"x", sequence=0)
        paced(b"x", sequence=1)
        self.assertEqual(seen_sleeps, [0.5])
        self.assertEqual(paced.first_partial_ms, 500.0)

    async def test_cancel_drains_before_one_remote_cancel_then_successor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "voice_asr"
            _build_corpus(root)
            corpus = benchmark.load_corpus_manifest(root)
            service = FakeService()

            result = await benchmark._cancel_successor_smoke(
                corpus.items[0], config=_config(), clients=service.clients()
            )

            self.assertTrue(result["cancellationObserved"])
            self.assertEqual(result["startCount"], 2)
            self.assertTrue(result["streamIdsDistinct"])
            self.assertTrue(result["cancelTargetFirst"])
            self.assertEqual(result["remoteCancelCount"], 1)
            self.assertEqual(result["remoteCancelSuccessCount"], 1)
            self.assertTrue(result["taskDrained"])
            self.assertEqual(result["pendingTaskCount"], 0)
            self.assertTrue(result["batchFallbackUsable"])
            self.assertTrue(result["successorAuthoritative"])
            self.assertLess(service.events.index("push"), service.events.index("cancel"))
            self.assertEqual(service.streams, {})

    async def test_reused_successor_stream_identity_cannot_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "voice_asr"
            _build_corpus(root)
            corpus = benchmark.load_corpus_manifest(root)
            service = FakeService()

            original_start = service.start

            def reused_start(**kwargs):
                response = original_start(**kwargs)
                if service.next_id == 2:
                    state = service.streams.pop(response["streamId"])
                    response["streamId"] = "stream-1"
                    service.streams["stream-1"] = state
                return response

            clients = service.clients()
            clients = benchmark.ClientFunctions(
                batch=clients.batch,
                start=reused_start,
                push=clients.push,
                finish=clients.finish,
                cancel=clients.cancel,
            )
            result = await benchmark._cancel_successor_smoke(
                corpus.items[0], config=_config(), clients=clients
            )

            self.assertEqual(result["startCount"], 2)
            self.assertFalse(result["streamIdsDistinct"])

    async def test_non_authoritative_conflict_is_a_fixed_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "voice_asr"
            _build_corpus(root)
            corpus = benchmark.load_corpus_manifest(root)
            service = FakeService()

            async def conflict_stream(*_args, **_kwargs):
                return benchmark.CompletedSttStream(
                    final_text=REFERENCE_CANARY,
                    partial_text=REFERENCE_CANARY,
                    committed_text="이블린 ",
                    authoritative=False,
                    revision_count=2,
                    fallback_reason="stable_prefix_conflict",
                )

            clients = benchmark.ClientFunctions(
                batch=service.batch,
                start=service.start,
                push=service.push,
                finish=service.finish,
                cancel=service.cancel,
                stream=conflict_stream,
            )
            aggregates = benchmark._new_aggregates()
            await benchmark._process_item(
                corpus.items[0],
                config=_config(),
                clients=clients,
                aggregates=aggregates,
            )

            snapshot = benchmark._aggregates_snapshot(aggregates)
            self.assertEqual(snapshot["positive"]["stream"]["usableCount"], 0)
            self.assertEqual(
                snapshot["positive"]["stream"]["stablePrefixConflictCount"], 1
            )
            self.assertEqual(
                snapshot["failureCounts"]["stream_stable_prefix_conflict"], 1
            )


class VoiceAsrReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_manifest_digest_fails_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            validation_root = Path(directory) / "validation"
            private_root = validation_root / "voice_asr"
            _build_corpus(private_root)
            service = FakeService()

            report = await benchmark.run_benchmark(
                _config(retain=True, manifest_sha256="0" * 64),
                private_root=private_root,
                report_path=validation_root / "report.json",
                validation_root=validation_root,
                clients=service.clients(),
            )

            self.assertEqual(report["status"], "fail")
            self.assertEqual(service.events, [])

    async def test_report_is_aggregate_only_and_never_persists_private_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            validation_root = Path(directory) / "validation"
            private_root = validation_root / "voice_asr"
            report_path = validation_root / "report.json"
            _build_corpus(private_root)
            service = FakeService()

            report = await benchmark.run_benchmark(
                _config(
                    retain=True,
                    manifest_sha256=_manifest_sha256(private_root),
                ),
                private_root=private_root,
                report_path=report_path,
                validation_root=validation_root,
                clients=service.clients(),
            )

            serialized = report_path.read_text(encoding="utf-8")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["identity"]["model"], benchmark.P0_4_STT_MODEL)
            self.assertEqual(report["identity"]["backend"], "vllm")
            self.assertEqual(report["identity"]["memoryUtilization"], 0.35)
            self.assertNotIn(REFERENCE_CANARY, serialized)
            self.assertNotIn(PATH_CANARY, serialized)
            self.assertNotIn('"entities"', serialized)
            self.assertNotIn('"audio"', serialized)
            self._assert_numeric_projection(report["aggregates"])
            self.assertTrue(
                all(
                    kwargs["validation_bound"] is True
                    and kwargs["stage"] == "validation-batch"
                    for kwargs in service.batch_kwargs
                )
            )

    async def test_evaluation_is_durable_before_cleanup_and_write_failure_preserves_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            validation_root = Path(directory) / "validation"
            private_root = validation_root / "voice_asr"
            report_path = validation_root / "report.json"
            _build_corpus(private_root)
            events: list[str] = []
            config = _config(
                retain=False,
                manifest_sha256=_manifest_sha256(private_root),
            )

            def writer(_path, report):
                events.append(f"write:{report['phase']}")

            def quarantine(corpus, _config, **_kwargs):
                events.append("quarantine")
                return corpus.root.parent / "fake-quarantine"

            def cleanup(corpus, _quarantine):
                events.append("delete")
                return len(corpus.items) + 1

            with patch.object(benchmark, "_write_report", side_effect=writer), patch.object(
                benchmark, "quarantine_bound_corpus", side_effect=quarantine
            ), patch.object(benchmark, "cleanup_quarantined_corpus", side_effect=cleanup):
                await benchmark.run_benchmark(
                    config,
                    private_root=private_root,
                    report_path=report_path,
                    validation_root=validation_root,
                    clients=FakeService().clients(),
                )
            self.assertLess(events.index("write:evaluation"), events.index("quarantine"))
            self.assertLess(events.index("quarantine"), events.index("write:cleanup"))
            self.assertLess(events.index("write:cleanup"), events.index("delete"))
            self.assertEqual(events[0], "write:running")
            self.assertLess(events.index("write:preflight"), events.index("quarantine"))

            events.clear()

            def failing_writer(_path, report):
                events.append(f"write:{report['phase']}")
                if report["phase"] == "evaluation":
                    raise OSError("simulated")

            with patch.object(benchmark, "_write_report", side_effect=failing_writer), patch.object(
                benchmark, "quarantine_bound_corpus"
            ) as quarantine_mock, patch.object(benchmark, "cleanup_quarantined_corpus") as cleanup_mock:
                with self.assertRaises(OSError):
                    await benchmark.run_benchmark(
                        config,
                        private_root=private_root,
                        report_path=report_path,
                        validation_root=validation_root,
                        clients=FakeService().clients(),
                    )
            quarantine_mock.assert_not_called()
            cleanup_mock.assert_not_called()
            self.assertTrue(any(private_root.glob("*.wav")))

    async def test_cleanup_receipt_failure_preserves_the_whole_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            validation_root = Path(directory) / "validation"
            private_root = validation_root / "voice_asr"
            report_path = validation_root / "report.json"
            _build_corpus(private_root)
            config = _config(
                retain=False,
                manifest_sha256=_manifest_sha256(private_root),
            )
            original_writer = benchmark._write_report

            def fail_cleanup_receipt(path, report):
                if report["phase"] == "cleanup":
                    raise OSError("simulated")
                return original_writer(path, report)

            with patch.object(benchmark, "_write_report", side_effect=fail_cleanup_receipt):
                report = await benchmark.run_benchmark(
                    config,
                    private_root=private_root,
                    report_path=report_path,
                    validation_root=validation_root,
                    clients=FakeService().clients(),
                )

            quarantine = benchmark._quarantine_path(
                benchmark.Corpus(
                    root=private_root,
                    root_identity=(0, 0, 0, 0),
                    manifest_path=private_root / "manifest.json",
                    manifest_identity=(0, 0, 0, 0),
                    manifest_sha256=config.expected_manifest_sha256,
                    items=(),
                    entity_occurrences=0,
                ),
                config,
            )
            durable = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "fail")
            self.assertEqual(durable["status"], "fail")
            self.assertFalse(private_root.exists())
            self.assertTrue(quarantine.exists())
            self.assertEqual(len(list(quarantine.glob("*.wav"))), 50)
            self.assertTrue((quarantine / "manifest.json").exists())

    async def test_terminal_write_failure_leaves_no_durable_pass_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            validation_root = Path(directory) / "validation"
            private_root = validation_root / "voice_asr"
            report_path = validation_root / "report.json"
            _build_corpus(private_root)
            config = _config(
                retain=False,
                manifest_sha256=_manifest_sha256(private_root),
            )
            original_writer = benchmark._write_report

            def fail_terminal(path, report):
                if report["phase"] == "terminal":
                    raise OSError("simulated")
                return original_writer(path, report)

            with patch.object(benchmark, "_write_report", side_effect=fail_terminal):
                with self.assertRaises(OSError):
                    await benchmark.run_benchmark(
                        config,
                        private_root=private_root,
                        report_path=report_path,
                        validation_root=validation_root,
                        clients=FakeService().clients(),
                    )

            durable = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(durable["phase"], "cleanup")
            self.assertEqual(durable["status"], "running")
            self.assertFalse(durable["evaluation"]["passed"])
            self.assertFalse(private_root.exists())
            self.assertEqual(
                list(validation_root.glob(".voice_asr-quarantine-*")),
                [],
            )

    def test_cli_binds_identity_and_defaults_to_loopback_8892(self) -> None:
        args = benchmark.parse_args(
            [
                "--attempt",
                "attempt-7",
                "--source-commit",
                "1" * 40,
                "--image-sha256",
                "sha256:" + ("2" * 64),
                "--gpu-uuid",
                "GPU-12345678-1234-1234-1234-123456789abc",
                "--manifest-sha256",
                "3" * 64,
            ]
        )
        self.assertEqual(args.stt_url, "http://127.0.0.1:8892")
        self.assertEqual(args.image_sha256, "2" * 64)
        self.assertEqual(args.manifest_sha256, "3" * 64)

        for override in (
            ("--stt-url", "http://127.0.0.1:8893"),
            ("--timeout-sec", "61"),
            ("--timeout-sec", "nan"),
        ):
            with self.subTest(override=override), self.assertRaises(SystemExit):
                benchmark.parse_args(
                    [
                        "--attempt",
                        "attempt-7",
                        "--source-commit",
                        "1" * 40,
                        "--image-sha256",
                        "2" * 64,
                        "--gpu-uuid",
                        "GPU-12345678-1234-1234-1234-123456789abc",
                        "--manifest-sha256",
                        "3" * 64,
                        *override,
                    ]
                )

    def _assert_numeric_projection(self, value) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                self._assert_numeric_projection(nested)
            return
        self.assertTrue(value is None or isinstance(value, (bool, int, float)))


if __name__ == "__main__":
    unittest.main()
