from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from tools import discord_corpus_model_diagnostic as diagnostic
from tools import discord_corpus_user_acceptance as acceptance


def _write_corpus(root: Path) -> None:
    root.mkdir()
    hashes: list[str] = []
    for index in range(10):
        path = root / f"clip-{index + 1:04d}.wav"
        samples = array("h", [0] * 1_600)
        samples[0] = index + 1
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16_000)
            wav.writeframes(samples.tobytes())
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    marker = {
        "schema": diagnostic.STAGING_MARKER_SCHEMA,
        "owner": diagnostic.STAGING_OWNER,
        "runId": "1" * 32,
        "attemptId": "acceptance-test",
        "sourceRevision": "2" * 40,
        "captureToolSha256": diagnostic.CAPTURE_TOOL_SHA256,
        "itemCount": 10,
        "audioSha256": hashes,
    }
    (root / diagnostic.STAGING_MARKER_NAME).write_text(
        json.dumps(marker, separators=(",", ":")),
        encoding="utf-8",
    )


def _failed_report(
    path: Path,
    corpus: Path,
    *,
    matched: int = 8,
    legacy: bool = False,
) -> None:
    report = diagnostic._empty_report()
    if legacy:
        report["schema"] = diagnostic.LEGACY_REPORT_SCHEMA
        report.pop("captureMarkerSha256")
    else:
        report["captureMarkerSha256"] = hashlib.sha256(
            (corpus / diagnostic.STAGING_MARKER_NAME).read_bytes()
        ).hexdigest()
    report["health"] = {"pre": True, "post": True}
    report["counts"].update(
        {
            "validWavCount": 10,
            "batchAttemptCount": 10,
            "responseCount": 10,
            "nonemptyCount": 10,
            "matchedCount": matched,
            "normalizedExactCount": 0,
            "criticalEntityExactCount": 0,
            "sameIndexStrictUniqueBestCount": 9,
            "errorCount": 0,
        }
    )
    report = diagnostic._finish_report(report, None)
    path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")


class DiscordCorpusUserAcceptanceTests(unittest.TestCase):
    def test_acceptance_is_content_bound_and_preserves_automated_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "private-corpus"
            report = root / "diagnostic.json"
            receipt_path = root / "acceptance.json"
            _write_corpus(corpus)
            _failed_report(report, corpus)
            report_before = report.read_bytes()

            receipt = acceptance.create_acceptance(
                diagnostic_report=report,
                capture_dir=corpus,
                output=receipt_path,
                accepted_at="2026-08-28T00:00:00Z",
            )

            self.assertEqual(report.read_bytes(), report_before)
            self.assertEqual(receipt["decision"], acceptance.DECISION)
            self.assertEqual(receipt["scope"], acceptance.SCOPE)
            self.assertEqual(receipt["automatedDiagnostic"]["status"], "fail")
            self.assertTrue(receipt["sameRunCryptographicBinding"])
            self.assertFalse(receipt["productionPromotionAuthorized"])
            self.assertEqual(
                acceptance.verify_acceptance(
                    diagnostic_report=report,
                    capture_dir=corpus,
                    receipt_path=receipt_path,
                ),
                receipt,
            )
            serialized = receipt_path.read_text(encoding="utf-8")
            for forbidden in (
                "private-corpus",
                "transcript",
                "channelId",
                '"audioSha256"',
            ):
                self.assertNotIn(forbidden, serialized)

    def test_source_change_makes_receipt_stale_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus"
            report = root / "diagnostic.json"
            receipt_path = root / "acceptance.json"
            _write_corpus(corpus)
            _failed_report(report, corpus)
            acceptance.create_acceptance(
                diagnostic_report=report,
                capture_dir=corpus,
                output=receipt_path,
            )
            receipt_before = receipt_path.read_bytes()
            _failed_report(report, corpus, matched=7)

            with self.assertRaisesRegex(acceptance.AcceptanceFailure, "receipt_stale"):
                acceptance.verify_acceptance(
                    diagnostic_report=report,
                    capture_dir=corpus,
                    receipt_path=receipt_path,
                )
            with self.assertRaisesRegex(acceptance.AcceptanceFailure, "output_exists"):
                acceptance.create_acceptance(
                    diagnostic_report=report,
                    capture_dir=corpus,
                    output=receipt_path,
                )
            self.assertEqual(receipt_path.read_bytes(), receipt_before)

    def test_cli_is_aggregate_only_and_rejects_operational_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "PRIVATE_PATH_CANARY"
            report = root / "diagnostic.json"
            receipt_path = root / "acceptance.json"
            _write_corpus(corpus)
            _failed_report(report, corpus)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = acceptance.main(
                    [
                        "create",
                        "--diagnostic-report",
                        str(report),
                        "--capture-dir",
                        str(corpus),
                        "--output",
                        str(receipt_path),
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertIn("acceptance_created", stdout.getvalue())
            self.assertNotIn("PRIVATE_PATH_CANARY", stdout.getvalue() + stderr.getvalue())

            broken = json.loads(report.read_text(encoding="utf-8"))
            broken["counts"]["errorCount"] = 1
            broken["gates"]["errorsZero"] = False
            report.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(acceptance.AcceptanceFailure, "diagnostic_invalid"):
                acceptance.create_acceptance(
                    diagnostic_report=report,
                    capture_dir=corpus,
                    output=root / "second.json",
                )

    def test_legacy_report_is_explicit_user_pairing_not_same_run_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus"
            report = root / "legacy-diagnostic.json"
            receipt_path = root / "acceptance.json"
            _write_corpus(corpus)
            _failed_report(report, corpus, legacy=True)

            receipt = acceptance.create_acceptance(
                diagnostic_report=report,
                capture_dir=corpus,
                output=receipt_path,
            )

            self.assertEqual(receipt["evidencePairing"], "explicit_user_pairing")
            self.assertFalse(receipt["sameRunCryptographicBinding"])
            self.assertEqual(
                receipt["automatedDiagnostic"]["schema"],
                diagnostic.LEGACY_REPORT_SCHEMA,
            )
            self.assertIn("contentDigestSha256", receipt)
            self.assertNotIn("receiptId", receipt)

    def test_v2_report_cannot_be_paired_with_a_different_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            report = root / "diagnostic.json"
            _write_corpus(first)
            _write_corpus(second)
            second_marker = json.loads(
                (second / diagnostic.STAGING_MARKER_NAME).read_text(encoding="utf-8")
            )
            second_marker["runId"] = "3" * 32
            (second / diagnostic.STAGING_MARKER_NAME).write_text(
                json.dumps(second_marker, separators=(",", ":")),
                encoding="utf-8",
            )
            _failed_report(report, first)

            with self.assertRaisesRegex(
                acceptance.AcceptanceFailure,
                "diagnostic_capture_mismatch",
            ):
                acceptance.create_acceptance(
                    diagnostic_report=report,
                    capture_dir=second,
                    output=root / "acceptance.json",
                )


if __name__ == "__main__":
    unittest.main()
