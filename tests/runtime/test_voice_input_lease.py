from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_input_lease import (  # noqa: E402
    DiscordVoiceInputLeaseClient,
    VoiceInputLeaseError,
    VoiceInputLeaseManager,
    VoiceInputObservation,
)
from evelyn_core.durable_artifact_process import (  # noqa: E402
    DurableArtifactProcess,
    DurableArtifactProcessTimeout,
)


INACTIVE = {
    "local_mic": VoiceInputObservation("inactive", "a" * 32),
    "discord_voice": VoiceInputObservation("inactive", "b" * 32),
}
FAULT_WORKER = (
    REPO_ROOT / "tests" / "fixtures" / "durable_artifact_fault_worker.py"
)
PYTHON_EXECUTABLE = str(
    getattr(sys, "_base_executable", "") or sys.executable
)


class VoiceInputLeaseManagerTests(unittest.TestCase):
    def manager(self, root: str) -> VoiceInputLeaseManager:
        return VoiceInputLeaseManager(
            state_path=Path(root) / "voice-input-owner.json",
            now=lambda: 123.0,
        )

    def fault_process(
        self,
        root: Path,
        target: Path,
        scenario: str,
    ) -> DurableArtifactProcess:
        process = DurableArtifactProcess(
            deadline_sec=0.8,
            start_timeout_sec=3.0,
            command=(
                PYTHON_EXECUTABLE,
                "-u",
                str(FAULT_WORKER),
                "--scenario",
                scenario,
                "--state",
                str(root / "fault-state.json"),
                "--target",
                str(target),
            ),
        )
        self.addCleanup(process.close)
        return process

    def test_stalled_write_is_bounded_and_latches_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "voice-input-owner.json"
            process = self.fault_process(
                root,
                state_path,
                "stall_before_replace_once",
            )
            manager = VoiceInputLeaseManager(
                state_path=state_path,
                artifact_process=process,
                artifact_deadline_sec=0.8,
            )

            started_at = time.monotonic()
            with self.assertRaises(DurableArtifactProcessTimeout):
                manager.observe(INACTIVE)

            self.assertLess(time.monotonic() - started_at, 5.0)
            self.assertEqual(
                manager.public_status(),
                {"state": "blocked", "source": ""},
            )
            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_unavailable",
            ):
                manager.acquire(
                    "discord_voice",
                    "b" * 32,
                    observations=INACTIVE,
                )

    def test_post_replace_stall_reconciles_exact_lease_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "voice-input-owner.json"
            process = self.fault_process(
                root,
                state_path,
                "stall_after_replace_once",
            )
            manager = VoiceInputLeaseManager(
                state_path=state_path,
                artifact_process=process,
                artifact_deadline_sec=0.8,
            )

            status = manager.observe(INACTIVE)

            self.assertEqual(status, {"state": "unowned", "source": ""})
            self.assertEqual(
                VoiceInputLeaseManager(
                    state_path=state_path,
                ).public_status(),
                status,
            )

    def test_stalled_load_is_bounded_and_recovers_read_only_retry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "voice-input-owner.json"
            owner = VoiceInputLeaseManager(state_path=state_path)
            owner.acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )
            before = state_path.read_bytes()
            process = self.fault_process(
                root,
                state_path,
                "stall_read_once",
            )

            started_at = time.monotonic()
            restored = VoiceInputLeaseManager(
                state_path=state_path,
                artifact_process=process,
                artifact_deadline_sec=0.8,
            )

            self.assertLess(time.monotonic() - started_at, 5.0)
            self.assertEqual(
                restored.public_status(),
                {"state": "owned", "source": "discord_voice"},
            )
            self.assertEqual(state_path.read_bytes(), before)

    def test_unreadable_canonical_state_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "voice-input-owner.json"
            owner = VoiceInputLeaseManager(state_path=state_path)
            owner.acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )
            before = state_path.read_bytes()

            with patch(
                "evelyn_core.voice_input_lease.read_bounded_text",
                side_effect=OSError("disk unavailable"),
            ):
                restored = VoiceInputLeaseManager(state_path=state_path)

            self.assertEqual(
                restored.public_status(),
                {"state": "blocked", "source": ""},
            )
            self.assertEqual(restored.observe(INACTIVE)["state"], "blocked")
            self.assertEqual(state_path.read_bytes(), before)

    def test_only_one_source_wins_a_concurrent_first_acquire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)

            def acquire(source: str, instance_id: str):
                try:
                    return manager.acquire(
                        source,
                        instance_id,
                        observations=INACTIVE,
                    )["source"]
                except VoiceInputLeaseError as exc:
                    return exc.code

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda item: acquire(*item),
                        (
                            ("local_mic", "a" * 32),
                            ("discord_voice", "b" * 32),
                        ),
                    )
                )

            self.assertEqual(
                sum(value in {"local_mic", "discord_voice"} for value in results),
                1,
            )
            self.assertEqual(results.count("voice_input_lease_conflict"), 1)

    def test_owner_survives_restart_and_exact_release_allows_handover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt = self.manager(tmp).acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )
            restarted = self.manager(tmp)

            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_conflict",
            ):
                restarted.acquire(
                    "local_mic",
                    "a" * 32,
                    observations=INACTIVE,
                )
            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_mismatch",
            ):
                restarted.release(
                    "discord_voice",
                    "b" * 32,
                    "c" * 32,
                )

            restarted.release(
                "discord_voice",
                "b" * 32,
                receipt["leaseId"],
            )
            local = restarted.acquire(
                "local_mic",
                "a" * 32,
                observations=INACTIVE,
            )
            self.assertEqual(local["source"], "local_mic")

    def test_verified_hard_crash_retirement_unstrands_durable_discord_owner(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.manager(tmp).acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )
            restarted = self.manager(tmp)
            stale_after_hard_crash = {
                "local_mic": VoiceInputObservation(
                    "inactive",
                    "a" * 32,
                ),
                "discord_voice": VoiceInputObservation("unknown"),
            }

            restarted.observe(stale_after_hard_crash)
            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_conflict",
            ):
                restarted.acquire(
                    "local_mic",
                    "a" * 32,
                    observations=stale_after_hard_crash,
                )

            claim = restarted.prepare_retirement("discord_voice")
            self.assertTrue(claim["required"])
            self.assertNotIn("instanceId", claim)
            self.assertNotIn("leaseId", claim)
            retired = restarted.complete_retirement(claim["claimId"])
            self.assertTrue(retired["retired"])
            local = restarted.acquire(
                "local_mic",
                "a" * 32,
                observations=INACTIVE,
            )
            self.assertEqual(local["source"], "local_mic")

    def test_retirement_claim_cannot_release_a_successor_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            old = manager.acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )
            claim = manager.prepare_retirement("discord_voice")
            manager.release(
                "discord_voice",
                "b" * 32,
                old["leaseId"],
            )
            successor_observations = {
                "local_mic": VoiceInputObservation(
                    "inactive",
                    "a" * 32,
                ),
                "discord_voice": VoiceInputObservation(
                    "inactive",
                    "c" * 32,
                ),
            }
            successor = manager.acquire(
                "discord_voice",
                "c" * 32,
                observations=successor_observations,
            )

            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_retirement_stale",
            ):
                manager.complete_retirement(claim["claimId"])
            current = manager.acquire(
                "discord_voice",
                "c" * 32,
                observations=successor_observations,
            )
            self.assertEqual(current["leaseId"], successor["leaseId"])

    def test_expired_retirement_claim_never_expires_the_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clock = [123.0]
            manager = VoiceInputLeaseManager(
                state_path=Path(tmp) / "voice-input-owner.json",
                now=lambda: clock[0],
            )
            manager.acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )
            claim = manager.prepare_retirement("discord_voice")
            clock[0] = float(claim["expiresAt"]) + 1.0

            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_retirement_claim_invalid",
            ):
                manager.complete_retirement(claim["claimId"])
            self.assertEqual(
                manager.public_status(),
                {"state": "owned", "source": "discord_voice"},
            )

    def test_unknown_bootstrap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_unavailable",
            ):
                manager.acquire(
                    "discord_voice",
                    "b" * 32,
                    observations={
                        "local_mic": VoiceInputObservation("unknown"),
                        "discord_voice": VoiceInputObservation(
                            "inactive",
                            "b" * 32,
                        ),
                    },
                )

    def test_local_owner_releases_only_through_physical_stop_ack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.acquire(
                "local_mic",
                "a" * 32,
                observations=INACTIVE,
            )
            manager.observe(
                {
                    **INACTIVE,
                    "local_mic": VoiceInputObservation(
                        "active",
                        "a" * 32,
                    ),
                }
            )
            self.assertEqual(manager.public_status()["source"], "local_mic")
            manager.observe(
                INACTIVE
            )
            self.assertEqual(manager.public_status()["source"], "local_mic")
            manager.release_if_inactive(
                "local_mic",
                "a" * 32,
                observations=INACTIVE,
            )
            self.assertEqual(manager.public_status()["state"], "unowned")
            receipt = manager.acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )
            self.assertEqual(receipt["source"], "discord_voice")

    def test_unowned_unknown_observation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.observe(INACTIVE)

            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_unavailable",
            ):
                manager.acquire(
                    "discord_voice",
                    "b" * 32,
                    observations={
                        "local_mic": VoiceInputObservation("unknown"),
                        "discord_voice": VoiceInputObservation(
                            "inactive",
                            "b" * 32,
                        ),
                    },
                )

    def test_same_owner_reacquire_rejects_unknown_peer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )

            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_unavailable",
            ):
                manager.acquire(
                    "discord_voice",
                    "b" * 32,
                    observations={
                        "local_mic": VoiceInputObservation("unknown"),
                        "discord_voice": VoiceInputObservation(
                            "inactive",
                            "b" * 32,
                        ),
                    },
                )
            self.assertEqual(
                manager.public_status()["source"],
                "discord_voice",
            )

    def test_same_owner_reacquire_blocks_active_peer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )

            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_conflict",
            ):
                manager.acquire(
                    "discord_voice",
                    "b" * 32,
                    observations={
                        "local_mic": VoiceInputObservation(
                            "active",
                            "a" * 32,
                        ),
                        "discord_voice": VoiceInputObservation(
                            "inactive",
                            "b" * 32,
                        ),
                    },
                )
            self.assertEqual(manager.public_status()["state"], "blocked")

    def test_persist_failure_latches_until_safe_restart_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.observe(INACTIVE)
            with patch(
                "evelyn_core.voice_input_lease.atomic_json_write",
                side_effect=OSError("disk unavailable"),
            ):
                with self.assertRaises(OSError):
                    manager.acquire(
                        "discord_voice",
                        "b" * 32,
                        observations=INACTIVE,
                    )

            self.assertEqual(manager.public_status()["state"], "blocked")
            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_unavailable",
            ):
                manager.acquire(
                    "discord_voice",
                    "b" * 32,
                    observations=INACTIVE,
                )

            restarted = self.manager(tmp)
            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_unavailable",
            ):
                restarted.acquire(
                    "discord_voice",
                    "b" * 32,
                    observations={
                        "local_mic": VoiceInputObservation("unknown"),
                        "discord_voice": VoiceInputObservation(
                            "inactive",
                            "b" * 32,
                        ),
                    },
                )
            receipt = restarted.acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )
            self.assertEqual(receipt["source"], "discord_voice")

    def test_inactive_observation_cannot_release_acquire_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )
            manager.observe(INACTIVE)

            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_conflict",
            ):
                manager.acquire(
                    "local_mic",
                    "a" * 32,
                    observations=INACTIVE,
                )

    def test_cross_source_acquire_retires_inactive_old_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.acquire(
                "local_mic",
                "a" * 32,
                observations=INACTIVE,
            )
            restarted = self.manager(tmp)

            receipt = restarted.acquire(
                "discord_voice",
                "b" * 32,
                observations={
                    "local_mic": VoiceInputObservation(
                        "inactive",
                        "c" * 32,
                    ),
                    "discord_voice": VoiceInputObservation(
                        "inactive",
                        "b" * 32,
                    ),
                },
            )

            self.assertEqual(receipt["source"], "discord_voice")

    def test_cross_source_acquire_keeps_same_instance_inactive_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.acquire(
                "local_mic",
                "a" * 32,
                observations=INACTIVE,
            )
            restarted = self.manager(tmp)

            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_conflict",
            ):
                restarted.acquire(
                    "discord_voice",
                    "b" * 32,
                    observations=INACTIVE,
                )
            self.assertEqual(
                restarted.public_status()["source"],
                "local_mic",
            )

    def test_old_generation_reconcile_rejects_unknown_peer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.acquire(
                "local_mic",
                "a" * 32,
                observations=INACTIVE,
            )
            restarted = self.manager(tmp)

            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_unavailable",
            ):
                restarted.acquire(
                    "discord_voice",
                    "b" * 32,
                    observations={
                        "local_mic": VoiceInputObservation(
                            "inactive",
                            "c" * 32,
                        ),
                        "discord_voice": VoiceInputObservation(
                            "unknown",
                            "b" * 32,
                        ),
                    },
                )
            self.assertEqual(
                restarted.public_status()["source"],
                "local_mic",
            )

    def test_same_source_inactive_new_instance_recovers_old_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.acquire(
                "discord_voice",
                "b" * 32,
                observations=INACTIVE,
            )
            restarted = self.manager(tmp)
            observations = {
                **INACTIVE,
                "discord_voice": VoiceInputObservation(
                    "inactive",
                    "c" * 32,
                ),
            }

            receipt = restarted.acquire(
                "discord_voice",
                "c" * 32,
                observations=observations,
            )

            self.assertEqual(receipt["instanceId"], "c" * 32)

    def test_local_stop_ack_can_retire_previous_process_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.acquire(
                "local_mic",
                "a" * 32,
                observations=INACTIVE,
            )
            manager.release_if_inactive(
                "local_mic",
                "c" * 32,
                observations={
                    "local_mic": VoiceInputObservation(
                        "inactive",
                        "c" * 32,
                    ),
                    "discord_voice": VoiceInputObservation(
                        "inactive",
                        "b" * 32,
                    ),
                },
            )

            self.assertEqual(manager.public_status()["state"], "unowned")

    def test_different_active_source_blocks_instead_of_becoming_unowned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.acquire(
                "local_mic",
                "a" * 32,
                observations=INACTIVE,
            )

            status = manager.observe(
                {
                    "local_mic": VoiceInputObservation(
                        "inactive",
                        "a" * 32,
                    ),
                    "discord_voice": VoiceInputObservation(
                        "active",
                        "b" * 32,
                    ),
                }
            )

            self.assertEqual(status["state"], "blocked")
            with self.assertRaisesRegex(
                VoiceInputLeaseError,
                "voice_input_lease_unavailable",
            ):
                manager.acquire(
                    "discord_voice",
                    "b" * 32,
                    observations={
                        "local_mic": VoiceInputObservation(
                            "inactive",
                            "a" * 32,
                        ),
                        "discord_voice": VoiceInputObservation(
                            "active",
                            "b" * 32,
                        ),
                    },
                )


class FakeDiscordLeaseClient(DiscordVoiceInputLeaseClient):
    def __init__(self, *, release_retry_delay_sec: float = 0.25) -> None:
        super().__init__(
            base_url="http://unused",
            token="x" * 48,
            instance_id="b" * 32,
            release_retry_delay_sec=release_retry_delay_sec,
        )
        self.requests: list[dict] = []

    async def _request(self, payload: dict) -> dict:
        self.requests.append(dict(payload))
        if payload["action"] == "acquire":
            return {"ok": True, "leaseId": "d" * 32}
        return {"ok": True, "released": True}


class FlakyReleaseDiscordLeaseClient(FakeDiscordLeaseClient):
    def __init__(self) -> None:
        super().__init__(release_retry_delay_sec=0.01)
        self.release_attempts = 0

    async def _request(self, payload: dict) -> dict:
        self.requests.append(dict(payload))
        if payload["action"] == "acquire":
            return {"ok": True, "leaseId": "d" * 32}
        self.release_attempts += 1
        if self.release_attempts == 1:
            raise OSError("temporary release failure")
        return {"ok": True, "released": True}


class RejectedAcquireDiscordLeaseClient(FakeDiscordLeaseClient):
    async def _request(self, payload: dict) -> dict:
        self.requests.append(dict(payload))
        raise VoiceInputLeaseError("voice_input_lease_conflict")


class CancelledReleaseDiscordLeaseClient(DiscordVoiceInputLeaseClient):
    def __init__(
        self,
        manager: VoiceInputLeaseManager,
        *,
        commit_before_cancellation: bool,
    ) -> None:
        super().__init__(
            base_url="http://unused",
            token="x" * 48,
            instance_id="b" * 32,
            release_retry_delay_sec=0.01,
        )
        self.manager = manager
        self.commit_before_cancellation = commit_before_cancellation
        self.release_started = asyncio.Event()
        self.never_return_first_release = asyncio.Event()
        self.requests: list[dict] = []
        self.release_attempts = 0
        self.release_commits = 0

    def _release_on_server(self, payload: dict) -> dict:
        was_owned = self.manager.public_status() == {
            "state": "owned",
            "source": "discord_voice",
        }
        result = self.manager.release(
            "discord_voice",
            payload["instanceId"],
            payload["leaseId"],
        )
        if was_owned:
            self.release_commits += 1
        return {"ok": True, **result}

    async def _request(self, payload: dict) -> dict:
        self.requests.append(dict(payload))
        if payload["action"] == "acquire":
            return {
                "ok": True,
                **self.manager.acquire(
                    "discord_voice",
                    payload["instanceId"],
                    observations=INACTIVE,
                ),
            }
        self.release_attempts += 1
        if self.release_attempts == 1:
            result = (
                self._release_on_server(payload)
                if self.commit_before_cancellation
                else None
            )
            self.release_started.set()
            await self.never_return_first_release.wait()
            return result or self._release_on_server(payload)
        return self._release_on_server(payload)


class AmbiguousAcquireDiscordLeaseClient(DiscordVoiceInputLeaseClient):
    def __init__(
        self,
        manager: VoiceInputLeaseManager,
        *,
        blocked_acquire_attempt: int = 1,
        lose_first_acquire_response: bool = False,
        block_first_release_after_commit: bool = False,
    ) -> None:
        super().__init__(
            base_url="http://unused",
            token="x" * 48,
            instance_id="b" * 32,
            release_retry_delay_sec=0.01,
        )
        self.manager = manager
        self.blocked_acquire_attempt = blocked_acquire_attempt
        self.lose_first_acquire_response = lose_first_acquire_response
        self.block_first_release_after_commit = (
            block_first_release_after_commit
        )
        self.acquire_started = asyncio.Event()
        self.allow_acquire_response = asyncio.Event()
        self.release_started = asyncio.Event()
        self.allow_release_response = asyncio.Event()
        self.requests: list[dict] = []
        self.acquire_attempts = 0
        self.acquire_commits = 0
        self.release_attempts = 0
        self.release_commits = 0

    def _acquire_on_server(self, payload: dict) -> dict:
        was_unowned = self.manager.public_status()["state"] == "unowned"
        result = self.manager.acquire(
            "discord_voice",
            payload["instanceId"],
            observations=INACTIVE,
        )
        if was_unowned:
            self.acquire_commits += 1
        return {"ok": True, **result}

    def _release_on_server(self, payload: dict) -> dict:
        was_owned = self.manager.public_status() == {
            "state": "owned",
            "source": "discord_voice",
        }
        result = self.manager.release(
            "discord_voice",
            payload["instanceId"],
            payload["leaseId"],
        )
        if was_owned:
            self.release_commits += 1
        return {"ok": True, **result}

    async def _request(self, payload: dict) -> dict:
        self.requests.append(dict(payload))
        if payload["action"] == "acquire":
            self.acquire_attempts += 1
            result = self._acquire_on_server(payload)
            if self.acquire_attempts == self.blocked_acquire_attempt:
                self.acquire_started.set()
                if self.lose_first_acquire_response:
                    raise OSError("response lost after acquire commit")
                await self.allow_acquire_response.wait()
            return result

        self.release_attempts += 1
        if self.release_attempts == 1 and self.block_first_release_after_commit:
            result = self._release_on_server(payload)
            self.release_started.set()
            await self.allow_release_response.wait()
            return result
        return self._release_on_server(payload)


class DiscordVoiceInputLeaseClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_refcount_keeps_server_owner_during_rearm(self) -> None:
        client = FakeDiscordLeaseClient()
        old_listener = await client.acquire()
        transition = await client.acquire()

        await client.release(old_listener)
        self.assertEqual(
            [row["action"] for row in client.requests],
            ["acquire", "acquire"],
        )

        await client.release(transition)
        self.assertEqual(
            [row["action"] for row in client.requests],
            ["acquire", "acquire", "release"],
        )

    async def test_last_listener_release_retries_without_losing_lease(self) -> None:
        client = FlakyReleaseDiscordLeaseClient()
        listener = await client.acquire()

        await client.release(listener)

        self.assertEqual(client._lease_id, "d" * 32)
        self.assertEqual(client._listener_tokens, set())
        retry_task = client._release_retry_task
        self.assertIsNotNone(retry_task)
        await asyncio.wait_for(retry_task, timeout=1.0)
        self.assertEqual(
            [row["action"] for row in client.requests],
            ["acquire", "release", "release"],
        )
        self.assertEqual(client._lease_id, "")
        self.assertIsNone(client._release_retry_task)

    async def _assert_cancelled_last_release_recovers(
        self,
        *,
        commit_before_cancellation: bool,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            manager = VoiceInputLeaseManager(
                state_path=Path(root) / "voice-input-owner.json",
                now=lambda: 123.0,
            )
            manager.observe(INACTIVE)
            client = CancelledReleaseDiscordLeaseClient(
                manager,
                commit_before_cancellation=commit_before_cancellation,
            )
            listener = await client.acquire()

            release_task = asyncio.create_task(client.release(listener))
            await client.release_started.wait()
            release_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await release_task

            retry_task = client._release_retry_task
            self.assertIsNotNone(retry_task)
            await client.release(listener)
            self.assertIs(client._release_retry_task, retry_task)
            await asyncio.wait_for(retry_task, timeout=1.0)

            self.assertEqual(
                [row["action"] for row in client.requests],
                ["acquire", "release", "release"],
            )
            self.assertEqual(client.release_commits, 1)
            self.assertEqual(client._listener_tokens, set())
            self.assertEqual(client._lease_id, "")
            self.assertIsNone(client._release_retry_task)
            self.assertEqual(manager.public_status(), {"state": "unowned", "source": ""})
            local = manager.acquire(
                "local_mic",
                "a" * 32,
                observations=INACTIVE,
            )
            self.assertEqual(local["source"], "local_mic")

    async def test_cancelled_last_release_retries_before_server_commit(self) -> None:
        await self._assert_cancelled_last_release_recovers(
            commit_before_cancellation=False,
        )

    async def test_cancelled_last_release_retry_is_idempotent_after_server_commit(
        self,
    ) -> None:
        await self._assert_cancelled_last_release_recovers(
            commit_before_cancellation=True,
        )

    async def test_cancelled_first_acquire_drains_and_releases_server_commit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            manager = VoiceInputLeaseManager(
                state_path=Path(root) / "voice-input-owner.json",
                now=lambda: 123.0,
            )
            manager.observe(INACTIVE)
            client = AmbiguousAcquireDiscordLeaseClient(manager)
            acquire_task = asyncio.create_task(client.acquire())
            await client.acquire_started.wait()

            acquire_task.cancel()
            await asyncio.sleep(0)
            client.allow_acquire_response.set()
            with self.assertRaises(asyncio.CancelledError):
                await acquire_task

            self.assertEqual(
                [row["action"] for row in client.requests],
                ["acquire", "release"],
            )
            self.assertEqual(client.acquire_commits, 1)
            self.assertEqual(client.release_commits, 1)
            self.assertEqual(manager.public_status(), {"state": "unowned", "source": ""})
            local = manager.acquire(
                "local_mic",
                "a" * 32,
                observations=INACTIVE,
            )
            self.assertEqual(local["source"], "local_mic")

    async def test_repeated_cancel_during_acquire_cleanup_has_one_retry_owner(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            manager = VoiceInputLeaseManager(
                state_path=Path(root) / "voice-input-owner.json",
                now=lambda: 123.0,
            )
            manager.observe(INACTIVE)
            client = AmbiguousAcquireDiscordLeaseClient(
                manager,
                block_first_release_after_commit=True,
            )
            acquire_task = asyncio.create_task(client.acquire())
            await client.acquire_started.wait()

            acquire_task.cancel()
            await asyncio.sleep(0)
            acquire_task.cancel()
            await asyncio.sleep(0)
            client.allow_acquire_response.set()
            await client.release_started.wait()
            acquire_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await acquire_task

            retry_task = client._release_retry_task
            self.assertIsNotNone(retry_task)
            await client.release("unknown-listener")
            self.assertIs(client._release_retry_task, retry_task)
            await asyncio.wait_for(retry_task, timeout=1.0)

            self.assertEqual(
                [row["action"] for row in client.requests],
                ["acquire", "release", "release"],
            )
            self.assertEqual(client.release_commits, 1)
            self.assertEqual(manager.public_status(), {"state": "unowned", "source": ""})

    async def test_cancelled_duplicate_acquire_keeps_existing_listener_lease(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            manager = VoiceInputLeaseManager(
                state_path=Path(root) / "voice-input-owner.json",
                now=lambda: 123.0,
            )
            manager.observe(INACTIVE)
            client = AmbiguousAcquireDiscordLeaseClient(
                manager,
                blocked_acquire_attempt=2,
            )
            first_listener = await client.acquire()
            duplicate_task = asyncio.create_task(client.acquire())
            await client.acquire_started.wait()

            duplicate_task.cancel()
            client.allow_acquire_response.set()
            with self.assertRaises(asyncio.CancelledError):
                await duplicate_task

            self.assertEqual(
                [row["action"] for row in client.requests],
                ["acquire", "acquire"],
            )
            self.assertEqual(manager.public_status()["source"], "discord_voice")
            await client.release(first_listener)
            self.assertEqual(manager.public_status(), {"state": "unowned", "source": ""})

    async def test_lost_acquire_response_reconciles_server_commit(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            manager = VoiceInputLeaseManager(
                state_path=Path(root) / "voice-input-owner.json",
                now=lambda: 123.0,
            )
            manager.observe(INACTIVE)
            client = AmbiguousAcquireDiscordLeaseClient(
                manager,
                lose_first_acquire_response=True,
            )

            with self.assertRaisesRegex(OSError, "response lost"):
                await client.acquire()
            retry_task = client._release_retry_task
            self.assertIsNotNone(retry_task)
            await asyncio.wait_for(retry_task, timeout=1.0)

            self.assertEqual(
                [row["action"] for row in client.requests],
                ["acquire", "acquire", "release"],
            )
            self.assertEqual(client.acquire_commits, 1)
            self.assertEqual(client.release_commits, 1)
            self.assertEqual(manager.public_status(), {"state": "unowned", "source": ""})

    async def test_definite_acquire_rejection_does_not_start_cleanup_retry(
        self,
    ) -> None:
        client = RejectedAcquireDiscordLeaseClient()

        with self.assertRaisesRegex(
            VoiceInputLeaseError,
            "voice_input_lease_conflict",
        ):
            await client.acquire()

        retry_task = client._release_retry_task
        try:
            self.assertIsNone(retry_task)
            self.assertEqual(
                [row["action"] for row in client.requests],
                ["acquire"],
            )
        finally:
            if retry_task is not None:
                retry_task.cancel()
                await asyncio.gather(retry_task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
