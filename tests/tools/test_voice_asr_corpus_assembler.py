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
from array import array
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
TOOL_PATH = REPO_ROOT / "tools" / "voice_asr_corpus_assembler.py"
SPEC = importlib.util.spec_from_file_location("voice_asr_corpus_assembler", TOOL_PATH)
assert SPEC and SPEC.loader
assembler = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = assembler
SPEC.loader.exec_module(assembler)

PRIVATE_TRANSCRIPT_CANARY = "이블린 PRIVATE_TRANSCRIPT_CANARY"
PRIVATE_PATH_CANARY = "PRIVATE_AUDIO_PATH_CANARY"


def _write_wav(path: Path, *, salt: int, sample_rate: int = 16_000) -> None:
    samples = array("h", [0] * 160)
    samples[0] = salt + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())


def _build_selection(staging: Path) -> Path:
    items: list[dict] = []
    index = 0
    for source_class, count in assembler.benchmark.POSITIVE_CLASSES.items():
        for _ in range(count):
            relative = Path("captures") / f"{PRIVATE_PATH_CANARY}_{index:02d}.wav"
            _write_wav(staging / relative, salt=index)
            items.append(
                {
                    "source": relative.as_posix(),
                    "kind": "positive",
                    "sourceClass": source_class,
                    "reference": PRIVATE_TRANSCRIPT_CANARY,
                    "entities": ["이블린"],
                }
            )
            index += 1
    for source_class, count in assembler.benchmark.NEGATIVE_CLASSES.items():
        for _ in range(count):
            relative = Path("captures") / f"negative_{index:02d}.wav"
            _write_wav(staging / relative, salt=index)
            items.append(
                {
                    "source": relative.as_posix(),
                    "kind": "negative",
                    "sourceClass": source_class,
                    "reference": "",
                    "entities": [],
                }
            )
            index += 1
    selection = staging / "selection.json"
    selection.write_text(
        json.dumps(
            {"schema": assembler.SELECTION_SCHEMA, "items": items},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return selection


def _payload(selection: Path) -> dict:
    return json.loads(selection.read_text(encoding="utf-8"))


def _write_payload(selection: Path, payload: dict) -> None:
    selection.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class VoiceAsrCorpusAssemblerTests(unittest.TestCase):
    def test_assembles_then_preflights_the_exact_flat_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = _build_selection(root / "private-staging")
            final = root / "validation" / "voice_asr"

            digest = assembler.assemble_corpus(selection, final_root=final)

            self.assertEqual(
                digest,
                hashlib.sha256((final / "manifest.json").read_bytes()).hexdigest(),
            )
            corpus = assembler.benchmark.load_corpus_manifest(
                final,
                expected_manifest_sha256=digest,
            )
            self.assertEqual(len(corpus.items), 50)
            self.assertEqual(
                sorted(path.name for path in final.iterdir()),
                [*(f"{index:03d}.wav" for index in range(50)), "manifest.json"],
            )
            self.assertFalse((final.parent / assembler._PARTIAL_NAME).exists())
            manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
            for item in manifest["items"]:
                self.assertEqual(
                    item["audioSha256"],
                    hashlib.sha256((final / item["audio"]).read_bytes()).hexdigest(),
                )

    def test_cli_output_is_content_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = _build_selection(root / PRIVATE_PATH_CANARY)
            final = root / "validation" / "voice_asr"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch.object(assembler, "_fresh_process_preflight"),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = assembler.main([str(selection)], final_root=final)

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue() + stderr.getvalue()
            self.assertNotIn(PRIVATE_TRANSCRIPT_CANARY, output)
            self.assertNotIn(PRIVATE_PATH_CANARY, output)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["itemCount"], 50)
            self.assertRegex(result["manifestSha256"], r"^[0-9a-f]{64}$")

    def test_existing_final_or_partial_is_never_modified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = _build_selection(root / "private-staging")
            final = root / "validation" / "voice_asr"
            final.mkdir(parents=True)
            sentinel = final / "private-partial.bin"
            sentinel.write_bytes(b"keep")

            with self.assertRaisesRegex(assembler.AssemblyError, "output_exists"):
                assembler.assemble_corpus(selection, final_root=final)
            self.assertEqual(sentinel.read_bytes(), b"keep")

            sentinel.unlink()
            final.rmdir()
            partial = final.parent / assembler._PARTIAL_NAME
            partial.mkdir()
            partial_sentinel = partial / "private-partial.bin"
            partial_sentinel.write_bytes(b"keep-partial")
            with self.assertRaisesRegex(assembler.AssemblyError, "partial_exists"):
                assembler.assemble_corpus(selection, final_root=final)
            self.assertEqual(partial_sentinel.read_bytes(), b"keep-partial")

    def test_selection_cannot_escape_its_private_staging_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "private-staging"
            selection = _build_selection(staging)
            outside = root / "outside.wav"
            _write_wav(outside, salt=100)
            payload = _payload(selection)
            payload["items"][0]["source"] = "../outside.wav"
            _write_payload(selection, payload)
            final = root / "validation" / "voice_asr"

            with self.assertRaisesRegex(assembler.AssemblyError, "selection_invalid"):
                assembler.assemble_corpus(selection, final_root=final)
            self.assertFalse(final.exists())
            self.assertFalse((final.parent / assembler._PARTIAL_NAME).exists())

    def test_loader_rejects_count_wav_and_hash_failures_before_publish(self) -> None:
        for failure in ("count", "wav", "hash"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                staging = root / "private-staging"
                selection = _build_selection(staging)
                payload = _payload(selection)
                expected_error = "corpus_invalid"
                if failure == "count":
                    payload["items"].pop()
                    _write_payload(selection, payload)
                    expected_error = "selection_invalid"
                elif failure == "wav":
                    source = staging / Path(payload["items"][0]["source"])
                    _write_wav(source, salt=0, sample_rate=8_000)
                else:
                    first = staging / Path(payload["items"][0]["source"])
                    second = staging / Path(payload["items"][1]["source"])
                    second.write_bytes(first.read_bytes())
                final = root / "validation" / "voice_asr"

                with self.assertRaisesRegex(assembler.AssemblyError, expected_error):
                    assembler.assemble_corpus(selection, final_root=final)
                self.assertFalse(final.exists())
                self.assertFalse((final.parent / assembler._PARTIAL_NAME).exists())

    def test_fresh_process_failure_cleans_only_the_owned_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "private-staging"
            selection = _build_selection(staging)
            final = root / "validation" / "voice_asr"

            with (
                patch.object(
                    assembler,
                    "_fresh_process_preflight",
                    side_effect=assembler.AssemblyError("preflight_failed"),
                ),
                self.assertRaisesRegex(assembler.AssemblyError, "preflight_failed"),
            ):
                assembler.assemble_corpus(selection, final_root=final)

            self.assertTrue(selection.exists())
            self.assertFalse(final.exists())
            self.assertFalse((final.parent / assembler._PARTIAL_NAME).exists())


if __name__ == "__main__":
    unittest.main()
