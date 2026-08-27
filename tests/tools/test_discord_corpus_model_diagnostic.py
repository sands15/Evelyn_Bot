from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
TOOL_PATH = REPO_ROOT / "tools" / "discord_corpus_model_diagnostic.py"
SPEC = importlib.util.spec_from_file_location("discord_corpus_model_diagnostic", TOOL_PATH)
assert SPEC and SPEC.loader
diagnostic = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = diagnostic
SPEC.loader.exec_module(diagnostic)

PATH_CANARY = "PRIVATE_DISCORD_CORPUS_PATH_CANARY"
TRANSCRIPT_CANARY = "PRIVATE_TRANSCRIPT_CANARY"


def _write_wav(path: Path, *, index: int, sample_rate: int = 16_000) -> None:
    samples = np.zeros(1_600, dtype="<i2")
    samples[0] = index + 1
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())


def _write_corpus(root: Path, *, count: int = 10, sample_rate: int = 16_000) -> None:
    root.mkdir()
    hashes: list[str] = []
    for index in range(count):
        path = root / f"clip-{index + 1:04d}.wav"
        _write_wav(
            path,
            index=index,
            sample_rate=sample_rate,
        )
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    marker = {
        "schema": diagnostic.STAGING_MARKER_SCHEMA,
        "owner": diagnostic.STAGING_OWNER,
        "runId": "1" * 32,
        "attemptId": "test-attempt",
        "sourceRevision": "2" * 40,
        "captureToolSha256": diagnostic.CAPTURE_TOOL_SHA256,
        "itemCount": count,
        "audioSha256": hashes,
    }
    (root / diagnostic.STAGING_MARKER_NAME).write_text(
        json.dumps(marker, separators=(",", ":")),
        encoding="utf-8",
    )


def _health() -> dict:
    return {
        "ok": True,
        "ready": True,
        "model": diagnostic.STT_MODEL,
        "backend": diagnostic.STT_BACKEND,
        "maxAudioSec": 30.0,
        "engine": dict(diagnostic.STT_ENGINE),
        "errorCount": 0,
    }


class FakeHealth:
    def __init__(self, payloads: list[dict] | None = None) -> None:
        self.payloads = list(payloads or [_health(), _health()])
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.payloads.pop(0)


class FakeBatch:
    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.calls: list[dict] = []

    def __call__(self, audio, **kwargs):
        self.calls.append({"audio": np.asarray(audio), **kwargs})
        return {"text": self.texts[len(self.calls) - 1]}


class DiscordCorpusModelDiagnosticTests(unittest.TestCase):
    def test_exact_corpus_passes_with_one_private_batch_per_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory) / PATH_CANARY
            _write_corpus(corpus)
            health = FakeHealth()
            batch = FakeBatch(list(diagnostic.DOMAIN_PHRASES))

            report = diagnostic.run_diagnostic(
                corpus_dir=corpus,
                stt_url="http://127.0.0.1:8892",
                health=health,
                batch=batch,
            )

            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["schema"], diagnostic.REPORT_SCHEMA)
            self.assertIsNone(report["failureCode"])
            self.assertEqual(
                report["captureMarkerSha256"],
                hashlib.sha256(
                    (corpus / diagnostic.STAGING_MARKER_NAME).read_bytes()
                ).hexdigest(),
            )
            self.assertEqual(report["counts"]["validWavCount"], 10)
            self.assertEqual(report["counts"]["batchAttemptCount"], 10)
            self.assertEqual(report["counts"]["responseCount"], 10)
            self.assertEqual(report["counts"]["nonemptyCount"], 10)
            self.assertEqual(report["counts"]["matchedCount"], 10)
            self.assertEqual(report["counts"]["normalizedExactCount"], 10)
            self.assertEqual(report["counts"]["criticalEntityExactCount"], 10)
            self.assertEqual(
                report["counts"]["sameIndexStrictUniqueBestCount"], 10
            )
            self.assertEqual(report["counts"]["errorCount"], 0)
            self.assertEqual(len(health.calls), 2)
            self.assertEqual(len(batch.calls), 10)
            for call in batch.calls:
                self.assertEqual(call["sampling_rate"], 16_000)
                self.assertEqual(call["max_new_tokens"], 256)
                self.assertEqual(call["stage"], "validation-batch")
                self.assertEqual(call["language"], "Korean")
                self.assertIs(call["validation_bound"], True)
            serialized = diagnostic._render(report)
            self.assertNotIn(PATH_CANARY, serialized)
            self.assertTrue(
                all(phrase not in serialized for phrase in diagnostic.DOMAIN_PHRASES)
            )
            for forbidden_key in ('"path"', '"hash"', '"transcript"', '"items"'):
                self.assertNotIn(forbidden_key, serialized)
            self.assertEqual(len(tuple(corpus.iterdir())), 11)

    def test_near_miss_critical_entities_fail_even_when_similarity_is_high(self) -> None:
        replacements = (
            (0, "다이아몬드 곡괭이", "금 곡괭이"),
            (1, "열두 개", "두 개"),
            (7, "GPU 일 번", "GPU 이 번"),
            (9, "오후 세 시", "오후 네 시"),
        )
        for phrase_index, expected, near_miss in replacements:
            with self.subTest(phrase_index=phrase_index), tempfile.TemporaryDirectory() as directory:
                corpus = Path(directory) / "corpus"
                _write_corpus(corpus)
                texts = list(diagnostic.DOMAIN_PHRASES)
                texts[phrase_index] = texts[phrase_index].replace(expected, near_miss)
                batch = FakeBatch(texts)

                report = diagnostic.run_diagnostic(
                    corpus_dir=corpus,
                    stt_url="http://127.0.0.1:8892",
                    health=FakeHealth(),
                    batch=batch,
                )

                self.assertEqual(report["status"], "fail")
                self.assertEqual(report["failureCode"], "model_diagnostic_failed")
                self.assertEqual(report["counts"]["criticalEntityExactCount"], 9)
                self.assertFalse(report["gates"]["criticalEntityExact10"])
                self.assertEqual(len(batch.calls), 10)

    def test_reversed_actions_fail_even_when_entities_and_order_match(self) -> None:
        replacements = (
            (0, "찾아줘", "버려줘"),
            (5, "다시 확인해줘", "끊어줘"),
            (7, "확인해줘", "초기화해줘"),
        )
        for phrase_index, expected, reversed_action in replacements:
            with self.subTest(phrase_index=phrase_index), tempfile.TemporaryDirectory() as directory:
                corpus = Path(directory) / "corpus"
                _write_corpus(corpus)
                texts = list(diagnostic.DOMAIN_PHRASES)
                texts[phrase_index] = texts[phrase_index].replace(
                    expected,
                    reversed_action,
                )
                batch = FakeBatch(texts)

                report = diagnostic.run_diagnostic(
                    corpus_dir=corpus,
                    stt_url="http://127.0.0.1:8892",
                    health=FakeHealth(),
                    batch=batch,
                )

                self.assertEqual(report["status"], "fail")
                self.assertEqual(report["counts"]["criticalEntityExactCount"], 9)
                self.assertEqual(len(batch.calls), 10)

    def test_trailing_contradictions_cannot_pass_frozen_phrase_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory) / "corpus"
            _write_corpus(corpus)
            batch = FakeBatch(
                [f"{phrase} 말고 반대로 해줘" for phrase in diagnostic.DOMAIN_PHRASES]
            )

            report = diagnostic.run_diagnostic(
                corpus_dir=corpus,
                stt_url="http://127.0.0.1:8892",
                health=FakeHealth(),
                batch=batch,
            )

            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["counts"]["matchedCount"], 10)
            self.assertEqual(report["counts"]["criticalEntityExactCount"], 10)
            self.assertEqual(report["counts"]["normalizedExactCount"], 0)
            self.assertFalse(report["gates"]["normalizedExact10"])
            self.assertEqual(len(batch.calls), 10)

    def test_shifted_phrases_fail_content_and_order_without_mutating_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory) / "corpus"
            _write_corpus(corpus)
            shifted = list(diagnostic.DOMAIN_PHRASES[1:]) + [diagnostic.DOMAIN_PHRASES[0]]
            batch = FakeBatch(shifted)

            report = diagnostic.run_diagnostic(
                corpus_dir=corpus,
                stt_url="http://127.0.0.1:8892",
                health=FakeHealth(),
                batch=batch,
            )

            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["failureCode"], "model_diagnostic_failed")
            self.assertEqual(report["counts"]["nonemptyCount"], 10)
            self.assertLess(report["counts"]["matchedCount"], 10)
            self.assertEqual(
                report["counts"]["sameIndexStrictUniqueBestCount"], 0
            )
            self.assertEqual(len(batch.calls), 10)
            self.assertEqual(
                {path.name for path in corpus.iterdir()},
                {f"clip-{index:04d}.wav" for index in range(1, 11)}
                | {diagnostic.STAGING_MARKER_NAME},
            )

    def test_exact_file_set_and_canonical_wav_fail_before_network(self) -> None:
        for count, sample_rate in ((9, 16_000), (10, 8_000)):
            with self.subTest(count=count, sample_rate=sample_rate), tempfile.TemporaryDirectory() as directory:
                corpus = Path(directory) / "corpus"
                _write_corpus(corpus, count=count, sample_rate=sample_rate)
                health = FakeHealth()
                batch = FakeBatch(list(diagnostic.DOMAIN_PHRASES))

                report = diagnostic.run_diagnostic(
                    corpus_dir=corpus,
                    stt_url="http://127.0.0.1:8892",
                    health=health,
                    batch=batch,
                )

                self.assertEqual(report["status"], "fail")
                self.assertEqual(report["failureCode"], "corpus_invalid")
                self.assertEqual(health.calls, [])
                self.assertEqual(batch.calls, [])

    def test_oversized_wav_is_rejected_before_bounded_read_or_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory) / "corpus"
            _write_corpus(corpus)
            with (corpus / "clip-0001.wav").open("ab") as oversized:
                oversized.write(b"x" * diagnostic.MAX_WAV_BYTES)
            health = FakeHealth()
            batch = FakeBatch(list(diagnostic.DOMAIN_PHRASES))

            report = diagnostic.run_diagnostic(
                corpus_dir=corpus,
                stt_url="http://127.0.0.1:8892",
                health=health,
                batch=batch,
            )

            self.assertEqual(report["failureCode"], "corpus_invalid")
            self.assertEqual(health.calls, [])
            self.assertEqual(batch.calls, [])

    def test_health_rejects_bool_values_for_integer_engine_fields(self) -> None:
        for field in ("maxModelLen", "maxNumSeqs", "audioPerPrompt"):
            with self.subTest(field=field):
                payload = _health()
                payload["engine"][field] = True
                self.assertFalse(diagnostic._health_exact(payload))

    def test_pre_and_post_health_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory) / "corpus"
            _write_corpus(corpus)
            pre_bad = _health()
            pre_bad["engine"] = {**diagnostic.STT_ENGINE, "maxModelLen": 4096}
            batch = FakeBatch(list(diagnostic.DOMAIN_PHRASES))
            report = diagnostic.run_diagnostic(
                corpus_dir=corpus,
                stt_url="http://127.0.0.1:8892",
                health=FakeHealth([pre_bad]),
                batch=batch,
            )
            self.assertEqual(report["failureCode"], "stt_health_pre_invalid")
            self.assertEqual(batch.calls, [])

            post_bad = _health()
            post_bad["errorCount"] = 1
            batch = FakeBatch(list(diagnostic.DOMAIN_PHRASES))
            report = diagnostic.run_diagnostic(
                corpus_dir=corpus,
                stt_url="http://127.0.0.1:8892",
                health=FakeHealth([_health(), post_bad]),
                batch=batch,
            )
            self.assertEqual(report["failureCode"], "stt_health_post_invalid")
            self.assertEqual(len(batch.calls), 10)

    def test_request_failure_is_fixed_aggregate_only_and_never_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory) / PATH_CANARY
            _write_corpus(corpus)
            calls: list[dict] = []

            def batch(_audio, **kwargs):
                calls.append(dict(kwargs))
                if len(calls) == 1:
                    raise RuntimeError(TRANSCRIPT_CANARY)
                return {"text": diagnostic.DOMAIN_PHRASES[len(calls) - 1]}

            report = diagnostic.run_diagnostic(
                corpus_dir=corpus,
                stt_url="http://127.0.0.1:8892",
                health=FakeHealth(),
                batch=batch,
            )

            self.assertEqual(report["failureCode"], "stt_request_failed")
            self.assertEqual(report["counts"]["batchAttemptCount"], 10)
            self.assertEqual(report["counts"]["errorCount"], 1)
            self.assertEqual(len(calls), 10)
            serialized = diagnostic._render(report)
            self.assertNotIn(TRANSCRIPT_CANARY, serialized)
            self.assertNotIn(PATH_CANARY, serialized)
            self.assertEqual(len(tuple(corpus.iterdir())), 11)

    def test_staging_marker_hash_or_capture_tool_mismatch_fails_before_network(self) -> None:
        for field in ("audioSha256", "captureToolSha256"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                corpus = Path(directory) / "corpus"
                _write_corpus(corpus)
                marker_path = corpus / diagnostic.STAGING_MARKER_NAME
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                if field == "audioSha256":
                    marker[field][0] = "f" * 64
                else:
                    marker[field] = "f" * 64
                marker_path.write_text(json.dumps(marker), encoding="utf-8")
                health = FakeHealth()

                report = diagnostic.run_diagnostic(
                    corpus_dir=corpus,
                    stt_url="http://127.0.0.1:8892",
                    health=health,
                    batch=FakeBatch(list(diagnostic.DOMAIN_PHRASES)),
                )

                self.assertEqual(report["failureCode"], "corpus_invalid")
                self.assertEqual(health.calls, [])

    def test_cli_stdout_and_output_are_the_same_aggregate_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / PATH_CANARY
            output = root / "aggregate.json"
            _write_corpus(corpus)
            stdout = io.StringIO()
            batch = FakeBatch(list(diagnostic.DOMAIN_PHRASES))
            with patch.object(diagnostic, "fetch_health", side_effect=FakeHealth()), patch.object(
                diagnostic, "transcribe_audio16k_via_service", side_effect=batch
            ), contextlib.redirect_stdout(stdout):
                exit_code = diagnostic.main(
                    [
                        "--corpus-dir",
                        str(corpus),
                        "--stt-url",
                        "http://127.0.0.1:8892",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            emitted = json.loads(stdout.getvalue())
            persisted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(emitted, persisted)
            self.assertEqual(emitted["status"], "pass")
            self.assertNotIn(PATH_CANARY, stdout.getvalue())
            self.assertTrue(
                all(
                    phrase not in output.read_text(encoding="utf-8")
                    for phrase in diagnostic.DOMAIN_PHRASES
                )
            )

    def test_cli_refuses_to_overwrite_a_corpus_clip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory) / "corpus"
            _write_corpus(corpus)
            clip = corpus / "clip-0001.wav"
            original = clip.read_bytes()
            stdout = io.StringIO()
            with patch.object(diagnostic, "run_diagnostic") as run, contextlib.redirect_stdout(
                stdout
            ):
                exit_code = diagnostic.main(
                    [
                        "--corpus-dir",
                        str(corpus),
                        "--stt-url",
                        "http://127.0.0.1:8892",
                        "--output",
                        str(clip),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(json.loads(stdout.getvalue())["failureCode"], "invalid_arguments")
            self.assertEqual(clip.read_bytes(), original)
            run.assert_not_called()

    def test_cli_pins_private_audio_to_exact_loopback_stt_endpoint(self) -> None:
        parser = diagnostic.build_parser()
        accepted = parser.parse_args(
            [
                "--corpus-dir",
                "corpus",
                "--stt-url",
                "http://127.0.0.1:8892",
            ]
        )
        self.assertEqual(accepted.stt_url, "http://127.0.0.1:8892")
        for endpoint in (
            "http://localhost:8892",
            "http://stt:8892",
            "http://127.0.0.1:9999",
            "http://example.test:8892",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(
                diagnostic.DiagnosticFailure
            ):
                parser.parse_args(
                    ["--corpus-dir", "corpus", "--stt-url", endpoint]
                )


if __name__ == "__main__":
    unittest.main()
