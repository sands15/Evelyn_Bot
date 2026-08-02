from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import threading
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

from evelyn_core import voice_capture_consent_claim_lease as lease_module  # noqa: E402
from evelyn_core.voice_capture_consent_claim_lease import (  # noqa: E402
    VoiceCaptureConsentClaimLeaseBusy,
    VoiceCaptureConsentClaimLeaseTimeout,
    VoiceCaptureConsentClaimLeaseUnavailable,
    acquire_voice_capture_consent_claim_lease,
)


class VoiceCaptureConsentClaimLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_context_release_is_idempotent_and_path_is_stable(self) -> None:
        expected_path = (
            self.root
            / "voice_capture_consent"
            / "claim_lease.lock"
        )
        with acquire_voice_capture_consent_claim_lease(
            root=self.root
        ) as first:
            self.assertTrue(first.acquired)
            self.assertEqual(first.path, expected_path)
            with self.assertRaisesRegex(
                VoiceCaptureConsentClaimLeaseBusy,
                "^voice_capture_consent_claim_inflight$",
            ):
                acquire_voice_capture_consent_claim_lease(root=self.root)

        self.assertFalse(first.acquired)
        first.release()
        self.assertEqual(expected_path.read_bytes(), b"\0")
        successor = acquire_voice_capture_consent_claim_lease(root=self.root)
        self.assertTrue(successor.acquired)
        successor.release()

    def test_context_releases_when_callback_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "callback failed"):
            with acquire_voice_capture_consent_claim_lease(root=self.root):
                raise RuntimeError("callback failed")

        successor = acquire_voice_capture_consent_claim_lease(root=self.root)
        self.assertTrue(successor.acquired)
        successor.release()

    def test_blocking_mode_waits_for_same_process_owner(self) -> None:
        first = acquire_voice_capture_consent_claim_lease(root=self.root)
        acquired = []

        def wait_for_lease() -> None:
            with acquire_voice_capture_consent_claim_lease(
                root=self.root,
                blocking=True,
            ):
                acquired.append(True)

        worker = threading.Thread(target=wait_for_lease)
        worker.start()
        self.assertEqual(acquired, [])
        first.release()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(acquired, [True])

    def test_blocking_timeout_releases_process_mutex_for_retry(self) -> None:
        owner_lock = Mock()
        owner_lock.acquire.side_effect = lease_module.MinecraftOwnerLockBusy(
            "minecraft_owner_lock_busy"
        )
        with patch.object(
            lease_module,
            "MinecraftOwnerLock",
            return_value=owner_lock,
        ):
            with self.assertRaisesRegex(
                VoiceCaptureConsentClaimLeaseTimeout,
                "^voice_capture_consent_claim_lease_timeout$",
            ) as raised:
                acquire_voice_capture_consent_claim_lease(
                    root=self.root,
                    blocking=True,
                    timeout_sec=0.02,
                )

        self.assertEqual(
            raised.exception.code,
            "voice_capture_consent_claim_lease_timeout",
        )
        self.assertGreater(owner_lock.acquire.call_count, 1)
        successor = acquire_voice_capture_consent_claim_lease(root=self.root)
        self.assertTrue(successor.acquired)
        successor.release()

    def test_unavailable_backend_is_fixed_and_releases_process_mutex(
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
                VoiceCaptureConsentClaimLeaseUnavailable,
                "^voice_capture_consent_claim_lease_unavailable$",
            ) as raised:
                acquire_voice_capture_consent_claim_lease(root=self.root)

        self.assertEqual(
            raised.exception.code,
            "voice_capture_consent_claim_lease_unavailable",
        )
        successor = acquire_voice_capture_consent_claim_lease(root=self.root)
        self.assertTrue(successor.acquired)
        successor.release()

    def test_process_crash_releases_cross_process_lock(self) -> None:
        worker = textwrap.dedent(
            """
            import os
            import sys
            from pathlib import Path

            sys.path.insert(0, sys.argv[1])

            from evelyn_core.voice_capture_consent_claim_lease import (
                acquire_voice_capture_consent_claim_lease,
            )

            lease = acquire_voice_capture_consent_claim_lease(
                root=Path(sys.argv[2])
            )
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
                    "claim lease worker did not become ready: "
                    f"stdout={ready + stdout!r} stderr={stderr!r}"
                )

            with self.assertRaisesRegex(
                VoiceCaptureConsentClaimLeaseBusy,
                "^voice_capture_consent_claim_inflight$",
            ) as raised:
                acquire_voice_capture_consent_claim_lease(root=self.root)
            self.assertEqual(
                raised.exception.code,
                "voice_capture_consent_claim_inflight",
            )

            self.assertIsNotNone(process.stdin)
            process.stdin.write("\n")
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=10), 78)

            successor = acquire_voice_capture_consent_claim_lease(
                root=self.root
            )
            self.addCleanup(successor.release)
            self.assertTrue(successor.acquired)
            lock_path = successor.path
            successor.release()
            self.assertEqual(
                lock_path.read_bytes(),
                b"\0",
            )
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
