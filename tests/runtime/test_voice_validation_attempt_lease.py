from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import voice_validation_attempt_lease as lease_module  # noqa: E402
from evelyn_core.voice_validation_attempt_lease import (  # noqa: E402
    VoiceValidationAttemptLeaseBusy,
    VoiceValidationAttemptLeaseUnavailable,
    acquire_attempt_lease,
    acquire_attempt_leases,
    attempt_binding_digest,
    normalize_attempt_binding,
)


class VoiceValidationAttemptLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    @staticmethod
    def binding(
        *,
        attempt: int = 1,
        attempt_id: str = "attempt-private-canary-1",
    ) -> dict[str, object]:
        return {
            "sessionId": "session-private-canary",
            "stepId": "step-private-canary",
            "attempt": attempt,
            "attemptId": attempt_id,
        }

    def test_normalized_digest_and_lock_path_are_canonical_and_content_free(
        self,
    ) -> None:
        canonical = self.binding()
        aliases = {
            "validation_session_id": " session-private-canary ",
            "validationStepId": " step-private-canary ",
            "validation_attempt": "1",
            "validationAttemptId": " attempt-private-canary-1 ",
        }

        normalized = normalize_attempt_binding(aliases)
        digest = attempt_binding_digest(aliases)

        self.assertEqual(normalized, canonical)
        self.assertEqual(digest, attempt_binding_digest(canonical))
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

        leases = acquire_attempt_lease(aliases, root=self.root)
        self.addCleanup(leases.release)
        lease = leases._leases[0]
        expected_path = (
            self.root
            / "voice_validation"
            / "attempt_locks"
            / digest
            / "owner_claim.lock"
        )

        self.assertEqual(lease.digest, digest)
        self.assertEqual(lease.path, expected_path)
        for raw_identifier in (
            "session-private-canary",
            "step-private-canary",
            "attempt-private-canary-1",
        ):
            self.assertNotIn(raw_identifier, str(lease.path))
        leases.release()
        self.assertEqual(lease.path.read_bytes(), b"\0")

    def test_same_process_contender_is_busy(self) -> None:
        holder = acquire_attempt_lease(self.binding(), root=self.root)
        self.addCleanup(holder.release)

        with self.assertRaisesRegex(
            VoiceValidationAttemptLeaseBusy,
            "^voice_validation_attempt_inflight$",
        ):
            acquire_attempt_lease(self.binding(), root=self.root)

        self.assertTrue(holder.acquired)

    def test_distinct_attempts_can_be_held_simultaneously(self) -> None:
        first = acquire_attempt_lease(self.binding(), root=self.root)
        second = acquire_attempt_lease(
            self.binding(attempt=2, attempt_id="attempt-private-canary-2"),
            root=self.root,
        )
        self.addCleanup(first.release)
        self.addCleanup(second.release)

        self.assertTrue(first.acquired)
        self.assertTrue(second.acquired)
        self.assertNotEqual(first._leases[0].digest, second._leases[0].digest)
        self.assertNotEqual(first._leases[0].path, second._leases[0].path)

    def test_multi_acquire_is_sorted_deduplicated_and_all_or_nothing(
        self,
    ) -> None:
        first = self.binding()
        first_alias = {
            "validationSessionId": first["sessionId"],
            "validationStepId": first["stepId"],
            "validationAttempt": first["attempt"],
            "validationAttemptId": first["attemptId"],
        }
        second = self.binding(
            attempt=2,
            attempt_id="attempt-private-canary-2",
        )
        expected_order = sorted(
            {attempt_binding_digest(first), attempt_binding_digest(second)}
        )
        attempted: list[str] = []
        released: list[str] = []
        fail_second = True

        class FakeOwnerLock:
            def __init__(fake_self, path: Path) -> None:
                fake_self.path = Path(path)

            def acquire(fake_self) -> None:
                digest = fake_self.path.parent.name
                attempted.append(digest)
                if fail_second and len(attempted) == 2:
                    raise lease_module.MinecraftOwnerLockBusy(
                        "minecraft_owner_lock_busy"
                    )

            def release(fake_self) -> None:
                released.append(fake_self.path.parent.name)

        with patch.object(
            lease_module,
            "MinecraftOwnerLock",
            new=FakeOwnerLock,
        ):
            with self.assertRaisesRegex(
                VoiceValidationAttemptLeaseBusy,
                "^voice_validation_attempt_inflight$",
            ):
                acquire_attempt_leases(
                    (second, first_alias, first),
                    root=self.root,
                )

            self.assertEqual(attempted, expected_order)
            self.assertEqual(released, expected_order[:1])

            attempted.clear()
            released.clear()
            fail_second = False
            leases = acquire_attempt_leases(
                (second, first_alias, first),
                root=self.root,
            )
            self.assertEqual(attempted, expected_order)
            self.assertEqual(
                [lease.digest for lease in leases._leases],
                expected_order,
            )
            leases.release()
            self.assertEqual(released, list(reversed(expected_order)))

    def test_unavailable_backend_fails_closed_and_releases_process_mutex(
        self,
    ) -> None:
        owner_lock = Mock()
        owner_lock.acquire.side_effect = (
            lease_module.MinecraftOwnerLockUnavailable(
                "minecraft_owner_lock_unavailable"
            )
        )
        with patch.object(
            lease_module,
            "MinecraftOwnerLock",
            return_value=owner_lock,
        ):
            with self.assertRaisesRegex(
                VoiceValidationAttemptLeaseUnavailable,
                "^voice_validation_attempt_lease_unavailable$",
            ):
                acquire_attempt_lease(self.binding(), root=self.root)

        owner_lock.release.assert_not_called()
        replacement = acquire_attempt_lease(self.binding(), root=self.root)
        self.assertTrue(replacement.acquired)
        replacement.release()

    def test_release_is_idempotent_and_allows_reacquire(self) -> None:
        first = acquire_attempt_lease(self.binding(), root=self.root)
        first_path = first._leases[0].path

        first.release()
        first.release()

        self.assertFalse(first.acquired)
        self.assertTrue(first_path.exists())
        second = acquire_attempt_lease(self.binding(), root=self.root)
        self.addCleanup(second.release)
        self.assertTrue(second.acquired)
        self.assertEqual(second._leases[0].path, first_path)

    def test_process_crash_releases_stable_os_lock(self) -> None:
        worker = textwrap.dedent(
            """
            import os
            import sys
            from pathlib import Path

            sys.path.insert(0, sys.argv[1])

            from evelyn_core.voice_validation_attempt_lease import (
                acquire_attempt_lease,
            )

            binding = {
                "sessionId": "session-private-canary",
                "stepId": "step-private-canary",
                "attempt": 1,
                "attemptId": "attempt-private-canary-1",
            }
            lease = acquire_attempt_lease(binding, root=Path(sys.argv[2]))
            print("READY", flush=True)
            sys.stdin.readline()
            os._exit(78)
            """
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                worker,
                str(RUNTIME_ROOT),
                str(self.root),
            ],
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertIsNotNone(process.stdout)
            ready = process.stdout.readline()
            if ready.strip() != "READY":
                stdout, stderr = process.communicate(timeout=10)
                self.fail(
                    "attempt lease worker did not become ready: "
                    f"stdout={ready + stdout!r} stderr={stderr!r}"
                )

            with self.assertRaisesRegex(
                VoiceValidationAttemptLeaseBusy,
                "^voice_validation_attempt_inflight$",
            ):
                acquire_attempt_lease(self.binding(), root=self.root)

            self.assertIsNotNone(process.stdin)
            process.stdin.write("\n")
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=10), 78)

            successor = acquire_attempt_lease(self.binding(), root=self.root)
            self.addCleanup(successor.release)
            lock_path = successor._leases[0].path
            self.assertTrue(successor.acquired)
            self.assertTrue(lock_path.exists())
            successor.release()
            self.assertEqual(lock_path.read_bytes(), b"\0")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            for stream in (
                process.stdin,
                process.stdout,
                process.stderr,
            ):
                if stream is not None:
                    stream.close()


if __name__ == "__main__":
    unittest.main()
