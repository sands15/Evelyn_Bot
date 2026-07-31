from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.cloud_source_export import (
    ALLOWED_AUDIO_PATHS,
    ArchiveEntry,
    CloudSourceExportError,
    SubmodulePin,
    _build_manifest,
    _validate_entries,
    _write_zip,
    path_policy_violation,
    secret_content_violation,
)


class CloudSourcePathPolicyTests(unittest.TestCase):
    def test_allows_source_and_explicit_audio_fixtures(self) -> None:
        self.assertIsNone(path_policy_violation("evelyn_core/runtime/example.py"))
        self.assertIsNone(path_policy_violation(".env.example"))
        for path in ALLOWED_AUDIO_PATHS:
            with self.subTest(path=path):
                self.assertIsNone(path_policy_violation(path))

    def test_rejects_runtime_secrets_models_databases_and_audio(self) -> None:
        cases = {
            ".env": "environment_secret_file",
            ".env.production": "environment_secret_file",
            "config/auth.json": "credential_file",
            "deploy/service_account.json": "credential_file",
            "runtime_artifacts/status.json": "runtime_or_dependency_path",
            "package/node_modules/example/index.js": "runtime_or_dependency_segment",
            "models/assistant.gguf": "secret_database_or_model_file",
            "data/memory.sqlite3": "secret_database_or_model_file",
            "credentials/client.pem": "secret_database_or_model_file",
            "recordings/private.wav": "runtime_or_dependency_path",
            "assets/user_voice.wav": "unapproved_audio_file",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(path_policy_violation(path), expected)

    def test_rejects_unsafe_paths(self) -> None:
        for path in ("../secret.txt", "/absolute.txt", "folder\\secret.txt"):
            with self.subTest(path=path):
                with self.assertRaises(CloudSourceExportError):
                    path_policy_violation(path)


class CloudSourceSecretScannerTests(unittest.TestCase):
    def test_detects_high_confidence_provider_tokens_without_storing_one_in_test_source(self) -> None:
        samples = {
            "openai_api_key": b"sk-" + (b"A" * 24),
            "aws_access_key": b"AKIA" + (b"A" * 16),
            "github_token": b"ghp_" + (b"a" * 32),
            "slack_token": b"xoxb-" + (b"1" * 12) + b"-" + (b"a" * 20),
            "google_api_key": b"AIza" + (b"A" * 35),
            "huggingface_token": b"hf_" + (b"a" * 30),
            "private_key": b"-----BEGIN " + b"PRIVATE KEY-----",
        }
        for expected, sample in samples.items():
            with self.subTest(expected=expected):
                self.assertEqual(secret_content_violation(sample), expected)

    def test_does_not_reject_generic_test_placeholders(self) -> None:
        self.assertIsNone(secret_content_violation(b"token=fake-test-token"))
        self.assertIsNone(secret_content_violation(b"Bearer status-secret"))


class CloudSourceBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = [
            ArchiveEntry("README.md", b"Evelyn\n", 0o100644),
            ArchiveEntry("bin/run.sh", b"#!/bin/sh\n", 0o100755),
        ]
        self.pins = [
            SubmodulePin(
                path="external/mindcraft",
                url="https://github.com/mindcraft-bots/mindcraft.git",
                commit="a" * 40,
            )
        ]

    def test_manifest_contains_only_reproducible_source_metadata(self) -> None:
        manifest = json.loads(_build_manifest("b" * 40, self.pins, self.entries))
        self.assertEqual(manifest["schema"], "evelyn.cloud-source.v1")
        self.assertEqual(manifest["root"]["commit"], "b" * 40)
        self.assertEqual(manifest["content"]["fileCount"], 2)
        self.assertEqual(manifest["content"]["totalBytes"], sum(len(item.data) for item in self.entries))
        self.assertNotIn("createdAt", manifest)
        self.assertEqual(manifest["submodules"][0]["commit"], "a" * 40)

    def test_zip_bytes_are_deterministic_and_preserve_executable_mode(self) -> None:
        manifest = _build_manifest("b" * 40, self.pins, self.entries)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.zip"
            second = root / "second.zip"
            first_digest = _write_zip(first, self.entries, manifest)
            second_digest = _write_zip(second, list(reversed(self.entries)), manifest)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_digest, second_digest)
            with zipfile.ZipFile(first) as bundle:
                self.assertEqual(bundle.namelist(), ["README.md", "bin/run.sh", "cloud-source-manifest.json"])
                mode = bundle.getinfo("bin/run.sh").external_attr >> 16
                self.assertEqual(mode, 0o100755)

    def test_validation_reports_rule_and_path_but_not_secret_value(self) -> None:
        secret = b"sk-" + (b"Z" * 24)
        with self.assertRaises(CloudSourceExportError) as raised:
            _validate_entries([ArchiveEntry("config/example.txt", secret, 0o100644)])
        rendered = str(raised.exception)
        self.assertIn("config/example.txt", rendered)
        self.assertIn("openai_api_key", rendered)
        self.assertNotIn(secret.decode("ascii"), rendered)

    def test_validation_rejects_duplicate_paths(self) -> None:
        duplicate = ArchiveEntry("README.md", b"duplicate", 0o100644)
        with self.assertRaisesRegex(CloudSourceExportError, "duplicate archive path"):
            _validate_entries([*self.entries, duplicate])


if __name__ == "__main__":
    unittest.main()
