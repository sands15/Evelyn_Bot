from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.vision_remote_model_lock import (  # noqa: E402
    FALCON_OCR_REPO_ID,
    FALCON_OCR_REVISION,
    LockedModelFile,
    RemoteModelLock,
    VisionRemoteModelLockError,
    load_remote_model_lock,
    public_remote_model_status,
    validate_remote_model_configuration,
    verify_remote_model_snapshot,
)


LOCK_PATH = REPO_ROOT / "docker" / "falcon_ocr_snapshot.lock.json"


class VisionRemoteModelLockTests(unittest.TestCase):
    def _fixture_lock(self, root: Path) -> tuple[RemoteModelLock, dict[str, Path]]:
        files: list[LockedModelFile] = []
        paths: dict[str, Path] = {}
        for index, (name, role) in enumerate(
            (
                ("remote.py", "remote_code"),
                ("config.json", "configuration"),
                ("tokenizer.json", "tokenizer"),
                ("model.safetensors", "weights"),
            )
        ):
            content = f"locked-{index}".encode("ascii")
            path = root / name
            path.write_bytes(content)
            paths[name] = path
            files.append(
                LockedModelFile(
                    name=name,
                    role=role,
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
        return (
            RemoteModelLock(
                repo_id=FALCON_OCR_REPO_ID,
                revision=FALCON_OCR_REVISION,
                files=tuple(files),
            ),
            paths,
        )

    def test_repository_lock_is_exact_and_complete(self) -> None:
        lock = load_remote_model_lock(LOCK_PATH)

        self.assertEqual(lock.repo_id, FALCON_OCR_REPO_ID)
        self.assertEqual(lock.revision, FALCON_OCR_REVISION)
        self.assertEqual(len(lock.revision), 40)
        self.assertEqual(len(lock.files), 11)
        self.assertEqual(
            {item.name for item in lock.files},
            {
                "attention.py",
                "configuration_falcon_ocr.py",
                "modeling_falcon_ocr.py",
                "processing_falcon_ocr.py",
                "rope.py",
                "config.json",
                "model_args.json",
                "special_tokens_map.json",
                "tokenizer_config.json",
                "tokenizer.json",
                "model.safetensors",
            },
        )
        self.assertTrue(all(len(item.sha256) == 64 for item in lock.files))
        self.assertEqual(
            next(item for item in lock.files if item.role == "weights").sha256,
            "cc0d1d34c0406448de8129d2e28e98e59553a5c7654a8d0970f007a1d042f3cb",
        )

    def test_verifier_hashes_every_file_with_pinned_offline_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock, paths = self._fixture_lock(root)
            calls: list[tuple[object, ...]] = []

            def downloader(repo_id: str, name: str, **kwargs: object) -> str:
                calls.append((repo_id, name, kwargs))
                return str(paths[name])

            receipt = verify_remote_model_snapshot(
                lock,
                downloader=downloader,
                local_files_only=True,
                cache_dir=root,
            )

        self.assertTrue(receipt["verified"])
        self.assertTrue(receipt["revisionPinned"])
        self.assertTrue(receipt["localFilesOnly"])
        self.assertTrue(receipt["contentFree"])
        self.assertEqual(receipt["fileCount"], 4)
        self.assertEqual(receipt["weightFileCount"], 1)
        self.assertEqual(len(calls), 4)
        for repo_id, _name, kwargs in calls:
            self.assertEqual(repo_id, FALCON_OCR_REPO_ID)
            self.assertEqual(kwargs["revision"], FALCON_OCR_REVISION)
            self.assertIs(kwargs["local_files_only"], True)
            self.assertTrue(Path(str(kwargs["cache_dir"])).is_absolute())

    def test_verifier_fails_closed_on_tamper_or_missing_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock, paths = self._fixture_lock(root)
            paths["remote.py"].write_bytes(b"tampered")

            with self.assertRaises(VisionRemoteModelLockError) as caught:
                verify_remote_model_snapshot(
                    lock,
                    downloader=lambda _repo, name, **_kwargs: paths[name],
                    local_files_only=True,
                )
            self.assertEqual(
                caught.exception.code,
                "vision_remote_model_integrity_failed",
            )

            def unavailable(*_args: object, **_kwargs: object) -> str:
                raise FileNotFoundError

            with self.assertRaises(VisionRemoteModelLockError) as caught:
                verify_remote_model_snapshot(
                    lock,
                    downloader=unavailable,
                    local_files_only=True,
                )
            self.assertEqual(
                caught.exception.code,
                "vision_remote_model_snapshot_unavailable",
            )

    def test_configuration_rejects_repo_and_revision_drift(self) -> None:
        lock = load_remote_model_lock(LOCK_PATH)
        validate_remote_model_configuration(
            lock,
            repo_id=FALCON_OCR_REPO_ID,
            revision=FALCON_OCR_REVISION,
        )
        for repo_id, revision, code in (
            (
                "other/model",
                FALCON_OCR_REVISION,
                "vision_remote_model_repo_mismatch",
            ),
            (
                FALCON_OCR_REPO_ID,
                "0" * 40,
                "vision_remote_model_revision_mismatch",
            ),
            (
                FALCON_OCR_REPO_ID,
                "main",
                "vision_remote_model_revision_mismatch",
            ),
        ):
            with self.subTest(code=code):
                with self.assertRaises(VisionRemoteModelLockError) as caught:
                    validate_remote_model_configuration(
                        lock,
                        repo_id=repo_id,
                        revision=revision,
                    )
                self.assertEqual(caught.exception.code, code)

    def test_lock_loader_rejects_missing_corrupt_and_drifted_lock(self) -> None:
        payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing.json"
            with self.assertRaises(VisionRemoteModelLockError) as caught:
                load_remote_model_lock(missing)
            self.assertEqual(
                caught.exception.code,
                "vision_remote_model_lock_unavailable",
            )

            cases = []
            corrupt = dict(payload)
            corrupt["unexpected"] = True
            cases.append(corrupt)
            drifted = dict(payload)
            drifted["revision"] = "0" * 40
            cases.append(drifted)
            incomplete = dict(payload)
            incomplete["files"] = payload["files"][:-1]
            cases.append(incomplete)
            for index, candidate in enumerate(cases):
                path = root / f"bad-{index}.json"
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.subTest(index=index):
                    with self.assertRaises(VisionRemoteModelLockError) as caught:
                        load_remote_model_lock(path)
                    self.assertEqual(
                        caught.exception.code,
                        "vision_remote_model_lock_rejected",
                    )

    def test_public_status_is_content_free_and_allowlists_failures(self) -> None:
        status = public_remote_model_status(
            configured=True,
            local_files_only=True,
            receipt={
                "schema": "vision.remote-model-verification.v1",
                "verified": True,
                "contentFree": True,
                "fileCount": 11,
                "weightFileCount": 1,
                "secret": "must-not-escape",
            },
            failure_code="attacker-controlled-detail",
        )

        self.assertEqual(
            set(status),
            {
                "schema",
                "configured",
                "verified",
                "revisionPinned",
                "localFilesOnly",
                "fileCount",
                "weightVerified",
                "failureCode",
                "contentFree",
            },
        )
        self.assertTrue(status["verified"])
        self.assertEqual(status["failureCode"], "")
        self.assertNotIn("secret", status)


if __name__ == "__main__":
    unittest.main()
