from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import memory_provenance_correction as correction  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_HEAD_SCHEMA,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
    MemoryIntegrityAuthenticity,
    MemoryIntegrityAuthenticityError,
    load_memory_integrity_authenticity,
)


class MemoryIntegrityAuthenticityTests(unittest.TestCase):
    def setUp(self) -> None:
        env_patch = patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "false",
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    @contextmanager
    def configured(
        self,
        base: Path,
        *,
        bootstrap: bool,
        anchor: bool = True,
    ):
        key_path = base / "memory-integrity.key"
        if not key_path.exists():
            key_path.write_bytes(b"memory-integrity-test-key-32bytes!!")
        anchor_root = base / "external-anchor"
        anchor_root.mkdir(exist_ok=True)
        values = {
            MEMORY_INTEGRITY_KEY_FILE_ENV: str(key_path),
            MEMORY_INTEGRITY_BOOTSTRAP_ENV: (
                "true" if bootstrap else "false"
            ),
            MEMORY_INTEGRITY_ANCHOR_DIR_ENV: (
                str(anchor_root) if anchor else ""
            ),
        }
        with patch.dict(os.environ, values, clear=False):
            yield key_path, anchor_root

    @staticmethod
    def append(root: Path, change_id: str) -> None:
        persisted_change_id = (
            "provcorr-"
            + hashlib.sha256(change_id.encode("utf-8")).hexdigest()[:24]
        )
        correction._append_journal_event(
            {
                "eventType": "failed",
                "changeId": persisted_change_id,
                "failedAt": "2026-08-01T00:00:00Z",
                "errorCode": "memory_provenance_correction_failed",
            },
            root=root,
        )

    def test_unconfigured_empty_journal_remains_chain_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            overview = correction.memory_provenance_correction_overview(
                root=Path(tmp) / "memory"
            )

        self.assertTrue(overview["ok"])
        self.assertTrue(overview["journalChainReady"])
        self.assertEqual(overview["journalAuthenticity"], "unconfigured")
        self.assertFalse(overview["journalRollbackProtected"])

    def test_loader_rejects_key_and_anchor_inside_protected_root(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            protected = Path(tmp) / "protected"
            protected.mkdir()
            key_path = protected / "key"
            key_path.write_bytes(b"memory-integrity-test-key-32bytes!!")
            with self.assertRaises(MemoryIntegrityAuthenticityError) as key:
                load_memory_integrity_authenticity(
                    protected_root=protected,
                    environ={
                        MEMORY_INTEGRITY_KEY_FILE_ENV: str(key_path),
                    },
                )
            outside_key = Path(tmp) / "outside.key"
            outside_key.write_bytes(b"memory-integrity-test-key-32bytes!!")
            anchor = protected / "anchor"
            anchor.mkdir()
            with self.assertRaises(MemoryIntegrityAuthenticityError) as root:
                load_memory_integrity_authenticity(
                    protected_root=protected,
                    environ={
                        MEMORY_INTEGRITY_KEY_FILE_ENV: str(outside_key),
                        MEMORY_INTEGRITY_ANCHOR_DIR_ENV: str(anchor),
                    },
                )

        self.assertEqual(
            str(key.exception),
            "memory_provenance_correction_auth_key_file_rejected",
        )
        self.assertEqual(
            str(root.exception),
            "memory_provenance_correction_anchor_directory_rejected",
        )

    def test_loader_rejects_key_inside_writable_anchor_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            protected = base / "protected"
            protected.mkdir()
            anchor = base / "anchor"
            anchor.mkdir()
            key_path = anchor / "memory.key"
            key_path.write_bytes(b"memory-integrity-test-key-32bytes!!")

            with self.assertRaises(MemoryIntegrityAuthenticityError) as raised:
                load_memory_integrity_authenticity(
                    protected_root=protected,
                    environ={
                        MEMORY_INTEGRITY_KEY_FILE_ENV: str(key_path),
                        MEMORY_INTEGRITY_ANCHOR_DIR_ENV: str(anchor),
                    },
                )

        self.assertEqual(
            str(raised.exception),
            "memory_provenance_correction_anchor_directory_rejected",
        )

    def test_head_signature_detects_tampering(self) -> None:
        authenticity = MemoryIntegrityAuthenticity(
            key=b"memory-integrity-test-key-32bytes!!"
        )
        head = authenticity.sign_head(
            {
                "schema": "memory.provenance.correction-chain-head.v1",
                "sequence": 4,
                "eventHash": "a" * 64,
                "updatedAt": "2026-08-01T00:00:00Z",
                "contentFree": True,
            }
        )
        authenticity.verify_head(head)
        head["sequence"] = 3
        with self.assertRaises(MemoryIntegrityAuthenticityError) as raised:
            authenticity.verify_head(head)

        self.assertEqual(
            str(raised.exception),
            "memory_provenance_correction_auth_failed",
        )

    def test_bootstrap_signs_head_and_anchors_then_runs_strict(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            with self.configured(base, bootstrap=True):
                self.append(memory_root, "bootstrap")
                first = correction._journal_snapshot(memory_root)
                head = json.loads(
                    correction._chain_head_path(memory_root).read_text(
                        encoding="utf-8"
                    )
                )
                anchor_payload = json.loads(
                    (
                        base
                        / "external-anchor"
                        / "memory-provenance-corrections.json"
                    ).read_text(encoding="utf-8")
                )
            with self.configured(base, bootstrap=False):
                strict = correction._journal_snapshot(memory_root)

        self.assertEqual(head["schema"], MEMORY_INTEGRITY_HEAD_SCHEMA)
        self.assertEqual(first["headAuthenticity"], "verified")
        self.assertEqual(first["externalAnchorState"], "verified")
        self.assertEqual(
            set(anchor_payload),
            {
                "schema",
                "sequence",
                "eventHash",
                "updatedAt",
                "contentFree",
                "authAlgorithm",
                "authScope",
                "authKeyId",
                "authTag",
            },
        )
        self.assertTrue(anchor_payload["contentFree"])
        self.assertEqual(strict["headAuthenticity"], "verified")
        self.assertEqual(strict["externalAnchorState"], "verified")

    def test_signed_v2_event_rejects_duplicate_private_actor_without_rehash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            with self.configured(base, bootstrap=True) as (_key, anchor):
                correction._append_journal_event(
                    {
                        "eventType": "prepared",
                        "changeId": "provcorr-000000000000000000000009",
                        "action": "unlink",
                        "targetNoteId": "concept-0123456789abcdef",
                        "previousSourceIds": [],
                        "previousOriginSourceIds": [],
                        "newSourceIds": [],
                        "newOriginSourceIds": [],
                        "previousRevision": 0,
                        "nextRevision": 1,
                        "undoOfChangeId": "",
                        "actor": "control-page-user",
                        "preparedAt": "2026-08-01T00:00:00Z",
                        "contentFree": True,
                    },
                    root=memory_root,
                )
                journal_path = correction._journal_path(memory_root)
                original_raw = journal_path.read_text(encoding="utf-8")
                original_event = json.loads(original_raw)
                original_hash = original_event["eventHash"]
                head_path = correction._chain_head_path(memory_root)
                head_raw = head_path.read_bytes()
                anchor_path = anchor / "memory-provenance-corrections.json"
                anchor_raw = anchor_path.read_bytes()
                mutated_raw = original_raw.replace(
                    '"actor":"control-page-user"',
                    (
                        '"actor":"PRIVATE TRANSCRIPT CANARY",'
                        '"actor":"control-page-user"'
                    ),
                    1,
                )
                self.assertNotEqual(mutated_raw, original_raw)
                # A permissive parser keeps the valid last value, so the old
                # dict-only hash check could not see this raw-byte mutation.
                permissive = json.loads(mutated_raw)
                self.assertEqual(permissive["actor"], "control-page-user")
                self.assertEqual(
                    correction._event_hash(permissive),
                    original_hash,
                )
                journal_path.write_text(mutated_raw, encoding="utf-8")

            with self.configured(base, bootstrap=False):
                with self.assertRaises(
                    correction
                    .MemoryProvenanceCorrectionJournalIntegrityError
                ) as raised:
                    correction._journal_snapshot(memory_root)

            self.assertEqual(head_path.read_bytes(), head_raw)
            self.assertEqual(anchor_path.read_bytes(), anchor_raw)
            self.assertNotIn(b"PRIVATE", head_raw + anchor_raw)

        self.assertEqual(
            str(raised.exception),
            "memory_provenance_correction_journal_integrity_failed",
        )

    def test_signed_head_and_anchor_require_strict_canonical_json(
        self,
    ) -> None:
        for target in ("head", "anchor"):
            for mutation in ("duplicate-auth-tag", "whitespace"):
                with self.subTest(target=target, mutation=mutation):
                    with tempfile.TemporaryDirectory() as tmp:
                        base = Path(tmp)
                        memory_root = base / "memory"
                        with self.configured(
                            base,
                            bootstrap=True,
                        ) as (_key, anchor):
                            self.append(memory_root, "signed-metadata")
                            target_path = (
                                correction._chain_head_path(memory_root)
                                if target == "head"
                                else anchor
                                / "memory-provenance-corrections.json"
                            )
                            original = target_path.read_text(
                                encoding="utf-8"
                            )
                            payload = json.loads(original)
                            if mutation == "duplicate-auth-tag":
                                valid_tag = str(payload["authTag"])
                                mutated = original.replace(
                                    f'"authTag": "{valid_tag}"',
                                    (
                                        '"authTag": "PRIVATE CANARY",\n'
                                        f'  "authTag": "{valid_tag}"'
                                    ),
                                    1,
                                )
                                self.assertEqual(
                                    json.loads(mutated)["authTag"],
                                    valid_tag,
                                )
                            else:
                                mutated = " " + original
                                self.assertEqual(json.loads(mutated), payload)
                            target_path.write_text(
                                mutated,
                                encoding="utf-8",
                            )

                        with self.configured(base, bootstrap=False):
                            with self.assertRaises(
                                correction
                                .MemoryProvenanceCorrectionJournalIntegrityError
                            ) as raised:
                                correction._journal_snapshot(memory_root)

                    self.assertEqual(
                        str(raised.exception),
                        (
                            "memory_provenance_correction_"
                            "journal_integrity_failed"
                        )
                        if target == "head"
                        else (
                            "memory_provenance_correction_"
                            "anchor_record_rejected"
                        ),
                    )

    def test_existing_unsigned_head_requires_explicit_bootstrap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            self.append(memory_root, "unsigned")
            with self.configured(base, bootstrap=False):
                blocked = correction.memory_provenance_correction_overview(
                    root=memory_root
                )
            with self.configured(base, bootstrap=True):
                adopted = correction.memory_provenance_correction_overview(
                    root=memory_root
                )
            with self.configured(base, bootstrap=False):
                strict = correction._journal_snapshot(memory_root)

        self.assertFalse(blocked["ok"])
        self.assertEqual(
            blocked["error"],
            "memory_provenance_correction_auth_bootstrap_required",
        )
        self.assertTrue(adopted["ok"])
        self.assertEqual(adopted["journalAuthenticity"], "verified")
        self.assertTrue(adopted["journalAuthenticityConfigured"])
        self.assertEqual(
            adopted["journalExternalAnchorState"], "verified"
        )
        self.assertTrue(adopted["journalRollbackProtected"])
        self.assertEqual(strict["headAuthenticity"], "verified")
        self.assertEqual(strict["externalAnchorState"], "verified")

    def test_signed_head_without_memory_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            with self.configured(base, bootstrap=True):
                self.append(memory_root, "signed")
            with self.assertRaises(
                correction.MemoryProvenanceCorrectionJournalIntegrityError
            ) as raised:
                correction._journal_snapshot(memory_root)

        self.assertEqual(
            str(raised.exception),
            "memory_provenance_correction_auth_key_required",
        )

    def test_external_anchor_authentication_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            with self.configured(base, bootstrap=True) as (_key, anchor):
                self.append(memory_root, "anchored")
                anchor_path = anchor / "memory-provenance-corrections.json"
                payload = json.loads(anchor_path.read_text(encoding="utf-8"))
                payload["sequence"] = 0
                anchor_path.write_text(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
            with self.configured(base, bootstrap=False):
                blocked = correction.memory_provenance_correction_overview(
                    root=memory_root
                )

        self.assertFalse(blocked["ok"])
        self.assertEqual(
            blocked["error"],
            "memory_provenance_correction_anchor_auth_failed",
        )

    def test_signed_past_replay_is_rejected_by_external_anchor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            with self.configured(base, bootstrap=True):
                self.append(memory_root, "first")
                old_journal = correction._journal_path(memory_root).read_bytes()
                old_head = correction._chain_head_path(memory_root).read_bytes()
                self.append(memory_root, "second")
            correction._journal_path(memory_root).write_bytes(old_journal)
            correction._chain_head_path(memory_root).write_bytes(old_head)
            with self.configured(base, bootstrap=False):
                blocked = correction.memory_provenance_correction_overview(
                    root=memory_root
                )

        self.assertFalse(blocked["ok"])
        self.assertEqual(
            blocked["error"],
            "memory_provenance_correction_anchor_replay_detected",
        )
        self.assertTrue(blocked["journalAuthenticityConfigured"])
        self.assertTrue(blocked["journalExternalAnchorConfigured"])
        self.assertFalse(blocked["journalRollbackProtected"])

    def test_whole_journal_and_head_deletion_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            with self.configured(base, bootstrap=True):
                self.append(memory_root, "durable")
            correction._journal_path(memory_root).unlink()
            correction._chain_head_path(memory_root).unlink()
            with self.configured(base, bootstrap=False):
                blocked = correction.memory_provenance_correction_overview(
                    root=memory_root
                )

        self.assertFalse(blocked["ok"])
        self.assertEqual(
            blocked["error"],
            "memory_provenance_correction_anchor_replay_detected",
        )

    def test_missing_head_recovers_from_matching_external_anchor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            with self.configured(base, bootstrap=True):
                self.append(memory_root, "durable")
            correction._chain_head_path(memory_root).unlink()
            with self.configured(base, bootstrap=False):
                recoverable = correction._journal_snapshot(memory_root)
                overview = correction.memory_provenance_correction_overview(
                    root=memory_root
                )
                current = correction._journal_snapshot(memory_root)

        self.assertEqual(recoverable["headState"], "missing")
        self.assertEqual(
            recoverable["headAuthenticity"], "recoverable"
        )
        self.assertTrue(overview["ok"])
        self.assertEqual(current["headAuthenticity"], "verified")
        self.assertEqual(current["externalAnchorState"], "verified")

    def test_journal_ahead_of_head_recovers_head_and_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            with self.configured(base, bootstrap=True):
                self.append(memory_root, "first")
                original = correction._write_chain_head
                with patch.object(
                    correction,
                    "_write_chain_head",
                    side_effect=OSError("simulated head crash"),
                ):
                    with self.assertRaises(OSError):
                        self.append(memory_root, "second")
                lagging = correction._journal_snapshot(memory_root)
                with patch.object(
                    correction,
                    "_write_chain_head",
                    wraps=original,
                ):
                    recovered = (
                        correction.memory_provenance_correction_overview(
                            root=memory_root
                        )
                    )
                current = correction._journal_snapshot(memory_root)

        self.assertEqual(lagging["headState"], "lagging")
        self.assertTrue(recovered["ok"])
        self.assertEqual(current["headState"], "current")
        self.assertEqual(current["externalAnchorState"], "verified")

    def test_head_ahead_of_anchor_recovers_one_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            with self.configured(base, bootstrap=True):
                self.append(memory_root, "first")
                original = MemoryIntegrityAuthenticity.reconcile_anchor
                with patch.object(
                    MemoryIntegrityAuthenticity,
                    "reconcile_anchor",
                    side_effect=MemoryIntegrityAuthenticityError(
                        "memory_provenance_correction_anchor_unavailable"
                    ),
                ):
                    with self.assertRaises(
                        correction.MemoryProvenanceCorrectionJournalIntegrityError
                    ):
                        self.append(memory_root, "second")
                lagging = correction._journal_snapshot(memory_root)
                with patch.object(
                    MemoryIntegrityAuthenticity,
                    "reconcile_anchor",
                    autospec=True,
                    side_effect=original,
                ):
                    recovered = (
                        correction.memory_provenance_correction_overview(
                            root=memory_root
                        )
                    )
                current = correction._journal_snapshot(memory_root)

        self.assertEqual(lagging["externalAnchorState"], "lagging")
        self.assertTrue(recovered["ok"])
        self.assertEqual(current["externalAnchorState"], "verified")

    def test_fresh_process_rejects_signed_past_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            with self.configured(base, bootstrap=True) as (key, anchor):
                self.append(memory_root, "first")
                old_journal = correction._journal_path(memory_root).read_bytes()
                old_head = correction._chain_head_path(memory_root).read_bytes()
                self.append(memory_root, "second")
            correction._journal_path(memory_root).write_bytes(old_journal)
            correction._chain_head_path(memory_root).write_bytes(old_head)
            script = (
                "import json,sys;"
                f"sys.path.insert(0,{str(RUNTIME_ROOT)!r});"
                "from pathlib import Path;"
                "from evelyn_core.memory_provenance_correction "
                "import memory_provenance_correction_overview as view;"
                f"print(json.dumps(view(root=Path({str(memory_root)!r}))))"
            )
            env = dict(os.environ)
            env.update(
                {
                    MEMORY_INTEGRITY_KEY_FILE_ENV: str(key),
                    MEMORY_INTEGRITY_ANCHOR_DIR_ENV: str(anchor),
                    MEMORY_INTEGRITY_BOOTSTRAP_ENV: "false",
                }
            )
            result = json.loads(
                subprocess.check_output(
                    [sys.executable, "-c", script],
                    cwd=str(REPO_ROOT),
                    env=env,
                    text=True,
                )
            )

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "memory_provenance_correction_anchor_replay_detected",
        )


if __name__ == "__main__":
    unittest.main()
